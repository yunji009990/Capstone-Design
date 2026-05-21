using UnityEngine;

public class AnimationLogger : StateMachineBehaviour
{
    // 애니메이션 상태가 시작될 때 호출되는 함수
    override public void OnStateEnter(Animator animator, AnimatorStateInfo stateInfo, int layerIndex)
    {
        // 현재 실행 중인 애니메이션 클립 정보 가져오기
        AnimatorClipInfo[] clipInfo = animator.GetCurrentAnimatorClipInfo(layerIndex);

        if (clipInfo.Length > 0)
        {
            // 애니메이션 클립의 이름 출력
            Debug.Log("애니메이션 클립 이름: " + clipInfo[0].clip.name + "완료");
        }
        else
        {
            Debug.Log("애니메이션 클립 정보를 가져올 수 없습니다.");
        }
    }
}
