// 고정 몸체의 스킨 기준 자세를 Mixamo Avatar의 기준 자세로 맞추는 회전 보정.
// FBX와 GLB의 로컬 본 축이 달라 정면/위쪽 기준의 공통 좌표에서 회전 차이를 저장한다.
using System;
using System.Collections.Generic;
using UnityEngine;

public sealed class PersonaHumanoidReferencePose : ScriptableObject
{
    [Serializable]
    public struct BoneRotation
    {
        public string name;
        public Quaternion rotationDelta;
    }

    public string sourceModelPath;
    public string sourceAnimationPath;
    public BoneRotation[] bones = Array.Empty<BoneRotation>();

    public bool TryApply(Transform root, out string error)
    {
        error = null;
        if (bones == null || bones.Length == 0)
        {
            error = "고정 몸체의 Avatar 기준 자세가 비었다";
            return false;
        }
        var hierarchy = root.GetComponentsInChildren<Transform>(true);
        var byName = new Dictionary<string, Transform>();
        foreach (var bone in hierarchy)
            if (!byName.ContainsKey(bone.name)) byName.Add(bone.name, bone);
        var rotations = new Dictionary<Transform, Quaternion>();
        Quaternion frame = Quaternion.LookRotation(PersonaHumanoid.MeasureFacing(root), Vector3.up);
        foreach (var reference in bones)
        {
            if (!byName.TryGetValue(reference.name, out var bone))
            {
                error = "Avatar 기준 자세의 본을 찾지 못했다: " + reference.name;
                return false;
            }
            rotations[bone] = frame * reference.rotationDelta * Quaternion.Inverse(frame) * bone.rotation;
        }
        // 부모를 먼저 바꾸고 자식의 최종 월드 회전을 설정한다. 본 위치·길이·스케일은 그대로 둔다.
        foreach (var bone in hierarchy)
            if (rotations.TryGetValue(bone, out var rotation)) bone.rotation = rotation;
        return true;
    }
}
