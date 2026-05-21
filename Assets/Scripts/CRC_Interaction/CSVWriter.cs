using Newtonsoft.Json;
using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using TMPro;
using UnityEngine;

public class CSVWriter : MonoBehaviour
{



    public static CSVWriter Instance { get; private set; }
    public string FullSavePath => fullSavePath;
    public int CurrentSessionNumber => currentSessionNumber;

    [SerializeField] private bool isEnabledRecording = true;
    [SerializeField] private TextMeshProUGUI statusText; // 관리자 화면에 표시할 텍스트 (선택)
    [SerializeField] private CSVExport csvExport;

    [Header("Observers")]
    [SerializeField] private EyeGazeObserver eyeGazeObserver;
    [SerializeField] private HeadObserver headObserver;
    [SerializeField] private FaceObserver faceObserver;
    [SerializeField] private HandObserver handObserver;

    public bool isRecording = false;
    private string fullSavePath;


    private int currentSessionNumber = 1;
    private int sceneIndex;
    private int timestamp_counter;
    private float timer = 0f;

    private int recordedFrameCount = 0; // 몇 줄이나 쌓였는지 카운트


    List<string> keyColnames = new List<string>() { "session_id", "scene_index", "timestamp_counter" };


    public readonly static string coreLogs_Foldername = "core";
    public readonly static string coreLogs_filename = "coreLogs";

    public readonly static string sceneObjLogs_Foldername = "sceneObj";
    public readonly static string sceneObjLogs_filename = "sceneObjLogs";





    private List<string> coreLogs_colnames = new List<string>();
    private List<string[]> coreLogs_csvData = new List<string[]>();
    private List<string> coreLogs_rowData = new List<string>();

    private List<string> sceneObjLogs_colnames = new List<string>();
    public List<string[]> sceneObjLogs_csvData = new List<string[]>();
    private List<string> sceneObjLogs_rowData = new List<string>();

    private void Awake()
    {
        Instance = this;

        SetupSavePath();
    }


    private void Start()
    {
        LoadLastSessionOnly();
    }


    public void Initialize(string filename)
    {
        string s = filename;

        Match m = Regex.Match(s, @"\d+");
        Debug.Log($"<color=magenta>{m}</color>");

        this.sceneIndex = int.Parse(m.Value);
        timer = 0f;
        timestamp_counter = 0;
        recordedFrameCount = 0;



        // 경로 설정 및 폴더 생성
        /*string desktopPath = Environment.GetFolderPath(Environment.SpecialFolder.Desktop);
        string userIdFolder = string.IsNullOrEmpty(api_controller.USER_ID) ? "Unknown_User" : api_controller.USER_ID;
        fullSavePath = Path.Combine(desktopPath, "CRC_test/Session_Log/", userIdFolder);*/
        SetupSavePath();


        // 유저 파일 없으면 생성
        if (!Directory.Exists(fullSavePath)) Directory.CreateDirectory(fullSavePath);


        // 컬럼 이름 설정
        eyeGazeObserver.SetupColumns();

        coreLogs_colnames.Clear();

        coreLogs_colnames.AddRange(keyColnames);
        coreLogs_colnames.Add("timestamp");
        coreLogs_colnames.AddRange(faceObserver.GetColumnNames());
        coreLogs_colnames.AddRange(handObserver.GetColumnNames());
        coreLogs_colnames.AddRange(headObserver.GetColumnNames());
        coreLogs_colnames.AddRange(eyeGazeObserver.GetColumnNames());

        sceneObjLogs_colnames.AddRange(keyColnames);
        sceneObjLogs_colnames.AddRange(new List<string> { "obj_id", "top_left_x", "top_left_y", "bottom_right_x", "bottom_right_y" });



        if (recordedFrameCount == 0 && timer == 0)
        {
            LoadSessionProgress();
        }

        isRecording = true;
        Debug.Log($"<color=cyan>씬 변경에 따른 CSV 초기화 완료</color>");
    }








