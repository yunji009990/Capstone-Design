using System;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Threading.Tasks;
using UnityEditor;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.UIElements;
using Object = UnityEngine.Object;

/// <summary>실제 카페 크기·착석·바닥·시점과 두 배경 사이의 설정 보존을 검사한다.</summary>
public static class PersonaBodyPreviewCafeValidation
{
    const string Folder = "tools/_work/cafe_body_preview_20261024";
    [Serializable] sealed class Report
    {
        public bool success, scaleMatchesCafe, seatedPositionMatches, standingOnFloor, cameraFixedDuringResize;
        public bool temporaryObjectsExcluded, environmentRoundTrip, profileUnchanged, cafeFileUnchanged, defaultProfileUnchanged, uiConnected;
        public float targetHeight, multiplier, standingHeight, seatedHeight, maximumGroundError;
        public Vector3 cameraPosition;
        public string error;
    }

    public static async void Run()
    {
        var report = new Report();
        var profile = PersonaBodyPreviewScene.Profile;
        string settings = profile != null ? JsonUtility.ToJson(profile) : "";
        string cafeHash = Hash(PersonaBodyPreviewCafe.ScenePath);
        string defaultHash = Hash("Assets/Resources/PersonaBodyShape.asset");
        var originalPose = PersonaBodyPreviewScene.CurrentPose;
        PersonaBodyShapeProfile test = null;
        try
        {
            await PersonaBodyPreviewScene.SetEnvironment(true);
            Require(PersonaBodyPreviewCafe.IsActive && PersonaBodyPreviewScene.Model != null && !PersonaBodyPreviewScene.Busy, "카페 미리보기 로드 실패");
            var model = PersonaBodyPreviewScene.Model;
            var controller = PersonaBodyPreviewScene.Controller;
            var spawner = PersonaBodyPreviewCafe.CurrentSpawner;
            report.targetHeight = spawner.targetHeightMeters;
            report.multiplier = spawner.scaleMultiplier;
            float expected = PersonaBodyPreviewCafe.NormalizationScale * (spawner.scaleMultiplier > 0f ? spawner.scaleMultiplier : 1f) * profile.BodyScale;
            Require(Vector3.Distance(model.transform.localScale, Vector3.one * expected) < 1e-5f, "카페 스폰 배율 불일치");
            report.scaleMatchesCafe = true;
            var window = Resources.FindObjectsOfTypeAll<PersonaBodyPreviewWindow>().FirstOrDefault();
            var buttons = window.rootVisualElement.Query<Button>().ToList();
            Require(buttons.Any(b => b.text == "체험자 자리에서 보기" && b.enabledInHierarchy) &&
                buttons.Any(b => b.text == "단독 미리보기로 돌아가기" && b.enabledInHierarchy), "카페 조절창 버튼 연결 오류");
            report.uiConnected = true;
            PersonaBodyPreviewScene.SetPose(PersonaBodyPreviewScene.Pose.앉기);
            await Task.Delay(150);
            var arrival = spawner.arrival;
            var seat = arrival != null && arrival.seat != null ? arrival.seat : spawner.spawnPoint;
            var position = seat.position + (arrival != null ? arrival.seatOffset : Vector3.zero);
            Require(Vector3.Distance(model.transform.position, position) < 1e-5f, "실제 착석 위치 불일치");
            Require(Vector3.Angle(Vector3.ProjectOnPlane(PersonaHumanoid.MeasureFacing(model.transform), Vector3.up),
                Vector3.ProjectOnPlane(seat.forward, Vector3.up)) < .1f, "실제 착석 방향 불일치");
            report.seatedPositionMatches = true;
            report.seatedHeight = PersonaBodyPreviewScene.Bounds().size.y;
            PersonaBodyPreviewCafe.PlayerView();
            var camera = PersonaBodyPreviewCafe.Camera();
            report.cameraPosition = camera.transform.position;
            Require(Vector3.Distance(camera.transform.position, PersonaBodyPreviewCafe.PlayerEyePosition) < 1e-5f, "체험자 카메라 위치 불일치");
            Capture("카페_체험자시점.private.png");
            PersonaBodyPreviewScene.Frame(90);
            Capture("카페_착석_측면.private.png");
            report.temporaryObjectsExcluded = model.GetComponentsInChildren<Transform>(true)
                .All(t => (t.gameObject.hideFlags & HideFlags.DontSaveInEditor) != 0)
                && (camera.gameObject.hideFlags & HideFlags.DontSaveInEditor) != 0;
            Require(report.temporaryObjectsExcluded, "모델·카메라 저장 제외 오류");
            PersonaBodyPreviewScene.SetPose(PersonaBodyPreviewScene.Pose.서있기);
            await Task.Delay(150);
            report.standingHeight = PersonaBodyPreviewScene.Bounds().size.y;
            Capture("카페_서있기.private.png");
            test = Object.Instantiate(profile);
            test.hideFlags = HideFlags.HideAndDontSave;
            controller.Bind(test);
            var cameraPosition = camera.transform.position;
            var cameraRotation = camera.transform.rotation;
            var serialized = new SerializedObject(test);
            foreach (float size in new[] { .7f, 1.3f })
            {
                serialized.FindProperty("bodyScale").floatValue = size;
                serialized.ApplyModifiedPropertiesWithoutUndo();
                PersonaBodyPreviewScene.ApplyShapes();
                Require(Vector3.Distance(camera.transform.position, cameraPosition) < 1e-5f && Quaternion.Angle(camera.transform.rotation, cameraRotation) < .01f,
                    "배율 조절 중 카메라가 움직여 크기 비교가 불가능합니다.");
                float floor = arrival.groundPath.GetWorldPoint(arrival.groundPath.PointCount - 1).y;
                report.maximumGroundError = Mathf.Max(report.maximumGroundError, Mathf.Abs(PersonaBodyPreviewScene.Bounds().min.y - floor));
                Require(report.maximumGroundError < .001f, "배율 조절 후 발이 바닥에서 벗어났습니다.");
                Require(Vector3.Distance(model.transform.localScale, Vector3.one * expected / profile.BodyScale * size) < 1e-5f, "전체 크기 조절 오류");
                Capture(size < 1f ? "카페_크기_최소.private.png" : "카페_크기_최대.private.png");
            }
            report.cameraFixedDuringResize = true;
            report.standingOnFloor = true;
            controller.Bind(profile);
            PersonaBodyPreviewScene.ApplyShapes();
            var previousModel = model;
            var previousCamera = camera;
            await PersonaBodyPreviewScene.SetEnvironment(false);
            Require(!PersonaBodyPreviewCafe.IsActive && PersonaBodyPreviewScene.Model != null && previousModel == null && previousCamera == null,
                "배경 전환 때 카페 미리보기 정리 오류");
            Require(Mathf.Abs(PersonaBodyPreviewScene.Model.transform.localScale.x - expected) < 1e-5f, "단독 씬과 카페의 크기가 다릅니다.");
            await PersonaBodyPreviewScene.SetEnvironment(true);
            Require(PersonaBodyPreviewCafe.IsActive && PersonaBodyPreviewScene.Model != null && PersonaBodyPreviewScene.CurrentPose == PersonaBodyPreviewScene.Pose.앉기,
                "카페 재진입 오류");
            report.environmentRoundTrip = true;
            report.success = true;
        }
        catch (Exception error) { report.error = error.GetType().Name + ": " + error.Message; }
        finally
        {
            if (PersonaBodyPreviewScene.Controller != null && profile != null)
            {
                PersonaBodyPreviewScene.Controller.Bind(profile);
                PersonaBodyPreviewScene.ApplyShapes();
                if (PersonaBodyPreviewCafe.IsActive) PersonaBodyPreviewScene.SetPose(originalPose);
            }
            if (test != null) Object.DestroyImmediate(test);
            report.profileUnchanged = profile != null && JsonUtility.ToJson(profile) == settings;
            report.cafeFileUnchanged = cafeHash == Hash(PersonaBodyPreviewCafe.ScenePath);
            report.defaultProfileUnchanged = defaultHash == Hash("Assets/Resources/PersonaBodyShape.asset");
            report.success &= report.profileUnchanged && report.cafeFileUnchanged && report.defaultProfileUnchanged;
            Directory.CreateDirectory(Folder);
            File.WriteAllText(Path.Combine(Folder, "validation.json"), JsonUtility.ToJson(report, true));
            PersonaBodyPreviewScene.SetStatus(report.success ? "카페 크기·의자·바닥·시점·배경 전환·설정 보존 검사 통과" : "카페 검사 실패. 로컬 검사 결과를 확인하세요.");
        }
    }

    static string Hash(string path)
    {
        using (var sha = SHA256.Create()) return Convert.ToBase64String(sha.ComputeHash(File.ReadAllBytes(path)));
    }
    static void Require(bool value, string message) { if (!value) throw new InvalidOperationException(message); }
    static void Capture(string name)
    {
        var camera = PersonaBodyPreviewCafe.Camera();
        var previousTarget = camera.targetTexture;
        var previousActive = RenderTexture.active;
        float previousAspect = camera.aspect;
        var target = new RenderTexture(1280, 720, 24);
        var texture = new Texture2D(1280, 720, TextureFormat.RGB24, false);
        try
        {
            camera.targetTexture = target; camera.aspect = 1280f / 720f; camera.Render();
            RenderTexture.active = target;
            texture.ReadPixels(new Rect(0, 0, 1280, 720), 0, 0); texture.Apply();
            Directory.CreateDirectory(Folder);
            File.WriteAllBytes(Path.Combine(Folder, name), texture.EncodeToPNG());
        }
        finally
        {
            camera.targetTexture = previousTarget; camera.aspect = previousAspect; RenderTexture.active = previousActive;
            target.Release(); Object.DestroyImmediate(target); Object.DestroyImmediate(texture);
        }
    }
}
