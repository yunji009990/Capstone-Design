// MMF_OVRHaptics.cs
// Feel 피드백을 하나 더한다 — Quest 컨트롤러 진동.
//
// Feel 에 실린 진동 피드백(Nice Vibrations)은 휴대폰용이라 Quest 컨트롤러는 울리지
// 않는다. 여기서는 OVRInput 으로 직접 울린다. 핸드 트래킹으로 체험 중이면 컨트롤러가
// 없으니 아무 일도 하지 않는다 — 그래도 되는 것이, 진동은 보조 신호고 빛이 주 신호다.
//
// MMF_Player 의 피드백 목록에서 "Haptics/Quest 컨트롤러 진동" 으로 고를 수 있다.

using System.Collections;
using MoreMountains.Feedbacks;
using UnityEngine;

[AddComponentMenu("")]
[FeedbackPath("Haptics/Quest 컨트롤러 진동")]
[FeedbackHelp("OVRInput 으로 Quest 컨트롤러를 짧게 울린다. 핸드 트래킹 중이면 아무 일도 하지 않는다.")]
public class MMF_OVRHaptics : MMF_Feedback
{
    /// <summary>이 종류의 피드백을 한꺼번에 끄는 스위치. Feel 의 관례.</summary>
    public static bool FeedbackTypeAuthorized = true;

#if UNITY_EDITOR
    public override Color FeedbackColor => MMFeedbacksInspectorColors.HapticsColor;
#endif
    public override float FeedbackDuration { get => ApplyTimeMultiplier(Duration); set => Duration = value; }

    [MMFInspectorGroup("Quest 진동", true, 40)]
    [Tooltip("울릴 컨트롤러. Touch 는 양손.")]
    public OVRInput.Controller Controller = OVRInput.Controller.Touch;
    [Tooltip("진동 주파수 0~1. 낮을수록 둔탁하다.")]
    [Range(0f, 1f)] public float Frequency = 0.3f;
    [Tooltip("진동 세기 0~1. 플레이어의 세기(FeedbacksIntensity)가 곱해진다.")]
    [Range(0f, 1f)] public float Amplitude = 0.25f;
    [Tooltip("울리는 시간(초).")]
    public float Duration = 0.08f;

    Coroutine _stop;

    protected override void CustomPlayFeedback(Vector3 position, float feedbacksIntensity = 1.0f)
    {
        if (!Active || !FeedbackTypeAuthorized) return;

        float amp = Mathf.Clamp01(Amplitude * ComputeIntensity(feedbacksIntensity, position));
        OVRInput.SetControllerVibration(Frequency, amp, Controller);

        if (_stop != null) Owner.StopCoroutine(_stop);
        _stop = Owner.StartCoroutine(StopAfter(FeedbackDuration));
    }

    IEnumerator StopAfter(float sec)
    {
        yield return WaitFor(sec);
        _stop = null;
        Silence();
    }

    protected override void CustomStopFeedback(Vector3 position, float feedbacksIntensity = 1.0f)
    {
        if (!Active) return;
        if (_stop != null) { Owner.StopCoroutine(_stop); _stop = null; }
        Silence();
    }

    void Silence() => OVRInput.SetControllerVibration(0f, 0f, Controller);
}
