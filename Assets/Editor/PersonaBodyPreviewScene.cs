using System;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using GLTFast;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Animations;
using UnityEngine.Networking;
using UnityEngine.Playables;
using UnityEngine.Rendering;
using UnityEngine.SceneManagement;
using Object = UnityEngine.Object;

/// <summary>결합 GLB를 저장하지 않고 스튜디오·실제 카페에서 기존 체형 키를 조절한다.</summary>
[InitializeOnLoad]
public static class PersonaBodyPreviewScene
{
    public const string ScenePath = "Assets/Scenes/Persona_BodyShape_Preview.unity";
    public const string ProfilePath = "Assets/Settings/PersonaBodyShapePreview.asset";
    public const string CameraName = "Body Shape Preview Camera";
    const string ModelName = "__BodyShapePreview_NotSaved";
    const string CacheRelative = "tools/_work/body_shape_preview/current_model.private.glb";
    public enum Pose { T포즈, 서있기, 앉기 }
    public static Pose CurrentPose { get; private set; } = (Pose)SessionState.GetInt("PersonaBodyPreview.Pose", 0);
    public static string Status { get; private set; } = "얼굴과 몸체가 결합된 모델을 불러오세요.";
    public static bool Busy { get; private set; }
    public static bool IsOpen => SceneManager.GetActiveScene().path == ScenePath || PersonaBodyPreviewCafe.IsActive;
    public static GameObject Model { get; private set; }
    public static PersonaBodyShapeController Controller { get; private set; }
    public static Animator Animator { get; private set; }
    public static PersonaBodyShapeProfile Profile => AssetDatabase.LoadAssetAtPath<PersonaBodyShapeProfile>(ProfilePath);
    public static event Action Changed;
    static GltfImport importer;
    static Avatar avatar;
    static PlayableGraph graph;
    static Transform[] restBones;
    static Vector3[] restPositions, restScales;
    static Quaternion[] restRotations;
    static readonly System.Collections.Generic.Dictionary<Transform, Quaternion> handBindRotations =
        new System.Collections.Generic.Dictionary<Transform, Quaternion>();
    static readonly System.Collections.Generic.Dictionary<SkinnedMeshRenderer, Mesh> sourceMeshes =
        new System.Collections.Generic.Dictionary<SkinnedMeshRenderer, Mesh>();
    internal static Mesh OriginalMesh(SkinnedMeshRenderer skin) => sourceMeshes.TryGetValue(skin, out var mesh) ? mesh : null;
    static int generation;
    static bool opening;
    static Connection connection;
    static string CachePath => Path.GetFullPath(Path.Combine(Application.dataPath, "..", CacheRelative));

    sealed class Connection { public string url, token, session, local; }
    [Serializable] sealed class SessionResult { public string session; public bool has_model; }
    [Serializable] sealed class PreviewState { public string status, pose; public bool busy, modelLoaded, sourceAvailable, separateHead; public int shapes; }

    static PersonaBodyPreviewScene()
    {
        EditorSceneManager.sceneOpened += (_, __) => Resume();
        EditorSceneManager.activeSceneChangedInEditMode += (_, next) =>
        {
            Clear();
            if (next.path != PersonaBodyPreviewCafe.ScenePath) PersonaBodyPreviewCafe.Enabled = false;
            if (IsOpen) Resume();
        };
        EditorApplication.playModeStateChanged += state =>
        {
            if (state == PlayModeStateChange.ExitingEditMode || state == PlayModeStateChange.ExitingPlayMode) Clear();
            if (state == PlayModeStateChange.EnteredEditMode) Resume();
        };
        AssemblyReloadEvents.beforeAssemblyReload += Clear;
        EditorApplication.quitting += Clear;
        Undo.undoRedoPerformed += ApplyShapes;
        EditorApplication.delayCall += Resume;
    }

    [MenuItem("Tools/다시봄/캐릭터/얼굴·몸체 비율 조절 씬 열기", priority = 3)]
    public static async void Open()
    {
        if (IsOpen && Model != null) { PersonaBodyPreviewWindow.Open(); return; }
        await SetEnvironment(SceneManager.GetActiveScene().path == PersonaBodyPreviewCafe.ScenePath);
    }

