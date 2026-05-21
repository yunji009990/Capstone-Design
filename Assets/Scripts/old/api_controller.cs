using Newtonsoft.Json;


using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Security.Cryptography;
using TMPro;
using Unity.VisualScripting;
using UnityEngine;
using UnityEngine.Networking;
using UnityEngine.UI;
using static api_controller;
using static OVRAnchor;




public class api_controller : MonoBehaviour
{
    // Start is called before the first frame update
    public static api_controller instance { get; private set; }


    string url = "http://192.168.50.240:8000";

    // 토큰 임시 저장용. 인증요구 작업은 모두 적합한 토큰을 요구함.
    public string token;


    [Header("로그인 입력 UI")]
    public TMP_InputField idInputField;
    public TMP_InputField passwdInputField;

    [Header("회원가입 입력 UI")]
    public TMP_InputField newIdInputField;
    public TMP_InputField newPasswdInputField;
    public TMP_InputField nameInputField;
    public TMP_InputField birthInputField;
    public TMP_InputField phoneInputField;
    public TMP_InputField emailInputField;
    public Toggle[] genderToggle;
    public Toggle hasdisability;

    public string id;
    public string passwd;

    public bool button1;
    //public Button loginButton;

    public bool MeB;

    public string csv_name;

    LoginManager loginManager;

    public Toggle disabilityToggle;

    public static string USER_ID = "Guest1";


    // 응답 양식.요청에 대한 응답은 이 구조로 반환 받음.
    public class CommonResponse
    {
        public bool success;
        public int code;
        public string message;
        public Dictionary<string, object> data;
    }




    // 요청 데이터 양식. /docs로도 확인 가능.

    public class LoginData
    {
        public string id;
        public string passwd;

        public LoginData(string id, string passwd)
        {
            this.id = id;
            this.passwd = passwd;
        }
    }

    //로그인하고 받아온 유저데이터
    public class UserData
    {
        public string id;
        public string name;
        public string gender;
        public string birth;
        public int hasdisability;
    }

    public class ProfileResponse
    {
        public bool success;
        public string message;
        public UserData data;
    }

    public class RegisterData
    {
        public string id;
        public string passwd;
        public string usertype;
        public string name;
        public string birth; // 서버에서 date 형식이므로 "yyyy-MM-dd" 문자열로 보냅니다.
        public string gender;
        public bool hasdisability;
        public string phone;
        public string email;

        public RegisterData(string id, string passwd, string name, string birth, string gender, bool hasdisability, string phone, string email, string usertype = "user")
        {
            this.id = id;
            this.passwd = passwd;
            this.usertype = usertype;
            this.name = name;
            this.birth = birth;
            this.gender = gender;
            this.hasdisability = hasdisability;
            this.phone = phone;
            this.email = email;
        }
    }

    public class UploadCreateData
    {
        public int project_id;
        public string session_jsonl;
        public List<string> expected_coreLogs_files;
        public List<string> expected_sceneObjLogs_files;

        public UploadCreateData(string session_jsonl, List<string> expected_coreLogs_files, List<string> expected_sceneObjLogs_files)
        {
            project_id = 1;
            this.session_jsonl = session_jsonl;
            this.expected_coreLogs_files = expected_coreLogs_files;
            this.expected_sceneObjLogs_files = expected_sceneObjLogs_files;
        }
    }

    public class UploadFinalizeData
    {
        public string upload_id;

        public UploadFinalizeData(string upload_id)
        {
            this.upload_id = upload_id;
        }
    }





    void Start()
    {
        loginManager = GetComponent<LoginManager>();
        Debug.Log("test Start");
    }

    // Update is called once per frame
    void Update()
    {
        if (button1)
        {
            button1 = false;
            StartCoroutine(GetRequest());
        }

        /*if (loginButton)
        {
            loginButton = false;
            StartCoroutine(Login());
        }*/

        if (MeB)
        {
            MeB = false;
            StartCoroutine(Me());
        }
    }



    // api를 쓰기위해서 루트에 /api한 이후여야함.


    // 테스트용 요청. 이후 삭제 예정. 
    // 암호키와 알고리즘을 반환함.
    public IEnumerator GetRequest()
    {
        using (UnityWebRequest req = UnityWebRequest.Get($"{url}/api/"))
        {
            yield return req.SendWebRequest();

            if (req.result != UnityWebRequest.Result.Success)
            {
                Debug.LogError(req.error);
            }
            else
            {
                Debug.Log(req.downloadHandler.text);
            }
        }
    }

