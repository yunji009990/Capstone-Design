// PersonaArrival.cs — 인물이 도착하는 연출. 경로 중간 인사를 켜면 걷기 → 인사 → 걷기 → 착석.
//
// PersonaSpawner 가 인물을 세운 뒤 Begin() 을 불러 넘겨준다. 클립이 꽂혀 있지 않으면
// 스포너는 이걸 건너뛰고 예전처럼 preset:sit 을 제자리에서 돌린다 — 기본 동작은 그대로다.
//
// 왜 도착 연출이 필요한가: 지금은 GLB 로드가 끝나는 순간 인물이 허공에 나타난다. 서버
// 폴링이라 시점도 일정하지 않다. 걸어 들어오게 하면 그 로딩 시간이 연출로 덮이고, 재회라는
// 주제에도 "이미 앉아 있음"보다 "문을 열고 들어옴"이 맞다.
//
// 재생은 Humanoid 아바타 위에서 한다. Tripo 리깅에는 걷기·인사 프리셋이 없어 외부(Mixamo)
// 클립을 써야 하고, 그러려면 런타임에 아바타를 만들어야 한다(PersonaHumanoid).
// 고정 Human 몸체의 본 매핑을 재사용한다. 이전 Tripo 리그도 별도 매핑으로 읽는다.
//
// 시선은 도착 내내 켜 둔다. 인사할 때도, 걸어오는 동안에도 체험자를 본다. 몸이 도는 것과
// 무관하게 뼈대에서 얼굴 방향을 바로 받기 때문에(facingSource) 걸어도 기준이 안 흔들린다.
// 좌우 55도·상하 22도 안에서만 돌아가므로 몸을 등지면 알아서 앞으로 돌아온다.
//
// 앉은 뒤 몸짓은 두 갈래다. 인물이 말할 때는 답변별 확률로 손짓을 한 번 내고,
// 체험자가 말할 때는 이따금 끄덕인다(맞장구). 끄덕임은 무슨 말인지 알아들어서가 아니라
// 듣고 있다는 신호라, 서버가 감정을 알려 주지 않아도 마이크 크기만으로 낼 수 있다.
//
// 앉은 뒤에는 Sitting Idle을 반복한다. 대화 제스처는 별도 클립을 연결했을 때만
// 상반신에 얹는다(아바타 마스크). Scene_2는 Sitting Talking을 평균 4회 중 1회 선택한다.
// 다리·골반은 앉은 자세가 계속 붙들고 있으므로 의자에서 뜨지 않는다.
//
// 아는 한계:
//  - 손가락이 없는 이전 Tripo 리그에서는 손 모양이 편 채 고정이다.
//  - 상반신 클립의 허리 각도가 앉은 자세와 다르면 허리께가 살짝 꺾인다. maskFromSpine 으로
//    가르는 지점을 척추/가슴 중에 고른다.
//  - 도는 클립이 한 방향뿐이라 반대로 도는 경로에서는 발이 반대로 딛는 것처럼 보인다.
//  - 바닥 경로는 그린 선을 그대로 따른다. 장애물 회피·계단용 발 IK는 별도다.
//
// 발 미끄러짐은 walkSpeed 로 맞춘다. Mixamo Walking 기준 1.0 m/s 가 맞았다.

using UnityEngine;
using System.Collections.Generic;
using UnityEngine.Animations;
using UnityEngine.Playables;

public class PersonaArrival : MonoBehaviour
{
    // 기존 씬에 저장된 상태 번호를 유지하기 위해 새 상태는 끝에 추가한다.
    public enum Phase { 대기, 인사, 걷기, 돌기, 앉는중, 앉음, 서서대기, 인사방향전환, 경로방향전환 }

    [Header("클립")]
    [Tooltip("고정 Human 몸체의 Humanoid 기준 자세. 도착 연출 준비 메뉴에서 Mixamo Avatar를 기준으로 만든다.")]
    public PersonaHumanoidReferencePose referencePose;

    [Tooltip("출발 전 대기와 중간 인사의 방향 전환 동안 쓸 서 있는 동작.")]
    public AnimationClip idleClip;

    [Tooltip("출발 전에 Idle을 재생할 시간(초). 0이면 바로 연출을 시작한다.")]
    [Min(0f)] public float initialIdleSeconds = 1f;

    [Tooltip("한 번 인사하는 동작. 걷다가 인사를 켜면 경로의 지정 위치에서 재생한다. 비우면 인사를 생략한다.")]
    public AnimationClip greetClip;

    [Tooltip("걷기. 제자리 클립이어도 된다 — 이동은 코드가 시킨다. 비어 있으면 도착 연출을 쓰지 않는다.")]
    public AnimationClip walkClip;

    [Tooltip("방향 전환. 비워두면 걷기 클립인 채로 돈다.")]
    public AnimationClip turnClip;

    [Tooltip("의자에 앉는 동작. useSitDown 을 켰을 때만 쓴다.")]
    public AnimationClip sitDownClip;

    // 앉은 대기 전용 클립이 없으면 앉은 대화 클립의 한 프레임을 정지 자세로 쓴다.
    [Tooltip("앉은 자세로 쓸 클립. 기본은 이 클립의 앞부분에서 멈춰 있는다.")]
    public AnimationClip seatedClip;

    [Header("앉은 뒤 상반신 제스처")]
    [Tooltip("말하다가 이따금 한 번씩 낼 제스처. 서 있는 클립이어도 된다 — 상반신만 쓴다.")]
    public AnimationClip talkClip;

    [Tooltip("끄덕임. 체험자가 말하는 동안 저절로 나오고, 대화 쪽에서 Nod() 로도 부른다.")]
    public AnimationClip nodClip;

    [Tooltip("부정·갸웃. 대화 쪽에서 Shake() 로 부른다.")]
    public AnimationClip shakeClip;

    [Tooltip("비워두면 실행할 때 상반신 마스크를 만든다. 직접 만든 마스크를 꽂아도 된다.")]
    public AvatarMask upperBodyMask;

    [Tooltip("켜면 척추부터, 끄면 가슴 위(머리·팔)만 제스처가 먹는다. 허리가 꺾여 보이면 끈다.")]
    public bool maskFromSpine = true;

    [Tooltip("제스처가 얹히고 걷히는 시간(초).")]
    public float gestureBlendSec = 0.3f;

    [Tooltip("앉아서 말하는 답변마다 확률로 talkClip을 한 번 선택한다. 연속 답변에는 선택하지 않는다.")]
    public bool autoTalkGesture = true;

