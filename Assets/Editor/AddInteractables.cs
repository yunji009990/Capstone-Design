using Oculus.Interaction;
using Oculus.Interaction.Samples;
using UnityEditor;
using UnityEngine;

public class AddOVRInteractables : EditorWindow
{
    [MenuItem("Tools/Add OVR Interactables To Selected")]
    private static void ShowWindow()
    {
        GetWindow<AddOVRInteractables>("OVR Interactable Adder");
    }

    private void OnGUI()
    {
        if (GUILayout.Button("Add Interactables To Selected Objects"))
            AddComponentsToSelected();
    }

    private void AddComponentsToSelected()
    {
        foreach (GameObject go in Selection.gameObjects)
        {
            // 부모 오브젝트에 필수 컴포넌트
            if (!go.GetComponent<Rigidbody>())
                go.AddComponent<Rigidbody>();

            if (!go.GetComponent<Grabbable>())
                go.AddComponent<Grabbable>();

            if (!go.GetComponent<RespawnOnDrop>())
                go.AddComponent<RespawnOnDrop>();

            if (!go.GetComponent<RayReactor>())
                go.AddComponent<RayReactor>();



            // 부모의 Collider 목록
            Collider[] parentCols = go.GetComponentsInChildren<Collider>(true);
            Collider parentBounds = go.GetComponent<Collider>();   // 첫 번째 Collider를 Bounds로 사용
            Collider parentPoint = go.GetComponent<Collider>();   // 첫 번째 Collider를 Bounds로 사용

            // 자식 생성
            GameObject child = new GameObject("OVR_Interactable");
            child.transform.SetParent(go.transform);
            child.transform.localPosition = Vector3.zero;
            child.transform.localRotation = Quaternion.identity;
            child.transform.localScale = Vector3.one;

            // Touch Hand Grab Interactable 추가
            var grab = child.AddComponent<TouchHandGrabInteractable>();

            // Pointable Element: 부모 PointableElement 넣기
            var pointableField = grab.GetType().GetField("m_PointableElement",
                System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance);
            if (pointableField != null)
                pointableField.SetValue(grab, parentPoint);

            // Bounds Collider: 부모 Collider 한 개 넣기
            var boundsField = grab.GetType().GetField("m_BoundsCollider",
                System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance);
            if (boundsField != null)
                boundsField.SetValue(grab, parentBounds);

            // Colliders: 부모 Collider 배열 넣기
            var collidersField = grab.GetType().GetField("m_Colliders",
                System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance);
            if (collidersField != null)
                collidersField.SetValue(grab, parentCols);


            EditorUtility.SetDirty(go);
            EditorUtility.SetDirty(child);
        }

        AssetDatabase.SaveAssets();
    }
}
