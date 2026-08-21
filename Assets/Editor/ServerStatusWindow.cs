// ServerStatusWindow.cs — 두 서버의 상태를 한 창에서 보고, 웹 UI 를 여기서 띄운다.
//
// 체험을 하려면 서버가 둘 다 살아 있어야 한다. 웹만 떠 있으면 9단계 등록에서 막히고,
// Raon 만 떠 있으면 등록할 방법이 없다. 매번 터미널과 브라우저를 오가며 확인하는 대신
// 한 창에 모았다.
//
// Raon 은 여기서 못 켠다 — 교내망의 다른 기계에서 손으로 띄우는 서버다(`~/server/start.sh`).
// 웹 UI 는 이 PC 에서 도는 것이라 켤 수 있다.
//
//   메뉴: Tools > 서버 연결 상태 확인

using System;
using System.Diagnostics;
using System.IO;
using System.Net.Sockets;
using UnityEditor;
using UnityEngine;
using UnityEngine.Networking;

public class ServerStatusWindow : EditorWindow
{
    const int Port = 8500;
    const string Url = "http://localhost:8500";
    const string RaonFallback = "http://220.69.208.201:8000";
    const double AutoRefreshSec = 5;

    static readonly Color Green = new Color(0.35f, 0.75f, 0.42f);
    static readonly Color Amber = new Color(0.90f, 0.70f, 0.25f);
    static readonly Color Red = new Color(0.80f, 0.35f, 0.32f);
    static readonly Color Grey = new Color(0.45f, 0.47f, 0.50f);

    [MenuItem("Tools/서버 연결 상태 확인", false, 100)]
    static void ShowWindow()
    {
        var w = GetWindow<ServerStatusWindow>(false, "서버 상태", true);
        w.minSize = new Vector2(360, 300);
        w.Refresh();
    }

    // ── 상태 ──────────────────────────────────────────────────────

    [Serializable]
    class Health
    {
        public string status;
        public float vram_gb;
        public int uptime_sec;
        public int sessions;
        public int registered;
        public string current;
        public bool ready_to_talk;
        public bool cont;
    }

    [Serializable]
    class WebStatus
    {
        public bool engine;     // 화자 분리 엔진(extract_runner.py)이 제자리에 있는가
        public bool tripo;      // TRIPO_API_KEY 가 잡혔는가. 키 자체는 서버가 안 준다
    }

    bool _webUp;
    WebStatus _web;
    Health _raon;
    string _tripoInput = "";
    string _saved;              // 저장 직후 안내. 서버를 껐다 켜야 반영된다
    string _raonError;
    bool _raonChecking;
    double _nextRefresh;

    static string WebDir => Path.GetFullPath(Path.Combine(Application.dataPath, "..", "Web"));

    /// <summary>씬의 RaonVoiceClient 가 진짜 주소다. 씬이 안 열려 있을 때만 기본값을 쓴다.</summary>
    static string RaonUrl
    {
        get
        {
            var voice = FindObjectOfType<RaonVoiceClient>();
            var url = voice != null ? voice.serverUrl : null;
            return string.IsNullOrEmpty(url) ? RaonFallback : url.TrimEnd('/');
        }
    }

    void OnEnable() => Refresh();
    void OnFocus() => Refresh();

    void Update()
    {
        // 창을 보고 있을 때만 다시 묻는다. 띄워둔 채로 두면 Raon 로그가 /health 로
        // 도배되고, 어차피 안 보는 값이다.
        if (focusedWindow != this) return;
        if (EditorApplication.timeSinceStartup < _nextRefresh) return;
        Refresh();
    }

    void Refresh()
    {
        _nextRefresh = EditorApplication.timeSinceStartup + AutoRefreshSec;
        _webUp = WebUp();
        if (_webUp) CheckWeb(); else _web = null;
        CheckRaon();
        Repaint();
    }

