using System.IO;
using System.Threading.Tasks;
using GLTFast;
using UnityEngine;

public class PersonaSpawner : MonoBehaviour
{
    [Tooltip("Streamlit data 폴더의 절대 경로 (예: C:/.../다시봄_설문시스템/data)")]
    public string dataRoot = @"C:\Users\user\Desktop\다시봄_설문시스템\data";

    [Tooltip("이번 체험 세션 ID (예: 20260521-001)")]
    public string sessionId = "20260521-001";

    public Transform spawnPoint;
    public float retryIntervalSec = 2f;

    async void Start()
    {
        string glbPath = Path.Combine(dataRoot, "sessions", sessionId, "model.glb");

        // 백엔드가 아직 모델을 만들고 있을 수 있음 → 파일 생길 때까지 대기
        while (!File.Exists(glbPath))
        {
            Debug.Log($"[PersonaSpawner] 대기 중: {glbPath}");
            await Task.Delay((int)(retryIntervalSec * 1000));
        }

        var gltf = new GltfImport();
        bool ok = await gltf.Load(glbPath);
        if (!ok) { Debug.LogError("모델 로드 실패"); return; }

        var go = new GameObject($"Persona_{sessionId}");
        go.transform.SetPositionAndRotation(spawnPoint.position, spawnPoint.rotation);
        await gltf.InstantiateMainSceneAsync(go.transform);
    }
}