    [Tooltip("전체 답변 중 목표 선택 비율. 0.25는 평균 4회 중 1회이며 연속 선택 금지까지 포함한다.")]
    [Range(0f, .5f)] public float talkGestureRate = .25f;

    // 예전 씬의 직렬화 값만 보관한다. 현재 답변 단위 추첨에서는 사용하지 않는다.
    [HideInInspector] public float talkMinGapSec = 4f;
    [HideInInspector] public float talkMaxGapSec = 9f;
    [HideInInspector] public float emphasisFactor = 1.6f;

    [Tooltip("체험자가 말하는 동안 이따금 끄덕인다. 듣고 있다는 신호다.")]
    public bool autoNodWhileListening = true;

    [Tooltip("체험자가 이만큼 말한 뒤부터 끄덕이기 시작한다(초). 기침 한 번에 끄덕이지 않게.")]
    public float nodAfterListeningSec = 1.2f;

    [Tooltip("끄덕임 사이 간격(초). 이 범위에서 무작위로 흩뜨린다.")]
    public float nodMinGapSec = 3f;
    public float nodMaxGapSec = 6.5f;

    [Header("경로")]
    [Tooltip("Scene 창에서 그린 바닥 경로. 첫 점에서 시작해 마지막 점까지 걷는다. 비우면 기존 경유지를 쓴다.")]
    public PersonaGroundPath groundPath;

    [Tooltip("바닥 경로의 지정 위치까지 걸어간 뒤 인사하고, 남은 경로를 이어서 걷는다.")]
    public bool greetOnPath;

    [Tooltip("경로 전체 길이에서 인사할 위치. 0은 출발점, 1은 도착점. 경로 창의 분홍색 표시로 조절한다.")]
    [Range(0f, 1f)] public float greetPathProgress = .5f;

    [Tooltip("걷기 시작 지점(카페 입구). 비워두면 이 오브젝트의 위치를 쓴다.")]
    public Transform entrance;

    [Tooltip("거쳐 갈 지점들. 순서대로 걷는다. 마지막 지점이 '의자 옆에 서는 자리'다.")]
    public Transform[] waypoints;

    [Tooltip("도착해 앉을 지점. 비워두면 스포너의 spawnPoint 를 쓴다.")]
    public Transform seat;

    [Header("맞춤")]
    [Tooltip("걷는 속도(m/s). 발이 미끄러지면 이 값으로 맞춘다.")]
    [Range(0.2f, 3f)] public float walkSpeed = 1.0f;

    [Tooltip("출발할 때 걷는 속도를 올리는 정도(m/s²).")]
    [Min(.1f)] public float walkAcceleration = 2.5f;

    [Tooltip("인사·경로 끝에서 속도를 줄이는 정도(m/s²).")]
    [Min(.1f)] public float walkDeceleration = 3.5f;

    [Tooltip("바닥 경로의 앞뒤를 함께 보고 코너 방향을 부드럽게 잇는 거리(m).")]
    [Range(0f, 1f)] public float pathFacingLookAhead = .3f;

    [Tooltip("도는 속도(도/초).")]
    [Range(30f, 540f)] public float turnSpeedDeg = 140f;

    [Tooltip("이 거리 안에 들어오면 지점에 도착한 것으로 본다(m).")]
    [Range(0.02f, 1f)] public float arriveDistance = 0.12f;

    [Tooltip("클립 사이를 섞는 시간(초).")]
    [Range(0f, 1f)] public float blendSec = 0.25f;

    [Tooltip("별도 앉기 클립 없이 서 있는 자세에서 Sitting Idle로 내려앉는 시간(초).")]
    [Range(.2f, 3f)] public float sitTransitionSeconds = 1.1f;

    [Tooltip("앉은 뒤 유지할 자세의 시각(초). 손이 편안하게 놓인 구간을 고른다.")]
    [Range(0f, 3f)] public float seatedPoseTime;

    // Mixamo 클립마다 루트 기준 골반 높이가 다르다. 실측: Sitting Clap 은 spawnPoint 그대로가
    // 맞고, Sitting(앉는 동작)은 0.3 높게 잡혀 있어 그만큼 내려야 한다.
    [Tooltip("앉은 자세에서 의자 지점에 더할 보정(m).")]
    public Vector3 seatOffset;

    [Tooltip("앉는 동작 동안에만 더할 보정(m).")]
    public Vector3 sitDownOffset = new Vector3(0f, -0.3f, 0f);

    [Header("선택")]
    [Tooltip("인사할 때 체험자(Camera.main)를 향한다. 끄면 걸어갈 방향을 향한다.")]
    public bool greetFacesUser = true;

    [Tooltip("별도 앉기 클립을 쓴다. 끄면 Sit Transition Seconds 동안 기본 앉은 자세로 섞는다.")]
    public bool useSitDown;

    [Tooltip("앉는 동안 서 있던 자리에서 의자로 서서히 옮긴다.")]
    public bool sitDownMoveToSeat = true;

    [Tooltip("앉은 클립을 계속 반복한다. 끄면 seatedPoseTime의 자세를 유지한다.")]
    public bool loopSeated;

    [Tooltip("발 IK. 아바타 비율이 조금만 어긋나도 다리를 비튼다. 기본은 끔.")]
    public bool applyFootIK;

    [Header("읽기용")]
    public Phase phase = Phase.대기;
    public float distanceLeft;
    public int waypointIndex;
    [Tooltip("앉았을 때 골반이 발밑에서 얼마나 떠 있는지(m).")]
    public float hipHeight;

    /// <summary>앉아서 대화할 준비가 됐는지. 대화 쪽에서 연출을 걸 때 본다.</summary>
    public bool IsSeated => phase == Phase.앉음;

    /// <summary>도착 연출을 돌릴 수 있는 상태인지. 걷기 클립이 없으면 스포너가 건너뛴다.</summary>
    public bool CanRun => isActiveAndEnabled && walkClip != null;