    // 로그인. id, password를 입력해 인증된 토큰을 반환 받음.
    // 이후 업로드 등을 위해 토큰은 임시 저장해두어야함.
    // 로그인시 비밀번호는 반드시 휘발적으로 입력받아서 쓰고 바로 사라지도록.
    public IEnumerator Login()
    {
        // 서버 요청 데이터 생성
        LoginData loginData = new LoginData(id, passwd);
        string json_in = JsonConvert.SerializeObject(loginData);
        byte[] bodyRaw = System.Text.Encoding.UTF8.GetBytes(json_in);

        // WebRequest 설정
        UnityWebRequest req = new UnityWebRequest($"{url}/api/login", "POST");
        req.uploadHandler = new UploadHandlerRaw(bodyRaw);
        req.downloadHandler = new DownloadHandlerBuffer();
        req.SetRequestHeader("Content-Type", "application/json");

        // 요청 전송 및 대기
        yield return req.SendWebRequest();

        if (req.result != UnityWebRequest.Result.Success)
        {
            Debug.LogError($"로그인 실패: {req.error}");
        }
        else
        {
            Debug.Log($"서버 응답: {req.downloadHandler.text}");

            // JSON 데이터를 객체로 변환
            CommonResponse res = JsonConvert.DeserializeObject<CommonResponse>(req.downloadHandler.text);

            if (res.success && res.data != null)
            {
                // 토큰 추출 및 저장
                res.data.TryGetValue("token", out object obj);
                token = Convert.ToString(obj);
                Debug.Log($"로그인 성공! 발급된 토큰: {token}");

                StartCoroutine(Profile());

                loginManager.loginPanel.SetActive(false);
            }
            else
            {
                Debug.LogWarning($"로그인 거부: {res.message}");
            }
        }
    }


    // 테스트용 2. /me. 인증된 토큰을 전송하면 id를 반환해줌
    public IEnumerator Me()
    {

        using (UnityWebRequest req = UnityWebRequest.Get($"{url}/api/me"))
        {
            // 토큰을 쓰는 요청은 이렇게 헤더에 토큰을 저장해서 전송해야함.
            req.SetRequestHeader("Authorization", $"Bearer {token}");

            yield return req.SendWebRequest();

            if (req.result != UnityWebRequest.Result.Success)
            {
                Debug.LogError(req.error);
            }
            else
            {
                Debug.Log(req.downloadHandler.text);
            }
        }
    }

    public IEnumerator Profile()
    {
        // 1. GET 방식으로 주소 설정
        using (UnityWebRequest req = UnityWebRequest.Get($"{url}/api/profile"))
        {
            // 2. 인증 토큰만 헤더에 담습니다. (Body 데이터는 넣지 않습니다)
            req.SetRequestHeader("Authorization", $"Bearer {token}");

            yield return req.SendWebRequest();

            if (req.result == UnityWebRequest.Result.Success)
            {
                Debug.Log("프로필 로드 성공!");
                var res = JsonConvert.DeserializeObject<ProfileResponse>(req.downloadHandler.text);

                if (res.success && res.data != null)
                {
                    var user = res.data;
                    Debug.Log($"<color=cyan>[프로필 로드 성공]</color>\n" +
                         $"이름: {user.name}\n" +
                         $"아이디: {user.id}\n" +
                         $"성별: {(user.gender == "M" ? "남" : "여")}\n" +
                         $"장애 여부: {(user.hasdisability == 1 ? "있음" : "없음")}\n" +
                         $"원래 생년월일: {user.birth}");

                    // UI 업데이트
                    loginManager.nameText.text = user.name;
                    loginManager.patientIdText.text = user.id;
                    loginManager.genderText.text = (user.gender == "M" ? "남" : "여");
                    disabilityToggle.isOn = (user.hasdisability == 1);
                    loginManager.ageText.text = CalculateAgeAndMonths(user.birth);
                    USER_ID = user.id.ToString();
                }
            }
            else
            {
                // 3. 에러 발생 시 서버가 보내준 상세 메시지를 다시 확인합니다.
                Debug.LogError($"에러 코드: {req.responseCode} | 에러 내용: {req.error}");
                Debug.LogError($"서버 상세 메시지: {req.downloadHandler.text}");
            }
        }
    }


