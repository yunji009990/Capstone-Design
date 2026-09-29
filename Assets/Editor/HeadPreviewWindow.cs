// HeadPreviewWindow.cs
// "다시, 봄" — 머리만 생성한 GLB 를 지금 열려 있는 씬에 띄워 본다.
//
// 왜 PersonaSpawner 로 못 보나
//   PersonaSpawner 는 전신을 전제로 한다. NormalizeHeight 가 바운즈 높이를
//   targetHeightMeters(기본 1.7m)에 맞추므로, 머리만 있는 GLB 를 주면 키 1.7m 짜리
//   머리가 바닥에 놓인다. 자세 애니메이션·호흡·시선도 전부 뼈를 찾는데 머리 GLB 에는
//   스켈레톤이 없다(skins 0).
//
// 왜 씬 파일을 안 건드리나
//   Scene_2.unity 는 팀이 같이 쓰는 파일이고 지금도 수정 중이다. 미리보기를 위해
//   거기에 오브젝트를 심으면 커밋에 섞인다. 올라가는 오브젝트에 DontSaveInEditor 를
//   달아 두어 씬을 저장해도 파일에 남지 않게 한다.
//
// 쓰는 법
//   Tools ▸ 다시봄 ▸ 머리 미리보기  →  GLB 를 고르고 「씬에 올리기」
//   카페 조명 아래에서 보려면 Scene_2 를 열어 둔 채로 쓴다. 조명이 얼굴을 얼마나
//   깎는지가 생성 품질만큼 크게 듣기 때문이다(환경광만 올려 +0.084).
using System.IO;
using System.Threading.Tasks;
using GLTFast;
using UnityEditor;
using UnityEngine;

public class HeadPreviewWindow : EditorWindow
{
    const string RootName = "__HeadPreview";

    [SerializeField] string glbPath = "tools/_work/head02/head.glb";
    [SerializeField] float headHeightMeters = 0.45f;
    [SerializeField] Vector3 placement = new Vector3(0f, 1.15f, 0f);
    [SerializeField] bool followSpawnPoint = true;
    [SerializeField] bool sanitize = true;
    [SerializeField] bool previewLight;
    // 합친 전신 캐릭터도 이 창으로 본다. 머리만 볼 때와 두 가지가 다르다 —
    // 크기를 건드리면 안 되고(이미 실제 키다), 중심이 아니라 발이 바닥에 와야 한다.
    [SerializeField] bool keepScale;
    [SerializeField] bool anchorBottom;

    bool _busy;
    string _message = "";
    string[] _found;
    string[] _labels;

    [MenuItem("Tools/다시봄/모델 미리보기")]
    static void Open() => GetWindow<HeadPreviewWindow>("모델 미리보기").minSize = new Vector2(430, 400);

    void OnEnable() => Refresh();

    /// <summary>tools/_work 아래의 head*.glb 를 새것부터 모은다.
    ///
    /// head.glb 만 찾으면 head_trim.py 로 아래를 잘라 낸 결과(head_fill.glb 등)가
    /// 목록에 안 잡혀서, 다듬은 것을 두고 원본을 계속 올리게 된다.</summary>
    void Refresh()
    {
        var work = new DirectoryInfo(Path.Combine(ProjectRoot(), "tools", "_work"));
        var hits = new System.Collections.Generic.List<FileInfo>();
        if (work.Exists)
        {
            // 머리(head*.glb)뿐 아니라 합친 결과(merged*.glb)와 몸통도 잡는다.
            hits.AddRange(work.GetFiles("*.glb"));
            foreach (var dir in work.GetDirectories())
                hits.AddRange(dir.GetFiles("*.glb"));
        }
        hits.Sort((a, b) => b.LastWriteTime.CompareTo(a.LastWriteTime));
        _found = new string[hits.Count];
        _labels = new string[hits.Count];
        for (int i = 0; i < hits.Count; i++)
        {
            _found[i] = Relative(hits[i].FullName);
            _labels[i] = $"{hits[i].Directory.Name}/{Path.GetFileNameWithoutExtension(hits[i].Name)}"
                       + $"  ({hits[i].LastWriteTime:MM-dd HH:mm}, "
                       + $"{hits[i].Length / (1024 * 1024)}MB)";
        }
        // 창에 저장된 경로가 더 이상 없으면 가장 새것으로 옮긴다.
        string full = Path.IsPathRooted(glbPath) ? glbPath
                    : Path.Combine(ProjectRoot(), glbPath ?? "");
        if (_found.Length > 0 && !File.Exists(full)) glbPath = _found[0];
    }

