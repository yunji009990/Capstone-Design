using UnityEngine;
using System.Collections;

public class AnimationTimer : MonoBehaviour
{
    public Animator animator;

    // 애니메이션 이벤트에서 호출
    public void PauseForSeconds(float seconds)
    {
        StartCoroutine(PauseCoroutine(seconds));
    }

    private IEnumerator PauseCoroutine(float seconds)
    {
        // 현재 상태 멈춤
        animator.speed = 0f;
        yield return new WaitForSeconds(seconds);
        // 다시 재생
        animator.speed = 1f;
    }
}
