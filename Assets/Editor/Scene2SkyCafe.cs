// 기존 정원을 보관한 채 360도 하늘 카페를 미리 보고 원래 배경으로 전환한다.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using Newtonsoft.Json;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.Rendering.Universal;
using UnityEngine.SceneManagement;
using Object = UnityEngine.Object;

public static partial class Scene2SkyCafe
{
    internal const string Folder = "Assets/Graphics/Scene2/SkyCafe";
    const string RootName = "하늘 위 카페";
    const string UndoName = "하늘 카페 배경 전환";
    static string Work => Path.Combine(Path.GetDirectoryName(Application.dataPath), "tools/_work/sky_cafe_20261022");
    static readonly Vector3 Center = new Vector3(-1.4f, -.12f, -1.5f);

    public static void Apply()
    {
        var scene = Scene2GraphicsCapture.RequireScene();
        if (Lightmapping.isRunning) throw new InvalidOperationException("조명 베이크 중에는 전환할 수 없습니다.");
        var state = FindState();
        if (state == null)
        {
            Backup(scene);
            CaptureViews("garden");
            WriteReport("before", new { protectedDigest = ProtectedDigest(), glassDigest = GlassDigest() });
            var root = new GameObject(RootName);
            root.transform.SetParent(Scene2GraphicsCapture.SceneObject("04_카페 배경").transform, false);
            state = root.AddComponent<CafeSkyThemeState>();
            RememberOriginal(state);
            Build(state.transform);
            Undo.RegisterCreatedObjectUndo(root, UndoName);
        }
        SetTheme(state, true);
        AssetDatabase.SaveAssets();
        EditorSceneManager.MarkSceneDirty(scene);
        EditorSceneManager.SaveScene(scene);
        ViewCafe();
        CaptureViews("sky");
        Inspect();
        Debug.Log("[SkyCafe] 하늘 카페 적용 완료. 그래픽 > 배경 선택 > 기존 정원으로 돌아갈 수 있습니다.");
    }

    public static void Garden()
    {
        var scene = Scene2GraphicsCapture.RequireScene();
        var state = FindState();
        if (state == null || !state.captured) return;
        SetTheme(state, false);
        EditorSceneManager.MarkSceneDirty(scene);
        EditorSceneManager.SaveScene(scene);
        ViewCafe();
        Debug.Log("[SkyCafe] 기존 배경의 오브젝트·하늘·환경광을 복원했습니다.");
    }

    public static void ViewCafe()
    {
        Scene2GraphicsCapture.RequireScene();
        var view = SceneView.lastActiveSceneView;
        if (view == null) return;
        var rotation = Quaternion.Euler(2f, 0f, 0f);
        var eye = new Vector3(-2.85f, 1.45f, -.8f);
        view.sceneLighting = true;
        view.sceneViewState.showSkybox = true;
        view.sceneViewState.showImageEffects = true;
        view.drawGizmos = false;
        view.showGrid = false;
        view.orthographic = false;
        view.LookAtDirect(eye + rotation * Vector3.forward * 2, rotation, 1.4f);
        view.Repaint();
    }

