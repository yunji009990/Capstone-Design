using Oculus.Interaction.HandGrab;
using Oculus.Interaction.Input;
using System.Collections.Generic;
using UnityEngine;

/*
 * HandObserver는 양손의 위치와 어떤 물체를 잡고있는지, 잡고있다면 어떤 제스처로 잡고 있는지에 대한 정보를 저장합니다.
 * HandObserver saves position of both hands and which object user is holding and if user is holding something, which gesture is being used.
 */
public class HandObserver : MonoBehaviour
{
    [SerializeField]
    [Tooltip("Left hand GameObject")]
    private GameObject leftHand;

    [SerializeField]
    [Tooltip("Right hand GameObject")]
    private GameObject rightHand;

    [SerializeField]
    private HandGrabInteractor leftHandInteractor;
    private IHand lHand; // VR기기에서 왼손이 탐지되었는지를 확인할 손 클래스.  Hand class used for detecting whether user's left hand is dected in vr device.

    [SerializeField]
    private HandGrabInteractor rightHandInteractor;
    private IHand rHand; // VR기기에서 오른손이 탐지되었는지를 확인할 손 클래스.   Hand class used for detecting whether user's right hand is dected in vr device.

    [Tooltip("Show hands pointer in gui.")]
    public bool showHandsPointer = false;

    [SerializeField]
    [Tooltip("Texture for hands pointer.")]
    private Texture pointerTexture;

    private Camera observerCamera; // 화면 내 손의 위치를 나타내기 위해 필요한 카메라.  Camera object for determining hands location in the screen.

    private List<string> colnames = new List<string> { "l_hand_x", "l_hand_y", "r_hand_x", "r_hand_y", "l_hand_hld", "l_hand_gest", "r_hand_hld", "r_hand_gest" }; // csv에 저장할 열 이름. column names 
    private List<string> csvData = new List<string> { "0.0", "0.0", "0.0", "0.0", "None", "None", "None", "None" };

    // 시선 위치나 바운딩 박스의 위치를 0 ~ 1 크기로 정규화 하기 위한 실제 화면 크기.
    // Screen size to regularizing gazing position and bounding box position to 0 ~ 1.
    private int screenWidth;
    private int screentHeight;

    // Start is called before the first frame update
    void Start()
    {
        // Initialize
        observerCamera = GetComponent<Camera>();

        lHand = leftHandInteractor.Hand;
        rHand = rightHandInteractor.Hand;

        screenWidth = Screen.width;
        screentHeight = Screen.height;
    }

    private void OnGUI()
    {
        // 양손의 위치를 화면상 좌표로 변환하여 저장.
        // Get screen point of left and right hand.
        Vector2 screenLeftHandPoint = WorldPointToScreenPoint(leftHand.transform.position);
        Vector2 screenRightHandPoint = WorldPointToScreenPoint(rightHand.transform.position);

        if (showHandsPointer)
        {
            if (lHand.IsConnected)
                GUI.DrawTexture(new Rect(screenLeftHandPoint.x - 15f, screenLeftHandPoint.y - 15f, 30f, 30f), pointerTexture);
            if (rHand.IsConnected)
                GUI.DrawTexture(new Rect(screenRightHandPoint.x - 15f, screenRightHandPoint.y - 15f, 30f, 30f), pointerTexture);
        }

        // CSV 데이터 저장.
        // Save csv data.
        csvData[0] = lHand.IsConnected ? (screenLeftHandPoint.x / screenWidth).ToString() : "0.0";
        csvData[1] = lHand.IsConnected ? (screenLeftHandPoint.y / screentHeight).ToString() : "0.0";
        csvData[2] = rHand.IsConnected ? (screenRightHandPoint.x / screenWidth).ToString() : "0.0";
        csvData[3] = rHand.IsConnected ? (screenRightHandPoint.y / screentHeight).ToString() : "0.0";
        //csvData[4] = lHand.IsConnected && leftHandInteractor.IsGrabbing ? leftHandInteractor.SelectedInteractable.GetComponent<RayReactor>().objectName : "None"; // 왼쪽손이 잡고 있는 오브젝트 이름.  Object name which is holding by user's left hand.
        //csvData[5] = lHand.IsConnected && leftHandInteractor.IsGrabbing ? leftHandInteractor.HandGrabTarget.Anchor.ToString() : "None"; // 왼쪽손이 어떤 오브젝트를 잡고있을 때, 해당되는 제스처 타입.  Gesture type if user's left hand is holding some object.
        //csvData[6] = rHand.IsConnected && rightHandInteractor.IsGrabbing ? rightHandInteractor.SelectedInteractable.GetComponent<RayReactor>().objectName : "None"; // 오른쪽손이 잡고 있는 오브젝트 이름.  Object name which is holding by user's right hand.
        //csvData[7] = rHand.IsConnected && rightHandInteractor.IsGrabbing ? rightHandInteractor.HandGrabTarget.Anchor.ToString() : "None"; // 오른쪽손이 어떤 오브젝트를 잡고있을 때, 해당되는 제스처 타입.  Gesture type if user's right hand is holding some object.
    }

    // 3차원 좌표를 화면상의 2차원 좌표로 변환.
    // Convert 3-d coordinate value to screen 2-d.
    private Vector3 WorldPointToScreenPoint(Vector3 worldPoint)
    {
        Vector3 screenPoint = observerCamera.WorldToScreenPoint(worldPoint);
        screenPoint.y = Screen.height - screenPoint.y;
        return screenPoint;
    }

    public string[] GetColumnNames()
    {
        return colnames.ToArray();
    }

    public string[] GetCSVData()
    {
        return csvData.ToArray();
    }
}