    Transform _persona, _skeleton, _hip;
    PersonaSpawner _spawner;
    Animator _animator;
    Avatar _avatar;
    PlayableGraph _graph;
    AnimationLayerMixerPlayable _layers;    // 0: 온몸(도착 연출), 1: 상반신 제스처
    AnimationMixerPlayable _mixer;
    readonly AnimationClipPlayable[] _slots = new AnimationClipPlayable[2];
    readonly AnimationMixerPlayable[] _walkMixers = new AnimationMixerPlayable[2];
    readonly AnimationClipPlayable[] _walkIdles = new AnimationClipPlayable[2];
    AnimationClipPlayable _gesture;
    AnimationClip _gestureClip;
    AvatarMask _builtMask;
    float _gestureTime, _gestureWeight;
    bool _gestureLoop, _gestureFading;
    readonly PersonaTalkGestureSelector _talkSelector = new PersonaTalkGestureSelector();
    string _talkResponseId;
    bool _talkPending, _automaticTalkActive;
    public bool IsTalkGesturePlaying => _automaticTalkActive && _gesture.IsValid() && !_gestureFading;
    float _heardFor, _sinceNod, _quietFor, _nextNodAt;
    int _active = -1;                      // 지금 무게를 올리고 있는 입력
    AnimationClip _current;
    float _clipTime, _blend, _holdAt = -1f;
    bool _loop;
    float _blendDuration, _playbackRate = 1f, _outgoingRate = 1f, _outgoingHold = -1f;
    bool _outgoingLoop;
    float _walkVelocity, _turnElapsed, _turnDuration, _sitElapsed, _sitDuration;
    Quaternion _turnFrom, _turnTo;
    bool _blendDirectlyToSeat;
    Vector3 _seatBlendOffset;
    Vector3 _sitFrom;
    PersonaGroundRoute _groundRoute;
    float _pathDistance, _rootHeightAboveFloor;
    bool _pathGreetingPending, _pathGreetingActive;
    float _pathGreetingDistance;
    Vector3 _greetingFacing, _resumeFacing;

    public bool UsesDrawnPath => _groundRoute != null;
    public float PathDistance => _pathDistance;
    public float PathLength => _groundRoute != null ? _groundRoute.Length : 0f;
    public float PathGreetingDistance => _pathGreetingDistance;
    public float CurrentWalkSpeed => _walkVelocity;
    public float LocomotionWeight => _active >= 0 && _walkMixers[_active].IsValid()
        ? _walkMixers[_active].GetInputWeight(1) : 0f;
    public float RootHeightAboveFloor => _rootHeightAboveFloor;
    public Vector3 GroundPosition => _persona != null
        ? _persona.position - Vector3.up * _rootHeightAboveFloor : transform.position;

    /// <summary>
    /// 스포너가 인물을 세운 뒤 불러 준다. 기존 legacy Animation 을 비켜세우고 Humanoid
    /// 아바타를 만든 다음 입구에서 연출을 시작한다.
    /// </summary>
    public void Begin(Transform personaRoot, PersonaSpawner spawner)
    {
        TryBegin(personaRoot, spawner, personaRoot);
    }

    /// <summary>리그 연결 성공 여부를 돌려주어 스포너가 기본 재생으로 복귀할 수 있게 한다.</summary>
    public bool TryBegin(Transform personaRoot, PersonaSpawner spawner, Transform gltfBindSpace = null)
    {
        ReleasePlayback();
        if (!CanRun || personaRoot == null || spawner == null) return false;
        foreach (var clip in new[] { idleClip, greetClip, walkClip, turnClip, sitDownClip, seatedClip, talkClip, nodClip, shakeClip })
        {
            if (clip == null || clip.isHumanMotion) continue;
            Debug.LogWarning($"[PersonaArrival] {clip.name}은 Humanoid 클립이 아니다 — 기본 재생으로 돌아간다");
            return false;
        }
        _persona = personaRoot;
        _spawner = spawner;
        var legacy = _persona.GetComponentInChildren<Animation>();
        bool legacyEnabled = legacy != null && legacy.enabled;
        if (legacy != null) legacy.enabled = false;
        if (!PersonaHumanoid.TryPrepare(_persona, out _skeleton, out _avatar, out string error, gltfBindSpace, referencePose))
        {
            Debug.LogWarning("[PersonaArrival] 아바타 생성 실패 — " + error + " (기본 재생으로 돌아간다)");
            if (legacy != null) legacy.enabled = legacyEnabled;
            return false;
        }

        _hip = PersonaHumanoid.FindBone(_skeleton, HumanBodyBones.Hips);

        _animator = _skeleton.GetComponent<Animator>();
        if (_animator == null) _animator = _skeleton.gameObject.AddComponent<Animator>();
        _animator.avatar = _avatar;
        _animator.applyRootMotion = false;   // 이동은 코드가 맡는다. 클립이 제자리든 아니든 같게 돈다

        if (_graph.IsValid()) _graph.Destroy();
        _graph = PlayableGraph.Create("PersonaArrival");
        _mixer = AnimationMixerPlayable.Create(_graph, 2);
        // 온몸 위에 상반신 한 겹을 더 얹는다. 마스크가 가린 곳은 아래층(도착 연출)이 그대로 보인다.
        _layers = AnimationLayerMixerPlayable.Create(_graph, 2);
        _layers.ConnectInput(0, _mixer, 0, 1f);
        _layers.SetLayerMaskFromAvatarMask(1, UpperBodyMask());
        AnimationPlayableOutput.Create(_graph, "out", _animator).SetSourcePlayable(_layers);
        _graph.Play();

        // 3) 호흡이 Animator 를 덮어쓰지 않게 한다. 끄는 게 아니라 기준을 매 프레임 다시 잡게
        //    한다 — 그래야 호흡이 도착 연출 위에 작은 오프셋으로 얹힌다.
        _spawner.posedExternally = true;
        // 아바타 보정 뒤에는 뼈대의 +Z 가 곧 얼굴 방향이다. 스포너가 발로 짐작하지 않게 넘겨준다.
        // 이 값은 걷든 돌든 흔들리지 않으므로, 도착 연출 내내 시선을 켜 둘 수 있다.
        _spawner.facingSource = Facing;

        // 4) 바닥 경로의 첫 점이 입구다. 높이는 첫 자세에서 한 번만 맞추고 이동 중에는 고정한다.
        if (seat == null && _spawner != null) seat = _spawner.spawnPoint;
        if (groundPath != null && groundPath.isActiveAndEnabled)
        {
            _groundRoute = groundPath.CreateRoute();
            if (_groundRoute == null)
                Debug.LogWarning("[PersonaArrival] 바닥 경로에 서로 다른 점이 2개 이상 필요합니다. 기존 경유지를 사용합니다.", this);
        }
        _pathGreetingPending = _groundRoute != null && greetOnPath && greetClip != null;
        if (_pathGreetingPending)
            _pathGreetingDistance = _groundRoute.Length * (float.IsNaN(greetPathProgress) ? .5f : Mathf.Clamp01(greetPathProgress));
        Vector3 start = entrance != null ? entrance.position : transform.position;
        if (_groundRoute != null) start = _groundRoute.Start + Vector3.up * _rootHeightAboveFloor;
        _persona.position = start;

        // 중간에서 인사할 때는 출발부터 진행 방향을 본다. 사용자 방향은 인사 지점에서 다시 잰다.
        if (!_pathGreetingPending && greetFacesUser && Camera.main != null)
        {
            Vector3 toUser = Flat(Camera.main.transform.position - _persona.position);
            if (toUser.sqrMagnitude > 1e-6f) FaceWorld(toUser.normalized);
        }
        else FaceGoal(start);

        if (idleClip != null && initialIdleSeconds > 0f)
        {
            Play(idleClip, true);
            phase = Phase.서서대기;
            Debug.Log($"[PersonaArrival] 서서 대기 — {idleClip.name} ({initialIdleSeconds:0.00}s)");
        }
        else StartGreetingOrWalking();
        if (_groundRoute != null)
        {
            // 처음 보이는 자세에서 배치 높이만 정한다. 이후 발 동작이나 콜라이더를 따라 루트를 올리지 않는다.
            _graph.Evaluate(0);
            _rootHeightAboveFloor = MeasureRootHeightAboveFloor(_persona);
            var position = _persona.position;
            position.y = _groundRoute.Start.y + _rootHeightAboveFloor;
            _persona.position = position;
        }
        return true;
    }

