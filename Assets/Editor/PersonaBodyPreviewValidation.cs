using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using UnityEditor;
using UnityEditor.UIElements;
using UnityEngine;
using UnityEngine.UIElements;
using Object = UnityEngine.Object;

/// <summary>실제 미리보기의 6개 키·3개 자세에서 얼굴/손/본 보존과 원상 복귀를 검사한다.</summary>
public static class PersonaBodyPreviewValidation
{
    [Serializable] sealed class Report
    {
        public bool success, validHumanoid, profileRestored, sliderBindingPassed, proportionSliderBindingPassed, proportionsPassed, tPoseHandsPassed, headMotionPassed;
        public bool neutralHeadRigid, collarRestored, originalHeadSurfacePreserved;
        public bool settingsUnchanged;
        public int restoredCollarVertices;
        public float maximumNeutralHeadDeformation;
        public float maximumCollarSeamGap;
        public int collarSeamChecks;
        public int combinations, proportionCombinations, shapeCount, protectedVertices, boneCount;
        public float maximumMovement, maximumProtectedMovement, maximumResetError;
        public string error, status;
        public List<PoseReport> poses = new List<PoseReport>();
    }
    [Serializable] sealed class PoseReport { public string pose; public Vector3 head, hips, knee; public Bounds bounds; public bool animatorInitialized; }
    sealed class Snapshot { public Vector3[] vertices; public bool[] protect; }