    /// <summary>웹이 떠 있을 때만 `/status` 를 물어 엔진·Tripo 키 유무를 받는다.</summary>
    void CheckWeb()
    {
        var req = UnityWebRequest.Get($"{Url}/status");
        req.timeout = 8;
        var op = req.SendWebRequest();

        EditorApplication.CallbackFunction tick = null;
        tick = () =>
        {
            if (!op.isDone) return;
            EditorApplication.update -= tick;

            if (req.result == UnityWebRequest.Result.Success)
                try { _web = JsonUtility.FromJson<WebStatus>(req.downloadHandler.text); }
                catch { _web = null; }
            else _web = null;

            // 서버가 새 키를 물고 다시 떴으면 안내는 할 일을 다 한 것이다.
            if (_web != null && _web.tripo) _saved = null;

            req.Dispose();
            Repaint();
        };
        EditorApplication.update += tick;
    }

    /// <summary>포트가 열려 있으면 돌고 있는 것이다. localhost 라 사실상 즉시 답한다.</summary>
    static bool WebUp()
    {
        try
        {
            using (var c = new TcpClient())
                return c.ConnectAsync("127.0.0.1", Port).Wait(200) && c.Connected;
        }
        catch { return false; }
    }

    /// <summary>
    /// `/health` 를 물어본다. 인증이 없는 엔드포인트라 토큰은 필요 없다.
    /// 에디터에는 코루틴이 없으므로 update 로 완료를 지켜본다.
    /// </summary>
    void CheckRaon()
    {
        if (_raonChecking) return;
        _raonChecking = true;

        var req = UnityWebRequest.Get($"{RaonUrl}/health");
        req.timeout = 8;
        var op = req.SendWebRequest();

        EditorApplication.CallbackFunction tick = null;
        tick = () =>
        {
            if (!op.isDone) return;
            EditorApplication.update -= tick;

            if (req.result != UnityWebRequest.Result.Success)
            {
                _raon = null;
                _raonError = req.error;
            }
            else
            {
                try { _raon = JsonUtility.FromJson<Health>(req.downloadHandler.text); _raonError = null; }
                catch (Exception e) { _raon = null; _raonError = $"응답을 읽지 못했습니다 — {e.Message}"; }
            }

            req.Dispose();
            _raonChecking = false;
            Repaint();
        };
        EditorApplication.update += tick;
    }

    // ── 화면 ──────────────────────────────────────────────────────

    void OnGUI()
    {
        EditorGUILayout.Space(8);

        DrawWeb();
        EditorGUILayout.Space(10);
        Divider();
        EditorGUILayout.Space(10);
        DrawRaon();

        GUILayout.FlexibleSpace();
        Divider();
        EditorGUILayout.Space(6);

        using (new EditorGUILayout.HorizontalScope())
        {
            EditorGUILayout.LabelField(
                focusedWindow == this ? $"{AutoRefreshSec}초마다 자동 확인" : "창을 누르면 다시 확인",
                EditorStyles.miniLabel);
            GUILayout.FlexibleSpace();
            if (GUILayout.Button("지금 확인", GUILayout.Width(80))) Refresh();
        }
        EditorGUILayout.Space(6);
    }

    void DrawWeb()
    {
        Header("웹 UI — 등록 도구", _webUp ? Green : Grey, _webUp ? "켜짐" : "꺼짐");
        Sub(Url);

        EditorGUILayout.Space(6);
        using (new EditorGUILayout.HorizontalScope())
        {
            using (new EditorGUI.DisabledScope(_webUp))
                if (GUILayout.Button("서버 켜기", GUILayout.Height(26))) StartWeb();

            using (new EditorGUI.DisabledScope(!_webUp))
                if (GUILayout.Button("웹 사이트 열기", GUILayout.Height(26)))
                    Application.OpenURL(Url);
        }

        if (!_webUp)
        {
            Note("켜면 별도 콘솔 창에서 돕니다. 창을 닫는 것이 곧 종료입니다.");
            return;
        }

        if (_web == null) return;

        EditorGUILayout.Space(4);
        Row("화자 분리", _web.engine ? "준비됨" : "엔진 없음", _web.engine ? Green : Red);

        // 3D 모델이 안 뜨는 이유는 대개 이것 하나다.
        Row("Tripo 키", _web.tripo ? "있음" : "없음 — 인물 모델이 stub 으로 끝납니다",
            _web.tripo ? Green : Amber);

        DrawTripoField();
    }

