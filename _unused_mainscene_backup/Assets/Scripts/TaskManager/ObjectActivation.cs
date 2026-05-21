using UnityEngine;
using System.Collections.Generic;

public class AnimationEventObjectController : MonoBehaviour
{
    // 정수 키와 오브젝트를 매핑한 딕셔너리
    public Dictionary<int, GameObject> targetObjectsDict = new Dictionary<int, GameObject>();

    // 유니티 인스펙터에서 설정할 수 있도록 리스트 사용
    [System.Serializable]
    public struct ObjectMapping
    {
        public int id; // 각 오브젝트를 구분할 정수 키
        public GameObject targetObject; // 활성화/비활성화할 오브젝트
    }

    public List<ObjectMapping> objectMappings = new List<ObjectMapping>();

    void Start()
    {
        // objectMappings 리스트를 targetObjectsDict 딕셔너리로 변환
        foreach (var mapping in objectMappings)
        {
            if (!targetObjectsDict.ContainsKey(mapping.id))
            {
                targetObjectsDict[mapping.id] = mapping.targetObject;
            }
            else
            {
                Debug.LogWarning($"ID {mapping.id}가 이미 존재합니다. 중복된 키는 무시됩니다.");
            }
        }
    }

    // 애니메이션 이벤트를 통해 오브젝트 활성화
    public void ActivateObject(int id)
    {
        if (targetObjectsDict.TryGetValue(id, out GameObject targetObject))
        {
            if (targetObject != null && !targetObject.activeInHierarchy)
            {
                targetObject.SetActive(true);
                Debug.Log($"{targetObject.name}가 활성화되었습니다.");
            }
        }
        else
        {
            Debug.LogWarning($"ID {id}에 해당하는 오브젝트가 없습니다.");
        }
    }

    // 애니메이션 이벤트를 통해 오브젝트 비활성화
    public void DeactivateObject(int id)
    {
        if (targetObjectsDict.TryGetValue(id, out GameObject targetObject))
        {
            if (targetObject != null && targetObject.activeInHierarchy)
            {
                targetObject.SetActive(false);
                Debug.Log($"{targetObject.name}가 비활성화되었습니다.");
            }
        }
        else
        {
            Debug.LogWarning($"ID {id}에 해당하는 오브젝트가 없습니다.");
        }
    }
}
