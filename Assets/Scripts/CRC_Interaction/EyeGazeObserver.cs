// EyeGazeObserver.cs
using System.Collections.Generic;
using System.Linq;
using UnityEngine;
using UnityEngine.SceneManagement;
using FE = OVRFaceExpressions.FaceExpression;

[RequireComponent(typeof(Camera))]
public class EyeGazeObserver : MonoBehaviour
{
    [SerializeField, Tooltip("GameObject containing EyeRaycaster class.")]
    private EyeRaycaster eyeRaycaster;

    [SerializeField]
    private OVRFaceExpressions faceExpressions;

    [Tooltip("Show box boundarys in gui.")]
    public bool showReactorObjectBoundary = true;

    [Tooltip("Show eye gazing pointer in gui.")]
    public bool showEyeGazingPointer = false;

    [SerializeField, Tooltip("Texture for eye gazing pointer.")]
    private Texture eyeGazingPointer;

    [SerializeField, Tooltip("Auto-filled with RayReactor objects in Hierarchy.")]
    public List<GameObject> reactorObjects = new List<GameObject>();

    private Camera observerCamera;

    private readonly List<string> colnames = new List<string>
    {
        "l_eye_pnt_x","l_eye_pnt_y","r_eye_pnt_x","r_eye_pnt_y",
        "eye_pnt_x","eye_pnt_y","gaz_obj","l_eye_cls","r_eye_cls"
    };
    private readonly List<string> csvData = new List<string>
    {
        "0.0","0.0","0.0","0.0","0.0","0.0","None","0.0","0.0"
    };


    private readonly List<string[]> sceneObj_csvData = new List<string[]>();




    private int screenWidth;
    private int screenHeight;

    private GUIStyle timerStyle;
    private float timer = 0f;

    // -------- 자동 동기화 --------
#if UNITY_EDITOR
    [ContextMenu("Refresh Reactor Objects (Auto)")]
#endif
    private void RefreshReactorObjects()
    {
        var reactors = Object.FindObjectsByType<RayReactor>(FindObjectsInactive.Exclude, FindObjectsSortMode.None);

        reactorObjects = reactors
            .Where(r => {
                // 2. Renderer와 Collider가 있는지 검사 (둘 중 하나라도 없으면 제외됨)
                bool hasRenderer = r.GetComponent<Renderer>() != null;
                //bool hasCollider = r.GetComponent<Collider>() != null;
                return hasRenderer; //&& hasCollider;
            })
            .Select(r => r.gameObject)
            .Distinct()
            .ToList();
    }

    private void OnValidate()
    {
        // 에디터에서 구성 변경 시 자동 반영
        //RefreshReactorObjects();       
    }
    // --------------------------------    

    public void SetupColumns() // Awake 대신 호출할 수 있는 public 함수로 변경
    {
        // 1. 기존 데이터 초기화 (중요: 중복 생성 방지)
        colnames.Clear();
        csvData.Clear();

        // 기본 컬럼 다시 추가
        colnames.AddRange(new string[] {
        "l_eye_pnt_x","l_eye_pnt_y","r_eye_pnt_x","r_eye_pnt_y",
        "eye_pnt_x","eye_pnt_y","gaz_obj","l_eye_cls","r_eye_cls"
    });
        for (int i = 0; i < 9; i++) csvData.Add("0.0");


        // 2. 리액터 오브젝트 갱신
        var reactors = Object.FindObjectsByType<RayReactor>(FindObjectsInactive.Include, FindObjectsSortMode.None);
        reactorObjects = reactors.Select(r => r.gameObject).Distinct().ToList();

        // 3. 리액터별 컬럼 추가
        /*foreach (var reactorObject in reactorObjects)
        {
            var rr = reactorObject.GetComponent<RayReactor>();
            if (rr == null) continue;

            colnames.Add(rr.objectName + "_top_left_x");
            colnames.Add(rr.objectName + "_top_left_y");
            colnames.Add(rr.objectName + "_bottom_right_x");
            colnames.Add(rr.objectName + "_bottom_right_y");

            for (int i = 0; i < 4; i++) csvData.Add("0.0");
        }*/




        Debug.Log($"[EyeGaze] 컬럼 생성 완료! 총 개수: {colnames.Count}");
    }

