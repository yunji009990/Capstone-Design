// VRUIPointer.cs
// 헤드셋을 쓴 채 World Space 캔버스의 uGUI 단추를 누른다.
//
//   - 컨트롤러: 컨트롤러 앞쪽으로 레이저가 나가고, 검지 트리거를 당겼다 놓으면 누른다.
//   - 손 추적: 손의 포인터 방향(OVRHand.PointerPose)으로 레이저가 나가고, 엄지와 검지를 집었다 떼면 누른다.
//
// Player_CRC 리그에는 잡기(HandGrab) 상호작용만 있고 UI 를 가리키는 레이는 없다. Interaction SDK 의
// 레이·PointableCanvas 를 엮는 대신, 단추에 uGUI 포인터 이벤트(들어감·나감·누름·뗌·클릭)를 직접 보낸다.
// 그래서 단추의 색 전환과 onClick 이 마우스로 누를 때와 똑같이 동작한다.
//
// 누름은 "뗄 때" 확정한다. 집은 채로 다른 단추로 옮겨 가서 떼면 아무것도 누르지 않는다.

using System.Collections.Generic;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.UI;

[DisallowMultipleComponent]
public class VRUIPointer : MonoBehaviour
{
    [Tooltip("가리킬 World Space 캔버스. 비워두면 씬에서 찾는다.")]
    public List<Canvas> canvases = new List<Canvas>();

    [Tooltip("레이저가 닿는 최대 거리(m).")]
    public float maxDistance = 8f;

    [Tooltip("아무것도 가리키지 않을 때 보이는 레이저 길이(m).")]
    public float idleLength = 0.6f;

    [Header("컨트롤러 트리거")]
    [Range(0f, 1f)] public float pressAt = 0.55f;
    [Range(0f, 1f)] public float releaseAt = 0.35f;

    [Header("모양")]
    public float lineWidth = 0.004f;
    public Color lineColor = new Color(1f, 1f, 1f, 0.55f);
    public Color hoverColor = new Color(0.773f, 0.561f, 0.627f, 0.95f);
    public float dotSize = 0.018f;

    class Pointer
    {
        public System.Func<bool> valid;
        public System.Func<Transform> origin;
        public System.Func<bool, bool> pressed;   // 지난 상태를 받아 지금 눌렸는지 돌려준다(트리거 히스테리시스).
        public OVRInput.Controller controller = OVRInput.Controller.None;
        public bool inTrackingSpace;              // origin 이 트래킹 공간 좌표를 담고 있다(OVRHand.PointerPose).
        public LineRenderer line;
        public Transform dot;
        public PointerEventData data;
        public GameObject hover, down;
        public bool isDown;
    }

    readonly List<Pointer> _pointers = new List<Pointer>();
    OVRCameraRig _rig;
    Material _material;
    float _nextHandSearch;
    readonly HashSet<OVRHand> _hands = new HashSet<OVRHand>();

    void Start()
    {
        if (canvases.Count == 0)
            foreach (var c in FindObjectsOfType<Canvas>())
                if (c.isRootCanvas && c.renderMode == RenderMode.WorldSpace) canvases.Add(c);

        _material = new Material(Shader.Find("Sprites/Default"));
        AddController(OVRInput.Controller.LTouch, () => _rig != null ? _rig.leftControllerAnchor : null);
        AddController(OVRInput.Controller.RTouch, () => _rig != null ? _rig.rightControllerAnchor : null);
    }

    void AddController(OVRInput.Controller controller, System.Func<Transform> anchor)
    {
        var p = Create("ControllerRay_" + controller);
        p.controller = controller;
        p.origin = anchor;
        p.valid = () => anchor() != null
                        && OVRInput.IsControllerConnected(controller)
                        && OVRInput.GetControllerPositionTracked(controller);
        p.pressed = wasDown =>
        {
            float t = OVRInput.Get(OVRInput.Axis1D.PrimaryIndexTrigger, controller);
            return wasDown ? t > releaseAt : t > pressAt;
        };
    }

    void AddHand(OVRHand hand)
    {
        var p = Create("HandRay_" + hand.name);
        p.origin = () => hand != null ? hand.PointerPose : null;
        p.inTrackingSpace = true;
        p.valid = () => hand != null && hand.isActiveAndEnabled && hand.IsTracked && hand.IsPointerPoseValid;
        p.pressed = _ => hand.GetFingerIsPinching(OVRHand.HandFinger.Index);
    }

    Pointer Create(string name)
    {
        var go = new GameObject(name);
        go.transform.SetParent(transform, false);
        var line = go.AddComponent<LineRenderer>();
        line.useWorldSpace = true;
        line.positionCount = 2;
        line.widthMultiplier = lineWidth;
        line.numCapVertices = 4;
        line.sharedMaterial = _material;
        line.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
        line.receiveShadows = false;
        line.enabled = false;

        var dot = GameObject.CreatePrimitive(PrimitiveType.Sphere);
        dot.name = "Dot";
        Destroy(dot.GetComponent<Collider>());
        dot.transform.SetParent(go.transform, false);
        dot.transform.localScale = Vector3.one * dotSize;
        var dotRenderer = dot.GetComponent<Renderer>();
        dotRenderer.sharedMaterial = _material;
        dotRenderer.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
        dot.SetActive(false);

        var p = new Pointer { line = line, dot = dot.transform, data = new PointerEventData(EventSystem.current) };
        _pointers.Add(p);
        return p;
    }

