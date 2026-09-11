using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using GLTFast;
using UnityEditor;
using UnityEngine;
using UnityEngine.Rendering;

// Inspects already downloaded files. No generation requests or server calls.
public class TripoRigInspector : EditorWindow
{
    static readonly string[] Files = { "generated.glb", "rigged.glb", "animated.glb" };
    static readonly string[] Labels = { "1. 생성 원본", "2. 리깅 원본", "3. 애니메이션" };
    static readonly Dictionary<string, string> MotionLabels = new Dictionary<string, string>
    {
        { "sit", "앉기" }, { "look_around", "주변 둘러보기" }, { "greet_01", "인사" },
        { "wave_goodbye_01", "작별인사" }, { "agree", "동의" }, { "clap", "박수" },
        { "hug", "포옹" }, { "laugh_01", "웃기" }, { "sob", "흐느끼기" }
    };
    [SerializeField] string directory;
    [SerializeField] int phase = 2;
    [SerializeField] string selectedMotion = "sit";
    [SerializeField] bool showBones = true, loop = true;
    string error, loadedFile;
    string[] directories = Array.Empty<string>();
    int selectedBone, selectedClip, loadVersion;
    bool loading, playing, validating;
    float time;
    double lastPreviewTick;
    int previewFrames;
    GameObject model;
    GltfImport importer;
    Animation animationComponent;
    AnimationClip[] clips = Array.Empty<AnimationClip>();
    Transform[] bones = Array.Empty<Transform>();
    Vector3[] restPositions;
    Quaternion[] restRotations;
    Vector3[] restScales;
    int influencedVertices;
    Vector2 scroll;

    [Serializable]
    class MotionCheck
    {
        public string clip, label;
        public float duration, maxVertexMovement, playbackSeconds;
        public int sampledBonesMoved, playbackBonesMoved, playbackFrames;
        public bool finiteMesh = true, verified;
    }

    [Serializable]
    class MotionReport
    {
        public string status, modelFile, unityVersion, error, scenePath;
        public bool playMode;
        public int bones, clipCount;
        public MotionCheck[] motions;
    }

    [MenuItem("Tools/Tripo/Rig view/Generated model")]
    public static void ShowGenerated() { SwitchPhase(0); }

    [MenuItem("Tools/Tripo/Rig view/Rig before animation")]
    public static void ShowRig() { SwitchPhase(1); }

    [MenuItem("Tools/Tripo/Rig view/Sitting animation")]
    public static void ShowSitting() { SwitchPhase(2); }

    [MenuItem("Tools/Tripo/Rig view/Animations")]
    public static void ShowAnimations() { SwitchPhase(2); }

    static TripoRigInspector ActiveMotionWindow()
    {
        var window = Resources.FindObjectsOfTypeAll<TripoRigInspector>().FirstOrDefault();
        if (window == null || !TripoModelTestScene.IsOpen || window.loading || window.validating || window.clips.Length == 0)
        {
            Debug.LogWarning("Open Tools > Tripo > Inspect local rig, then select 3. 애니메이션.");
            return null;
        }
        return window;
    }

    [MenuItem("Tools/Tripo/Rig view/Next animation")]
    public static void NextAnimation()
    {
        var window = ActiveMotionWindow();
        if (window != null) window.SelectClip((window.selectedClip + 1) % window.clips.Length);
    }

    [MenuItem("Tools/Tripo/Rig view/Next model")]
    public static void NextModel()
    {
        var window = Resources.FindObjectsOfTypeAll<TripoRigInspector>().FirstOrDefault();
        if (window == null || !TripoModelTestScene.IsOpen || window.loading || window.validating) return;
        window.RefreshDirectories();
        if (window.directories.Length == 0) return;
        int next = (Array.IndexOf(window.directories, window.directory) + 1) % window.directories.Length;
        window.UseDirectory(window.directories[next]);
    }

    [MenuItem("Tools/Tripo/Rig view/Sample selected middle")]
    public static void SampleMiddle()
    {
        var window = ActiveMotionWindow();
        if (window != null) window.Sample(window.animationComponent.clip.length * .5f);
    }