    public static void Inspect()
    {
        var scene = Scene2GraphicsCapture.RequireScene();
        var state = FindState();
        var rs = state != null ? state.GetComponentsInChildren<Renderer>(true) : Array.Empty<Renderer>();
        var mats = rs.SelectMany(r => r.sharedMaterials).Where(m => m != null).Distinct().ToArray();
        var skyMaterials = RenderSettings.skybox != null ? new[] { RenderSettings.skybox } : Array.Empty<Material>();
        var errors = mats.Concat(skyMaterials).Distinct().Where(m => !m.shader.isSupported || ShaderUtil.ShaderHasError(m.shader))
            .Select(m => m.name).ToArray();
        var report = new
        {
            scene = scene.path, skyActive = state != null && state.skyActive,
            appearance = state != null ? state.appearance.ToString() : null,
            rendererCount = rs.Length, materialCount = mats.Length,
            meshTriangles = rs.OfType<MeshRenderer>().Sum(r => r.TryGetComponent<MeshFilter>(out var f) && f.sharedMesh != null ? f.sharedMesh.triangles.Length / 3 : 0),
            unsupportedOrErrorMaterials = errors,
            originalObjects = state != null ? state.originalObjects.Select(o => new { name = o.target != null ? o.target.name : "missing", o.active, now = o.target != null && o.target.activeSelf }).ToArray() : null,
            hiddenGrounds = state != null ? state.originalRenderers.Select(o => new { name = o.target != null ? o.target.name : "missing", o.enabled, now = o.target != null && o.target.enabled }).ToArray() : null,
            protectedDigest = ProtectedDigest(), glassDigest = GlassDigest(),
            lightmaps = LightmapSettings.lightmaps.Length,
            addedBehaviourTypes = state != null ? state.GetComponentsInChildren<MonoBehaviour>(true).Where(c => c != null).Select(c => c.GetType().Name).ToArray() : Array.Empty<string>(),
            skybox = AssetDatabase.GetAssetPath(RenderSettings.skybox)
        };
        WriteReport("validation", report);
        CaptureViews(state != null && state.skyActive ? "sky" : "garden_restored");
        if (errors.Length > 0) throw new InvalidOperationException("하늘 카페 셰이더 오류: " + string.Join(", ", errors));
        Debug.Log("[SkyCafe] 촬영·셰이더·기하 검사 완료. HMD 성능·양안·플레이어 빌드는 별도 검사입니다.");
    }

    public static void ValidateSwitch()
    {
        Scene2GraphicsCapture.RequireScene();
        var state = FindState();
        if (state == null) throw new InvalidOperationException("하늘 카페를 먼저 적용하세요.");
        bool originalTheme = state.skyActive;
        string before = ProtectedDigest(), glass = GlassDigest();
        var beforeComponents = ProtectedComponents().ToDictionary(c => c, c => EditorJsonUtility.ToJson(c));
        string originalLayout = LayoutDigest(state.originalObjects.Select(o => o.target));
        bool restored = false, sky = false;
        try
        {
            SetTheme(state, false);
            restored = state.originalObjects.All(o => o.target != null && o.target.activeSelf == o.active) &&
                state.originalRenderers.All(o => o.target != null && o.target.enabled == o.enabled) &&
                RenderSettings.skybox == state.originalSky && RenderSettings.ambientMode == state.originalAmbientMode &&
                RenderSettings.ambientSkyColor == state.originalAmbientSky && RenderSettings.ambientEquatorColor == state.originalAmbientEquator &&
                RenderSettings.ambientGroundColor == state.originalAmbientGround && RenderSettings.fog == state.originalFog &&
                Mathf.Approximately(RenderSettings.ambientIntensity, state.originalAmbientIntensity);
            SetTheme(state, true);
            sky = state.originalObjects.All(o => o.target != null && !o.target.activeSelf) &&
                state.originalRenderers.All(o => o.target != null && !o.target.enabled) && state.gameObject.activeSelf;
        }
        finally { SetTheme(state, originalTheme); }
        bool protectedMatch = before == ProtectedDigest(), glassMatch = glass == GlassDigest();
        bool layoutMatch = originalLayout == LayoutDigest(state.originalObjects.Select(o => o.target));
        var protectedChanges = beforeComponents.Where(p => p.Key == null || p.Value != EditorJsonUtility.ToJson(p.Key)).Select(p => new
        {
            component = p.Key != null ? p.Key.GetType().FullName : "missing",
            fields = p.Key == null ? Array.Empty<string>() : Newtonsoft.Json.Linq.JObject.Parse(p.Value).Properties()
                .Where(v => !Newtonsoft.Json.Linq.JToken.DeepEquals(v.Value, Newtonsoft.Json.Linq.JObject.Parse(EditorJsonUtility.ToJson(p.Key))[v.Name]))
                .Select(v => v.Name).ToArray()
        }).ToArray();
        WriteReport("switch_validation", new { restored, sky, protectedMatch, glassMatch, layoutMatch, beforeDigest = before, afterDigest = ProtectedDigest(), protectedChanges });
        if (!restored || !sky || !protectedMatch || !glassMatch || !layoutMatch)
            throw new InvalidOperationException("하늘 카페 전환 보존 검사에 실패했습니다.");
        EditorSceneManager.SaveScene(SceneManager.GetActiveScene());
        Debug.Log("[SkyCafe] 정원 복원·하늘 전환·카페 배치·VR/대화 구성·유리 재질 보존 검사 통과.");
    }