    [MenuItem("Tools/다시봄/개발 도구/캐릭터 검사/얼굴·몸체 체형 미리보기")]
    public static async void Run()
    {
        if (PersonaBodyPreviewCafe.IsActive) { PersonaBodyPreviewCafeValidation.Run(); return; }
        var report = new Report { status = PersonaBodyPreviewScene.Status };
        var controller = PersonaBodyPreviewScene.Controller;
        var original = controller != null ? controller.Profile : null;
        string originalSettings = original != null ? JsonUtility.ToJson(original) : null;
        var originalPose = PersonaBodyPreviewScene.CurrentPose;
        PersonaBodyShapeProfile test = null;
        Slider testedSlider = null;
        var testedProportionSliders = new List<Slider>();
        try
        {
            Require(PersonaBodyPreviewScene.IsOpen && !PersonaBodyPreviewScene.Busy && controller != null,
                "미리보기 씬에서 모델 로딩을 끝낸 뒤 검사하세요.");
            var model = PersonaBodyPreviewScene.Model;
            report.shapeCount = controller.BoundShapeCount;
            report.validHumanoid = PersonaBodyPreviewScene.Animator.avatar.isValid && PersonaBodyPreviewScene.Animator.avatar.isHuman;
            Require(report.shapeCount == 6 && report.validHumanoid, "체형 키 또는 Humanoid 연결 오류");
            var bones = model.GetComponentsInChildren<Transform>(true);
            report.boneCount = bones.Length;
            test = ScriptableObject.CreateInstance<PersonaBodyShapeProfile>();
            test.hideFlags = HideFlags.HideAndDontSave;
            controller.Bind(test);
            foreach (var pose in new[] { PersonaBodyPreviewScene.Pose.T포즈, PersonaBodyPreviewScene.Pose.서있기, PersonaBodyPreviewScene.Pose.앉기 })
            {
                PersonaBodyShapeValidation.SetValues(test, -1, 0f);
                PersonaBodyPreviewScene.SetPose(pose);
                await Task.Delay(100);
                PersonaBodyPreviewScene.Frame(0f);
                var head = PersonaHumanoid.FindBone(model.transform, HumanBodyBones.Head);
                var hip = PersonaHumanoid.FindBone(model.transform, HumanBodyBones.Hips);
                var knee = PersonaHumanoid.FindBone(model.transform, HumanBodyBones.LeftLowerLeg);
                report.poses.Add(new PoseReport { pose = pose.ToString(), head = head.position, hips = hip.position,
                    knee = knee.position, bounds = PersonaBodyPreviewScene.Bounds(), animatorInitialized = PersonaBodyPreviewScene.Animator.isInitialized });
                var start = Capture(model);
                int protectedCount = start.Sum(s => s.protect.Count(v => v));
                Require(protectedCount > 0, "보호할 얼굴·손 정점을 구분하지 못했습니다.");
                report.protectedVertices = Math.Max(report.protectedVertices, protectedCount);
                CaptureImage(pose + ".private.png");
                var positions = bones.Select(b => b.localPosition).ToArray();
                var rotations = bones.Select(b => b.localRotation).ToArray();
                var scales = bones.Select(b => b.localScale).ToArray();
                for (int shape = 0; shape < 6; shape++)
                    foreach (float value in new[] { -1f, 1f })
                    {
                        PersonaBodyShapeValidation.SetValues(test, shape, value);
                        controller.ApplyNow();
                        var deformed = Capture(model);
                        float movement = Difference(start, deformed, false);
                        float protectedMovement = Difference(start, deformed, true);
                        Require(movement > .001f && movement < .25f, "체형 변형 범위 오류");
                        Require(protectedMovement < .00001f, "체형 키가 얼굴·손·소매 안감을 변형했습니다.");
                        report.maximumMovement = Mathf.Max(report.maximumMovement, movement);
                        report.maximumProtectedMovement = Mathf.Max(report.maximumProtectedMovement, protectedMovement);
                        if (pose == PersonaBodyPreviewScene.Pose.서있기 && shape == 0 && value < 0)
                            CaptureImage("상체폭_최소.private.png");
                        for (int i = 0; i < bones.Length; i++)
                        {
                            Require(Vector3.Distance(positions[i], bones[i].localPosition) < .00001f, "체형 키가 본 위치를 바꿨습니다.");
                            Require(Quaternion.Angle(rotations[i], bones[i].localRotation) < .05f, "체형 키가 본 회전을 바꿨습니다.");
                            Require(Vector3.Distance(scales[i], bones[i].localScale) < .00001f, "체형 키가 본 배율을 바꿨습니다.");
                        }
                        report.combinations++;
                    }
                PersonaBodyShapeValidation.SetValues(test, -1, 0f);
                controller.ApplyNow();
                report.maximumResetError = Mathf.Max(report.maximumResetError, Difference(start, Capture(model), false));
                Require(report.maximumResetError < .00001f, "0에서 원래 메시로 돌아오지 않았습니다.");
            }
            Require(Vector3.Distance(report.poses[1].head - report.poses[1].hips, report.poses[2].head - report.poses[2].hips) > .005f ||
                Mathf.Abs((report.poses[1].hips - report.poses[1].knee).y - (report.poses[2].hips - report.poses[2].knee).y) > .1f,
                "서있는 자세와 앉은 자세가 구분되지 않습니다.");
            ValidateProportions(model, controller, test, report);
            // 실제 UITK 슬라이더를 임시 설정에 연결해 저장된 사용자 설정을 건드리지 않고 연동을 확인한다.
            var window = Resources.FindObjectsOfTypeAll<PersonaBodyPreviewWindow>().FirstOrDefault();
            Require(window != null, "체형 조절창을 열어 주세요.");
            testedSlider = window.rootVisualElement.Query<Slider>().ToList()
                .First(s => s.bindingPath == PersonaBodyShapeProfileEditor.Fields[0]);
            testedSlider.Unbind();
            testedSlider.BindProperty(new SerializedObject(test).FindProperty(PersonaBodyShapeProfileEditor.Fields[0]));
            var beforeSlider = Capture(model);
            testedSlider.value = .6f;
            await Task.Delay(150);
            Require(Mathf.Abs(test.GetValue(0) - .6f) < .00001f, "슬라이더와 체형 설정의 연결 오류");
            controller.ApplyNow();
            Require(Difference(beforeSlider, Capture(model), false) > .001f, "슬라이더 변경이 모델에 반영되지 않았습니다.");
            report.sliderBindingPassed = true;
            for (int i = 0; i < PersonaBodyShapeProfileEditor.ProportionFields.Length; i++)
            {
                string field = PersonaBodyShapeProfileEditor.ProportionFields[i];
                var slider = window.rootVisualElement.Query<Slider>().ToList().First(s => s.bindingPath == field);
                testedProportionSliders.Add(slider);
                slider.Unbind();
                slider.BindProperty(new SerializedObject(test).FindProperty(field));
                float value = i == 0 ? .93f : i == 1 ? 1.12f : i == 2 ? .012f : -.008f;
                slider.value = value;
                await Task.Delay(100);
                Require(Mathf.Abs(new SerializedObject(test).FindProperty(field).floatValue - value) < 1e-5f, "크기·머리 슬라이더의 설정 연결 오류");
                controller.ApplyNow();
            }
            report.proportionSliderBindingPassed = true;
            report.success = true;
        }
        catch (Exception error) { report.error = error.Message; }
        finally
        {
            if (testedSlider != null && original != null)
            {
                testedSlider.Unbind();
                testedSlider.BindProperty(new SerializedObject(original).FindProperty(PersonaBodyShapeProfileEditor.Fields[0]));
            }
            if (original != null)
                for (int i = 0; i < testedProportionSliders.Count; i++)
                {
                    testedProportionSliders[i].Unbind();
                    testedProportionSliders[i].BindProperty(new SerializedObject(original).FindProperty(PersonaBodyShapeProfileEditor.ProportionFields[i]));
                }
            if (controller != null)
            {
                controller.Bind(original);
                PersonaBodyPreviewScene.SetPose(originalPose);
                report.profileRestored = controller.Profile == original;
            }
            if (test != null) Object.DestroyImmediate(test);
            report.settingsUnchanged = original == null || JsonUtility.ToJson(original) == originalSettings;
            if (!report.settingsUnchanged) { report.success = false; report.error = "검사 중 미리보기 설정이 변경됐습니다. 사용자 동시 편집 여부를 확인하세요."; }
            Directory.CreateDirectory("tools/_work/body_shape_preview");
            File.WriteAllText("tools/_work/body_shape_preview/validation.json", JsonUtility.ToJson(report, true));
            if (report.success) Debug.Log("[BodyShapePreviewValidation] 통과: 체형 " + report.combinations + "개 + 크기·머리 " + report.proportionCombinations + "개, T포즈 양손·본 보존·UI 연결");
            else Debug.LogWarning("[BodyShapePreviewValidation] " + report.error);
        }
    }

