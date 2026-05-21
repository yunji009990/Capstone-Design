using UnityEngine;
using System.Collections;

public class IdleBreathEvent : MonoBehaviour
{
    private Vector3 basePos;
    private Coroutine routine;

    void Start()
    {
        basePos = transform.localPosition;
    }

    // 애니메이션 이벤트에서 호출
    public void StartBreath(float duration)
    {
        if (routine != null) StopCoroutine(routine);
        routine = StartCoroutine(BreathRoutine(duration));
    }

    private IEnumerator BreathRoutine(float duration)
    {
        float timer = 0f;
        while (timer < duration)
        {
            timer += Time.deltaTime;
            float offset = Mathf.Sin(Time.time * 2f) * 0.02f; // 2Hz, 0.02 단위로 위아래
            transform.localPosition = basePos + new Vector3(0, offset, 0);
            yield return null;
        }
        transform.localPosition = basePos; // 원위치
    }
}
