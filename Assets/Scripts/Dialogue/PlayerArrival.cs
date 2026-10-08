// PlayerArrival.cs
// 체험자가 밖에서 안으로 걸어 들어오는 연출. 카메라(사람의 자리)만 옮기고, 발걸음 소리를 낸다.
//
// 체험 시작 단추를 누르면 ExperienceControl 이 Play() 를 부른다. 눈 감았다 뜨는 페이드가 가장
// 어두운 순간에 출발점으로 옮기고, 페이드가 밝아지는 동안 걸어 들어와 원래 앉을 자리에 선다.
//
// 헤드셋 위치 추적은 건드리지 않는다. 사람의 자리(pivot = OVRCameraRig 를 든 부모)만 옮긴다.
// 머리 흔들림(bob)은 일부러 넣지 않았다. 카메라가 위아래로 흔들리면 VR 에서 멀미가 난다.
// 걷는 느낌은 속도 변화(천천히 출발·천천히 멈춤)와 발걸음 소리로 낸다.
//
// 경로: waypoints 의 첫 점이 "밖"이고, 점들을 차례로 지나 마지막에 체험 시작 때 서 있던 자리로
// 끝난다. 비워두면 지금 자리에서 뒤쪽으로 fallbackBackDistance 만큼 떨어진 곳에서 직선으로
// 걸어온다. 점의 높이는 쓰지 않고 지금 자리의 높이로 맞춘다.
//
// 발걸음 소리는 clips 가 비어 있으면 코드로 만든 임시 소리(나무 바닥 느낌)를 쓴다. 실제 소리
// 에셋을 clips 에 꽂으면 그것으로 바뀐다.

using System.Collections;
using System.Collections.Generic;
using UnityEngine;

[DisallowMultipleComponent]
public class PlayerArrival : MonoBehaviour
{
    [Header("켜기")]
    [Tooltip("끄면 체험 시작 때 걸어 들어오지 않고 지금 자리에 그대로 있는다.")]
    public bool playWalkIn = true;

    [Tooltip("테스트용. Play 중 이 키를 누르면 바로 한 번 걸어 들어온다. None 이면 쓰지 않는다.")]
    public KeyCode testKey = KeyCode.F8;

    [Header("대상")]
    [Tooltip("옮길 대상. 보통 OVRCameraRig 를 든 부모. 비워두면 VRMoveControl 과 같은 방식으로 찾는다.")]
    public Transform pivot;

    [Header("경로")]
    [Tooltip("첫 점이 출발(바깥)이다. 점들을 지나 마지막에 원래 자리로 들어온다. 비우면 뒤쪽 직선.")]
    public List<Transform> waypoints = new List<Transform>();

    [Tooltip("waypoints 가 비었을 때, 지금 자리에서 뒤로 얼마나 떨어진 곳에서 출발할지(m).")]
    public float fallbackBackDistance = 4f;

    [Header("걷기")]
    [Tooltip("걷는 속도(m/s). 사람이 천천히 걷는 정도.")]
    public float walkSpeed = 1.0f;

    [Tooltip("출발 때 속도가 오르는 데 쓰는 거리(m).")]
    public float accelDistance = 0.6f;

    [Tooltip("멈출 때 속도가 줄어드는 데 쓰는 거리(m).")]
    public float decelDistance = 1.0f;

    [Header("시작 타이밍")]
    [Tooltip("ExperienceCues 가 있으면 눈 감는 연출이 끝난 순간에 맞춘다. 그 값이 없을 때 쓰는 대기(초).")]
    public float fallbackStartDelaySec = 1.0f;

    [Header("발걸음 소리")]
    [Tooltip("비워두면 코드로 만든 임시 소리를 쓴다. 여러 개를 꽂으면 번갈아 무작위로 고른다.")]
    public AudioClip[] clips;

    [Tooltip("한 걸음의 길이(m).")]
    public float stepLength = 0.7f;