    [MenuItem("Tools/Tripo/Rig view/Play selected animation")]
    public static void PlaySelected()
    {
        var window = ActiveMotionWindow();
        if (window != null) window.PlayAnimation();
    }

    [MenuItem("Tools/Tripo/Rig view/Validate all animations")]
    public static void ValidateAnimations()
    {
        var window = ActiveMotionWindow();
        if (window != null) window.ValidateMotions();
    }

    static void SwitchPhase(int phase)
    {
        var window = Resources.FindObjectsOfTypeAll<TripoRigInspector>().FirstOrDefault();
        if (window == null || !TripoModelTestScene.IsOpen || string.IsNullOrEmpty(window.directory))
        {
            Debug.LogWarning("Start with Tools > Tripo > Inspect local rig.");
            return;
        }
        window.LoadPhase(phase);
    }

    public static void Inspect(string sourceDirectory)
    {
        var window = GetWindow<TripoRigInspector>("Tripo 리깅 검사");
        window.minSize = new Vector2(405, 500);
        window.RefreshDirectories();
        if (TripoModelTestScene.IsAvailableModelDirectory(sourceDirectory)) window.directory = sourceDirectory;
        else
        {
            var preferred = EditorPrefs.GetString(TripoModelTestScene.PreferenceKey + ".Directory", "");
            if (TripoModelTestScene.IsAvailableModelDirectory(preferred)) window.directory = preferred;
            else if (!TripoModelTestScene.IsAvailableModelDirectory(window.directory))
                window.directory = window.directories.FirstOrDefault();
        }
        window.RefreshDirectories();
        if (TripoModelTestScene.IsAvailableModelDirectory(window.directory))
            EditorPrefs.SetString(TripoModelTestScene.PreferenceKey + ".Directory", window.directory);
        window.Show();
        if (window.model == null && !window.loading && Directory.Exists(window.directory)) window.LoadPhase(window.phase);
        window.Repaint();
    }

    public static void Suspend()
    {
        foreach (var window in Resources.FindObjectsOfTypeAll<TripoRigInspector>()) window.ClearModel();
    }

    void OnEnable()
    {
        SceneView.duringSceneGui += DrawBones;
        EditorApplication.playModeStateChanged += PlayState;
        EditorApplication.update += UpdatePreview;
    }

    void OnDisable()
    {
        SceneView.duringSceneGui -= DrawBones;
        EditorApplication.playModeStateChanged -= PlayState;
        EditorApplication.update -= UpdatePreview;
        ClearModel();
    }

    void PlayState(PlayModeStateChange state)
    {
        if (state == PlayModeStateChange.ExitingPlayMode || state == PlayModeStateChange.ExitingEditMode) ClearModel();
    }

    void OnInspectorUpdate()
    {
        if (model == null) return;
        if (EditorApplication.isPlaying && playing && !loop && !animationComponent.isPlaying)
            Sample(animationComponent.clip.length);
        Repaint();
        if (showBones) SceneView.RepaintAll();
    }

    void UpdatePreview()
    {
        if (model == null || loading || !playing || EditorApplication.isPlaying) return;
        if (!TripoModelTestScene.IsOpen) { ClearModel(); return; }
        double now = EditorApplication.timeSinceStartup;
        float elapsed = (float)(now - lastPreviewTick);
        if (elapsed < 1f / 60f) return;
        lastPreviewTick = now;
        time += elapsed;
        float duration = animationComponent.clip.length;
        if (loop) time = Mathf.Repeat(time, duration);
        else if (time >= duration) { time = duration; playing = false; }
        animationComponent.clip.SampleAnimation(animationComponent.gameObject, time);
        previewFrames++;
        EditorApplication.QueuePlayerLoopUpdate();
        SceneView.RepaintAll();
        Repaint();
    }

    void OnGUI()
    {
        using (var view = new EditorGUILayout.ScrollViewScope(scroll))
        {
            scroll = view.scrollPosition;
            DrawInspector();
        }
    }

