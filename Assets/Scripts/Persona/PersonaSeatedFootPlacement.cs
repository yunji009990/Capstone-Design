using System.Collections.Generic;
using UnityEngine;

/// <summary>새 몸체의 지지발과 착석한 양발을 바닥에 맞춘다. 경로·본 길이·스킨은 유지한다.</summary>
[DefaultExecutionOrder(100)]
public sealed class PersonaSeatedFootPlacement : MonoBehaviour
{
    sealed class Leg
    {
        public Transform thigh, calf, foot, toes;
        public readonly List<Vector3> solePoints = new List<Vector3>();
        public readonly List<SkinPoint> supports = new List<SkinPoint>();
    }
    struct SkinTerm { public Transform bone; public Vector3 point; public float weight; }
    sealed class SkinPoint
    {
        public SkinTerm[] terms;
        public Vector3 WorldPosition()
        {
            Vector3 result = Vector3.zero;
            foreach (var term in terms) result += term.bone.TransformPoint(term.point) * term.weight;
            return result;
        }
    }
    PersonaArrival arrival;
    Transform hip;
    readonly List<Leg> legs = new List<Leg>();
    public float StandingCorrection { get; private set; }

    public void Bind(PersonaArrival source)
    {
        arrival = source;
        hip = PersonaHumanoid.FindBone(transform, HumanBodyBones.Hips);
        StandingCorrection = 0f;
        legs.Clear();
        foreach (bool left in new[] { true, false })
        {
            var leg = new Leg
            {
                thigh = PersonaHumanoid.FindBone(transform, left ? HumanBodyBones.LeftUpperLeg : HumanBodyBones.RightUpperLeg),
                calf = PersonaHumanoid.FindBone(transform, left ? HumanBodyBones.LeftLowerLeg : HumanBodyBones.RightLowerLeg),
                foot = PersonaHumanoid.FindBone(transform, left ? HumanBodyBones.LeftFoot : HumanBodyBones.RightFoot),
                toes = PersonaHumanoid.FindBone(transform, left ? HumanBodyBones.LeftToes : HumanBodyBones.RightToes),
            };
            if (leg.thigh == null || leg.calf == null || leg.foot == null) continue;
            foreach (var skin in GetComponentsInChildren<SkinnedMeshRenderer>(true))
            {
                var mesh = skin.sharedMesh;
                if (mesh == null) continue;
                int footIndex = System.Array.IndexOf(skin.bones, leg.foot);
                int toeIndex = System.Array.IndexOf(skin.bones, leg.toes);
                if (footIndex < 0 || footIndex >= mesh.bindposes.Length) continue;
                var points = mesh.vertices;
                var weights = mesh.boneWeights;
                var bones = skin.bones;
                var binds = mesh.bindposes;
                if (weights.Length != points.Length) continue;
                var bind = mesh.bindposes[footIndex];
                for (int i = 0; i < points.Length; i++)
                {
                    var w = weights[i];
                    float influence = FootWeight(w.boneIndex0, w.weight0, footIndex, toeIndex) +
                        FootWeight(w.boneIndex1, w.weight1, footIndex, toeIndex) +
                        FootWeight(w.boneIndex2, w.weight2, footIndex, toeIndex) +
                        FootWeight(w.boneIndex3, w.weight3, footIndex, toeIndex);
                    if (influence > .8f)
                    {
                        leg.solePoints.Add(bind.MultiplyPoint3x4(points[i]));
                        var terms = new List<SkinTerm>(4);
                        AddTerm(terms, points[i], w.boneIndex0, w.weight0, bones, binds);
                        AddTerm(terms, points[i], w.boneIndex1, w.weight1, bones, binds);
                        AddTerm(terms, points[i], w.boneIndex2, w.weight2, bones, binds);
                        AddTerm(terms, points[i], w.boneIndex3, w.weight3, bones, binds);
                        if (terms.Count > 0) leg.supports.Add(new SkinPoint { terms = terms.ToArray() });
                    }
                }
            }
            if (leg.solePoints.Count > 0) legs.Add(leg);
        }
    }

    static float FootWeight(int index, float weight, int foot, int toes) => index == foot || index == toes ? weight : 0f;

