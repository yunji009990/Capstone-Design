// PersonaSpawner.cs
// "다시, 봄" — Streamlit 백엔드가 Meshy AI로 생성한 .glb 모델을
// 빌드된 Unity 앱이 런타임에 읽어 고정된 스폰포인트에 자동 배치한다.
//
// 의존성: com.unity.cloud.gltfast (Packages/manifest.json에 이미 추가됨)
//
// 데이터 흐름
//   1. Streamlit이 설문 제출 시 data/current_session.txt 에 최신 세션ID를 기록한다.
//   2. Streamlit이 백그라운드에서 Meshy로 .glb 생성 → data/sessions/<id>/model.glb 저장.
//   3. 본 스크립트가 Start() 시점부터 그 파일이 생길 때까지 폴링.
//   4. 파일 등장 → glTFast로 로드 → spawnPoint에 Instantiate.
//
// 셋업
//   1. 빈 GameObject 두 개: "PersonaSpawner"(부모) + 그 자식으로 "SpawnPoint".
//   2. SpawnPoint의 position/rotation으로 인물이 등장할 정확한 자리·방향을 잡는다.
//   3. PersonaSpawner GameObject에 이 스크립트를 부착.
//   4. 인스펙터에서 dataRoot, spawnPoint(드래그) 설정.
//   5. (선택) Loading Indicator로 "잠시만요…" UI를 연결.

using System;
using System.IO;
using System.Threading.Tasks;
using GLTFast;
using UnityEngine;

[DisallowMultipleComponent]
public class PersonaSpawner : MonoBehaviour
{
    [Header("데이터 위치")]
    [Tooltip("Streamlit data 폴더 절대 경로.\n예: C:/Users/user/Desktop/다시봄_설문시스템/data")]
    public string dataRoot = @"C:\Users\user\Desktop\다시봄_설문시스템\data";

    [Tooltip("강제로 사용할 세션 ID. 비워두면 currentSessionFile에서 읽음.")]
    public string sessionIdOverride = "";

    [Tooltip("세션 ID 포인터 파일(dataRoot 기준 상대 경로). Streamlit 제출 시 자동 갱신됨.")]
    public string currentSessionFile = "current_session.txt";

    [Header("스폰")]
    [Tooltip("인물이 등장할 위치/회전. 비워두면 본 GameObject 자신을 사용.")]
    public Transform spawnPoint;

    [Tooltip("로드된 모델을 이 높이(m)로 자동 스케일. 0이면 자동 스케일 생략(GLB 원본 크기).")]
    public float targetHeightMeters = 1.7f;

    [Tooltip("최종 스케일에 곱하는 추가 배수. 자동 스케일 후(또는 생략 시 GLB 원본에) 적용.\n예: 0.1 = 10%, 0.01 = 1%. 맵이 작을 때 유용.")]
    public float scaleMultiplier = 1.0f;

    [Tooltip("spawnPoint의 회전을 그대로 사용할지 여부.")]
    public bool useSpawnRotation = true;

    [Header("폴링")]
    [Tooltip(".glb 파일이 생길 때까지 다시 시도하는 간격(초).")]
    public float retryIntervalSec = 2f;

    [Tooltip("이 시간(초) 안에 파일이 생기지 않으면 포기. 0이면 무한 대기.")]
    public float maxWaitSec = 0f;

    [Header("디버그 / UI (선택)")]
    [Tooltip("모델 로딩 동안 켜져 있다가 완료 시 자동으로 꺼지는 GameObject.")]
    public GameObject loadingIndicator;

    [Tooltip("자세한 로그 출력.")]
    public bool verboseLog = true;

    // ── 내부 상태 ──────────────────────────────────────────────
    GameObject _spawnedInstance;
    bool _isLoading;

    async void Start()
    {
        try { await LoadAsync(); }
        catch (Exception e) { Debug.LogError($"[PersonaSpawner] 예외: {e}"); }
    }

