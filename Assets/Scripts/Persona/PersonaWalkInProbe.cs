// PersonaWalkInProbe.cs — 프로토타입. 인물이 카페 입구에서 걸어와 의자에 앉는 흐름을 시험한다.
//
// 기존 재생 경로(PersonaSpawner 의 legacy Animation)는 건드리지 않는다. Play 중에 legacy 를
// 끄고 Humanoid 아바타 위에서 외부 클립을 돌린다. PersonaHumanoidProbe 와 같은 방식이다.
//
// 아는 한계(시험이므로 그대로 둔다):
//  - 앉는 전환(Mixamo Sitting)은 붙여 봤지만 오히려 어색해서 기본으로 끄고 쓴다(useSitDown).
//    돌기가 끝나면 앉은 자세로 바로 넘어간다.
//  - 도는 클립이 한 방향뿐이다(지금은 Left Turn). 실제 회전은 코드가 시키고 클립은 보여주기용이라,
//    반대쪽으로 돌아야 하는 경로에서는 발이 반대로 딛는 것처럼 보인다.
//  - 손가락 관절이 없어 손 모양은 편 채 고정이다.
//
// 발 미끄러짐은 walkSpeed 로 맞춘다. 클립의 보폭과 실제 이동 속도가 어긋나면 스케이트를 탄다.
// 재생 중에 인스펙터에서 바꿔 가며 맞추는 게 빠르다.

using UnityEngine;
using UnityEngine.Animations;
using UnityEngine.Playables;

public class PersonaWalkInProbe : MonoBehaviour
{
    public enum Phase { 대기, 걷기, 돌기, 앉는중, 앉음 }

    [Header("클립")]
    [Tooltip("걷기. 제자리 클립이어도 된다 — 이동은 코드가 시킨다.")]
    public AnimationClip walkClip;

    [Tooltip("방향 전환. 비워두면 걷기 상태로 그냥 돈다.")]
    public AnimationClip turnClip;

    [Tooltip("의자에 앉는 동작. useSitDown 을 켰을 때만 쓴다.")]
    public AnimationClip sitDownClip;

    // 실측(2026-09-18): 앉는 동작을 거치는 쪽이 오히려 어색했다. 돌기가 끝나자마자 앉은 자세로
    // 넘어가는 편이 자연스러워 기본을 끔으로 둔다. 클립은 남겨 두니 켜면 다시 쓴다.
    [Tooltip("앉는 동작을 거친다. 끄면 돌기가 끝나자마자 앉은 자세로 넘어간다(기본).")]
    public bool useSitDown;

    // 지금 가진 앉은 클립은 Sitting Clap 뿐이라 반복시키면 계속 박수를 친다. 앞부분 한 프레임을
    // 정지 자세로 쓰고, 박수는 나중에 대화 쪽에서 호응이 나올 때 PlayClapOnce() 로 부른다.
    [Tooltip("앉은 자세로 쓸 클립. 기본은 이 클립의 앞부분에서 멈춰 있는다.")]
    public AnimationClip seatedClip;

    [Tooltip("앉은 뒤 유지할 자세의 시각(초). 박수가 시작되기 전 구간을 고른다.")]
    [Range(0f, 3f)] public float seatedPoseTime;

    [Tooltip("앉은 클립을 계속 반복한다. 켜면 박수를 계속 친다.")]
    public bool loopSeated;

    [Header("경로")]
    [Tooltip("걷기 시작 지점(카페 입구). 비워두면 이 오브젝트의 위치를 쓴다.")]
    public Transform entrance;

    [Tooltip("거쳐 갈 지점들. 순서대로 걷는다. 테이블을 피하도록 씬 뷰에서 끌어다 놓는다. " +
             "마지막 지점이 '의자 옆에 서는 자리'다 — 거기서 돌고 앉는다.")]
    public Transform[] waypoints;

    [Tooltip("도착해 앉을 지점. 비워두면 스포너의 spawnPoint 를 쓴다.")]
    public Transform seat;

    // Mixamo 클립마다 루트 기준 골반 높이가 다르다. 실측: Sitting Clap(앉은 자세)은 spawnPoint
    // 그대로가 맞고, Sitting(앉는 동작)은 0.3 높게 잡혀 있어 그만큼 내려야 한다.
    // 루트는 단계가 바뀌며 0.3 튀지만 골반은 제자리라 몸은 이어져 보인다.
    [Tooltip("앉은 자세에서 의자 지점에 더할 보정(m). 재생 중에 바꿀 수 있다.")]
    public Vector3 seatOffset;

    [Tooltip("앉는 동작 동안에만 더할 보정(m). 이 클립은 골반이 높게 잡혀 있어 Y 를 내려야 한다.")]
    public Vector3 sitDownOffset = new Vector3(0f, -0.3f, 0f);