    public static async Task SetEnvironment(bool cafe)
    {
        if (EditorApplication.isPlayingOrWillChangePlaymode)
        {
            EditorUtility.DisplayDialog("체형 미리보기", "Play를 종료한 뒤 열어 주세요. 이 씬에서는 Play 없이 조절합니다.", "확인");
            return;
        }
        if (Busy || opening) return;
        if (IsOpen && Model != null && cafe == PersonaBodyPreviewCafe.IsActive) { PersonaBodyPreviewWindow.Open(); return; }
        if (!EditorSceneManager.SaveCurrentModifiedScenesIfUserWantsTo()) return;
        CaptureConnection();
        opening = true;
        try
        {
            EnsureAssets();
            Clear();
            PersonaBodyPreviewCafe.Enabled = cafe;
            string path = cafe ? PersonaBodyPreviewCafe.ScenePath : ScenePath;
            if (!cafe && !File.Exists(ScenePath)) CreateScene();
            else if (SceneManager.GetActiveScene().path != path) EditorSceneManager.OpenScene(path, OpenSceneMode.Single);
            if (cafe) { CaptureConnection(); CurrentPose = Pose.앉기; }
            PersonaBodyPreviewWindow.Open();
            if (File.Exists(CachePath)) await LoadFile(CachePath);
            else if (connection != null) await LoadRegisteredModel();
            else SetStatus("Scene_2에서 이 메뉴를 열거나, ‘로컬 결합 GLB 열기’로 모델을 선택하세요.");
        }
        catch (Exception error) { Fail(error); }
        finally { opening = false; SetStatus(Status); }
    }

    static void CaptureConnection()
    {
        if (SceneManager.GetActiveScene().path == ScenePath) return;
        var scene = SceneManager.GetActiveScene();
        var spawner = scene.GetRootGameObjects().SelectMany(r => r.GetComponentsInChildren<PersonaSpawner>(true)).FirstOrDefault();
        if (spawner == null) { connection = null; return; }
        var voice = spawner.voiceClient;
        connection = new Connection
        {
            url = (voice != null ? voice.serverUrl : spawner.serverUrl).TrimEnd('/'),
            token = voice != null ? voice.token : spawner.token,
            session = !string.IsNullOrWhiteSpace(spawner.sessionIdOverride) ? spawner.sessionIdOverride.Trim()
                : voice != null && !voice.followServerSession ? voice.sessionId : "",
            local = spawner.localGlbPath,
        };
        // 주소·토큰·인물 ID는 메모리에만 두고 씬·설정 자산·로그에 쓰지 않는다.
    }

    static void Resume()
    {
        if (!opening && !Busy && !EditorApplication.isPlayingOrWillChangePlaymode && IsOpen && Model == null)
        {
            EnsureAssets();
            PersonaBodyPreviewWindow.Open();
            if (PersonaBodyPreviewCafe.IsActive) CaptureConnection();
            if (File.Exists(CachePath)) _ = LoadFile(CachePath);
            else SetStatus("Scene_2에서 이 메뉴를 열거나, ‘로컬 결합 GLB 열기’로 모델을 선택하세요.");
        }
    }

    public static async Task LoadRegisteredModel()
    {
        if (Busy || !IsOpen) return;
        if (connection == null)
        {
            SetStatus("다른 등록 인물을 가져오려면 Scene_2에서 이 씬 열기 메뉴를 다시 선택하세요. 로컬 결합 GLB도 열 수 있습니다.");
            return;
        }
        Busy = true;
        SetStatus("현재 등록된 결합 모델을 가져오는 중입니다…");
        try
        {
            byte[] bytes;
            if (!string.IsNullOrWhiteSpace(connection.local))
            {
                string path = Path.IsPathRooted(connection.local) ? connection.local
                    : Path.GetFullPath(Path.Combine(Application.dataPath, "..", connection.local));
                bytes = File.ReadAllBytes(path);
            }
            else
            {
                string session = connection.session;
                if (string.IsNullOrWhiteSpace(session))
                {
                    var data = await Get(connection.url + "/session/current", connection.token);
                    var current = JsonUtility.FromJson<SessionResult>(System.Text.Encoding.UTF8.GetString(data));
                    if (current == null || !current.has_model || string.IsNullOrEmpty(current.session))
                        throw new InvalidOperationException("현재 등록 인물의 모델이 아직 준비되지 않았습니다.");
                    session = current.session;
                }
                bytes = await Get(connection.url + "/session/" + UnityWebRequest.EscapeURL(session) + "/model.glb", connection.token);
            }
            await Install(bytes);
            if (Model != null && IsOpen)
            {
                Directory.CreateDirectory(Path.GetDirectoryName(CachePath));
                File.WriteAllBytes(CachePath, bytes);
            }
        }
        catch (Exception error) { Fail(error); }
        finally { Busy = false; SetStatus(Status); }
    }

