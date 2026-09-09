using System;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Threading.Tasks;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

// Local, Editor-only comparison through the production PersonaSpawner import path.
// Models and evidence stay in the ignored tools/_work directory.
[InitializeOnLoad]
public static class TripoAnimationTrial
{
    const string SourceKey = "TripoTrial.Source";
    const string SettingsKey = "TripoTrial.Settings";
    static Animation frozen, animated;
    static Report report;
    static string outputDirectory;

    [Serializable]
    public class Report
    {
        public string status, error, unityVersion, sourceScene, modelFile, clipName;
        public bool productionFreezePose, productionApplyAnimation;
        public float productionScaleMultiplier, productionTargetHeight, productionFreezeTime;
        public int bones, skinnedMeshes, triangles, clipCount, changedPoseBones;
        public float clipSeconds, maxVertexMovement, originalPoseHeight, displayScale;
        public float displayYaw = 90f;
        public bool legacyClip, shadersSupported, playbackAdvanced, frozenStayedStill;
        public int playbackFrames, animatedBonesMoved, frozenBonesMoved;
        public float playbackTimeBefore, playbackTimeAfter;
        public string[] shaders, textures;
    }

    static TripoAnimationTrial()
    {
        EditorApplication.playModeStateChanged += OnPlayState;
    }

    [MenuItem("Tools/Tripo/Inspect local rig")]
    public static void InspectRig() { TripoModelTestScene.Open(); }

    [MenuItem("Tools/Tripo/Trial/Run latest local model")]
    public static void RunLatest()
    {
        if (EditorApplication.isPlayingOrWillChangePlaymode)
        {
            Debug.LogWarning("Stop Play Mode before starting the production-loader comparison.");
            return;
        }
        var source = TripoModelTestScene.ModelDirectories()
            .Select(d => Path.Combine(d, "animated.glb")).FirstOrDefault(File.Exists);
        if (source == null) throw new FileNotFoundException("No local animated.glb trial result was found.");
        var production = UnityEngine.Object.FindObjectOfType<PersonaSpawner>();
        report = new Report { sourceScene = EditorSceneManager.GetActiveScene().path, unityVersion = Application.unityVersion };
        if (production != null)
        {
            report.productionFreezePose = production.freezePose;
            report.productionApplyAnimation = production.applyPoseAnimation;
            report.productionScaleMultiplier = production.scaleMultiplier;
            report.productionTargetHeight = production.targetHeightMeters;
            report.productionFreezeTime = production.freezeTimeSec;
        }
        if (!TripoModelTestScene.OpenScene(true)) return;
        SessionState.SetString(SettingsKey, JsonUtility.ToJson(report));
        SessionState.SetString(SourceKey, source);
        EditorApplication.isPlaying = true;
    }

    static void OnPlayState(PlayModeStateChange state)
    {
        if (state == PlayModeStateChange.EnteredPlayMode)
        {
            var source = SessionState.GetString(SourceKey, "");
            SessionState.EraseString(SourceKey);
            if (!string.IsNullOrEmpty(source) && TripoModelTestScene.IsOpen && TripoModelTestScene.IsComparison)
                Run(source);
        }
        if (state == PlayModeStateChange.EnteredEditMode)
        {
            SessionState.EraseString(SourceKey);
            SessionState.EraseString(SettingsKey);
        }
    }