    public static void Rebuild()
    {
        var scene = Scene2GraphicsCapture.RequireScene();
        var state = FindState();
        if (state == null) { Apply(); return; }
        foreach (Transform child in state.transform.Cast<Transform>().ToArray()) Undo.DestroyObjectImmediate(child.gameObject);
        Build(state.transform);
        foreach (Transform child in state.transform) Undo.RegisterCreatedObjectUndo(child.gameObject, UndoName);
        SetTheme(state, true);
        AssetDatabase.SaveAssets();
        EditorSceneManager.MarkSceneDirty(scene);
        EditorSceneManager.SaveScene(scene);
        Inspect();
    }

    static CafeSkyThemeState FindState() => Scene2GraphicsCapture.RequireScene().GetRootGameObjects()
        .SelectMany(r => r.GetComponentsInChildren<CafeSkyThemeState>(true)).FirstOrDefault();

    static void Backup(Scene scene)
    {
        Directory.CreateDirectory(Work);
        string destination = Path.Combine(Work, "Scene_2_before_" + DateTime.Now.ToString("yyyyMMdd_HHmmss") + ".unity");
        if (scene.isDirty)
        {
            if (!EditorSceneManager.SaveScene(scene, destination, true))
                throw new InvalidOperationException("현재 편집 상태 백업에 실패했습니다.");
        }
        else File.Copy(scene.path, destination, false);
    }

    static void RememberOriginal(CafeSkyThemeState state)
    {
        var garden = Scene2GraphicsCapture.SceneObject("카페 앞 정원");
        if (garden == null) throw new InvalidOperationException("기존 카페 정원을 찾지 못했습니다.");
        state.originalObjects = new[] { new CafeSkyThemeState.ObjectState { target = garden, active = garden.activeSelf } };
        // 거대한 외부 지면의 표시만 숨긴다. 실내 바닥과 기존 충돌/동선은 유지한다.
        state.originalRenderers = Scene2GraphicsCapture.CafeRenderers()
            .Where(r => r.bounds.size.y < 1 && Mathf.Max(r.bounds.size.x, r.bounds.size.z) > 20)
            .Select(r => new CafeSkyThemeState.RendererState { target = r, enabled = r.enabled }).ToArray();
        state.originalSky = RenderSettings.skybox;
        state.originalFog = RenderSettings.fog;
        state.originalAmbientMode = RenderSettings.ambientMode;
        state.originalAmbientSky = RenderSettings.ambientSkyColor;
        state.originalAmbientEquator = RenderSettings.ambientEquatorColor;
        state.originalAmbientGround = RenderSettings.ambientGroundColor;
        state.originalAmbientIntensity = RenderSettings.ambientIntensity;
        state.captured = true;
    }

    static void SetTheme(CafeSkyThemeState state, bool sky)
    {
        Undo.IncrementCurrentGroup();
        int group = Undo.GetCurrentGroup();
        Undo.SetCurrentGroupName(UndoName);
        Undo.RecordObjects(new Object[] { state, state.gameObject, Unsupported.GetRenderSettings() }, UndoName);
        foreach (var item in state.originalObjects)
        {
            if (item.target == null) continue;
            Undo.RecordObject(item.target, UndoName);
            item.target.SetActive(sky ? false : item.active);
        }
        foreach (var item in state.originalRenderers)
        {
            if (item.target == null) continue;
            Undo.RecordObject(item.target, UndoName);
            item.target.enabled = sky ? false : item.enabled;
            PrefabUtility.RecordPrefabInstancePropertyModifications(item.target);
        }
        state.gameObject.SetActive(sky);
        state.skyActive = sky;
        RenderSettings.fog = sky ? false : state.originalFog;
        if (sky) ApplyAppearance(state);
        else
        {
            RenderSettings.skybox = state.originalSky;
            RenderSettings.ambientMode = state.originalAmbientMode;
            RenderSettings.ambientSkyColor = state.originalAmbientSky;
            RenderSettings.ambientEquatorColor = state.originalAmbientEquator;
            RenderSettings.ambientGroundColor = state.originalAmbientGround;
            RenderSettings.ambientIntensity = state.originalAmbientIntensity;
        }
        EditorUtility.SetDirty(state);
        EditorSceneManager.MarkSceneDirty(state.gameObject.scene);
        Undo.CollapseUndoOperations(group);
        DynamicGI.UpdateEnvironment();
        SceneView.RepaintAll();
    }

