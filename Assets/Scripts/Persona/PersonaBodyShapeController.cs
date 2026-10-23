using System;
using System.Collections.Generic;
using UnityEngine;

/// <summary>스킨·본·공유 메시를 바꾸지 않고 렌더러의 체형 Blend Shape만 조절한다.</summary>
[ExecuteAlways, DisallowMultipleComponent]
[AddComponentMenu("다시봄/캐릭터 체형 조절")]
public sealed class PersonaBodyShapeController : MonoBehaviour
{
    [SerializeField, Tooltip("체형 설정 자산. Inspector에서 자산을 열어 6개 값을 조절한다.")]
    PersonaBodyShapeProfile profile;
    public PersonaBodyShapeProfile Profile => profile;
    public int BoundShapeCount { get; private set; }

    struct Binding
    {
        public SkinnedMeshRenderer renderer;
        public int shape, index;
        public float frameWeight;
    }

    readonly List<Binding> bindings = new List<Binding>();
    readonly float[] appliedValues = new float[PersonaBodyShapeProfile.ShapeCount];
    bool rebindPending, applied;

    /// <summary>비활성 상태로 로드한 GLB도 등장 전에 체형을 적용한다.</summary>
    public void Bind(PersonaBodyShapeProfile settings)
    {
        profile = settings;
        Rebind();
        ApplyNow();
    }

    void Reset() => profile = PersonaBodyShapeProfile.LoadDefault();
    void OnEnable() { Rebind(); ApplyNow(); }
    // OnValidate는 import 스레드에서도 호출될 수 있어 Unity 메시 API를 실행하지 않는다.
    void OnValidate() { rebindPending = true; applied = false; }
    void OnTransformChildrenChanged() => rebindPending = true;

    void LateUpdate()
    {
        if (rebindPending) Rebind();
        if (!applied) { ApplyNow(); return; }
        for (int i = 0; i < appliedValues.Length; i++)
            if (ReadValue(i) != appliedValues[i]) { ApplyNow(); return; }
    }

    void Rebind()
    {
        bindings.Clear();
        int found = 0;
        foreach (var skin in GetComponentsInChildren<SkinnedMeshRenderer>(true))
        {
            var mesh = skin.sharedMesh;
            if (mesh == null) continue;
            for (int index = 0; index < mesh.blendShapeCount; index++)
            {
                string name = mesh.GetBlendShapeName(index);
                for (int shape = 0; shape < PersonaBodyShapeProfile.ShapeCount; shape++)
                {
                    string expected = PersonaBodyShapeProfile.ShapeName(shape);
                    if (name != expected && !name.EndsWith("." + expected, StringComparison.Ordinal)) continue;
                    int frames = mesh.GetBlendShapeFrameCount(index);
                    if (frames == 0) continue;
                    // FBX는 100, glTFast는 1을 완전 변형 프레임의 가중치로 사용한다.
                    float weight = mesh.GetBlendShapeFrameWeight(index, frames - 1);
                    if (weight <= 0f || float.IsInfinity(weight) || float.IsNaN(weight)) continue;
                    bindings.Add(new Binding { renderer = skin, index = index, shape = shape, frameWeight = weight });
                    found |= 1 << shape;
                    break;
                }
            }
        }
        BoundShapeCount = 0;
        for (int i = 0; i < PersonaBodyShapeProfile.ShapeCount; i++)
            if ((found & (1 << i)) != 0) BoundShapeCount++;
        rebindPending = false;
        applied = false;
    }

    float ReadValue(int index) => profile != null ? profile.GetValue(index) : 0f;

    public void ApplyNow()
    {
        if (rebindPending) Rebind();
        for (int i = 0; i < appliedValues.Length; i++) appliedValues[i] = ReadValue(i);
        foreach (var binding in bindings)
            if (binding.renderer != null)
                binding.renderer.SetBlendShapeWeight(binding.index, appliedValues[binding.shape] * binding.frameWeight);
        applied = true;
    }
}
