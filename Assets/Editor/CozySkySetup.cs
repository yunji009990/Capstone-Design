// CozySkySetup.cs — COZY 하늘을 이 체험에 맞게 맞춘다.
//
// 설치 자체는 COZY 가 자기 메뉴로 한다(Tools > Cozy: Stylized Weather 3 > Setup Scene).
// 여기서 하는 건 그 다음, 이 프로젝트에만 해당하는 두 가지다.
//
// 1) 시간을 세워 둔다.
//    COZY 는 기본으로 하루가 흐른다. 체험은 10분 남짓인데 그동안 하늘이 눈에 띄게
//    움직이면 재회 장면의 시간 감각이 어긋난다. 봄 늦은 오후에 고정한다.
//
// 2) 시간 프로파일을 프로젝트 안으로 복사한다. ★ 이게 핵심이다
//    COZY 의 기본 PerennialProfile 은 Packages/com.distantlands.cozy.core 안에 있다.
//    패키지 안의 에셋은 레포에 안 담기고 패키지를 업데이트하면 덮인다. 거기다 대고
//    시간을 고치면 나중에 조용히 사라지고, 팀원이 받아도 그 설정이 없다.
//    그래서 Assets/Settings 로 복사해 두고 그 사본을 쓰게 한다.
//
// 되돌리려면 Ctrl+Z 로 씬 변경이 되돌아가고, 복사된 에셋은 지우면 된다.

using UnityEditor;
using UnityEngine;
#if COZY_WEATHER
using System.IO;
using DistantLands.Cozy;
using DistantLands.Cozy.Data;   // PerennialProfile 은 이쪽 네임스페이스다
using UnityEditor.SceneManagement;
#endif

static class CozySkySetup
{
    const string Menu = "다시봄/카페 하늘 맞추기 (COZY)";

    // 봄 늦은 오후. 창으로 낮게 드는 빛이 인물 얼굴에 닿는 시간대다.
    const int Hour = 16;
    const int Minute = 30;

    const string ProfileFolder = "Assets/Settings";
    const string ProfileName = "다시봄 하늘 시간.asset";

#if !COZY_WEATHER
    [MenuItem(Menu)]
    static void NotInstalled()
    {
        EditorUtility.DisplayDialog("COZY 없음",
            "COZY_WEATHER 정의가 없다. 패키지가 빠졌거나 아직 컴파일되지 않았다.", "확인");
    }
#else
    [MenuItem(Menu)]
    static void Setup()
    {
        var weather = Object.FindObjectOfType<CozyWeather>();
        if (weather == null)
        {
            EditorUtility.DisplayDialog("COZY 하늘 없음",
                "씬에 CozyWeather 가 없다.\n\n" +
                "먼저 COZY 자체 메뉴로 설치할 것:\n" +
                "Tools > Cozy: Stylized Weather 3 > Setup Scene\n\n" +
                "그 다음 이 메뉴를 다시 실행하면 시간을 맞춘다.", "확인");
            return;
        }

        var time = weather.timeModule != null ? weather.timeModule : weather.GetModule<CozyTimeModule>();
        if (time == null)
        {
            EditorUtility.DisplayDialog("시간 모듈 없음",
                "CozyWeather 에 시간 모듈(CozyTimeModule)이 없다.\n" +
                "Cozy Weather Sphere 인스펙터에서 Time 모듈을 켤 것.", "확인");
            return;
        }

        var report = new System.Collections.Generic.List<string>();

        // ── 프로파일을 프로젝트 안으로 ──────────────────────────
        PerennialProfile profile = time.perennialProfile;
        string path = profile != null ? AssetDatabase.GetAssetPath(profile) : "";
        bool inPackage = string.IsNullOrEmpty(path) || path.StartsWith("Packages/");

        if (inPackage)
        {
            if (!Directory.Exists(ProfileFolder))
            {
                Directory.CreateDirectory(ProfileFolder);
                AssetDatabase.Refresh();
            }
            string dest = $"{ProfileFolder}/{ProfileName}";

            if (AssetDatabase.LoadAssetAtPath<PerennialProfile>(dest) != null)
            {
                profile = AssetDatabase.LoadAssetAtPath<PerennialProfile>(dest);
                report.Add($"  이미 있는 사본을 쓴다: {dest}");
            }
            else if (!string.IsNullOrEmpty(path) && AssetDatabase.CopyAsset(path, dest))
            {
                profile = AssetDatabase.LoadAssetAtPath<PerennialProfile>(dest);
                report.Add($"  프로파일을 프로젝트로 복사했다: {path}\n    → {dest}");
            }
            else
            {
                profile = ScriptableObject.CreateInstance<PerennialProfile>();
                AssetDatabase.CreateAsset(profile, dest);
                report.Add($"  프로파일이 없어 새로 만들었다: {dest}");
            }

            Undo.RecordObject(time, "하늘 시간 맞추기");
            time.perennialProfile = profile;
            EditorUtility.SetDirty(time);
        }
        else report.Add($"  프로파일은 이미 프로젝트 안에 있다: {path}");

        // ── 시간을 세운다 ───────────────────────────────────────
        Undo.RecordObject(profile, "하늘 시간 맞추기");
        profile.startTime = new MeridiemTime(Hour, Minute);
        profile.resetTimeOnStart = true;    // 실행할 때마다 같은 시간에서 시작한다
        profile.pauseTime = true;           // 체험 중에 하늘이 흐르지 않는다
        profile.progressDay = false;        // 날짜도 넘어가지 않는다
        EditorUtility.SetDirty(profile);

        Undo.RecordObject(time, "하늘 시간 맞추기");
        time.currentTime = profile.startTime;   // 에디터 미리보기도 같은 시간으로
        EditorUtility.SetDirty(time);

        AssetDatabase.SaveAssets();
        EditorSceneManager.MarkSceneDirty(weather.gameObject.scene);
        Selection.activeGameObject = weather.gameObject;

        report.Add($"  시간 고정: {Hour:00}:{Minute:00} (흐름 정지, 날짜 정지)");
        Debug.Log("[CozySkySetup] 카페 하늘을 맞췄다.\n" + string.Join("\n", report) +
                  "\n  시간대를 바꾸려면 이 스크립트의 Hour/Minute 을 고치거나 " +
                  "프로파일 에셋을 인스펙터에서 직접 만질 것.", weather);
    }

    [MenuItem(Menu, true)]
    static bool Allowed() => !EditorApplication.isPlayingOrWillChangePlaymode;
#endif
}
