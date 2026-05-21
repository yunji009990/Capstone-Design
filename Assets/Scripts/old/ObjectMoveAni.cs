using UnityEngine;

public class MoveEventController : MonoBehaviour
{
    public Animator animator;

    // ObjectMoveWatcher가 호출
    public void NotifyObjectMoved(GameObject obj)
    {
        Debug.Log($"[ObjectMoved] {obj.name} moved");

        if (animator != null)
        {
            animator.SetTrigger("ObjectMoved");
        }
    }
}
