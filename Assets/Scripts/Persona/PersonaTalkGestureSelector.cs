using System;
using System.Collections.Generic;

/// <summary>답변 단위 추첨. 같은 답변의 음성 조각·재개는 재추첨하지 않고 연속 선택을 막는다.</summary>
public sealed class PersonaTalkGestureSelector
{
    readonly HashSet<string> _seen = new HashSet<string>(StringComparer.Ordinal);
    bool _hasResponse, _skipNext;

    /// <param name="rate">전체 발화 중 목표 비율. 연속 금지 때문에 최대 0.5다.</param>
    /// <param name="sample">0 이상 1 이하의 난수. 호출자가 공급해 검사도 재현 가능하게 한다.</param>
    public bool TrySelect(string responseId, float rate, float sample)
    {
        if (string.IsNullOrEmpty(responseId) || !_seen.Add(responseId)) return false;
        bool first = !_hasResponse;
        _hasResponse = true;
        if (_skipNext) { _skipNext = false; return false; }

        double target = float.IsNaN(rate) ? 0 : Math.Max(0, Math.Min(.5, rate));
        // 강제 건너뛰기까지 포함한 평균을 맞춘다. 25% 목표라면 일반 추첨은 1/3,
        // 바로 다음 발화는 0%다. 첫 발화는 목표 확률 그대로 시작한다.
        double chance = first ? target : target / (1 - target);
        bool selected = chance >= 1 || sample < chance;
        _skipNext = selected;
        return selected;
    }

    public void Reset()
    {
        _seen.Clear();
        _hasResponse = _skipNext = false;
    }
}
