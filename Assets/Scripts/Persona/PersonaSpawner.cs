// PersonaSpawner.cs
// "다시, 봄" — 웹에서 등록한 인물의 3D 모델(.glb)을 Raon 서버에서 받아
// 고정된 스폰포인트에 배치한다.
//
// 의존성: com.unity.cloud.gltfast (Packages/manifest.json에 이미 추가됨)
//
// 데이터 흐름
//   1. 웹에서 설문·사진·음성을 올려 등록한다.
//   2. 웹 백엔드가 3D 모델을 만들어 서버의 /session/{id}/model 로 올린다.
//   3. 본 스크립트가 /session/current 를 지켜보다가 has_model 이 되면
//      /session/{id}/model.glb 를 받아 glTFast 로 띄운다.
//
// 예전에는 Streamlit 이 쓰던 로컬 폴더(data/sessions/<id>/model.glb)를 직접 읽었다.
// 빌드해서 VR 기기에 넣으면 그 경로가 없어서 아무것도 안 뜬다. 서버에서 받아야 한다.
//
// 셋업
//   1. 빈 GameObject 두 개: "PersonaSpawner"(부모) + 그 자식으로 "SpawnPoint".
//   2. SpawnPoint 의 position/rotation 으로 인물이 등장할 자리·방향을 잡는다.
//   3. PersonaSpawner 에 이 스크립트를 부착하고 spawnPoint 를 연결한다.
//   4. 같은 씬에 DialogueVoiceClient 가 있으면 주소·토큰·세션을 자동으로 따라간다.
//      없으면 인스펙터의 serverUrl/token 을 쓴다.

using System;
using System.Collections;
using System.Collections.Generic;
using System.Threading.Tasks;
using GLTFast;
using UnityEngine;
using UnityEngine.Networking;

[DisallowMultipleComponent]
public class PersonaSpawner : MonoBehaviour
{
    [Header("서버")]
    [Tooltip("같은 씬의 DialogueVoiceClient. 비워두면 자동으로 찾는다.\n" +
             "찾으면 주소·토큰·세션을 그쪽에 맞춘다 — 두 곳에 따로 적으면 어긋난다.")]
    public DialogueVoiceClient voiceClient;

    [Tooltip("DialogueVoiceClient 가 없을 때 쓸 서버 주소.")]
    public string serverUrl = "http://220.69.208.201:8000";

    [Tooltip("DialogueVoiceClient 가 없을 때 쓸 토큰.")]
    public string token = "";

    [Tooltip("강제로 사용할 세션 ID. 비워두면 서버의 현재 세션을 따라간다.")]
    public string sessionIdOverride = "";

    [Header("스폰")]
    [Tooltip("인물이 등장할 위치/회전. 비워두면 본 GameObject 자신을 사용.")]
    public Transform spawnPoint;

    [Tooltip("로드된 모델을 이 높이(m)로 자동 스케일. 0이면 GLB 원본 크기.")]
    public float targetHeightMeters = 1.7f;

    [Tooltip("최종 스케일에 곱하는 추가 배수. 예: 0.1 = 10%.")]
    public float scaleMultiplier = 1.0f;

    [Tooltip("spawnPoint 의 회전을 그대로 사용할지 여부.")]
    public bool useSpawnRotation = true;

    [Header("자세")]
    // Tripo 가 리타겟해 준 glb 는 앉은 자세를 애니메이션으로 들고 온다. 바인드
    // 포즈는 서 있는 자세라, 클립을 적용하지 않으면 카페 의자 위에 선 채로 뜬다.
    // glTFast 는 Animation 컴포넌트를 붙이고 clip 까지 넣어주지만 재생은 하지 않는다.
    [Tooltip("모델에 애니메이션이 있으면 적용한다. 끄면 바인드 포즈(선 자세) 그대로.")]
    public bool applyPoseAnimation = true;

    [Tooltip("한 프레임만 적용하고 정지. 앉은 자세로 굳는다. 끄면 루프 재생.")]
    public bool freezePose = true;

    // preset:sit 은 7.2초 루프인데 시작 지점은 자세가 덜 자리잡아 있다. 실측으로
    // t=0 의 허벅지가 128°, 중간이 104° 로 24° 차이가 났다. 굳힐 프레임을 고를 수
    // 있어야 의자에 맞는 자세를 잡는다.
    [Tooltip("굳힐 시점(초). 0 이면 클립 시작. 자세가 어색하면 조금 올려 본다.")]
    public float freezeTimeSec = 0f;

    [Header("앉아서 숨쉬기")]
    // preset:sit 7.2초는 "앉음 → 손 들기(2.0~3.0s) → 손 내리기(6.0~7.2s)" 라서 통째로
    // 반복하면 대화 중에 계속 손을 흔든다. 앞쪽 0~1.5초는 손을 무릎에 얹고 앉은 채
    // 미세하게만 움직이는 구간이다(2026-09-14 실측: 전체 뼈 각속도 합 20~40°/s, 손을
    // 들 때는 560°/s). 이 구간만 느리게 앞뒤로 오가면 앉아서 숨 쉬는 사람처럼 보인다.
    // 새 클립도 크레딧도 필요 없다. Tripo 프리셋에는 앉은 채 쉬는 동작이 없다.
    [Tooltip("켜면 freezePose 대신 아래 구간을 느리게 왕복 재생한다. 앉아서 숨 쉬는 느낌.")]
    public bool quietLoop = true;

    [Tooltip("왕복 구간 시작(초).")]
    public float quietStartSec = 0f;

