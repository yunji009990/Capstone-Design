using UnityEngine;

public class TriggerTalkOnExit : StateMachineBehaviour
{
    public override void OnStateExit(Animator animator, AnimatorStateInfo stateInfo, int layerIndex)
    {
        animator.SetFloat("TalkWeight", 1f);   // TalkLayer 켜기
    }
}