    public IEnumerator Register(RegisterData data)
    {
        string json_in = JsonConvert.SerializeObject(data); // Newtonsoft.Json 사용 권장
        byte[] bodyRaw = System.Text.Encoding.UTF8.GetBytes(json_in);

        UnityWebRequest req = new UnityWebRequest($"{url}/api/register", "POST");
        req.uploadHandler = new UploadHandlerRaw(bodyRaw);
        req.downloadHandler = new DownloadHandlerBuffer();
        req.SetRequestHeader("Content-Type", "application/json");

        yield return req.SendWebRequest();

        if (req.result != UnityWebRequest.Result.Success)
        {
            Debug.LogError($"회원가입 실패: {req.error}\n상세내용: {req.downloadHandler.text}");
        }
        else
        {
            Debug.Log($"회원가입 성공: {req.downloadHandler.text}");
            SuccessRegist();
        }
    }

    public void LoginButtonClick()
    {

        id = idInputField.text;
        passwd = passwdInputField.text;

        if (string.IsNullOrEmpty(id) || string.IsNullOrEmpty(passwd))
        {
            Debug.LogWarning("아이디와 비밀번호를 입력해주세요.");
            return;
        }

        StartCoroutine(Login());
    }

    //���� ��� �Լ�
    private string CalculateAgeAndMonths(string birthStr)
    {
        if (string.IsNullOrEmpty(birthStr)) return "정보 없음";

        try
        {
            DateTime birthDate = DateTime.Parse(birthStr);
            DateTime now = DateTime.Now;

            // 총 개월 수 계산
            int totalMonths = ((now.Year - birthDate.Year) * 12) + now.Month - birthDate.Month;

            // 아직 이번 달 생일이 지나지 않았다면 -1개월
            if (now.Day < birthDate.Day) totalMonths--;

            //if (totalMonths < 0) totalMonths = 0;

            int years = totalMonths / 12;
            int months = totalMonths;

            return $"{years}세({months}개월)";
        }
        catch (Exception e)
        {
            Debug.LogError("나이 계산 오류: " + e.Message);
            return "형식 오류";
        }
    }

    public void OnClickRegister()
    {
        //성별 값 결정
        string selectedGender = genderToggle[0].isOn ? "M" : "F";
        //생일 변환 19990101 -> 1999-01-01
        string formattedBirth = FormatBirthDate(birthInputField.text);

        RegisterData data = new RegisterData(
            newIdInputField.text,
            newPasswdInputField.text,
            nameInputField.text,
            formattedBirth,
            selectedGender,
            hasdisability.isOn,
            phoneInputField.text,
            emailInputField.text
        );

        // 3. api_controller의 코루틴 호출
        StartCoroutine(Register(data));
    }

    private string FormatBirthDate(string rawDate)
    {
        if (string.IsNullOrEmpty(rawDate) || rawDate.Length != 8)
        {
            Debug.LogWarning("날짜 형식이 올바르지 않습니다. (8자리 필요)");
            return rawDate;
        }
        string formatted = rawDate.Insert(4, "-").Insert(7, "-");
        return formatted;
    }

    private void SuccessRegist()
    {
        newIdInputField.text = string.Empty;
        newPasswdInputField.text = string.Empty;
        nameInputField.text = (string)null;
        birthInputField.text = string.Empty;
        genderToggle[0].isOn = false;
        hasdisability.isOn = false;
        phoneInputField.text = (string)null;
        emailInputField.text = (string)null;
    }





    public IEnumerator upload_create(UploadCreateData uploadCreateData, Action<string> onDone)
    {
        string json_in = JsonConvert.SerializeObject(uploadCreateData);
        byte[] bodyRaw = System.Text.Encoding.UTF8.GetBytes(json_in);

        UnityWebRequest req = new UnityWebRequest($"{url}/api/upload/create", "POST");
        req.SetRequestHeader("Authorization", $"Bearer {token}");
        req.uploadHandler = new UploadHandlerRaw(bodyRaw);
        req.downloadHandler = new DownloadHandlerBuffer();
        req.SetRequestHeader("Content-Type", "application/json");

        yield return req.SendWebRequest();

        if (req.result != UnityWebRequest.Result.Success)
        {
            Debug.LogError($"upload_create success: {req.error}");
        }
        else
        {
            CommonResponse res = JsonConvert.DeserializeObject<CommonResponse>(req.downloadHandler.text);

            if (res.success && res.data != null)
            {
                res.data.TryGetValue("upload_id", out object obj);
                string upload_id = Convert.ToString(obj);
                Debug.Log($"upload_create. upload_id: {upload_id}");

                onDone?.Invoke(upload_id);
            }
            else
            {
                Debug.LogWarning($"upload_create receive fail: {res.message}");
            }
        }
    }