    [Range(0f, 1f)] public float volume = 0.5f;
    [Tooltip("소리 높낮이를 걸음마다 이 만큼 흩뜨린다.")]
    [Range(0f, 0.3f)] public float pitchJitter = 0.08f;

    /// <summary>걷는 중인가.</summary>
    public bool Walking { get; private set; }

    /// <summary>다 걸어서 도착했을 때.</summary>
    public event System.Action Arrived;

    Coroutine _run;
    Vector3 _home;
    AudioSource _audio;
    AudioClip[] _generated;
    int _stepIndex;
    ExperienceCues _cues;

    void Awake()
    {
        ResolvePivot();
        _cues = FindObjectOfType<ExperienceCues>();
    }

    void Update()
    {
        if (testKey != KeyCode.None && Input.GetKeyDown(testKey)) Play(true);
    }

    void ResolvePivot()
    {
        if (pivot != null) return;
        var rig = GameObject.Find("OVRCameraRig");
        if (rig != null) pivot = rig.transform.parent != null ? rig.transform.parent : rig.transform;
    }

    /// <summary>걸어 들어오기를 시작한다. immediate 가 true 면 페이드를 기다리지 않는다(테스트용).</summary>
    public void Play(bool immediate = false)
    {
        if (!playWalkIn && !immediate) return;
        ResolvePivot();
        if (pivot == null)
        {
            Debug.LogWarning("[PlayerArrival] 옮길 대상(OVRCameraRig)을 못 찾았습니다.");
            return;
        }
        Stop(true);   // 걷는 도중이었다면 도착 자리로 돌려놓고 다시 시작한다. 길 한가운데를 자리로 잡으면 안 된다.
        _run = StartCoroutine(Run(immediate));
    }

    /// <summary>
    /// 걷기를 멈춘다. toHome 이 true 면 도착 자리로 바로 옮긴다.
    /// 걷는 도중에 체험이 끝났을 때 사람을 길 한가운데에 남기지 않으려면 true.
    /// </summary>
    public void Stop(bool toHome)
    {
        if (_run != null) { StopCoroutine(_run); _run = null; }
        if (Walking && toHome && pivot != null) pivot.position = _home;
        Walking = false;
    }

    IEnumerator Run(bool immediate)
    {
        // 사람이 서 있던 자리가 도착점이다. 운영자가 미리 옮겨 둔 자리를 그대로 존중한다.
        _home = pivot.position;
        List<Vector3> path = BuildPath(_home);
        if (path.Count < 2) yield break;

        // 눈 감는 연출: 어두워지는 동안 기다렸다가 가장 어두울 때 옮기고, 잠시 머문 뒤 걷는다.
        float toDark = 0f, hold = 0f;
        if (!immediate)
        {
            if (_cues != null) { toDark = _cues.beginDarkenSec; hold = _cues.beginHoldSec; }
            else toDark = fallbackStartDelaySec;
        }
        if (toDark > 0f) yield return new WaitForSeconds(toDark);

        Walking = true;
        pivot.position = path[0];
        if (hold > 0f) yield return new WaitForSeconds(hold);

        float total = 0f;
        for (int i = 1; i < path.Count; i++) total += Vector3.Distance(path[i - 1], path[i]);

        float walked = 0f, nextStep = 0f;
        int seg = 1;
        Vector3 pos = path[0];
        while (seg < path.Count)
        {
            float remaining = Mathf.Max(0f, total - walked);
            float ease = Mathf.Min(
                accelDistance > 0.01f ? walked / accelDistance : 1f,
                decelDistance > 0.01f ? remaining / decelDistance : 1f);
            float speed = walkSpeed * Mathf.Clamp(ease, 0.15f, 1f);
            float move = speed * Time.deltaTime;
            walked += move;

            // 이번 프레임에 나아갈 만큼을 점에서 점으로 따라간다.
            while (move > 0f && seg < path.Count)
            {
                float d = Vector3.Distance(pos, path[seg]);
                if (d <= move) { pos = path[seg]; move -= d; seg++; }
                else { pos = Vector3.MoveTowards(pos, path[seg], move); move = 0f; }
            }
            pivot.position = pos;

            while (walked >= nextStep && nextStep <= total)
            {
                Footstep(nextStep >= total - 0.01f);
                nextStep += stepLength;
            }
            yield return null;
        }

        pivot.position = _home;
        Walking = false;
        _run = null;
        Arrived?.Invoke();
    }