    void DrawInspector()
    {
        EditorGUILayout.LabelField("모델 · 리깅 · 애니메이션 테스트", EditorStyles.boldLabel);
        if (!TripoModelTestScene.IsOpen)
        {
            EditorGUILayout.HelpBox("Tripo_Model_Test 씬을 열면 모델이 자동으로 표시됩니다.", MessageType.Info);
            if (GUILayout.Button("모델 테스트 씬 열기")) TripoModelTestScene.Open();
            return;
        }
        if (TripoModelTestScene.IsComparison)
        {
            EditorGUILayout.HelpBox("실제 로더의 재생·자세 고정을 비교하고 있습니다. Play Mode를 정지하면 모델 검사로 돌아옵니다.", MessageType.Info);
            return;
        }
        EditorGUILayout.HelpBox("Play 버튼 없이도 동작을 재생할 수 있습니다. Scene 탭에서 뼈대와 관절을, Game 탭에서 표면을 확인하세요.", MessageType.Info);
        using (new EditorGUI.DisabledScope(loading || validating)) DrawModelSelector();
        using (new EditorGUI.DisabledScope(loading || validating || string.IsNullOrEmpty(directory)))
        {
            int next = GUILayout.Toolbar(phase, Labels);
            if (next != phase) LoadPhase(next);
        }
        if (loading) { EditorGUILayout.LabelField("모델을 불러오고 있습니다..."); return; }
        if (!string.IsNullOrEmpty(error)) EditorGUILayout.HelpBox(error, MessageType.Error);
        if (model == null) return;
        EditorGUILayout.LabelField("파일", loadedFile);
        EditorGUILayout.LabelField("뼈대", bones.Length + "개");
        showBones = EditorGUILayout.Toggle("뼈대 겹쳐 보기", showBones);
        if (GUILayout.Button("Scene에서 모델 전체 보기")) FrameModel();
        float yaw = EditorGUILayout.Slider("모델 방향", model.transform.eulerAngles.y, 0, 360);
        model.transform.rotation = Quaternion.Euler(0, yaw, 0);

        using (new EditorGUI.DisabledScope(validating)) DrawAnimationControls();
        if (validating)
        {
            EditorGUILayout.HelpBox("모든 동작의 재생과 메쉬 변형을 검사하고 있습니다...", MessageType.Info);
            return;
        }

        if (bones.Length > 0)
        {
            EditorGUILayout.Space();
            int next = EditorGUILayout.Popup("선택한 뼈", selectedBone, bones.Select(b => b.name).ToArray());
            if (next != selectedBone) SelectBone(next);
            EditorGUILayout.LabelField("부모", bones[selectedBone].parent != null ? bones[selectedBone].parent.name : "없음");
            EditorGUILayout.LabelField("영향받는 정점 (가중치 > 1%)", influencedVertices.ToString("N0"));
            EditorGUILayout.HelpBox("리깅 원본에서 어깨·팔꿈치 뼈를 선택한 뒤 E 회전 도구로 살짝 움직여 보세요. 팔 대신 몸통이나 반대 손이 끌려오면 연결 상태를 확인해야 합니다.", MessageType.None);
            if (GUILayout.Button("선택한 뼈 회전 도구 (E)"))
            {
                StopAnimation();
                SelectBone(selectedBone);
                Tools.current = Tool.Rotate;
                SceneView.lastActiveSceneView?.Focus();
            }
            if (GUILayout.Button("수동 회전 되돌리기")) ResetBones();
        }
        EditorGUILayout.Space();
        if (GUILayout.Button("재생 정지 · 시작 자세")) ResetBones();
    }

    void RefreshDirectories()
    {
        directories = TripoModelTestScene.ModelDirectories();
        if (TripoModelTestScene.IsAvailableModelDirectory(directory) && !directories.Contains(directory))
            directories = directories.Concat(new[] { directory }).ToArray();
    }