    [Header("이동")]
    [Tooltip("걷는 속도(m/s). 발이 미끄러지면 이 값으로 맞춘다.")]
    [Range(0.2f, 3f)] public float walkSpeed = 1.0f;

    [Tooltip("도는 속도(도/초).")]
    [Range(30f, 540f)] public float turnSpeedDeg = 140f;

    [Tooltip("이 거리 안에 들어오면 도착으로 본다(m).")]
    [Range(0.02f, 1f)] public float arriveDistance = 0.12f;

    [Tooltip("클립 사이를 섞는 시간(초).")]
    [Range(0f, 1f)] public float blendSec = 0.25f;

    [Header("동작")]
    [Tooltip("인물이 스폰되면 자동으로 시작한다.")]
    public bool runOnSpawn = true;

    [Tooltip("걷는 동안 시선 추적을 끈다. 걸을 때 발이 흔들려 시선 기준이 튀기 때문이다.")]
    public bool pauseGazeWhileWalking = true;

    [Tooltip("발 IK. 아바타 비율이 조금만 어긋나도 다리를 비튼다. 시험 중엔 끄고 본다.")]
    public bool applyFootIK;

    [Tooltip("앉는 동안 서 있던 자리에서 의자로 서서히 옮긴다. 끄면 클립이 끝난 뒤 한 번에 옮긴다.")]
    public bool sitDownMoveToSeat = true;

    [Header("읽기용")]
    public Phase phase = Phase.대기;
    public float distanceLeft;
    [Tooltip("지금 향하고 있는 웨이포인트 번호.")]
    public int waypointIndex;

    [Tooltip("읽기용. 앉았을 때 골반이 바닥에서 얼마나 떠 있는지(m). 의자 앉는 면 높이와 맞춰 본다.")]
    public float hipHeight;

    Transform _persona, _skeleton, _hip;
    PersonaSpawner _spawner;
    Animator _animator;
    PlayableGraph _graph;
    AnimationMixerPlayable _mixer;
    readonly AnimationClipPlayable[] _slots = new AnimationClipPlayable[2];
    int _active = -1;                      // 지금 무게를 올리고 있는 입력
    AnimationClip _current;
    float _clipTime, _blend, _gazeWasOn;
    bool _breatheWasOn, _loop;
    Vector3 _sitFrom;
    float _holdAt = -1f;                   // 0 이상이면 그 시각에 멈춰 있는다

    void Update()
    {
        if (phase == Phase.대기)
        {
            if (!runOnSpawn) return;
            var found = FindPersona();
            if (found == null) return;
            Begin(found);
            return;
        }
        Step();
    }

    [ContextMenu("걸어오기 시작")]
    void StartFromMenu()
    {
        var found = FindPersona();
        if (found == null) { Debug.LogWarning("[PersonaWalkInProbe] 인물을 찾지 못했다 — Play 중에 실행할 것"); return; }
        Begin(found);
    }

    static Transform FindPersona()
    {
        var renderers = FindObjectsOfType<SkinnedMeshRenderer>();
        foreach (var r in renderers)
            if (r.transform.root != null && r.transform.root.name.StartsWith("Persona_")) return r.transform.root;
        foreach (var r in renderers)
        {
            var bones = r.bones;
            if (bones == null) continue;
            bool arm = false, hip = false;
            foreach (var b in bones)
            {
                if (b == null) continue;
                if (b.name == "L_Upperarm") arm = true;
                else if (b.name == "Hip") hip = true;
            }
            if (arm && hip) return r.transform.root;
        }
        return null;
    }

