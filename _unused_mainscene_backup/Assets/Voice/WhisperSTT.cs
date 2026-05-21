using System;
using System.Collections;
using System.IO;
using UnityEngine;
using UnityEngine.Networking;

public class WhisperSTT : MonoBehaviour
{
    [SerializeField] private string openAIKey = "YOUR_API_KEY";   // 여기에 본인 키 입력

    // 파일 경로를 직접 받아 Whisper로 전송하는 함수
    public IEnumerator SendToWhisper(string filePath)
    {
        if (string.IsNullOrEmpty(openAIKey) || openAIKey == "YOUR_API_KEY")
        {
            Debug.LogError("OpenAI API Key가 설정되지 않았습니다.");
            yield break;
        }

        if (string.IsNullOrEmpty(filePath))
        {
            Debug.LogError("파일 경로가 비어있습니다.");
            yield break;
        }

        if (!File.Exists(filePath))
        {
            Debug.LogError("파일이 존재하지 않습니다: " + filePath);
            yield break;
        }

        byte[] fileBytes = File.ReadAllBytes(filePath);

        WWWForm form = new WWWForm();
        form.AddBinaryData("file", fileBytes, Path.GetFileName(filePath), "audio/wav");
        form.AddField("model", "gpt-4o-transcribe");

        UnityWebRequest req = UnityWebRequest.Post(
            "https://api.openai.com/v1/audio/transcriptions",
            form
        );
        req.SetRequestHeader("Authorization", "Bearer " + openAIKey);

        Debug.Log("Whisper 요청 전송 중...");

        yield return req.SendWebRequest();

        if (req.result != UnityWebRequest.Result.Success)
        {
            Debug.LogError("Whisper Error: " + req.error);

            Debug.LogError("Response: " + req.downloadHandler.text);
            yield break;
        }

        Debug.Log("Whisper Response: " + req.downloadHandler.text);

        // 필요하다면 JSON 파싱
        WhisperResponse response = JsonUtility.FromJson<WhisperResponse>(req.downloadHandler.text);
        Debug.Log("Transcription Text: " + response.text);
    }

    [Serializable]
    public class WhisperResponse
    {
        public string text;
    }
}