    /// <summary>
    /// 키를 여기서 등록한다. 화면에 되돌려주지 않는다 — 있는지 없는지는 위 줄이 말해준다.
    /// 저장하면 Web/.env 에 쓰고, 서버가 뜰 때 읽으므로 껐다 켜야 반영된다.
    /// </summary>
    void DrawTripoField()
    {
        EditorGUILayout.Space(4);
        using (new EditorGUILayout.HorizontalScope())
        {
            GUILayout.Space(26);
            _tripoInput = EditorGUILayout.PasswordField(_tripoInput);

            using (new EditorGUI.DisabledScope(string.IsNullOrWhiteSpace(_tripoInput)))
                if (GUILayout.Button("저장", GUILayout.Width(56)))
                {
                    // 붙여넣기에 공백·줄바꿈이 섞여 오는 일이 잦다.
                    if (WriteEnv("TRIPO_API_KEY", _tripoInput.Trim()))
                    {
                        _tripoInput = "";
                        GUI.FocusControl(null);
                    }
                }
            GUILayout.Space(10);
        }

        Note(_saved ?? (_web.tripo
            ? "바꾸려면 새 키를 넣고 저장하세요. 없어도 대화는 정상입니다."
            : "여기에 넣으면 Web/.env 에 저장됩니다. 없어도 대화는 정상이고, 인물 3D 모델만 안 뜹니다."));
    }

    /// <summary>Web/.env 의 한 줄만 바꾼다. 나머지 줄과 주석은 그대로 둔다.</summary>
    bool WriteEnv(string key, string value)
    {
        string path = Path.Combine(WebDir, ".env");
        try
        {
            var lines = File.Exists(path)
                ? new System.Collections.Generic.List<string>(File.ReadAllLines(path))
                : new System.Collections.Generic.List<string>();

            bool replaced = false;
            for (int i = 0; i < lines.Count; i++)
            {
                var t = lines[i].TrimStart();
                if (t.StartsWith("#") || !t.StartsWith(key + "=")) continue;
                lines[i] = $"{key}={value}";
                replaced = true;
                break;
            }
            if (!replaced) lines.Add($"{key}={value}");

            // BOM 을 붙이면 파이썬이 첫 키 이름에 ﻿ 를 달고 읽어 안 잡힌다.
            File.WriteAllText(path, string.Join("\n", lines) + "\n",
                              new System.Text.UTF8Encoding(false));

            _saved = _webUp
                ? "저장했습니다. 서버를 껐다 켜야 반영됩니다 — 콘솔 창을 닫고 «서버 켜기»."
                : "저장했습니다. «서버 켜기» 를 누르면 반영됩니다.";
            return true;
        }
        catch (Exception e)
        {
            EditorUtility.DisplayDialog("키 저장", $"{path} 에 쓰지 못했습니다.\n\n{e.Message}", "확인");
            return false;
        }
    }

    void DrawRaon()
    {
        bool ready = _raon != null && _raon.status == "ready";
        bool loading = _raon != null && _raon.status != "ready";

        Header("Raon 서버 — 목소리·대화",
               ready ? Green : loading ? Amber : Red,
               ready ? "준비 완료" : loading ? "준비 중" : "연결 안 됨");
        Sub(RaonUrl);

        EditorGUILayout.Space(4);

        if (_raon == null)
        {
            Note(_raonChecking
                ? "확인 중…"
                : $"꺼져 있거나 교내망이 아닙니다.\n{_raonError}\n\n"
                  + "이 상태로는 인물 등록도 대화도 되지 않습니다.\n"
                  + "서버에서 ~/server/start.sh 를 실행해야 합니다.");
            return;
        }

        if (loading)
        {
            Note("모델을 올리는 중입니다. 약 20초 걸립니다.");
            return;
        }

        // 제일 알고 싶은 것 — 지금 누구로 대화가 되는가.
        if (_raon.ready_to_talk)
            Row("등록된 인물", _raon.current, Green);
        else
            Row("등록된 인물", "없음 — 웹에서 등록해야 합니다", Amber);

        Row("VRAM", $"{_raon.vram_gb:F1} GB", Grey);
        Row("가동", Uptime(_raon.uptime_sec), Grey);
        Row("대화 / 등록", $"{_raon.sessions}개 / {_raon.registered}개", Grey);
        Row("억양 복제", _raon.cont ? "켬 (RAON_CONT=1)" : "끔", Grey);
    }

