using System;
using System.Collections.Generic;
using UnityEditor;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.SceneManagement;

// 현재 스포너가 불러온 모델을 읽기만 하는 Play Mode용 리깅 표시 창.
// 표시 메시와 재질은 창이 소유하며 씬/빌드에는 저장하지 않는다.
public class PersonaRigViewerWindow : EditorWindow
{
    const string ShaderPath = "Assets/Editor/PersonaRigOverlay.shader";
    const string OverlayName = "__PersonaRigOverlay";
    static readonly Color CenterColor = new Color(1f, .87f, .12f);
    static readonly Color LeftColor = new Color(.08f, .85f, 1f);
    static readonly Color RightColor = new Color(1f, .32f, .2f);

    [SerializeField] bool showRig = true, showJoints = true, xray = true, showNames;
    [SerializeField] float thickness = .006f;
    PersonaSpawner spawner;
    GameObject model, overlay;
    Mesh mesh;
    Material material;
    MeshRenderer overlayRenderer;
    Transform[] bones = Array.Empty<Transform>();
    int[] parents = Array.Empty<int>();
    Color[] boneColors = Array.Empty<Color>();
    string[] boneNames = Array.Empty<string>();
    readonly List<Vector3> vertices = new List<Vector3>(1024);
    readonly List<Color> colors = new List<Color>(1024);
    readonly List<int> triangles = new List<int>(4096);
    int selectedBone, links, humanoidBones, skinnedMeshes;
    double nextSearch;
    string error;

    [MenuItem("Tools/Persona/리깅 보기", priority = 4)]
    public static void Open()
    {
        var window = GetWindow<PersonaRigViewerWindow>("캐릭터 리깅 보기");
        window.minSize = new Vector2(370, 410);
        window.Show();
    }

    [MenuItem("Tools/Persona/리깅 표시 켜기·끄기 _F8", priority = 5)]
    public static void Toggle()
    {
        var windows = Resources.FindObjectsOfTypeAll<PersonaRigViewerWindow>();
        if (windows.Length == 0) { Open(); return; }
        windows[0].showRig = !windows[0].showRig;
        windows[0].ApplyVisibility();
        windows[0].Repaint();
    }

    void OnEnable()
    {
        EditorApplication.update += Tick;
        EditorApplication.playModeStateChanged += PlayState;
        SceneView.duringSceneGui += DrawNames;
        // 애니메이션/LateUpdate가 끝난 실제 위치를 카메라 렌더 직전에 읽는다.
        RenderPipelineManager.beginCameraRendering += BeforeCamera;
        Camera.onPreCull += BeforeBuiltinCamera;
    }

    void OnDisable()
    {
        EditorApplication.update -= Tick;
        EditorApplication.playModeStateChanged -= PlayState;
        SceneView.duringSceneGui -= DrawNames;
        RenderPipelineManager.beginCameraRendering -= BeforeCamera;
        Camera.onPreCull -= BeforeBuiltinCamera;
        ClearOverlay();
    }

    void PlayState(PlayModeStateChange state)
    {
        if (state == PlayModeStateChange.ExitingPlayMode || state == PlayModeStateChange.ExitingEditMode)
        {
            ClearOverlay();
            spawner = null;
            nextSearch = 0;
        }
        Repaint();
    }

    void OnInspectorUpdate() => Repaint();

    void Tick()
    {
        if (!EditorApplication.isPlaying || EditorApplication.isCompiling) return;
        double now = EditorApplication.timeSinceStartup;
        if (spawner == null && now >= nextSearch)
        {
            nextSearch = now + .5;
            var scene = SceneManager.GetActiveScene();
            foreach (var candidate in FindObjectsOfType<PersonaSpawner>())
                if (candidate.gameObject.scene == scene) { spawner = candidate; break; }
        }
        var current = spawner != null ? spawner.Spawned : null;
        if (current != model) { ClearOverlay(); model = current; nextSearch = 0; }
        // Spawned는 비동기 GLB 로드 도중에도 설정된다. Skin이 생길 때까지 기다린다.
        if (model != null && bones.Length == 0 && now >= nextSearch)
        {
            nextSearch = now + .5;
            BindBones();
        }
        if (model == null && overlay != null) ClearOverlay();
        ApplyVisibility();
    }