    static string Describe(string path)
    {
        if (string.IsNullOrWhiteSpace(path)) return "경로가 비어 있습니다";
        string full = Path.IsPathRooted(path) ? path : Path.Combine(ProjectRoot(), path);
        if (!File.Exists(full)) return "파일 없음: " + full;
        var info = new FileInfo(full);
        return $"{info.LastWriteTime:yyyy-MM-dd HH:mm}  ·  {info.Length / (1024 * 1024)}MB";
    }

    void OnGUI()
    {
        EditorGUILayout.LabelField("생성한 GLB 를 씬에 띄운다 (머리 · 몸통 · 합친 것)",
                                   EditorStyles.boldLabel);
        EditorGUILayout.HelpBox(
            "씬 파일에는 저장되지 않는다(DontSaveInEditor). 플레이를 눌러도 사라진다.",
            MessageType.None);
        EditorGUILayout.Space();

        // 찾은 모델을 목록으로 보여준다. 경로를 손으로 적게 두면 창에 저장된 예전 값이
        // 그대로 남아, 새로 뽑은 모델을 올린 줄 알고 옛 모델을 보게 된다(실제로 겪었다).
        using (new EditorGUILayout.HorizontalScope())
        {
            if (_found == null) Refresh();
            int current = System.Array.IndexOf(_found, glbPath);
            int picked = EditorGUILayout.Popup("찾은 모델", current, _labels);
            if (picked >= 0 && picked < _found.Length && picked != current)
                glbPath = _found[picked];
            if (GUILayout.Button("새로고침", GUILayout.Width(70))) Refresh();
        }

        using (new EditorGUILayout.HorizontalScope())
        {
            glbPath = EditorGUILayout.TextField("GLB 경로", glbPath);
            if (GUILayout.Button("…", GUILayout.Width(28)))
            {
                string chosen = EditorUtility.OpenFilePanel("머리 GLB", ProjectRoot(), "glb");
                if (!string.IsNullOrEmpty(chosen)) glbPath = Relative(chosen);
            }
        }
        // 언제 만들어진 파일인지 늘 보이게 둔다. "예전 게 올라왔다" 를 눈으로 잡는 유일한 방법이다.
        EditorGUILayout.LabelField(" ", Describe(glbPath), EditorStyles.miniLabel);

        keepScale = EditorGUILayout.Toggle("원본 크기 유지", keepScale);
        using (new EditorGUI.DisabledScope(keepScale))
            headHeightMeters = EditorGUILayout.Slider("높이 (m)", headHeightMeters, 0.1f, 2.2f);
        EditorGUILayout.LabelField(" ", keepScale
            ? "GLB 의 크기를 그대로 쓴다. 합친 전신 모델은 이미 실제 키라 이쪽이 맞다"
            : "머리카락까지 포함한 전체 높이. 사람 머리는 0.23m 남짓, 긴 머리면 0.4~0.5m",
            EditorStyles.miniLabel);

        anchorBottom = EditorGUILayout.Toggle("발을 바닥에", anchorBottom);
        EditorGUILayout.LabelField(" ", anchorBottom
            ? "모델의 맨 아래가 아래 좌표에 온다. 전신 캐릭터는 이쪽"
            : "모델의 중심이 아래 좌표에 온다. 머리만 볼 때는 이쪽",
            EditorStyles.miniLabel);

        followSpawnPoint = EditorGUILayout.Toggle("스폰포인트 기준", followSpawnPoint);
        placement = EditorGUILayout.Vector3Field(
            followSpawnPoint ? "스폰포인트 + 오프셋" : "월드 위치", placement);
        sanitize = EditorGUILayout.Toggle("머티리얼 보정", sanitize);
        EditorGUILayout.LabelField(" ", "metallic 0 · roughness 0.85 · emissive 제거. "
                                        + "PersonaSpawner 가 인물에 하는 것과 같게 맞춘다",
                                   EditorStyles.miniLabel);

        previewLight = EditorGUILayout.Toggle("미리보기 조명", previewLight);
        EditorGUILayout.LabelField(" ", "모델에만 닿는 중립 백색광을 함께 올린다. 씬 조명이 "
                                        + "어두워 모델 자체를 판단할 수 없을 때 켠다",
                                   EditorStyles.miniLabel);

        // 머리만 볼 때와 합친 전신을 볼 때는 설정 세 개가 통째로 달라진다. 매번 손으로
        // 맞추면 틀리기 쉬워 한 번에 바꾼다.
        using (new EditorGUILayout.HorizontalScope())
        {
            EditorGUILayout.LabelField("프리셋", GUILayout.Width(EditorGUIUtility.labelWidth - 4));
            if (GUILayout.Button("전신 (합친 모델)"))
            {
                keepScale = true;
                anchorBottom = true;
                placement = Vector3.zero;
            }
            if (GUILayout.Button("머리만"))
            {
                keepScale = false;
                anchorBottom = false;
                headHeightMeters = 0.45f;
                placement = new Vector3(0f, 1.15f, 0f);
            }
        }

        EditorGUILayout.Space();
        using (new EditorGUI.DisabledScope(_busy))
        using (new EditorGUILayout.HorizontalScope())
        {
            if (GUILayout.Button("씬에 올리기", GUILayout.Height(28))) _ = Load();
            if (GUILayout.Button("지우기", GUILayout.Height(28), GUILayout.Width(80))) Clear();
            if (GUILayout.Button("선택", GUILayout.Height(28), GUILayout.Width(60))) Focus();
        }
        if (!string.IsNullOrEmpty(_message))
            EditorGUILayout.HelpBox(_message, MessageType.Info);
    }