    void StartWeb()
    {
        if (!File.Exists(Path.Combine(WebDir, "app.py")))
        {
            EditorUtility.DisplayDialog("웹 UI",
                $"Web/app.py 를 찾을 수 없습니다.\n\n{WebDir}", "확인");
            return;
        }

        try
        {
            // /k 로 창을 남긴다. 파이썬이 없거나 포트가 막혀 있으면 그 메시지를 봐야 하고,
            // 화자 분리 진행 문구도 거기 찍힌다.
            Process.Start(new ProcessStartInfo
            {
                FileName = "cmd.exe",
                Arguments = $"/k python -m uvicorn app:app --port {Port}",
                WorkingDirectory = WebDir,
                UseShellExecute = true,
            });
        }
        catch (Exception e)
        {
            EditorUtility.DisplayDialog("웹 UI", $"실행하지 못했습니다.\n\n{e.Message}", "확인");
            return;
        }

        WaitThenOpen();
    }

    /// <summary>뜰 때까지 기다렸다 브라우저를 연다. 바로 열면 아직 안 떠서 오류 화면이 뜬다.</summary>
    void WaitThenOpen()
    {
        double deadline = EditorApplication.timeSinceStartup + 20;
        double next = 0;

        EditorApplication.CallbackFunction tick = null;
        tick = () =>
        {
            if (EditorApplication.timeSinceStartup < next) return;
            next = EditorApplication.timeSinceStartup + 0.5;

            if (WebUp())
            {
                EditorApplication.update -= tick;
                _webUp = true;
                Repaint();
                Application.OpenURL(Url);
            }
            else if (EditorApplication.timeSinceStartup > deadline)
            {
                EditorApplication.update -= tick;
                UnityEngine.Debug.LogWarning(
                    "[웹 UI] 20초 안에 뜨지 않았습니다. 콘솔 창의 메시지를 확인하세요.");
            }
        };
        EditorApplication.update += tick;
    }

    // ── 그리기 도구 ───────────────────────────────────────────────

    static string Uptime(int sec)
    {
        if (sec < 3600) return $"{sec / 60}분";
        if (sec < 86400) return $"{sec / 3600}시간 {sec % 3600 / 60}분";
        return $"{sec / 86400}일 {sec % 86400 / 3600}시간";
    }

    static void Header(string title, Color dot, string state)
    {
        using (new EditorGUILayout.HorizontalScope())
        {
            GUILayout.Space(10);
            var r = GUILayoutUtility.GetRect(10, 10, GUILayout.Width(10), GUILayout.Height(10));
            r.y += 5;
            EditorGUI.DrawRect(r, dot);
            GUILayout.Space(6);
            EditorGUILayout.LabelField(title, EditorStyles.boldLabel);
            GUILayout.FlexibleSpace();
            var s = new GUIStyle(EditorStyles.label) { normal = { textColor = dot } };
            EditorGUILayout.LabelField(state, s, GUILayout.Width(80));
            GUILayout.Space(10);
        }
    }

    static void Sub(string text)
    {
        using (new EditorGUILayout.HorizontalScope())
        {
            GUILayout.Space(26);
            EditorGUILayout.LabelField(text, EditorStyles.miniLabel);
        }
    }

    static void Row(string label, string value, Color c)
    {
        using (new EditorGUILayout.HorizontalScope())
        {
            GUILayout.Space(26);
            EditorGUILayout.LabelField(label, EditorStyles.miniLabel, GUILayout.Width(90));
            var s = new GUIStyle(EditorStyles.miniLabel) { normal = { textColor = c } };
            EditorGUILayout.LabelField(value, s);
            GUILayout.Space(10);
        }
    }

    static void Note(string text)
    {
        using (new EditorGUILayout.HorizontalScope())
        {
            GUILayout.Space(26);
            var s = new GUIStyle(EditorStyles.miniLabel) { wordWrap = true };
            EditorGUILayout.LabelField(text, s);
            GUILayout.Space(10);
        }
    }

    static void Divider()
    {
        var r = EditorGUILayout.GetControlRect(false, 1);
        EditorGUI.DrawRect(r, new Color(0, 0, 0, 0.20f));
    }
}
