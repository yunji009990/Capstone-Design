using UnityEngine;

public class TalkOneShot_OnExit : StateMachineBehaviour
{
    public override void OnStateExit(Animator animator, AnimatorStateInfo stateInfo, int layerIndex)
    {
        animator.SetFloat("TalkWeight", 0f);   // TalkLayer 끄기
    }
}