    static async void Run(string source)
    {
        outputDirectory = Path.GetDirectoryName(source);
        report = JsonUtility.FromJson<Report>(SessionState.GetString(SettingsKey, "{}"));
        report.modelFile = Path.GetFileName(source);
        report.status = "loading";
        SaveReport();
        try
        {
            Application.runInBackground = true;
            var camera = Camera.main;
            camera.transform.position = new Vector3(0, 1.8f, 6f);
            camera.transform.LookAt(new Vector3(0, 1.1f, 0));
            camera.orthographicSize = 1.65f;
            var bytes = File.ReadAllBytes(source);
            var left = await Spawn(bytes, true, "Frozen");
            var right = await Spawn(bytes, false, "Animated");
            frozen = left.GetComponentInChildren<Animation>();
            animated = right.GetComponentInChildren<Animation>();
            if (frozen == null || animated == null || animated.clip == null)
                throw new InvalidDataException("PersonaSpawner did not produce an Animation with a clip.");
            var skins = right.GetComponentsInChildren<SkinnedMeshRenderer>();
            if (skins.Length == 0) throw new InvalidDataException("No SkinnedMeshRenderer was imported.");
            var bones = skins.SelectMany(s => s.bones).Distinct().ToArray();
            report.bones = bones.Length;
            report.skinnedMeshes = skins.Length;
            report.triangles = skins.Sum(s => s.sharedMesh.triangles.Length / 3);
            report.clipCount = animated.GetClipCount();
            report.clipName = animated.clip.name;
            report.clipSeconds = animated.clip.length;
            report.legacyClip = animated.clip.legacy;
            var materials = skins.SelectMany(s => s.sharedMaterials).Distinct().ToArray();
            report.shaders = materials.Select(m => m.shader.name).ToArray();
            report.shadersSupported = materials.All(m => m.shader.isSupported);
            report.textures = materials.SelectMany(m => m.GetTexturePropertyNames()
                .Select(p => m.GetTexture(p))).Where(t => t != null).Distinct()
                .Select(t => t.name + " " + t.width + "x" + t.height +
                    (t is Texture2D ? " mipLevels=" + ((Texture2D)t).mipmapCount : "")).ToArray();

            animated.enabled = false;
            animated.clip.SampleAnimation(animated.gameObject, 0);
            var atStart = bones.Select(b => b.localRotation).ToArray();
            var meshStart = new Mesh();
            var meshMiddle = new Mesh();
            try
            {
                skins[0].BakeMesh(meshStart);
                animated.clip.SampleAnimation(animated.gameObject, report.clipSeconds / 2);
                skins[0].BakeMesh(meshMiddle);
                report.changedPoseBones = bones.Where((b, i) => Quaternion.Angle(atStart[i], b.localRotation) > .05f).Count();
                var a = meshStart.vertices;
                var b = meshMiddle.vertices;
                report.maxVertexMovement = a.Select((v, i) => Vector3.Distance(v, b[i])).Max();
                animated.clip.SampleAnimation(animated.gameObject, 0);

                // Both copies receive the same display scale, measured from a baked pose.
                report.originalPoseHeight = meshStart.bounds.size.y * skins[0].transform.lossyScale.y;
                report.displayScale = 2f / report.originalPoseHeight;
                foreach (var root in new[] { left, right })
                {
                    root.transform.localScale *= report.displayScale;
                    root.transform.rotation = Quaternion.Euler(0, report.displayYaw, 0);
                }
                var min = skins[0].transform.TransformPoint(meshStart.bounds.min).y;
                left.transform.position = new Vector3(-1.15f, -min, 0);
                right.transform.position = new Vector3(1.15f, -min, 0);
            }
            finally
            {
                UnityEngine.Object.Destroy(meshStart);
                UnityEngine.Object.Destroy(meshMiddle);
            }
            var frozenBones = left.GetComponentsInChildren<SkinnedMeshRenderer>()
                .SelectMany(s => s.bones).Distinct().ToArray();
            var still = frozenBones.Select(b => b.localRotation).ToArray();
            Play();
            var moving = bones.Select(b => b.localRotation).ToArray();
            report.playbackTimeBefore = animated[animated.clip.name].time;
            var frame = Time.frameCount;
            await Task.Delay(2200);
            if (!EditorApplication.isPlaying || animated == null) return;
            report.playbackFrames = Time.frameCount - frame;
            report.playbackTimeAfter = animated[animated.clip.name].time;
            report.animatedBonesMoved = bones.Where((b, i) => Quaternion.Angle(moving[i], b.localRotation) > .05f).Count();
            report.frozenBonesMoved = frozenBones.Where((b, i) => Quaternion.Angle(still[i], b.localRotation) > .05f).Count();
            report.playbackAdvanced = animated.isPlaying && report.playbackFrames > 0 &&
                report.playbackTimeAfter > report.playbackTimeBefore && report.animatedBonesMoved > 0;
            report.frozenStayedStill = !frozen.enabled && report.frozenBonesMoved == 0;
            report.status = report.playbackAdvanced && report.frozenStayedStill &&
                report.maxVertexMovement > .0001f && report.shadersSupported ? "playback_verified" : "playback_failed";
            SaveReport();
            Debug.Log("[TripoTrial] " + report.status + ": " + report.bones + " bones, " +
                report.clipSeconds.ToString("F2") + "s; playback check only, visual quality requires inspection.");
        }
        catch (Exception exception)
        {
            report.status = "failed";
            report.error = exception.ToString();
            SaveReport();
            Debug.LogException(exception);
        }
    }

    static async Task<GameObject> Spawn(byte[] bytes, bool freeze, string name)
    {
        var host = new GameObject("Trial Spawner " + name);
        var spawner = host.AddComponent<PersonaSpawner>();
        spawner.enabled = false; // Start/WatchSession never runs; this is a local import trial.
        spawner.spawnPoint = host.transform;
        spawner.targetHeightMeters = 0;
        spawner.scaleMultiplier = 1;
        spawner.useSpawnRotation = false;
        spawner.freezePose = freeze;
        spawner.applyPoseAnimation = true;
        spawner.verboseLog = false;
        var flags = BindingFlags.NonPublic | BindingFlags.Instance;
        var method = typeof(PersonaSpawner).GetMethod("SpawnAsync", flags);
        if (method == null) throw new MissingMethodException("PersonaSpawner.SpawnAsync");
        await (Task)method.Invoke(spawner, new object[] { "Trial_" + name, bytes });
        var root = (GameObject)typeof(PersonaSpawner).GetField("_spawnedInstance", flags).GetValue(spawner);
        if (root == null) throw new InvalidDataException("PersonaSpawner did not instantiate a model.");
        return root;
    }

    static void SaveReport()
    {
        File.WriteAllText(Path.Combine(outputDirectory, "unity_validation.json"), JsonUtility.ToJson(report, true));
    }

    [MenuItem("Tools/Tripo/Trial/Sample start")]
    public static void SampleStart() { Sample(0); }

    [MenuItem("Tools/Tripo/Trial/Sample middle")]
    public static void SampleMiddle() { if (animated != null) Sample(animated.clip.length / 2); }

    static void Sample(float time)
    {
        if (animated == null) return;
        animated.enabled = false;
        animated.clip.SampleAnimation(animated.gameObject, time);
        SceneView.RepaintAll();
    }

    [MenuItem("Tools/Tripo/Trial/Play animation")]
    public static void Play()
    {
        if (animated == null) return;
        animated.enabled = true;
        animated.wrapMode = WrapMode.Loop;
        animated.Play();
    }

    [MenuItem("Tools/Tripo/Trial/Stop playback")]
    public static void Restore()
    {
        if (EditorApplication.isPlaying) EditorApplication.isPlaying = false;
        else TripoModelTestScene.Open();
    }
}
