// PersonaHumanoidProbe.cs — 프로토타입. 기존 재생 경로(PersonaSpawner 의 legacy Animation)는 건드리지 않는다.
//
// 묻는 것: Tripo 리깅 인물에 Unity Humanoid 아바타를 런타임으로 만들 수 있는가, 그리고
// 그 위에서 Mixamo 같은 외부 Humanoid 클립이 제대로 도는가.
//
// 이게 되면 Tripo 프리셋에 없는 동작(예: 컵 들어 마시기)을 크레딧 없이 가져올 수 있다.
// 프리셋 조회 결과 drink/sip/eat 계열은 존재하지 않는다(2026-09-17, 14개 이름 전부 거절).
//
// 대응표는 인물마다 만들 필요가 없다. Tripo v1.0-20240301 biped 리깅은 항상 같은 41개 뼈에
// 같은 이름을 쓴다 — 생성한 모델 8개에서 누락 0으로 확인했다.
//
// 좌표계 주의: 이 리그는 바인드 포즈에서 위=+Y 로 정상이지만 앞이 +X 다(팔은 Z축으로 벌어진다).
// Unity Humanoid 는 인물이 +Z 를 본다고 전제하므로, 아바타를 만들기 전에 90° 돌려야 한다.
// 돌린 만큼 부모를 반대로 돌려서 화면상 방향은 그대로 둔다.

using System.Collections.Generic;
using UnityEngine;
using UnityEngine.Animations;
using UnityEngine.Playables;

public static class PersonaHumanoid
{
    /// <summary>Unity Humanoid 뼈 이름 → Tripo 뼈 이름. 트위스트 뼈 14개는 매핑하지 않는다.</summary>
    public static readonly (string human, string tripo)[] Map =
    {
        ("Hips", "Hip"), ("Spine", "Waist"), ("Chest", "Spine01"), ("UpperChest", "Spine02"),
        ("Neck", "NeckTwist01"), ("Head", "Head"),
        ("LeftShoulder", "L_Clavicle"), ("LeftUpperArm", "L_Upperarm"),
        ("LeftLowerArm", "L_Forearm"), ("LeftHand", "L_Hand"),
        ("RightShoulder", "R_Clavicle"), ("RightUpperArm", "R_Upperarm"),
        ("RightLowerArm", "R_Forearm"), ("RightHand", "R_Hand"),
        ("LeftUpperLeg", "L_Thigh"), ("LeftLowerLeg", "L_Calf"),
        ("LeftFoot", "L_Foot"), ("LeftToes", "L_ToeBase"),
        ("RightUpperLeg", "R_Thigh"), ("RightLowerLeg", "R_Calf"),
        ("RightFoot", "R_Foot"), ("RightToes", "R_ToeBase"),
    };

    static Transform Find(Transform root, string name)
    {
        foreach (var t in root.GetComponentsInChildren<Transform>(true))
            if (t.name == name) return t;
        return null;
    }

    /// <summary>
    /// 뼈를 바인드 포즈(=T포즈)로 되돌린다. 아바타는 T포즈에서 만들어야 하는데, 로드된
    /// animated.glb 는 노드가 이미 자세를 먹고 있을 수 있다. 정점 가중치와 함께 저장된
    /// 바인드포즈 역행렬이 자세와 무관한 기준이라 그걸 쓴다.
    /// </summary>
    public static bool ForceBindPose(SkinnedMeshRenderer renderer)
    {
        if (renderer == null || renderer.sharedMesh == null) return false;
        var bones = renderer.bones;
        var binds = renderer.sharedMesh.bindposes;
        if (bones == null || binds == null || bones.Length != binds.Length) return false;

        var toWorld = renderer.transform.localToWorldMatrix;
        for (int i = 0; i < bones.Length; i++)
        {
            if (bones[i] == null) continue;
            Matrix4x4 m = toWorld * binds[i].inverse;
            bones[i].SetPositionAndRotation(
                m.GetColumn(3),
                Quaternion.LookRotation(m.GetColumn(2), m.GetColumn(1)));
        }
        return true;
    }

