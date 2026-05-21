using UnityEngine;

public class ObjectMoveWatcher : MonoBehaviour
{
    public MoveEventController controller;     // 중앙 컨트롤러
    public float threshold = 10f;              // ±10 기준
    private Vector3 initialPos;                // 초기 기준 위치
    private bool moved = false;                // 중복 트리거 방지

    void Start()
    {
        initialPos = transform.position;
    }

    void Update()
    {
        if (controller == null || moved) return;

        Vector3 current = transform.position;

        float dx = Mathf.Abs(current.x - initialPos.x);
        float dy = Mathf.Abs(current.y - initialPos.y);
        float dz = Mathf.Abs(current.z - initialPos.z);

        // 하나라도 ±10 이상이면 이동으로 간주
        if (dx >= threshold || dy >= threshold || dz >= threshold)
        {
            moved = true;
            controller.NotifyObjectMoved(gameObject);
        }
    }

    // 이동 감시를 다시 시작해야 할 때 호출
    public void ResetWatcher()
    {
        initialPos = transform.position;
        moved = false;
    }
}
