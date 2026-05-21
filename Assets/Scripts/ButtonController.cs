using TMPro;
using UnityEngine;
using System.Collections;

public class ButtonController : MonoBehaviour
{
    [Header("Animator 연결")]
    public Animator animator;

    [Header("애니메이션 클립 순서 (Editor에서 자동 채움)")]
    public AnimationClip[] stateClips;


    [Header("오디오로부터 정답 판별 버튼 및 클립")]
    public GameObject AudioAnswerCollectBtn;
    public GameObject AudioAnswerWrongBtn;
    public AnimationClip[] AudioAnswerCollectClips;

    [Header("카메라 이동 Pivot")]
    public Transform cameraPivot;
    public float moveStep = 0.1f;     // 상하좌우 이동량
    public float forwardStep = 0.2f;  // 앞뒤 이동량

    private bool isPaused = false;

    // =========================
    // Rig / Tracking
    // =========================
    [Header("Rig / Tracking")]
    public Transform trackingSpace;      // OVRCameraRig의 TrackingSpace
    public Transform centerEye;          // CenterEyeAnchor (HMD)

    [Header("Hand Tracking")]
    public Transform rightHandAnchor;
    public Transform leftHandAnchor;
    public bool useRightHand = true;

    private Transform ActiveHand => useRightHand ? rightHandAnchor : leftHandAnchor;


    [Header("Calibration Target")]
    [Tooltip("손이 최종적으로 위치해야 할 기준 위치")]
    public Transform targetHandPoint;

    [Header("UI")]
    public Canvas VRCanvas;
    public TextMeshProUGUI messageText;

    [Header("Detection Tuning")]
    [Tooltip("손이 정면이라고 판단하는 Dot 기준")]
    public float minForwardDot = 0.6f;

    [Tooltip("팔을 뻗었다고 판단하는 최소 거리 (m)")]
    public float minExtendDistance = 0.35f;

    [Tooltip("조건 유지 시간 (초)")]
    public float holdSeconds = 3f;

    private bool isCalibrating = false;


    private void Update()
    {
        CheckConditionalButton();
    }

    private void CheckConditionalButton()
    {
        if (AudioAnswerCollectBtn == null || AudioAnswerWrongBtn == null || AudioAnswerCollectClips == null || AudioAnswerCollectClips.Length == 0 || animator == null) return;

        AnimationClip currentClip = GetCurrentClip();
        bool isTargetClipPlaying = false;

        foreach (var clip in AudioAnswerCollectClips)
        {
            if (clip == currentClip)
            {
                isTargetClipPlaying = true;
                break;
            }
        }
        
        if (AudioAnswerCollectBtn.activeSelf != isTargetClipPlaying)
        {
            AudioAnswerCollectBtn.SetActive(isTargetClipPlaying);
        }
        
        if (AudioAnswerWrongBtn.activeSelf != isTargetClipPlaying)
        {
            AudioAnswerWrongBtn.SetActive(isTargetClipPlaying);
        }
    }


    // ---------------------------------------------------
    // Start / Restart / Test
    // ---------------------------------------------------
    public void StartContent()
    {
        animator.SetTrigger("Start");
        Debug.Log("[Button] Start Trigger");
    }

    public void RestartContent()
    {
        if (stateClips == null || stateClips.Length == 0) return;

        // 첫 번째 애니메이션을 처음부터 강제 재생
        animator.Play(stateClips[0].name, 0, 0f);

        Debug.Log("[Button] Restart → " + stateClips[0].name);
    }
    public void TestContent()
    {
        animator.SetTrigger("Test");
        Debug.Log("[Button] Test Trigger");
    }

    // ---------------------------------------------------
    // Next / Prev (강제 State 이동)
    // ---------------------------------------------------
    public void NextClip()
    {
        int index = GetCurrentClipIndex();
        if (index < 0) return;

        index++;

        if (index >= stateClips.Length)
        {
            Debug.Log("최종 클립");
            return;
        }

        animator.Play(stateClips[index].name, 0, 0f);
        Debug.Log("[Button] Next → " + stateClips[index].name);
    }

    public void PrevClip()
    {
        int index = GetCurrentClipIndex();
        if (index < 0) return;

        index--;

        if (index < 0)
        {
            Debug.Log("최초 클립");
            return;
        }

        animator.Play(stateClips[index].name, 0, 0f);
        Debug.Log("[Button] Prev → " + stateClips[index].name);
    }

    // ---------------------------------------------------
    // Pause
    // ---------------------------------------------------
    public void PauseContent()
    {
        isPaused = !isPaused;
        Time.timeScale = isPaused ? 0f : 1f;
        Debug.Log(isPaused ? "[Button] Pause" : "[Button] Resume");
    }

    private AnimationClip GetCurrentClip()
    {
        if (animator == null) return null;

        var infos = animator.GetCurrentAnimatorClipInfo(0);
        if (infos.Length == 0) return null;

        return infos[0].clip;
    }

    private int GetCurrentClipIndex()
    {
        AnimationClip current = GetCurrentClip();
        if (current == null) return -1;

        for (int i = 0; i < stateClips.Length; i++)
        {
            if (stateClips[i] == current)
                return i;
        }

        return -1;
    }

    // ---------------------------------------------------
    // Camera MOVE (상하좌우 + 앞뒤 + 리센터)
    // ---------------------------------------------------
    public void Recenter()
    {
        if (cameraPivot == null) return;
        cameraPivot.localPosition = Vector3.zero;
        Debug.Log("[Button] Recenter");
    }

    public void MoveUp()
    {
        if (cameraPivot == null) return;
        cameraPivot.localPosition += Vector3.up * moveStep;
        Debug.Log("[Button] Move Up");
    }