    void Begin(Transform personaRoot)
    {
        _persona = personaRoot;
        _spawner = FindObjectOfType<PersonaSpawner>();

        var renderer = _persona.GetComponentInChildren<SkinnedMeshRenderer>();
        if (renderer == null) { Debug.LogError("[PersonaWalkInProbe] 스킨 메시가 없다"); return; }

        _skeleton = renderer.rootBone != null ? renderer.rootBone : renderer.transform;
        while (_skeleton.parent != null && _skeleton.parent != _persona) _skeleton = _skeleton.parent;

        // 1) 기존 재생을 비켜세우고 T포즈로 되돌린다.
        var legacy = _persona.GetComponentInChildren<Animation>();
        if (legacy != null) legacy.enabled = false;
        if (!PersonaHumanoid.ForceBindPose(renderer))
            Debug.LogWarning("[PersonaWalkInProbe] 바인드 포즈 복원 실패 — 현재 자세로 진행한다");

        // 2) 이 리그는 +X 를 본다. 아바타는 뼈대의 로컬 rest 를 굽으므로 로컬에서 보정한다.
        Vector3 facing = _persona.InverseTransformDirection(PersonaHumanoid.MeasureFacing(_skeleton));
        facing = Vector3.ProjectOnPlane(facing, Vector3.up);
        if (facing.sqrMagnitude < 1e-8f) facing = Vector3.forward;
        Quaternion fix = Quaternion.FromToRotation(facing.normalized, Vector3.forward);
        if (_skeleton != _persona)
        {
            _skeleton.localRotation = fix * _skeleton.localRotation;
            _persona.localRotation = _persona.localRotation * Quaternion.Inverse(fix);
        }

        Debug.Log("[PersonaWalkInProbe] 복원한 자세: " + PersonaHumanoid.DescribePose(_skeleton));
        var avatar = PersonaHumanoid.Build(_skeleton, out string error);
        if (avatar == null) { Debug.LogError("[PersonaWalkInProbe] 아바타 생성 실패 — " + error); return; }
        Debug.Log($"[PersonaWalkInProbe] 아바타 생성: isHuman={avatar.isHuman}, isValid={avatar.isValid}");

        foreach (var t in _skeleton.GetComponentsInChildren<Transform>(true))
            if (t.name == "Hip") { _hip = t; break; }

        _animator = _skeleton.GetComponent<Animator>();
        if (_animator == null) _animator = _skeleton.gameObject.AddComponent<Animator>();
        _animator.avatar = avatar;
        _animator.applyRootMotion = false;   // 이동은 코드가 맡는다. 클립이 제자리든 아니든 같게 돈다

        if (_graph.IsValid()) _graph.Destroy();
        _graph = PlayableGraph.Create("PersonaWalkInProbe");
        _mixer = AnimationMixerPlayable.Create(_graph, 2);
        AnimationPlayableOutput.Create(_graph, "out", _animator).SetSourcePlayable(_mixer);
        _graph.Play();

        // 3) 입구로 옮기고 의자를 향해 선다.
        if (seat == null && _spawner != null) seat = _spawner.spawnPoint;
        Vector3 start = entrance != null ? entrance.position : transform.position;
        _persona.position = start;
        Vector3 toSeat = Flat(SeatPosition() - start);
        FaceWorld(toSeat.sqrMagnitude > 1e-6f ? toSeat.normalized : Facing());

        // 호흡은 LateUpdate 에서 Animator 보다 "뒤에" 척추·가슴·목·쇄골을 덮어쓴다.
        // legacy 를 꺼 둔 지금은 낡은 기준 자세로 상체를 되돌려서 팔다리가 따로 논다. 꺼야 한다.
        if (_spawner != null)
        {
            _breatheWasOn = _spawner.breathe;
            _spawner.breathe = false;
            if (pauseGazeWhileWalking) { _gazeWasOn = _spawner.gaze ? 1f : 0f; _spawner.gaze = false; }
        }

        Play(walkClip, true);
        phase = Phase.걷기;
        Debug.Log($"[PersonaWalkInProbe] 걷기 시작 — 입구 {start} → 의자 {SeatPosition()} " +
                  $"(거리 {Vector3.Distance(start, SeatPosition()):0.00}m, 속도 {walkSpeed:0.00}m/s)");
    }