    // preset:sit 의 길이는 인물마다 다르게 리타겟된다(실측 7.21초 / 5.77초). 짧은 쪽에서는
    // 손 들기가 1.43초부터 시작해서, 1.5 로 두면 왕복 끝에 그 첫 동작이 걸려 흠칫거린다.
    // 1.4 는 두 길이 모두에서 조용한 구간 안이다(뼈 각속도 합 30°/s 대).
    [Tooltip("왕복 구간 끝(초). 클립보다 길면 클립 끝까지. 새 인물을 넣으면 실제 클립에서 다시 재 볼 것.")]
    public float quietEndSec = 1.4f;

    [Tooltip("왕복 구간 끝을 로드 때 클립에서 직접 재서 정한다. 끄면 위 quietEndSec 을 그대로 쓴다.")]
    public bool autoQuietEnd = true;

    // 실측 두 클립으로 쓸어 본 값. 50 은 앉은 직후 안정화 노이즈(53°/s)에 걸려 오검출하고,
    // 120 이상은 손 들기가 이미 시작한 뒤에 걸린다. 80 은 둘 다 피한다(7.21초→1.83s, 5.77초→1.47s).
    [Tooltip("동작이 시작됐다고 볼 기준(뼈 각속도 합, °/초). 조용한 구간은 30 안팎이다.")]
    public float quietThresholdDegPerSec = 80f;


    [Tooltip("재생 속도 배수. 0.5 면 절반 속도, 1.5초 구간을 3초에 한 번 왕복.")]
    [Range(0.1f, 2f)] public float quietSpeed = 0.5f;

    [Tooltip("가슴·어깨 뼈에 아주 작은 호흡 움직임을 얹는다. 정지 자세에서도 동작한다.")]
    public bool breathe = true;

    [Tooltip("분당 호흡 수. 쉬는 성인은 12~16.")]
    [Range(4f, 30f)] public float breathsPerMinute = 13f;

    [Tooltip("가슴이 앞뒤로 기우는 진폭(도). 1도 안팎이면 충분하다.")]
    [Range(0f, 5f)] public float breathChestDeg = 1.0f;

    [Tooltip("어깨가 오르내리는 진폭(도).")]
    [Range(0f, 5f)] public float breathShoulderDeg = 0.7f;

    [Header("시선")]
    // 얼굴 뼈도 블렌드셰이프도 없어서 표정으로는 아무것도 못 한다. 대신 "나를 보고 있다"는
    // 신호가 VR 에서 가장 강한 살아있음이 된다. 호흡과 같은 방식으로 뼈 위에 얹는다.
    [Tooltip("체험자를 바라본다. 목과 머리가 나눠 돌아간다.")]
    public bool gaze = true;

    [Tooltip("바라볼 대상. 비워두면 Camera.main(HMD)을 쓴다.")]
    public Transform gazeTarget;

    [Tooltip("좌우 한계(도). 넘어가면 그 각도에서 멈추고 더 돌지 않는다.")]
    [Range(0f, 90f)] public float gazeMaxYaw = 55f;

    [Tooltip("위아래 한계(도).")]
    [Range(0f, 60f)] public float gazeMaxPitch = 22f;

    [Tooltip("목이 맡는 비율. 나머지는 머리가 맡는다. 0 이면 머리만 꺾여 뻣뻣하다.")]
    [Range(0f, 0.8f)] public float gazeNeckShare = 0.35f;

    [Tooltip("따라붙는 빠르기(초). 작을수록 빠르게 쫓는다.")]
    [Range(0.05f, 1.5f)] public float gazeResponseSec = 0.28f;

    [Tooltip("가끔 시선을 뗀다. 계속 응시하면 사람은 부담을 느낀다.")]
    public bool gazeBreaks = true;

    [Tooltip("시선이 정반대로 간다면 켠다. 얼굴 앞 방향의 부호를 뒤집는다.")]
    public bool gazeFlipForward;

    [Tooltip("읽기용. 지금 얼굴이 향한다고 계산한 방향(월드).")]
    public Vector3 gazeForwardNow;

    [Tooltip("읽기용. 그 방향과 대상 사이의 각도(도). 0 에 가까워야 정상이다.")]
    public float gazeErrorDeg;

    [Header("텍스처")]
    // glTFast 는 기본값으로 밉맵을 만들지 않는다(ImportSettings.GenerateMipMaps).
    // 밉맵 없는 2K 얼굴 텍스처는 VR 에서 조금만 멀어져도 픽셀이 지글거려서,
    // 생성 품질과 상관없이 "뭉개져 보인다". 만드는 비용은 로드 때 한 번뿐이다.
    [Tooltip("텍스처 밉맵 생성. VR 에서 지글거림이 크게 줄어든다. 로드가 조금 느려진다.")]
    public bool generateMipMaps = true;

    [Tooltip("비등방성 필터링 레벨. 비스듬히 보이는 면의 선명도. 1 이면 끔.")]
    [Range(1, 16)] public int anisotropicFilterLevel = 8;

    [Header("머티리얼 보정")]
    // 사진에서 만든 GLB 는 metallic/roughness 맵이 사실상 쓸 수 없는 값이다.
    // 피부가 금속으로 잡히거나 젖은 플라스틱처럼 번들거려서, 형태가 멀쩡해도
    // 시체처럼 보인다. glTF 규격상 최종값은 factor × 텍스처 채널이라, 맵을 그대로
    // 두면 factor 를 아무리 낮춰도 맵의 얼룩이 남는다. 그래서 맵을 떼고 값을 준다.
    [Tooltip("로드 직후 모든 머티리얼을 무광 피부 쪽으로 되돌린다.")]
    public bool sanitizeMaterials = true;

    [Tooltip("금속성. 사람 피부는 0 이다.")]
    [Range(0f, 1f)] public float metallic = 0f;

    [Tooltip("거칠기. 1 에 가까울수록 무광. 피부는 0.8 언저리.")]
    [Range(0f, 1f)] public float roughness = 0.85f;

    [Tooltip("metallic/roughness 텍스처를 떼어낸다. 켜야 위 두 값이 그대로 먹는다.")]
    public bool dropMetallicRoughnessMap = true;