    void Update()
    {
        if (phase == Phase.대기 || _persona == null) return;
        Advance();
        DriveTalkGesture();
        if (phase == Phase.앉음) DriveNodGesture();

        if (phase == Phase.서서대기)
        {
            if (_clipTime >= initialIdleSeconds) StartGreetingOrWalking();
            return;
        }

        if (phase == Phase.인사)
        {
            if (ClipDone)
            {
                if (_pathGreetingActive) ResumeAfterPathGreeting();
                else StartWalking(_persona.position);
            }
            return;
        }

        if (phase == Phase.인사방향전환 || phase == Phase.경로방향전환)
        {
            if (!AdvanceFacing()) return;
            // 방향이 이미 맞아도 걷기/인사에서 서 있는 자세로 섞는 시간을 마친다.
            // 두 슬롯 믹서에서 연달아 클립을 바꿔 아직 보이는 동작을 끊지 않게 한다.
            if (_blend < 1f) return;
            if (phase == Phase.인사방향전환) StartGreeting();
            else
            {
                _pathGreetingActive = false;
                StartWalking(_persona.position);
            }
            return;
        }

        if (phase == Phase.걷기)
        {
            if (_groundRoute != null)
            {
                float limit = _pathGreetingPending ? _pathGreetingDistance : _groundRoute.Length;
                // 한 프레임에 멀리 이동해도 인사 지점을 건너뛰지 않고 정확히 멈춘다.
                float remaining = Mathf.Max(0f, limit - _pathDistance);
                float brakingSpeed = Mathf.Sqrt(2f * Mathf.Max(.1f, walkDeceleration) * remaining);
                float targetSpeed = Mathf.Min(walkSpeed, brakingSpeed);
                _walkVelocity = Mathf.MoveTowards(_walkVelocity, targetSpeed,
                    (targetSpeed > _walkVelocity ? walkAcceleration : walkDeceleration) * Time.deltaTime);
                _pathDistance = Mathf.Min(limit, _pathDistance + _walkVelocity * Time.deltaTime);
                if (limit - _pathDistance < .001f) _pathDistance = limit;
                _playbackRate = _walkVelocity / Mathf.Max(.01f, walkSpeed);
                Vector3 point = _groundRoute.Evaluate(_pathDistance, out Vector3 direction, out int segment);
                waypointIndex = segment + 1;
                distanceLeft = Mathf.Max(0f, _groundRoute.Length - _pathDistance);
                if (pathFacingLookAhead > 0f)
                {
                    Vector3 behind = _groundRoute.Evaluate(Mathf.Max(0f, _pathDistance - pathFacingLookAhead), out _, out _);
                    Vector3 ahead = _groundRoute.Evaluate(Mathf.Min(limit, _pathDistance + pathFacingLookAhead), out _, out _);
                    if ((ahead - behind).sqrMagnitude > 1e-6f) direction = (ahead - behind).normalized;
                }
                TurnToward(direction);
                // 몸의 회전 속도와 이동 선을 분리한다. 급한 코너에서도 선 밖으로 밀리지 않는다.
                _persona.position = point + Vector3.up * _rootHeightAboveFloor;
                if (_blend >= 1f)
                {
                    if (_pathGreetingPending && _pathDistance >= _pathGreetingDistance) StartPathGreeting();
                    else if (_pathDistance >= _groundRoute.Length) StartTurning();
                }
                return;
            }
            Vector3 flat = Flat(GoalAt(waypointIndex) - _persona.position);
            distanceLeft = flat.magnitude;
            if (distanceLeft <= arriveDistance)
            {
                if (waypointIndex < GoalCount - 1) { waypointIndex++; return; }
                StartTurning();
                return;
            }
            TurnToward(flat.normalized);
            _persona.position += Facing() * walkSpeed * Time.deltaTime;
        }
        else if (phase == Phase.돌기)
        {
            if (AdvanceFacing() && _blend >= 1f) StartSitting();
        }
        else if (phase == Phase.앉는중)
        {
            _sitElapsed += Time.deltaTime;
            float progress = Mathf.Clamp01(_sitElapsed / _sitDuration);
            Vector3 offset = _blendDirectlyToSeat ? Vector3.zero : sitDownOffset;
            _persona.position = Vector3.Lerp(_sitFrom, SeatPosition() + offset, Mathf.SmoothStep(0f, 1f, progress));
            if (progress >= 1f && _blend >= 1f)
            {
                // 별도 앉기 클립의 높이 보정도 다음 자세의 혼합 비율에 맞춰 없앤다.
                _seatBlendOffset = offset;
                Seat();
            }
        }
        else if (phase == Phase.앉음)
        {
            // 매 프레임 다시 놓아서 seatOffset 을 재생 중에 조절할 수 있게 한다.
            _persona.position = SeatPosition() + _seatBlendOffset * (1f - Mathf.SmoothStep(0f, 1f, _blend));
            if (_hip != null) hipHeight = _hip.position.y - _persona.position.y;
            // 박수를 한 번 친 뒤에는 다시 정지 자세로 돌아온다.
            if (!loopSeated && _holdAt < 0f && _clipTime >= (_current != null ? _current.length : 0f))
                _holdAt = seatedPoseTime;
        }
    }