    void BindBones()
    {
        var skins = model.GetComponentsInChildren<SkinnedMeshRenderer>(true);
        var unique = new HashSet<Transform>();
        foreach (var skin in skins)
            foreach (var bone in skin.bones)
                if (bone != null && bone.IsChildOf(model.transform)) unique.Add(bone);
        if (unique.Count == 0) return;

        var ordered = new List<Transform>(unique.Count);
        foreach (var bone in model.GetComponentsInChildren<Transform>(true))
            if (unique.Contains(bone)) ordered.Add(bone);
        bones = ordered.ToArray();
        parents = new int[bones.Length];
        boneNames = new string[bones.Length];
        boneColors = new Color[bones.Length];
        links = humanoidBones = 0;
        skinnedMeshes = skins.Length;
        for (int i = 0; i < bones.Length; i++)
        {
            // 실제 부모가 Skin 본일 때만 연결한다. 가상의 뼈/루트를 만들지 않는다.
            parents[i] = Array.IndexOf(bones, bones[i].parent);
            if (parents[i] >= 0) links++;
            boneNames[i] = bones[i].name;
            string name = boneNames[i].ToLowerInvariant();
            boneColors[i] = name.EndsWith(".l") || name.StartsWith("l_") || name.Contains("left")
                ? LeftColor : name.EndsWith(".r") || name.StartsWith("r_") || name.Contains("right")
                ? RightColor : CenterColor;
        }
        foreach (var animator in model.GetComponentsInChildren<Animator>(true))
        {
            if (animator.avatar == null || !animator.avatar.isValid || !animator.avatar.isHuman) continue;
            for (int i = 0; i < (int)HumanBodyBones.LastBone; i++)
                if (animator.GetBoneTransform((HumanBodyBones)i) != null) humanoidBones++;
            break;
        }

        var shader = AssetDatabase.LoadAssetAtPath<Shader>(ShaderPath);
        if (shader == null || !shader.isSupported)
        {
            error = "리깅 표시 셰이더를 불러오지 못했습니다. Console을 확인하세요.";
            return;
        }
        overlay = new GameObject(OverlayName) { hideFlags = HideFlags.HideAndDontSave };
        SceneManager.MoveGameObjectToScene(overlay, model.scene);
        overlay.layer = skins[0].gameObject.layer;
        mesh = new Mesh { name = "PersonaRigOverlay", hideFlags = HideFlags.HideAndDontSave };
        mesh.MarkDynamic();
        material = new Material(shader) { hideFlags = HideFlags.HideAndDontSave };
        overlay.AddComponent<MeshFilter>().sharedMesh = mesh;
        overlayRenderer = overlay.AddComponent<MeshRenderer>();
        overlayRenderer.sharedMaterial = material;
        overlayRenderer.shadowCastingMode = ShadowCastingMode.Off;
        overlayRenderer.receiveShadows = false;
        overlayRenderer.lightProbeUsage = LightProbeUsage.Off;
        overlayRenderer.reflectionProbeUsage = ReflectionProbeUsage.Off;
        overlayRenderer.motionVectorGenerationMode = MotionVectorGenerationMode.ForceNoMotion;
        error = null;
        selectedBone = 0;
        UpdateMesh();
        Debug.Log($"[PersonaRigViewer] Skin 본 {bones.Length}개, 연결 {links}개, Humanoid 매핑 {humanoidBones}개");
    }

    void ApplyVisibility()
    {
        if (overlayRenderer != null)
            overlayRenderer.enabled = showRig && model != null && model.activeInHierarchy;
        if (material != null)
            material.SetInt("_ZTest", (int)(xray ? CompareFunction.Always : CompareFunction.LessEqual));
    }

    void BeforeCamera(ScriptableRenderContext context, Camera camera) => UpdateMesh();
    void BeforeBuiltinCamera(Camera camera)
    {
        if (GraphicsSettings.currentRenderPipeline == null) UpdateMesh();
    }

    void UpdateMesh()
    {
        if (!showRig || mesh == null || model == null) return;
        vertices.Clear(); colors.Clear(); triangles.Clear();
        float width = Mathf.Max(.001f, thickness);
        for (int i = 0; i < bones.Length; i++)
        {
            if (bones[i] == null) continue;
            Vector3 point = bones[i].position;
            Color color = i == selectedBone ? Color.white : boneColors[i];
            int p = parents[i];
            if (p >= 0 && bones[p] != null)
            {
                Vector3 from = bones[p].position;
                Vector3 direction = point - from;
                float length = direction.magnitude;
                if (length > .00001f)
                {
                    direction /= length;
                    Vector3 side = Vector3.Cross(direction,
                        Mathf.Abs(direction.y) > .9f ? Vector3.right : Vector3.up).normalized;
                    Vector3 up = Vector3.Cross(direction, side);
                    float radius = Mathf.Min(width, length * .12f);
                    AddDiamond(from, point, Vector3.Lerp(from, point, .3f), side * radius, up * radius, color);
                }
            }
            if (showJoints)
            {
                float radius = width * (i == selectedBone ? 1.8f : 1.15f);
                AddDiamond(point - Vector3.up * radius, point + Vector3.up * radius,
                    point, Vector3.right * radius, Vector3.forward * radius, color);
            }
        }
        mesh.Clear();
        mesh.SetVertices(vertices);
        mesh.SetColors(colors);
        mesh.SetTriangles(triangles, 0);
        mesh.RecalculateBounds();
    }