    [Tooltip("노멀맵 세기. 생성된 노멀은 과장돼 있어 피부가 우둘투둘해진다. 0 이면 끈다.")]
    [Range(0f, 2f)] public float normalStrength = 0.5f;

    [Tooltip("AO 세기. 알베도에 그늘이 이미 구워져 있어 겹치면 얼굴이 지저분해진다.")]
    [Range(0f, 1f)] public float occlusionStrength = 0.3f;

    [Tooltip("알베도 밝기 배수. 색이 어둡게 뜰 때 1.1~1.3 으로 올려 본다.")]
    [Range(0.5f, 2f)] public float albedoBrightness = 1f;

    [Tooltip("자체발광을 끈다. 생성물에 엉뚱한 emissive 가 실려 색이 뜨는 경우가 있다.")]
    public bool killEmissive = true;

    [Header("대기")]
    [Tooltip("모델이 아직 없을 때 다시 물어보는 간격(초). 생성에 몇 분 걸린다.")]
    public float retryIntervalSec = 3f;

    [Tooltip("이 시간(초) 안에 모델이 오지 않으면 포기. 0이면 무한 대기.")]
    public float maxWaitSec = 0f;

    [Header("디버그 / UI (선택)")]
    [Tooltip("모델을 기다리는 동안 켜져 있다가 완료 시 자동으로 꺼지는 GameObject.")]
    public GameObject loadingIndicator;

    [Tooltip("에디터 확인용. 서버 대신 이 로컬 GLB 를 바로 띄운다. 비우면 서버를 따라간다.\n" +
             "절대 경로 또는 프로젝트 루트 기준 상대 경로. 예: tools/_work/tripo_trial_x/animated.glb")]
    public string localGlbPath = "";

    public bool verboseLog = true;

    // ── 내부 상태 ──────────────────────────────────────────────
    GameObject _spawnedInstance;
    /// <summary>지금 서 있는 인물. 아직 안 받았으면 null. 연출이 얼굴 위치를 재는 데 쓴다.</summary>
    public GameObject Spawned => _spawnedInstance;
    string _loadedSession = "";      // 이미 띄운 세션. 인물이 바뀔 때만 다시 받는다.
    bool _isLoading;

    // 앉아서 숨쉬기. 클립을 왕복시키는 Animation 과, 호흡을 얹을 뼈들.
    Animation _poseAnim;
    float _quietStart, _quietEnd;
    Transform _spine, _chest, _neck, _lClav, _rClav;
    Quaternion _spineBase, _chestBase, _neckBase, _lClavBase, _rClavBase;
    // 인물이 보는 방향(루트 기준). 루트의 forward 가 얼굴 방향이라는 보장이 없어 뼈로 잰다.
    Vector3 _faceForwardLocal = Vector3.forward, _faceRightLocal = Vector3.right;
    // 시선. _aim 은 지금 보고 있는 방향(월드)이고 목표를 서서히 따라간다.
    Transform _head, _lFootBone, _lToeBone, _rFootBone, _rToeBone;
    Quaternion _headBase;
    Vector3 _aim, _gazeBreakDir;
    Camera _gazeCamera;
    float _gazeBreakAt, _gazeBreakUntil;

    string Url => voiceClient != null ? voiceClient.serverUrl : serverUrl;
    string Tok => voiceClient != null ? voiceClient.token : token;

    void Start()
    {
        if (voiceClient == null) voiceClient = FindObjectOfType<DialogueVoiceClient>();
        if (!string.IsNullOrWhiteSpace(localGlbPath)) StartCoroutine(LoadLocal());
        else StartCoroutine(WatchSession());
    }

    /// <summary>서버 없이 로컬 GLB 를 띄운다. 파일이 없으면 서버 흐름으로 돌아간다.</summary>
    IEnumerator LoadLocal()
    {
        string path = localGlbPath.Trim();
        if (!System.IO.Path.IsPathRooted(path))
            path = System.IO.Path.GetFullPath(System.IO.Path.Combine(Application.dataPath, "..", path));
        if (!System.IO.File.Exists(path))
        {
            Debug.LogWarning($"[PersonaSpawner] 로컬 GLB 가 없어 서버를 따라갑니다: {path}");
            yield return StartCoroutine(WatchSession());
            yield break;
        }
        _isLoading = true;
        if (verboseLog) Debug.Log($"[PersonaSpawner] 로컬 GLB 로드: {path}");
        var task = SpawnAsync("local", System.IO.File.ReadAllBytes(path));
        while (!task.IsCompleted) yield return null;
        if (task.IsFaulted) Debug.LogError($"[PersonaSpawner] 스폰 실패: {task.Exception}");
        Finish();
    }

    void Update()
    {
        // 조용한 구간 왕복. Animation 컴포넌트가 재생하게 두고 방향만 뒤집는다.
        if (!quietLoop || _poseAnim == null || !_poseAnim.enabled || _poseAnim.clip == null) return;
        var state = _poseAnim[_poseAnim.clip.name];
        if (state == null) return;
        float speed = Mathf.Max(0.1f, quietSpeed);
        if (state.speed >= 0f && state.time >= _quietEnd)      { state.time = _quietEnd;   state.speed = -speed; }
        else if (state.speed < 0f && state.time <= _quietStart) { state.time = _quietStart; state.speed = speed; }
        else state.speed = (state.speed < 0f ? -1f : 1f) * speed;   // 인스펙터에서 속도를 바꿔도 따라간다
    }

