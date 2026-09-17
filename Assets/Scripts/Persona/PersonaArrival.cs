// PersonaArrival.cs — 인물이 도착하는 연출. 인사 → 걷기 → (돌기) → 앉음.
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
// 대응표는 인물마다 만들지 않는다 — Tripo v1.0 biped 는 항상 같은 41개 뼈에 같은 이름이다.
//
// 아는 한계:
//  - 손가락 관절이 없어 손 모양은 편 채 고정이다.
//  - 도는 클립이 한 방향뿐이라 반대로 도는 경로에서는 발이 반대로 딛는 것처럼 보인다.
//  - 경유지 사이는 직선이다. 곡선으로 돌아와야 하면 지점을 촘촘히 둔다.
//
// 발 미끄러짐은 walkSpeed 로 맞춘다. Mixamo Walking 기준 1.0 m/s 가 맞았다.

using UnityEngine;
using UnityEngine.Animations;
using UnityEngine.Playables;

public class PersonaArrival : MonoBehaviour
{
    public enum Phase { 대기, 인사, 걷기, 돌기, 앉는중, 앉음 }

    [Header("클립")]
    [Tooltip("걷기 전에 한 번 인사한다. 비워두면 바로 걷기로 시작한다.")]
    public AnimationClip greetClip;

    [Tooltip("걷기. 제자리 클립이어도 된다 — 이동은 코드가 시킨다. 비어 있으면 도착 연출을 쓰지 않는다.")]
    public AnimationClip walkClip;

    [Tooltip("방향 전환. 비워두면 걷기 클립인 채로 돈다.")]
    public AnimationClip turnClip;

    [Tooltip("의자에 앉는 동작. useSitDown 을 켰을 때만 쓴다.")]
    public AnimationClip sitDownClip;

    // 가진 앉은 클립이 Sitting Clap 뿐이라 반복시키면 계속 박수를 친다. 앞부분 한 프레임을
    // 정지 자세로 쓰고, 박수는 대화 쪽에서 호응이 나올 때 PlayClapOnce() 로 부른다.
    [Tooltip("앉은 자세로 쓸 클립. 기본은 이 클립의 앞부분에서 멈춰 있는다.")]
    public AnimationClip seatedClip;

    [Header("경로")]
    [Tooltip("걷기 시작 지점(카페 입구). 비워두면 이 오브젝트의 위치를 쓴다.")]
    public Transform entrance;

    [Tooltip("거쳐 갈 지점들. 순서대로 걷는다. 마지막 지점이 '의자 옆에 서는 자리'다.")]
    public Transform[] waypoints;

    [Tooltip("도착해 앉을 지점. 비워두면 스포너의 spawnPoint 를 쓴다.")]
    public Transform seat;

    [Header("맞춤")]
    [Tooltip("걷는 속도(m/s). 발이 미끄러지면 이 값으로 맞춘다.")]
    [Range(0.2f, 3f)] public float walkSpeed = 1.0f;

    [Tooltip("도는 속도(도/초).")]
    [Range(30f, 540f)] public float turnSpeedDeg = 140f;

    [Tooltip("이 거리 안에 들어오면 지점에 도착한 것으로 본다(m).")]
    [Range(0.02f, 1f)] public float arriveDistance = 0.12f;

    [Tooltip("클립 사이를 섞는 시간(초).")]
    [Range(0f, 1f)] public float blendSec = 0.25f;

    [Tooltip("앉은 뒤 유지할 자세의 시각(초). 박수가 시작되기 전 구간을 고른다.")]
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

    // 실측(2026-09-18): 앉는 동작을 거치는 쪽이 오히려 어색했다. 돌기가 끝나자마자 앉은
    // 자세로 넘어가는 편이 자연스러워 기본을 끔으로 둔다.
    [Tooltip("앉는 동작을 거친다. 끄면 돌기가 끝나자마자 앉은 자세로 넘어간다(기본).")]
    public bool useSitDown;

    [Tooltip("앉는 동안 서 있던 자리에서 의자로 서서히 옮긴다.")]
    public bool sitDownMoveToSeat = true;