    static async Task<byte[]> Get(string url, string token)
    {
        using (var request = UnityWebRequest.Get(url))
        {
            if (!string.IsNullOrEmpty(token)) request.SetRequestHeader("X-Token", token);
            request.timeout = 120;
            var operation = request.SendWebRequest();
            while (!operation.isDone)
            {
                EditorApplication.QueuePlayerLoopUpdate();
                await Task.Delay(25);
            }
            if (request.result != UnityWebRequest.Result.Success)
                throw new InvalidOperationException("모델 서버 요청을 완료하지 못했습니다. HTTP " + request.responseCode);
            return request.downloadHandler.data;
        }
    }

    public static async Task LoadFile(string path)
    {
        if (Busy || !IsOpen || EditorApplication.isPlayingOrWillChangePlaymode) return;
        Busy = true;
        SetStatus("결합 모델을 읽는 중입니다…");
        try
        {
            byte[] bytes = File.ReadAllBytes(path);
            await Install(bytes);
            if (Model != null && IsOpen && Path.GetFullPath(path) != CachePath)
            {
                Directory.CreateDirectory(Path.GetDirectoryName(CachePath));
                File.WriteAllBytes(CachePath, bytes);
            }
        }
        catch (Exception error) { Fail(error); }
        finally { Busy = false; SetStatus(Status); }
    }

