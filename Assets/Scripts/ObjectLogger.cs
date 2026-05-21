using UnityEngine;

public class ObjectLogger : MonoBehaviour
{
    private void OnTriggerEnter(Collider other)
    {
        string triggerName = this.gameObject.name;     // 트리거 오브젝트 이름
        string incomingName = other.gameObject.name;   // 들어온 오브젝트 이름

        Debug.Log($"트리거: {triggerName} | 들어온 오브젝트: {incomingName}");
    }
}