    [Tooltip("앉은 클립을 계속 반복한다. 켜면 박수를 계속 친다.")]
    public bool loopSeated;

    [Tooltip("걷는 동안 시선 추적을 끈다. 인사 단계에서는 켠 채로 둔다.")]
    public bool pauseGazeWhileWalking = true;

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
    public bool CanRun => walkClip != null;

    Transform _persona, _skeleton, _hip;
    PersonaSpawner _spawner;
    Animator _animator;
    PlayableGraph _graph;
    AnimationMixerPlayable _mixer;
    readonly AnimationClipPlayable[] _slots = new AnimationClipPlayable[2];
    int _active = -1;                      // 지금 무게를 올리고 있는 입력
    AnimationClip _current;
    float _clipTime, _blend, _holdAt = -1f, _gazeWasOn;
    bool _loop;
    Vector3 _sitFrom;

    /// <summary>
    /// 스포너가 인물을 세운 뒤 불러 준다. 기존 legacy Animation 을 비켜세우고 Humanoid
    /// 아바타를 만든 다음 입구에서 연출을 시작한다.
    /// </summary>
    public void Begin(Transform personaRoot, PersonaSpawner spawner)
    {
        _persona = personaRoot;
        _spawner = spawner;

        var renderer = _persona.GetComponentInChildren<SkinnedMeshRenderer>();
        if (renderer == null) { Debug.LogError("[PersonaArrival] 스킨 메시가 없다"); return; }

        _skeleton = renderer.rootBone != null ? renderer.rootBone : renderer.transform;
        while (_skeleton.parent != null && _skeleton.parent != _persona) _skeleton = _skeleton.parent;

        // 1) 기존 재생을 비켜세우고 바인드 포즈(=T포즈)로 되돌린다. 아바타는 T포즈에서 구워야 한다.
        var legacy = _persona.GetComponentInChildren<Animation>();
        if (legacy != null) legacy.enabled = false;
        if (!PersonaHumanoid.ForceBindPose(renderer))
            Debug.LogWarning("[PersonaArrival] 바인드 포즈 복원 실패 — 현재 자세로 진행한다");

        // 2) 이 리그는 +X 를 본다. 아바타는 뼈대의 *로컬* rest 를 굽으므로 보정도 로컬에서 한다.
        //    월드로 돌려놓고 부모를 되돌리면 기준이 다시 깨져 팔다리가 늘어진다.
        Vector3 facing = _persona.InverseTransformDirection(PersonaHumanoid.MeasureFacing(_skeleton));
        facing = Vector3.ProjectOnPlane(facing, Vector3.up);
        if (facing.sqrMagnitude < 1e-8f) facing = Vector3.forward;
        Quaternion fix = Quaternion.FromToRotation(facing.normalized, Vector3.forward);
        if (_skeleton != _persona)
        {
            _skeleton.localRotation = fix * _skeleton.localRotation;
            _persona.localRotation = _persona.localRotation * Quaternion.Inverse(fix);
        }

        var avatar = PersonaHumanoid.Build(_skeleton, out string error);
        if (avatar == null)
        {
            Debug.LogError("[PersonaArrival] 아바타 생성 실패 — " + error + " (도착 연출을 건너뛴다)");
            if (legacy != null) legacy.enabled = true;
            return;
        }

        foreach (var t in _skeleton.GetComponentsInChildren<Transform>(true))
            if (t.name == "Hip") { _hip = t; break; }

        _animator = _skeleton.GetComponent<Animator>();
        if (_animator == null) _animator = _skeleton.gameObject.AddComponent<Animator>();
        _animator.avatar = avatar;
        _animator.applyRootMotion = false;   // 이동은 코드가 맡는다. 클립이 제자리든 아니든 같게 돈다

        if (_graph.IsValid()) _graph.Destroy();
        _graph = PlayableGraph.Create("PersonaArrival");
        _mixer = AnimationMixerPlayable.Create(_graph, 2);
        AnimationPlayableOutput.Create(_graph, "out", _animator).SetSourcePlayable(_mixer);
        _graph.Play();

        // 3) 호흡이 Animator 를 덮어쓰지 않게 한다. 끄는 게 아니라 기준을 매 프레임 다시 잡게
        //    한다 — 그래야 호흡이 도착 연출 위에 작은 오프셋으로 얹힌다.
        _spawner.posedExternally = true;
        _gazeWasOn = _spawner.gaze ? 1f : 0f;

        // 4) 입구에 세운다.
        if (seat == null && _spawner != null) seat = _spawner.spawnPoint;
        Vector3 start = entrance != null ? entrance.position : transform.position;
        _persona.position = start;

        if (greetClip != null)
        {
            // 인사는 제자리에서 한다. 시선은 켠 채로 둔다 — 발이 안 흔들려 기준이 안정적이고,
            // 인사는 팔·상체가 하고 눈은 사람을 보는 편이 자연스럽다.
            if (greetFacesUser && Camera.main != null)
            {
                Vector3 toUser = Flat(Camera.main.transform.position - _persona.position);
                if (toUser.sqrMagnitude > 1e-6f) FaceWorld(toUser.normalized);
            }
            else FaceGoal(start);
            Play(greetClip, false);
            phase = Phase.인사;
            Debug.Log($"[PersonaArrival] 인사 — {greetClip.name} ({greetClip.length:0.00}s), 그다음 걷기");
            return;
        }
        StartWalking(start);
    }