    static void AddTerm(List<SkinTerm> terms, Vector3 point, int index, float weight, Transform[] bones, Matrix4x4[] binds)
    {
        if (weight > 0f && index >= 0 && index < bones.Length && index < binds.Length && bones[index] != null)
            terms.Add(new SkinTerm { bone = bones[index], point = binds[index].MultiplyPoint3x4(point), weight = weight });
    }

    void LateUpdate()
    {
        StandingCorrection = 0f;
        if (arrival == null || arrival.phase == PersonaArrival.Phase.대기 || arrival.groundPath == null ||
            arrival.groundPath.PointCount < 2) return;
        float floor = arrival.groundPath.GetWorldPoint(arrival.groundPath.PointCount - 1).y;
        float seated = arrival.SeatedPoseWeight;
        if (seated < 1f && hip != null)
        {
            // 다른 체형으로 리타게팅한 보행은 Idle과 지지발 높이가 다를 수 있다.
            // 실제 스킨 가중치로 발바닥을 평가하고 골반 자세만 보정한다. 경로 루트의 Y는 바꾸지 않는다.
            float bottom = float.PositiveInfinity;
            foreach (var leg in legs)
                foreach (var point in leg.supports) bottom = Mathf.Min(bottom, point.WorldPosition().y);
            if (!float.IsInfinity(bottom) && !float.IsNaN(bottom))
            {
                StandingCorrection = Mathf.Clamp(floor + .003f - bottom, -.2f, .2f) * (1f - seated);
                hip.position += Vector3.up * StandingCorrection;
            }
        }
        if (seated > 0f)
            foreach (var leg in legs) Place(leg, floor, seated);
    }

    static void Place(Leg leg, float floor, float weight)
    {
        Vector3 hip = leg.thigh.position, knee = leg.calf.position, ankle = leg.foot.position;
        float upperLength = Vector3.Distance(hip, knee), lowerLength = Vector3.Distance(knee, ankle);
        if (upperLength < .001f || lowerLength < .001f) return;
        float sole = float.PositiveInfinity;
        var footMatrix = leg.foot.localToWorldMatrix;
        foreach (var point in leg.solePoints) sole = Mathf.Min(sole, footMatrix.MultiplyPoint3x4(point).y);
        // 앞으로 뻗은 발을 무릎 쪽으로 당겨, 발바닥이 바닥에 놓이는 편안한 착석 자세를 만든다.
        Vector3 target = Vector3.Lerp(ankle, knee, .8f);
        target.y = floor + Mathf.Max(0f, ankle.y - sole) + .003f;
        Vector3 direction = target - hip;
        if (direction.sqrMagnitude < 1e-8f) return;
        float distance = Mathf.Clamp(direction.magnitude, Mathf.Abs(upperLength - lowerLength) + .001f,
            upperLength + lowerLength - .001f);
        direction.Normalize();
        target = hip + direction * distance;
        Vector3 bend = Vector3.ProjectOnPlane(knee - hip, direction);
        if (bend.sqrMagnitude < 1e-8f) bend = Vector3.ProjectOnPlane(leg.thigh.forward, direction);
        if (bend.sqrMagnitude < 1e-8f) return;
        bend.Normalize();
        float cosine = Mathf.Clamp((upperLength * upperLength + distance * distance - lowerLength * lowerLength) /
            (2f * upperLength * distance), -1f, 1f);
        Vector3 wantedKnee = hip + direction * (upperLength * cosine) +
            bend * (upperLength * Mathf.Sqrt(Mathf.Max(0f, 1f - cosine * cosine)));
        Quaternion upper = leg.thigh.rotation, lower = leg.calf.rotation, foot = leg.foot.rotation;
        // 두 관절의 회전만 풀어 발을 놓는다. 발 회전을 보존해 신발이 바닥으로 기울지 않게 한다.
        leg.thigh.rotation = Quaternion.FromToRotation(knee - hip, wantedKnee - hip) * upper;
        leg.calf.rotation = Quaternion.FromToRotation(leg.foot.position - leg.calf.position, target - leg.calf.position) * leg.calf.rotation;
        Quaternion wantedUpper = leg.thigh.rotation, wantedLower = leg.calf.rotation;
        leg.thigh.rotation = Quaternion.Slerp(upper, wantedUpper, weight);
        leg.calf.rotation = Quaternion.Slerp(lower, wantedLower, weight);
        leg.foot.rotation = foot;
    }
}