    /// <summary>
    /// 외부에서 강제로 호출할 수도 있음 (예: 세션 변경 후 다시 로드).
    /// </summary>
    public async Task LoadAsync()
    {
        if (_isLoading) { Debug.LogWarning("[PersonaSpawner] 이미 로드 중"); return; }
        _isLoading = true;

        if (loadingIndicator) loadingIndicator.SetActive(true);
        try
        {
            string sid = ResolveSessionId();
            if (string.IsNullOrWhiteSpace(sid))
            {
                Debug.LogWarning("[PersonaSpawner] sessionId를 결정할 수 없습니다. "
                    + "sessionIdOverride를 설정하거나 currentSessionFile을 확인하세요.");
                return;
            }

            string glbPath = Path.Combine(dataRoot, "sessions", sid, "model.glb");
            if (verboseLog) Debug.Log($"[PersonaSpawner] 대기 시작: {glbPath}");

            if (!await WaitForFileAsync(glbPath)) return;

            if (verboseLog) Debug.Log($"[PersonaSpawner] glTFast 로드: {glbPath}");
            var gltf = new GltfImport();
            bool ok = await gltf.Load(glbPath);
            if (!ok)
            {
                Debug.LogError("[PersonaSpawner] GLB 로드 실패");
                return;
            }

            // 이전 인스턴스 정리 (재로드 시)
            if (_spawnedInstance) Destroy(_spawnedInstance);

            Transform anchor = spawnPoint != null ? spawnPoint : transform;
            Vector3 pos = anchor.position;
            Quaternion rot = useSpawnRotation ? anchor.rotation : Quaternion.identity;

            _spawnedInstance = new GameObject($"Persona_{sid}");
            _spawnedInstance.transform.SetPositionAndRotation(pos, rot);

            await gltf.InstantiateMainSceneAsync(_spawnedInstance.transform);

            if (targetHeightMeters > 0f) NormalizeHeight(_spawnedInstance, targetHeightMeters);
            if (scaleMultiplier > 0f && Mathf.Abs(scaleMultiplier - 1f) > 0.0001f)
                _spawnedInstance.transform.localScale *= scaleMultiplier;

            if (verboseLog) Debug.Log($"[PersonaSpawner] 스폰 완료: pos={anchor.position}, scale={_spawnedInstance.transform.localScale}");
        }
        finally
        {
            if (loadingIndicator) loadingIndicator.SetActive(false);
            _isLoading = false;
        }
    }

    // ── 헬퍼 ───────────────────────────────────────────────────
    string ResolveSessionId()
    {
        if (!string.IsNullOrWhiteSpace(sessionIdOverride))
            return sessionIdOverride.Trim();

        string ptr = Path.Combine(dataRoot, currentSessionFile);
        if (File.Exists(ptr))
        {
            try { return File.ReadAllText(ptr).Trim(); }
            catch (Exception e) { Debug.LogWarning($"[PersonaSpawner] 포인터 파일 읽기 실패: {e.Message}"); }
        }
        return "";
    }

    async Task<bool> WaitForFileAsync(string path)
    {
        float waited = 0f;
        while (!File.Exists(path))
        {
            if (maxWaitSec > 0 && waited >= maxWaitSec)
            {
                Debug.LogWarning($"[PersonaSpawner] {maxWaitSec}초 안에 모델이 도착하지 않음: {path}");
                return false;
            }
            await Task.Delay((int)(retryIntervalSec * 1000));
            waited += retryIntervalSec;
        }
        return true;
    }

    static void NormalizeHeight(GameObject root, float targetHeight)
    {
        var renderers = root.GetComponentsInChildren<Renderer>();
        if (renderers.Length == 0) return;

        Bounds bounds = renderers[0].bounds;
        for (int i = 1; i < renderers.Length; i++) bounds.Encapsulate(renderers[i].bounds);
        if (bounds.size.y <= 0.0001f) return;

        float scale = targetHeight / bounds.size.y;
        root.transform.localScale *= scale;

    }
}