    static void Build(Transform root)
    {
        ConfigureDepthSky();
        var stone = Material("TerraceStone", "AgainSpring/Sky Cafe Cloud");
        stone.SetColor("_LightColor", new Color(.24f,.30f,.43f));
        stone.SetColor("_ShadeColor", new Color(.07f,.11f,.20f));
        stone.SetColor("_HazeColor", new Color(.15f,.20f,.34f));
        stone.SetFloat("_Detail",.35f);
        EditorUtility.SetDirty(stone);
        var rock = Material("FloatingStone", "AgainSpring/Sky Cafe Cloud");
        rock.SetColor("_LightColor", new Color(.18f,.23f,.37f));
        rock.SetColor("_ShadeColor", new Color(.04f,.07f,.15f));
        rock.SetColor("_HazeColor", new Color(.12f,.16f,.28f));
        EditorUtility.SetDirty(rock);
        var gold = Lit("BrushedChampagne",new Color(.46f,.35f,.23f),.38f,.42f);
        var glow = Lit("LanternGlow",new Color(1,.77f,.47f),.2f);
        glow.EnableKeyword("_EMISSION");
        glow.SetColor("_EmissionColor",new Color(1.4f,.85f,.37f));
        EditorUtility.SetDirty(glow);

        var terrace = Child(root,"달빛 테라스");
        MeshObject(terrace,"곡선 석재 테라스",AssetMesh("Terrace",IslandMesh(8.4f,8,.4f,false)),stone,Center);
        MeshObject(terrace,"카페를 받치는 부유암",AssetMesh("CafeIsland",IslandMesh(8.5f,8.1f,7.2f,true)),rock,Center+Vector3.down*.4f);
        MeshObject(terrace,"테라스 가장자리 금속선",AssetMesh("TerraceTrim",EllipseTube(8.4f,8,.034f)),gold,Center+Vector3.up*.12f);
        var rails = new List<CombineInstance>();
        for(int i=0;i<52;i++)
        {
            float a=i*Mathf.PI*2/52;
            AddBox(rails,Center+new Vector3(Mathf.Cos(a)*8.33f,.46f,Mathf.Sin(a)*7.93f),new Vector3(.035f,.85f,.035f));
        }
        MeshObject(terrace,"가느다란 테라스 난간 기둥",AssetMesh("Railing",Combine(rails)),gold,Vector3.zero);
        MeshObject(terrace,"곡선 난간 손잡이",AssetMesh("Handrail",EllipseTube(8.33f,7.93f,.027f)),gold,Center+Vector3.up*.89f);

        BuildDepthClouds(root);
        var accents=Child(root,"테라스의 따뜻한 작은 빛");
        for(int i=0;i<3;i++)
        {
            var p=new Vector3(-6.4f+i*4.1f,0,3.8f+.3f*i);
            Primitive(accents,"등 받침 "+i,PrimitiveType.Cylinder,p+Vector3.up*.08f,new Vector3(.31f,.08f,.31f),stone);
            Primitive(accents,"등 기둥 "+i,PrimitiveType.Cylinder,p+Vector3.up*.65f,new Vector3(.035f,.59f,.035f),gold);
            Primitive(accents,"둥근 기억등 "+i,PrimitiveType.Sphere,p+Vector3.up*1.3f,Vector3.one*.22f,glow);
        }
        MeshObject(accents,"천천히 떠오르는 작은 빛",AssetMesh("Motes",MoteMesh()),Material("Motes","AgainSpring/Sky Cafe Motes"),Vector3.zero);
    }

