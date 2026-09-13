// ExperienceCuesBaker.cs
// ExperienceCues 가 실행할 때 조립하는 연출을 씬에 박아 둔다.
//
// 실행 중에 만든 MMF_Player 는 플레이를 멈추면 사라져서 인스펙터로 만질 수 없다.
// 이 메뉴를 한 번 돌리면 플레이어와 덮개·빛이 씬 오브젝트로 남고, 그때부터 곡선과
// 시간을 인스펙터에서 고쳐 저장할 수 있다. 이미 채워진 칸은 건드리지 않는다.

using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

static class ExperienceCuesBaker
{
    const string Menu = "다시봄/Feel 연출을 씬에 굽기";

    [MenuItem(Menu)]
    static void Bake()
    {
        var cues = Selection.activeGameObject != null
            ? Selection.activeGameObject.GetComponent<ExperienceCues>()
            : null;
        if (cues == null) cues = Object.FindObjectOfType<ExperienceCues>();
        if (cues == null)
        {
            EditorUtility.DisplayDialog("Feel 연출", "씬에 ExperienceCues 가 없습니다.", "확인");
            return;
        }

        Undo.RecordObject(cues, "Feel 연출 굽기");
        cues.Build(o => Undo.RegisterCreatedObjectUndo(o, "Feel 연출 굽기"));
        EditorUtility.SetDirty(cues);
        EditorSceneManager.MarkSceneDirty(cues.gameObject.scene);
        Debug.Log("[ExperienceCues] Feel 연출을 씬에 구웠습니다. 인스펙터에서 각 플레이어를 열어 조정하세요.", cues);
    }

    [MenuItem(Menu, true)]
    static bool BakeAllowed() => !EditorApplication.isPlayingOrWillChangePlaymode;
}
