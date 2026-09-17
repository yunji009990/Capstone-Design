// PersonaHumanoidTest.cs — PersonaHumanoidProbe 를 씬에 세우고 Humanoid 클립을 꽂아 준다.
//
// 씬 파일을 직접 고치지 않는다. Unity 가 씬을 열어 둔 상태에서는 디스크 수정이 덮이기 때문에,
// 열려 있는 씬에 오브젝트를 만들어 주는 쪽이 확실하다.

using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

public static class PersonaHumanoidTest
{
    const string ProbeName = "PersonaHumanoidProbe";
    const string ModelFolder = "Assets/Models";

    [MenuItem("Tools/Persona/Humanoid 클립 시험 준비", priority = 0)]
    public static void Prepare()
    {
        var clips = FindHumanoidClips();
        if (clips.Count == 0)
        {
            EditorUtility.DisplayDialog("Humanoid 클립 없음",
                $"{ModelFolder} 에서 Humanoid 클립을 찾지 못했다.\n\n" +
                "FBX 를 고른 뒤 Inspector 의 Rig > Animation Type 을 Humanoid 로 바꾸고 Apply 할 것.", "확인");
            return;
        }

        // "Sitting Clap" 을 우선 고르고, 없으면 첫 번째.
        var chosen = clips.FirstOrDefault(c => c.path.Contains("Sitting Clap")).clip ?? clips[0].clip;

        var probe = Object.FindObjectOfType<PersonaHumanoidProbe>();
        if (probe == null)
        {
            var go = new GameObject(ProbeName);
            probe = go.AddComponent<PersonaHumanoidProbe>();
            Undo.RegisterCreatedObjectUndo(go, "Create " + ProbeName);
        }
        Undo.RecordObject(probe, "Assign humanoid clip");
        probe.humanoidClip = chosen;
        probe.runOnSpawn = true;
        EditorUtility.SetDirty(probe);
        EditorSceneManager.MarkSceneDirty(probe.gameObject.scene);
        Selection.activeObject = probe.gameObject;

        var lines = new List<string> { $"[PersonaHumanoidTest] 준비 완료 — 클립 '{chosen.name}' ({chosen.length:0.00}s)" };
        lines.Add($"  Humanoid 여부: {chosen.isHumanMotion}  (false 면 FBX 를 Humanoid 로 다시 임포트할 것)");
        lines.Add($"  쓸 수 있는 클립 {clips.Count}개:");
        foreach (var (path, clip) in clips)
            lines.Add($"    {Path.GetFileNameWithoutExtension(path)} / {clip.name}  {clip.length:0.00}s" +
                      (clip == chosen ? "   ← 선택됨" : ""));
        lines.Add("  이제 Play 하면 인물이 스폰된 뒤 자동으로 아바타를 만들고 이 클립을 재생한다.");
        Debug.Log(string.Join("\n", lines), probe);
    }

    [MenuItem("Tools/Persona/Humanoid 클립 목록 확인", priority = 1)]
    public static void ListClips()
    {
        var clips = FindHumanoidClips();
        if (clips.Count == 0) { Debug.LogWarning($"[PersonaHumanoidTest] {ModelFolder} 에 Humanoid 클립이 없다"); return; }
        Debug.Log($"[PersonaHumanoidTest] Humanoid 클립 {clips.Count}개\n" + string.Join("\n",
            clips.Select(c => $"  {c.path}  →  {c.clip.name}  {c.clip.length:0.00}s  (isHumanMotion={c.clip.isHumanMotion})")));
    }

    /// <summary>Assets/Models 아래 모델 에셋에서 Humanoid 클립만 모은다. 미리보기용 클립은 뺀다.</summary>
    static List<(string path, AnimationClip clip)> FindHumanoidClips()
    {
        var found = new List<(string, AnimationClip)>();
        foreach (var guid in AssetDatabase.FindAssets("t:Model", new[] { ModelFolder }))
        {
            string path = AssetDatabase.GUIDToAssetPath(guid);
            foreach (var asset in AssetDatabase.LoadAllAssetsAtPath(path))
            {
                if (asset is AnimationClip clip && !clip.name.StartsWith("__preview__") && clip.isHumanMotion)
                    found.Add((path, clip));
            }
        }
        return found;
    }
}
