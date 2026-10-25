using System.Collections.Generic;
using UnityEngine;

/// <summary>착석한 양발만 다리 관절로 바닥에 놓는다. 서기·걷기의 원래 자세는 변경하지 않는다.</summary>
[DefaultExecutionOrder(100)]
public sealed class PersonaSeatedFootPlacement : MonoBehaviour
{
    sealed class Leg
    {
        public Transform thigh, calf, foot, toes;
        public readonly List<Vector3> solePoints = new List<Vector3>();
    }
    PersonaArrival arrival;
    readonly List<Leg> legs = new List<Leg>();

    public void Bind(PersonaArrival source)
    {
        arrival = source;
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
                        leg.solePoints.Add(bind.MultiplyPoint3x4(points[i]));
                }
            }
            if (leg.solePoints.Count > 0) legs.Add(leg);
        }
    }

    static float FootWeight(int index, float weight, int foot, int toes) => index == foot || index == toes ? weight : 0f;

    void LateUpdate()
    {
        if (arrival == null || arrival.phase == PersonaArrival.Phase.대기 || arrival.groundPath == null ||
            arrival.groundPath.PointCount < 2) return;
        float seated = arrival.SeatedPoseWeight;
        if (seated <= 0f) return;
        float floor = arrival.groundPath.GetWorldPoint(arrival.groundPath.PointCount - 1).y;
        foreach (var leg in legs) Place(leg, floor, seated);
    }

    static void Place(Leg leg, float floor, float weight)
    {
        Vector3 knee = leg.calf.position, ankle = leg.foot.position;
        float sole = SoleHeight(leg);
        // 앞으로 뻗은 발을 무릎 쪽으로 당겨, 발바닥이 바닥에 놓이는 편안한 착석 자세를 만든다.
        Vector3 target = Vector3.Lerp(ankle, knee, .8f);
        target.y = floor + Mathf.Max(0f, ankle.y - sole) + .003f;
        Solve(leg, target, weight);
    }

    static float SoleHeight(Leg leg)
    {
        float sole = float.PositiveInfinity;
        var matrix = leg.foot.localToWorldMatrix;
        foreach (var point in leg.solePoints) sole = Mathf.Min(sole, matrix.MultiplyPoint3x4(point).y);
        return sole;
    }

    static void Solve(Leg leg, Vector3 target, float weight)
    {
        if (weight <= .001f) return;
        Vector3 hip = leg.thigh.position, knee = leg.calf.position, ankle = leg.foot.position;
        float upperLength = Vector3.Distance(hip, knee), lowerLength = Vector3.Distance(knee, ankle);
        if (upperLength < .001f || lowerLength < .001f) return;
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
