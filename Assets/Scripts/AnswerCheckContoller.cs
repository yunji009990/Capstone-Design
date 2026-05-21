using UnityEngine;
using System.Collections.Generic;

public class AnswerCheckController : MonoBehaviour
{
    [System.Serializable]
    public class ClipAnswerRule
    {
        public Collider passTrigger;        // Top Collider (Trigger)
        public GameObject correctObject;    // 통과해야 하는 오브젝트
        // nextClipTrigger = "nextClip" 고정
    }

    [System.Serializable]
    public class ClipRuleSet
    {
        public AnimationClip clip;
        public ClipAnswerRule[] rules;
    }

    // Inspector 설정
    public Animator animator;
    public ClipRuleSet[] clipRuleSets;

    // 이미 통과했는지 여부
    private bool clipPassed = false;
    private AnimationClip lastClip = null;

    // 상태저장: "현재 어떤 오브젝트가 트리거 안에 있는가"
    private Dictionary<(Collider, GameObject), bool> insideState  = new Dictionary<(Collider, GameObject), bool>();

    [Header("Conditional Button Settings")]
    public GameObject AudioAnswerCollectBtn;
    public AnimationClip[] AudioAnswerCollectClips;


    // Router가 호출함
    public void NotifyTriggerEnter(Collider trigger, GameObject obj)
    {
        insideState[(trigger, obj)] = true;
    }

    public void NotifyTriggerExit(Collider trigger, GameObject obj)
    {
        // Exit가 발생하면 "통과 완료"로 간주
        if (insideState.ContainsKey((trigger, obj)) && insideState[(trigger, obj)] == true)
        {
            insideState[(trigger, obj)] = false;  // 상태 초기화
            EvaluatePass(trigger, obj);           // "통과" 판정
        }
    }

    private void Update()
    {
        // 애니메이션 클립 변경 감지
        var clip = GetCurrentClip();
        if (clip != lastClip)
        {
            lastClip = clip;
            clipPassed = false; // 새 클립 시작 → 초기화
        }

    }


    // 통과 판정
    private void EvaluatePass(Collider trigger, GameObject obj)
    {
        if (clipPassed) return;

        AnimationClip currentClip = GetCurrentClip();
        if (currentClip == null) return;

        foreach (var set in clipRuleSets)
        {
            if (set.clip != currentClip) continue;

            foreach (var rule in set.rules)
            {
                if (rule.passTrigger == trigger && rule.correctObject == obj)
                {
                    clipPassed = true;
                    Debug.Log($"[Correct PASS] Clip:{currentClip.name} | Trigger:{trigger.name} | Obj:{obj.name}");
                    animator.SetTrigger("nextClip");
                    return;
                }
            }

            Debug.Log($"[Incorrect PASS] Clip:{currentClip.name} | Trigger:{trigger.name} | Obj:{obj.name}");
        }
    }

    //오디오 기반 정답 처리 버튼 연결 이벤트
    //후에 데이터 분석을 위해 정답/오답 분리
    public void AnswerCollectBtn()
    {
        animator.SetTrigger("nextClip");
    }

    public void AnswerWrongBtn()
    {
        animator.SetTrigger("nextClip");
    }


    public AnimationClip GetCurrentClip()
    {
        var info = animator.GetCurrentAnimatorClipInfo(0);
        if (info.Length == 0) return null;
        return info[0].clip;
    }
}
