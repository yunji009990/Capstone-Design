using System;
using System.Collections.Generic;
using UnityEngine;
using Object = UnityEngine.Object;

/// <summary>원본 옷깃을 복원하고 생성 머리를 목 본의 접합점에 맞춘다. 원본 메시·본·재질은 보존한다.</summary>
public sealed class PersonaModelProportions : IDisposable
{
    public const string HeadSizeShape = "다시봄_머리_크기";
    public const string HeadHeightShape = "다시봄_머리_높이";
    public const string HeadForwardShape = "다시봄_머리_앞뒤";
    readonly Transform root;
    readonly Vector3 baseScale;
    readonly List<Binding> heads = new List<Binding>();
    readonly List<Binding> collars = new List<Binding>();
    float body = float.NaN, size, height, forward;
    public bool HasSeparateHead => heads.Count > 0;
    public bool HasRestoredCollar => collars.Count > 0;
    sealed class Binding
    {
        public SkinnedMeshRenderer skin;
        public Mesh original, instance;
        public Bounds originalBounds;
        public int size, height, forward;
    }

    public PersonaModelProportions(Transform model)
    {
        root = model; baseScale = model.localScale;
        var headBone = PersonaHumanoid.FindBone(model, HumanBodyBones.Head);
        if (headBone == null) return;
        var skins = model.GetComponentsInChildren<SkinnedMeshRenderer>(true);
        SkinnedMeshRenderer bodySkin = null;
        foreach (var skin in skins)
            if (skin.sharedMesh != null && skin.sharedMesh.blendShapeCount >= PersonaBodyShapeProfile.ShapeCount)
            { bodySkin = skin; break; }
        if (bodySkin == null) return;
        var bodySource = bodySkin.sharedMesh;
        float bodyHeight = bodySource.bounds.size.y, cutY = bodySource.bounds.max.y;
        if (bodyHeight <= .00001f) return;
        var template = Resources.Load<PersonaCollarTemplate>(PersonaCollarTemplate.ResourceName);
        if (template != null)
        {
            var restored = template.Restore(bodySkin);
            if (restored != null)
            {
                collars.Add(new Binding { skin = bodySkin, original = bodySource, instance = restored, originalBounds = bodySkin.localBounds });
                bodySkin.sharedMesh = restored;
            }
        }
        foreach (var skin in skins)
        {
            var source = skin.sharedMesh;
            if (skin == bodySkin || source == null || source.blendShapeCount != 0) continue;
            int headIndex = Array.IndexOf(skin.bones, headBone);
            var weights = source.boneWeights;
            if (headIndex < 0 || weights.Length != source.vertexCount || weights.Length == 0) continue;
            bool rigidHead = true;
            foreach (var weight in weights)
                if (weight.boneIndex0 != headIndex || weight.weight0 < .99f) { rigidHead = false; break; }
            if (!rigidHead || source.bindposes.Length <= headIndex) continue;
            Vector3 socket = source.bindposes[headIndex].inverse.GetColumn(3);
            var points = source.vertices;
            // 실제 목 절단면을 찾는다. 전체 머리 바운즈의 중심을 목으로 쓰지 않는다.
            var ring = new Bounds(); int ringCount = 0;
            foreach (var point in points)
                if (Mathf.Abs(point.y - cutY) < bodyHeight * .0001f)
                {
                    if (ringCount++ == 0) ring = new Bounds(point, Vector3.zero);
                    else ring.Encapsulate(point);
                }
            Vector3 offset = Vector3.zero;
            if (ringCount >= 12) offset = Vector3.ClampMagnitude(socket - ring.center, bodyHeight * .03f);
            offset.y += bodyHeight * .008f;
            var clone = Object.Instantiate(source);
            clone.name = "Persona Head Proportions (Instance)"; clone.hideFlags = HideFlags.DontSave;
            var sizeDelta = new Vector3[points.Length]; var upDelta = new Vector3[points.Length]; var forwardDelta = new Vector3[points.Length];
            for (int i = 0; i < points.Length; i++)
            {
                // 중립 결합은 머리 전체의 이동뿐이다. 턱을 늘리거나 목 원통에 섞지 않는다.
                points[i] += offset;
                float t = Mathf.Clamp01((points[i].y - (socket.y - bodyHeight * .02f)) / (bodyHeight * .05f));
                float w = t * t * (3f - 2f * t);
                // 낮은 턱 앞부분도 얼굴과 함께 움직인다. 목 아래의 고정 영역은 옷깃 안에 둔다.
                float face = Mathf.SmoothStep(0f, 1f, (points[i].z - socket.z - source.bounds.size.z * .22f) / (source.bounds.size.z * .14f));
                w = Mathf.Max(w, face);
                sizeDelta[i] = (points[i] - socket) * w;
                upDelta[i] = Vector3.up * (bodyHeight * .05f * w);
                forwardDelta[i] = Vector3.forward * (bodyHeight * .05f * w);
            }
            clone.vertices = points; clone.RecalculateBounds();
            clone.AddBlendShapeFrame(HeadSizeShape, 100f, sizeDelta, null, null);
            clone.AddBlendShapeFrame(HeadHeightShape, 100f, upDelta, null, null);
            clone.AddBlendShapeFrame(HeadForwardShape, 100f, forwardDelta, null, null);
            heads.Add(new Binding { skin = skin, original = source, instance = clone, originalBounds = skin.localBounds,
                size = clone.GetBlendShapeIndex(HeadSizeShape), height = clone.GetBlendShapeIndex(HeadHeightShape), forward = clone.GetBlendShapeIndex(HeadForwardShape) });
            skin.sharedMesh = clone;
            var bounds = clone.bounds; bounds.Expand(bodyHeight * .3f); skin.localBounds = bounds;
        }
    }

    public bool NeedsUpdate(PersonaBodyShapeProfile profile) => body != (profile != null ? profile.BodyScale : 1f) ||
        size != (profile != null ? profile.HeadScale : 1f) || height != (profile != null ? profile.HeadHeight : 0f) || forward != (profile != null ? profile.HeadForward : 0f);
    public void Apply(PersonaBodyShapeProfile profile)
    {
        body = profile != null ? profile.BodyScale : 1f; size = profile != null ? profile.HeadScale : 1f;
        height = profile != null ? profile.HeadHeight : 0f; forward = profile != null ? profile.HeadForward : 0f;
        if (root != null) root.localScale = baseScale * body;
        foreach (var item in heads)
            if (item.skin != null)
            {
                item.skin.SetBlendShapeWeight(item.size, (size - 1f) * 100f);
                item.skin.SetBlendShapeWeight(item.height, height / .05f * 100f);
                item.skin.SetBlendShapeWeight(item.forward, forward / .05f * 100f);
            }
    }
    public void Dispose()
    {
        foreach (var list in new[] { heads, collars })
        {
            foreach (var item in list)
            {
                if (item.skin != null && item.skin.sharedMesh == item.instance)
                { item.skin.sharedMesh = item.original; item.skin.localBounds = item.originalBounds; }
                if (item.instance != null)
                { if (Application.isPlaying) Object.Destroy(item.instance); else Object.DestroyImmediate(item.instance); }
            }
            list.Clear();
        }
        if (root != null) root.localScale = baseScale;
    }
}