    /// <summary>
    /// 실제 음성 재생이 시작된 답변만 한 번 추첨한다. 스트리밍 조각·보류 후 재개는 같은 답변이다.
    /// 다른 제스처가 끝나기를 기다리되, 발화가 끝나면 남은 요청도 버린다.
    /// </summary>
    void DriveTalkGesture()
    {
        var voice = _spawner != null ? _spawner.voiceClient : null;
        if (phase != Phase.앉음 || !autoTalkGesture || talkClip == null || voice == null ||
            !voice.IsSpeaking || string.IsNullOrEmpty(voice.CurrentResponseId))
        {
            StopAutomaticTalkGesture();
            return;
        }

        if (_talkResponseId != voice.CurrentResponseId)
        {
            StopAutomaticTalkGesture();
            _talkResponseId = voice.CurrentResponseId;
            _talkPending = _talkSelector.TrySelect(_talkResponseId, talkGestureRate, UnityEngine.Random.value);
        }
        if (!_talkPending || _gestureClip != null) return;
        _talkPending = false;
        PlayGesture(talkClip);   // 반복 없이 한 번. 음성이 먼저 끝나도 부드럽게 걷는다.
        _automaticTalkActive = _gesture.IsValid();
    }

    void StopAutomaticTalkGesture()
    {
        _talkPending = false;
        if (_automaticTalkActive) StopGesture();
    }

    /// <summary>
    /// 체험자가 말하는 동안 이따금 끄덕인다. 맞장구다 — 무슨 말인지 알아들어서가 아니라
    /// 듣고 있다는 신호라서, 서버가 감정을 알려 주지 않아도 지금 붙일 수 있다.
    ///
    /// 인물이 말하는 중에는 끄덕이지 않는다. 제 말에 제가 맞장구치는 꼴이 된다.
    /// </summary>
    void DriveNodGesture()
    {
        if (!autoNodWhileListening || nodClip == null) return;
        var voice = _spawner != null ? _spawner.voiceClient : null;
        if (voice == null) return;

        if (voice.IsSpeaking) { ResetNod(); return; }

        bool loud = voice.IsListening && voice.MicLevel > Mathf.Max(0.0001f, voice.VadThreshold);
        _quietFor = loud ? 0f : _quietFor + Time.deltaTime;

        // 음절 사이가 끊기는 건 말이 끝난 게 아니다. 짧은 공백은 이어서 센다.
        if (!loud && _quietFor >= 0.4f) { ResetNod(); return; }

        _heardFor += Time.deltaTime;
        _sinceNod += Time.deltaTime;

        if (_gestureClip != null) return;            // 뭔가 이미 얹혀 있다
        if (_heardFor < nodAfterListeningSec) return;
        if (_sinceNod < _nextNodAt) return;

        Nod();
        _sinceNod = 0f;
        _nextNodAt = UnityEngine.Random.Range(nodMinGapSec, Mathf.Max(nodMinGapSec, nodMaxGapSec));
    }

    void ResetNod()
    {
        _heardFor = _sinceNod = 0f;
        _nextNodAt = UnityEngine.Random.Range(nodMinGapSec, Mathf.Max(nodMinGapSec, nodMaxGapSec));
    }

    /// <summary>앉은 상태로 들어간다. 시선을 되돌리고 앉은 자세를 잡는다.</summary>
    void Seat()
    {
        Play(seatedClip, loopSeated, loopSeated ? -1f : seatedPoseTime);
        phase = Phase.앉음;
        string gazeState = _spawner == null ? "스포너 없음"
            : !_spawner.gaze ? "꺼짐"
            : !_spawner.GazeReady ? "켜져 있지만 머리 뼈를 못 찾음"
            : Camera.main == null && _spawner.gazeTarget == null ? "켜져 있지만 바라볼 대상이 없음"
            : "정상";
        Debug.Log($"[PersonaArrival] 착석 — 대화 준비 완료 (시선 {gazeState}, 얼굴 방향 {Facing()})");
    }

    /// <summary>중간 인사를 쓰면 먼저 걷는다. 그 외에는 기존 입구 인사를 유지한다.</summary>
    void StartGreetingOrWalking()
    {
        if (_pathGreetingPending || greetClip == null) { StartWalking(_persona.position); return; }
        StartGreeting();
    }

    void StartSitting()
    {
        _sitFrom = _persona.position;
        _sitElapsed = 0f;
        _blendDirectlyToSeat = !useSitDown || sitDownClip == null;
        _sitDuration = _blendDirectlyToSeat ? Mathf.Max(.2f, sitTransitionSeconds)
            : Mathf.Max(.2f, sitDownClip.length);
        if (!_blendDirectlyToSeat && !sitDownMoveToSeat) _sitFrom = SeatPosition() + sitDownOffset;
        if (_blendDirectlyToSeat)
            Play(seatedClip, loopSeated, loopSeated ? -1f : seatedPoseTime, transitionSeconds: _sitDuration);
        else Play(sitDownClip, false);
        phase = Phase.앉는중;
    }

    void StartGreeting()
    {
        Play(greetClip, false, restart: true);
        phase = Phase.인사;
        Debug.Log($"[PersonaArrival] 인사 — {greetClip.name} ({greetClip.length:0.00}s), 그다음 걷기");
    }

    void StartPathGreeting()
    {
        _walkVelocity = 0f;
        _pathGreetingPending = false;   // 재출발해도 같은 위치에서 인사를 반복하지 않는다.
        _pathGreetingActive = true;
        _groundRoute.Evaluate(_pathDistance, out _resumeFacing, out _);
        _greetingFacing = _resumeFacing;
        if (greetFacesUser && Camera.main != null)
        {
            Vector3 toUser = Flat(Camera.main.transform.position - _persona.position);
            if (toUser.sqrMagnitude > 1e-6f) _greetingFacing = toUser.normalized;
        }
        PlayStandingForTurn(false);
        BeginFacing(_greetingFacing, blendSec);
        phase = Phase.인사방향전환;
    }

    void ResumeAfterPathGreeting()
    {
        if (_pathDistance >= _groundRoute.Length)
        {
            _pathGreetingActive = false;
            StartTurning();
            return;
        }
        PlayStandingForTurn(true);
        BeginFacing(_resumeFacing, blendSec);
        phase = Phase.경로방향전환;
    }

