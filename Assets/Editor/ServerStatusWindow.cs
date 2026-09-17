using System;
using System.Net.Sockets;
using UnityEditor;
using UnityEngine;
using UnityEngine.Networking;

public class ServerStatusWindow : EditorWindow
{
    const int Port = 8500;
    // 웹도 Session 과 같은 기계에 있다. localhost 가 아니다.
    const string Url = "http://220.69.208.201:8500";
    const string SessionFallback = "http://220.69.208.201:8000";
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
        public string device;
        public float uptime_sec;
        public int registered;
        public string current;
        public bool ready_to_talk;
    }

    [Serializable]
    class WebStatus
    {
        public bool engine;     // 화자 분리 엔진(extract_runner.py)이 제자리에 있는가
        public bool tripo;      // TRIPO_API_KEY 가 잡혔는가. 키 자체는 서버가 안 준다
    }

    bool _webUp;
    WebStatus _web;
    string _tripoInput = "";
    string _adminPw = "";
    string _saved;              // 보낸 직후 안내
    bool _busy;                 // 보내는 동안 단추를 막는다
    Health _session;
    string _sessionError;
    bool _sessionChecking;
    double _nextRefresh;


    /// <summary>씬의 DialogueVoiceClient 가 진짜 주소다. 씬이 안 열려 있을 때만 기본값을 쓴다.</summary>
    static string SessionUrl
    {
        get
        {
            var voice = FindObjectOfType<DialogueVoiceClient>();
            var url = voice != null ? voice.serverUrl : null;
            return string.IsNullOrEmpty(url) ? SessionFallback : url.TrimEnd('/');
        }
    }

    void OnEnable() => Refresh();
    void OnFocus() => Refresh();

    void Update()
    {
        // 창을 보고 있을 때만 다시 묻는다. 띄워둔 채로 두면 Session 로그가 /health 로
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
        CheckSession();
        CheckDialogue();
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

            req.Dispose();
            Repaint();
        };
        EditorApplication.update += tick;
    }

    /// <summary>포트가 열려 있으면 돌고 있는 것이다.
    /// **교내망을 건너가므로 200ms 로는 모자란다** — localhost 이던 때의 값이었다.</summary>
    static bool WebUp()
    {
        try
        {
            using (var c = new TcpClient())
                return c.ConnectAsync("220.69.208.201", Port).Wait(1500) && c.Connected;
        }
        catch { return false; }
    }

    /// <summary>
    /// `/health` 를 물어본다. 인증이 없는 엔드포인트라 토큰은 필요 없다.
    /// 에디터에는 코루틴이 없으므로 update 로 완료를 지켜본다.
    /// </summary>
    void CheckSession()
    {
        if (_sessionChecking) return;
        _sessionChecking = true;

        var req = UnityWebRequest.Get($"{SessionUrl}/health");
        req.timeout = 8;
        var op = req.SendWebRequest();

        EditorApplication.CallbackFunction tick = null;
        tick = () =>
        {
            if (!op.isDone) return;
            EditorApplication.update -= tick;

            if (req.result != UnityWebRequest.Result.Success)
            {
                _session = null;
                _sessionError = req.error;
            }
            else
            {
                try { _session = JsonUtility.FromJson<Health>(req.downloadHandler.text); _sessionError = null; }
                catch (Exception e) { _session = null; _sessionError = $"응답을 읽지 못했습니다 — {e.Message}"; }
            }

            req.Dispose();
            _sessionChecking = false;
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
        DrawSession();
        EditorGUILayout.Space(10);
        DrawDialogue();

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
        // **꺼져 있어도 열 수 있게 둔다.** 서버가 잠깐 안 보이는 것과 주소가
        // 틀린 것은 다른 문제인데, 단추를 막아 두면 그 둘을 구별할 수 없다.
        if (GUILayout.Button("등록 화면 열기", GUILayout.Height(26)))
            Application.OpenURL(Url);

        if (!_webUp)
        {
            Note("서버에서 돕니다. 꺼져 있으면 그 기계에서 켜야 합니다 —\n"
                 + "ssh raon \"cd ~/webapp; ./web_start.sh\"");
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
    /// 3D 키를 **서버로** 보낸다. 웹이 서버로 옮겨간 뒤로 이 PC 의 Web/.env 는
    /// 아무도 안 읽으므로, 예전처럼 파일에 쓰면 「넣었는데 왜 안 되지」가 된다.
    ///
    /// 관리자 비밀번호를 같이 보낸다 — 교내망 전체에 열린 서버라 아무나 바꾸면 안 된다.
    /// **키는 되돌려받지 않는다.** 들어 있는지와 앞 네 글자만 온다.
    /// </summary>
    void DrawTripoField()
    {
        EditorGUILayout.Space(4);
        using (new EditorGUILayout.HorizontalScope())
        {
            GUILayout.Space(26);
            GUILayout.Label("관리자 비번", GUILayout.Width(66));
            _adminPw = EditorGUILayout.PasswordField(_adminPw);
            GUILayout.Space(10);
        }
        using (new EditorGUILayout.HorizontalScope())
        {
            GUILayout.Space(26);
            GUILayout.Label("3D 키", GUILayout.Width(66));
            _tripoInput = EditorGUILayout.PasswordField(_tripoInput);

            using (new EditorGUI.DisabledScope(
                       _busy || string.IsNullOrWhiteSpace(_adminPw)
                       || string.IsNullOrWhiteSpace(_tripoInput)))
                if (GUILayout.Button("보내기", GUILayout.Width(60)))
                    SendTripo(_tripoInput.Trim());

            // 시연이 끝나면 지운다. 공용 기계에 결제 키를 남길 이유가 없다.
            using (new EditorGUI.DisabledScope(
                       _busy || string.IsNullOrWhiteSpace(_adminPw) || !_web.tripo))
                if (GUILayout.Button("지우기", GUILayout.Width(60)))
                    SendTripo("");
            GUILayout.Space(10);
        }

        Note(_saved ?? (_web.tripo
            ? "키가 서버에 들어 있습니다. 시연이 끝나면 지워 주세요."
            : "키가 없어 인물 3D 모델이 안 뜹니다(대화는 정상)."));
    }

    /// <summary>서버에 키를 넣거나(빈 값이면) 지운다. 즉시 반영된다 — 재시작이 필요 없다.</summary>
    void SendTripo(string key)
    {
        var form = new WWWForm();
        form.AddField("key", key);
        var req = UnityWebRequest.Post(Url + "/admin/tripo", form);
        req.SetRequestHeader("X-Admin-Pw", _adminPw);
        req.timeout = 10;
        _busy = true;

        var op = req.SendWebRequest();
        EditorApplication.CallbackFunction tick = null;
        tick = () =>
        {
            if (!op.isDone) return;
            EditorApplication.update -= tick;
            _busy = false;

            if (req.result != UnityWebRequest.Result.Success)
                _saved = req.responseCode == 401
                    ? "관리자 비밀번호가 다릅니다."
                    : $"보내지 못했습니다 — {req.error}";
            else
            {
                _saved = key.Length > 0 ? "서버에 넣었습니다. 바로 반영됩니다."
                                        : "서버에서 지웠습니다.";
                _tripoInput = "";
                GUI.FocusControl(null);
            }
            req.Dispose();
            Refresh();
            Repaint();
        };
        EditorApplication.update += tick;
    }

    void DrawSession()
    {
        bool ready = _session != null && _session.status == "ready";
        bool loading = _session != null && _session.status != "ready";

        Header("등록 서버 — 인물·참조 자료·3D",
               ready ? Green : loading ? Amber : Red,
               ready ? "준비 완료" : loading ? "준비 중" : "연결 안 됨");
        Sub(SessionUrl);

        EditorGUILayout.Space(4);

        if (_session == null)
        {
            Note(_sessionChecking
                ? "확인 중…"
                : $"꺼져 있거나 교내망이 아닙니다.\n{_sessionError}\n\n"
                  + "인물 등록과 3D 자료를 관리하는 CPU 서비스입니다.\n"
                  + "서버에서 ~/capstone-server/service.sh session start 를 실행해야 합니다.");
            return;
        }

        if (loading)
        {
            Note("등록 API 준비 상태를 확인해 주세요.");
            return;
        }

        // 제일 알고 싶은 것 — 지금 누구로 대화가 되는가.
        if (_session.ready_to_talk)
            Row("등록된 인물", _session.current, Green);
        else
            Row("등록된 인물", "없음 — 웹에서 등록해야 합니다", Amber);

        Row("실행 장치", "CPU", Grey);
        Row("가동", Uptime((int)_session.uptime_sec), Grey);
        Row("등록", $"{_session.registered}개", Grey);
    }

    [Serializable] class DialogueHealth
    {
        public string status, stt, llm, mode;
        public bool llm_ready, tts_ready, reasoning_ready;
    }
    DialogueHealth _dialogueHealth;
    bool _dialogueChecking;
    static string DialogueUrl => FindObjectOfType<DialogueVoiceClient>()?.DialogueServerUrl
        ?? "http://220.69.208.201:8002";

    void CheckDialogue()
    {
        if (_dialogueChecking) return;
        _dialogueChecking = true;
        var req = UnityWebRequest.Get(DialogueUrl + "/health");
        req.timeout = 8;
        var op = req.SendWebRequest();
        EditorApplication.CallbackFunction tick = null;
        tick = () =>
        {
            if (!op.isDone) return;
            EditorApplication.update -= tick;
            _dialogueHealth = null;
            if (req.result == UnityWebRequest.Result.Success)
                try { _dialogueHealth = JsonUtility.FromJson<DialogueHealth>(req.downloadHandler.text); }
                catch (Exception) { }
            req.Dispose();
            _dialogueChecking = false;
            Repaint();
        };
        EditorApplication.update += tick;
    }

    void DrawDialogue()
    {
        bool ready = _dialogueHealth != null && _dialogueHealth.status == "ready";
        Header("음성 대화 — STT · LLM · TTS", ready ? Green : Amber, ready ? "준비 완료" : "연결 확인 필요");
        Sub(DialogueUrl);
        if (_dialogueHealth == null) return;
        Row("STT", _dialogueHealth.stt, Grey);
        Row("답변", _dialogueHealth.llm_ready ? "Gemma · 준비됨" : "연결 안 됨", Grey);
        Row("추론", _dialogueHealth.reasoning_ready ? "준비됨" : "연결 안 됨", Grey);
        Row("TTS", _dialogueHealth.tts_ready ? "Qwen3-TTS · 준비됨" : "연결 안 됨", Grey);
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