    void DrawModelSelector()
    {
        if (directories.Length > 0)
        {
            int current = Array.IndexOf(directories, directory);
            int next = EditorGUILayout.Popup("테스트 모델", current, directories.Select(Path.GetFileName).ToArray());
            if (next >= 0 && next != current) UseDirectory(directories[next]);
        }
        else EditorGUILayout.HelpBox("로컬 모델을 찾지 못했습니다. GLB가 있는 실험 폴더를 선택하세요.", MessageType.Warning);
        using (new EditorGUILayout.HorizontalScope())
        {
            if (GUILayout.Button("목록 새로고침")) RefreshDirectories();
            if (GUILayout.Button("모델 폴더 선택"))
            {
                string next = EditorUtility.OpenFolderPanel("Tripo 모델 폴더", directory ?? "", "");
                if (!string.IsNullOrEmpty(next)) UseDirectory(next);
            }
        }
    }

    void UseDirectory(string value)
    {
        if (!TripoModelTestScene.IsAvailableModelDirectory(value))
        {
            Debug.LogWarning("This folder has no test model or is excluded from the model test scene.");
            return;
        }
        directory = value;
        EditorPrefs.SetString(TripoModelTestScene.PreferenceKey + ".Directory", directory);
        RefreshDirectories();
        LoadPhase(phase);
    }

    static string MotionLabel(string name)
    {
        var key = name.Split(':').Last();
        return MotionLabels.TryGetValue(key, out var label) ? label : name;
    }

    void DrawAnimationControls()
    {
        if (animationComponent == null || clips.Length == 0) return;
        EditorGUILayout.Space();
        EditorGUILayout.LabelField("동작 선택 · " + clips.Length + "개", EditorStyles.boldLabel);
        int nextClip = EditorGUILayout.Popup("동작", selectedClip, clips.Select(c => MotionLabel(c.name)).ToArray());
        if (nextClip != selectedClip) SelectClip(nextClip);
        float duration = animationComponent.clip.length;
        float rawTime = playing && EditorApplication.isPlaying ? animationComponent[animationComponent.clip.name].time : time;
        float shownTime = playing && loop ? Mathf.Repeat(rawTime, duration) : Mathf.Clamp(rawTime, 0, duration);
        float nextTime = EditorGUILayout.Slider("재생 위치 (초)", shownTime, 0, duration);
        if (Mathf.Abs(nextTime - shownTime) > .0001f) Sample(nextTime);
        bool nextLoop = EditorGUILayout.Toggle("반복 재생", loop);
        if (nextLoop != loop)
        {
            loop = nextLoop;
            if (playing) { time = shownTime; PlayAnimation(); }
        }
        using (new EditorGUILayout.HorizontalScope())
        {
            if (GUILayout.Button(playing ? "일시 정지" : "재생"))
            {
                if (playing) Sample(shownTime);
                else PlayAnimation();
            }
            if (GUILayout.Button("처음으로")) Sample(0);
            if (GUILayout.Button("다음 동작")) SelectClip((selectedClip + 1) % clips.Length);
        }
    }

    void SelectClip(int index)
    {
        StopAnimation();
        animationComponent.Stop();
        selectedClip = index;
        selectedMotion = clips[index].name;
        animationComponent.clip = clips[index];
        ResetBones();
        Repaint();
        Debug.Log("[TripoRigInspector] selected " + clips[index].name + " (" + MotionLabel(clips[index].name) + ")");
    }

    void PlayAnimation()
    {
        if (animationComponent == null || animationComponent.clip == null) return;
        if (time >= animationComponent.clip.length) time = 0;
        if (!EditorApplication.isPlaying)
        {
            animationComponent.enabled = false;
            lastPreviewTick = EditorApplication.timeSinceStartup;
            playing = true;
            return;
        }
        animationComponent.Stop();
        animationComponent.enabled = true;
        animationComponent.wrapMode = loop ? WrapMode.Loop : WrapMode.Once;
        var state = animationComponent[animationComponent.clip.name];
        state.wrapMode = animationComponent.wrapMode;
        animationComponent.Play(animationComponent.clip.name);
        state.time = time;
        playing = true;
    }