    static void ValidateProportions(GameObject model, PersonaBodyShapeController controller, PersonaBodyShapeProfile test, Report report)
    {
        Require(controller.HasSeparateHead, "분리된 생성 머리의 크기 키를 찾지 못했습니다.");
        var skins = model.GetComponentsInChildren<SkinnedMeshRenderer>(true);
        var head = skins.First(s => s.sharedMesh.GetBlendShapeIndex(PersonaModelProportions.HeadSizeShape) >= 0);
        var body = skins.First(s => s.sharedMesh.GetBlendShapeIndex(PersonaBodyShapeProfile.ShapeName(0)) >= 0);
        var mesh = head.sharedMesh;
        ValidateAttachment(head, body, report);
        var sourcePoints = mesh.vertices;
        int count = sourcePoints.Length;
        var delta = new Vector3[count];
        int pinned = 0;
        for (int shape = 0; shape < 3; shape++)
        {
            mesh.GetBlendShapeFrameVertices(shape, 0, delta, null, null);
            int fixedCount = delta.Count(d => d.sqrMagnitude < 1e-12f);
            pinned = shape == 0 ? fixedCount : Mathf.Min(pinned, fixedCount);
        }
        Require(pinned > 20, "목 아래쪽의 고정 정점이 없습니다.");
        foreach (var pose in new[] { PersonaBodyPreviewScene.Pose.T포즈, PersonaBodyPreviewScene.Pose.서있기, PersonaBodyPreviewScene.Pose.앉기 })
        {
            SetProportion(test, -1, 0);
            PersonaBodyPreviewScene.SetPose(pose);
            var transforms = model.GetComponentsInChildren<Transform>(true);
            var positions = transforms.Select(t => t.localPosition).ToArray();
            var rotations = transforms.Select(t => t.localRotation).ToArray();
            var scales = transforms.Select(t => t.localScale).ToArray();
            var baselineHead = Bake(head);
            var baselineBody = Bake(body);
            ValidateCollarSeam(body, report);
            for (int field = 0; field < 4; field++)
                foreach (float value in new[] { PersonaBodyShapeProfileEditor.ProportionMin[field], PersonaBodyShapeProfileEditor.ProportionMax[field] })
                {
                    SetProportion(test, field, value); controller.ApplyNow();
                    var changedHead = Bake(head);
                    Require(changedHead.All(v => !float.IsNaN(v.sqrMagnitude) && !float.IsInfinity(v.sqrMagnitude)), "머리 변형에 유효하지 않은 좌표가 있습니다.");
                    if (field == 0)
                        Require(Vector3.Distance(model.transform.localScale, scales[0] * value) < 1e-5f, "전체 배율 적용 오류");
                    else
                    {
                        Require(MaxDistance(baselineBody, Bake(body)) < 1e-5f, "머리 설정이 몸체 메시를 바꿨습니다.");
                        Require(MaxDistance(baselineHead, changedHead) > .002f, "머리 설정이 화면 메시를 바꾸지 않았습니다.");
                    }
                    for (int i = 0; i < transforms.Length; i++)
                    {
                        Require(Vector3.Distance(transforms[i].localPosition, positions[i]) < 1e-5f, "비율 조절이 본 위치를 바꿨습니다.");
                        Require(Quaternion.Angle(transforms[i].localRotation, rotations[i]) < .05f, "비율 조절이 본 회전을 바꿨습니다.");
                        if (transforms[i] != model.transform) Require(Vector3.Distance(transforms[i].localScale, scales[i]) < 1e-5f, "전체 배율 이외의 본 배율이 바뀌었습니다.");
                    }
                    report.proportionCombinations++;
                    if (pose == PersonaBodyPreviewScene.Pose.T포즈 && field == 1)
                    { PersonaBodyPreviewScene.Frame(90, true); CaptureImage(value < 1f ? "머리_최소_측면.private.png" : "머리_최대_측면.private.png"); }
                }
            SetProportion(test, -1, 0); controller.ApplyNow();
            Require(MaxDistance(sourcePoints, mesh.vertices) < 1e-7f, "머리 조절이 메시 원본 정점을 덮어썼습니다.");
            Require(MaxDistance(baselineHead, Bake(head)) < 1e-5f, "머리 조절 원상 복귀 오류");
            controller.Bind(test);
            Require(head.sharedMesh == mesh, "반복 연결에서 머리 복사본이 다시 생성됐습니다.");
        }
        PersonaBodyPreviewScene.SetPose(PersonaBodyPreviewScene.Pose.T포즈);
        foreach (bool left in new[] { true, false })
        {
            var arm = PersonaHumanoid.FindBone(model.transform, left ? HumanBodyBones.LeftUpperArm : HumanBodyBones.RightUpperArm);
            var elbow = PersonaHumanoid.FindBone(model.transform, left ? HumanBodyBones.LeftLowerArm : HumanBodyBones.RightLowerArm);
            var hand = PersonaHumanoid.FindBone(model.transform, left ? HumanBodyBones.LeftHand : HumanBodyBones.RightHand);
            Require(Mathf.Abs((elbow.position - arm.position).normalized.y) < .001f && Mathf.Abs((hand.position - elbow.position).normalized.y) < .001f, "T포즈 팔이 수평이 아닙니다.");
            foreach (string finger in new[] { "Index", "Middle", "Ring", "Little", "Thumb" })
            {
                string prefix = left ? "Left" : "Right";
                Transform a = PersonaHumanoid.FindBone(model.transform, (HumanBodyBones)Enum.Parse(typeof(HumanBodyBones), prefix + finger + "Proximal"));
                Transform b = PersonaHumanoid.FindBone(model.transform, (HumanBodyBones)Enum.Parse(typeof(HumanBodyBones), prefix + finger + "Intermediate"));
                Transform c = PersonaHumanoid.FindBone(model.transform, (HumanBodyBones)Enum.Parse(typeof(HumanBodyBones), prefix + finger + "Distal"));
                Require(Vector3.Angle(b.position - a.position, c.position - b.position) < .1f, "T포즈 손가락이 꺾였습니다.");
            }
        }
        report.tPoseHandsPassed = true;
        var headBone = PersonaHumanoid.FindBone(model.transform, HumanBodyBones.Head);
        Quaternion headRotation = headBone.rotation;
        var neutral = Bake(head);
        try
        {
            foreach (float yaw in new[] { -40f, 40f })
            {
                headBone.rotation = Quaternion.AngleAxis(yaw, Vector3.up) * headRotation;
                var turned = Bake(head);
                Require(turned.All(v => !float.IsNaN(v.sqrMagnitude) && !float.IsInfinity(v.sqrMagnitude)), "머리 회전 후 목 연결 좌표 오류");
                Require(MaxDistance(neutral, turned) > .01f, "머리 회전이 스킨에 반영되지 않았습니다.");
                ValidateCollarSeam(body, report);
                PersonaBodyPreviewScene.Frame(0, true);
                CaptureImage(yaw < 0f ? "머리_왼쪽회전.private.png" : "머리_오른쪽회전.private.png");
            }
        }
        finally { headBone.rotation = headRotation; }
        Require(MaxDistance(neutral, Bake(head)) < 1e-5f, "머리 회전 원상 복귀 오류");
        report.headMotionPassed = true;
        PersonaBodyPreviewScene.Frame(0, true); CaptureImage("목_연결_정면.private.png");
        PersonaBodyPreviewScene.Frame(90, true); CaptureImage("목_연결_측면.private.png");
        PersonaBodyPreviewScene.Frame(180, true); CaptureImage("목_연결_후면.private.png");
        foreach (bool left in new[] { true, false })
        {
            var hand = PersonaHumanoid.FindBone(model.transform, left ? HumanBodyBones.LeftHand : HumanBodyBones.RightHand);
            var camera = GameObject.Find(PersonaBodyPreviewScene.CameraName).GetComponent<Camera>();
            Vector3 front = PersonaHumanoid.MeasureFacing(model.transform).normalized;
            camera.orthographicSize = .18f;
            camera.transform.SetPositionAndRotation(hand.position + Vector3.up * 3, Quaternion.LookRotation(Vector3.down, front));
            CaptureImage(left ? "T포즈_왼손.private.png" : "T포즈_오른손.private.png");
        }
        PersonaBodyPreviewScene.Frame(0);
        report.proportionsPassed = true;
    }
    static void ValidateAttachment(SkinnedMeshRenderer head, SkinnedMeshRenderer body, Report report)
    {
        var source = PersonaBodyPreviewScene.OriginalMesh(head);
        Require(source != null && source.vertexCount == head.sharedMesh.vertexCount, "머리에 임의의 목 기하가 추가됐습니다.");
        var before = source.vertices; var after = head.sharedMesh.vertices;
        Vector3 offset = after[0] - before[0];
        for (int i = 0; i < before.Length; i++)
            report.maximumNeutralHeadDeformation = Mathf.Max(report.maximumNeutralHeadDeformation, (after[i] - before[i] - offset).magnitude);
        Require(report.maximumNeutralHeadDeformation < 1e-5f, "중립 결합이 얼굴·턱·목 형태를 늘렸습니다.");
        report.neutralHeadRigid = true;
        Require(source.uv.SequenceEqual(head.sharedMesh.uv) && source.normals.SequenceEqual(head.sharedMesh.normals) &&
            source.triangles.SequenceEqual(head.sharedMesh.triangles) && source.boneWeights.SequenceEqual(head.sharedMesh.boneWeights) &&
            head.sharedMaterials.Length == source.subMeshCount, "생성 머리의 UV·표면·가중치·재질 구성이 바뀌었습니다.");
        report.originalHeadSurfacePreserved = true;
        var sourceBody = PersonaBodyPreviewScene.OriginalMesh(body);
        var points = body.sharedMesh.vertices; var bodyOriginal = sourceBody.vertices;
        report.restoredCollarVertices = points.Length - bodyOriginal.Length;
        Require(report.restoredCollarVertices > 0 && body.sharedMesh.bounds.max.y > sourceBody.bounds.max.y + sourceBody.bounds.size.y * .003f,
            "잘린 옷깃의 원래 상단이 복원되지 않았습니다.");
        for (int i = 0; i < bodyOriginal.Length; i++) Require((points[i] - bodyOriginal[i]).sqrMagnitude < 1e-12f, "옷깃 복원이 기존 몸체·손의 정점을 바꿨습니다.");
        Require(sourceBody.blendShapeCount == body.sharedMesh.blendShapeCount, "옷깃 복원이 체형 키 개수를 바꿨습니다.");
        report.collarRestored = true;
    }
    static void SetProportion(PersonaBodyShapeProfile profile, int field, float value)
    {
        var settings = new SerializedObject(profile);
        for (int i = 0; i < PersonaBodyShapeProfileEditor.ProportionFields.Length; i++)
            settings.FindProperty(PersonaBodyShapeProfileEditor.ProportionFields[i]).floatValue = i == field ? value : PersonaBodyShapeProfileEditor.ProportionDefaults[i];
        settings.ApplyModifiedPropertiesWithoutUndo();
    }
    static void ValidateCollarSeam(SkinnedMeshRenderer body, Report report)
    {
        var original = PersonaBodyPreviewScene.OriginalMesh(body);
        var raw = body.sharedMesh.vertices; var source = original.vertices;
        float cut = original.bounds.max.y;
        float tolerance = original.bounds.size.y * .00001f;
        var segments = new List<Vector2Int>();
        var indices = original.GetTriangles(0);
        for (int i = 0; i < indices.Length; i += 3)
            for (int k = 0; k < 3; k++)
            {
                int a = indices[i + k], b = indices[i + (k + 1) % 3];
                if (Mathf.Abs(source[a].y - cut) < tolerance && Mathf.Abs(source[b].y - cut) < tolerance)
                    segments.Add(new Vector2Int(a, b));
            }
        Require(segments.Count > 3, "원래 몸체의 목 절단 경계를 찾지 못했습니다.");
        var baked = Bake(body);
        int tested = 0;
        for (int i = source.Length; i < raw.Length; i++)
        {
            if (Mathf.Abs(raw[i].y - cut) > tolerance) continue;
            float nearest = float.MaxValue;
            foreach (var edge in segments)
            {
                var a = baked[edge.x]; var d = baked[edge.y] - a;
                float t = d.sqrMagnitude > 1e-15f ? Mathf.Clamp01(Vector3.Dot(baked[i] - a, d) / d.sqrMagnitude) : 0f;
                nearest = Mathf.Min(nearest, Vector3.Distance(baked[i], a + d * t));
            }
            report.maximumCollarSeamGap = Mathf.Max(report.maximumCollarSeamGap, nearest);
            tested++;
        }
        Require(tested > 3, "복원한 옷깃의 접합 경계를 찾지 못했습니다.");
        Require(report.maximumCollarSeamGap < original.bounds.size.y * .001f, "자세·머리 회전에서 옷깃 접합 경계가 벌어졌습니다.");
        report.collarSeamChecks++;
    }
    static Vector3[] Bake(SkinnedMeshRenderer skin)
    {
        var mesh = new Mesh();
        try { skin.BakeMesh(mesh); return mesh.vertices; }
        finally { Object.DestroyImmediate(mesh); }
    }
    static float MaxDistance(Vector3[] a, Vector3[] b)
    {
        Require(a.Length == b.Length, "정점 개수가 바뀌었습니다.");
        float max = 0f;
        for (int i = 0; i < a.Length; i++) max = Mathf.Max(max, Vector3.Distance(a[i], b[i]));
        return max;
    }