    /// <summary>
    /// 바인드 포즈 복원이 실제로 T포즈를 만들었는지 확인한다. 아바타 품질이 여기에 전적으로 달려 있다.
    /// T포즈가 아닌 상태로 구우면 Unity 가 뼈 축을 잘못 잡아 팔다리가 늘어나거나 꺾인다.
    /// </summary>
    public static string DescribePose(Transform root)
    {
        Transform lh = Find(root, "L_Hand"), rh = Find(root, "R_Hand");
        Transform arm = Find(root, "L_Upperarm"), hip = Find(root, "Hip"), head = Find(root, "Head");
        if (lh == null || rh == null || arm == null || hip == null || head == null)
            return "뼈 일부를 찾지 못해 자세를 재지 못했다";

        float span = Vector3.Distance(lh.position, rh.position);
        float torso = Vector3.Distance(hip.position, head.position);
        float drop = Mathf.Abs(lh.position.y - arm.position.y);
        bool tpose = torso > 0.0001f && drop < torso * 0.2f && span > torso;
        return $"팔 폭 {span:0.000}, 골반~머리 {torso:0.000}, 손-어깨 높이차 {drop:0.000} → " +
               (tpose ? "T포즈로 보인다" : "T포즈가 아니다 (이 상태로 구우면 팔다리가 늘어진다)");
    }

    /// <summary>인물이 보는 방향(월드). 루트 회전과 무관하게 발목→발가락 뼈로 잰다.</summary>
    public static Vector3 MeasureFacing(Transform root)
    {
        Transform lf = Find(root, "L_Foot"), lt = Find(root, "L_ToeBase");
        Transform rf = Find(root, "R_Foot"), rt = Find(root, "R_ToeBase");
        if (lf == null || lt == null || rf == null || rt == null) return root.forward;
        Vector3 f = (lt.position - lf.position) + (rt.position - rf.position);
        f = Vector3.ProjectOnPlane(f, Vector3.up);
        return f.sqrMagnitude > 1e-8f ? f.normalized : root.forward;
    }

    /// <summary>
    /// 인물 뼈대로 Humanoid 아바타를 만든다. 실패하면 null 과 이유를 돌려준다.
    /// skeletonRoot 는 뼈를 담고 있는 오브젝트다(스포너가 만든 Persona_* 의 자식).
    /// </summary>
    public static Avatar Build(Transform skeletonRoot, out string error)
    {
        error = null;
        var human = new List<HumanBone>();
        var missing = new List<string>();
        foreach (var (humanName, tripoName) in Map)
        {
            var bone = Find(skeletonRoot, tripoName);
            if (bone == null) { missing.Add(tripoName); continue; }
            human.Add(new HumanBone
            {
                humanName = humanName,
                boneName = bone.name,
                limit = new HumanLimit { useDefaultValues = true },
            });
        }
        if (missing.Count > 0)
        {
            error = "뼈를 찾지 못했다: " + string.Join(", ", missing);
            return null;
        }

        // skeleton 배열은 계층 전체의 현재(=T포즈) 로컬 TRS 를 담는다.
        var skeleton = new List<SkeletonBone>();
        foreach (var t in skeletonRoot.GetComponentsInChildren<Transform>(true))
            skeleton.Add(new SkeletonBone
            {
                name = t.name,
                position = t.localPosition,
                rotation = t.localRotation,
                scale = t.localScale,
            });

        var description = new HumanDescription
        {
            human = human.ToArray(),
            skeleton = skeleton.ToArray(),
            upperArmTwist = 0.5f, lowerArmTwist = 0.5f,
            upperLegTwist = 0.5f, lowerLegTwist = 0.5f,
            armStretch = 0.05f, legStretch = 0.05f,
            feetSpacing = 0f, hasTranslationDoF = false,
        };

        var avatar = AvatarBuilder.BuildHumanAvatar(skeletonRoot.gameObject, description);
        if (avatar == null || !avatar.isValid || !avatar.isHuman)
        {
            error = avatar == null ? "BuildHumanAvatar 가 null 을 돌려줬다"
                                   : $"아바타가 쓸 수 없는 상태다(isValid={avatar.isValid}, isHuman={avatar.isHuman})";
            return null;
        }
        avatar.name = "PersonaHumanoid";
        return avatar;
    }
}

