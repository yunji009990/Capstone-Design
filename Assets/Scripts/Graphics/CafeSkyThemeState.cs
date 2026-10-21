using System;
using UnityEngine;
using UnityEngine.Rendering;

// 하늘 카페의 원래 배경 상태. Editor 전환 메뉴가 한 번 기록하며 실행 중에는 갱신하지 않는다.
[DisallowMultipleComponent, AddComponentMenu("")]
public sealed class CafeSkyThemeState : MonoBehaviour
{
    public enum Appearance { Moonlit, Pastel }

    [Serializable]
    public struct ObjectState { public GameObject target; public bool active; }
    [Serializable]
    public struct RendererState { public Renderer target; public bool enabled; }

    [HideInInspector] public bool captured;
    [HideInInspector] public bool skyActive;
    [HideInInspector] public Appearance appearance;
    [HideInInspector] public ObjectState[] originalObjects = Array.Empty<ObjectState>();
    [HideInInspector] public RendererState[] originalRenderers = Array.Empty<RendererState>();
    [HideInInspector] public Material originalSky;
    [HideInInspector] public AmbientMode originalAmbientMode;
    [HideInInspector] public Color originalAmbientSky, originalAmbientEquator, originalAmbientGround;
    [HideInInspector] public float originalAmbientIntensity;
    [HideInInspector] public bool originalFog;
}
