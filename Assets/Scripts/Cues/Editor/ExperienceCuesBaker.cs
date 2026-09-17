// ExperienceCuesBaker.cs
// ExperienceCues 가 실행할 때 조립하는 연출을 씬에 박아 둔다.
//
// 실행 중에 만든 MMF_Player 는 플레이를 멈추면 사라져서 인스펙터로 만질 수 없다.
// 이 메뉴를 한 번 돌리면 플레이어와 덮개·빛이 씬 오브젝트로 남고, 그때부터 곡선과
// 시간을 인스펙터에서 고쳐 저장할 수 있다. 이미 채워진 칸은 건드리지 않는다.
//
// 씬에 ExperienceCues 가 아직 없으면 여기서 만든다. 스크립트는 다 짜여 있는데 씬에
// 얹히지 않아 Feel 이 통째로 놀고 있는 상태가 오래갔다 — 메뉴 한 번으로 붙게 한다.
// 덮개는 실행할 때 스스로 카메라 앞으로 옮겨 붙으므로 어디에 두든 상관없다.

using System.Collections.Generic;
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

        bool created = false;
        if (cues == null)
        {
            var host = new GameObject("체험 연출(ExperienceCues)");
            Undo.RegisterCreatedObjectUndo(host, "체험 연출 만들기");
            cues = Undo.AddComponent<ExperienceCues>(host);
            created = true;
        }

        // 실행하면 Awake 가 알아서 찾지만, 지금 채워 두면 인스펙터에서 배선이 보인다.
        Undo.RecordObject(cues, "Feel 연출 굽기");
        var wired = new List<string>();
        if (cues.voice == null && (cues.voice = Object.FindObjectOfType<DialogueVoiceClient>()) != null)
            wired.Add("음성");
        if (cues.experience == null && (cues.experience = Object.FindObjectOfType<ExperienceControl>()) != null)
            wired.Add("체험 제어");
        if (cues.spawner == null && (cues.spawner = Object.FindObjectOfType<PersonaSpawner>()) != null)
            wired.Add("인물 스포너");
        if (cues.operatorUI == null && (cues.operatorUI = Object.FindObjectOfType<DialogueVoiceUI>()) != null)
            wired.Add("운영자 화면");

        cues.Build(o => Undo.RegisterCreatedObjectUndo(o, "Feel 연출 굽기"));
        EditorUtility.SetDirty(cues);
        EditorSceneManager.MarkSceneDirty(cues.gameObject.scene);
        Selection.activeGameObject = cues.gameObject;

        Debug.Log($"[ExperienceCues] {(created ? "씬에 새로 붙이고 " : "")}Feel 연출을 구웠습니다." +
                  (wired.Count > 0 ? $" 연결: {string.Join(", ", wired)}." : "") +
                  " 인스펙터에서 각 플레이어를 열어 조정하세요.", cues);
    }

    [MenuItem(Menu, true)]
    static bool BakeAllowed() => !EditorApplication.isPlayingOrWillChangePlaymode;
}
