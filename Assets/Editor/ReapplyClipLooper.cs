// 모델을 갈아끼울 때마다 ClipLooper 를 손으로 다시 붙이지 않기 위한 메뉴.
//
// FBX 를 교체하면 GUID 가 바뀌고, 씬에 있던 오브젝트를 지우고 새로 끌어다
// 놓으면 붙어 있던 컴포넌트도 같이 사라진다. 실제로 바리스타 모델을 한 번
// 바꿨더니 ClipLooper 가 씬에서 통째로 없어졌다.
//
// 클립은 FBX 안에 들어 있고 그 fileID 는 임포트 시점에 정해져서 .meta 만
// 봐서는 알 수 없다(internalIDToNameTable 이 비어 있다). 그래서 에디터
// 밖에서는 씬에 클립 참조를 넣어줄 방법이 없다. 이 메뉴가 그 일을 대신한다.

using System.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

public static class ReapplyClipLooper
{
    // 이름으로 찾는다. 폴더 구조가 또 바뀌어도 따라간다.
    const string ClipAssetName = "barista_cafe_work_idle";

    [MenuItem("Tools/바리스타 동작 재적용", false, 101)]
    static void Reapply()
    {
        string path = FindModelPath(ClipAssetName);
        if (path == null)
        {
            EditorUtility.DisplayDialog("바리스타 동작 재적용",
                $"'{ClipAssetName}' 모델을 프로젝트에서 찾지 못했습니다.", "확인");
            return;
        }

        // FBX 안의 클립을 꺼낸다. __preview__ 로 시작하는 것은 에디터가
        // 미리보기용으로 만든 것이라 건너뛴다.
        var clip = AssetDatabase.LoadAllAssetRepresentationsAtPath(path)
            .OfType<AnimationClip>()
            .FirstOrDefault(c => !c.name.StartsWith("__preview__"));

        if (clip == null)
        {
            EditorUtility.DisplayDialog("바리스타 동작 재적용",
                $"{path} 안에서 애니메이션 클립을 찾지 못했습니다.\n" +
                "임포트 설정에서 Import Animation 이 켜져 있는지 보세요.", "확인");
            return;
        }

        int touched = 0;
        foreach (var root in EditorSceneManager.GetActiveScene().GetRootGameObjects())
        {
            foreach (var t in root.GetComponentsInChildren<Transform>(true))
            {
                // 이 FBX 에서 온 인스턴스만 고른다.
                var source = PrefabUtility.GetCorrespondingObjectFromSource(t.gameObject);
                if (source == null) continue;
                if (AssetDatabase.GetAssetPath(source) != path) continue;
                // 프리팹 인스턴스의 최상단에만 붙인다.
                if (PrefabUtility.GetNearestPrefabInstanceRoot(t.gameObject) != t.gameObject) continue;

                var looper = t.GetComponent<ClipLooper>();
                if (looper == null) looper = Undo.AddComponent<ClipLooper>(t.gameObject);

                Undo.RecordObject(looper, "바리스타 동작 재적용");
                looper.clip = clip;
                EditorUtility.SetDirty(looper);
                touched++;
            }
        }

        if (touched == 0)
        {
            EditorUtility.DisplayDialog("바리스타 동작 재적용",
                $"열려 있는 씬에서 {System.IO.Path.GetFileName(path)} 인스턴스를 찾지 못했습니다.\n" +
                "바리스타가 있는 씬을 연 뒤 다시 실행하세요.", "확인");
            return;
        }

        EditorSceneManager.MarkSceneDirty(EditorSceneManager.GetActiveScene());
        Debug.Log($"[ReapplyClipLooper] {touched}개 오브젝트에 '{clip.name}' 적용 " +
                  $"(길이 {clip.length:0.00}s). 씬을 저장하세요.");
    }

    static string FindModelPath(string assetName)
    {
        foreach (var guid in AssetDatabase.FindAssets($"{assetName} t:Model"))
        {
            string p = AssetDatabase.GUIDToAssetPath(guid);
            if (System.IO.Path.GetFileNameWithoutExtension(p) == assetName) return p;
        }
        return null;
    }
}
