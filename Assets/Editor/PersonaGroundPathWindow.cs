using System.Collections.Generic;
using System.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.SceneManagement;

/// <summary>기존 Persona Editor 창과 같은 IMGUI 도구. 경로 편집은 Edit Mode에서만 저장한다.</summary>
public sealed class PersonaGroundPathWindow : EditorWindow
{
    [SerializeField] PersonaArrival arrival;
    [SerializeField] float pointSpacing = .12f;
    [SerializeField] float simplifyTolerance = .025f;
    [SerializeField] float preview;
    int selected = -1;
    bool drawing;
    bool placingGreeting;
    bool ownsToolVisibility, previousToolHidden;
    int strokeControl;
    readonly List<Vector3> stroke = new List<Vector3>();
    Vector2 scroll;
    PersonaGroundPath Path => arrival != null ? arrival.groundPath : null;
    bool CanEdit => !EditorApplication.isPlayingOrWillChangePlaymode && arrival != null &&
                    arrival.gameObject.scene == SceneManager.GetActiveScene();

    [MenuItem("Tools/Persona/이동 경로 그리기", priority = 12)]
    public static void Open()
    {
        var window = GetWindow<PersonaGroundPathWindow>("이동 경로");
        window.minSize = new Vector2(330, 420);
        if (window.arrival == null)
        {
            var candidates = SceneManager.GetActiveScene().GetRootGameObjects()
                .SelectMany(root => root.GetComponentsInChildren<PersonaArrival>(true)).ToArray();
            if (candidates.Length == 1) window.arrival = candidates[0];
        }
        if (window.Path != null && !EditorApplication.isPlayingOrWillChangePlaymode)
            Selection.activeGameObject = window.Path.gameObject;
        window.Show();
    }

    [MenuItem("Tools/Persona/바닥 경로 준비", priority = 11)]
    public static void PrepareCurrentScene()
    {
        if (EditorApplication.isPlayingOrWillChangePlaymode) return;
        var candidates = SceneManager.GetActiveScene().GetRootGameObjects()
            .SelectMany(root => root.GetComponentsInChildren<PersonaArrival>(true)).ToArray();
        if (candidates.Length != 1)
        {
            Debug.LogWarning("[PersonaGroundPath] 현재 씬의 도착 연출이 1개일 때 준비할 수 있습니다. 경로 창에서 대상을 선택하세요.");
            return;
        }
        CreateFromLegacy(candidates[0]);
        Open();
        var window = GetWindow<PersonaGroundPathWindow>();
        window.arrival = candidates[0];
        window.FramePath();
    }

    void OnEnable()
    {
        SceneView.duringSceneGui += DuringSceneGUI;
        Undo.undoRedoPerformed += OnUndo;
        EditorApplication.playModeStateChanged += OnPlayModeChanged;
    }

    void OnDisable()
    {
        CancelStroke();
        RestoreTools();
        SceneView.duringSceneGui -= DuringSceneGUI;
        Undo.undoRedoPerformed -= OnUndo;
        EditorApplication.playModeStateChanged -= OnPlayModeChanged;
    }

    void OnUndo() { selected = -1; CancelStroke(); Repaint(); SceneView.RepaintAll(); }
    void OnPlayModeChanged(PlayModeStateChange state) { CancelStroke(); drawing = false; RestoreTools(); Repaint(); }

    void RestoreTools()
    {
        if (!ownsToolVisibility) return;
        Tools.hidden = previousToolHidden;
        ownsToolVisibility = false;
    }

