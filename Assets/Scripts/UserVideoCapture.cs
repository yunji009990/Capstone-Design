using UnityEngine;
using System.IO;
using System;
using System.Collections;

public class UserViewCapture : MonoBehaviour
{
    public Camera eyeCamera; // CenterEyeAnchor에 붙인 Camera
    public int captureWidth = 480;
    public int captureHeight = 270;

    private RenderTexture renderTexture;
    private Texture2D texture2D;
    private int frameCounter = 0;
    private string fullSavePath; // 최종 경로 (폴더 + 날짜)

    void Start()
    {
        // ✅ 데스크탑 경로 가져오기
        string desktopPath = Environment.GetFolderPath(Environment.SpecialFolder.Desktop);

        // ✅ 날짜 기반 폴더 이름 생성
        string timestamp = DateTime.Now.ToString("yyyy-MM-dd-HH-mm-ss");
        fullSavePath = Path.Combine(desktopPath, "CRC_test/Capture/", timestamp);

        // ✅ 경로 생성
        if (!Directory.Exists(fullSavePath))
            Directory.CreateDirectory(fullSavePath);

      //  Debug.Log("📂 캡처 저장 위치: " + fullSavePath);

        // 렌더 설정
        renderTexture = new RenderTexture(captureWidth, captureHeight, 24);
        eyeCamera.targetTexture = renderTexture;
        texture2D = new Texture2D(captureWidth, captureHeight, TextureFormat.RGB24, false);
    }

    void LateUpdate()
    {
        frameCounter++;

        if (frameCounter % 30 == 0)
        {
            StartCoroutine(CaptureFrameCoroutine());
        }
    }

    IEnumerator CaptureFrameCoroutine()
    {
        yield return new WaitForEndOfFrame();

        RenderTexture currentRT = RenderTexture.active;
        RenderTexture.active = renderTexture;

        eyeCamera.Render();
        texture2D.ReadPixels(new Rect(0, 0, captureWidth, captureHeight), 0, 0);
        texture2D.Apply();

        byte[] bytes = texture2D.EncodeToPNG();
        string filename = $"frame_{Time.frameCount}.png";

        string filePath = Path.Combine(fullSavePath, filename);
        File.WriteAllBytes(filePath, bytes);

        RenderTexture.active = currentRT;

     //   Debug.Log($"✅ [Frame {Time.frameCount}] 저장 완료: {filePath}");
    }
}