    private void FixedUpdate()
    {
        if (!isRecording || Time.timeScale == 0f) return;

        timer += Time.fixedDeltaTime;
        timestamp_counter++;
        recordedFrameCount++;

        // 키값 설정
        coreLogs_rowData.Clear();
        coreLogs_rowData.Add(currentSessionNumber.ToString());
        coreLogs_rowData.Add(sceneIndex.ToString());
        coreLogs_rowData.Add(timestamp_counter.ToString());

        foreach (string[] row in eyeGazeObserver.GetsceneObj_csvData())
        {
            sceneObjLogs_rowData.Clear();

            sceneObjLogs_rowData.AddRange(coreLogs_rowData); // 키값 복사

            sceneObjLogs_rowData.AddRange(row);

            sceneObjLogs_csvData.Add(sceneObjLogs_rowData.ToArray());
        }



        coreLogs_rowData.Add(timer.ToString("F3"));
        coreLogs_rowData.AddRange(headObserver.GetCSVData());
        coreLogs_rowData.AddRange(faceObserver.GetCSVData());
        coreLogs_rowData.AddRange(handObserver.GetCSVData());
        coreLogs_rowData.AddRange(eyeGazeObserver.GetCSVData());

        coreLogs_csvData.Add(coreLogs_rowData.ToArray());

        //관리자 화면 실시간 업데이트 (1초에 한 번만 로그 출력)
        if (recordedFrameCount % 50 == 0 && isRecording)
        {
            string statusMsg = $"[Recording] {DateTime.Now:HH:mm:ss} | {timer:F1}s progress | {recordedFrameCount} Accumulated data";
            Debug.Log($"<color=green>{statusMsg}</color>");

            if (statusText != null)
                statusText.text = statusMsg;
        }
    }


    const long MAX_CSV_SIZE = 10 * 1024 * 1024;

    public void Save()
    {        
        if (!isEnabledRecording || coreLogs_csvData.Count <= 0 || sceneObjLogs_csvData.Count <= 0) return;

        SaveCsv(coreLogs_Foldername, coreLogs_filename, coreLogs_csvData, coreLogs_colnames);


        SaveCsv(sceneObjLogs_Foldername, sceneObjLogs_filename, sceneObjLogs_csvData, sceneObjLogs_colnames);


        void SaveCsv(string foldername, string filename, List<string[]> csvData, List<string> colnames)
        {
            try
            {
                string folderPath = Path.Combine(fullSavePath, foldername);
                if (!Directory.Exists(folderPath)) Directory.CreateDirectory(folderPath);


                Regex regex = new Regex($@"^{Regex.Escape(filename)}_(\d+)\.csv$");

                int maxNum = -1;
                string targetFile = null;

                bool isFirst = true;

                foreach (var file in Directory.GetFiles(folderPath, "*.csv"))
                {
                    string name = Path.GetFileName(file);
                    var m = regex.Match(name);
                    if (!m.Success) continue;

                    int num = int.Parse(m.Groups[1].Value);
                    if (num > maxNum)
                    {
                        maxNum = num;
                        targetFile = file;
                        isFirst = false;
                    }
                }



                if (targetFile == null)
                {
                    maxNum = 1;
                    targetFile = Path.Combine(folderPath, $"{filename}_{maxNum}.csv");
                }


                FileStream fs = new FileStream(
                    targetFile,
                    FileMode.Append,
                    FileAccess.Write,
                    FileShare.Read
                );

                StreamWriter writer = new StreamWriter(fs, Encoding.UTF8);

                if (isFirst)
                    writer.WriteLine(string.Join(",", colnames));


                try
                {
                    foreach (var row in csvData)
                    {
                        string line = string.Join(",", row);
                        int lineSize = Encoding.UTF8.GetByteCount(line + "\n");

                        if (MAX_CSV_SIZE < fs.Length + lineSize)
                        {
                            writer.Flush();
                            writer.Dispose();
                            fs.Dispose();

                            maxNum++;
                            targetFile = Path.Combine(folderPath, $"{filename}_{maxNum}.csv");

                            fs = new FileStream(
                                targetFile,
                                FileMode.Append,
                                FileAccess.Write,
                                FileShare.Read
                            );
                            writer = new StreamWriter(fs, Encoding.UTF8);
                            writer.WriteLine(string.Join(",", colnames));
                        }

                        writer.WriteLine(line);
                    }
                }
                finally
                {
                    writer?.Dispose();
                    fs?.Dispose();
                }

                Debug.Log($"<color=yellow>CSV 저장 완료: {targetFile}</color>");

                csvData.Clear();

            }
            catch (Exception ex)
            {
                Debug.LogError($"[CSV System] 저장 실패: {ex.Message}");
            }

        }

    }