    void LateUpdate()
    {
        if (_spawnedInstance == null) return;
        // 애니메이션이 매 프레임 뼈를 다시 쓰면 그 위에 얹고, 정지 자세면 저장해 둔 기준에 얹는다.
        bool driven = _poseAnim != null && _poseAnim.enabled && _poseAnim.isPlaying;
        if (driven) CaptureBreathBase();
        Transform body = _spawnedInstance.transform;

        if (breathe)
        {
            float phase = Mathf.Sin(Time.time * breathsPerMinute / 60f * Mathf.PI * 2f);
            // 뼈의 로컬 축은 리깅마다 달라서 인물이 보는 방향 기준 축(옆·앞)으로 돌린다.
            Vector3 side = body.TransformDirection(_faceRightLocal), front = body.TransformDirection(_faceForwardLocal);
            Breathe(_spine, _spineBase, -phase * breathChestDeg * 0.5f, side);
            Breathe(_chest, _chestBase, -phase * breathChestDeg, side);
            Breathe(_neck,  _neckBase,   phase * breathChestDeg * 1.5f, side);   // 머리는 수평을 지킨다
            Breathe(_lClav, _lClavBase,  phase * breathShoulderDeg, front);
            Breathe(_rClav, _rClavBase, -phase * breathShoulderDeg, front);
        }

        // 시선은 호흡 다음이다. 호흡이 목에 써 놓은 결과 위에 얹는다.
        if (gaze) AimGaze(body);
    }

    /// <summary>인물이 보는 방향을 발목→발가락 뼈로 잰다. 루트 회전이나 뼈의 로컬 축과 무관하다.</summary>
    void FindFacing(GameObject root)
    {
        Transform lFoot = null, lToe = null, rFoot = null, rToe = null;
        foreach (var t in root.GetComponentsInChildren<Transform>(true))
        {
            string n = t.name.ToLowerInvariant();
            if (n.EndsWith("l_foot")) lFoot = t;
            else if (n.EndsWith("l_toebase")) lToe = t;
            else if (n.EndsWith("r_foot")) rFoot = t;
            else if (n.EndsWith("r_toebase")) rToe = t;
        }
        Transform body = root.transform;
        Vector3 forward = body.forward;
        if (lFoot != null && lToe != null && rFoot != null && rToe != null)
        {
            Vector3 f = Vector3.ProjectOnPlane((lToe.position - lFoot.position) + (rToe.position - rFoot.position), body.up);
            if (f.sqrMagnitude > 1e-8f) forward = f.normalized;
        }
        _faceForwardLocal = body.InverseTransformDirection(forward);
        _faceRightLocal = body.InverseTransformDirection(Vector3.Cross(body.up, forward).normalized);
        _lFootBone = lFoot; _lToeBone = lToe; _rFootBone = rFoot; _rToeBone = rToe;
        if (verboseLog) Debug.Log($"[PersonaSpawner] 얼굴 방향(월드): {forward}");
    }

    /// <summary>
    /// 클립을 직접 찍어 보며 동작이 시작되는 지점을 찾는다. preset:sit 은 인물마다 다른 길이로
    /// 리타겟돼서(실측 7.21초 / 5.77초) 조용한 구간의 끝이 고정값이 아니다. 커브를 읽는
    /// AnimationUtility 는 에디터 전용이라, 런타임에서도 되는 SampleAnimation 으로 자세를
    /// 찍어 뼈 회전이 프레임마다 얼마나 도는지 잰다.
    /// </summary>
    float MeasureQuietEnd(Animation anim, float start, float length)
    {
        var bones = anim.GetComponentsInChildren<Transform>();
        if (bones.Length == 0) return quietEndSec;

        const float step = 1f / 30f;
        const float scanSec = 4f;            // 이보다 긴 왕복은 쓸 일이 없다. 로드 때 찍는 횟수도 묶어 둔다
        float limit = Mathf.Min(length, start + scanSec);
        var subject = anim.gameObject;
        var previous = new Quaternion[bones.Length];

        anim.clip.SampleAnimation(subject, start);
        for (int i = 0; i < bones.Length; i++) previous[i] = bones[i].localRotation;

        float quiet = limit;
        for (float t = start + step; t <= limit; t += step)
        {
            anim.clip.SampleAnimation(subject, t);
            float degrees = 0f;
            for (int i = 0; i < bones.Length; i++)
            {
                degrees += Quaternion.Angle(previous[i], bones[i].localRotation);
                previous[i] = bones[i].localRotation;
            }
            if (degrees / step > quietThresholdDegPerSec) { quiet = t - step; break; }
        }
        anim.clip.SampleAnimation(subject, start);   // 재느라 흐트러진 자세를 되돌린다

        // 시작하자마자 걸렸으면 자세가 잡히는 흔들림을 동작으로 오해한 것이다. 설정값으로 물러난다.
        if (quiet < start + 0.3f)
        {
            if (verboseLog)
                Debug.LogWarning($"[PersonaSpawner] 조용한 구간이 너무 짧게 잡혔다({quiet:0.00}s) — " +
                                 $"quietEndSec {quietEndSec:0.00}s 를 쓴다");
            return quietEndSec;
        }
        if (verboseLog)
            Debug.Log($"[PersonaSpawner] 조용한 구간 측정: {start:0.00}~{quiet:0.00}s " +
                      $"(기준 {quietThresholdDegPerSec:0}°/s, 클립 {length:0.00}s)");
        return quiet;
    }

