// VRMoveControl.cs
// 체험자의 자리를 운영자가 옮겨 준다.
//
// 헤드셋을 쓴 사람은 자기가 방 어디에 서 있는지 스스로 맞추기 어렵다. 특히 앉아서
// 체험할 때 인물과 눈높이가 안 맞으면 옆에서 조정해 줘야 한다.
//
// MainScene 의 ButtonController 에 같은 기능이 있었지만 그 스크립트는 441줄에
// 애니메이션·과제 흐름·CSV 내보내기까지 얽혀 있다. 이동에 필요한 것만 옮겼다.

using UnityEngine;

[DisallowMultipleComponent]
public class VRMoveControl : MonoBehaviour
{
    [Tooltip("옮길 대상. 보통 OVRCameraRig 를 들고 있는 부모다. 비워두면 씬에서 찾는다.")]
    public Transform pivot;

    [Tooltip("좌우·상하 한 번에 움직일 거리(m).")]
    public float step = 0.1f;

    [Tooltip("앞뒤로 한 번에 움직일 거리(m).")]
    public float forwardStep = 0.2f;

    Vector3 _home;
    bool _hasHome;

    void Awake()
    {
        if (pivot == null)
        {
            // OVRCameraRig 를 들고 있는 것이 곧 사람의 자리다.
            var rig = GameObject.Find("OVRCameraRig");
            if (rig != null && rig.transform.parent != null) pivot = rig.transform.parent;
            else if (rig != null) pivot = rig.transform;
        }
        if (pivot != null) { _home = pivot.localPosition; _hasHome = true; }
    }

    /// <summary>처음 자리로 되돌린다. 이리저리 옮기다 길을 잃었을 때 쓴다.</summary>
    public void Recenter()
    {
        if (pivot != null && _hasHome) pivot.localPosition = _home;
    }

    public void MoveUp()      => Shift(Vector3.up * step);
    public void MoveDown()    => Shift(Vector3.down * step);
    public void MoveLeft()    => Shift(Vector3.left * step);
    public void MoveRight()   => Shift(Vector3.right * step);

    /// <summary>보고 있는 쪽으로 나아간다. 위아래로는 안 움직인다 — 바닥을 뚫거나 뜬다.</summary>
    public void MoveForward()  => Advance(forwardStep);
    public void MoveBackward() => Advance(-forwardStep);

    void Shift(Vector3 delta)
    {
        if (pivot != null) pivot.localPosition += delta;
    }

    void Advance(float amount)
    {
        if (pivot == null) return;
        Vector3 f = pivot.forward;
        f.y = 0f;
        if (f.sqrMagnitude < 0.0001f) return;
        pivot.position += f.normalized * amount;
    }
}
