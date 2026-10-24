using UnityEngine;

/// <summary>PCM 생성 뒤에 공간화를 적용하고, 음원을 로드된 인물의 머리에 따라 붙인다.</summary>
[DisallowMultipleComponent, RequireComponent(typeof(PersonaSpawner))]
[DefaultExecutionOrder(50)] // PersonaSpawner.LateUpdate의 시선·호흡·끄덕임 다음에 머리 위치를 읽는다.
[AddComponentMenu("다시봄/캐릭터 공간 음향")]
public sealed class PersonaVoiceSpatialAudio : MonoBehaviour
{
    [Tooltip("켜면 인물 위치에서 들린다. 모델이 없는 음성 전용 체험은 2D로 재생한다.")]
    public bool spatialVoice = true;
    [Tooltip("머리 본 기준 위치 보정(m). 인물 루트의 방향을 쓰며 모델 배율은 곱하지 않는다.")]
    public Vector3 headOffsetMeters = new Vector3(0f, -.07f, 0f);
    [Min(.1f), Tooltip("이 거리까지는 목소리 크기를 유지한다(m).")]
    public float minDistance = 1.5f;
    [Min(.2f), Tooltip("선형 감쇠의 최대 거리(m). 가까운 대사를 놓치지 않도록 완만하게 줄인다.")]
    public float maxDistance = 18f;

    PersonaSpawner spawner;
    AudioSource source;
    GameObject model;
    Transform head;
    bool headResolved;
    Vector3 originalPosition;
    float originalBlend, originalMin, originalMax, originalDoppler, originalSpread;
    bool originalSpatialize, originalPost;
    AudioRolloffMode originalRolloff;
    public bool FollowingHead { get; private set; }

    void Awake() => spawner = GetComponent<PersonaSpawner>();
    void LateUpdate() => RefreshSource();

    /// <summary>메인 스레드에서만 호출한다. 재생 버퍼나 소비 위치는 바꾸지 않는다.</summary>
    public void RefreshSource()
    {
        if (spawner == null) spawner = GetComponent<PersonaSpawner>();
        var next = spawner.voiceClient != null ? spawner.voiceClient.GetComponent<AudioSource>() : null;
        if (source != next)
        {
            Restore();
            source = next;
            if (source != null) Remember();
        }
        if (source == null) { FollowingHead = false; return; }

        var current = spawner.Spawned;
        if (current != model) { model = current; head = null; headResolved = false; }
        bool visible = model != null && model.activeInHierarchy;
        if (visible && !headResolved)
        {
            headResolved = true;
            head = spawner.HeadTransform != null ? spawner.HeadTransform
                : PersonaHumanoid.FindBone(model.transform, HumanBodyBones.Head);
        }

        FollowingHead = spatialVoice && visible && head != null;
        source.spatialBlend = FollowingHead ? 1f : 0f;
        source.spatialize = FollowingHead;
        // 공간화를 먼저 하면 뒤의 PCM 필터가 좌우 차이를 덮어쓰므로 필터 뒤에 적용한다.
        source.spatializePostEffects = true;
        source.dopplerLevel = 0f; // 입장·고개 움직임 때문에 음성의 높낮이가 바뀌지 않게 한다.
        source.spread = 0f;
        source.rolloffMode = AudioRolloffMode.Linear;
        source.minDistance = Mathf.Max(.1f, minDistance);
        source.maxDistance = Mathf.Max(source.minDistance + .1f, maxDistance);
        if (FollowingHead)
            source.transform.position = head.position + model.transform.rotation * headOffsetMeters;
    }

    void Remember()
    {
        originalPosition = source.transform.position;
        originalBlend = source.spatialBlend;
        originalSpatialize = source.spatialize;
        originalPost = source.spatializePostEffects;
        originalMin = source.minDistance;
        originalMax = source.maxDistance;
        originalDoppler = source.dopplerLevel;
        originalSpread = source.spread;
        originalRolloff = source.rolloffMode;
    }

    void Restore()
    {
        if (source != null)
        {
            source.transform.position = originalPosition;
            source.spatialBlend = originalBlend;
            source.spatialize = originalSpatialize;
            source.spatializePostEffects = originalPost;
            source.minDistance = originalMin;
            source.maxDistance = originalMax;
            source.dopplerLevel = originalDoppler;
            source.spread = originalSpread;
            source.rolloffMode = originalRolloff;
        }
        source = null;
        model = null;
        head = null;
        headResolved = false;
        FollowingHead = false;
    }

    void OnDisable() => Restore();
    void OnDestroy() => Restore();
}