    void StopAnimation()
    {
        playing = false;
        if (animationComponent != null) animationComponent.enabled = false;
    }

    void Sample(float seconds)
    {
        StopAnimation();
        time = seconds;
        animationComponent.clip.SampleAnimation(animationComponent.gameObject, seconds);
        EditorApplication.QueuePlayerLoopUpdate();
        SceneView.RepaintAll();
    }

    void SelectBone(int index)
    {
        selectedBone = index;
        Selection.activeTransform = bones[index];
        influencedVertices = 0;
        foreach (var skin in model.GetComponentsInChildren<SkinnedMeshRenderer>())
        {
            int joint = Array.IndexOf(skin.bones, bones[index]);
            if (joint < 0) continue;
            influencedVertices += skin.sharedMesh.boneWeights.Count(w =>
                (w.boneIndex0 == joint && w.weight0 > .01f) || (w.boneIndex1 == joint && w.weight1 > .01f) ||
                (w.boneIndex2 == joint && w.weight2 > .01f) || (w.boneIndex3 == joint && w.weight3 > .01f));
        }
        SceneView.RepaintAll();
    }

    void ResetBones()
    {
        StopAnimation();
        for (int i = 0; i < bones.Length; i++)
        {
            bones[i].localPosition = restPositions[i];
            bones[i].localRotation = restRotations[i];
            bones[i].localScale = restScales[i];
        }
        if (phase == 2) Sample(0);
        SceneView.RepaintAll();
    }

    void DrawBones(SceneView view)
    {
        if (!showBones || model == null || loading) return;
        var previousColor = Handles.color;
        var previousDepth = Handles.zTest;
        Handles.zTest = CompareFunction.Always;
        for (int i = 0; i < bones.Length; i++)
        {
            var bone = bones[i];
            if (bone == null) continue;
            bool selected = Selection.activeTransform == bone;
            Handles.color = selected ? Color.cyan : new Color(1f, .55f, .08f);
            if (bone.parent != null && Array.IndexOf(bones, bone.parent) >= 0)
                Handles.DrawAAPolyLine(3, bone.parent.position, bone.position);
            float radius = HandleUtility.GetHandleSize(bone.position) * .016f;
            if (Handles.Button(bone.position, Quaternion.identity, radius, radius * 1.7f, Handles.SphereHandleCap)) SelectBone(i);
            if (selected) Handles.Label(bone.position + Vector3.up * .025f, bone.name);
        }
        Handles.zTest = previousDepth;
        Handles.color = previousColor;
    }

