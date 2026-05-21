#if UNITY_EDITOR
using UnityEngine;
using UnityEditor;
using UnityEditor.Animations;

public class AnimatorDebugger : MonoBehaviour
{
    public Animator animator;

    [ContextMenu("Print All Animator States & Transitions")]
    public void PrintAll()
    {
        if (animator == null)
        {
            Debug.LogError("Animator가 연결되지 않음");
            return;
        }

        AnimatorController ac = animator.runtimeAnimatorController as AnimatorController;
        if (ac == null)
        {
            Debug.LogError("AnimatorController를 찾을 수 없음");
            return;
        }

        Debug.Log("=== Animator States (Layer 0) ===");
        var sm = ac.layers[0].stateMachine;

        foreach (var s in sm.states)
        {
            Debug.Log("State: " + s.state.name);

            foreach (var t in s.state.transitions)
            {
                Debug.Log("  → Transition: " + t.name);
                foreach (var c in t.conditions)
                {
                    Debug.Log("      Condition: " + c.parameter + " (" + c.mode + ")");
                }
            }
        }

        Debug.Log("=== Any State Transitions ===");
        foreach (var t in sm.anyStateTransitions)
        {
            Debug.Log("Any State → " + t.destinationState.name);
            foreach (var c in t.conditions)
            {
                Debug.Log("    Condition: " + c.parameter + " (" + c.mode + ")");
            }
        }

        Debug.Log("=== Animator Parameters ===");
        foreach (var p in animator.parameters)
        {
            Debug.Log("Parameter: " + p.name + " (" + p.type + ")");
        }
    }
}
#endif