    void OnGUI()
    {
        scroll = EditorGUILayout.BeginScrollView(scroll);
        EditorGUILayout.LabelField("캐릭터가 걸을 바닥 경로", EditorStyles.boldLabel);
        EditorGUI.BeginChangeCheck();
        var next = (PersonaArrival)EditorGUILayout.ObjectField("캐릭터 도착 연출", arrival, typeof(PersonaArrival), true);
        if (EditorGUI.EndChangeCheck()) { CancelStroke(); arrival = next; selected = -1; }
        if (arrival == null)
        {
            EditorGUILayout.HelpBox("현재 씬의 PersonaArrival을 연결하세요.", MessageType.Info);
            EditorGUILayout.EndScrollView();
            return;
        }
        if (!CanEdit)
            EditorGUILayout.HelpBox("경로는 현재 씬에서 Play를 종료한 뒤 편집합니다.", MessageType.Info);
        using (new EditorGUI.DisabledScope(!CanEdit))
        {
            if (Path == null)
            {
                EditorGUILayout.HelpBox("입구와 경유지를 바닥 경로로 가져옵니다. 이후 첫 점과 마지막 점이 출발·도착 위치가 됩니다.", MessageType.Info);
                if (GUILayout.Button("기존 경로로 만들기")) { CreateFromLegacy(arrival); FramePath(); }
                EditorGUILayout.EndScrollView();
                return;
            }

            EditorGUILayout.ObjectField("저장된 경로", Path, typeof(PersonaGroundPath), true);
            EditorGUI.BeginChangeCheck();
            bool use = EditorGUILayout.Toggle("그린 경로 사용", Path.enabled);
            if (EditorGUI.EndChangeCheck()) { Undo.RecordObject(Path, "바닥 경로 사용 변경"); Path.enabled = use; Changed(Path); }
            EditorGUI.BeginChangeCheck();
            float height = EditorGUILayout.FloatField("바닥 높이 (Y)", Path.FloorHeight);
            if (EditorGUI.EndChangeCheck() && !float.IsNaN(height) && !float.IsInfinity(height))
            {
                Undo.RecordObject(Path.transform, "경로 바닥 높이 변경");
                var p = Path.transform.position; p.y = height; Path.transform.position = p;
                Changed(Path.transform);
            }

            EditorGUILayout.Space();
            EditorGUI.BeginChangeCheck();
            int mode = GUILayout.Toolbar(drawing ? 1 : 0, new[] { "점 편집", "경로 그리기" });
            if (EditorGUI.EndChangeCheck()) { CancelStroke(); drawing = mode == 1; SceneView.RepaintAll(); }
            EditorGUILayout.HelpBox(drawing
                ? "Scene 창에서 출발 → 도착 방향으로 왼쪽 버튼을 누른 채 그리세요. 버튼을 놓으면 새 경로로 바뀝니다. Esc로 취소합니다."
                : "Scene 창의 점을 끌어 이동하세요. Shift+클릭은 끝에 점 추가, Ctrl+클릭은 선 중간에 점 삽입입니다. Ctrl+Z로 되돌립니다.", MessageType.Info);
            using (new EditorGUILayout.HorizontalScope())
            {
                if (GUILayout.Button("위에서 보기")) FramePath();
                if (GUILayout.Button("씬 저장")) EditorSceneManager.SaveScene(arrival.gameObject.scene);
            }

            EditorGUILayout.Space();
            EditorGUILayout.LabelField("경로 중간 인사", EditorStyles.boldLabel);
            EditorGUI.BeginChangeCheck();
            bool greetOnPath = EditorGUILayout.Toggle("걷다가 인사", arrival.greetOnPath);
            if (EditorGUI.EndChangeCheck())
            {
                Undo.RecordObject(arrival, "중간 인사 사용 변경");
                arrival.greetOnPath = greetOnPath; placingGreeting = false; Changed(arrival);
            }
            if (arrival.greetOnPath)
            {
                EditorGUI.BeginChangeCheck();
                float percent = EditorGUILayout.Slider("인사 위치 (%)", arrival.greetPathProgress * 100f, 0f, 100f);
                if (EditorGUI.EndChangeCheck())
                {
                    Undo.RecordObject(arrival, "인사 위치 이동");
                    arrival.greetPathProgress = percent / 100f; Changed(arrival);
                }
                using (new EditorGUI.DisabledScope(!Path.enabled || Path.CreateRoute() == null))
                {
                    if (GUILayout.Button(placingGreeting ? "위치 선택 취소" : "Scene에서 인사 위치 찍기"))
                    {
                        bool pick = !placingGreeting;
                        CancelStroke(); drawing = false; placingGreeting = pick;
                        SceneView.RepaintAll();
                    }
                }
                EditorGUILayout.HelpBox(placingGreeting
                    ? "Scene 창에서 원하는 곳을 클릭하세요. 가장 가까운 경로 위에 인사 표시가 붙습니다. Esc로 취소합니다."
                    : "분홍색 인사 표시를 끌어서 옮길 수도 있습니다. 그 자리에서 멈춰 인사한 뒤 남은 경로를 걷습니다.", MessageType.Info);
                if (arrival.greetClip == null)
                    EditorGUILayout.HelpBox("Greet Clip이 비어 있어 재생할 때 인사를 생략합니다.", MessageType.Warning);
            }

            if (drawing)
            {
                pointSpacing = EditorGUILayout.Slider("그리기 점 간격 (m)", pointSpacing, .03f, .4f);
                simplifyTolerance = EditorGUILayout.Slider("선 정리 허용폭 (m)", simplifyTolerance, 0f, .08f);
            }
            else if (selected >= 0 && selected < Path.PointCount)
            {
                EditorGUILayout.LabelField($"선택한 점: {PointLabel(selected, Path.PointCount)}");
                var current = Path.GetWorldPoint(selected);
                EditorGUI.BeginChangeCheck();
                var xz = EditorGUILayout.Vector2Field("바닥 위치 (X, Z)", new Vector2(current.x, current.z));
                if (EditorGUI.EndChangeCheck())
                {
                    Undo.RecordObject(Path, "경로 점 이동");
                    Path.SetWorldPoint(selected, new Vector3(xz.x, Path.FloorHeight, xz.y)); Changed(Path);
                }
                using (new EditorGUI.DisabledScope(Path.PointCount <= 2))
                    if (GUILayout.Button("선택한 점 삭제")) RemoveSelected();
            }
            if (Path.PointCount < 2)
                EditorGUILayout.HelpBox("서로 다른 점이 2개 이상 있어야 사용할 수 있습니다.", MessageType.Warning);
        }

        var route = Path != null ? Path.CreateRoute() : null;
        if (route != null)
        {
            EditorGUILayout.Space();
            EditorGUILayout.LabelField($"{Path.PointCount}개 점 · {route.Length:0.00} m · 걷기 약 {route.Length / Mathf.Max(.01f, arrival.walkSpeed):0.0}초");
            EditorGUI.BeginChangeCheck();
            preview = EditorGUILayout.Slider("발밑 위치 미리보기", preview, 0f, 1f);
            if (EditorGUI.EndChangeCheck()) SceneView.RepaintAll();
            if (EditorApplication.isPlaying)
                EditorGUILayout.LabelField($"{arrival.phase} · {arrival.PathDistance:0.00} / {arrival.PathLength:0.00} m");
        }
        EditorGUILayout.HelpBox("첫 점 = 출발 / 분홍색 = 인사 / 마지막 점 = 의자 옆에 서는 자리\n노란 선 = 착석 위치. 평평한 바닥용이며 가구는 경로를 그릴 때 피해주세요.", MessageType.None);
        EditorGUILayout.EndScrollView();
    }