/// <summary>
/// 시험용. 스폰된 인물(Persona_*)을 찾아 Humanoid 아바타를 만들고, 클립을 꽂아두면 재생한다.
/// 아무 오브젝트에나 붙여서 Play 하면 된다. 기존 재생 경로는 건드리지 않는다.
/// </summary>
public class PersonaHumanoidProbe : MonoBehaviour
{
    [Tooltip("붙일 Humanoid 클립. Mixamo FBX 를 Rig > Animation Type = Humanoid 로 임포트한 뒤 " +
             "그 안의 클립을 넣는다. 비워두면 아바타 생성까지만 확인한다.")]
    public AnimationClip humanoidClip;

    [Tooltip("켜면 인물이 스폰되는 즉시 시도한다. 끄면 컨텍스트 메뉴로 직접 실행한다.")]
    public bool runOnSpawn = true;

    [Tooltip("읽기용. 아바타가 만들어졌는지.")]
    public bool avatarReady;

    Transform _handled;
    PlayableGraph _graph;

    void Update()
    {
        if (!runOnSpawn || _handled != null) return;
        var found = FindPersona();
        if (found != null) Run(found);
    }

    /// <summary>
    /// 스포너가 세운 Persona_* 를 먼저 찾고, 없으면 Tripo 뼈 이름을 가진 스킨 메시를 찾는다.
    /// 모델 테스트 씬처럼 스포너를 거치지 않고 올린 모델에서도 시험할 수 있게 한다.
    /// </summary>
    static Transform FindPersona()
    {
        var renderers = FindObjectsOfType<SkinnedMeshRenderer>();
        foreach (var renderer in renderers)
        {
            var root = renderer.transform.root;
            if (root != null && root.name.StartsWith("Persona_")) return root;
        }
        foreach (var renderer in renderers)
        {
            var bones = renderer.bones;
            if (bones == null) continue;
            bool tripo = false, hip = false;
            foreach (var b in bones)
            {
                if (b == null) continue;
                if (b.name == "L_Upperarm") tripo = true;
                else if (b.name == "Hip") hip = true;
            }
            if (tripo && hip) return renderer.transform.root;
        }
        return null;
    }

    [ContextMenu("Humanoid 아바타 만들어 보기")]
    void RunFromMenu()
    {
        var found = FindPersona();
        if (found == null) { Debug.LogWarning("[PersonaHumanoidProbe] Persona_* 를 찾지 못했다 — Play 중에 실행할 것"); return; }
        Run(found);
    }

