using System;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.SceneManagement;

// The saved scene owns the camera/light/floor. Imported local models are disposable previews.
[InitializeOnLoad]
public static class TripoModelTestScene
{
    public const string ScenePath = "Assets/Scenes/Tripo_Model_Test.unity";
    public const string CameraName = "Tripo Model Test Camera";
    const string ComparisonKey = "TripoModelTest.ProductionComparison";
    static bool previewPending = true;

    public static bool IsOpen => SceneManager.GetActiveScene().path == ScenePath;
    public static bool IsComparison => SessionState.GetBool(ComparisonKey, false);
    public static string PreferenceKey => "TripoModelTest." + Application.dataPath;

    static TripoModelTestScene()
    {
        EditorSceneManager.sceneOpened += OnSceneOpened;
        EditorSceneManager.activeSceneChangedInEditMode += OnActiveSceneChanged;
        EditorApplication.playModeStateChanged += OnPlayState;
        EditorApplication.update += OpenPendingPreview;
        AssemblyReloadEvents.beforeAssemblyReload += TripoRigInspector.Suspend;
    }

    [MenuItem("Tools/Tripo/Open model test scene", priority = 0)]
    public static void Open() { OpenScene(false); }

    public static bool OpenScene(bool comparison)
    {
        if (EditorApplication.isPlayingOrWillChangePlaymode)
        {
            if (IsOpen && !comparison) TripoRigInspector.Inspect(null);
            else Debug.LogWarning("Stop Play Mode before opening the model test scene.");
            return false;
        }
        if (!IsOpen && !EditorSceneManager.SaveCurrentModifiedScenesIfUserWantsTo()) return false;
        TripoRigInspector.Suspend();
        SessionState.SetBool(ComparisonKey, comparison);
        // Retire return requests left by the old temporary-scene tester.
        SessionState.SetBool("TripoTrial.Restore", false);
        SessionState.EraseString("TripoTrial.PreviousScene");
        SessionState.EraseString("TripoTrial.Source");
        if (!File.Exists(ScenePath)) CreateScene();
        else if (!IsOpen) EditorSceneManager.OpenScene(ScenePath, OpenSceneMode.Single);
        previewPending = !comparison;
        return true;
    }

    public static string[] ModelDirectories()
    {
        var root = Path.GetFullPath(Path.Combine(Application.dataPath, "../tools/_work"));
        if (!Directory.Exists(root)) return Array.Empty<string>();
        return Directory.GetDirectories(root, "tripo_trial_*")
            .Where(IsAvailableModelDirectory)
            .OrderByDescending(d => Path.GetFileName(d), StringComparer.Ordinal).ToArray();
    }

    public static bool IsAvailableModelDirectory(string directory)
    {
        return Directory.Exists(directory) &&
            !File.Exists(Path.Combine(directory, ".exclude-from-model-test")) &&
            new[] { "generated.glb", "rigged.glb", "animated.glb", "animated_pack.glb" }
                .Any(f => File.Exists(Path.Combine(directory, f)));
    }

    static void OnSceneOpened(Scene scene, OpenSceneMode mode)
    {
        if (scene.path == ScenePath) previewPending = !IsComparison;
        else if (!IsOpen) TripoRigInspector.Suspend();
    }

    static void OnActiveSceneChanged(Scene previous, Scene current)
    {
        if (current.path == ScenePath) previewPending = !IsComparison;
        else TripoRigInspector.Suspend();
    }

    static void OnPlayState(PlayModeStateChange state)
    {
        if (state == PlayModeStateChange.ExitingEditMode || state == PlayModeStateChange.ExitingPlayMode)
            TripoRigInspector.Suspend();
        if (state == PlayModeStateChange.EnteredEditMode)
        {
            SessionState.SetBool(ComparisonKey, false);
            previewPending = IsOpen;
        }
        if (state == PlayModeStateChange.EnteredPlayMode) previewPending = IsOpen && !IsComparison;
    }

    static void OpenPendingPreview()
    {
        if (!previewPending || EditorApplication.isCompiling || EditorApplication.isUpdating ||
            (EditorApplication.isPlayingOrWillChangePlaymode && !EditorApplication.isPlaying)) return;
        previewPending = false;
        if (IsOpen && !IsComparison) TripoRigInspector.Inspect(null);
    }

    static void CreateScene()
    {
        // Called only once; future visits open the saved asset and preserve scene edits.
        var scene = EditorSceneManager.NewScene(NewSceneSetup.DefaultGameObjects, NewSceneMode.Single);
        var camera = Camera.main;
        camera.name = CameraName;
        camera.transform.position = new Vector3(0, 1.3f, 5f);
        camera.transform.LookAt(new Vector3(0, .9f, 0));
        camera.orthographic = true;
        camera.orthographicSize = 1.15f;
        camera.clearFlags = CameraClearFlags.SolidColor;
        camera.backgroundColor = new Color(.79f, .83f, .86f);
        camera.nearClipPlane = .01f;
        camera.farClipPlane = 100f;
        RenderSettings.skybox = null;
        RenderSettings.ambientMode = AmbientMode.Trilight;
        RenderSettings.ambientSkyColor = new Color(.8f, .83f, .9f);
        RenderSettings.ambientEquatorColor = new Color(.62f, .65f, .68f);
        RenderSettings.ambientGroundColor = new Color(.36f, .38f, .4f);
        var light = UnityEngine.Object.FindObjectOfType<Light>();
        light.transform.rotation = Quaternion.Euler(35, -30, 0);
        light.intensity = 1.2f;
        light.shadows = LightShadows.Soft;
        var floor = GameObject.CreatePrimitive(PrimitiveType.Plane);
        floor.name = "Model test floor";
        const string materialPath = "Assets/Scenes/Tripo_Model_Test_Floor.mat";
        var material = AssetDatabase.LoadAssetAtPath<Material>(materialPath);
        if (material == null)
        {
            var shader = Shader.Find("Universal Render Pipeline/Lit");
            if (shader == null) throw new InvalidOperationException("URP Lit shader was not found.");
            material = new Material(shader) { color = new Color(.68f, .73f, .77f) };
            AssetDatabase.CreateAsset(material, materialPath);
        }
        floor.GetComponent<Renderer>().sharedMaterial = material;
        if (!EditorSceneManager.SaveScene(scene, ScenePath)) throw new IOException("Could not save the model test scene.");
        Debug.Log("[TripoModelTest] Created " + ScenePath);
    }
}