    /// <summary>
    /// 목과 머리를 체험자 쪽으로 돌린다. 뼈의 로컬 축이 리깅마다 달라서 호흡과 같이
    /// 월드 회전 델타로 얹는다. 목이 일부를 맡고 남은 각도를 머리가 마저 돌린다 —
    /// 머리만 꺾으면 목이 꺾인 인형처럼 보인다.
    /// </summary>
    void AimGaze(Transform body)
    {
        if (_head == null) return;
        Transform look = gazeTarget;
        if (look == null)
        {
            if (_gazeCamera == null) _gazeCamera = Camera.main;   // VR 에서는 HMD 다
            if (_gazeCamera == null) return;
            look = _gazeCamera.transform;
        }

        Vector3 up = body.up;
        // 캐시된 방향 대신 매 프레임 발 뼈로 다시 잰다. 다른 스크립트가 뼈대를 돌려도 흔들리지 않는다.
        Vector3 forward = MeasureFaceForward(body, up);
        if (gazeFlipForward) forward = -forward;
        gazeForwardNow = forward;
        Vector3 want = ClampAim(look.position - _head.position, forward, up, gazeMaxYaw, gazeMaxPitch);
        if (gazeBreaks) want = GazeBreak(want, up);

        if (_aim.sqrMagnitude < 1e-6f) _aim = forward;
        _aim = Vector3.Slerp(_aim, want, 1f - Mathf.Exp(-Time.deltaTime / Mathf.Max(0.01f, gazeResponseSec))).normalized;

        // 머리를 기준 자세로 되돌린다. 안 그러면 델타가 프레임마다 쌓인다.
        _head.localRotation = _headBase;

        Vector3 reached = forward;
        if (_neck != null && gazeNeckShare > 0.001f)
        {
            Quaternion share = Quaternion.Slerp(Quaternion.identity, Quaternion.FromToRotation(forward, _aim), gazeNeckShare);
            _neck.rotation = share * _neck.rotation;   // 머리는 자식이라 같이 따라간다
            reached = share * forward;
        }
        _head.rotation = Quaternion.FromToRotation(reached, _aim) * _head.rotation;
        gazeErrorDeg = Vector3.Angle(_aim, Vector3.Normalize(look.position - _head.position));
    }

    /// <summary>얼굴이 보는 방향(월드)을 발목→발가락으로 잰다. 앉은 자세에서도 발은 앞을 향한다.</summary>
    Vector3 MeasureFaceForward(Transform body, Vector3 up)
    {
        Vector3 f = Vector3.zero;
        if (_lFootBone && _lToeBone) f += _lToeBone.position - _lFootBone.position;
        if (_rFootBone && _rToeBone) f += _rToeBone.position - _rFootBone.position;
        f = Vector3.ProjectOnPlane(f, up);
        return f.sqrMagnitude > 1e-8f ? f.normalized : body.TransformDirection(_faceForwardLocal);
    }

    /// <summary>바라볼 방향을 몸 기준 좌우·상하 한계 안으로 자른다. 뒤를 보려 해도 꺾이지 않는다.</summary>
    static Vector3 ClampAim(Vector3 toTarget, Vector3 forward, Vector3 up, float maxYaw, float maxPitch)
    {
        if (toTarget.sqrMagnitude < 1e-6f) return forward;
        Vector3 dir = toTarget.normalized;
        Vector3 flatFwd = Vector3.ProjectOnPlane(forward, up);
        Vector3 flatDir = Vector3.ProjectOnPlane(dir, up);
        if (flatFwd.sqrMagnitude < 1e-6f) return forward;
        if (flatDir.sqrMagnitude < 1e-6f) flatDir = flatFwd;
        flatFwd.Normalize(); flatDir.Normalize();

        float yaw = Mathf.Clamp(Vector3.SignedAngle(flatFwd, flatDir, up), -maxYaw, maxYaw);
        // 상하는 수평 성분에서 잰다. 축을 세울 때와 되돌릴 때 같은 규칙을 써서 부호를 맞춘다.
        float pitch = Mathf.Clamp(Vector3.SignedAngle(flatDir, dir, Vector3.Cross(up, flatDir)), -maxPitch, maxPitch);

        Vector3 aimed = Quaternion.AngleAxis(yaw, up) * flatFwd;
        return (Quaternion.AngleAxis(pitch, Vector3.Cross(up, aimed)) * aimed).normalized;
    }

    /// <summary>가끔 시선을 뗀다. 한 점만 계속 응시하면 사람은 부담을 느낀다.</summary>
    Vector3 GazeBreak(Vector3 want, Vector3 up)
    {
        float now = Time.time;
        if (_gazeBreakAt <= 0f) { _gazeBreakAt = now + UnityEngine.Random.Range(4f, 9f); return want; }
        if (now >= _gazeBreakAt)
        {
            _gazeBreakAt = now + UnityEngine.Random.Range(4f, 9f);
            _gazeBreakUntil = now + UnityEngine.Random.Range(0.5f, 1.2f);
            Quaternion away = Quaternion.AngleAxis(UnityEngine.Random.Range(-28f, 28f), up)
                            * Quaternion.AngleAxis(UnityEngine.Random.Range(-8f, 14f), Vector3.Cross(up, want));
            _gazeBreakDir = (away * want).normalized;
        }
        return now < _gazeBreakUntil && _gazeBreakDir.sqrMagnitude > 1e-6f ? _gazeBreakDir : want;
    }

    static void Breathe(Transform bone, Quaternion baseLocal, float angleDeg, Vector3 worldAxis)
    {
        if (bone == null) return;
        Quaternion parent = bone.parent != null ? bone.parent.rotation : Quaternion.identity;
        Quaternion world = Quaternion.AngleAxis(angleDeg, worldAxis) * (parent * baseLocal);
        bone.localRotation = Quaternion.Inverse(parent) * world;
    }

    void FindBreathBones(GameObject root)
    {
        _spine = _chest = _neck = _lClav = _rClav = _head = null;
        foreach (var t in root.GetComponentsInChildren<Transform>(true))
        {
            string n = t.name.ToLowerInvariant();
            if (_spine == null && n.EndsWith("spine01")) _spine = t;
            else if (_chest == null && n.EndsWith("spine02")) _chest = t;
            else if (_neck == null && n.Contains("neck")) _neck = t;
            else if (_lClav == null && n.Contains("l_clavicle")) _lClav = t;
            else if (_rClav == null && n.Contains("r_clavicle")) _rClav = t;
            else if (_head == null && n.EndsWith("head")) _head = t;
        }
        CaptureBreathBase();
        if (verboseLog)
            Debug.Log($"[PersonaSpawner] 호흡 뼈: spine={_spine?.name} chest={_chest?.name} neck={_neck?.name} " +
                      $"clavicle={_lClav?.name}/{_rClav?.name} head={_head?.name}");
    }

