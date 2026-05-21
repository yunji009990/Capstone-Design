using System.Collections.Generic; // 사전(Dictionary) 사용을 위해 추가
using TMPro;
using UnityEngine;

// 환자 데이터를 담을 그릇 (클래스) 정의
[System.Serializable]
public class PatientData
{
    public string name;           // 이름
    public int age;               // 나이
    public string gender;         // 성별
    public bool hasDisability;    // 발달장애 유무
    //public string examDate;       // 검사일
    public string patientID;      // 환자번호ID
    public string password;       // 비밀번호 (로그인용)
}

public class GuestData
{
    public string name;           // 이름
    public int age;               // 나이
    public string gender;         // 성별
    public bool hasDisability;    // 발달장애 유무        
    public string guestID;
    public string password;       // 비밀번호 (로그인용)
}

public class LoginManager : MonoBehaviour
{
    [Header("로그인 입력 UI")]
    public TMP_InputField idInputField;
    public TMP_InputField passwordInputField;
    public TMP_Text messageText; // 에러 메시지 등 표시
    public TMP_Dropdown guestDropdown;
    public GameObject loginPanel; // 로그인 화면


    [Header("환자 정보 표시 UI (결과 화면)")]
    public TextMeshProUGUI nameText;    // 이름 텍스트
    public TextMeshProUGUI ageText;     // 나이 텍스트
    public TextMeshProUGUI genderText;  // 성별 텍스트
    public TextMeshProUGUI disabilityText; // 장애유무 텍스트
    public TextMeshProUGUI dateText;    // 검사일 텍스트
    public TextMeshProUGUI patientIdText; // 환자ID 텍스트

    // 아이디를 키(Key)로 사용하여 환자 데이터를 찾는 가짜 데이터베이스
    private Dictionary<string, PatientData> patientDatabase = new Dictionary<string, PatientData>();
    private Dictionary<string, GuestData> guestDatabase = new Dictionary<string, GuestData>();

    //CSVWriter가 가져갈 수 있는 '공유 변수' 만들기
    public static string USER_ID = "Guest";

    void Start()
    {
        if (messageText != null)
        {
            messageText.text = "";
        }
    }

    private void AddGuestToDatabase(string guestName)
    {
        if (!guestDatabase.ContainsKey(guestName))
        {
            GuestData g = new GuestData();
            g.name = guestName;
            g.age = 0;
            g.gender = "?";
            g.hasDisability = false;
            g.guestID = guestName; // ID를 이름과 동일하게 설정
            g.password = "";

            guestDatabase.Add(guestName, g);
        }
    }

    public void GuestLogin()
    {
        // 1. 드롭다운에서 현재 선택된 텍스트 가져오기
        int index = guestDropdown.value;
        string selectedName = guestDropdown.options[index].text;

        // 2. 만약 "+ 새 항목 추가" 같은 특수 옵션이라면 로그인 차단
        if (selectedName.Contains("+"))
        {
            messageText.text = "새 항목을 먼저 추가하거나 올바른 게스트를 선택하세요.";
            messageText.color = Color.yellow;
            return;
        }

        // 3. 해당 이름이 데이터베이스에 없으면 즉석 생성
        if (!guestDatabase.ContainsKey(selectedName))
        {
            AddGuestToDatabase(selectedName);
        }

        // 4. 로그인 처리
        GuestData selectedGuest = guestDatabase[selectedName];
        DisplayGuestInfo(selectedGuest);

        messageText.text = $"{selectedName}(으)로 로그인했습니다.";
        messageText.color = Color.cyan;
    }

    void DisplayGuestInfo(GuestData data)
    {
        nameText.text = data.name;
        ageText.text = $"{data.age}세";
        genderText.text = data.gender;
        patientIdText.text = data.guestID;

        // 중요: 다른 스크립트(CSVWriter 등)에서 참조할 ID 설정
        USER_ID = data.guestID;
        api_controller.USER_ID = data.guestID;

        loginPanel.SetActive(false); // 로그인창 닫기
    }
}