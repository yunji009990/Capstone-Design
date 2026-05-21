using UnityEngine;

// 해당 스크립트는 'LeftEye'와 'rightEye' 자식 오브젝트를 가진 부모 오브젝트에 적용되어야 합니다.
// This class must be connected to 'Eyes' Component, which is containing left and right eye.

/*
 * EyeRaycaster는 LeftEye와 RightEye를 가지고 있는 부모 오브젝트에 적용되는 오브젝트입니다.
 * 이 클래스는 사용자 눈 위치에서 앞의 방향으로 레이를 발사하여 발사된 레이가 Border 오브젝트 또는 다른 오브젝트에 부딫힐 때 부딫힌 위치를 사용자가 바라보고 있는 위치로 판단하여 저장합니다.
 * EyeRaycaster is parent object which is containing LeftEye and RightEye objects.
 * In this class, a ray is fired from user's eye location to foward direction and if ray is hit object like Border or something, it determines that user is gazing this position and save that position.
 */
public class EyeRaycaster : MonoBehaviour
{
    [Tooltip("Reference frame for eye. " +
             "Reference frame should be set in the forward direction of the eye. It is there to calculate the initial offset of the eye GameObject. " +
             "If it's null, then world reference frame will be used.")]
    public Transform ReferenceFrame;

    [SerializeField]
    [Tooltip("Layers to be detected.")]
    private LayerMask layersToInclude; // 레이 발사 후 부딫힐 오브젝트에 대한 범위.  Hit object range.


    private Vector3 gazingPoint; // 유저의 양쪽눈이 바라보고 있는 위치의 중심점.  The center point of users' left and right gazing position.
    private Vector3 leftGazingPoint; // 유저의 왼쪽눈이 바라보고 있는 위치.  Point of user's left gazing point.
    private Vector3 rightGazingPoint; // 유저의 오른쪽눈이 바라보고 있는 위치.  Point of user's right gazing point.

    private Transform leftEye; // User's left eye object.
    private Transform rightEye; // User's right eye object.

    private void Start()
    {
        // 자식 요소 중 LeftEye와 RightEye를 가져옴.
        // Get left eye and right eye from child objects.
        for (int i = 0; i < 2; i++)
        {
            if (transform.GetChild(i).GetComponent<OVREyeGaze>().Eye == OVREyeGaze.EyeId.Left) // Left eye
            {
                leftEye = transform.GetChild(i);
                leftEye.GetComponent<OVREyeGaze>().ReferenceFrame = ReferenceFrame;
            }
            else // Right eye
            {
                rightEye = transform.GetChild(i);
                rightEye.GetComponent<OVREyeGaze>().ReferenceFrame = ReferenceFrame;
            }
        }
    }

    void FixedUpdate()
    {
        RaycastHit hit; // 레이가 부딫힌 오브젝트가 hit에 저장됨.  A hit object will be saved in hit.

        // 각 눈의 방향을 저장함.  Get direction of each eye.
        Vector3 leftEyeGazingDirection = leftEye.transform.TransformDirection(Vector3.forward);
        Vector3 rightEyeGazingDirection = rightEye.transform.TransformDirection(Vector3.forward);

        // 사용자의 각 눈의 위치에서 앞쪽으로 레이를 발사하여 부딫힌 좌표를 gazingPoint에 저장.
        // Fire a ray to forward direction from user's eye location and save hit position into gazingPoint.
        if (Physics.Raycast(leftEye.position, leftEyeGazingDirection, out hit, Mathf.Infinity, layersToInclude)) // left eye
            leftGazingPoint = hit.point;
        if (Physics.Raycast(rightEye.position, rightEyeGazingDirection, out hit, Mathf.Infinity, layersToInclude)) // right eye
            rightGazingPoint = hit.point;
        gazingPoint = GetMiddlePoint(leftGazingPoint, rightGazingPoint); // 두 눈의 gazingPoint의 중심점 계산.   Calculate middle point between left and right gazing point.
    }

    // 두 벡터 간의 중심점 계산.
    // Calculate middle point from two points.
    Vector3 GetMiddlePoint(Vector3 vec1, Vector3 vec2)
    {
        return new Vector3((vec1.x + vec2.x) / 2, (vec1.y + vec2.y) / 2, (vec1.z + vec2.z) / 2);
    }

    public Vector3 GazingPoint { get { return gazingPoint; } }
    public Vector3 LeftGazingPoint { get { return leftGazingPoint; } }
    public Vector3 RightGazingPoint { get { return rightGazingPoint; } }
}