    void PlayStandingForTurn(bool afterGreeting)
    {
        if (idleClip != null) Play(idleClip, true);
        else Play(greetClip, false, afterGreeting ? greetClip.length : 0f, restart: true);
    }

    /// <summary>걷기로 들어간다. 첫 목표 쪽으로 몸을 돌리고 걷기 클립을 튼다.</summary>
    void StartWalking(Vector3 from)
    {
        if (_pathGreetingPending && _pathGreetingDistance <= 0f) { StartPathGreeting(); return; }
        FaceGoal(from);
        Play(walkClip, true);
        _walkVelocity = 0f;
        if (_groundRoute != null) _playbackRate = 0f;
        phase = Phase.걷기;
        if (_spawner != null && _spawner.verboseLog)
            Debug.Log($"[PersonaArrival] 걷기 시작 — {from} → 의자 {SeatPosition()} " +
                      $"(거리 {Vector3.Distance(from, SeatPosition()):0.00}m, 속도 {walkSpeed:0.00}m/s)");
    }

    void FaceGoal(Vector3 from)
    {
        if (_groundRoute != null)
        {
            _groundRoute.Evaluate(_pathDistance, out Vector3 direction, out _);
            FaceWorld(direction);
            return;
        }
        Vector3 toGoal = Flat(GoalAt(waypointIndex) - from);
        if (toGoal.sqrMagnitude > 1e-6f) FaceWorld(toGoal.normalized);
    }

    void StartTurning()
    {
        _walkVelocity = 0f;
        Vector3 want = seat != null ? Flat(seat.forward) : Facing();
        float angle = Vector3.Angle(Facing(), want);
        // 작은 정렬에는 90도 회전 클립의 큰 발걸음을 넣지 않는다.
        var clip = angle < 30f && idleClip != null ? idleClip : turnClip;
        if (clip == null) clip = idleClip != null ? idleClip : walkClip;
        bool turningClip = clip == turnClip;
        BeginFacing(want, turningClip ? clip.length : blendSec);
        Play(clip, !turningClip);
        if (turningClip) _playbackRate = clip.length / _turnDuration;
        phase = Phase.돌기;
        if (_spawner != null && _spawner.verboseLog)
            Debug.Log("[PersonaArrival] 마지막 지점 도착 — 의자 쪽으로 돈다");
    }

    void BeginFacing(Vector3 direction, float minimumDuration)
    {
        if (direction.sqrMagnitude < 1e-6f) direction = Facing();
        _turnFrom = _persona.rotation;
        _turnTo = Quaternion.LookRotation(direction, Vector3.up) * Quaternion.Inverse(_skeleton.localRotation);
        _turnElapsed = 0f;
        // SmoothStep의 중간 속도는 평균의 1.5배. 설정한 회전 최고 속도를 넘지 않는다.
        _turnDuration = Mathf.Max(.05f, minimumDuration,
            1.5f * Quaternion.Angle(_turnFrom, _turnTo) / Mathf.Max(1f, turnSpeedDeg));
    }

    bool AdvanceFacing()
    {
        _turnElapsed += Time.deltaTime;
        float progress = Mathf.Clamp01(_turnElapsed / _turnDuration);
        _persona.rotation = Quaternion.Slerp(_turnFrom, _turnTo, Mathf.SmoothStep(0f, 1f, progress));
        return progress >= 1f;
    }

    // Renderer.bounds는 애니메이션 전체를 감싼 바운즈일 수 있어 현재 기준 자세의 실제 정점을 잰다.
    // 시작 높이 측정용이다. 재생 중에는 다시 측정하거나 높이를 따라가지 않는다.
    static float MeasureRootHeightAboveFloor(Transform root)
    {
        float bottom = float.PositiveInfinity;
        var baked = new Mesh();
        var vertices = new List<Vector3>();
        try
        {
            foreach (var skin in root.GetComponentsInChildren<SkinnedMeshRenderer>())
            {
                if (skin.sharedMesh == null) continue;
                skin.BakeMesh(baked, true);
                baked.GetVertices(vertices);
                foreach (var vertex in vertices)
                    bottom = Mathf.Min(bottom, skin.transform.TransformPoint(vertex).y);
                baked.Clear();
            }
        }
        finally { ReleaseObject(baked); }
        return float.IsPositiveInfinity(bottom) ? 0f : root.position.y - bottom;
    }

    /// <summary>
    /// 상반신 제스처를 얹는다. 앉아 있을 때만 먹는다. 같은 클립을 다시 부르면 무시한다.
    /// loop 를 끄면 한 번 재생하고 스스로 걷힌다.
    /// </summary>
    public void PlayGesture(AnimationClip clip, bool loop = false)
    {
        if (clip == null || phase != Phase.앉음 || !_graph.IsValid()) return;
        if (_gestureClip == clip && !_gestureFading) return;

        DropGesture();
        _gesture = AnimationClipPlayable.Create(_graph, clip);
        _gesture.SetSpeed(0);
        _gesture.SetApplyFootIK(false);      // 상반신만 쓰므로 발 IK 는 의미가 없다
        _layers.ConnectInput(1, _gesture, 0, 0f);
        _gestureClip = clip;
        _gestureLoop = loop;
        _gestureTime = 0f;
        _gestureWeight = 0f;
        _gestureFading = false;
    }

    /// <summary>얹혀 있는 상반신 제스처를 걷는다.</summary>
    public void StopGesture()
    {
        if (_gesture.IsValid()) _gestureFading = true;
    }

    /// <summary>끄덕인다. 대화에서 호응이 나올 때.</summary>
    public void Nod() => PlayGesture(nodClip);

    /// <summary>고개를 젓는다. 부정하거나 갸웃할 때.</summary>
    public void Shake() => PlayGesture(shakeClip);

    /// <summary>
    /// 박수를 한 번 친다. 대화에서 호응이 나올 때 부르면 된다 — 끝나면 정지 자세로 돌아온다.
    /// </summary>
    public void PlayClapOnce()
    {
        if (phase != Phase.앉음 || seatedClip == null) return;
        _holdAt = -1f;
        _loop = false;
        _clipTime = 0f;
    }

    Vector3 SeatPosition() =>
        (seat != null ? seat.position : (_persona != null ? _persona.position : transform.position)) + seatOffset;

