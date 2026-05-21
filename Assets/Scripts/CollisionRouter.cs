using UnityEngine;

public class CollisionRouter : MonoBehaviour
{
    public AnswerCheckController controller;

    private void OnTriggerEnter(Collider other)
    {
        if (controller == null) return;
        controller.NotifyTriggerEnter(GetComponent<Collider>(), other.gameObject);
    }

    private void OnTriggerExit(Collider other)
    {
        if (controller == null) return;
        controller.NotifyTriggerExit(GetComponent<Collider>(), other.gameObject);
    }
}