    static async Task Install(byte[] bytes)
    {
        int ticket = ++generation;
        var incoming = new GltfImport(deferAgent: new UninterruptedDeferAgent());
        GameObject candidate = null;
        try
        {
            var settings = new ImportSettings { GenerateMipMaps = true, AnisotropicFilterLevel = 8, AnimationMethod = AnimationMethod.Legacy };
            if (!await incoming.Load(bytes, null, settings)) throw new InvalidOperationException("GLB를 읽지 못했습니다.");
            if (ticket != generation || !IsOpen || EditorApplication.isPlayingOrWillChangePlaymode) return;
            candidate = new GameObject(ModelName) { hideFlags = HideFlags.DontSaveInEditor };
            SceneManager.MoveGameObjectToScene(candidate, SceneManager.GetActiveScene());
            if (!await incoming.InstantiateMainSceneAsync(candidate.transform)) throw new InvalidOperationException("GLB 장면 생성에 실패했습니다.");
            if (ticket != generation || !IsOpen || EditorApplication.isPlayingOrWillChangePlaymode) return;
            foreach (var item in candidate.GetComponentsInChildren<Transform>(true)) item.gameObject.hideFlags = HideFlags.DontSaveInEditor;
            ReleaseModel();
            Model = candidate;
            candidate = null;
            importer = incoming;
            incoming = null;
            foreach (var legacy in Model.GetComponentsInChildren<Animation>(true)) legacy.enabled = false;
            PersonaBodyPreviewCafe.Normalize(Model);
            foreach (var skin in Model.GetComponentsInChildren<SkinnedMeshRenderer>(true))
            {
                sourceMeshes[skin] = skin.sharedMesh;
                skin.updateWhenOffscreen = true;
                // Edit Mode의 수동 자세·카메라 촬영에서도 이전 프레임의 스킨 행렬을 쓰지 않는다.
                skin.forceMatrixRecalculationPerRender = true;
            }
            // 애니메이션 Avatar 기준에는 손목·손가락 회전도 포함된다. 표시용 T포즈에는 제작 바인드 자세를 쓴다.
            handBindRotations.Clear();
            foreach (var side in new[] { HumanBodyBones.LeftHand, HumanBodyBones.RightHand })
            {
                var hand = PersonaHumanoid.FindBone(Model.transform, side);
                if (hand != null)
                    foreach (var bone in hand.GetComponentsInChildren<Transform>(true)) handBindRotations[bone] = bone.localRotation;
            }
            var reference = AssetDatabase.LoadAssetAtPath<PersonaHumanoidReferencePose>(PersonaReferencePoseSetup.AssetPath);
            if (!PersonaHumanoid.TryPrepare(Model.transform, out var skeleton, out avatar, out _, Model.transform, reference))
                throw new InvalidOperationException("고정 몸체의 Humanoid 리깅을 준비하지 못했습니다.");
            avatar.hideFlags = HideFlags.HideAndDontSave;
            Animator = skeleton.GetComponent<Animator>();
            if (Animator == null) Animator = skeleton.gameObject.AddComponent<Animator>();
            Animator.avatar = avatar;
            Animator.applyRootMotion = false;
            Animator.cullingMode = AnimatorCullingMode.AlwaysAnimate;
            restBones = Model.GetComponentsInChildren<Transform>(true);
            restPositions = restBones.Select(b => b.localPosition).ToArray();
            restRotations = restBones.Select(b => b.localRotation).ToArray();
            restScales = restBones.Select(b => b.localScale).ToArray();
            Controller = Model.GetComponent<PersonaBodyShapeController>();
            if (Controller == null) Controller = Model.AddComponent<PersonaBodyShapeController>();
            Controller.Bind(Profile);
            if (Controller.BoundShapeCount != PersonaBodyShapeProfile.ShapeCount)
                throw new InvalidOperationException("체형 Blend Shape 6개가 있는 얼굴·몸체 결합 GLB가 필요합니다.");
            SetPose(CurrentPose);
            SetStatus("얼굴·몸체 연결 완료 · 체형 6/6 · Humanoid 리깅 정상 · " + (Controller.HasSeparateHead ? "머리 크기·위치 조절 연결" : "분리 머리 없음"));
        }
        catch { ReleaseModel(); throw; }
        finally
        {
            if (candidate != null) Object.DestroyImmediate(candidate);
            incoming?.Dispose();
        }
    }

    public static void SetPose(Pose pose)
    {
        CurrentPose = pose;
        SessionState.SetInt("PersonaBodyPreview.Pose", (int)pose);
        if (Model == null || Animator == null) return;
        if (graph.IsValid()) graph.Destroy();
        Animator.Rebind();
        for (int i = 0; i < restBones.Length; i++)
            if (restBones[i] != null)
            {
                restBones[i].localPosition = restPositions[i];
                restBones[i].localRotation = restRotations[i];
                restBones[i].localScale = restScales[i];
            }
        if (pose == Pose.T포즈) ApplyDisplayTPose();
        else
        {
            string name = pose == Pose.앉기 ? "Sitting Idle" : "Idle";
            var clip = PersonaBodyPreviewCafe.IsActive ? PersonaBodyPreviewCafe.Clip(pose) : null;
            if (clip == null) clip = AssetDatabase.LoadAllAssetsAtPath(PersonaArrivalSetup.AnimationFolder + "/" + name + ".fbx")
                .OfType<AnimationClip>().FirstOrDefault(c => c.isHumanMotion && !c.name.StartsWith("__preview__"));
            if (clip == null) throw new InvalidOperationException("미리보기 자세 클립이 없습니다.");
            graph = PlayableGraph.Create("Body Shape Preview");
            graph.SetTimeUpdateMode(DirectorUpdateMode.Manual);
            var playable = AnimationClipPlayable.Create(graph, clip);
            playable.SetApplyFootIK(false);
            AnimationPlayableOutput.Create(graph, "body", Animator).SetSourcePlayable(playable);
            graph.Play();
            float time = PersonaBodyPreviewCafe.IsActive && pose == Pose.앉기 && PersonaBodyPreviewCafe.Arrival != null && !PersonaBodyPreviewCafe.Arrival.loopSeated
                ? PersonaBodyPreviewCafe.Arrival.seatedPoseTime : clip.length * .35f;
            playable.SetTime(time);
            graph.Evaluate(.01f);
        }
        Controller.ApplyNow();
        Place();
        if (PersonaBodyPreviewCafe.IsActive) PersonaBodyPreviewCafe.PlayerView();
        else Frame(0f);
        var expectedModel = Model;
        EditorApplication.delayCall += () =>
        {
            if (Model == expectedModel && Model != null && IsOpen && CurrentPose == pose)
            {
                if (PersonaBodyPreviewCafe.IsActive) PersonaBodyPreviewCafe.PlayerView();
                else Frame(0f);
            }
        };
        Repaint();
        SetStatus(Status);
    }