    public static PersonaGroundPath CreateFromLegacy(PersonaArrival target)
    {
        if (target == null || EditorApplication.isPlayingOrWillChangePlaymode) return null;
        if (target.groundPath != null) return target.groundPath;
        Undo.IncrementCurrentGroup();
        int group = Undo.GetCurrentGroup();
        Undo.SetCurrentGroupName("바닥 이동 경로 만들기");
        Vector3 start = target.entrance != null ? target.entrance.position : target.transform.position;
        var go = new GameObject("04_이동 경로 (바닥)");
        SceneManager.MoveGameObjectToScene(go, target.gameObject.scene);
        Undo.RegisterCreatedObjectUndo(go, "바닥 이동 경로 만들기");
        if (target.entrance != null && target.entrance.parent != null)
            Undo.SetTransformParent(go.transform, target.entrance.parent, "입장 동선에 경로 묶기");
        go.transform.position = start;
        var path = Undo.AddComponent<PersonaGroundPath>(go);
        var world = new List<Vector3> { start };
        if (target.waypoints != null)
            foreach (var waypoint in target.waypoints)
                if (waypoint != null) world.Add(waypoint.position);
        if (world.Count == 1)
            world.Add(target.seat != null ? target.seat.position : start + target.transform.forward);
        Undo.RecordObject(path, "기존 경유지 가져오기");
        path.SetWorldPoints(world);
        Undo.RecordObject(target, "바닥 경로 연결");
        target.groundPath = path;
        Changed(path); Changed(target);
        Undo.CollapseUndoOperations(group);
        Selection.activeGameObject = go;
        return path;
    }

