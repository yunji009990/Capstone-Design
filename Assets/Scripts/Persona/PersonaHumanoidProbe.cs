// 고정 Human 몸체와 이전 Tripo 몸체의 뼈를 Unity Humanoid에 연결한다.
// 메시·웨이트·본 이름을 바꾸지 않는다. 사진마다 달라지는 머리도 같은 몸체 리그를 따른다.
// GLB에는 Unity Avatar가 없으므로 런타임에 기준 자세와 정면 방향을 맞춰 만든다.

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

    // Assets/Models/human/Human.fbx 및 이 몸체에서 내보낸 GLB의 변형용 본.
    // twist 본은 부모를 따라가며, 손가락은 제스처 클립의 손 모양까지 전달한다.
    public static readonly (string human, string tripo)[] FixedBodyMap =
    {
        ("Hips", "root.x"), ("Spine", "spine_01.x"), ("Chest", "spine_02.x"),
        ("UpperChest", "spine_03.x"), ("Neck", "neck.x"), ("Head", "head.x"),
        ("LeftShoulder", "shoulder.l"), ("LeftUpperArm", "arm_stretch.l"),
        ("LeftLowerArm", "forearm_stretch.l"), ("LeftHand", "hand.l"),
        ("RightShoulder", "shoulder.r"), ("RightUpperArm", "arm_stretch.r"),
        ("RightLowerArm", "forearm_stretch.r"), ("RightHand", "hand.r"),
        ("LeftUpperLeg", "thigh_stretch.l"), ("LeftLowerLeg", "leg_stretch.l"),
        ("LeftFoot", "foot.l"), ("LeftToes", "toes_01.l"),
        ("RightUpperLeg", "thigh_stretch.r"), ("RightLowerLeg", "leg_stretch.r"),
        ("RightFoot", "foot.r"), ("RightToes", "toes_01.r"),
        ("LeftThumbProximal", "c_thumb1.l"), ("LeftThumbIntermediate", "c_thumb2.l"), ("LeftThumbDistal", "c_thumb3.l"),
        ("LeftIndexProximal", "c_index1.l"), ("LeftIndexIntermediate", "c_index2.l"), ("LeftIndexDistal", "c_index3.l"),
        ("LeftMiddleProximal", "c_middle1.l"), ("LeftMiddleIntermediate", "c_middle2.l"), ("LeftMiddleDistal", "c_middle3.l"),
        ("LeftRingProximal", "c_ring1.l"), ("LeftRingIntermediate", "c_ring2.l"), ("LeftRingDistal", "c_ring3.l"),
        ("LeftLittleProximal", "c_pinky1.l"), ("LeftLittleIntermediate", "c_pinky2.l"), ("LeftLittleDistal", "c_pinky3.l"),
        ("RightThumbProximal", "c_thumb1.r"), ("RightThumbIntermediate", "c_thumb2.r"), ("RightThumbDistal", "c_thumb3.r"),
        ("RightIndexProximal", "c_index1.r"), ("RightIndexIntermediate", "c_index2.r"), ("RightIndexDistal", "c_index3.r"),
        ("RightMiddleProximal", "c_middle1.r"), ("RightMiddleIntermediate", "c_middle2.r"), ("RightMiddleDistal", "c_middle3.r"),
        ("RightRingProximal", "c_ring1.r"), ("RightRingIntermediate", "c_ring2.r"), ("RightRingDistal", "c_ring3.r"),
        ("RightLittleProximal", "c_pinky1.r"), ("RightLittleIntermediate", "c_pinky2.r"), ("RightLittleDistal", "c_pinky3.r"),
    };

    public static (string human, string tripo)[] Mapping(Transform root) =>
        Find(root, "root.x") != null ? FixedBodyMap : Map;

    /// <summary>초기화 때 찾고, 재생 중에는 호출 쪽에서 참조를 보관한다.</summary>
    public static Transform FindBone(Transform root, HumanBodyBones bone)
    {
        string name = bone.ToString();
        foreach (var entry in Mapping(root))
            if (entry.human == name) return Find(root, entry.tripo);
        return null;
    }

    static Transform Find(Transform root, string name)
    {
        if (root == null) return null;
        foreach (var t in root.GetComponentsInChildren<Transform>(true))
            if (t.name == name) return t;
        return null;
    }

    /// <summary>
    /// 뼈를 스킨의 바인드 자세로 되돌린다. 바인드 자세가 Humanoid의 T포즈와 같은 것은 아니다. 로드된
    /// animated.glb 는 노드가 이미 자세를 먹고 있을 수 있다. 정점 가중치와 함께 저장된
    /// 바인드포즈 역행렬이 자세와 무관한 기준이라 그걸 쓴다.
    /// </summary>
    public static bool ForceBindPose(SkinnedMeshRenderer renderer)
    {
        return RestoreBindPose(new[] { renderer });
    }

    static bool RestoreBindPose(SkinnedMeshRenderer[] renderers, Transform gltfBindSpace = null)
    {
        // 결합 GLB의 첫 메시가 머리일 수도 있다. 모든 스킨에서 기준 행렬을 먼저 수집한다.
        var poses = new Dictionary<Transform, Matrix4x4>();
        foreach (var renderer in renderers)
        {
            if (renderer == null || renderer.sharedMesh == null) continue;
            var bones = renderer.bones;
            var binds = renderer.sharedMesh.bindposes;
            if (bones.Length != binds.Length) continue;
            // glTF의 inverseBindMatrices는 파일의 공통 좌표계 기준이다.
            // 메시 노드의 0.01 배율을 다시 곱하면 Human의 팔다리가 100배 줄어든다.
            // FBX의 Unity bindposes는 Renderer 기준이므로 기존 행렬을 사용한다.
            var toWorld = gltfBindSpace != null ? gltfBindSpace.localToWorldMatrix
                                               : renderer.transform.localToWorldMatrix;
            for (int i = 0; i < bones.Length; i++)
                if (bones[i] != null && !poses.ContainsKey(bones[i]))
                    poses.Add(bones[i], toWorld * binds[i].inverse);
        }
        var ordered = new List<Transform>(poses.Keys);
        ordered.Sort((a, b) => Depth(a).CompareTo(Depth(b)));
        // 부모를 나중에 복원하면 이미 맞춘 자식까지 움직이므로 계층 순서로 적용한다.
        foreach (var bone in ordered)
        {
            var matrix = poses[bone];
            bone.SetPositionAndRotation(matrix.GetColumn(3),
                Quaternion.LookRotation(matrix.GetColumn(2), matrix.GetColumn(1)));
        }
        return ordered.Count > 0;
    }

    static int Depth(Transform bone)
    {
        int depth = 0;
        while (bone.parent != null) { depth++; bone = bone.parent; }
        return depth;
    }

    /// <summary>몸체 전체를 기준 자세로 맞추고 Avatar를 만든다. 실패하면 원래 자세를 복원한다.</summary>
    public static bool TryPrepare(Transform personaRoot, out Transform skeletonRoot,
                                  out Avatar avatar, out string error, Transform gltfBindSpace = null,
                                  PersonaHumanoidReferencePose referencePose = null)
    {
        skeletonRoot = null;
        avatar = null;
        error = null;
        if (personaRoot == null) { error = "인물 루트가 없다"; return false; }
        var hip = FindBone(personaRoot, HumanBodyBones.Hips);
        if (hip == null) { error = "몸체의 골반 본을 찾지 못했다"; return false; }
        skeletonRoot = hip;
        while (skeletonRoot.parent != null && skeletonRoot.parent != personaRoot)
            skeletonRoot = skeletonRoot.parent;

        var transforms = personaRoot.GetComponentsInChildren<Transform>(true);
        var positions = new Vector3[transforms.Length];
        var rotations = new Quaternion[transforms.Length];
        for (int i = 0; i < transforms.Length; i++)
        {
            positions[i] = transforms[i].localPosition;
            rotations[i] = transforms[i].localRotation;
        }
        if (!RestoreBindPose(personaRoot.GetComponentsInChildren<SkinnedMeshRenderer>(true), gltfBindSpace))
            error = "몸체의 스킨 바인드 포즈가 없다";
        else
        {
            Vector3 facing = personaRoot.InverseTransformDirection(MeasureFacing(personaRoot));
            facing = Vector3.ProjectOnPlane(facing, Vector3.up);
            if (facing.sqrMagnitude < 1e-8f) facing = Vector3.forward;
            Quaternion fix = Quaternion.FromToRotation(facing.normalized, Vector3.forward);
            if (skeletonRoot != personaRoot)
            {
                skeletonRoot.localRotation = fix * skeletonRoot.localRotation;
                personaRoot.localRotation *= Quaternion.Inverse(fix);
            }
            // 바인드 자세와 Humanoid 기준 자세는 다르다. 같은 몸체로 만든 Mixamo 기준을 먼저 맞춘다.
            if (referencePose == null || Mapping(personaRoot) != FixedBodyMap || referencePose.TryApply(personaRoot, out error))
            {
                avatar = Build(skeletonRoot, out error);
                if (avatar != null) return true;
            }
        }
        for (int i = 0; i < transforms.Length; i++)
        {
            transforms[i].localPosition = positions[i];
            transforms[i].localRotation = rotations[i];
        }
        return false;
    }

    /// <summary>
    /// 팔 높이로 기준 자세를 대략 확인한다. 정확한 Humanoid 기준은 애니메이션 Avatar에 맞춘다.
    /// T포즈가 아닌 상태로 구우면 Unity 가 뼈 축을 잘못 잡아 팔다리가 늘어나거나 꺾인다.
    /// </summary>
    public static string DescribePose(Transform root)
    {
        Transform lh = FindBone(root, HumanBodyBones.LeftHand), rh = FindBone(root, HumanBodyBones.RightHand);
        Transform arm = FindBone(root, HumanBodyBones.LeftUpperArm), hip = FindBone(root, HumanBodyBones.Hips),
                  head = FindBone(root, HumanBodyBones.Head);
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
        Transform lf = FindBone(root, HumanBodyBones.LeftFoot), lt = FindBone(root, HumanBodyBones.LeftToes);
        Transform rf = FindBone(root, HumanBodyBones.RightFoot), rt = FindBone(root, HumanBodyBones.RightToes);
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
        var humanNames = HumanTrait.BoneName;
        foreach (var (humanName, tripoName) in Mapping(skeletonRoot))
        {
            var bone = Find(skeletonRoot, tripoName);
            if (bone == null) { missing.Add(tripoName); continue; }
            human.Add(new HumanBone
            {
                // 손가락의 공식 이름에는 공백이 있다("Left Thumb Proximal").
                humanName = humanNames[(int)(HumanBodyBones)System.Enum.Parse(typeof(HumanBodyBones), humanName)],
                boneName = bone.name,
                limit = new HumanLimit { useDefaultValues = true },
            });
        }
        if (missing.Count > 0)
        {
            error = "뼈를 찾지 못했다: " + string.Join(", ", missing);
            return null;
        }

        // skeleton 배열은 계층 전체의 현재 기준 자세 로컬 TRS를 담는다.
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
            if (avatar != null)
            {
                if (Application.isPlaying) Object.Destroy(avatar);
                else Object.DestroyImmediate(avatar);
            }
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

        // 운영 재생과 같은 GLB 좌표계·매핑을 사용한다.
        if (!PersonaHumanoid.TryPrepare(personaRoot, out var skeletonRoot, out var avatar,
                                       out string error, personaRoot))
        {
            if (legacy != null) legacy.enabled = true;
            Debug.LogError("[PersonaHumanoidProbe] 아바타 생성 실패 — " + error);
            return;
        }
        Debug.Log("[PersonaHumanoidProbe] 복원한 자세: " + PersonaHumanoid.DescribePose(skeletonRoot));
        avatarReady = true;
        Debug.Log($"[PersonaHumanoidProbe] 아바타 생성 성공: 매핑 {PersonaHumanoid.Mapping(skeletonRoot).Length}개, " +
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