    static void ApplyDisplayTPose()
    {
        foreach (var item in handBindRotations)
            if (item.Key != null) item.Key.localRotation = item.Value;
        Vector3 facing = Vector3.ProjectOnPlane(PersonaHumanoid.MeasureFacing(Model.transform), Vector3.up).normalized;
        Vector3 right = Vector3.Cross(Vector3.up, facing).normalized;
        var chest = PersonaHumanoid.FindBone(Model.transform, HumanBodyBones.UpperChest);
        var leftShoulder = PersonaHumanoid.FindBone(Model.transform, HumanBodyBones.LeftShoulder);
        var rightShoulder = PersonaHumanoid.FindBone(Model.transform, HumanBodyBones.RightShoulder);
        if (chest != null && leftShoulder != null && rightShoulder != null)
            chest.rotation = Quaternion.FromToRotation(rightShoulder.position - leftShoulder.position, right) * chest.rotation;
        foreach (bool left in new[] { true, false })
        {
            var shoulder = left ? leftShoulder : rightShoulder;
            var arm = PersonaHumanoid.FindBone(Model.transform, left ? HumanBodyBones.LeftUpperArm : HumanBodyBones.RightUpperArm);
            var elbow = PersonaHumanoid.FindBone(Model.transform, left ? HumanBodyBones.LeftLowerArm : HumanBodyBones.RightLowerArm);
            var hand = PersonaHumanoid.FindBone(Model.transform, left ? HumanBodyBones.LeftHand : HumanBodyBones.RightHand);
            if (arm == null || elbow == null || hand == null) continue;
            Vector3 outward = left ? -right : right;
            if (shoulder != null)
                shoulder.rotation = Quaternion.FromToRotation(arm.position - shoulder.position, outward) * shoulder.rotation;
            arm.rotation = Quaternion.FromToRotation(elbow.position - arm.position, outward) * arm.rotation;
            elbow.rotation = Quaternion.FromToRotation(hand.position - elbow.position, outward) * elbow.rotation;
            var index = PersonaHumanoid.FindBone(Model.transform, left ? HumanBodyBones.LeftIndexProximal : HumanBodyBones.RightIndexProximal);
            var middle = PersonaHumanoid.FindBone(Model.transform, left ? HumanBodyBones.LeftMiddleProximal : HumanBodyBones.RightMiddleProximal);
            var little = PersonaHumanoid.FindBone(Model.transform, left ? HumanBodyBones.LeftLittleProximal : HumanBodyBones.RightLittleProximal);
            if (index == null || middle == null || little == null) continue;
            Vector3 length = middle.position - hand.position;
            Vector3 dorsal = Vector3.Cross(length, index.position - little.position) * (left ? 1f : -1f);
            hand.rotation = Quaternion.LookRotation(outward, Vector3.up) * Quaternion.Inverse(Quaternion.LookRotation(length, dorsal)) * hand.rotation;
            foreach (string finger in new[] { "Index", "Middle", "Ring", "Little", "Thumb" })
            {
                string prefix = left ? "Left" : "Right";
                var a = PersonaHumanoid.FindBone(Model.transform, (HumanBodyBones)Enum.Parse(typeof(HumanBodyBones), prefix + finger + "Proximal"));
                var b = PersonaHumanoid.FindBone(Model.transform, (HumanBodyBones)Enum.Parse(typeof(HumanBodyBones), prefix + finger + "Intermediate"));
                var c = PersonaHumanoid.FindBone(Model.transform, (HumanBodyBones)Enum.Parse(typeof(HumanBodyBones), prefix + finger + "Distal"));
                if (a == null || b == null || c == null) continue;
                float spread = finger == "Index" ? .08f : finger == "Ring" ? -.06f : finger == "Little" ? -.12f : finger == "Thumb" ? .8f : 0f;
                Vector3 direction = (outward * (finger == "Thumb" ? .6f : 1f) + facing * spread).normalized;
                a.rotation = Quaternion.FromToRotation(b.position - a.position, direction) * a.rotation;
                b.rotation = Quaternion.FromToRotation(c.position - b.position, direction) * b.rotation;
                c.rotation = Quaternion.FromToRotation(c.up, direction) * c.rotation;
            }
        }
    }