    static string ProjectRoot() => Path.GetFullPath(Path.Combine(Application.dataPath, ".."));

    static string Relative(string absolute)
    {
        string root = ProjectRoot().Replace('\\', '/').TrimEnd('/') + "/";
        string full = Path.GetFullPath(absolute).Replace('\\', '/');
        return full.StartsWith(root) ? full.Substring(root.Length) : full;
    }

    static GameObject Existing() => GameObject.Find(RootName);

    void Clear()
    {
        var found = Existing();
        if (found != null) DestroyImmediate(found);
        _message = "지웠습니다.";
        Repaint();
    }

    void Focus()
    {
        var found = Existing();
        if (found == null) { _message = "씬에 올라와 있지 않습니다."; return; }
        Selection.activeGameObject = found;
        SceneView.lastActiveSceneView?.FrameSelected();
    }

    async Task Load()
    {
        _busy = true;
        _message = "읽는 중…";
        Repaint();
        try
        {
            string path = glbPath.Trim();
            if (!Path.IsPathRooted(path)) path = Path.GetFullPath(Path.Combine(ProjectRoot(), path));
            if (!File.Exists(path)) { _message = "파일이 없습니다: " + path; return; }

            var gltf = new GltfImport();
            var settings = new ImportSettings
            {
                GenerateMipMaps = true,
                AnisotropicFilterLevel = 8,
                AnimationMethod = AnimationMethod.Legacy,
            };
            if (!await gltf.Load(File.ReadAllBytes(path), null, settings))
            { _message = "GLB 해석 실패"; return; }

            Clear();
            var root = new GameObject(RootName);
            if (!await gltf.InstantiateMainSceneAsync(root.transform))
            { DestroyImmediate(root); _message = "장면 생성 실패"; return; }

            // 머리는 대개 원점 중심으로 나온다. 바운즈를 재서 높이를 맞추고,
            // 중심이 지정한 자리에 오게 옮긴다.
            Bounds bounds = Measure(root);
            float height = Mathf.Max(bounds.size.y, 1e-4f);
            if (!keepScale) root.transform.localScale = Vector3.one * (headHeightMeters / height);

            Vector3 origin = placement;
            _message = "";
            if (followSpawnPoint)
            {
                var spawner = FindObjectOfType<PersonaSpawner>();
                Transform anchor = spawner != null && spawner.spawnPoint != null
                    ? spawner.spawnPoint : null;
                if (anchor != null)
                {
                    origin = anchor.position + placement;
                    root.transform.rotation = anchor.rotation;
                }
                else _message = "스폰포인트를 못 찾아 월드 좌표로 놓았습니다. ";
            }
            // 스케일을 먼저 건 뒤에 다시 재야 자리가 맞는다.
            Bounds scaled = Measure(root);
            Vector3 anchor = anchorBottom
                ? new Vector3(scaled.center.x, scaled.min.y, scaled.center.z)
                : scaled.center;
            root.transform.position += origin - anchor;

            if (sanitize) Sanitize(root);
            if (previewLight) AddPreviewLight(root, origin);
            foreach (var t in root.GetComponentsInChildren<Transform>(true))
                t.gameObject.hideFlags = HideFlags.DontSaveInEditor;

            Selection.activeGameObject = root;
            SceneView.lastActiveSceneView?.FrameSelected();
            // 어떤 파일을 올렸는지 이름과 시각까지 적는다. 폴더 이름만으로는 구분이 안 된다.
            _message += $"올렸습니다 — {glbPath}\n{Describe(glbPath)}\n"
                      + $"원본 높이 {height:F3} → {headHeightMeters:F2}m, 위치 {origin}";
        }
        finally
        {
            _busy = false;
            Repaint();
        }
    }