    void CaptureBreathBase()
    {
        if (_spine) _spineBase = _spine.localRotation;
        if (_chest) _chestBase = _chest.localRotation;
        if (_neck)  _neckBase  = _neck.localRotation;
        if (_lClav) _lClavBase = _lClav.localRotation;
        if (_rClav) _rClavBase = _rClav.localRotation;
        if (_head)  _headBase  = _head.localRotation;
    }

    /// <summary>
    /// 서버의 현재 세션을 지켜본다. 인물이 바뀌면 새 모델로 갈아 끼운다.
    /// 웹에서 등록하는 순간 VR 안의 인물도 따라 바뀌어야 한다.
    /// </summary>
    IEnumerator WatchSession()
    {
        var wait = new WaitForSeconds(Mathf.Max(1f, retryIntervalSec));
        float waited = 0f;

        while (true)
        {
            string sid = sessionIdOverride.Trim();
            bool hasModel = true;

            if (string.IsNullOrEmpty(sid))
            {
                // DialogueVoiceClient 가 이미 세션을 따라가고 있으면 그 값을 쓴다.
                if (voiceClient != null && voiceClient.HasSession)
                {
                    sid = voiceClient.sessionId;
                    hasModel = voiceClient.SessionHasModel;
                }
                else
                {
                    yield return StartCoroutine(FetchCurrent(r => { sid = r.session; hasModel = r.has_model; }));
                }
            }

            // 인물이 바뀌었는데 새 사람에게 모델이 없으면, 앞사람을 치워야 한다.
            // 안 그러면 목소리는 새 사람인데 서 있는 모습은 앞사람이 된다.
            if (!string.IsNullOrEmpty(sid) && sid != _loadedSession && !hasModel && _spawnedInstance)
            {
                Destroy(_spawnedInstance);
                _spawnedInstance = null;
                _loadedSession = "";
                if (verboseLog) Debug.Log($"[PersonaSpawner] 인물이 {sid} 로 바뀌었고 모델이 없어 치웠습니다.");
            }

            if (!string.IsNullOrEmpty(sid) && hasModel && sid != _loadedSession && !_isLoading)
            {
                yield return StartCoroutine(LoadModel(sid));
                waited = 0f;
            }
            else if (maxWaitSec > 0 && string.IsNullOrEmpty(_loadedSession))
            {
                waited += retryIntervalSec;
                if (waited >= maxWaitSec)
                {
                    Debug.LogWarning($"[PersonaSpawner] {maxWaitSec}초 안에 모델이 오지 않았습니다.");
                    yield break;
                }
            }
            yield return wait;
        }
    }

    IEnumerator FetchCurrent(Action<SessionResponse> onOk)
    {
        using (var req = UnityWebRequest.Get($"{Url}/session/current"))
        {
            if (!string.IsNullOrEmpty(Tok)) req.SetRequestHeader("X-Token", Tok);
            req.timeout = 10;
            yield return req.SendWebRequest();
            if (req.result != UnityWebRequest.Result.Success) yield break;
            SessionResponse s = null;
            try { s = JsonUtility.FromJson<SessionResponse>(req.downloadHandler.text); }
            catch (Exception e) { Debug.LogWarning($"[PersonaSpawner] 세션 응답 파싱 실패: {e.Message}"); }
            if (s != null) onOk(s);
        }
    }

    /// <summary>모델을 내려받아 띄운다. 파일로 저장하지 않고 메모리에서 바로 읽는다.</summary>
    IEnumerator LoadModel(string sid)
    {
        _isLoading = true;
        if (loadingIndicator) loadingIndicator.SetActive(true);

        byte[] glb = null;
        using (var req = UnityWebRequest.Get($"{Url}/session/{UnityWebRequest.EscapeURL(sid)}/model.glb"))
        {
            if (!string.IsNullOrEmpty(Tok)) req.SetRequestHeader("X-Token", Tok);
            req.timeout = 120;
            if (verboseLog) Debug.Log($"[PersonaSpawner] 모델 받는 중: {sid}");
            yield return req.SendWebRequest();

            // 404 는 아직 안 만들어졌다는 뜻이라 정상이다. 다음 회차에 다시 묻는다.
            if (req.result != UnityWebRequest.Result.Success)
            {
                if (req.responseCode != 404)
                    Debug.LogWarning($"[PersonaSpawner] 모델 요청 실패 {req.responseCode}: {req.error}");
                Finish();
                yield break;
            }
            glb = req.downloadHandler.data;
        }

        if (glb == null || glb.Length == 0)
        {
            Debug.LogWarning("[PersonaSpawner] 받은 모델이 비어 있습니다.");
            Finish();
            yield break;
        }

        // glTFast 는 async/await 라 코루틴에서 완료를 기다린다.
        var task = SpawnAsync(sid, glb);
        while (!task.IsCompleted) yield return null;
        if (task.IsFaulted) Debug.LogError($"[PersonaSpawner] 스폰 실패: {task.Exception}");
        Finish();
    }

    void Finish()
    {
        _isLoading = false;
        if (loadingIndicator) loadingIndicator.SetActive(false);
    }

