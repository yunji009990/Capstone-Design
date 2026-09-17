// DialogueDiagnosticMenu.cs
// 진단 메뉴. 제품 UI·씬·프리팹을 건드리지 않고 Editor 메뉴에만 붙는다.
//
// Tools/Dialogue/Diagnostics/
//   Mark issue        진행 중인 체험에 "지금 이상하다" 표시만 남긴다. 응답·마이크는 그대로 둔다.
//   Create local report  tools/analyze_dialogue_trace.py --latest 를 창 없이 돌려 새 보고서를 만든다.
//   Open log folder   JSONL 폴더를 연다.
//
// 지키는 것:
// - 메뉴를 고르기 전에는 아무 일도 하지 않는다. InitializeOnLoad·주기 실행이 없다.
// - SSH 를 쓰지 않는다. 서버에 붙지 않고 이 PC 의 로그만 본다.
// - 보고 도구의 표준출력·표준오류를 콘솔에 덤프하지 않는다. 종료 코드와 보고서 경로만 알린다.
//   (로그 원문에 무엇이 들어 있든 콘솔·화면으로 새지 않게 하려는 것이다.)
// - Unity 2022 의 .NET 프로필에서 확실한 것만 쓴다. ProcessStartInfo.ArgumentList 에 기대지 않고
//   Windows 규칙으로 직접 따옴표를 붙인 Arguments 문자열을 만든다.

using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using UnityEditor;
using UnityEngine;
using Debug = UnityEngine.Debug;

public static class DialogueDiagnosticMenu
{
    const string ToolRelative = "tools/analyze_dialogue_trace.py";
    const string ReportsRelative = ".dialogue-work/reports";
    const int TimeoutSeconds = 120;
    const int MaxPathChars = 4096;   // stdout 한 줄을 들고 있는 상한

    static Process _process;
    static double _deadline;
    // 프로세스 스레드가 쓰고 메인 스레드가 읽는다.
    // stdout 은 마지막 한 줄만 상한을 걸어 들고 있고, stderr 는 내용을 전혀 보관하지 않는다.
    static readonly object _gate = new object();
    static string _lastOut;
    static bool _outDone, _errDone;

    [MenuItem("Tools/Dialogue/Diagnostics/Mark issue", priority = 40)]
    static void MarkIssue()
    {
        if (!EditorApplication.isPlaying)
        {
            Debug.LogWarning("[Diag] Play Mode 에서, 체험이 진행 중일 때만 표시할 수 있습니다.");
            return;
        }
        var voice = UnityEngine.Object.FindObjectOfType<DialogueVoiceClient>();
        if (voice == null) { Debug.LogWarning("[Diag] DialogueVoiceClient 가 씬에 없습니다."); return; }
        // 진행 중인 것을 멈추거나 새로 시작하지 않는다. 기록만 한 줄 늘어난다.
        if (voice.MarkIssue()) Debug.Log("[Diag] 표시를 남겼습니다. trace=" + voice.TraceId);
        else Debug.LogWarning("[Diag] 기록 중인 체험이 없어 표시할 곳이 없습니다.");
    }

    [MenuItem("Tools/Dialogue/Diagnostics/Open log folder", priority = 41)]
    static void OpenLogFolder()
    {
        string directory = DialogueTraceLog.DefaultDirectory();
        if (!Directory.Exists(directory))
        {
            Debug.LogWarning("[Diag] 로그 폴더가 아직 없습니다: " + directory);
            return;
        }
        EditorUtility.RevealInFinder(directory);
    }