    // 씬 조명이 얼굴을 얼마나 깎는지와, 모델 자체가 어떤지는 다른 질문이다. 씬을
    // 건드리지 않고 후자만 보기 위한 임시 광원 — Range 를 좁혀 머리 주변만 비춘다.
    static void AddPreviewLight(GameObject root, Vector3 origin)
    {
        var holder = new GameObject("PreviewLight");
        holder.transform.SetParent(root.transform, false);
        holder.transform.position = origin + new Vector3(0.25f, 0.35f, 0.6f);
        var light = holder.AddComponent<Light>();
        light.type = LightType.Point;
        light.color = Color.white;
        light.intensity = 2.5f;
        // 점광원의 Range 는 부모의 스케일을 탄다. 루트는 이미 머리 크기에 맞춰
        // 줄여 둔 상태라, 나눠 주지 않으면 빛이 얼굴에도 못 닿는다.
        light.range = 2.5f / Mathf.Max(Mathf.Abs(root.transform.lossyScale.x), 1e-4f);
        light.shadows = LightShadows.None;
    }

    static Bounds Measure(GameObject root)
    {
        var renderers = root.GetComponentsInChildren<Renderer>(true);
        if (renderers.Length == 0) return new Bounds(root.transform.position, Vector3.one);
        Bounds bounds = renderers[0].bounds;
        for (int i = 1; i < renderers.Length; i++) bounds.Encapsulate(renderers[i].bounds);
        return bounds;
    }

    // glTFast 가 만드는 머티리얼은 glTF 이름(metallicFactor …)을 쓰고, 빌트인·URP Lit 으로
    // 떨어지면 표준 이름(_Metallic …)을 쓴다. 둘 다 짚어야 한다. PersonaSpawner 가 이미
    // 찾아 둔 이름 그대로다.
    static readonly int P_Metallic      = Shader.PropertyToID("metallicFactor");
    static readonly int P_Roughness     = Shader.PropertyToID("roughnessFactor");
    static readonly int P_MetalRoughMap = Shader.PropertyToID("metallicRoughnessTexture");
    static readonly int P_NormalScale   = Shader.PropertyToID("normalTexture_scale");
    static readonly int P_OcclusionStr  = Shader.PropertyToID("occlusionTexture_strength");
    static readonly int P_Emissive      = Shader.PropertyToID("emissiveFactor");
    static readonly int P_MetallicStd   = Shader.PropertyToID("_Metallic");
    static readonly int P_SmoothnessStd = Shader.PropertyToID("_Smoothness");
    static readonly int P_GlossinessStd = Shader.PropertyToID("_Glossiness");
    static readonly int P_MetalMapStd   = Shader.PropertyToID("_MetallicGlossMap");
    static readonly int P_BumpScaleStd  = Shader.PropertyToID("_BumpScale");
    static readonly int P_OcclusionStd  = Shader.PropertyToID("_OcclusionStrength");
    static readonly int P_EmissionStd   = Shader.PropertyToID("_EmissionColor");