    public static void ApplyStroke(PersonaGroundPath path, IReadOnlyList<Vector3> world, float tolerance)
    {
        if (path == null || world.Count < 2) return;
        var projected = new List<Vector3>(world.Count);
        for (int i = 0; i < world.Count; i++) projected.Add(path.ProjectToFloor(world[i]));
        var simplified = new List<Vector3>();
        LineUtility.Simplify(projected, Mathf.Max(0f, tolerance), simplified);
        if (PersonaGroundRoute.Create(simplified) == null) return;
        Undo.RegisterCompleteObjectUndo(path, "이동 경로 그리기");
        path.SetWorldPoints(simplified);
        Changed(path);
    }

    void DuringSceneGUI(SceneView view)
    {
        if (Path == null || arrival.gameObject.scene != SceneManager.GetActiveScene()) { RestoreTools(); return; }
        bool hideTools = CanEdit && Path.enabled && (drawing || placingGreeting || Selection.activeGameObject == Path.gameObject);
        if (hideTools && !ownsToolVisibility)
        {
            previousToolHidden = Tools.hidden;
            ownsToolVisibility = true;
            Tools.hidden = true;
        }
        else if (!hideTools) RestoreTools();
        var previousZ = Handles.zTest;
        var previousColor = Handles.color;
        Handles.zTest = CompareFunction.Always;
        try
        {
            DrawPath();
            if (CanEdit && Path.enabled)
            {
                HandleDrawing();
                if (!drawing && !placingGreeting && strokeControl == 0)
                {
                    HandlePoints();
                    HandleGreetingPoint();
                }
            }
        }
        finally { Handles.zTest = previousZ; Handles.color = previousColor; }
    }

    void DrawPath()
    {
        var route = Path.CreateRoute();
        Handles.color = Color.cyan;
        for (int i = 1; i < Path.PointCount; i++)
            Handles.DrawAAPolyLine(4f, Path.GetWorldPoint(i - 1), Path.GetWorldPoint(i));
        for (int i = 0; i < Path.PointCount; i++)
            if (i == 0 || i == Path.PointCount - 1 || i == selected)
                Handles.Label(Path.GetWorldPoint(i) + Vector3.up * .08f, PointLabel(i, Path.PointCount));
        if (route != null)
        {
            if (arrival.greetOnPath)
            {
                Vector3 greeting = route.Evaluate(route.Length * arrival.greetPathProgress, out _, out _);
                Handles.color = Color.magenta;
                Handles.DrawWireDisc(greeting, Vector3.up, .16f);
                Handles.Label(greeting + Vector3.up * .12f, "인사");
            }
            Vector3 at = route.Evaluate(route.Length * preview, out Vector3 dir, out _);
            if (EditorApplication.isPlaying && arrival.UsesDrawnPath && arrival.phase == PersonaArrival.Phase.걷기)
                at = arrival.GroundPosition;
            Handles.color = Color.green;
            Handles.DrawWireDisc(at, Vector3.up, .22f);
            Handles.ArrowHandleCap(0, at, Quaternion.LookRotation(dir), .45f, EventType.Repaint);
            if (arrival.seat != null)
            {
                Handles.color = Color.yellow;
                Handles.DrawDottedLine(route.End, arrival.seat.position, 5f);
                Handles.Label(arrival.seat.position, "착석 위치");
            }
        }
        if (stroke.Count >= 2)
        {
            Handles.color = Color.green;
            Handles.DrawAAPolyLine(5f, stroke.ToArray());
        }
    }