    void Awake()
    {
        SetupColumns(); // 자체적으로도 실행
    }

    private void Start()
    {
        observerCamera = GetComponent<Camera>();

        screenWidth = Screen.width;
        screenHeight = Screen.height;

        timerStyle = new GUIStyle
        {
            normal = { textColor = Color.white },
            alignment = TextAnchor.MiddleCenter,
            fontSize = 60
        };
    }

    private void Update()
    {
        timer += Time.deltaTime;
    }

    private void OnGUI()
    {
        if (eyeRaycaster == null || observerCamera == null) return;

        Vector2 screenLeftEyeGazingPoint = WorldPointToScreenPoint(eyeRaycaster.LeftGazingPoint);
        Vector2 screenRightEyeGazingPoint = WorldPointToScreenPoint(eyeRaycaster.RightGazingPoint);
        Vector2 screenEyeGazingPoint = WorldPointToScreenPoint(eyeRaycaster.GazingPoint);

        Rect screenEyeGazingRect = new Rect(screenEyeGazingPoint.x - 15f, screenEyeGazingPoint.y - 15f, 30f, 30f);

        if (showEyeGazingPointer && eyeGazingPointer != null)
            GUI.DrawTexture(screenEyeGazingRect, eyeGazingPointer);

        // 기본 CSV 데이터
        csvData[0] = (screenLeftEyeGazingPoint.x / screenWidth).ToString();
        csvData[1] = (screenLeftEyeGazingPoint.y / screenHeight).ToString();
        csvData[2] = (screenRightEyeGazingPoint.x / screenWidth).ToString();
        csvData[3] = (screenRightEyeGazingPoint.y / screenHeight).ToString();
        csvData[4] = (screenEyeGazingPoint.x / screenWidth).ToString();
        csvData[5] = (screenEyeGazingPoint.y / screenHeight).ToString();
        csvData[6] = "None";

        // 눈 감김 가중치
        bool validFE = faceExpressions != null && faceExpressions.ValidExpressions;
        csvData[7] = validFE ? faceExpressions.GetWeight(FE.EyesClosedL).ToString() : "0.0";
        csvData[8] = validFE ? faceExpressions.GetWeight(FE.EyesClosedR).ToString() : "0.0";


        sceneObj_csvData.Clear();

        foreach (var reactorObject in reactorObjects)
        {
            if (reactorObject == null) continue;

            var rend = reactorObject.GetComponent<Renderer>();
            var col = reactorObject.GetComponent<Collider>();
            var rr = reactorObject.GetComponent<RayReactor>();
            if (rend == null || col == null || rr == null) continue;

            // 1. 오브젝트의 3D 월드 좌표 (부피의 중심점) 가져오기
            Vector3 worldPos = rend.bounds.center;

            // 카메라 화면 좌표로 변환 (기존 로직 유지)
            Vector3 screenPointOfObjectCenter = observerCamera.WorldToScreenPoint(worldPos);

            if (screenPointOfObjectCenter.z > 0)
            {
                Rect screenRectForObject = WorldObjectToScreenRect(reactorObject);

                // 2. sceneObj_csvData에 3D 좌표(XYZ) 추가
                sceneObj_csvData.Add(new string[]{
                rr.objectName,
                worldPos.x.ToString("F4"), // 3D X
                worldPos.y.ToString("F4"), // 3D Y
                worldPos.z.ToString("F4"), // 3D Z
                (screenRectForObject.xMin / screenWidth).ToString("F4"), // 2D Ratio X_Min
                (screenRectForObject.yMin / screenHeight).ToString("F4"),
                (screenRectForObject.xMax / screenWidth).ToString("F4"),
                (screenRectForObject.yMax / screenHeight).ToString("F4")
            });

                // 시선 충돌 판정 및 GUI 박스 표시 (기존 유지)
                if (screenRectForObject.Overlaps(screenEyeGazingRect))
                {
                    rr.isEyeReacted = true;
                    csvData[6] = rr.objectName;
                }
                else { rr.isEyeReacted = false; }

                if (showReactorObjectBoundary)
                    GUI.Box(screenRectForObject, rr.objectName);
            }
        }
    }