    [MenuItem("Tools/Dialogue/Diagnostics/Create local report", priority = 42)]
    static void CreateLocalReport()
    {
        if (_process != null) { Debug.LogWarning("[Diag] 보고서를 이미 만들고 있습니다."); return; }

        string root = ProjectRoot();
        string tool = Path.Combine(root, ToolRelative.Replace('/', Path.DirectorySeparatorChar));
        if (!File.Exists(tool))
        {
            Debug.LogWarning("[Diag] 보고 도구가 없습니다: " + ToolRelative);
            return;
        }
        string python = FindPython(root);

        var info = new ProcessStartInfo
        {
            FileName = python,
            // ArgumentList 대신 Windows 규칙으로 직접 만든다. 경로에 공백이 있어도 안전하다.
            Arguments = Quote(tool) + " --latest",
            WorkingDirectory = root,
            UseShellExecute = false,
            CreateNoWindow = true,
            // 파이프가 차서 멈추지 않게 읽기는 하되, 내용은 버리고 줄 수만 센다.
            RedirectStandardOutput = true,
            RedirectStandardError = true,
        };

        lock (_gate) { _lastOut = null; _outDone = _errDone = false; }
        try
        {
            _process = new Process { StartInfo = info };
            _process.OutputDataReceived += (s, e) =>
            {
                lock (_gate)
                {
                    if (e.Data == null) { _outDone = true; return; }   // EOF
                    string line = e.Data.Trim();
                    // 마지막 한 줄만 후보로 남긴다. 상한을 넘는 줄은 아예 들고 있지 않는다.
                    if (line.Length > 0 && line.Length <= MaxPathChars) _lastOut = line;
                }
            };
            // stderr 는 파이프가 막히지 않게 읽기만 하고 버린다. 내용을 보관하지 않는다.
            _process.ErrorDataReceived += (s, e) => { lock (_gate) { if (e.Data == null) _errDone = true; } };
            _process.Start();
            _process.BeginOutputReadLine();
            _process.BeginErrorReadLine();
        }
        catch (Exception e)
        {
            // 실행 자체가 안 된 경우다. 예외 형식만 남기고 원문 출력은 만들지 않는다.
            Release();
            Debug.LogWarning("[Diag] 보고 도구를 실행하지 못했습니다(" + e.GetType().Name + "). python=" + python);
            Announce("start_failed", null);
            return;
        }

        Debug.Log("[Diag] 보고서를 만드는 중입니다. 끝나면 결과만 알립니다.");
        _deadline = EditorApplication.timeSinceStartup + TimeoutSeconds;
        EditorApplication.update += Poll;

        // 메인 스레드에서 끝을 기다린다. 창은 뜨지 않고 편집기도 멈추지 않는다.
        void Poll()
        {
            var process = _process;
            if (process == null) { EditorApplication.update -= Poll; return; }
            // 종료만으로는 부족하다. 비동기 reader 가 EOF 에 닿아야 마지막 줄이 다 들어와 있다.
            bool exited = process.HasExited;
            bool drained;
            lock (_gate) drained = _outDone && _errDone;
            if (!exited || !drained)
            {
                if (EditorApplication.timeSinceStartup < _deadline) return;
                EditorApplication.update -= Poll;
                if (!exited) { try { process.Kill(); } catch (Exception) { } }
                Release();
                Announce(exited ? "reader_incomplete" : "timeout", null);
                return;
            }

            EditorApplication.update -= Poll;
            int code = process.ExitCode;
            string candidate;
            lock (_gate) candidate = _lastOut;
            Release();

            if (code != 0) { Announce("tool_failed", null); return; }
            string status = Validate(root, candidate, out string path);
            Announce(status, path);
        }
    }

    /// <summary>도구가 마지막 줄로 알려 준 경로만 받아들인다.
    /// 저장소의 보고서 폴더 아래이고, 이름이 report.md 이고, 실제로 있는 파일일 때만 통과한다.
    /// 통과하지 못한 문자열은 콘솔에 내보내지 않는다.</summary>
    static string Validate(string root, string candidate, out string path)
    {
        path = null;
        if (string.IsNullOrEmpty(candidate)) return "no_report_path";
        string full;
        try
        {
            if (!Path.IsPathRooted(candidate)) return "path_rejected";
            full = Path.GetFullPath(candidate);
        }
        catch (Exception) { return "path_rejected"; }

        if (!string.Equals(Path.GetFileName(full), "report.md", StringComparison.OrdinalIgnoreCase))
            return "path_rejected";
        string reports = Path.GetFullPath(Path.Combine(root, ReportsRelative.Replace('/', Path.DirectorySeparatorChar)));
        string prefix = reports.TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
        if (!full.StartsWith(prefix, StringComparison.OrdinalIgnoreCase)) return "path_rejected";
        if (!File.Exists(full)) return "missing_file";
        path = full;
        return "ok";
    }

    /// <summary>콘솔에는 고정 상태 코드와 검증을 통과한 경로만 남긴다.</summary>
    static void Announce(string status, string path)
    {
        string text = "[Diag] report status=" + status + (path == null ? "" : " path=" + path);
        if (status == "ok") Debug.Log(text);
        else Debug.LogWarning(text + " — 도구를 직접 실행해 원인을 확인하세요.");
    }

    /// <summary>프로세스 handle 을 반드시 놓는다. 중단·실패 경로에서도 지나친다.</summary>
    static void Release()
    {
        var process = _process;
        _process = null;
        if (process == null) return;
        try { process.Dispose(); } catch (Exception) { }
    }

    static string FindPython(string root)
    {
        string windows = Path.Combine(root, ".venv-dialogue", "Scripts", "python.exe");
        if (File.Exists(windows)) return windows;
        string unix = Path.Combine(root, ".venv-dialogue", "bin", "python");
        if (File.Exists(unix)) return unix;
        return "python";
    }

    static string ProjectRoot() => Path.GetFullPath(Path.Combine(Application.dataPath, ".."));

    /// <summary>Windows 명령행 규칙으로 인수 하나를 감싼다. 끝의 역슬래시가 닫는 따옴표를 먹지 않게 한다.</summary>
    static string Quote(string value)
    {
        if (string.IsNullOrEmpty(value)) return "\"\"";
        var text = new StringBuilder(value.Length + 8);
        text.Append('"');
        int slashes = 0;
        foreach (char c in value)
        {
            if (c == '\\') { slashes++; continue; }
            if (c == '"') { text.Append('\\', slashes * 2 + 1).Append('"'); slashes = 0; continue; }
            if (slashes > 0) { text.Append('\\', slashes); slashes = 0; }
            text.Append(c);
        }
        text.Append('\\', slashes * 2).Append('"');
        return text.ToString();
    }
}