    void Update()
    {
        if (phase == Phase.대기 || _persona == null) return;
        Advance();

        if (phase == Phase.인사)
        {
            if (ClipDone) StartWalking(_persona.position);   // 인사 중에는 제자리에 선다
            return;
        }

        if (phase == Phase.걷기)
        {
            Vector3 flat = Flat(GoalAt(waypointIndex) - _persona.position);
            distanceLeft = flat.magnitude;
            if (distanceLeft <= arriveDistance)
            {
                if (waypointIndex < GoalCount - 1) { waypointIndex++; return; }
                Play(turnClip != null ? turnClip : walkClip, true);
                phase = Phase.돌기;
                if (_spawner != null && _spawner.verboseLog)
                    Debug.Log("[PersonaArrival] 마지막 지점 도착 — 의자 쪽으로 돈다");
                return;
            }
            TurnToward(flat.normalized);
            _persona.position += Facing() * walkSpeed * Time.deltaTime;
        }
        else if (phase == Phase.돌기)
        {
            Vector3 want = seat != null ? Flat(seat.forward) : Facing();
            if (want.sqrMagnitude < 1e-6f) want = Facing();
            want = want.normalized;
            TurnToward(want);
            if (Vector3.Angle(Facing(), want) >= 6f) return;

            FaceWorld(want);
            if (useSitDown && sitDownClip != null)
            {
                _sitFrom = _persona.position;   // 서 있던 자리. 앉는 동안 의자로 옮긴다
                Play(sitDownClip, false);
                phase = Phase.앉는중;
                return;
            }
            _persona.position = SeatPosition();
            Seat();
        }
        else if (phase == Phase.앉는중)
        {
            // 클립은 제자리에서 앉는다. 서 있던 자리에서 의자까지는 우리가 옮겨 준다.
            if (sitDownMoveToSeat && _current != null && _current.length > 0.01f)
                _persona.position = Vector3.Lerp(_sitFrom, SeatPosition() + sitDownOffset,
                                                 Mathf.SmoothStep(0f, 1f, _clipTime / _current.length));
            else
                _persona.position = SeatPosition() + sitDownOffset;

            if (ClipDone) { _persona.position = SeatPosition(); Seat(); }
        }
        else if (phase == Phase.앉음)
        {
            // 매 프레임 다시 놓아서 seatOffset 을 재생 중에 조절할 수 있게 한다.
            _persona.position = SeatPosition();
            if (_hip != null) hipHeight = _hip.position.y - _persona.position.y;
            // 박수를 한 번 친 뒤에는 다시 정지 자세로 돌아온다.
            if (!loopSeated && _holdAt < 0f && _clipTime >= (_current != null ? _current.length : 0f))
                _holdAt = seatedPoseTime;
        }
    }