    public static Bounds Bounds()
    {
        if (Model == null) return new Bounds(Vector3.up, Vector3.one * 2f);
        var mesh = new Mesh();
        var result = new Bounds();
        bool first = true;
        try
        {
            foreach (var skin in Model.GetComponentsInChildren<SkinnedMeshRenderer>(true))
            {
                skin.BakeMesh(mesh);
                foreach (var vertex in mesh.vertices)
                {
                    Vector3 point = skin.transform.position + skin.transform.rotation * vertex;
                    if (first) { result = new Bounds(point, Vector3.zero); first = false; }
                    else result.Encapsulate(point);
                }
            }
            // 결합 머리는 본에 붙은 일반 MeshRenderer일 수도 있다. 몸체만 재면 얼굴이 잘린다.
            foreach (var renderer in Model.GetComponentsInChildren<Renderer>(true))
            {
                if (renderer is SkinnedMeshRenderer) continue;
                if (first) { result = renderer.bounds; first = false; }
                else result.Encapsulate(renderer.bounds);
            }
        }
        finally { Object.DestroyImmediate(mesh); }
        return first ? new Bounds(Model.transform.position + Vector3.up, Vector3.one * 2f) : result;
    }

    public static void Frame(float yaw, bool close = false)
    {
        if (Model == null) return;
        var bounds = Bounds();
        var head = PersonaHumanoid.FindBone(Model.transform, HumanBodyBones.Head);
        Vector3 center = close && head != null ? head.position : bounds.center;
        Vector3 facing = Vector3.ProjectOnPlane(PersonaHumanoid.MeasureFacing(Model.transform), Vector3.up).normalized;
        if (facing.sqrMagnitude < .1f) facing = Vector3.forward;
        Vector3 direction = Quaternion.AngleAxis(yaw, Vector3.up) * facing;
        var camera = SceneManager.GetActiveScene().GetRootGameObjects().SelectMany(r => r.GetComponentsInChildren<Camera>(true)).FirstOrDefault(c => c.name == CameraName);
        float size = close ? .42f : Mathf.Max(.7f, bounds.size.y * .6f, bounds.size.x * .55f);
        if (PersonaBodyPreviewCafe.IsActive)
        {
            PersonaBodyPreviewCafe.Frame(center, direction, size);
            Repaint();
            return;
        }
        if (camera != null)
        {
            camera.orthographicSize = size;
            camera.transform.SetPositionAndRotation(center + direction * 5f, Quaternion.LookRotation(-direction, Vector3.up));
        }
        SceneView.lastActiveSceneView?.LookAt(center, Quaternion.LookRotation(-direction, Vector3.up), size * 1.15f, true);
        Repaint();
    }