    void HandleDrawing()
    {
        var evt = Event.current;
        int control = GUIUtility.GetControlID("PersonaGroundPathDraw".GetHashCode(), FocusType.Passive);
        bool adding = !drawing && !placingGreeting && (evt.shift || evt.control || evt.command);
        if ((drawing || adding || placingGreeting) && !evt.alt && evt.type == EventType.Layout) HandleUtility.AddDefaultControl(control);
        if (evt.type == EventType.KeyDown && evt.keyCode == KeyCode.Escape)
        {
            CancelStroke(); drawing = false; evt.Use(); Repaint(); return;
        }
        if (evt.type == EventType.KeyDown && evt.keyCode == KeyCode.Delete && selected >= 0 && !drawing && !placingGreeting)
        { RemoveSelected(); evt.Use(); return; }
        if (evt.alt || evt.button != 0) return;
        bool hit = TryFloorPoint(evt.mousePosition, out Vector3 point);
        if (evt.type == EventType.MouseDown && hit && HandleUtility.nearestControl == control)
        {
            if (placingGreeting)
            {
                SetGreetingPoint(arrival, point);
                placingGreeting = false; evt.Use(); Repaint();
            }
            else if (drawing)
            {
                stroke.Clear(); stroke.Add(point); strokeControl = control;
                GUIUtility.hotControl = control; evt.Use();
            }
            else if (adding)
            {
                int index = evt.shift ? Path.PointCount : NearestSegment(point) + 1;
                Undo.RecordObject(Path, "경로 점 추가");
                Path.InsertWorldPoint(index, point); selected = index; Changed(Path); evt.Use();
            }
        }
        else if (evt.type == EventType.MouseDrag && GUIUtility.hotControl == strokeControl && strokeControl != 0)
        {
            if (hit && stroke.Count < 2048 && Vector3.Distance(stroke[stroke.Count - 1], point) >= pointSpacing)
                stroke.Add(point);
            evt.Use(); SceneView.RepaintAll();
        }
        else if (evt.type == EventType.MouseUp && strokeControl != 0)
        {
            if (hit && Vector3.Distance(stroke[stroke.Count - 1], point) > .001f) stroke.Add(point);
            ApplyStroke(Path, stroke, simplifyTolerance);
            CancelStroke(); drawing = false; selected = -1; evt.Use(); Repaint();
        }
        else if (evt.type == EventType.Ignore) CancelStroke();
    }

    bool TryFloorPoint(Vector2 mouse, out Vector3 point)
    {
        var ray = HandleUtility.GUIPointToWorldRay(mouse);
        var floor = new Plane(Vector3.up, new Vector3(0f, Path.FloorHeight, 0f));
        bool hit = floor.Raycast(ray, out float enter) && enter < 1000f;
        point = hit ? Path.ProjectToFloor(ray.GetPoint(enter)) : default;
        return hit;
    }

    void HandlePoints()
    {
        for (int i = 0; i < Path.PointCount; i++)
        {
            var p = Path.GetWorldPoint(i);
            int id = GUIUtility.GetControlID(i + 57100, FocusType.Passive);
            Handles.color = i == selected ? Color.yellow : Color.cyan;
            EditorGUI.BeginChangeCheck();
            Vector3 moved = Handles.Slider2D(id, p, Vector3.zero, Vector3.up, Vector3.right, Vector3.forward,
                HandleUtility.GetHandleSize(p) * .055f, Handles.DotHandleCap, Vector2.zero);
            if (GUIUtility.hotControl == id && selected != i) { selected = i; Repaint(); }
            if (!EditorGUI.EndChangeCheck()) continue;
            Undo.RecordObject(Path, "경로 점 이동");
            Path.SetWorldPoint(i, moved); selected = i; Changed(Path); Repaint();
        }
    }