    int GoalCount => waypoints != null && waypoints.Length > 0 ? waypoints.Length : 1;

    Vector3 GoalAt(int i)
    {
        if (waypoints == null || waypoints.Length == 0) return SeatPosition();
        var t = waypoints[Mathf.Clamp(i, 0, waypoints.Length - 1)];
        return t != null ? t.position : SeatPosition();
    }

    static Vector3 Flat(Vector3 v) { v.y = 0f; return v; }

    /// <summary>아바타 보정 뒤에는 뼈대의 +Z 가 얼굴 방향이다.</summary>
    Vector3 Facing() => Flat(_skeleton.forward).normalized;

    void FaceWorld(Vector3 dir)
    {
        if (dir.sqrMagnitude < 1e-6f) return;
        _persona.rotation = Quaternion.LookRotation(dir, Vector3.up) * Quaternion.Inverse(_skeleton.localRotation);
    }

    void TurnToward(Vector3 dir)
    {
        Quaternion now = Quaternion.LookRotation(Facing(), Vector3.up);
        Quaternion want = Quaternion.LookRotation(dir, Vector3.up);
        Quaternion next = Quaternion.RotateTowards(now, want, turnSpeedDeg * Time.deltaTime);
        _persona.rotation = next * Quaternion.Inverse(_skeleton.localRotation);
    }

    /// <summary>클립을 바꾼다. 비어 있는 입력에 얹고 무게를 그쪽으로 옮긴다.</summary>
    void Play(AnimationClip clip, bool loop, float holdAt = -1f, bool restart = false, float transitionSeconds = -1f)
    {
        if (clip == null || (!restart && clip == _current) || !_graph.IsValid()) return;
        int next = _active == 0 ? 1 : 0;
        double idleTime = _active >= 0 && _walkIdles[_active].IsValid() ? _walkIdles[_active].GetTime()
            : _current == idleClip && _active >= 0 ? _slots[_active].GetTime() : 0;
        if (_slots[next].IsValid())
        {
            _mixer.DisconnectInput(next);
            if (_walkMixers[next].IsValid())
            {
                _walkMixers[next].DisconnectInput(0); _walkMixers[next].DisconnectInput(1);
                _graph.DestroyPlayable(_walkIdles[next]); _graph.DestroyPlayable(_walkMixers[next]);
                _walkIdles[next] = default; _walkMixers[next] = default;
            }
            _graph.DestroyPlayable(_slots[next]);
        }
        _slots[next] = AnimationClipPlayable.Create(_graph, clip);
        _slots[next].SetSpeed(0); // 시간을 아래에서 직접 지정한다. 그래프 자동 진행과 중복하지 않는다.
        _slots[next].SetApplyFootIK(applyFootIK);
        if (clip == walkClip && idleClip != null)
        {
            // 속도가 줄면 다리가 공중에서 느려지는 대신 서 있는 자세로 점차 돌아온다.
            _walkIdles[next] = AnimationClipPlayable.Create(_graph, idleClip);
            _walkIdles[next].SetSpeed(0); _walkIdles[next].SetTime(idleTime);
            _walkIdles[next].SetApplyFootIK(applyFootIK);
            _walkMixers[next] = AnimationMixerPlayable.Create(_graph, 2);
            _walkMixers[next].ConnectInput(0, _walkIdles[next], 0, 1f);
            _walkMixers[next].ConnectInput(1, _slots[next], 0, 0f);
            _mixer.ConnectInput(next, _walkMixers[next], 0);
        }
        else _mixer.ConnectInput(next, _slots[next], 0);
        _outgoingLoop = _loop;
        _outgoingHold = _holdAt;
        _outgoingRate = _playbackRate;
        bool first = _active < 0;
        _active = next;
        _current = clip;
        _loop = loop;
        _holdAt = holdAt;
        _clipTime = clip == idleClip ? (float)idleTime : 0f;
        _playbackRate = 1f;
        _blendDuration = transitionSeconds < 0f ? blendSec : transitionSeconds;
        _blend = first || _blendDuration <= 0f ? 1f : 0f;
        _slots[next].SetTime(holdAt >= 0f ? Mathf.Min(holdAt, clip.length) : _clipTime);
        _mixer.SetInputWeight(next, _blend);
        if (!first) _mixer.SetInputWeight(1 - next, 1f - _blend);
    }

    /// <summary>시간을 직접 돌린다. FBX 의 loopTime 설정과 무관하게 동작한다.</summary>
    void Advance()
    {
        if (!_graph.IsValid() || _current == null || _active < 0) return;
        _clipTime += Time.deltaTime * _playbackRate;
        if (_slots[_active].IsValid())
            _slots[_active].SetTime(
                _holdAt >= 0f ? Mathf.Clamp(_holdAt, 0f, _current.length)
                : _loop ? (_current.length > 0.01f ? _clipTime % _current.length : 0f)
                : Mathf.Min(_clipTime, _current.length));

        int other = 1 - _active;
        if (_slots[other].IsValid())
        {
            float length = _slots[other].GetAnimationClip().length;
            double time = _slots[other].GetTime() + Time.deltaTime * _outgoingRate;
            _slots[other].SetTime(_outgoingHold >= 0f ? Mathf.Min(_outgoingHold, length)
                : _outgoingLoop && length > .01f ? time % length : System.Math.Min(time, length));
        }

        _blend = _blendDuration <= 0f ? 1f : Mathf.Min(1f, _blend + Time.deltaTime / _blendDuration);
        float weight = Mathf.SmoothStep(0f, 1f, _blend);
        _mixer.SetInputWeight(_active, weight);
        if (_slots[other].IsValid()) _mixer.SetInputWeight(other, 1f - weight);

        for (int slot = 0; slot < _walkMixers.Length; slot++)
        {
            if (!_walkMixers[slot].IsValid()) continue;
            float length = idleClip.length;
            _walkIdles[slot].SetTime(length > .01f ? (_walkIdles[slot].GetTime() + Time.deltaTime) % length : 0);
            float walking = phase == Phase.걷기 ? Mathf.SmoothStep(0f, 1f,
                Mathf.Clamp01(_walkVelocity / Mathf.Max(.01f, walkSpeed))) : 0f;
            _walkMixers[slot].SetInputWeight(0, 1f - walking);
            _walkMixers[slot].SetInputWeight(1, walking);
        }

        AdvanceGesture();
    }