    void AddDiamond(Vector3 a, Vector3 b, Vector3 center, Vector3 right, Vector3 up, Color color)
    {
        int start = vertices.Count;
        vertices.Add(a); vertices.Add(b);
        vertices.Add(center + right); vertices.Add(center + up);
        vertices.Add(center - right); vertices.Add(center - up);
        for (int i = 0; i < 6; i++) colors.Add(color);
        for (int i = 0; i < 4; i++)
        {
            int c = start + 2 + i, d = start + 2 + (i + 1) % 4;
            triangles.Add(start); triangles.Add(c); triangles.Add(d);
            triangles.Add(start + 1); triangles.Add(d); triangles.Add(c);
        }
    }

    void OnGUI()
    {
        EditorGUILayout.LabelField("실행 중인 캐릭터의 리깅", EditorStyles.boldLabel);
        EditorGUILayout.HelpBox("Play 후 모델이 로드되면 Game/Scene 화면에 뼈를 표시합니다. F8로 표시를 켜고 끌 수 있습니다.", MessageType.Info);
        EditorGUI.BeginChangeCheck();
        showRig = EditorGUILayout.Toggle("리깅 표시 (F8)", showRig);
        showJoints = EditorGUILayout.Toggle("관절점 표시", showJoints);
        xray = EditorGUILayout.Toggle("몸을 투과해 표시", xray);
        showNames = EditorGUILayout.Toggle("뼈 이름 표시 (Scene)", showNames);
        thickness = EditorGUILayout.Slider("표시 두께", thickness, .001f, .02f);
        if (EditorGUI.EndChangeCheck()) { ApplyVisibility(); UpdateMesh(); SceneView.RepaintAll(); }
        using (new EditorGUILayout.HorizontalScope())
        {
            Legend("중앙", CenterColor); Legend("왼쪽", LeftColor); Legend("오른쪽", RightColor);
        }
        EditorGUILayout.Space();
        if (!EditorApplication.isPlaying) { EditorGUILayout.LabelField("Play를 기다리고 있습니다."); return; }
        if (!string.IsNullOrEmpty(error)) { EditorGUILayout.HelpBox(error, MessageType.Error); return; }
        if (model == null || bones.Length == 0) { EditorGUILayout.LabelField("캐릭터의 뼈를 불러오는 중입니다."); return; }
        EditorGUILayout.LabelField("Skin 본 / 연결", $"{bones.Length}개 / {links}개");
        EditorGUILayout.LabelField("Humanoid 매핑 / Skin 메시", $"{humanoidBones}개 / {skinnedMeshes}개");
        selectedBone = EditorGUILayout.Popup("확인할 뼈", selectedBone, boneNames);
        var bone = bones[Mathf.Clamp(selectedBone, 0, bones.Length - 1)];
        if (bone != null)
        {
            EditorGUILayout.LabelField("부모", bone.parent != null ? bone.parent.name : "없음");
            using (new EditorGUI.DisabledScope(true))
                EditorGUILayout.Vector3Field("현재 위치", bone.position);
            if (GUILayout.Button("선택한 뼈를 Inspector에서 보기")) Selection.activeTransform = bone;
        }
        if (GUILayout.Button("Scene 화면을 캐릭터에 맞추기"))
        {
            var bounds = new Bounds(bones[0].position, Vector3.one * .1f);
            foreach (var t in bones) if (t != null) bounds.Encapsulate(t.position);
            GetWindow<SceneView>().Frame(bounds, false);
        }
    }

    static void Legend(string label, Color color)
    {
        Rect rect = GUILayoutUtility.GetRect(12, 16, GUILayout.Width(12));
        EditorGUI.DrawRect(new Rect(rect.x, rect.y + 3, 10, 10), color);
        GUILayout.Label(label, GUILayout.Width(48));
    }

    void DrawNames(SceneView view)
    {
        if (!showRig || !showNames || !EditorApplication.isPlaying || model == null) return;
        var previous = Handles.zTest;
        Handles.zTest = xray ? CompareFunction.Always : CompareFunction.LessEqual;
        for (int i = 0; i < bones.Length; i++)
            if (bones[i] != null) Handles.Label(bones[i].position, boneNames[i]);
        Handles.zTest = previous;
    }

    void ClearOverlay()
    {
        if (overlay != null) DestroyImmediate(overlay);
        if (mesh != null) DestroyImmediate(mesh);
        if (material != null) DestroyImmediate(material);
        overlay = model = null;
        mesh = null; material = null; overlayRenderer = null;
        bones = Array.Empty<Transform>();
        parents = Array.Empty<int>();
        boneNames = Array.Empty<string>();
        boneColors = Array.Empty<Color>();
        error = null;
    }
}