    public void MoveDown()
    {
        if (cameraPivot == null) return;
        cameraPivot.localPosition += Vector3.down * moveStep;
        Debug.Log("[Button] Move Down");
    }

    public void MoveLeft()
    {
        if (cameraPivot == null) return;
        cameraPivot.localPosition += Vector3.left * moveStep;
        Debug.Log("[Button] Move Left");
    }

    public void MoveRight()
    {
        if (cameraPivot == null) return;
        cameraPivot.localPosition += Vector3.right * moveStep;
        Debug.Log("[Button] Move Right");
    }

    // ---------------------------------------------------
    // Forward / Backward (앞뒤 이동)
    // ---------------------------------------------------
    public void MoveForward()
    {
        if (cameraPivot == null) return;

        Vector3 forward = cameraPivot.forward;
        forward.y = 0;
        forward.Normalize();

        cameraPivot.position += forward * forwardStep;
        Debug.Log("[Button] Forward");
    }

    public void MoveBackward()
    {
        if (cameraPivot == null) return;

        Vector3 forward = cameraPivot.forward;
        forward.y = 0;
        forward.Normalize();

        cameraPivot.position -= forward * forwardStep;
        Debug.Log("[Button] Backward");
    }

    // =========================
    // 자리 자동 조정
    // =========================
    public void StartCalibration()
    {

        Debug.Log("[Calibration] StartCalibration called");
        if (isCalibrating) return;
        StartCoroutine(CalibrationRoutine());
    }

    private IEnumerator CalibrationRoutine()
    {
        if (!Validate()) yield break;

        isCalibrating = true;

        ShowUI("hand to forward");
        yield return WaitForConditionHold(IsHandExtended, holdSeconds);

        Vector3 handSnapshot = ActiveHand.transform.position;

        CalibrateYaw();
        CalibratePosition();

        HideUI();
        isCalibrating = false;
    }

    // =========================
    // Detection
    // =========================

    // 손 뻗음 인식
    private bool IsHandExtended()
    {
        if (ActiveHand == null || centerEye == null) return false;

        Vector3 handPos = ActiveHand.transform.position;
        Vector3 hmdPos = centerEye.position;

        Vector3 dir = handPos - hmdPos;
        float dist = dir.magnitude;
        if (dist < minExtendDistance) return false;

        dir.y = 0f;
        if (dir.sqrMagnitude < 0.0001f) return false;
        dir.Normalize();

        Vector3 forward = centerEye.forward;
        forward.y = 0f;
        forward.Normalize();

        float dot = Vector3.Dot(forward, dir);
        return dot >= minForwardDot;
    }

    // =========================
    // Calibration
    // =========================
    private void CalibrateYaw()
    {
        if (centerEye == null || trackingSpace == null) return;

        // HMD가 바라보는 방향 (수평)
        Vector3 forward = centerEye.forward;
        forward.y = 0f;

        if (forward.sqrMagnitude < 0.0001f) return;
        forward.Normalize();

        // 현재 TrackingSpace의 yaw
        float currentYaw = trackingSpace.eulerAngles.y;

        // HMD yaw
        float hmdYaw = Quaternion.LookRotation(forward).eulerAngles.y;

        // 차이만큼 TrackingSpace를 회전
        float deltaYaw = hmdYaw - currentYaw;

        trackingSpace.Rotate(Vector3.up, -deltaYaw, Space.World);

        Debug.Log($"[VR Calibration] Yaw 보정 완료: {deltaYaw}");
    }

    private void CalibratePosition()
    {
        if (!TryGetHandsCenter(out Vector3 handsCenter))
        {
            Debug.LogWarning("[VR Calibration] 손 좌표 누락");
            return;
        }

        Vector3 rigPos = trackingSpace.position;

        // Rig → 양손 중앙 오프셋
        Vector3 rigToHandsCenter = handsCenter - rigPos;

        // Rig의 목표 위치
        Vector3 desiredRigPos = targetHandPoint.position - rigToHandsCenter;

        trackingSpace.position = desiredRigPos;

        Debug.Log("[VR Calibration] 양손 중앙 기준 위치 보정 완료");
    }


    // =========================
    // Utility
    // =========================
    private IEnumerator WaitForConditionHold(System.Func<bool> condition, float seconds)
    {
        float timer = 0f;

        while (true)
        {
            if (condition())
            {
                timer += Time.deltaTime;
                if (timer >= seconds) yield break;
            }
            else
                timer = 0f;

            yield return null;
        }
    }

    private bool TryGetHandsCenter(out Vector3 center)
    {
        center = Vector3.zero;

        if (leftHandAnchor == null || rightHandAnchor == null)
            return false;

        center = (leftHandAnchor.position + rightHandAnchor.position) * 0.5f;
        return true;
    }

    private void ShowUI(string msg)
    {
        VRCanvas.gameObject.SetActive(true);
        messageText.text = msg;
    }

    private void HideUI()
    {
        VRCanvas.gameObject.SetActive(false);
    }

    private bool Validate()
    {
        if (!trackingSpace || !centerEye || ActiveHand == null)
        {
            Debug.LogWarning("[VR Calibration] Tracking Transform 누락");
            return false;
        }

        if (!targetHandPoint)
        {
            Debug.LogWarning("[VR Calibration] TargetHandPoint 누락");
            return false;
        }

        if (!VRCanvas || !messageText)
        {
            Debug.LogWarning("[VR Calibration] UI 누락");
            return false;
        }

        return true;
    }



    // ---------------------------------------------------
    // Exit
    // ---------------------------------------------------
    public void ExitContent()
    {
        Debug.Log("[Button] EXIT → Quit Application");
        Application.Quit();

#if UNITY_EDITOR
        UnityEditor.EditorApplication.isPlaying = false;
#endif
    }
}