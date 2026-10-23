// Scene_2 그래픽 편집 도구가 공유하는 씬·카페 조회. 촬영은 현재 배경 검사로 통합한다.
using System;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEngine;
using UnityEngine.SceneManagement;

public static class Scene2GraphicsCapture
{
    internal const string ScenePath = "Assets/Scenes/Scene_2.unity";
    internal const string CafeAssets = "Assets/Models/Cozy cafe - confectionery and bakery/";
    internal static string OutputDirectory => Path.Combine(Path.GetDirectoryName(Application.dataPath),
        "tools", "_work", "graphics_" + DateTime.Now.ToString("yyyyMMdd") + "_rework");

    internal static Scene RequireScene()
    {
        var scene = SceneManager.GetActiveScene();
        if (scene.path != ScenePath || EditorApplication.isPlayingOrWillChangePlaymode)
            throw new InvalidOperationException("Scene_2를 Edit Mode에서 연 뒤 실행하세요.");
        return scene;
    }

    internal static GameObject SceneObject(string name) => RequireScene().GetRootGameObjects()
        .SelectMany(root => root.GetComponentsInChildren<Transform>(true)).Select(t => t.gameObject)
        .FirstOrDefault(go => go.name == name);

    internal static Renderer[] CafeRenderers()
    {
        var root = SceneObject("카페 구조와 소품");
        if (root == null) throw new InvalidOperationException("카페 배경 그룹을 찾지 못했습니다.");
        return root.GetComponentsInChildren<Renderer>(true);
    }
}