    async Task SpawnAsync(string sid, byte[] glb)
    {
        var gltf = new GltfImport();

        // 밉맵과 필터링은 임포트 시점에만 정할 수 있다. 텍스처가 만들어진
        // 뒤에는 못 바꾼다 — 읽기 불가 상태로 GPU 에 올라가 재생성이 안 된다.
        var settings = new ImportSettings
        {
            GenerateMipMaps = generateMipMaps,
            AnisotropicFilterLevel = Mathf.Max(1, anisotropicFilterLevel),
            // ApplyPose 가 Animation 컴포넌트를 찾으므로 legacy 로 받아야 한다.
            AnimationMethod = AnimationMethod.Legacy,
        };

        if (!await gltf.Load(glb, null, settings))
        {
            Debug.LogError("[PersonaSpawner] GLB 해석 실패");
            return;
        }

        if (_spawnedInstance) Destroy(_spawnedInstance);   // 인물이 바뀌면 이전 것을 치운다

        Transform anchor = spawnPoint != null ? spawnPoint : transform;
        _spawnedInstance = new GameObject($"Persona_{sid}");
        _spawnedInstance.transform.SetPositionAndRotation(
            anchor.position, useSpawnRotation ? anchor.rotation : Quaternion.identity);

        if (!await gltf.InstantiateMainSceneAsync(_spawnedInstance.transform))
        {
            Debug.LogError("[PersonaSpawner] 장면 생성 실패");
            return;
        }

        FindFacing(_spawnedInstance);
        if (applyPoseAnimation) ApplyPose(_spawnedInstance);
        if (sanitizeMaterials) SanitizeMaterials(_spawnedInstance);

        // 자세를 먼저 잡고 높이를 잰다. 선 자세와 앉은 자세는 바운즈가 크게
        // 달라서, 순서가 뒤바뀌면 앉은 인물을 선 키에 맞춰 키워버린다.
        if (targetHeightMeters > 0f) NormalizeHeight(_spawnedInstance, targetHeightMeters);
        if (scaleMultiplier > 0f && Mathf.Abs(scaleMultiplier - 1f) > 0.0001f)
            _spawnedInstance.transform.localScale *= scaleMultiplier;

        _loadedSession = sid;
        if (verboseLog)
            Debug.Log($"[PersonaSpawner] 스폰 완료: {sid} at {anchor.position}, " +
                      $"scale={_spawnedInstance.transform.localScale}");
    }

    [Serializable]
    class SessionResponse
    {
        public string session;
        public bool has_model;
    }

    /// <summary>
    /// glb 에 실려 온 자세 애니메이션(Tripo 의 preset:sit 등)을 적용한다.
    /// glTFast 는 legacy 클립을 Animation 컴포넌트에 넣어두기만 하고 재생하지
    /// 않아서, 이걸 부르지 않으면 바인드 포즈 그대로 서 있는다.
    /// </summary>
    void ApplyPose(GameObject root)
    {
        var anim = root.GetComponentInChildren<Animation>();
        if (anim == null || anim.clip == null)
        {
            if (verboseLog) Debug.Log("[PersonaSpawner] 자세 애니메이션 없음 — 원본 포즈 사용");
            return;
        }

        _poseAnim = anim;
        if (quietLoop)
        {
            // 조용한 구간만 느리게 왕복. 방향 전환은 Update 가 맡는다.
            float len = anim.clip.length;
            _quietStart = Mathf.Clamp(quietStartSec, 0f, len);
            float measured = autoQuietEnd ? MeasureQuietEnd(anim, _quietStart, len) : quietEndSec;
            _quietEnd = Mathf.Clamp(measured, _quietStart + 0.05f, len);
            anim.wrapMode = WrapMode.ClampForever;
            anim.Play(anim.clip.name);
            var state = anim[anim.clip.name];
            if (state != null) { state.time = _quietStart; state.speed = Mathf.Max(0.1f, quietSpeed); }
            if (verboseLog)
                Debug.Log($"[PersonaSpawner] 자세 왕복: {anim.clip.name} {_quietStart:0.00}~{_quietEnd:0.00}s " +
                          $"x{quietSpeed} (클립 {len:0.00}s, 구간 끝 {(autoQuietEnd ? "자동 측정" : "설정값")})");
        }
        else if (freezePose)
        {
            // 재생하지 않고 한 프레임만 찍어 굳힌다. 대화 중 인물이 움직일
            // 필요가 없고, 정지 쪽이 프레임도 아낀다.
            float t = Mathf.Clamp(freezeTimeSec, 0f, anim.clip.length);
            anim.clip.SampleAnimation(anim.gameObject, t);
            anim.enabled = false;   // 이후 아무도 포즈를 덮어쓰지 못하게 한다
            if (verboseLog)
                Debug.Log($"[PersonaSpawner] 자세 고정: {anim.clip.name} @ {t:0.00}s " +
                          $"(클립 {anim.clip.length:0.00}s)");
        }
        else
        {
            anim.wrapMode = WrapMode.Loop;
            anim.Play();
            if (verboseLog)
                Debug.Log($"[PersonaSpawner] 자세 재생: {anim.clip.name} 루프");
        }
        if (breathe) FindBreathBones(root);
    }

    // ── 머티리얼 보정 ─────────────────────────────────────────
    //
    // glTFast 가 URP 에서 만드는 머티리얼은 glTF 규격 이름을 그대로 쓴다
    // (baseColorFactor, metallicFactor …). 빌트인 RP 로 떨어지면 유니티 표준
    // 이름(_Metallic, _Glossiness …)이 된다. 어느 쪽이 올지는 실행해 봐야
    // 알기 때문에 양쪽 이름을 다 확인하고, 있는 것만 건드린다.
    static readonly int P_BaseColor     = Shader.PropertyToID("baseColorFactor");
    static readonly int P_Metallic      = Shader.PropertyToID("metallicFactor");
    static readonly int P_Roughness     = Shader.PropertyToID("roughnessFactor");
    static readonly int P_MetalRoughMap = Shader.PropertyToID("metallicRoughnessTexture");
    static readonly int P_NormalScale   = Shader.PropertyToID("normalTexture_scale");
    static readonly int P_OcclusionStr  = Shader.PropertyToID("occlusionTexture_strength");
    static readonly int P_Emissive      = Shader.PropertyToID("emissiveFactor");

