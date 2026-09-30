using System.Collections.Generic;
using UnityEngine;

/// <summary>발밑이 따라갈 수평 바닥 경로. Scene 편집기로 그리며 첫 점이 입구, 마지막 점이 서는 자리다.</summary>
[DisallowMultipleComponent]
public sealed class PersonaGroundPath : MonoBehaviour
{
    [SerializeField, HideInInspector] List<Vector3> points = new List<Vector3>();

    public int PointCount => points.Count;
    public float FloorHeight => transform.position.y;

    public Vector3 ProjectToFloor(Vector3 world)
    {
        world.y = FloorHeight;
        return world;
    }

    public Vector3 GetWorldPoint(int index) => ProjectToFloor(transform.TransformPoint(points[index]));

    public void SetWorldPoint(int index, Vector3 world) =>
        points[index] = transform.InverseTransformPoint(ProjectToFloor(world));

    public void SetWorldPoints(IReadOnlyList<Vector3> worldPoints)
    {
        points.Clear();
        for (int i = 0; i < worldPoints.Count; i++)
            points.Add(transform.InverseTransformPoint(ProjectToFloor(worldPoints[i])));
    }

    public void InsertWorldPoint(int index, Vector3 world) =>
        points.Insert(index, transform.InverseTransformPoint(ProjectToFloor(world)));

    public void RemovePoint(int index) => points.RemoveAt(index);

    /// <summary>재생 시작 시 모양을 고정한다. 이동 중에는 배열이나 누적 거리를 다시 만들지 않는다.</summary>
    public PersonaGroundRoute CreateRoute()
    {
        var world = new List<Vector3>(PointCount);
        for (int i = 0; i < PointCount; i++) world.Add(GetWorldPoint(i));
        return PersonaGroundRoute.Create(world);
    }

    void OnDrawGizmos()
    {
        if (!enabled || PointCount < 2) return;
        Gizmos.color = new Color(.15f, .85f, 1f, .8f);
        for (int i = 1; i < PointCount; i++)
            Gizmos.DrawLine(GetWorldPoint(i - 1), GetWorldPoint(i));
        Gizmos.DrawWireSphere(GetWorldPoint(0), .10f);
        Gizmos.DrawWireSphere(GetWorldPoint(PointCount - 1), .10f);
    }
}

/// <summary>누적 거리를 기준으로 선 위를 이동한다. 큰 프레임 간격에서도 코너·끝점을 넘겨 달리지 않는다.</summary>
public sealed class PersonaGroundRoute
{
    readonly Vector3[] points;
    readonly float[] distances;
    public float Length => distances[distances.Length - 1];
    public Vector3 Start => points[0];
    public Vector3 End => points[points.Length - 1];

    PersonaGroundRoute(Vector3[] points, float[] distances)
    {
        this.points = points;
        this.distances = distances;
    }

    public static PersonaGroundRoute Create(IReadOnlyList<Vector3> input)
    {
        var valid = new List<Vector3>(input.Count);
        for (int i = 0; i < input.Count; i++)
        {
            Vector3 p = input[i];
            if (float.IsNaN(p.x) || float.IsNaN(p.y) || float.IsNaN(p.z) ||
                float.IsInfinity(p.x) || float.IsInfinity(p.y) || float.IsInfinity(p.z)) return null;
            if (valid.Count == 0 || Vector3.Distance(valid[valid.Count - 1], p) > .001f) valid.Add(p);
        }
        if (valid.Count < 2) return null;
        var cumulative = new float[valid.Count];
        for (int i = 1; i < valid.Count; i++)
            cumulative[i] = cumulative[i - 1] + Vector3.Distance(valid[i - 1], valid[i]);
        return new PersonaGroundRoute(valid.ToArray(), cumulative);
    }

    public Vector3 Evaluate(float distance, out Vector3 direction, out int segment)
    {
        distance = Mathf.Clamp(distance, 0f, Length);
        int low = 0, high = points.Length - 1;
        while (high - low > 1)
        {
            int mid = (low + high) / 2;
            if (distances[mid] <= distance) low = mid;
            else high = mid;
        }
        segment = low;
        direction = (points[high] - points[low]).normalized;
        float t = (distance - distances[low]) / (distances[high] - distances[low]);
        return Vector3.Lerp(points[low], points[high], t);
    }

    /// <summary>바닥에서 고른 위치를 경로 선 위에 붙이고 출발점부터의 거리를 돌려준다.</summary>
    public float FindClosestDistance(Vector3 point)
    {
        float bestSquared = float.PositiveInfinity, along = 0f;
        for (int i = 0; i < points.Length - 1; i++)
        {
            Vector3 delta = points[i + 1] - points[i];
            float t = Mathf.Clamp01(Vector3.Dot(point - points[i], delta) / delta.sqrMagnitude);
            float squared = (point - (points[i] + delta * t)).sqrMagnitude;
            if (squared >= bestSquared) continue;
            bestSquared = squared;
            along = Mathf.Lerp(distances[i], distances[i + 1], t);
        }
        return along;
    }
}
