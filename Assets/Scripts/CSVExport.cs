using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using UnityEngine;
using static api_controller;



public class CSVExport : MonoBehaviour
{
    //[SerializeField] private CSVWriter csvWriter; ? 이거 왜?
    [SerializeField] private api_controller api;

    public void OnClickSendButton()
    {
        if (api == null) api = api_controller.instance;
        if (api == null) return;

        string desktopPath = Environment.GetFolderPath(Environment.SpecialFolder.Desktop);
        string user_id = string.IsNullOrEmpty(api_controller.USER_ID) ? "Unknown_User" : api_controller.USER_ID;
        string savePath = Path.Combine(desktopPath, "CRC_test", "Session_Log", user_id);



        if (!Directory.Exists(savePath))
        {
            Debug.LogWarning("savePath가 존재하지 않습니다.");
            return;
        }

        StartCoroutine(Send(savePath));
    }






    IEnumerator Send(string savePath)
    {
        Debug.Log("Send Start");

        string datFilePath = Path.Combine(savePath, "session_progress.dat");
        StringBuilder jsonlContent = new StringBuilder();

        if (!File.Exists(datFilePath))
        {
            Debug.LogWarning(".dat 세션 파일이 존재하지 않습니다.");
            yield break;
        }

        Dictionary<string, string> coreLogs_files = new Dictionary<string, string>();
        Dictionary<string, string> sceneObjLogs_files = new Dictionary<string, string>();

        Upload_File_Dic(savePath, coreLogs_files, sceneObjLogs_files);

        string upload_id = "";
        string sessions_jsonl;

        try
        {
            using (FileStream fs = new FileStream(datFilePath, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
            using (BinaryReader reader = new BinaryReader(fs))
            {
                while (fs.Position < fs.Length)
                {
                    int length = reader.ReadInt32();
                    byte[] data = reader.ReadBytes(length);
                    string jsonLine = Encoding.UTF8.GetString(data);
                    jsonlContent.AppendLine(jsonLine);
                }
            }
            sessions_jsonl = jsonlContent.ToString();
        }
        catch (Exception e)
        {
            Debug.LogError($"[.dat 변환 실패] {e.Message}");
            yield break;
        }

        // 1. 업로드 세션 생성
        yield return api.upload_create(new UploadCreateData(sessions_jsonl, new List<string>(coreLogs_files.Keys), new List<string>(sceneObjLogs_files.Keys)),
            (string id) => { upload_id = id; });

        if (string.IsNullOrEmpty(upload_id))
        {
            Debug.LogError("Upload ID를 받아오지 못했습니다.");
            yield break;
        }

        // 2. 코어로그 전송 및 재전송 로직 (생략 - 기존과 동일)
        foreach (string filename in coreLogs_files.Keys)
            yield return api.upload_logs(upload_id, filename, coreLogs_files[filename], UploadLogType.CORELOGS);

        List<string> missing_CoreFileList = new List<string>();
        yield return api.upload_completeCheck(upload_id, UploadLogType.CORELOGS, missing_CoreFileList);
        if (0 < missing_CoreFileList.Count)
        {
            foreach (string filename in missing_CoreFileList)
                yield return api.upload_logs(upload_id, filename, coreLogs_files[filename], UploadLogType.CORELOGS);
        }

        // 3. 씬옵젝로그 전송 및 재전송 로직 (생략 - 기존과 동일)
        foreach (string filename in sceneObjLogs_files.Keys)
            yield return api.upload_logs(upload_id, filename, sceneObjLogs_files[filename], UploadLogType.SCENEOBJLOGS);

        List<string> missing_SceneObjFileList = new List<string>();
        yield return api.upload_completeCheck(upload_id, UploadLogType.SCENEOBJLOGS, missing_SceneObjFileList);
        if (0 < missing_SceneObjFileList.Count)
        {
            foreach (string filename in missing_SceneObjFileList)
                yield return api.upload_logs(upload_id, filename, sceneObjLogs_files[filename], UploadLogType.SCENEOBJLOGS);
        }

        // 4. Finalize 전송
        yield return api.upload_finalize(upload_id);

        // ---------------------------------------------------------
        // 5. 파일 삭제 로직 추가 (Finalize 이후에 실행)
        // ---------------------------------------------------------
        Debug.Log("<color=yellow>전송 완료. CSV 파일 삭제를 시작합니다.</color>");
        DeleteUploadedFiles(coreLogs_files);
        DeleteUploadedFiles(sceneObjLogs_files);
        Debug.Log("<color=cyan>CSV 파일 삭제 완료. (.dat 파일은 유지됨)</color>");
    }

    // 파일 삭제를 위한 헬퍼 함수
    void DeleteUploadedFiles(Dictionary<string, string> files_Dic)
    {
        foreach (var filePath in files_Dic.Values)
        {
            try
            {
                if (File.Exists(filePath))
                {
                    File.Delete(filePath);
                    Debug.Log($"파일 삭제 성공: {Path.GetFileName(filePath)}");
                }
            }
            catch (Exception e)
            {
                Debug.LogError($"파일 삭제 실패 ({Path.GetFileName(filePath)}): {e.Message}");
            }
        }
    }


    void Upload_File_Dic(string savePath, Dictionary<string, string> coreLogs_files, Dictionary<string, string> sceneObjLogs_files)
    {

        files_from_folder(CSVWriter.coreLogs_Foldername, CSVWriter.coreLogs_filename, coreLogs_files);
        files_from_folder(CSVWriter.sceneObjLogs_Foldername, CSVWriter.sceneObjLogs_filename, sceneObjLogs_files);

        void files_from_folder(string foldername, string filename, Dictionary<string, string> files_Dic)
        {
            string folderPath = Path.Combine(savePath, foldername);

            if (!Directory.Exists(folderPath))
                Debug.LogWarning($"{folderPath}이 존재하지 않습니다.");

            foreach (var file in Directory.GetFiles(folderPath, "*.csv"))
            {
                string name = Path.GetFileName(file);

                if (name == filename || name.StartsWith(filename + "_"))
                {
                    files_Dic.Add(name, file);
                }
            }
        }
    }

}