    void Step()
    {
        if (_persona == null) return;
        Advance();

        if (phase == Phase.걷기)
        {
            Vector3 flat = Flat(GoalAt(waypointIndex) - _persona.position);
            distanceLeft = flat.magnitude;
            if (distanceLeft <= arriveDistance)
            {
                if (waypointIndex < GoalCount - 1)
                {
                    waypointIndex++;   // 다음 지점으로. 클립은 걷기 그대로 이어 간다
                    return;
                }
                Play(turnClip != null ? turnClip : walkClip, true);
                phase = Phase.돌기;
                Debug.Log("[PersonaWalkInProbe] 마지막 지점 도착 — 의자 쪽으로 돈다");
                return;
            }
            TurnToward(flat.normalized);
            _persona.position += Facing() * walkSpeed * Time.deltaTime;
        }
        else if (phase == Phase.돌기)
        {
            Vector3 want = seat != null ? Flat(seat.forward) : Facing();
            if (want.sqrMagnitude < 1e-6f) want = Facing();
            TurnToward(want.normalized);
            if (Vector3.Angle(Facing(), want.normalized) < 6f)
            {
                FaceWorld(want.normalized);
                if (useSitDown && sitDownClip != null)
                {
                    _sitFrom = _persona.position;   // 서 있던 자리. 앉는 동안 의자로 옮긴다
                    Play(sitDownClip, false);
                    phase = Phase.앉는중;
                    Debug.Log($"[PersonaWalkInProbe] 앉는다 — {sitDownClip.name} ({sitDownClip.length:0.00}s)");
                    return;
                }
                // 앉는 동작을 건너뛰고 바로 앉은 자세로 간다.
                _persona.position = SeatPosition();
                PlaySeated();
                phase = Phase.앉음;
                if (pauseGazeWhileWalking && _spawner != null && _gazeWasOn > 0.5f) _spawner.gaze = true;
                Debug.Log("[PersonaWalkInProbe] 착석 — 앉은 클립으로 전환(전환 동작 없음)");
            }
        }
        else if (phase == Phase.앉는중)
        {
            // 클립은 제자리에서 앉는다. 서 있던 자리에서 의자까지는 우리가 옮겨 준다.
            if (sitDownMoveToSeat && _current != null && _current.length > 0.01f)
                _persona.position = Vector3.Lerp(_sitFrom, SeatPosition() + sitDownOffset,
                                                 Mathf.SmoothStep(0f, 1f, _clipTime / _current.length));
            else
                _persona.position = SeatPosition() + sitDownOffset;
            if (ClipDone)
            {
                _persona.position = SeatPosition();
                PlaySeated();
                phase = Phase.앉음;
                if (pauseGazeWhileWalking && _spawner != null && _gazeWasOn > 0.5f) _spawner.gaze = true;
                Debug.Log("[PersonaWalkInProbe] 착석 완료 — 앉은 자세로 전환");
            }
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

    /// <summary>앉은 자세로 들어간다. 기본은 박수 전 구간에서 멈춰 있는 정지 자세다.</summary>
    void PlaySeated()
    {
        Play(seatedClip, loopSeated, loopSeated ? -1f : seatedPoseTime);
    }

    /// <summary>
    /// 박수를 한 번 친다. 대화 쪽에서 호응이 나올 때 부르면 된다 — 끝나면 알아서 정지 자세로 돌아온다.
    /// </summary>
    [ContextMenu("박수 한 번")]
    public void PlayClapOnce()
    {
        if (phase != Phase.앉음 || seatedClip == null) { Debug.LogWarning("[PersonaWalkInProbe] 앉은 상태에서만 부를 수 있다"); return; }
        _holdAt = -1f;
        _loop = false;
        _clipTime = 0f;
    }

    Vector3 SeatPosition() =>
        (seat != null ? seat.position : (_persona != null ? _persona.position : transform.position)) + seatOffset;

    /// <summary>웨이포인트가 있으면 그 개수, 없으면 의자 하나만 목표로 삼는다.</summary>
    int GoalCount => waypoints != null && waypoints.Length > 0 ? waypoints.Length : 1;

    Vector3 GoalAt(int i)
    {
        if (waypoints == null || waypoints.Length == 0) return SeatPosition();
        var t = waypoints[Mathf.Clamp(i, 0, waypoints.Length - 1)];
        return t != null ? t.position : SeatPosition();
    }

    /// <summary>씬 뷰에 경로를 그린다. 지점을 끌어다 옮기면서 테이블을 피하게 맞출 수 있다.</summary>
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
    static Vector3 Flat(Vector3 v) { v.y = 0f; return v; }

    /// <summary>아바타 보정 뒤에는 뼈대의 +Z 가 얼굴 방향이다.</summary>
    Vector3 Facing() => Flat(_skeleton.forward).normalized;

    void FaceWorld(Vector3 dir)
    {
        if (dir.sqrMagnitude < 1e-6f) return;
        Quaternion want = Quaternion.LookRotation(dir, Vector3.up);
        _persona.rotation = want * Quaternion.Inverse(_skeleton.localRotation);
    }

    void TurnToward(Vector3 dir)
    {
        Quaternion now = Quaternion.LookRotation(Facing(), Vector3.up);
        Quaternion want = Quaternion.LookRotation(dir, Vector3.up);
        Quaternion next = Quaternion.RotateTowards(now, want, turnSpeedDeg * Time.deltaTime);
        _persona.rotation = next * Quaternion.Inverse(_skeleton.localRotation);
    }

    /// <summary>클립을 바꾼다. 비어 있는 입력에 새 클립을 얹고 무게를 그쪽으로 옮긴다.</summary>
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

    /// <summary>시간을 직접 돌린다. FBX 의 loopTime 설정과 무관하게 반복된다.</summary>
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

    void OnDestroy()
    {
        if (_graph.IsValid()) _graph.Destroy();
        if (_spawner != null)
        {
            _spawner.breathe = _breatheWasOn;
            if (pauseGazeWhileWalking && _gazeWasOn > 0.5f) _spawner.gaze = true;
        }
    }
}