    public static void ApplyShapes()
    {
        if (Controller != null) Controller.ApplyNow();
        Place();
        Repaint();
        Changed?.Invoke();
    }
    static void Place()
    {
        if (Model == null) return;
        if (PersonaBodyPreviewCafe.IsActive) PersonaBodyPreviewCafe.Place();
        else
        {
            var bounds = Bounds();
            Model.transform.position -= new Vector3(bounds.center.x, bounds.min.y, bounds.center.z);
        }
    }
    public static void Repaint() { SceneView.RepaintAll(); EditorApplication.QueuePlayerLoopUpdate(); }
    public static void SetStatus(string message)
    {
        Status = message;
        var state = new PreviewState { status = Status, pose = CurrentPose.ToString(), busy = Busy, modelLoaded = Model != null, separateHead = Controller != null && Controller.HasSeparateHead,
            sourceAvailable = connection != null, shapes = Controller != null ? Controller.BoundShapeCount : 0 };
        string folder = Path.GetDirectoryName(CachePath);
        Directory.CreateDirectory(folder);
        File.WriteAllText(Path.Combine(folder, "state.json"), JsonUtility.ToJson(state, true));
        Changed?.Invoke();
    }
    static void Fail(Exception error)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(CachePath));
        File.WriteAllText(Path.Combine(Path.GetDirectoryName(CachePath), "error.private.txt"), error.GetType().Name + "\n" + error.StackTrace);
        string reason = error is InvalidOperationException ? error.Message : "모델 준비에 실패했습니다 (" + error.GetType().Name + "). 파일과 서버 연결을 확인해 주세요.";
        SetStatus(reason);
        Debug.LogWarning("[BodyShapePreview] " + reason);
    }

    public static void Clear() { generation++; ReleaseModel(); PersonaBodyPreviewCafe.Clear(); Changed?.Invoke(); }
    static void ReleaseModel()
    {
        if (graph.IsValid()) graph.Destroy();
        if (Model != null) Object.DestroyImmediate(Model);
        if (avatar != null) Object.DestroyImmediate(avatar);
        importer?.Dispose();
        importer = null; Model = null; Controller = null; Animator = null; avatar = null;
        restBones = null;
        handBindRotations.Clear();
        sourceMeshes.Clear();
    }

    static void EnsureAssets()
    {
        PersonaCollarTemplateBuilder.Ensure();
        if (!AssetDatabase.IsValidFolder("Assets/Settings")) AssetDatabase.CreateFolder("Assets", "Settings");
        if (Profile != null) return;
        var profile = ScriptableObject.CreateInstance<PersonaBodyShapeProfile>();
        var defaults = PersonaBodyShapeProfile.LoadDefault();
        if (defaults != null) EditorUtility.CopySerialized(defaults, profile);
        profile.name = "PersonaBodyShapePreview";
        AssetDatabase.CreateAsset(profile, ProfilePath);
        AssetDatabase.SaveAssetIfDirty(profile);
    }

    static void CreateScene()
    {
        var scene = EditorSceneManager.NewScene(NewSceneSetup.DefaultGameObjects, NewSceneMode.Single);
        var camera = Camera.main;
        camera.name = CameraName;
        camera.orthographic = true; camera.orthographicSize = 1.25f;
        camera.transform.SetPositionAndRotation(new Vector3(0, 1, 5), Quaternion.Euler(0, 180, 0));
        camera.clearFlags = CameraClearFlags.SolidColor;
        camera.backgroundColor = new Color(.19f, .21f, .23f);
        camera.nearClipPlane = .01f; camera.farClipPlane = 50f;
        RenderSettings.skybox = null;
        RenderSettings.ambientMode = AmbientMode.Trilight;
        RenderSettings.ambientSkyColor = new Color(.65f, .68f, .72f);
        RenderSettings.ambientEquatorColor = new Color(.48f, .5f, .54f);
        RenderSettings.ambientGroundColor = new Color(.3f, .31f, .33f);
        var key = Object.FindObjectOfType<Light>();
        key.name = "주 조명"; key.transform.rotation = Quaternion.Euler(35, -35, 0); key.intensity = 1.1f;
        var fill = new GameObject("보조 조명").AddComponent<Light>();
        fill.type = LightType.Directional; fill.intensity = .6f; fill.shadows = LightShadows.None;
        fill.transform.rotation = Quaternion.Euler(15, 145, 0);
        var floor = GameObject.CreatePrimitive(PrimitiveType.Plane);
        floor.name = "미리보기 바닥";
        const string floorPath = "Assets/Editor/PersonaBodyPreviewFloor.mat";
        var material = AssetDatabase.LoadAssetAtPath<Material>(floorPath);
        if (material == null)
        {
            material = new Material(Shader.Find("Universal Render Pipeline/Lit")) { color = new Color(.27f, .29f, .31f) };
            AssetDatabase.CreateAsset(material, floorPath);
        }
        floor.GetComponent<Renderer>().sharedMaterial = material;
        if (!EditorSceneManager.SaveScene(scene, ScenePath)) throw new IOException("미리보기 씬 저장 실패");
    }
}