    static Snapshot[] Capture(GameObject model)
    {
        var result = new List<Snapshot>();
        foreach (var skin in model.GetComponentsInChildren<SkinnedMeshRenderer>(true))
        {
            var mesh = new Mesh();
            try
            {
                skin.BakeMesh(mesh);
                var protect = new bool[mesh.vertexCount];
                var materials = skin.sharedMaterials;
                for (int sub = 0; sub < mesh.subMeshCount && sub < materials.Length; sub++)
                    if (materials[sub] != null && !materials[sub].name.StartsWith("mtl_avatar_body", StringComparison.Ordinal))
                        foreach (int vertex in mesh.GetTriangles(sub)) protect[vertex] = true;
                result.Add(new Snapshot { vertices = mesh.vertices, protect = protect });
            }
            finally { Object.DestroyImmediate(mesh); }
        }
        foreach (var filter in model.GetComponentsInChildren<MeshFilter>(true))
        {
            if (filter.sharedMesh == null) continue;
            var vertices = filter.sharedMesh.vertices.Select(v => filter.transform.TransformPoint(v)).ToArray();
            result.Add(new Snapshot { vertices = vertices, protect = Enumerable.Repeat(true, vertices.Length).ToArray() });
        }
        return result.ToArray();
    }
    public static void CaptureImage(string name)
    {
        var camera = GameObject.Find(PersonaBodyPreviewScene.CameraName).GetComponent<Camera>();
        var previousTarget = camera.targetTexture;
        var previousActive = RenderTexture.active;
        var target = new RenderTexture(800, 900, 24);
        var image = new Texture2D(800, 900, TextureFormat.RGB24, false);
        float aspect = camera.aspect;
        try
        {
            camera.targetTexture = target;
            camera.aspect = 800f / 900f;
            camera.Render();
            RenderTexture.active = target;
            image.ReadPixels(new Rect(0, 0, 800, 900), 0, 0);
            image.Apply();
            Directory.CreateDirectory("tools/_work/body_shape_preview");
            File.WriteAllBytes(Path.Combine("tools/_work/body_shape_preview", name), image.EncodeToPNG());
        }
        finally
        {
            camera.targetTexture = previousTarget;
            camera.aspect = aspect;
            RenderTexture.active = previousActive;
            target.Release();
            Object.DestroyImmediate(target);
            Object.DestroyImmediate(image);
        }
    }
    static float Difference(Snapshot[] a, Snapshot[] b, bool onlyProtected)
    {
        float maximum = 0;
        for (int s = 0; s < a.Length; s++)
            for (int v = 0; v < a[s].vertices.Length; v++)
                if (!onlyProtected || a[s].protect[v])
                    maximum = Mathf.Max(maximum, Vector3.Distance(a[s].vertices[v], b[s].vertices[v]));
        return maximum;
    }
    static void Require(bool condition, string message) { if (!condition) throw new InvalidOperationException(message); }
}