    List<Vector3> BuildPath(Vector3 home)
    {
        var path = new List<Vector3>();
        foreach (var w in waypoints)
            if (w != null) path.Add(new Vector3(w.position.x, home.y, w.position.z));

        if (path.Count == 0)
        {
            Vector3 back = -pivot.forward; back.y = 0f;
            if (back.sqrMagnitude < 0.0001f) back = Vector3.back;
            path.Add(home + back.normalized * fallbackBackDistance);
        }
        path.Add(home);
        return path;
    }

    // ── 발걸음 소리 ──────────────────────────────────────────────────────

    void Footstep(bool last)
    {
        EnsureAudio();
        var set = (clips != null && clips.Length > 0) ? clips : _generated;
        var clip = set[Random.Range(0, set.Length)];
        _audio.pitch = 1f + Random.Range(-pitchJitter, pitchJitter);
        // 왼발·오른발이 살짝 좌우로 갈리게 한다. 내 발소리라 크게 갈릴 필요는 없다.
        _audio.panStereo = (_stepIndex++ % 2 == 0) ? -0.12f : 0.12f;
        _audio.PlayOneShot(clip, volume * (last ? 0.7f : Random.Range(0.85f, 1f)));
    }

    void EnsureAudio()
    {
        if (_audio != null) return;
        var go = new GameObject("PlayerFootsteps");
        go.transform.SetParent(pivot != null ? pivot : transform, false);
        _audio = go.AddComponent<AudioSource>();
        _audio.playOnAwake = false;
        _audio.spatialBlend = 0f;   // 내 발소리는 머리 안쪽에서 나는 소리다.
        if (clips == null || clips.Length == 0) _generated = MakeSteps();
    }

    /// <summary>나무 바닥을 밟는 소리 비슷한 것을 만든다. 둔한 "쿵" + 짧은 마찰음.</summary>
    static AudioClip[] MakeSteps()
    {
        const int rate = 44100;
        var result = new AudioClip[4];
        for (int v = 0; v < result.Length; v++)
        {
            var rng = new System.Random(1234 + v * 77);
            int n = (int)(rate * 0.24f);
            var data = new float[n];
            float f0 = 85f + v * 9f;          // 변주마다 조금씩 다른 음높이
            float low = 0f;                    // 노이즈를 눌러 둔하게 만드는 저역 필터 상태
            for (int i = 0; i < n; i++)
            {
                float t = i / (float)rate;
                float thump = Mathf.Sin(2f * Mathf.PI * (f0 * (1f - 0.55f * Mathf.Clamp01(t / 0.12f))) * t)
                              * Mathf.Exp(-t * 28f);
                float noise = (float)(rng.NextDouble() * 2.0 - 1.0);
                low += (noise - low) * 0.18f;
                float scuff = low * Mathf.Exp(-t * 55f);
                float attack = Mathf.Clamp01(t / 0.002f);
                data[i] = (thump * 0.9f + scuff * 0.9f) * attack;
            }
            var clip = AudioClip.Create("step_" + v, n, 1, rate, false);
            clip.SetData(data, 0);
            result[v] = clip;
        }
        return result;
    }

    // ── 에디터에서 경로 보기 ─────────────────────────────────────────────

    void OnDrawGizmosSelected()
    {
        Gizmos.color = new Color(0.9f, 0.77f, 0.42f);
        Transform prev = null;
        foreach (var w in waypoints)
        {
            if (w == null) continue;
            Gizmos.DrawSphere(w.position, 0.08f);
            if (prev != null) Gizmos.DrawLine(prev.position, w.position);
            prev = w;
        }
    }

    [ContextMenu("시험: 걸어 들어오기")]
    void TestPlay() => Play(true);
}
