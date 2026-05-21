using System.Collections;
using System.IO;
using UnityEngine;

public class CameraFrameRecorder : MonoBehaviour
{
    public Camera observerCamera;      // ObserverCamera를 연결
    public int width = 1920;           // 저장할 이미지의 너비
    public int height = 1080;          // 저장할 이미지의 높이
    public int frameRate = 30;         // 캡처할 프레임 속도
    public string baseFolder = "Recordings"; // 기본 폴더 이름

    private string outputFolder;       // 실제로 사용할 출력 폴더 이름
    private RenderTexture renderTexture;  // 카메라 출력을 담을 RenderTexture
    private Texture2D texture2D;          // RenderTexture를 PNG로 변환할 텍스처
    private Coroutine captureCoroutine;   // 캡처 코루틴을 위한 참조

    void Start()
    {
        // RenderTexture 및 Texture2D 초기화
        renderTexture = new RenderTexture(width, height, 24); // 깊이 버퍼 24비트
        observerCamera.targetTexture = renderTexture;         // ObserverCamera에 적용
        texture2D = new Texture2D(width, height, TextureFormat.RGB24, false);
    }

    // 녹화 시작
    public void StartRecording()
    {
        // 날짜와 시간을 기반으로 폴더 이름 생성
        string date = System.DateTime.Now.ToString("yyyy-MM-dd_HH-mm-ss");
        outputFolder = Path.Combine(baseFolder, "Session_" + date);

        // 폴더 생성
        if (!Directory.Exists(outputFolder))
        {
            Directory.CreateDirectory(outputFolder);
        }

        // 캡처 코루틴 시작
        captureCoroutine = StartCoroutine(CaptureFrames());
    }

    // 녹화 정지
    public void StopRecording()
    {
        if (captureCoroutine != null)
        {
            StopCoroutine(captureCoroutine);
            captureCoroutine = null;
            Debug.Log("녹화가 중지되었습니다.");
        }
    }

    // 프레임을 캡처하는 코루틴
    IEnumerator CaptureFrames()
    {
        while (true)
        {
            yield return new WaitForEndOfFrame(); // 모든 렌더링 후 프레임을 캡처

            // RenderTexture에서 픽셀 읽기
            RenderTexture.active = renderTexture;
            texture2D.ReadPixels(new Rect(0, 0, width, height), 0, 0);
            texture2D.Apply();
            RenderTexture.active = null;

            // 현재 시간을 기반으로 파일명 생성
            string timestamp = System.DateTime.Now.ToString("yyyy-MM-dd_HH-mm-ss"); // 연, 월, 일, 시, 분, 초 형식
            string filename = $"{outputFolder}/frame_{timestamp}.png";

            // PNG 파일로 저장
            byte[] bytes = texture2D.EncodeToPNG();
            File.WriteAllBytes(filename, bytes);
        }
    }
}