    void HandleGreetingPoint()
    {
        if (!arrival.greetOnPath) return;
        var route = Path.CreateRoute();
        if (route == null) return;
        Vector3 point = route.Evaluate(route.Length * arrival.greetPathProgress, out _, out _);
        int id = GUIUtility.GetControlID("PersonaGreetingPoint".GetHashCode(), FocusType.Passive);
        Handles.color = Color.magenta;
        EditorGUI.BeginChangeCheck();
        Vector3 moved = Handles.Slider2D(id, point, Vector3.zero, Vector3.up, Vector3.right, Vector3.forward,
            HandleUtility.GetHandleSize(point) * .075f, Handles.SphereHandleCap, Vector2.zero);
        if (!EditorGUI.EndChangeCheck()) return;
        SetGreetingPoint(arrival, moved); selected = -1; Repaint();
    }

    public static void SetGreetingPoint(PersonaArrival target, Vector3 world)
    {
        if (target == null || target.groundPath == null || EditorApplication.isPlayingOrWillChangePlaymode) return;
        var route = target.groundPath.CreateRoute();
        if (route == null) return;
        Undo.RecordObject(target, "인사 위치 이동");
        target.greetOnPath = true;
        target.greetPathProgress = route.FindClosestDistance(target.groundPath.ProjectToFloor(world)) / route.Length;
        Changed(target);
    }

    int NearestSegment(Vector3 point)
    {
        int nearest = 0;
        float best = float.PositiveInfinity;
        for (int i = 0; i < Path.PointCount - 1; i++)
        {
            Vector3 a = Path.GetWorldPoint(i), delta = Path.GetWorldPoint(i + 1) - a;
            float t = delta.sqrMagnitude > 1e-8f ? Mathf.Clamp01(Vector3.Dot(point - a, delta) / delta.sqrMagnitude) : 0f;
            float distance = (point - (a + delta * t)).sqrMagnitude;
            if (distance < best) { best = distance; nearest = i; }
        }
        return nearest;
    }

    void RemoveSelected()
    {
        if (!CanEdit || Path == null || Path.PointCount <= 2 || selected < 0 || selected >= Path.PointCount) return;
        Undo.RecordObject(Path, "경로 점 삭제"); Path.RemovePoint(selected);
        selected = Mathf.Min(selected, Path.PointCount - 1); Changed(Path); Repaint();
    }

    void CancelStroke()
    {
        if (strokeControl != 0 && GUIUtility.hotControl == strokeControl) GUIUtility.hotControl = 0;
        strokeControl = 0; stroke.Clear(); placingGreeting = false; SceneView.RepaintAll();
    }

    void FramePath()
    {
        if (Path == null || Path.PointCount == 0) return;
        var bounds = new Bounds(Path.GetWorldPoint(0), Vector3.zero);
        for (int i = 1; i < Path.PointCount; i++) bounds.Encapsulate(Path.GetWorldPoint(i));
        var sceneView = SceneView.lastActiveSceneView != null ? SceneView.lastActiveSceneView : GetWindow<SceneView>();
        sceneView.in2DMode = false;
        sceneView.orthographic = true;
        sceneView.LookAtDirect(bounds.center, Quaternion.Euler(90f, 0f, 0f), Mathf.Max(2f, bounds.extents.magnitude * 1.35f));
        sceneView.Show(); sceneView.Focus();
    }

    static string PointLabel(int i, int count) => i == 0 ? "출발" : i == count - 1 ? "도착" : $"점 {i}";
    static void Changed(Component target)
    {
        EditorUtility.SetDirty(target);
        PrefabUtility.RecordPrefabInstancePropertyModifications(target);
        EditorSceneManager.MarkSceneDirty(target.gameObject.scene);
        SceneView.RepaintAll();
    }
}

[CustomEditor(typeof(PersonaGroundPath))]
public sealed class PersonaGroundPathInspector : Editor
{
    public override void OnInspectorGUI()
    {
        var path = (PersonaGroundPath)target;
        EditorGUILayout.LabelField($"{path.PointCount}개 점 · 바닥 Y {path.FloorHeight:0.000}");
        EditorGUILayout.HelpBox("경로 창에서 바닥에 그리거나 점을 끌어서 수정합니다.", MessageType.Info);
        if (GUILayout.Button("이동 경로 편집 열기")) PersonaGroundPathWindow.Open();
    }
}