    void Update()
    {
        if (_rig == null) _rig = FindObjectOfType<OVRCameraRig>();
        // 손 프리팹은 리그가 실행 중에 켜는 경우가 있어 가끔 다시 찾는다.
        if (Time.unscaledTime >= _nextHandSearch)
        {
            _nextHandSearch = Time.unscaledTime + 1f;
            foreach (var hand in FindObjectsOfType<OVRHand>())
                if (_hands.Add(hand)) AddHand(hand);
        }
        foreach (var p in _pointers) Step(p);
    }

    void Step(Pointer p)
    {
        Transform origin = p.valid() ? p.origin() : null;
        if (origin == null)
        {
            Release(p, false);
            SetHover(p, null);
            p.line.enabled = false;
            p.dot.gameObject.SetActive(false);
            return;
        }

        var ray = new Ray(origin.position, origin.forward);
        // PointerPose 는 부모 없이 트래킹 공간 좌표만 받는다(_pointerPoseRoot 미지정). 리그 위치로 옮겨야 손에서 나간다.
        if (p.inTrackingSpace && origin.parent == null && _rig != null && _rig.trackingSpace != null)
            ray = new Ray(_rig.trackingSpace.TransformPoint(origin.localPosition),
                          _rig.trackingSpace.TransformDirection(origin.forward));
        GameObject target = Hit(ray, out Vector3 point, out bool onCanvas);
        SetHover(p, target);

        bool nowDown = p.pressed(p.isDown);
        if (nowDown && !p.isDown)
        {
            p.isDown = true;
            p.down = target;
            if (target != null)
            {
                p.data.pointerPress = target;
                p.data.pressPosition = p.data.position;
                ExecuteEvents.Execute(target, p.data, ExecuteEvents.pointerDownHandler);
            }
        }
        else if (!nowDown && p.isDown) Release(p, true);

        Vector3 end = onCanvas ? point : ray.origin + ray.direction * idleLength;
        p.line.enabled = true;
        p.line.SetPosition(0, ray.origin);
        p.line.SetPosition(1, end);
        var color = target != null ? hoverColor : lineColor;
        p.line.startColor = new Color(color.r, color.g, color.b, color.a * 0.2f);
        p.line.endColor = color;
        p.dot.gameObject.SetActive(onCanvas);
        p.dot.position = end;
    }

    /// <summary>집기·트리거를 놓았다. 누를 때와 같은 단추 위에서 놓았을 때만 클릭이다.</summary>
    void Release(Pointer p, bool allowClick)
    {
        if (!p.isDown) return;
        p.isDown = false;
        if (p.down != null)
        {
            ExecuteEvents.Execute(p.down, p.data, ExecuteEvents.pointerUpHandler);
            if (allowClick && p.down == p.hover)
            {
                ExecuteEvents.Execute(p.down, p.data, ExecuteEvents.pointerClickHandler);
                if (p.controller != OVRInput.Controller.None) StartCoroutine(Buzz(p.controller));
            }
        }
        p.data.pointerPress = null;
        p.down = null;
    }

    void SetHover(Pointer p, GameObject target)
    {
        if (p.hover == target) return;
        if (p.hover != null) ExecuteEvents.Execute(p.hover, p.data, ExecuteEvents.pointerExitHandler);
        p.hover = target;
        p.data.pointerEnter = target;
        if (target != null) ExecuteEvents.Execute(target, p.data, ExecuteEvents.pointerEnterHandler);
    }

    /// <summary>가장 가까운 캔버스의 평면에서 맞은 점과, 그 점 위에 가장 위에 그려진 단추를 찾는다.</summary>
    GameObject Hit(Ray ray, out Vector3 point, out bool onCanvas)
    {
        point = default;
        onCanvas = false;
        float best = maxDistance;
        Canvas hitCanvas = null;
        foreach (var canvas in canvases)
        {
            if (canvas == null || !canvas.isActiveAndEnabled) continue;
            var rect = (RectTransform)canvas.transform;
            var plane = new Plane(rect.forward, rect.position);
            if (!plane.Raycast(ray, out float distance) || distance > best) continue;
            Vector3 p = ray.GetPoint(distance);
            if (!rect.rect.Contains(rect.InverseTransformPoint(p))) continue;
            best = distance;
            point = p;
            hitCanvas = canvas;
        }
        if (hitCanvas == null) return null;
        onCanvas = true;

        GameObject found = null;
        // 계층 순서상 뒤에 오는 것이 위에 그려진다. 마지막으로 맞은 단추를 고른다.
        foreach (var selectable in hitCanvas.GetComponentsInChildren<Selectable>())
        {
            var rect = (RectTransform)selectable.transform;
            if (rect.rect.Contains(rect.InverseTransformPoint(point))) found = selectable.gameObject;
        }
        return found;
    }

    System.Collections.IEnumerator Buzz(OVRInput.Controller controller)
    {
        OVRInput.SetControllerVibration(0.4f, 0.3f, controller);
        yield return new WaitForSecondsRealtime(0.06f);
        OVRInput.SetControllerVibration(0f, 0f, controller);
    }

    void OnDisable()
    {
        foreach (var p in _pointers)
        {
            Release(p, false);
            SetHover(p, null);
        }
    }

    void OnDestroy()
    {
        if (_material != null) Destroy(_material);
    }
}