    // Preview objects are never serialized into the saved scene or added to builds.
    async void LoadPhase(int next)
    {
        if (loading || validating) return;
        error = null;
        phase = next;
        ClearModel();
        loading = true;
        int version = loadVersion;
        try
        {
            loadedFile = phase == 2 && File.Exists(Path.Combine(directory, "animated_pack.glb"))
                ? "animated_pack.glb" : Files[phase];
            importer = new GltfImport(deferAgent: new UninterruptedDeferAgent());
            if (!await importer.Load(File.ReadAllBytes(Path.Combine(directory, loadedFile))))
                throw new InvalidDataException("GLB를 불러오지 못했습니다.");
            if (version != loadVersion || !TripoModelTestScene.IsOpen) return;
            model = new GameObject("Tripo_Rig_Inspection") { hideFlags = HideFlags.DontSave };
            if (!await importer.InstantiateMainSceneAsync(model.transform))
                throw new InvalidDataException("모델을 배치하지 못했습니다.");
            if (version != loadVersion || !TripoModelTestScene.IsOpen) return;
            foreach (var child in model.GetComponentsInChildren<Transform>(true)) child.gameObject.hideFlags = HideFlags.DontSave;
            animationComponent = model.GetComponentInChildren<Animation>();
            StopAnimation();
            if (animationComponent != null)
                clips = animationComponent.Cast<AnimationState>().Select(s => s.clip).ToArray();
            bones = model.GetComponentsInChildren<SkinnedMeshRenderer>().SelectMany(s => s.bones).Distinct().ToArray();
            restPositions = bones.Select(b => b.localPosition).ToArray();
            restRotations = bones.Select(b => b.localRotation).ToArray();
            restScales = bones.Select(b => b.localScale).ToArray();
            // Normalize once from the bind pose, so selecting sitting/standing clips never resizes the person.
            model.transform.rotation = Quaternion.Euler(0, 90, 0);
            Bounds bounds = ModelBounds();
            model.transform.localScale *= 1.8f / Mathf.Max(bounds.size.y, .001f);
            bounds = ModelBounds();
            model.transform.position -= new Vector3(bounds.center.x, bounds.min.y, bounds.center.z);
            if (clips.Length > 0) SelectClip(Mathf.Max(0, Array.FindIndex(clips, c => c.name == selectedMotion)));
            if (bones.Length > 0)
            {
                int arm = Array.FindIndex(bones, b => b.name.IndexOf("forearm", StringComparison.OrdinalIgnoreCase) >= 0);
                SelectBone(Mathf.Max(0, arm));
            }
            FrameModel();
            Debug.Log("[TripoRigInspector] " + loadedFile + ": " + bones.Length + " bones, " + clips.Length + " clips, animation playing=false");
        }
        catch (Exception exception)
        {
            if (version == loadVersion) { error = exception.Message; Debug.LogException(exception); ClearModel(); }
        }
        finally { if (version == loadVersion) loading = false; Repaint(); SceneView.RepaintAll(); }
    }

    Bounds ModelBounds()
    {
        Bounds result = new Bounds();
        bool first = true;
        foreach (var renderer in model.GetComponentsInChildren<Renderer>())
        {
            var skin = renderer as SkinnedMeshRenderer;
            if (skin != null)
            {
                var baked = new Mesh();
                skin.BakeMesh(baked, false);
                foreach (var vertex in baked.vertices)
                {
                    var position = skin.transform.TransformPoint(vertex);
                    if (first) { result = new Bounds(position, Vector3.zero); first = false; }
                    else result.Encapsulate(position);
                }
                DestroyLocal(baked);
            }
            else if (first) { result = renderer.bounds; first = false; }
            else result.Encapsulate(renderer.bounds);
        }
        return result;
    }

    void FrameModel()
    {
        var sceneView = GetWindow<SceneView>();
        sceneView.rotation = Quaternion.Euler(5, 180, 0);
        sceneView.Frame(ModelBounds(), true);
        sceneView.Repaint();
    }