    private Rect WorldObjectToScreenRect(GameObject worldObject)
    {
        var collider = worldObject.GetComponent<Collider>();
        if (collider == null)
            return new Rect(0, 0, 0, 0);

        Bounds bounds = collider.bounds;

        Vector3 cen = bounds.center;
        Vector3 ext = bounds.extents;

        Vector3[] screenBoundsExtents = new Vector3[8];
        screenBoundsExtents[0] = WorldPointToScreenPoint(new Vector3(cen.x - ext.x, cen.y - ext.y, cen.z - ext.z));
        screenBoundsExtents[1] = WorldPointToScreenPoint(new Vector3(cen.x + ext.x, cen.y - ext.y, cen.z - ext.z));
        screenBoundsExtents[2] = WorldPointToScreenPoint(new Vector3(cen.x - ext.x, cen.y - ext.y, cen.z + ext.z));
        screenBoundsExtents[3] = WorldPointToScreenPoint(new Vector3(cen.x + ext.x, cen.y - ext.y, cen.z + ext.z));
        screenBoundsExtents[4] = WorldPointToScreenPoint(new Vector3(cen.x - ext.x, cen.y + ext.y, cen.z - ext.z));
        screenBoundsExtents[5] = WorldPointToScreenPoint(new Vector3(cen.x + ext.x, cen.y + ext.y, cen.z - ext.z));
        screenBoundsExtents[6] = WorldPointToScreenPoint(new Vector3(cen.x - ext.x, cen.y + ext.y, cen.z + ext.z));
        screenBoundsExtents[7] = WorldPointToScreenPoint(new Vector3(cen.x + ext.x, cen.y + ext.y, cen.z + ext.z));

        int margin = 0;
        int minimum = -margin;
        int maximumWidth = Screen.width + margin;
        int maximumHeight = Screen.height + margin;

        float xMin = minimum, xMax = minimum, yMin = minimum, yMax = minimum;

        for (int i = 0; i < screenBoundsExtents.Length; i++)
        {
            if (screenBoundsExtents[i].z > 0)
            {
                xMin = xMax = screenBoundsExtents[i].x;
                yMin = yMax = screenBoundsExtents[i].y;
                break;
            }
        }

        float widthMiddle = Screen.width * 0.5f;
        float heightMiddle = Screen.height * 0.5f;

        for (int i = 0; i < screenBoundsExtents.Length; i++)
        {
            if (screenBoundsExtents[i].z <= 0)
            {
                if (screenBoundsExtents[i].x <= widthMiddle) screenBoundsExtents[i].x = maximumWidth;
                else screenBoundsExtents[i].x = minimum;

                if (screenBoundsExtents[i].y <= heightMiddle) screenBoundsExtents[i].y = maximumHeight;
                else screenBoundsExtents[i].y = minimum;
            }

            if (screenBoundsExtents[i].x < xMin) xMin = screenBoundsExtents[i].x;
            else if (screenBoundsExtents[i].x > xMax) xMax = screenBoundsExtents[i].x;

            if (screenBoundsExtents[i].y < yMin) yMin = screenBoundsExtents[i].y;
            else if (screenBoundsExtents[i].y > yMax) yMax = screenBoundsExtents[i].y;
        }

        xMin = Mathf.Clamp(xMin, minimum, maximumWidth);
        xMax = Mathf.Clamp(xMax, minimum, maximumWidth);
        yMin = Mathf.Clamp(yMin, minimum, maximumHeight);
        yMax = Mathf.Clamp(yMax, minimum, maximumHeight);

        xMin = xMin >= 0 ? xMin : 0;
        yMin = yMin >= 0 ? yMin : 0;
        xMax = xMax <= Screen.width ? xMax : Screen.width;
        yMax = yMax <= Screen.height ? yMax : Screen.height;

        return new Rect(xMin, yMin, xMax - xMin, yMax - yMin);
    }

    private Vector3 WorldPointToScreenPoint(Vector3 worldPoint)
    {
        Vector3 screenPoint = observerCamera.WorldToScreenPoint(worldPoint);
        screenPoint.y = Screen.height - screenPoint.y;
        return screenPoint;
    }

    public string[] GetColumnNames() => colnames.ToArray();

    public List<string[]> GetsceneObj_csvData() => sceneObj_csvData;
    public string[] GetCSVData() => csvData.ToArray();
}
