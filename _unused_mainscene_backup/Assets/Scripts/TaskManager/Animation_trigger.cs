using System;
using System.Collections.Generic;
using UnityEngine;

public class Animation_trigger : MonoBehaviour
{
    [Header("전환할 Animator")]
    public Animator animator;

    [Header("전환 Trigger 파라미터 이름 (Animator)")]
    public string transitionTriggerName = "nextClip";

    [Header("클립-충돌 규칙 테이블")]
    public List<ClipCollisionRule> rules = new List<ClipCollisionRule>();

    private readonly Dictionary<Collider, CollisionRelay> _relays = new();

    [Serializable]
    public class ClipCollisionRule
    {
        [Tooltip("이 클립이 재생 중일 때만 발동")]
        public AnimationClip clip;

        [Tooltip("충돌 감지 객체 A (없으면 충돌 기반 비활성)")]
        public Collider objectA;

        [Tooltip("충돌 감지 객체 B (없으면 충돌 기반 비활성)")]
        public Collider objectB;

        [Tooltip("이 룰에서만 별도 Trigger를 쓰고 싶을 때")]
        public string overrideTriggerName = "";

        [Tooltip("룰 활성 여부")]
        public bool enabled = true;
    }



    private CollisionRelay GetOrAddRelay(Collider col)
    {
        if (_relays.TryGetValue(col, out var exist)) return exist;

        var relay = col.GetComponent<CollisionRelay>();
        if (!relay) relay = col.gameObject.AddComponent<CollisionRelay>();

        _relays[col] = relay;
        return relay;
    }

    private void OnColliderEntered(ClipCollisionRule rule, Collider self, Collider other)
    {
        if (!rule.enabled || animator == null) return;
        if (rule.objectA == null || rule.objectB == null) return;

        string playing = GetCurrentClip(animator);
        if (rule.clip == null || string.IsNullOrEmpty(playing)) return;

        // 현재 재생 중 클립 매칭
        if (!string.Equals(playing, rule.clip.name, StringComparison.Ordinal)) return;

        // 충돌 객체 매칭
        bool matched =
            (self == rule.objectA && other == rule.objectB) ||
            (self == rule.objectB && other == rule.objectA);

        if (!matched) return;

        string trig = string.IsNullOrEmpty(rule.overrideTriggerName)
            ? transitionTriggerName
            : rule.overrideTriggerName;

        animator.SetTrigger(trig);
        Debug.Log($"[ClipCollisionToNextState] '{playing}' 충돌 감지 → Trigger '{trig}' 발동");
    }

    // ★ AnimationEvent에서 직접 호출 가능
    public void TriggerNextClipFromEvent()
    {
        if (animator == null) return;

        animator.SetTrigger(transitionTriggerName);
        Debug.Log($"[AnimationEvent] Trigger '{transitionTriggerName}' 발동");
    }

    private static string GetCurrentClip(Animator anim)
    {
        var info = anim.GetCurrentAnimatorClipInfo(0);
        if (info.Length == 0 || info[0].clip == null) return null;
        return info[0].clip.name;
    }
}

[RequireComponent(typeof(Collider))]
public class CollisionRelay : MonoBehaviour
{
    public event Action<Collider> OnAnyEnter;

    void OnTriggerEnter(Collider other) => OnAnyEnter?.Invoke(other);

    void OnCollisionEnter(Collision c)
    {
        if (c != null && c.collider != null)
            OnAnyEnter?.Invoke(c.collider);
    }
}
