// 원본 Human 메시의 바인드 자세와 Mixamo Avatar의 기준 자세 차이를 한 번 굽는다.
using System;
using System.Collections.Generic;
using System.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using Object = UnityEngine.Object;

public static class PersonaReferencePoseSetup
{
    public const string AssetPath = "Assets/Models/human/HumanHumanoidReferencePose.asset";
    const string BodyPath = "Assets/Models/human/Human.fbx";

    public static PersonaHumanoidReferencePose Build(string animationPath)
    {
        var source = AssetDatabase.LoadAllAssetsAtPath(animationPath).OfType<Avatar>()
            .FirstOrDefault(a => a.isValid && a.isHuman);
        if (source == null) throw new InvalidOperationException("기준으로 쓸 Mixamo Avatar가 없습니다: " + animationPath);
        var scene = EditorSceneManager.NewPreviewScene();
        var holder = new GameObject("Reference pose bake") { hideFlags = HideFlags.HideAndDontSave };
        SceneManager.MoveGameObjectToScene(holder, scene);
        Avatar temporaryAvatar = null;
        try
        {
            var body = (GameObject)PrefabUtility.InstantiatePrefab(AssetDatabase.LoadAssetAtPath<GameObject>(BodyPath), scene);
            body.transform.SetParent(holder.transform, false);
            if (!PersonaHumanoid.TryPrepare(holder.transform, out var skeleton, out temporaryAvatar, out var error))
                throw new InvalidOperationException(error);
            var transforms = body.GetComponentsInChildren<Transform>(true).ToDictionary(t => t.name);
            var skinBones = body.GetComponentsInChildren<SkinnedMeshRenderer>(true)
                .SelectMany(s => s.bones).Where(b => b != null).Distinct().ToArray();
            Quaternion frame = Quaternion.LookRotation(PersonaHumanoid.MeasureFacing(body.transform), Vector3.up);
            var bind = skinBones.ToDictionary(b => b.name, b => Quaternion.Inverse(frame) * b.rotation);
            // 팔은 Mixamo 기준에 맞추되 다리는 원본 몸체의 곧은 바인드 자세를 유지한다.
            // Walking의 기준 회전을 다리까지 복사하면 한쪽 발목이 약 37도 꺾인 상태로
            // Avatar가 만들어져, Idle·Waving에서도 발끝으로 서게 된다.
            var legBones = new HashSet<Transform>();
            foreach (var leg in new[] { HumanBodyBones.LeftUpperLeg, HumanBodyBones.RightUpperLeg })
            {
                var upper = PersonaHumanoid.FindBone(body.transform, leg);
                if (upper != null) legBones.UnionWith(upper.GetComponentsInChildren<Transform>(true));
            }
            foreach (var reference in source.humanDescription.skeleton)
                if (transforms.TryGetValue(reference.name, out var bone) && bone != skeleton && !legBones.Contains(bone))
                    bone.localRotation = reference.rotation;
            var corrections = skinBones.Select(b => new PersonaHumanoidReferencePose.BoneRotation
            {
                name = b.name,
                rotationDelta = legBones.Contains(b) ? Quaternion.identity
                    : Quaternion.Inverse(frame) * b.rotation * Quaternion.Inverse(bind[b.name]),
            }).ToArray();

            var asset = AssetDatabase.LoadAssetAtPath<PersonaHumanoidReferencePose>(AssetPath);
            if (asset == null)
            {
                asset = ScriptableObject.CreateInstance<PersonaHumanoidReferencePose>();
                AssetDatabase.CreateAsset(asset, AssetPath);
            }
            bool changed = asset.sourceModelPath != BodyPath || asset.sourceAnimationPath != animationPath ||
                asset.bones == null || asset.bones.Length != corrections.Length;
            if (!changed)
                for (int i = 0; i < corrections.Length; i++)
                    if (asset.bones[i].name != corrections[i].name ||
                        Mathf.Abs(Quaternion.Dot(asset.bones[i].rotationDelta, corrections[i].rotationDelta)) < .999999f)
                    { changed = true; break; }
            if (changed)
            {
                Undo.RecordObject(asset, "Bake fixed-body reference pose");
                asset.sourceModelPath = BodyPath;
                asset.sourceAnimationPath = animationPath;
                asset.bones = corrections;
                EditorUtility.SetDirty(asset);
                AssetDatabase.SaveAssetIfDirty(asset);
            }
            return asset;
        }
        finally
        {
            if (temporaryAvatar != null) Object.DestroyImmediate(temporaryAvatar);
            Object.DestroyImmediate(holder);
            EditorSceneManager.ClosePreviewScene(scene);
        }
    }
}