    /// <summary>앉은 상태로 들어간다. 시선을 되돌리고 앉은 자세를 잡는다.</summary>
    void Seat()
    {
        Play(seatedClip, loopSeated, loopSeated ? -1f : seatedPoseTime);
        phase = Phase.앉음;
        if (pauseGazeWhileWalking && _spawner != null && _gazeWasOn > 0.5f) _spawner.gaze = true;
        Debug.Log("[PersonaArrival] 착석 — 대화 준비 완료");
    }

    /// <summary>걷기로 들어간다. 첫 목표 쪽으로 몸을 돌리고 걷기 클립을 튼다.</summary>
    void StartWalking(Vector3 from)
    {
        // 걸을 때는 발이 번갈아 흔들려 얼굴 방향 기준이 튄다. 여기서 끄고 앉으면 되돌린다.
        if (pauseGazeWhileWalking && _spawner != null) _spawner.gaze = false;
        FaceGoal(from);
        Play(walkClip, true);
        phase = Phase.걷기;
        if (_spawner != null && _spawner.verboseLog)
            Debug.Log($"[PersonaArrival] 걷기 시작 — {from} → 의자 {SeatPosition()} " +
                      $"(거리 {Vector3.Distance(from, SeatPosition()):0.00}m, 속도 {walkSpeed:0.00}m/s)");
    }

    void FaceGoal(Vector3 from)
    {
        Vector3 toGoal = Flat(GoalAt(waypointIndex) - from);
        if (toGoal.sqrMagnitude > 1e-6f) FaceWorld(toGoal.normalized);
    }

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
    void Play(AnimationClip clip, bool loop, float holdAt = -1f)
    {
        if (clip == null || clip == _current || !_graph.IsValid()) return;
        int next = _active == 0 ? 1 : 0;
        if (_slots[next].IsValid())
        {
            _mixer.DisconnectInput(next);
            _graph.DestroyPlayable(_slots[next]);
        }
        _slots[next] = AnimationClipPlayable.Create(_graph, clip);
        _slots[next].SetApplyFootIK(applyFootIK);
        _mixer.ConnectInput(next, _slots[next], 0);
        _active = next;
        _current = clip;
        _loop = loop;
        _holdAt = holdAt;
        _clipTime = 0f;
        _blend = blendSec <= 0f ? 1f : 0f;
    }

    /// <summary>시간을 직접 돌린다. FBX 의 loopTime 설정과 무관하게 동작한다.</summary>
    void Advance()
    {
        if (!_graph.IsValid() || _current == null || _active < 0) return;
        _clipTime += Time.deltaTime;
        if (_slots[_active].IsValid())
            _slots[_active].SetTime(
                _holdAt >= 0f ? Mathf.Clamp(_holdAt, 0f, _current.length)
                : _loop ? (_current.length > 0.01f ? _clipTime % _current.length : 0f)
                : Mathf.Min(_clipTime, _current.length));

        int other = 1 - _active;
        if (_slots[other].IsValid())
            _slots[other].SetTime(_slots[other].GetTime() + Time.deltaTime);   // 섞이는 동안 멈춰 보이지 않게

        _blend = blendSec <= 0f ? 1f : Mathf.Min(1f, _blend + Time.deltaTime / blendSec);
        _mixer.SetInputWeight(_active, _blend);
        if (_slots[other].IsValid()) _mixer.SetInputWeight(other, 1f - _blend);
    }

    /// <summary>한 번짜리 클립이 끝까지 재생됐는지.</summary>
    bool ClipDone => _current != null && !_loop && _holdAt < 0f && _clipTime >= _current.length;

    /// <summary>씬 뷰에 경로를 그린다. 재생 없이 지점을 끌어 맞출 수 있다.</summary>
    void OnDrawGizmos()
    {
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

    void OnDestroy()
    {
        if (_graph.IsValid()) _graph.Destroy();
        if (_spawner != null) _spawner.posedExternally = false;
    }
}