    public enum UploadLogType
    {
        CORELOGS,
        SCENEOBJLOGS
    }

    public IEnumerator upload_logs(string upload_id, string filename, string filepath, UploadLogType uploadLogType)
    {

        WWWForm form = new WWWForm();
        byte[] csvBytes;
        try
        {
            csvBytes = File.ReadAllBytes(filepath);
        }
        catch (Exception e)
        {
            Debug.LogError(e);
            yield break;
        }

        string endPoint = "";
        if (uploadLogType == UploadLogType.CORELOGS)
            endPoint = "corelogs";
        else if (uploadLogType == UploadLogType.SCENEOBJLOGS)
            endPoint = "sceneobjlogs";
        else
            Debug.LogError("endPoint Error");


        form.AddField("upload_id", upload_id);
        form.AddBinaryData("file", csvBytes, filename, "text/csv");


        using (UnityWebRequest req = UnityWebRequest.Post($"{url}/api/upload/{endPoint}", form))
        {
            req.SetRequestHeader("Authorization", $"Bearer {token}");

            yield return req.SendWebRequest();

            if (req.result != UnityWebRequest.Result.Success)
                Debug.LogError($"upload {filename} fail: {req.error}");
            else
            {
                CommonResponse res = JsonConvert.DeserializeObject<CommonResponse>(req.downloadHandler.text);

                Debug.Log($"<color=#7FBF7F>upload_logs messange: {res.message}</color>");
            }
        }
    }


    public IEnumerator upload_completeCheck(string upload_id, UploadLogType uploadLogType, List<string> missingFiles)
    {

        string endPoint = "";
        if (uploadLogType == UploadLogType.CORELOGS)
            endPoint = "corelogs";
        else if (uploadLogType == UploadLogType.SCENEOBJLOGS)
            endPoint = "sceneobjlogs";
        else
            Debug.LogError("endPoint Error");

        WWWForm form = new WWWForm();
        form.AddField("upload_id", upload_id);

        using (UnityWebRequest req = UnityWebRequest.Post($"{url}/api/upload/{endPoint}/complete", form))
        {
            req.SetRequestHeader("Authorization", $"Bearer {token}");
            req.downloadHandler = new DownloadHandlerBuffer();


            yield return req.SendWebRequest();

            if (req.result != UnityWebRequest.Result.Success)
                Debug.LogError($"upload_completeCheck {uploadLogType} fail: {req.error}");
            else
            {
                Debug.Log($"upload_completeCheck success: {req.downloadHandler.text}");

                CommonResponse res = JsonConvert.DeserializeObject<CommonResponse>(req.downloadHandler.text);

                if (!res.success)
                    Debug.LogWarning($"upload_completeCheck receive fail: {res.message}");



                if (res.data != null && res.data.TryGetValue("missing_files", out object obj))
                {
                    if (obj is List<string> arr)
                    {
                        missingFiles.AddRange(arr);
                    }
                }

            }
        }
    }




    public IEnumerator upload_finalize(string upload_id)
    {
        string json_in = JsonConvert.SerializeObject(new UploadFinalizeData(upload_id));
        byte[] bodyRaw = System.Text.Encoding.UTF8.GetBytes(json_in);


        UnityWebRequest req = new UnityWebRequest($"{url}/api/upload/finalize", "POST");
        req.SetRequestHeader("Authorization", $"Bearer {token}");
        req.uploadHandler = new UploadHandlerRaw(bodyRaw);
        req.downloadHandler = new DownloadHandlerBuffer();
        req.SetRequestHeader("Content-Type", "application/json");

        yield return req.SendWebRequest();


        if (req.result != UnityWebRequest.Result.Success)
        {
            Debug.LogError($"upload_finalize {upload_id} fail: {req.error}");
            yield break;
        }

        Debug.Log($"upload_finalize succeess: {req.downloadHandler.text}");
    }
}
