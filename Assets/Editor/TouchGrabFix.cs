// TouchGrabFix.cs — Scene_2 의 Touch_grab 이 매 프레임 NullReferenceException 을 뿜는 걸 고친다.
//
// 증상: Play 하면 Meta XR SDK 의 TouchHandGrabInteractor.ComputeCandidate 가 프레임마다 터진다.
//   NullReferenceException ... TouchHandGrabInteractor.cs:535
//   AssertionException: At GameObject Touch_grab, component TouchHandGrabInteractable.
//   Required Bounds Collider reference is missing.
//
// 원인 연쇄: TouchHandGrabInteractable.Start() 가 _boundsCollider 가 없어 예외로 죽는다
//   → ColliderGroup 이 영영 안 만들어진다
//   → 그런데 OnEnable 에서 Registry 에는 이미 등록됐다
//   → interactor 가 매 프레임 그걸 훑다가 터진다.
// Play 한 번에 수천 줄이 쌓여 Editor 로그가 500MB 를 넘겼다.
//
// 씬 파일을 직접 고치지 않는다. Unity 가 씬을 열어 둔 동안에는 디스크 수정이 덮이기 때문에,
// 열려 있는 씬의 오브젝트를 고쳐 주는 쪽이 확실하다.
//
// Meta 패키지 타입을 직접 참조하지 않고 타입 이름과 SerializedProperty 로만 접근한다.
// 패키지 버전이 바뀌거나 빠져도 이 스크립트가 컴파일을 막지 않는다.

using System.Collections.Generic;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

public static class TouchGrabFix
{
    const string TypeName = "TouchHandGrabInteractable";

    [MenuItem("Tools/Persona/Touch_grab 진단하기", priority = 20)]
    public static void Diagnose() { Run(false); }

    [MenuItem("Tools/Persona/Touch_grab 고치기", priority = 21)]
    public static void Fix() { Run(true); }

    static void Run(bool repair)
    {
        var report = new List<string>();
        int seen = 0, broken = 0, assigned = 0, disabled = 0;

        foreach (var behaviour in Object.FindObjectsOfType<MonoBehaviour>(true))
        {
            if (behaviour == null || behaviour.GetType().Name != TypeName) continue;
            seen++;

            var so = new SerializedObject(behaviour);
            var bounds = so.FindProperty("_boundsCollider");
            var colliders = so.FindProperty("_colliders");
            if (bounds == null)
            {
                report.Add($"  {Path(behaviour.transform)} — _boundsCollider 필드가 없다(패키지 구조가 바뀐 듯)");
                continue;
            }

            bool dangling = bounds.objectReferenceValue == null && bounds.objectReferenceInstanceIDValue != 0;
            bool empty = bounds.objectReferenceValue == null && bounds.objectReferenceInstanceIDValue == 0;
            int listed = colliders != null && colliders.isArray ? colliders.arraySize : -1;
            int alive = 0;
            for (int i = 0; i < Mathf.Max(0, listed); i++)
                if (colliders.GetArrayElementAtIndex(i).objectReferenceValue != null) alive++;

            string state = dangling ? "끊어진 참조(Missing)" : empty ? "비어 있음(None)" : "정상";
            report.Add($"  {Path(behaviour.transform)} — Bounds Collider: {state}, " +
                       $"Colliders {alive}/{listed} 살아있음, 컴포넌트 {(behaviour.enabled ? "켜짐" : "꺼짐")}");

            if (!dangling && !empty && alive > 0) continue;   // 멀쩡하다
            broken++;
            if (!repair) continue;

            var found = FindNearbyCollider(behaviour.transform);
            if (found != null)
            {
                Undo.RecordObject(behaviour, "Fix Touch_grab");
                bounds.objectReferenceValue = found;
                if (colliders != null && colliders.isArray)
                {
                    colliders.ClearArray();
                    colliders.InsertArrayElementAtIndex(0);
                    colliders.GetArrayElementAtIndex(0).objectReferenceValue = found;
                }
                so.ApplyModifiedProperties();
                EditorUtility.SetDirty(behaviour);
                assigned++;
                report.Add($"     → 고침: {Path(found.transform)} 의 {found.GetType().Name} 를 꽂았다");
            }
            else
            {
                Undo.RecordObject(behaviour, "Disable Touch_grab");
                behaviour.enabled = false;
                EditorUtility.SetDirty(behaviour);
                disabled++;
                report.Add("     → 주변에서 Collider 를 못 찾아 컴포넌트를 껐다. " +
                           "이 상태로는 어차피 동작하지 않았고, 끄면 NRE 는 멈춘다");
            }
            EditorSceneManager.MarkSceneDirty(behaviour.gameObject.scene);
        }

        string head = seen == 0
            ? $"[TouchGrabFix] 열려 있는 씬에서 {TypeName} 를 찾지 못했다"
            : $"[TouchGrabFix] {TypeName} {seen}개 중 문제 {broken}개" +
              (repair ? $" — 참조 연결 {assigned}개, 비활성화 {disabled}개" : " (진단만 했다. 고치려면 '고치기' 메뉴)");
        Debug.Log(head + (report.Count > 0 ? "\n" + string.Join("\n", report) : ""));
    }

    /// <summary>같은 오브젝트 → 자식 → 위로 올라가며 형제까지, 가까운 순서로 Collider 를 찾는다.</summary>
    static Collider FindNearbyCollider(Transform start)
    {
        var self = start.GetComponent<Collider>();
        if (self != null) return self;

        var child = start.GetComponentInChildren<Collider>(true);
        if (child != null) return child;

        for (Transform t = start.parent; t != null; t = t.parent)
        {
            var near = t.GetComponentInChildren<Collider>(true);
            if (near != null) return near;
        }
        return null;
    }

    static string Path(Transform t)
    {
        string path = t.name;
        for (Transform p = t.parent; p != null; p = p.parent) path = p.name + "/" + path;
        return path;
    }
}
