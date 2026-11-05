using System;
using System.Collections;
using System.IO;
using System.Linq;
using UnityEngine;

/// <summary>명시적인 검사 인수를 준 플레이어에서만 실행하는 배포 점검. 일반 체험에는 생성되지 않는다.</summary>
public sealed class LocalDemoBuildCheck : MonoBehaviour
{
    string reportPath;

    [Serializable]
    sealed class Report
    {
        public bool passed, localApi, serverReady, hasSession, modelReady, preparedBody, seatedFootPlacement;
        public int skinnedMeshes, unsupportedMaterials;
        public string scene, arrivalPhase;
        public string[] materialIssues;
    }

    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
    static void Install()
    {
        var args = Environment.GetCommandLineArgs();
        int index = Array.IndexOf(args, "--local-demo-smoke");
        if (index < 0 || index + 1 >= args.Length) return;
        var check = new GameObject("LocalDemoBuildCheck").AddComponent<LocalDemoBuildCheck>();
        check.reportPath = Path.GetFullPath(args[index + 1]);
        DontDestroyOnLoad(check.gameObject);
    }

    IEnumerator Start()
    {
        yield return null;
        var start = FindObjectOfType<StartScreen>();
        if (start != null) start.Begin();
        float deadline = Time.realtimeSinceStartup + 150f;
        Report report = null;
        while (Time.realtimeSinceStartup < deadline)
        {
            var voice = FindObjectOfType<DialogueVoiceClient>();
            var persona = FindObjectOfType<PersonaSpawner>();
            var arrival = FindObjectOfType<PersonaArrival>();
            var skins = FindObjectsOfType<SkinnedMeshRenderer>();
            report = new Report
            {
                localApi = voice != null && voice.UsesLocalApi,
                serverReady = voice != null && voice.serverReady,
                hasSession = voice != null && voice.HasSession,
                modelReady = persona != null && persona.ModelReady,
                preparedBody = File.Exists(PersonaLocalBodyPreview.SettingsPath),
                seatedFootPlacement = FindObjectOfType<PersonaSeatedFootPlacement>() != null,
                skinnedMeshes = skins.Length,
                unsupportedMaterials = skins.SelectMany(s => s.sharedMaterials)
                    .Count(m => m == null || m.shader == null || !m.shader.isSupported || m.shader.name == "Hidden/InternalErrorShader"),
                materialIssues = skins.SelectMany(s => s.sharedMaterials)
                    .Where(m => m == null || m.shader == null || !m.shader.isSupported || m.shader.name == "Hidden/InternalErrorShader")
                    .Select(m => m == null ? "missing material" : m.shader == null ? "missing shader" : m.shader.name)
                    .Distinct().ToArray(),
                scene = UnityEngine.SceneManagement.SceneManager.GetActiveScene().name,
                arrivalPhase = arrival != null ? arrival.phase.ToString() : "none"
            };
            report.passed = report.localApi && report.serverReady && report.hasSession && report.modelReady
                && report.preparedBody && report.seatedFootPlacement && report.skinnedMeshes > 0 && report.unsupportedMaterials == 0;
            if (report.passed) break;
            yield return new WaitForSecondsRealtime(1f);
        }
        // 이동·인사·착석을 볼 수 있도록 정상 입장 흐름을 더 진행한다.
        if (report != null && report.passed)
        {
            yield return new WaitForSecondsRealtime(18f);
            var arrival = FindObjectOfType<PersonaArrival>();
            report.arrivalPhase = arrival != null ? arrival.phase.ToString() : "none";
        }
        Directory.CreateDirectory(Path.GetDirectoryName(reportPath));
        File.WriteAllText(reportPath, JsonUtility.ToJson(report, true));
        ScreenCapture.CaptureScreenshot(Path.ChangeExtension(reportPath, ".png"));
        yield return new WaitForSecondsRealtime(2f);
        Application.Quit(report != null && report.passed ? 0 : 2);
    }
}