    static Mesh IslandMesh(float rx, float rz, float depth, bool irregular)
    {
        const int segments = 96, rings = 7;
        var vertices = new List<Vector3>(); var indices = new List<int>();
        vertices.Add(Vector3.zero);
        for (int j = 0; j < rings; j++)
        {
            float t = j / (float)(rings - 1);
            float width = irregular ? (t < .20f ? 1.0f - t * .12f : Mathf.Lerp(.98f, .045f, Mathf.Pow((t-.20f)/.8f, .66f))) : 1 - .08f * t;
            for (int i = 0; i < segments; i++)
            {
                float a = i * Mathf.PI * 2 / segments;
                float variation = irregular ? 1 + Mathf.Sin(a * 5 + t * 2) * .11f + Mathf.Sin(a * 9 - t) * .065f : 1;
                float y = -depth * t;
                if (irregular) y += Mathf.Sin(a * 7 + t * 5) * depth * .045f * Mathf.Sin(t*Mathf.PI);
                vertices.Add(new Vector3(Mathf.Cos(a) * rx * width * variation, y, Mathf.Sin(a) * rz * width * variation));
            }
        }
        for (int i = 0; i < segments; i++)
        {
            int next = (i + 1) % segments;
            indices.Add(0); indices.Add(1 + next); indices.Add(1 + i);
            for (int j = 0; j < rings - 1; j++)
            {
                int a = 1 + j * segments + i, b = 1 + j * segments + next;
                int c = a + segments, d = b + segments;
                indices.Add(a); indices.Add(b); indices.Add(c);
                indices.Add(b); indices.Add(d); indices.Add(c);
            }
        }
        var mesh = new Mesh(); mesh.SetVertices(vertices); mesh.SetTriangles(indices, 0); mesh.RecalculateNormals(); mesh.RecalculateBounds(); return mesh;
    }

    static Mesh EllipseTube(float rx, float rz, float radius)
    {
        var vertices = new List<Vector3>(); var indices = new List<int>();
        const int segments = 128, sides = 8;
        for (int i = 0; i <= segments; i++)
        {
            float a = i * Mathf.PI * 2 / segments;
            Vector3 outward = new Vector3(Mathf.Cos(a) / rx, 0, Mathf.Sin(a) / rz).normalized;
            for (int j = 0; j < sides; j++)
            {
                float b = j * Mathf.PI * 2 / sides;
                vertices.Add(new Vector3(Mathf.Cos(a) * rx, 0, Mathf.Sin(a) * rz) + radius * (outward * Mathf.Cos(b) + Vector3.up * Mathf.Sin(b)));
                if (i == segments) continue;
                int p = i * sides + j, q = i * sides + (j + 1) % sides;
                indices.Add(p); indices.Add(q); indices.Add(p + sides);
                indices.Add(q); indices.Add(q + sides); indices.Add(p + sides);
            }
        }
        var mesh = new Mesh(); mesh.SetVertices(vertices); mesh.SetTriangles(indices, 0); mesh.RecalculateNormals(); mesh.RecalculateBounds(); return mesh;
    }

    static Mesh MoteMesh()
    {
        var vertices = new List<Vector3>(); var uv = new List<Vector2>(); var colors = new List<Color>(); var triangles = new List<int>();
        var random = new System.Random(42);
        for(int i=0;i<44;i++)
        {
            float a=(float)random.NextDouble()*Mathf.PI*2, r=8+(float)random.NextDouble()*17;
            Vector3 center=Center+new Vector3(Mathf.Cos(a)*r,.6f+(float)random.NextDouble()*4,Mathf.Sin(a)*r);
            float size=.045f+(float)random.NextDouble()*.04f;
            // 작은 교차 면만 사용한다. 매 프레임 카메라를 찾아 빌보드를 회전하지 않는다.
            for(int axis=0;axis<2;axis++)
            {
                Vector3 right=axis==0?Vector3.right:Vector3.forward;
                int k=vertices.Count;
                vertices.Add(center+(-right-Vector3.up)*size); vertices.Add(center+(right-Vector3.up)*size);
                vertices.Add(center+(right+Vector3.up)*size); vertices.Add(center+(-right+Vector3.up)*size);
                uv.Add(new Vector2(0,0));uv.Add(new Vector2(1,0));uv.Add(new Vector2(1,1));uv.Add(new Vector2(0,1));
                for(int v=0;v<4;v++)colors.Add(new Color(i/44f,0,0,1));
                triangles.Add(k);triangles.Add(k+1);triangles.Add(k+2);triangles.Add(k);triangles.Add(k+2);triangles.Add(k+3);
            }
        }
        var mesh=new Mesh();mesh.SetVertices(vertices);mesh.SetUVs(0,uv);mesh.SetColors(colors);mesh.SetTriangles(triangles,0);mesh.RecalculateBounds();return mesh;
    }

