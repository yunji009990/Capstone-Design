using System;
using System.IO;
using UnityEngine;
using UnityEngine.UI;

public class VoiceRecorder2 : MonoBehaviour
{
    private AudioClip recordingClip;

    private const int HEADER_SIZE = 44;

    private bool isRecording = false;
    private string filename;

    [Header("UI")]
    public Button recordButton;
    public Color recordingColor = Color.red;
    private Color originalColor;

    private void Start()
    {
        if (recordButton != null)
            originalColor = recordButton.image.color;
    }

    // 버튼에서 이 함수 하나만 호출
    public void OnRecordButtonClicked()
    {
        if (!isRecording)
        {
            // 녹음 시작
            filename = "record_" + DateTime.Now.ToString("yyyyMMdd_HHmmss") + ".wav";
            StartRecording();

            isRecording = true;
            if (recordButton != null)
                recordButton.image.color = recordingColor;

            Debug.Log("Recording Start");
        }
        else
        {
            // 녹음 종료 + 자동 저장
            StopRecording();

            isRecording = false;
            if (recordButton != null)
                recordButton.image.color = originalColor;

            Debug.Log("Recording Stop & Saved");
        }
    }

    // 녹음 시작
    private void StartRecording()
    {
        recordingClip = Microphone.Start(null, true, 10, 44100);
    }

    // 녹음 종료
    private void StopRecording()
    {
        if (!Microphone.IsRecording(null))
            return;

        int lastTime = Microphone.GetPosition(null);
        Microphone.End(null);

        if (lastTime <= 0)
            return;

        float[] samples = new float[recordingClip.samples];
        recordingClip.GetData(samples, 0);

        float[] cutSamples = new float[lastTime];
        Array.Copy(samples, cutSamples, lastTime);

        recordingClip = AudioClip.Create(
            "RecordedClip",
            cutSamples.Length,
            1,
            44100,
            false
        );
        recordingClip.SetData(cutSamples, 0);

        Save();
    }

    // WAV 파일 저장
    private bool Save()
    {
        if (recordingClip == null)
            return false;

        string desktopPath = Environment.GetFolderPath(Environment.SpecialFolder.Desktop);
        string timestamp = DateTime.Now.ToString("yyyy-MM-dd-HH-mm-ss");

        string saveFolderPath = Path.Combine(
            desktopPath,
            "CRC_test",
            "Voice_Recording",
            timestamp
        );

        Directory.CreateDirectory(saveFolderPath);

        string filePath = Path.Combine(saveFolderPath, filename);

        using (FileStream fileStream = CreateEmpty(filePath))
        {
            ConvertAndWrite(fileStream, recordingClip);
            WriteHeader(fileStream, recordingClip);
        }


        Debug.Log($"Saved Voice File: {filePath}");

        StartCoroutine(GetComponent<WhisperSTT>().SendToWhisper(filePath));

        return true;
    }

    // 빈 WAV 파일 생성
    private FileStream CreateEmpty(string filePath)
    {
        FileStream fileStream = new FileStream(filePath, FileMode.Create);
        for (int i = 0; i < HEADER_SIZE; i++)
            fileStream.WriteByte(0);

        return fileStream;
    }

    // 오디오 데이터 변환 및 쓰기
    private void ConvertAndWrite(FileStream fileStream, AudioClip clip)
    {
        float[] samples = new float[clip.samples];
        clip.GetData(samples, 0);

        short[] intData = new short[samples.Length];
        byte[] bytesData = new byte[samples.Length * 2];

        const int rescaleFactor = 32767;

        for (int i = 0; i < samples.Length; i++)
        {
            intData[i] = (short)(samples[i] * rescaleFactor);
            byte[] byteArr = BitConverter.GetBytes(intData[i]);
            byteArr.CopyTo(bytesData, i * 2);
        }

        fileStream.Write(bytesData, 0, bytesData.Length);
    }

    // WAV 헤더 작성
    private void WriteHeader(FileStream fileStream, AudioClip clip)
    {
        int hz = clip.frequency;
        int channels = clip.channels;
        int samples = clip.samples;

        fileStream.Seek(0, SeekOrigin.Begin);

        fileStream.Write(System.Text.Encoding.UTF8.GetBytes("RIFF"), 0, 4);
        fileStream.Write(BitConverter.GetBytes(fileStream.Length - 8), 0, 4);
        fileStream.Write(System.Text.Encoding.UTF8.GetBytes("WAVE"), 0, 4);
        fileStream.Write(System.Text.Encoding.UTF8.GetBytes("fmt "), 0, 4);
        fileStream.Write(BitConverter.GetBytes(16), 0, 4);
        fileStream.Write(BitConverter.GetBytes((ushort)1), 0, 2);
        fileStream.Write(BitConverter.GetBytes((ushort)channels), 0, 2);
        fileStream.Write(BitConverter.GetBytes(hz), 0, 4);
        fileStream.Write(BitConverter.GetBytes(hz * channels * 2), 0, 4);
        fileStream.Write(BitConverter.GetBytes((ushort)(channels * 2)), 0, 2);
        fileStream.Write(BitConverter.GetBytes((ushort)16), 0, 2);
        fileStream.Write(System.Text.Encoding.UTF8.GetBytes("data"), 0, 4);
        fileStream.Write(BitConverter.GetBytes(samples * channels * 2), 0, 4);
    }
}