    private void SetupSavePath()
    {
        string desktopPath = Environment.GetFolderPath(Environment.SpecialFolder.Desktop);
        string userIdFolder = string.IsNullOrEmpty(api_controller.USER_ID) ? "Unknown_User" : api_controller.USER_ID;
        fullSavePath = Path.Combine(desktopPath, "CRC_test/Session_Log/", userIdFolder);

        if (!Directory.Exists(fullSavePath)) Directory.CreateDirectory(fullSavePath);
    }

    // 스타트 버튼에 연결할 함수
    public void OnStartButtonClickSessionPlus()
    {
        // 혹시 모르니 경로가 비어있다면 다시 한번 확인
        if (string.IsNullOrEmpty(fullSavePath)) SetupSavePath();

        string binaryPath = Path.Combine(fullSavePath, "session_progress.dat");

        if (!isSessionIncrementedThisAppRun)
        {
            currentSessionNumber++;
            string currentDateTime = DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss");

            SessionData newData = new SessionData
            {
                session_id = currentSessionNumber,
                sessionDatetime = currentDateTime
            };

            SaveSessionBinary(binaryPath, newData);
            isSessionIncrementedThisAppRun = true;

            Debug.Log($"<color=yellow>세션 증가 완료! 현재 세션: {currentSessionNumber}</color>");
        }
        else
        {
            Debug.Log($"<color=cyan>이번 실행에서 이미 세션이 증가되었습니다. ID: {currentSessionNumber}</color>");
        }
    }

    private void OnApplicationQuit()
    {
        Save();
    }

    public void RestartRecording()
    {
        if (!isEnabledRecording) return;

        // 1. 구분선 추가 (분석 시 재시작 지점을 확인하기 위함)
        string[] restartMark = new string[coreLogs_colnames.Count];
        for (int i = 0; i < restartMark.Length; i++) restartMark[i] = "RESTART_MARK";
        coreLogs_csvData.Add(restartMark);

        // 1. 구분선 추가 (분석 시 재시작 지점을 확인하기 위함)
        restartMark = new string[sceneObjLogs_colnames.Count];
        for (int i = 0; i < restartMark.Length; i++) restartMark[i] = "RESTART_MARK";
        sceneObjLogs_csvData.Add(restartMark);

        // 2. 녹화 상태 재설정
        isRecording = true;

        Debug.Log($"데이터를 초기화하지 않고 재시작합니다. 현재 누적 행수: coreLog:{coreLogs_colnames.Count} sceneOLog:{sceneObjLogs_colnames.Count}");
    }

    public void StartRecording(string sceneName)
    {
        if (!isEnabledRecording) return;

        Time.timeScale = 1f;

        Initialize(sceneName);
    }

    // 씬 전환 시 호출할 함수
    public void SwitchSceneAndSave(string newSceneName)
    {
        if (isRecording)
        {
            Save();
        }
    }

    //세션로드
    public class SessionData
    {
        public int session_id;
        public string sessionDatetime;
    }

    private static bool isSessionIncrementedThisAppRun = false;

