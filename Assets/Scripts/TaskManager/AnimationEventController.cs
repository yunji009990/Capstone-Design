using Oculus.Interaction;
using System.Collections;
using System.Collections.Generic;
using UnityEngine;

public class AnimationEventController : MonoBehaviour
{
    // Grab_L 위치
    public Transform grabPosition;

    // 제어 대상
    public List<GameObject> targetObjects = new List<GameObject>();

    // Animator (NPC 또는 이 스크립트가 붙은 오브젝트의 Animator)
    public Animator npcAnimator;   

    // 이동 방식
    [SerializeField] private bool smoothMoveOnRelease = false;
    [SerializeField] private float moveDuration = 0.25f;

    // 상태 플래그
    private Dictionary<GameObject, bool> isGrabbed = new Dictionary<GameObject, bool>();
    private Dictionary<GameObject, bool> hasFallen = new Dictionary<GameObject, bool>();

    void Start()
    {
        foreach (GameObject obj in targetObjects)
        {
            if (obj == null) continue;
            isGrabbed[obj] = false;
            hasFallen[obj] = false;
        }
    }

    // 애니메이션 이벤트에서 호출: 특정 오브젝트를 Grab_L로 이동
    public void MoveObjectToGrabLByIndex(int index)
    {
        if (grabPosition == null)
        {
            Debug.LogWarning("Grab_L 위치가 설정되지 않았습니다.");
            return;
        }
        if (index < 0 || index >= targetObjects.Count)
        {
            Debug.LogWarning("유효하지 않은 오브젝트 인덱스입니다.");
            return;
        }

        var targetObject = targetObjects[index];
        if (targetObject == null) return;

        targetObject.transform.SetPositionAndRotation(grabPosition.position, grabPosition.rotation);
        isGrabbed[targetObject] = true;
        Debug.Log($"{targetObject.name}가 Grab_L로 이동되었습니다.");
    }

    // 애니메이션 이벤트에서 호출: 떨어짐 표시
    public void OnObjectFallen(int index)
    {
        if (index < 0 || index >= targetObjects.Count)
        {
            Debug.LogWarning("유효하지 않은 오브젝트 인덱스입니다.");
            return;
        }
        var targetObject = targetObjects[index];
        if (targetObject == null) return;

        hasFallen[targetObject] = true;
        Debug.Log($"{targetObject.name}가 떨어졌습니다.");
    }

    void Update()
    {
        if (grabPosition == null) return;

        // Grab_L에 고정 유지
        foreach (GameObject targetObject in targetObjects)
        {
            if (targetObject == null) continue;
            if (isGrabbed.TryGetValue(targetObject, out bool grabbed) && grabbed)
            {
                targetObject.transform.SetPositionAndRotation(grabPosition.position, grabPosition.rotation);
            }
        }
    }

    // 릴리즈: Grab_L 해제 후 목표 위치로 이동
    public void ReleaseObject(int index)
    {
        if (index < 0 || index >= targetObjects.Count)
        {
            Debug.LogWarning("유효하지 않은 오브젝트 인덱스입니다.");
            return;
        }

        var targetObject = targetObjects[index];
        if (targetObject == null) return;

        if (isGrabbed.ContainsKey(targetObject))
        {
            isGrabbed[targetObject] = false;
            Debug.Log($"{targetObject.name}가 Grab_L에서 해제되었습니다.");
        }

        // 목표 위치 결정
        Transform goal = null;

        var release = targetObject.GetComponent<ReleaseTarget>();
        if (release == null || release.releasePoint == null) return;

        goal = release.releasePoint;

        // 이동 실행
        if (smoothMoveOnRelease)
            StartCoroutine(MoveTo(targetObject.transform, goal.position, goal.rotation, moveDuration));
        else
            SnapTo(targetObject.transform, goal.position, goal.rotation);
    }

    // 애니메이션 이벤트에서 직접 목표로 보내고 싶을 때 사용 가능
    public void MoveReleasedObjectToTargetByIndex(int index)
    {
        ReleaseObject(index);
        Debug.Log("Grab_point로 위치 이동 완료");
    }

    // 즉시 스냅
    private void SnapTo(Transform tr, Vector3 pos, Quaternion rot)
    {
        var rb = tr.GetComponent<Rigidbody>();
        if (rb)
        {
            rb.velocity = Vector3.zero;
            rb.angularVelocity = Vector3.zero;
            // 순간 이동 시 충돌 안정화
            rb.MovePosition(pos);
            rb.MoveRotation(rot);
        }
        else
        {
            tr.SetPositionAndRotation(pos, rot);
        }
    }

    // 부드럽게 이동
    private IEnumerator MoveTo(Transform tr, Vector3 pos, Quaternion rot, float dur)
    {
        var rb = tr.GetComponent<Rigidbody>();
        bool hadRb = rb != null;
        bool prevKinematic = false;
        Vector3 prevVel = Vector3.zero;
        Vector3 prevAngVel = Vector3.zero;

        if (hadRb)
        {
            prevKinematic = rb.isKinematic;
            prevVel = rb.velocity;
            prevAngVel = rb.angularVelocity;
            rb.isKinematic = true;
            rb.velocity = Vector3.zero;
            rb.angularVelocity = Vector3.zero;
        }

        Vector3 startPos = tr.position;
        Quaternion startRot = tr.rotation;
        float t = 0f;
        float durSafe = Mathf.Max(0.0001f, dur);

        while (t < 1f)
        {
            t += Time.deltaTime / durSafe;
            tr.position = Vector3.Lerp(startPos, pos, t);
            tr.rotation = Quaternion.Slerp(startRot, rot, t);
            yield return null;
        }

        if (hadRb)
        {
            rb.isKinematic = prevKinematic;
            rb.velocity = prevVel;
            rb.angularVelocity = prevAngVel;
        }
    }

    public void DeactivateObject(int index)
    {
        if (index < 0 || index >= targetObjects.Count) return;

        GameObject obj = targetObjects[index];
        if (obj == null) return;

        // meshRenderer OFF
        foreach (var r in obj.GetComponentsInChildren<MeshRenderer>(true))
            r.enabled = false;

        // Collider OFF
        foreach (var c in obj.GetComponentsInChildren<Collider>(true))
            c.enabled = false;

        Debug.Log($"[DeactivateObject] {obj.name} Renderer OFF + Collider OFF");
    }

    public void ActivateObject(int index)
    {
        if (index < 0 || index >= targetObjects.Count) return;

        GameObject obj = targetObjects[index];
        if (obj == null) return;

        // meshRenderer ON
        foreach (var r in obj.GetComponentsInChildren<MeshRenderer>(true))
            r.enabled = true;

        // Collider ON
        foreach (var c in obj.GetComponentsInChildren<Collider>(true))
            c.enabled = true;

        Debug.Log($"[ActivateObject] {obj.name} Renderer ON + Collider ON");
    }


}