    static readonly int P_BaseColorStd  = Shader.PropertyToID("_BaseColor");
    static readonly int P_ColorStd      = Shader.PropertyToID("_Color");
    static readonly int P_MetallicStd   = Shader.PropertyToID("_Metallic");
    static readonly int P_SmoothnessStd = Shader.PropertyToID("_Smoothness");
    static readonly int P_GlossinessStd = Shader.PropertyToID("_Glossiness");
    static readonly int P_MetalMapStd   = Shader.PropertyToID("_MetallicGlossMap");
    static readonly int P_BumpScaleStd  = Shader.PropertyToID("_BumpScale");
    static readonly int P_OcclusionStd  = Shader.PropertyToID("_OcclusionStrength");
    static readonly int P_EmissionStd   = Shader.PropertyToID("_EmissionColor");

    /// <summary>
    /// 생성물이 들고 온 재질값을 사람 피부에 맞게 되돌린다.
    /// 형태를 고치지는 못하지만, 번들거림 하나만 없애도 체감이 크게 달라진다.
    /// </summary>
    void SanitizeMaterials(GameObject root)
    {
        // sharedMaterials 로 받는다. materials 로 받으면 렌더러마다 사본이 생겨
        // 인물 하나에 머티리얼이 몇 배로 불어난다. 여기 있는 것들은 이번 로드에서
        // glTFast 가 방금 만든 것이라, 공유본을 고쳐도 다른 오브젝트로 안 번진다.
        var seen = new HashSet<Material>();

        foreach (var r in root.GetComponentsInChildren<Renderer>(true))
            foreach (var m in r.sharedMaterials)
                if (m != null && seen.Add(m)) Sanitize(m);

        if (verboseLog)
            Debug.Log($"[PersonaSpawner] 머티리얼 보정 {seen.Count}개 " +
                      $"(metallic={metallic}, roughness={roughness}, normal={normalStrength}, ao={occlusionStrength})");
    }

    void Sanitize(Material m)
    {
        // ── 금속성 / 거칠기 ──
        // 맵을 먼저 떼야 아래 factor 값이 그대로 최종값이 된다. 안 떼면
        // factor × 맵 이라, 맵에 박힌 얼룩이 그대로 남는다.
        if (dropMetallicRoughnessMap)
        {
            if (m.HasProperty(P_MetalRoughMap)) m.SetTexture(P_MetalRoughMap, null);
            if (m.HasProperty(P_MetalMapStd))
            {
                m.SetTexture(P_MetalMapStd, null);
                m.DisableKeyword("_METALLICSPECGLOSSMAP");
            }
        }

        if (m.HasProperty(P_Metallic))  m.SetFloat(P_Metallic, metallic);
        if (m.HasProperty(P_Roughness)) m.SetFloat(P_Roughness, roughness);

        // 빌트인·URP Lit 은 거칠기가 아니라 매끄러움으로 받는다. 뒤집어 준다.
        float smoothness = 1f - roughness;
        if (m.HasProperty(P_MetallicStd))   m.SetFloat(P_MetallicStd, metallic);
        if (m.HasProperty(P_SmoothnessStd)) m.SetFloat(P_SmoothnessStd, smoothness);
        if (m.HasProperty(P_GlossinessStd)) m.SetFloat(P_GlossinessStd, smoothness);

        // ── 노멀맵 ──
        if (m.HasProperty(P_NormalScale))  m.SetFloat(P_NormalScale, normalStrength);
        if (m.HasProperty(P_BumpScaleStd)) m.SetFloat(P_BumpScaleStd, normalStrength);
        if (normalStrength <= 0f) m.DisableKeyword("_NORMALMAP");

        // ── AO ──
        // 알베도에 그늘이 이미 구워져 있는데 AO 까지 곱하면 눈두덩·목이 새까매진다.
        if (m.HasProperty(P_OcclusionStr)) m.SetFloat(P_OcclusionStr, occlusionStrength);
        if (m.HasProperty(P_OcclusionStd)) m.SetFloat(P_OcclusionStd, occlusionStrength);
        if (occlusionStrength <= 0f) m.DisableKeyword("_OCCLUSION");

        // ── 알베도 밝기 ──
        if (!Mathf.Approximately(albedoBrightness, 1f))
        {
            int id = m.HasProperty(P_BaseColor)      ? P_BaseColor
                   : m.HasProperty(P_BaseColorStd)   ? P_BaseColorStd
                   : m.HasProperty(P_ColorStd)       ? P_ColorStd
                   : -1;
            if (id != -1)
            {
                Color c = m.GetColor(id);
                m.SetColor(id, new Color(c.r * albedoBrightness,
                                         c.g * albedoBrightness,
                                         c.b * albedoBrightness, c.a));
            }
        }

        // ── 자체발광 ──
        if (killEmissive)
        {
            if (m.HasProperty(P_Emissive))    m.SetColor(P_Emissive, Color.black);
            if (m.HasProperty(P_EmissionStd)) m.SetColor(P_EmissionStd, Color.black);
            m.DisableKeyword("_EMISSIVE");
            m.DisableKeyword("_EMISSION");
        }
    }

    static void NormalizeHeight(GameObject root, float targetHeight)
    {
        var renderers = root.GetComponentsInChildren<Renderer>();
        if (renderers.Length == 0) return;

        Bounds bounds = renderers[0].bounds;
        for (int i = 1; i < renderers.Length; i++) bounds.Encapsulate(renderers[i].bounds);
        if (bounds.size.y <= 0.0001f) return;

        root.transform.localScale *= targetHeight / bounds.size.y;
    }
}