    void Run(Transform personaRoot)
    {
        _handled = personaRoot;

        var renderer = personaRoot.GetComponentInChildren<SkinnedMeshRenderer>();
        if (renderer == null) { Debug.LogError("[PersonaHumanoidProbe] 스킨 메시가 없다"); return; }

        // 스포너가 만든 Persona_* 아래에 glTF 장면이 자식으로 들어온다. 뼈대는 그 자식 쪽이다.
        Transform skeletonRoot = renderer.rootBone != null ? renderer.rootBone : renderer.transform;
        while (skeletonRoot.parent != null && skeletonRoot.parent != personaRoot) skeletonRoot = skeletonRoot.parent;

        // 1) 자세를 먹기 전 기준으로 되돌린다. legacy Animation 이 매 프레임 덮어쓰므로 먼저 끈다.
        var legacy = personaRoot.GetComponentInChildren<Animation>();
        if (legacy != null) legacy.enabled = false;

        // 스포너의 호흡이 LateUpdate 에서 가슴·목·쇄골을 계속 덮어써서 결과를 흐린다. 시험 동안 끈다.
        var spawner = FindObjectOfType<PersonaSpawner>();
        if (spawner != null && spawner.breathe)
        {
            spawner.breathe = false;
            Debug.Log("[PersonaHumanoidProbe] 시험을 위해 스포너의 호흡(breathe)을 껐다");
        }

        if (!PersonaHumanoid.ForceBindPose(renderer))
            Debug.LogWarning("[PersonaHumanoidProbe] 바인드 포즈 복원 실패 — 현재 자세로 진행한다");
        Debug.Log("[PersonaHumanoidProbe] 복원한 자세: " + PersonaHumanoid.DescribePose(skeletonRoot));

        // 2) 이 리그는 +X 를 본다. Humanoid 는 +Z 전제라 뼈대를 돌린다.
        //    아바타는 뼈대의 *로컬* rest 자세를 굽는다. 그래서 보정도 로컬에서 해야 한다 —
        //    월드 회전으로 돌려놓고 부모를 되돌리면 자식 월드가 다시 틀어져 기준이 깨진다.
        Vector3 facingLocal = personaRoot.InverseTransformDirection(PersonaHumanoid.MeasureFacing(skeletonRoot));
        facingLocal = Vector3.ProjectOnPlane(facingLocal, Vector3.up);
        if (facingLocal.sqrMagnitude < 1e-8f) facingLocal = Vector3.forward;
        Quaternion fix = Quaternion.FromToRotation(facingLocal.normalized, Vector3.forward);
        if (skeletonRoot != personaRoot)
        {
            skeletonRoot.localRotation = fix * skeletonRoot.localRotation;
            personaRoot.localRotation = personaRoot.localRotation * Quaternion.Inverse(fix);
        }
        else Debug.LogWarning("[PersonaHumanoidProbe] 뼈대와 루트가 같아 방향 보정을 상쇄하지 못한다 — 90° 틀어져 보일 수 있다");

        Debug.Log($"[PersonaHumanoidProbe] 보던 방향(루트 기준) {facingLocal.normalized} → 보정 {fix.eulerAngles.y:0}° (Y축)");

        // 3) 아바타 생성
        var avatar = PersonaHumanoid.Build(skeletonRoot, out string error);
        if (avatar == null) { Debug.LogError("[PersonaHumanoidProbe] 아바타 생성 실패 — " + error); return; }
        avatarReady = true;
        Debug.Log($"[PersonaHumanoidProbe] 아바타 생성 성공: 매핑 {PersonaHumanoid.Map.Length}개, " +
                  $"isHuman={avatar.isHuman}, isValid={avatar.isValid}");

        if (humanoidClip == null)
        {
            Debug.Log("[PersonaHumanoidProbe] 클립이 비어 있어 아바타 생성까지만 확인했다");
            return;
        }
        if (!humanoidClip.isHumanMotion)
        {
            Debug.LogError($"[PersonaHumanoidProbe] '{humanoidClip.name}' 은 Humanoid 클립이 아니다 — " +
                           "FBX 의 Rig > Animation Type 을 Humanoid 로 바꿀 것");
            return;
        }

        // 4) AnimatorController 없이 클립 하나만 돌린다.
        var animator = skeletonRoot.GetComponent<Animator>();
        if (animator == null) animator = skeletonRoot.gameObject.AddComponent<Animator>();
        animator.avatar = avatar;
        animator.applyRootMotion = false;

        if (_graph.IsValid()) _graph.Destroy();
        _graph = PlayableGraph.Create("PersonaHumanoidProbe");
        var output = AnimationPlayableOutput.Create(_graph, "out", animator);
        var playable = AnimationClipPlayable.Create(_graph, humanoidClip);
        output.SetSourcePlayable(playable);
        _graph.Play();
        Debug.Log($"[PersonaHumanoidProbe] 클립 재생: {humanoidClip.name} ({humanoidClip.length:0.00}s) — " +
                  "팔다리가 제자리에서 움직이면 리타게팅 성공이다");
    }

    void OnDestroy()
    {
        if (_graph.IsValid()) _graph.Destroy();
    }
}