    // Exercise both Editor preview playback and real Play Mode playback with skinned vertices.
    async void ValidateMotions()
    {
        validating = true;
        int version = loadVersion;
        int originalClip = selectedClip;
        float originalTime = time;
        bool originalLoop = loop;
        bool playMode = EditorApplication.isPlaying;
        string reportPath = Path.Combine(directory, playMode ? "unity_motion_validation.json" : "unity_motion_editor_validation.json");
        var checks = new List<MotionCheck>();
        var report = new MotionReport { status = "running", modelFile = loadedFile,
            unityVersion = Application.unityVersion, bones = bones.Length, clipCount = clips.Length,
            scenePath = UnityEngine.SceneManagement.SceneManager.GetActiveScene().path, playMode = playMode };
        var mesh = new Mesh();
        try
        {
            Application.runInBackground = true;
            loop = true;
            var skins = model.GetComponentsInChildren<SkinnedMeshRenderer>();
            if (skins.Length == 0) throw new InvalidDataException("No skinned mesh in motion model.");
            for (int c = 0; c < clips.Length; c++)
            {
                SelectClip(c);
                var check = new MotionCheck { clip = clips[c].name, label = MotionLabel(clips[c].name), duration = clips[c].length };
                checks.Add(check);
                var rotations = bones.Select(b => b.localRotation).ToArray();
                var positions = bones.Select(b => b.localPosition).ToArray();
                var moved = new HashSet<int>();
                var starts = new List<Vector3[]>();
                foreach (var skin in skins) { skin.BakeMesh(mesh, false); starts.Add(mesh.vertices); }
                for (int step = 1; step <= 4; step++)
                {
                    Sample(check.duration * step / 4f);
                    for (int i = 0; i < bones.Length; i++)
                        if (Quaternion.Angle(rotations[i], bones[i].localRotation) > .05f ||
                            Vector3.Distance(positions[i], bones[i].localPosition) > .0001f) moved.Add(i);
                    for (int s = 0; s < skins.Length; s++)
                    {
                        skins[s].BakeMesh(mesh, false);
                        var vertices = mesh.vertices;
                        if (vertices.Length != starts[s].Length) throw new InvalidDataException("Skinned vertex count changed.");
                        for (int i = 0; i < vertices.Length; i++)
                        {
                            var v = vertices[i];
                            check.finiteMesh &= !(float.IsNaN(v.x) || float.IsNaN(v.y) || float.IsNaN(v.z) ||
                                float.IsInfinity(v.x) || float.IsInfinity(v.y) || float.IsInfinity(v.z));
                            check.maxVertexMovement = Mathf.Max(check.maxVertexMovement, Vector3.Distance(starts[s][i], v));
                        }
                    }
                }
                check.sampledBonesMoved = moved.Count;
                Sample(check.duration * .25f);
                var before = bones.Select(b => b.localRotation).ToArray();
                float beforeTime = time;
                int beforeFrame = playMode ? Time.frameCount : previewFrames;
                PlayAnimation();
                await Task.Delay(850);
                if (version != loadVersion || EditorApplication.isPlaying != playMode || model == null)
                    throw new OperationCanceledException("Preview stopped during motion validation.");
                check.playbackSeconds = (playMode ? animationComponent[clips[c].name].time : time) - beforeTime;
                check.playbackFrames = (playMode ? Time.frameCount : previewFrames) - beforeFrame;
                check.playbackBonesMoved = bones.Where((b, i) => Quaternion.Angle(before[i], b.localRotation) > .05f).Count();
                check.verified = check.duration > 0 && check.finiteMesh && check.sampledBonesMoved > 0 &&
                    check.maxVertexMovement > .0001f && check.playbackSeconds > .05f && check.playbackFrames > 1;
                Debug.Log("[TripoRigInspector] motion " + check.clip + ": " + (check.verified ? "verified" : "FAILED"));
            }
            report.status = checks.All(c => c.verified) ? "all_motions_verified" : "failed";
        }
        catch (OperationCanceledException)
        {
            report.status = "cancelled";
        }
        catch (Exception exception)
        {
            report.status = "failed";
            report.error = exception.Message;
            Debug.LogException(exception);
        }
        finally
        {
            DestroyLocal(mesh);
            report.motions = checks.ToArray();
            File.WriteAllText(reportPath, JsonUtility.ToJson(report, true));
            if (version == loadVersion)
            {
                validating = false;
                loop = originalLoop;
                if (model != null && clips.Length > originalClip)
                {
                    SelectClip(originalClip);
                    Sample(originalTime);
                }
            }
            Debug.Log("[TripoRigInspector] " + report.status + ": " + checks.Count + " clips");
            if (this != null) Repaint();
        }
    }

    void ClearModel()
    {
        loadVersion++;
        loading = false;
        validating = false;
        StopAnimation();
        if (model != null)
        {
            model.SetActive(false);
            if (EditorApplication.isPlaying) Destroy(model);
            else DestroyImmediate(model);
        }
        model = null;
        animationComponent = null;
        clips = Array.Empty<AnimationClip>();
        bones = Array.Empty<Transform>();
        importer?.Dispose();
        importer = null;
    }

    static void DestroyLocal(UnityEngine.Object value)
    {
        if (EditorApplication.isPlaying) Destroy(value);
        else DestroyImmediate(value);
    }
}
