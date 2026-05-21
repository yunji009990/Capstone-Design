using UnityEngine;
using Oculus.Interaction;

[RequireComponent(typeof(Grabbable))]
[RequireComponent(typeof(Rigidbody))]
public class GrabEventWatcher : MonoBehaviour
{
    private Grabbable grabbable;
    private Rigidbody rb;

    public MoveEventController controller;

    private bool grabbedOnce = false;

    void Awake()
    {
        grabbable = GetComponent<Grabbable>();
        rb = GetComponent<Rigidbody>();

        rb.isKinematic = true;
    }

    void OnEnable()
    {
        if (grabbable != null)
            grabbable.WhenPointerEventRaised += HandlePointerEvent;
    }

    // Grab 1회 감지
    private void HandlePointerEvent(PointerEvent evt)
    {
        if (grabbedOnce) return;

        if (evt.Type == PointerEventType.Select)
        {
            grabbedOnce = true;

            // 외부 컨트롤러에 알림
            if (controller != null)
            {
                rb.isKinematic = false;
                controller.NotifyObjectMoved(gameObject);
            }

            // 이후 Grab 이벤트 필요 없음
            grabbable.WhenPointerEventRaised -= HandlePointerEvent;
        }
    }
}