    private void LoadSessionProgress()
    {
        // 확장자를 .dat 또는 .bin으로 변경하는 것이 좋습니다.
        string binaryPath = Path.Combine(fullSavePath, "session_progress.dat");
        int lastSessionCount = 0;

        // 1. 바이너리 파일 읽기 및 마지막 세션 ID 추출
        if (File.Exists(binaryPath))
        {
            lastSessionCount = LoadLastSessionFromBinary(binaryPath);
        }

        // 2. 앱 실행 후 최초 1회만 세션 번호 증가 및 저장
        if (!isSessionIncrementedThisAppRun)
        {
            currentSessionNumber = lastSessionCount + 1;
            string currentDateTime = DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss");

            SessionData newData = new SessionData
            {
                session_id = currentSessionNumber,
                sessionDatetime = currentDateTime
            };

            SaveSessionBinary(binaryPath, newData);
            isSessionIncrementedThisAppRun = true;
        }

        Debug.Log($"<color=cyan>현재 세션 ID (바이너리 로드 완료): {currentSessionNumber}</color>");
    }

    private void LoadLastSessionOnly()
    {
        string binaryPath = Path.Combine(fullSavePath, "session_progress.dat");
        if (File.Exists(binaryPath))
        {
            currentSessionNumber = LoadLastSessionFromBinary(binaryPath);
        }
        else
        {
            currentSessionNumber = 0;
        }
    }

    private void SaveSessionBinary(string path, SessionData data)
    {
        try
        {
            // JSON 객체를 문자열로 변환 후 바이트 배열로 인코딩
            string jsonString = JsonConvert.SerializeObject(data);
            byte[] dataBytes = Encoding.UTF8.GetBytes(jsonString);

            // FileMode.Append를 사용하여 파일 끝에 이어붙임
            using (FileStream fs = new FileStream(path, FileMode.Append, FileAccess.Write))
            using (BinaryWriter writer = new BinaryWriter(fs))
            {
                // [데이터 길이]를 먼저 쓰고, 그 뒤에 [데이터]를 씀
                writer.Write(dataBytes.Length);
                writer.Write(dataBytes);
            }
        }
        catch (Exception e)
        {
            Debug.LogError($"바이너리 저장 실패: {e.Message}");
        }
    }

    private int LoadLastSessionFromBinary(string path)
    {
        int lastId = 0;
        int recordCount = 0; // 로그 출력을 위한 카운터

        try
        {
            using (FileStream fs = new FileStream(path, FileMode.Open, FileAccess.Read))
            using (BinaryReader reader = new BinaryReader(fs))
            {
                Debug.Log($"<color=white>[Binary Load] 파일 읽기 시작: {path}</color>");

                while (fs.Position < fs.Length)
                {
                    recordCount++;

                    int length = reader.ReadInt32();

                    byte[] dataBytes = reader.ReadBytes(length);

                    string jsonString = Encoding.UTF8.GetString(dataBytes);

                    //Debug.Log($"<color=yellow>[Record {recordCount}] 복구된 JSON: {jsonString}</color>");

                    var data = JsonConvert.DeserializeObject<SessionData>(jsonString);

                    if (data != null)
                    {
                        lastId = data.session_id;
                    }
                }
            }
            Debug.Log($"<color=white>[Binary Load] 총 {recordCount}개의 기록을 확인했습니다. 마지막 ID: {lastId}</color>");
        }
        catch (Exception e)
        {
            Debug.LogError($"바이너리 로드 중 오류: {e.Message}");
        }
        return lastId;
    }

    public void SetRecording(bool value)
    {
        isRecording = value;
    }

    public void BackToLobby()
    {
        // 로비로 돌아갈 때 이 플래그를 false로 해줘야 다음에 스타트를 누를 때 또 증가합니다.
        isSessionIncrementedThisAppRun = false;
        Save(); // 나가기 전 저장
    }

    public void IncrementSceneIndex()
    {
        this.sceneIndex++;

        // 데이터 기록 초기화 (타임스탬프와 타이머 리셋)
        timer = 0f;
        timestamp_counter = 0;
        recordedFrameCount = 0;

        // 이전까지 기록된 데이터가 있다면 저장 (선택 사항)
        if (coreLogs_csvData.Count > 0)
        {
            Save();
        }

        Debug.Log($"<color=orange>[Anim Event] 씬 인덱스 증가됨: {sceneIndex}</color>");
    }
}