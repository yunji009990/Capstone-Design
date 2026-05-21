#if UNITY_EDITOR
using UnityEngine;
using UnityEditor;
using UnityEditor.Animations;

[CustomEditor(typeof(ButtonController))]
public class AutoClipLoader : Editor
{
    public override void OnInspectorGUI()
    {
        DrawDefaultInspector();

        ButtonController bc = (ButtonController)target;

        if (GUILayout.Button("Animator에서 클립 자동 로드"))
        {
            if (bc.animator == null)
            {
                Debug.LogError("Animator가 비어있음");
                return;
            }

            AnimatorController ac = bc.animator.runtimeAnimatorController as AnimatorController;

            if (ac == null)
            {
                Debug.LogError("AnimatorController를 찾지 못함");
                return;
            }

            var states = ac.layers[0].stateMachine.states;

            AnimationClip[] clips = new AnimationClip[states.Length];

            for (int i = 0; i < states.Length; i++)
            {
                Motion m = states[i].state.motion;
                if (m is AnimationClip clip)
                {
                    clips[i] = clip;
                }
            }

            bc.stateClips = clips;

            EditorUtility.SetDirty(bc);
            Debug.Log("클립 자동 로드 완료 (" + clips.Length + " 개)");
        }
    }
}
#endif