    /// <summary>상반신 제스처의 시간과 무게를 굴린다. 다 걷히면 정리한다.</summary>
    void AdvanceGesture()
    {
        if (!_gesture.IsValid()) return;

        _gestureTime += Time.deltaTime;
        float length = _gestureClip != null ? _gestureClip.length : 0f;
        if (_gestureLoop) _gesture.SetTime(length > 0.01f ? _gestureTime % length : 0f);
        else
        {
            _gesture.SetTime(Mathf.Min(_gestureTime, length));
            if (_gestureTime >= length) _gestureFading = true;   // 한 번짜리는 끝나면 스스로 걷힌다
        }

        float step = gestureBlendSec <= 0f ? 1f : Time.deltaTime / gestureBlendSec;
        _gestureWeight = Mathf.Clamp01(_gestureWeight + (_gestureFading ? -step : step));
        _layers.SetInputWeight(1, _gestureWeight);

        if (_gestureFading && _gestureWeight <= 0f) DropGesture();
    }

    void DropGesture()
    {
        _automaticTalkActive = false;
        if (!_gesture.IsValid()) return;
        _layers.SetInputWeight(1, 0f);
        _layers.DisconnectInput(1);
        _graph.DestroyPlayable(_gesture);
        _gesture = default;
        _gestureClip = null;
        _gestureWeight = 0f;
        _gestureFading = false;
    }

    /// <summary>실행 중에 상반신 마스크를 만든다. 다리·골반은 빼고 위쪽만 켠다.</summary>
    AvatarMask UpperBodyMask()
    {
        if (upperBodyMask != null) return upperBodyMask;
        _builtMask = new AvatarMask { name = "PersonaUpperBody" };
        for (AvatarMaskBodyPart part = 0; part < AvatarMaskBodyPart.LastBodyPart; part++)
            _builtMask.SetHumanoidBodyPartActive(part, false);
        if (maskFromSpine) _builtMask.SetHumanoidBodyPartActive(AvatarMaskBodyPart.Body, true);
        _builtMask.SetHumanoidBodyPartActive(AvatarMaskBodyPart.Head, true);
        _builtMask.SetHumanoidBodyPartActive(AvatarMaskBodyPart.LeftArm, true);
        _builtMask.SetHumanoidBodyPartActive(AvatarMaskBodyPart.RightArm, true);
        _builtMask.SetHumanoidBodyPartActive(AvatarMaskBodyPart.LeftFingers, true);
        _builtMask.SetHumanoidBodyPartActive(AvatarMaskBodyPart.RightFingers, true);
        return _builtMask;
    }

    /// <summary>한 번짜리 클립이 끝까지 재생됐는지.</summary>
    bool ClipDone => _current != null && !_loop && _holdAt < 0f && _clipTime >= _current.length;

    /// <summary>씬 뷰에 경로를 그린다. 재생 없이 지점을 끌어 맞출 수 있다.</summary>
    void OnDrawGizmos()
    {
        if (groundPath != null && groundPath.isActiveAndEnabled && groundPath.PointCount >= 2)
        {
            if (greetOnPath)
            {
                var route = groundPath.CreateRoute();
                if (route != null)
                {
                    Gizmos.color = Color.magenta;
                    Gizmos.DrawWireSphere(route.Evaluate(route.Length * greetPathProgress, out _, out _), .16f);
                }
            }
            if (seat != null)
            {
                Gizmos.color = Color.yellow;
                Gizmos.DrawLine(groundPath.GetWorldPoint(groundPath.PointCount - 1), seat.position);
                Gizmos.DrawWireSphere(seat.position, .18f);
                Gizmos.DrawRay(seat.position, seat.forward * .5f);
            }
            return;
        }
        Vector3 from = entrance != null ? entrance.position : transform.position;
        Gizmos.color = Color.cyan;
        Gizmos.DrawWireSphere(from, 0.15f);
        if (waypoints != null)
        {
            foreach (var t in waypoints)
            {
                if (t == null) continue;
                Gizmos.DrawLine(from, t.position);
                Gizmos.DrawWireSphere(t.position, 0.12f);
                from = t.position;
            }
        }
        if (seat != null)
        {
            Gizmos.color = Color.yellow;
            Gizmos.DrawLine(from, seat.position);
            Gizmos.DrawWireSphere(seat.position, 0.18f);
            Gizmos.DrawRay(seat.position, seat.forward * 0.5f);   // 앉아서 바라볼 방향
        }
    }

    /// <summary>체험 종료·모델 교체 때 이동과 애니메이션을 멈추고 다음 입장을 준비한다.</summary>
    public void Stop() => ReleasePlayback();

    void ReleasePlayback()
    {
        if (_graph.IsValid()) _graph.Destroy();
        if (_animator != null && _animator.avatar == _avatar) _animator.avatar = null;
        ReleaseObject(_avatar);
        ReleaseObject(_builtMask);
        _avatar = null;
        _builtMask = null;
        _animator = null;
        _active = -1;
        _current = _gestureClip = null;
        _gesture = default;
        _slots[0] = _slots[1] = default;
        _walkMixers[0] = _walkMixers[1] = default;
        _walkIdles[0] = _walkIdles[1] = default;
        _gestureWeight = _gestureTime = _clipTime = _blend = 0f;
        _walkVelocity = _turnElapsed = _sitElapsed = _blendDuration = 0f;
        _playbackRate = _outgoingRate = 1f;
        _outgoingHold = -1f;
        _outgoingLoop = _blendDirectlyToSeat = false;
        _seatBlendOffset = Vector3.zero;
        _holdAt = -1f;
        _gestureFading = _gestureLoop = _loop = false;
        _talkSelector.Reset();
        _talkResponseId = null;
        _talkPending = _automaticTalkActive = false;
        _heardFor = _sinceNod = _quietFor = _nextNodAt = 0f;
        waypointIndex = 0;
        _groundRoute = null;
        _pathDistance = _rootHeightAboveFloor = distanceLeft = 0f;
        _pathGreetingPending = _pathGreetingActive = false;
        _pathGreetingDistance = 0f;
        _greetingFacing = _resumeFacing = Vector3.zero;
        phase = Phase.대기;
        if (_spawner != null)
        {
            _spawner.posedExternally = false;
            _spawner.facingSource = null;
        }
        _persona = _skeleton = _hip = null;
        _spawner = null;
    }

    static void ReleaseObject(Object value)
    {
        if (value == null) return;
        if (Application.isPlaying) Destroy(value);
        else DestroyImmediate(value);
    }

    void OnDestroy() => ReleasePlayback();
}
