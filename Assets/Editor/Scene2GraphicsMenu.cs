// 그래픽 메뉴의 등록은 이 파일에서 관리한다. 배경 선택과 편집 기능을 분리한다.
using System;
using UnityEditor;
using UnityEngine.SceneManagement;

public static class Scene2GraphicsMenu
{
    const string Root = "Tools/다시봄/그래픽/";
    const string Night = Root + "배경 선택/밤하늘 카페";
    const string Pastel = Root + "배경 선택/파스텔 하늘 카페";
    const string Garden = Root + "배경 선택/기존 정원";
    const string Glass = Root + "기능/유리 반사 조절 열기";
    const string View = Root + "기능/창가에서 보기";
    const string Inspect = Root + "기능/검사/현재 배경 촬영·검사";
    const string CheckBackgrounds = Root + "기능/검사/배경 전환 보존 검사";
    const string CheckGlass = Root + "기능/검사/유리 반사 검사";
    const string BakeLight = Root + "기능/베이크/실내 조명";
    const string BakeReflection = Root + "기능/베이크/실내 반사·저장";

    [MenuItem(Night, false, 0)]
    static void ChooseNight() => ChangeBackground(Scene2SkyCafe.MoonlitAppearance);

    [MenuItem(Pastel, false, 1)]
    static void ChoosePastel() => ChangeBackground(Scene2SkyCafe.PastelAppearance);

    [MenuItem(Garden, false, 2)]
    static void ChooseGarden() => ChangeBackground(Scene2SkyCafe.Garden);

    static void ChangeBackground(Action apply)
    {
        // 선택 항목을 회색으로 숨기지 않고, 실행할 수 없는 이유를 클릭 시 안내한다.
        string reason = BackgroundUnavailableReason();
        if (reason != null)
        {
            EditorUtility.DisplayDialog("배경 선택", reason, "확인");
            return;
        }
        apply();
    }

    static string BackgroundUnavailableReason()
    {
        if (SceneManager.GetActiveScene().path != Scene2GraphicsCapture.ScenePath)
            return "Scene_2 씬을 연 뒤 배경을 선택해 주세요.";
        if (EditorApplication.isCompiling)
            return "스크립트 컴파일이 끝난 뒤 배경을 선택해 주세요.";
        if (EditorApplication.isPlayingOrWillChangePlaymode)
            return "플레이를 중지한 뒤 배경을 선택해 주세요.";
        if (Lightmapping.isRunning)
            return "조명 베이크가 끝난 뒤 배경을 선택해 주세요.";
        return null;
    }

    [MenuItem(Glass, false, 0)]
    static void OpenGlass() => CafePlanarReflectionEditor.OpenControls();

    [MenuItem(View, false, 1)]
    static void ViewCafe() => Scene2SkyCafe.ViewCafe();

    [MenuItem(Inspect, false, 20)]
    static void InspectBackground()
    {
        Scene2SkyCafe.Inspect();
        Scene2SkyCafe.InspectCloudDepth();
    }

    [MenuItem(CheckBackgrounds, false, 21)]
    static void ValidateBackgrounds()
    {
        Scene2SkyCafe.ValidateSwitch();
        Scene2SkyCafe.ValidateAppearances();
    }

    [MenuItem(CheckGlass, false, 22)]
    static void ValidateGlass()
    {
        Scene2PlanarReflectionSetup.ValidateGeometry();
        Scene2PlanarReflectionValidation.Validate();
    }

    [MenuItem(BakeLight, false, 40)]
    static void BakeLighting() => Scene2NaturalLighting.Bake();

    [MenuItem(BakeReflection, false, 41)]
    static void BakeReflections() => Scene2NaturalLighting.BakeReflection();

    static bool IsScene2() => SceneManager.GetActiveScene().path == Scene2GraphicsCapture.ScenePath &&
        !EditorApplication.isCompiling;

    static bool CanEditScene() => IsScene2() && !EditorApplication.isPlayingOrWillChangePlaymode &&
        !Lightmapping.isRunning;

    // 반사 조절은 원래 기능대로 Play 중에도 열 수 있다.
    [MenuItem(Glass, true)] static bool CanOpenGlass() => IsScene2();
    [MenuItem(View, true)] static bool CanViewCafe() => CanEditScene();
    [MenuItem(Inspect, true)] static bool CanInspect() => CanEditScene();
    [MenuItem(CheckBackgrounds, true)] static bool CanCheckBackgrounds() => CanEditScene();
    [MenuItem(CheckGlass, true)] static bool CanCheckGlass() => CanEditScene();
    [MenuItem(BakeLight, true)] static bool CanBakeLight() => CanEditScene();
    [MenuItem(BakeReflection, true)] static bool CanBakeReflection() => CanEditScene();
}
