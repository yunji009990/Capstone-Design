using System.Collections.Generic;
using UnityEngine;

[System.Serializable]
public class BoxCondition
{
    public TriggerProxy triggerProxy;
    public List<GameObject> requiredBalls = new List<GameObject>();
}

[System.Serializable]
public class ClipBoxRuleSet
{
    public AnimationClip clip;
    public BoxCondition[] boxes;
}

public class BallCountChecker : MonoBehaviour
{
    AnswerCheckController answerCheckController;

    [Header("Clip별 상자 조건")]
    public ClipBoxRuleSet[] clipBoxRuleSets;

    [Header("옵션")]
    public bool triggerOnlyOnce = true;

    private Dictionary<TriggerProxy, HashSet<GameObject>> boxBallsMap = new();
    private bool hasTriggered = false;

    private void Start()
    {
        answerCheckController = FindObjectOfType<AnswerCheckController>();

        foreach (var clipSet in clipBoxRuleSets)
        {
            if (clipSet.boxes == null) continue;

            foreach (var box in clipSet.boxes)
            {
                if (box.triggerProxy != null && !boxBallsMap.ContainsKey(box.triggerProxy))
                {
                    boxBallsMap[box.triggerProxy] = new HashSet<GameObject>();
                    box.triggerProxy.SetEventCheck(this);
                }
            }
        }
    }

    // =========================
    // 공 들어옴
    // =========================
    public void OnBallEnter(TriggerProxy triggerProxy, GameObject ball)
    {
        AnimationClip currentClip = answerCheckController.GetCurrentClip();
        if (currentClip == null) return;

        BoxCondition condition = GetBoxCondition(currentClip, triggerProxy);
        if (condition == null) return;

        Debug.Log($"[공 진입] 공:{ball.name} / 상자:{triggerProxy.name} / 클립:{currentClip.name}");

        if (!condition.requiredBalls.Contains(ball))
        {
            Debug.Log("[무시] 이 상자에 필요 없는 공");
            return;
        }

        boxBallsMap[triggerProxy].Add(ball);

        CheckCondition(currentClip);
    }

    // =========================
    // 공 나감
    // =========================
    public void OnBallExit(TriggerProxy triggerProxy, GameObject ball)
    {
        if (!boxBallsMap.ContainsKey(triggerProxy))
            return;

        boxBallsMap[triggerProxy].Remove(ball);

        if (!triggerOnlyOnce)
            hasTriggered = false;
    }

    // =========================
    // 조건 검사
    // =========================
    private void CheckCondition(AnimationClip currentClip)
    {
        if (hasTriggered && triggerOnlyOnce)
            return;

        ClipBoxRuleSet ruleSet = GetClipBoxRuleSet(currentClip);
        if (ruleSet == null) return;

        foreach (var box in ruleSet.boxes)
        {
            foreach (var requiredBall in box.requiredBalls)
            {
                if (!boxBallsMap[box.triggerProxy].Contains(requiredBall))
                {
                    Debug.Log($"[실패] 부족한 공: {requiredBall.name}");
                    return;
                }
            }
        }

        hasTriggered = true;
        Debug.Log($"[성공] 모든 조건 충족 → 다음 클립 ({currentClip.name})");

        answerCheckController.animator.SetTrigger("nextClip");
    }

    private ClipBoxRuleSet GetClipBoxRuleSet(AnimationClip clip)
    {
        foreach (var ruleSet in clipBoxRuleSets)
            if (ruleSet.clip == clip)
                return ruleSet;
        return null;
    }

    private BoxCondition GetBoxCondition(AnimationClip clip, TriggerProxy triggerProxy)
    {
        ClipBoxRuleSet ruleSet = GetClipBoxRuleSet(clip);
        if (ruleSet == null) return null;

        foreach (var box in ruleSet.boxes)
            if (box.triggerProxy == triggerProxy)
                return box;

        return null;
    }

    public void ResetState()
    {
        hasTriggered = false;
        foreach (var set in boxBallsMap.Values)
            set.Clear();
    }
}