    static void AddBox(List<CombineInstance> parts, Vector3 p, Vector3 size)
    {
        parts.Add(new CombineInstance { mesh = Resources.GetBuiltinResource<Mesh>("Cube.fbx"), transform = Matrix4x4.TRS(p, Quaternion.identity, size) });
    }
    static Mesh Combine(List<CombineInstance> parts) { var mesh = new Mesh(); mesh.CombineMeshes(parts.ToArray()); mesh.RecalculateBounds(); return mesh; }
    static Transform Child(Transform parent, string name) { var go = new GameObject(name); go.transform.SetParent(parent,false); return go.transform; }
    static GameObject MeshObject(Transform parent, string name, Mesh mesh, Material mat, Vector3 position)
    {
        var go = new GameObject(name); go.transform.SetParent(parent,false); go.transform.localPosition=position;
        go.AddComponent<MeshFilter>().sharedMesh=mesh; var renderer=go.AddComponent<MeshRenderer>(); renderer.sharedMaterial=mat;
        renderer.shadowCastingMode=ShadowCastingMode.Off; renderer.receiveShadows=false;
        renderer.lightProbeUsage=LightProbeUsage.Off; renderer.reflectionProbeUsage=ReflectionProbeUsage.Off;
        return go;
    }
    static void Primitive(Transform parent, string name, PrimitiveType type, Vector3 p, Vector3 scale, Material mat)
    {
        var go=GameObject.CreatePrimitive(type); go.name=name; go.transform.SetParent(parent,false); go.transform.localPosition=p; go.transform.localScale=scale;
        Object.DestroyImmediate(go.GetComponent<Collider>()); go.GetComponent<Renderer>().sharedMaterial=mat;
        go.GetComponent<Renderer>().shadowCastingMode=ShadowCastingMode.Off;
    }
    static Material Material(string name, string shaderName)
    {
        var mat = AssetDatabase.LoadAssetAtPath<Material>(Folder+"/"+name+".mat");
        var shader = Shader.Find(shaderName);
        if(shader==null || !shader.isSupported)throw new InvalidOperationException("사용할 수 없는 셰이더: "+shaderName);
        if(mat==null){mat=new Material(shader){name=name};AssetDatabase.CreateAsset(mat,Folder+"/"+name+".mat");}
        else if(mat.shader!=shader){mat.shader=shader;EditorUtility.SetDirty(mat);}
        return mat;
    }
    static Material Lit(string name, Color color, float smoothness, float metallic=0)
    {
        var mat=Material(name,"Universal Render Pipeline/Lit");mat.SetColor("_BaseColor",color);mat.SetFloat("_Smoothness",smoothness);mat.SetFloat("_Metallic",metallic);EditorUtility.SetDirty(mat);return mat;
    }
    static Mesh AssetMesh(string name, Mesh mesh)
    {
        mesh.name=name; string path=Folder+"/"+name+".asset"; var existing=AssetDatabase.LoadAssetAtPath<Mesh>(path);
        if(existing==null)AssetDatabase.CreateAsset(mesh,path);
        else { EditorUtility.CopySerialized(mesh,existing);Object.DestroyImmediate(mesh);EditorUtility.SetDirty(existing);mesh=existing; }
        return mesh;
    }
    static string ProtectedDigest()
    {
        string serialized=string.Join("\n",ProtectedComponents().Select(c=>c.GetType().FullName+EditorJsonUtility.ToJson(c)));
        return Digest(serialized);
    }
    static IEnumerable<Component> ProtectedComponents() => Scene2GraphicsCapture.RequireScene().GetRootGameObjects()
        .Where(r => r.name.StartsWith("01_") || r.name.StartsWith("02_") || r.name.StartsWith("03_"))
        .SelectMany(r => r.GetComponentsInChildren<Component>(true)).Where(c => c != null);
    static string LayoutDigest(IEnumerable<GameObject> roots) => Digest(string.Join("\n",roots.Where(r=>r!=null).SelectMany(r=>r.GetComponentsInChildren<Transform>(true)).Select(t=>EditorJsonUtility.ToJson(t))));
    static string GlassDigest()
    {
        string[] names={"WindowGlass","OvalWindowGlass","ClearDoorGlass"};
        return Digest(string.Join("\n",names.Select(n=>File.ReadAllText("Assets/Graphics/Scene2/Materials/"+n+".mat",Encoding.UTF8))));
    }
    static string Digest(string value) { using(var sha=SHA256.Create())return BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(value))).Replace("-","").ToLowerInvariant(); }
    static void WriteReport(string name, object report) { Directory.CreateDirectory(Work);File.WriteAllText(Path.Combine(Work,name+".json"),JsonConvert.SerializeObject(report,Formatting.Indented),new UTF8Encoding(false)); }
    static void CaptureViews(string prefix)
    {
        Directory.CreateDirectory(Work);
        Capture(prefix+"_front",new Vector3(-2.85f,1.45f,-.8f),new Vector3(2,0,0));
        Capture(prefix+"_window",new Vector3(-.3f,1.55f,.2f),new Vector3(5,0,0));
        Capture(prefix+"_window_left",new Vector3(-.332f,1.55f,.2f),new Vector3(5,0,0));
        Capture(prefix+"_window_right",new Vector3(-.268f,1.55f,.2f),new Vector3(5,0,0));
        Capture(prefix+"_window_shift",new Vector3(-.8f,1.55f,.2f),new Vector3(5,0,0));
        Capture(prefix+"_oval",new Vector3(-2.5f,1.55f,-.76f),new Vector3(0,-90,0));
        Capture(prefix+"_oval_back",new Vector3(-2.5f,1.55f,-3.86f),new Vector3(0,-90,0));
        Capture(prefix+"_outside",new Vector3(19,9,24),new Vector3(14,218,0));
    }
    static void Capture(string name, Vector3 p, Vector3 rotation)
    {
        var go=new GameObject("__SkyCafeCapture"){hideFlags=HideFlags.HideAndDontSave};
        var camera=go.AddComponent<Camera>(); var source=Camera.main;
        if(source!=null)camera.CopyFrom(source);
        camera.enabled=false;camera.stereoTargetEye=StereoTargetEyeMask.None;camera.fieldOfView=70;camera.aspect=16f/9;
        camera.nearClipPlane=.05f;camera.farClipPlane=100;camera.clearFlags=CameraClearFlags.Skybox;
        camera.transform.SetPositionAndRotation(p,Quaternion.Euler(rotation));
        var data=camera.GetUniversalAdditionalCameraData();data.allowXRRendering=false;
        if(source!=null && source.TryGetComponent<UniversalAdditionalCameraData>(out var sourceData)){data.renderPostProcessing=sourceData.renderPostProcessing;data.volumeLayerMask=sourceData.volumeLayerMask;}
        var old=RenderTexture.active;var target=RenderTexture.GetTemporary(1280,720,24,RenderTextureFormat.ARGB32);Texture2D texture=null;
        try { camera.targetTexture=target;camera.Render();RenderTexture.active=target;texture=new Texture2D(1280,720,TextureFormat.RGB24,false);texture.ReadPixels(new Rect(0,0,1280,720),0,0);texture.Apply();File.WriteAllBytes(Path.Combine(Work,name+".png"),texture.EncodeToPNG()); }
        finally { RenderTexture.active=old;camera.targetTexture=null;if(texture!=null)Object.DestroyImmediate(texture);RenderTexture.ReleaseTemporary(target);Object.DestroyImmediate(go); }
    }
}