    // PersonaSpawner.Sanitize 와 같은 처리. 조명 아래 비교가 목적이라 인물에 적용되는
    // 것과 같은 조건이어야 한다. Scene_2 의 인스펙터 값과 맞춰 둔다.
    const float Metallic = 0f, Roughness = 0.85f, NormalStrength = 0.5f, Occlusion = 0.3f;

    static void Sanitize(GameObject root)
    {
        var seen = new System.Collections.Generic.HashSet<Material>();
        foreach (var r in root.GetComponentsInChildren<Renderer>(true))
            foreach (var m in r.sharedMaterials)
            {
                if (m == null || !seen.Add(m)) continue;
                // 어떤 셰이더에 무엇이 먹었는지 남긴다. 예전에 metallicFactor 를 못 찾아
                // 금속성 1.0 이 그대로 남는 바람에 모델이 새까맣게 나왔는데, 로그가
                // 없어서 머티리얼 문제인지 조명 문제인지 가릴 수가 없었다.
                Debug.Log($"[HeadPreview] 머티리얼 '{m.name}' 셰이더 '{m.shader.name}' · " +
                          $"metallicFactor={m.HasProperty(P_Metallic)} " +
                          $"_Metallic={m.HasProperty(P_MetallicStd)} " +
                          $"metallicRoughnessTexture={m.HasProperty(P_MetalRoughMap)}");

                // 맵을 먼저 뗀다. 안 떼면 최종값이 factor × 맵 이라 아래 값이 안 먹는다.
                // 반대로 맵만 떼고 factor 를 안 건드리면 Tripo 의 metallicFactor=1.0 이
                // 그대로 남아 완전 금속이 된다 — 반사할 환경이 없으면 새까맣게 나온다.
                if (m.HasProperty(P_MetalRoughMap)) m.SetTexture(P_MetalRoughMap, null);
                if (m.HasProperty(P_MetalMapStd))
                {
                    m.SetTexture(P_MetalMapStd, null);
                    m.DisableKeyword("_METALLICSPECGLOSSMAP");
                }

                if (m.HasProperty(P_Metallic))  m.SetFloat(P_Metallic, Metallic);
                if (m.HasProperty(P_Roughness)) m.SetFloat(P_Roughness, Roughness);
                // 빌트인·URP Lit 은 거칠기가 아니라 매끄러움으로 받는다.
                float smoothness = 1f - Roughness;
                if (m.HasProperty(P_MetallicStd))   m.SetFloat(P_MetallicStd, Metallic);
                if (m.HasProperty(P_SmoothnessStd)) m.SetFloat(P_SmoothnessStd, smoothness);
                if (m.HasProperty(P_GlossinessStd)) m.SetFloat(P_GlossinessStd, smoothness);

                if (m.HasProperty(P_NormalScale))  m.SetFloat(P_NormalScale, NormalStrength);
                if (m.HasProperty(P_BumpScaleStd)) m.SetFloat(P_BumpScaleStd, NormalStrength);
                if (m.HasProperty(P_OcclusionStr)) m.SetFloat(P_OcclusionStr, Occlusion);
                if (m.HasProperty(P_OcclusionStd)) m.SetFloat(P_OcclusionStd, Occlusion);

                if (m.HasProperty(P_Emissive))    m.SetColor(P_Emissive, Color.black);
                if (m.HasProperty(P_EmissionStd)) m.SetColor(P_EmissionStd, Color.black);
                m.DisableKeyword("_EMISSION");
            }
    }
}
