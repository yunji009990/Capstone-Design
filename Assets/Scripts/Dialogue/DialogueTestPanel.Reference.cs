using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using UnityEngine;
using UnityEngine.Networking;
using UnityEngine.UI;

[Serializable]
public class DialogueReferenceInfo
{
    public string reference_id, text, source, notice;
    public int sample_rate;
    public float duration_sec, original_sec, start_sec;
    public bool cropped;
}

public partial class DialogueTestPanel
{
    GameObject _referenceModal;
    InputField _referencePath, _referenceText;
    Text _referenceStatus, _referenceSummary;
    Button _referenceSettings, _referenceCurrent, _referenceBrowse, _referenceLoad, _referencePreview, _referenceApply;
    AudioSource _previewSource;
    AudioClip _previewClip;
    DialogueReferenceInfo _selectedReference;
    string _referenceUrl = "", _referenceToken = "";
    bool _referenceBusy;
    public bool CheckingReference => _referenceBusy;
    public string SelectedReferenceId => Voice.testReferenceId;
#if UNITY_EDITOR
    public bool ReferencePreviewPlaying => _previewSource != null && _previewSource.isPlaying;
    public void PreviewReferenceForCheck()
    {
        _referenceModal.SetActive(true);
        StartCoroutine(PreviewReference());
    }
    public void CloseReferenceForCheck()
    {
        StopReferencePreview();
        _referenceModal.SetActive(false);
    }
#endif
    [Serializable] class ReferenceError { public string detail; }

    void BuildReferencePanel()
    {
        var parent = _root;
        _referenceModal = Box(_root, "Reference voice settings", 0, 0, 1440, 900,
            new Color(.01f, .02f, .04f, .92f)).gameObject;
        _root = _referenceModal.transform;
        Box(_root, "Reference card", 270, 90, 900, 705, new Color(.065f, .09f, .145f));
        Label(_root, "참조 목소리 · 억양", 300, 114, 800, 42, 29);
        Label(_root, "한 사람이 선명하게 말하는 WAV를 선택하세요. 참조 음성의 음색과 말투를 답변에 반영합니다.\n3–12초 사용 · 6–12초 권장 · 16-bit PCM WAV", 300, 170, 835, 50, 18, true);
        _referenceCurrent = ButtonAt("서버 등록 음성 불러오기", 300, 235, 410, 42,
            () => StartCoroutine(LoadReferenceFile(null)));
        _referenceBrowse = ButtonAt("WAV 파일 선택", 725, 235, 410, 42, BrowseReference);
        _referencePath = Field("Reference WAV path", "", 300, 292, 700, 38, 2048);
        _referenceLoad = ButtonAt("경로 불러오기", 1010, 292, 125, 38,
            () => StartCoroutine(LoadReferenceFile(_referencePath.text.Trim().Trim('"'))));
        _referenceStatus = Label(_root, "현재 선택: 서버에 등록된 음성 (대화 시작 때 자동으로 불러옵니다)",
            300, 347, 835, 64, 18, true);
        _referencePreview = ButtonAt("사용할 구간 미리 듣기", 300, 425, 320, 42,
            () => StartCoroutine(PreviewReference()));
        ButtonAt("재생 중지", 634, 425, 190, 42, StopReferencePreview);
        Label(_root, "참조 음성의 전사 · 미리 들은 내용과 같도록 수정할 수 있습니다", 300, 489, 835, 28, 19);
        _referenceText = Field("Reference transcript", "", 300, 526, 835, 130, 2000, true);
        _referenceText.onValueChanged.AddListener(value => Voice.testReferenceText = value);
        _referenceApply = ButtonAt("적용하고 닫기", 300, 680, 835, 44, () =>
        {
            if (_selectedReference != null && string.IsNullOrWhiteSpace(_referenceText.text))
            { _referenceStatus.text = "참조 음성에 담긴 문장을 입력해 주세요."; return; }
            StopReferencePreview();
            _referenceModal.SetActive(false);
        }, true);
        Label(_root, "임시 업로드는 서버 메모리에만 보관됩니다. 서버 재시작 또는 30분 미사용 후 다시 불러오세요.",
            300, 744, 835, 28, 16, true);
        var preview = new GameObject("Reference preview", typeof(AudioSource));
        preview.transform.SetParent(transform, false);
        _previewSource = preview.GetComponent<AudioSource>();
        _previewSource.playOnAwake = false;
        _root = parent;
        _referenceModal.SetActive(false);
    }

    void UpdateReferencePanel(bool editable)
    {
        editable = editable && Voice.TtsEnabled;
        _referenceSettings.interactable = editable;
        _referenceCurrent.interactable = _referenceBrowse.interactable = _referenceLoad.interactable = editable;
        _referencePath.interactable = editable;
        _referenceText.interactable = editable && _selectedReference != null;
        _referenceApply.interactable = !_referenceBusy;
        _referencePreview.interactable = editable && _selectedReference != null;
        var reference = Voice.ExperienceActive ? Voice.ActiveReference : _selectedReference;
        _referenceSummary.text = !Voice.TtsEnabled ? "텍스트 답변 모드 · 음성 출력과 참조 목소리 사용을 껐습니다." :
            reference == null ? "참조 목소리 · 서버 등록 음성 자동 선택" :
            $"참조 목소리 · {reference.source} · {reference.duration_sec:F1}초 · 목소리·말투 복제";
    }

    void BrowseReference()
    {
#if UNITY_EDITOR
        string path = UnityEditor.EditorUtility.OpenFilePanel("참조 목소리 WAV 선택", "", "wav");
        if (!string.IsNullOrEmpty(path))
        {
            _referencePath.text = path;
            StartCoroutine(LoadReferenceFile(path));
        }
#else
        _referenceStatus.text = "WAV 파일의 전체 경로를 입력하고 ‘경로 불러오기’를 눌러 주세요.";
        _referencePath.ActivateInputField();
#endif
    }

    void ValidateReferenceServer()
    {
        if (_selectedReference == null ||
            (_referenceUrl == Voice.DialogueServerUrl && _referenceToken == Voice.token)) return;
        _selectedReference = null;
        Voice.testReferenceId = Voice.testReferenceText = "";
        _referenceText.text = "";
        _referenceStatus.text = "서버 또는 토큰이 바뀌었습니다. 참조 음성을 다시 불러오세요.";
    }

    public IEnumerator LoadReferenceFile(string path)
    {
        if (_referenceBusy || Voice.ExperienceActive || _checking) yield break;
        string url = _url.text.Trim().TrimEnd('/');
        if (!Uri.TryCreate(url, UriKind.Absolute, out var uri) || (uri.Scheme != "http" && uri.Scheme != "https"))
        { _referenceStatus.text = "대화 서버 주소를 먼저 확인해 주세요."; yield break; }
        byte[] wav = null;
        if (path != null)
        {
            try
            {
                var file = new FileInfo(path);
                if (!file.Exists || file.Length > 30 * 1024 * 1024)
                    throw new IOException("존재하는 30 MB 이하의 WAV 파일을 선택하세요.");
                wav = File.ReadAllBytes(file.FullName);
            }
            catch (Exception e) { _referenceStatus.text = "파일을 읽지 못했습니다: " + e.Message; yield break; }
        }
        StopReferencePreview();
        _referenceBusy = true;
        _referenceStatus.text = "참조 음성을 불러오고 SenseVoice로 전사하고 있습니다…";
        var form = new List<IMultipartFormSection>();
        if (wav != null) form.Add(new MultipartFormFileSection("voice", wav, Path.GetFileName(path), "audio/wav"));
        else form.Add(new MultipartFormDataSection("current", "1"));
        using (var request = UnityWebRequest.Post(url + "/references", form))
        {
            request.SetRequestHeader("X-Token", _token.text);
            request.timeout = 60;
            try
            {
                yield return request.SendWebRequest();
                if (request.result != UnityWebRequest.Result.Success)
                { _referenceStatus.text = ReferenceFailure(request); yield break; }
                var info = JsonUtility.FromJson<DialogueReferenceInfo>(request.downloadHandler.text);
                if (info == null || string.IsNullOrEmpty(info.reference_id))
                { _referenceStatus.text = "서버의 참조 음성 응답이 올바르지 않습니다."; yield break; }
                // Release only after the new upload succeeds. A bad replacement
                // never loses the last working recording.
                if (_selectedReference != null)
                    yield return ReleaseReference(_referenceUrl, _referenceToken, _selectedReference.reference_id);
                _selectedReference = info;
                _referenceUrl = url;
                _referenceToken = _token.text;
                Voice.testReferenceId = info.reference_id;
                _referenceText.text = Voice.testReferenceText = info.text;
                _referenceStatus.text = $"{info.source} · {info.start_sec:F1}–{info.start_sec + info.duration_sec:F1}초 구간 / 원본 {info.original_sec:F1}초\n" +
                    (info.cropped ? info.notice : "미리 듣고 아래 전사를 확인한 뒤 테스트를 시작하세요.");
            }
            finally { _referenceBusy = false; }
        }
    }

    IEnumerator ReleaseReference(string url, string token, string id)
    {
        using (var request = UnityWebRequest.Delete(url + "/references/" + Uri.EscapeDataString(id)))
        {
            request.SetRequestHeader("X-Token", token);
            request.timeout = 5;
            yield return request.SendWebRequest();
        }
    }

    IEnumerator PreviewReference()
    {
        if (_selectedReference == null || _referenceBusy || Voice.ExperienceActive) yield break;
        StopReferencePreview();
        _referenceBusy = true;
        using (var request = UnityWebRequestMultimedia.GetAudioClip(_referenceUrl + "/references/" +
            Uri.EscapeDataString(_selectedReference.reference_id) + "/audio.wav", AudioType.WAV))
        {
            request.SetRequestHeader("X-Token", _referenceToken);
            request.timeout = 30;
            try
            {
                yield return request.SendWebRequest();
                if (request.result != UnityWebRequest.Result.Success)
                { _referenceStatus.text = "미리 듣기에 실패했습니다. 참조 음성을 다시 불러오세요."; yield break; }
                if (_previewClip != null) Destroy(_previewClip);
                _previewClip = DownloadHandlerAudioClip.GetContent(request);
                _previewSource.clip = _previewClip;
                _previewSource.Play();
            }
            finally { _referenceBusy = false; }
        }
    }

    void StopReferencePreview() { if (_previewSource != null) _previewSource.Stop(); }

    static string ReferenceFailure(UnityWebRequest request)
    {
        try
        {
            var error = JsonUtility.FromJson<ReferenceError>(request.downloadHandler.text);
            if (!string.IsNullOrEmpty(error?.detail)) return error.detail;
        }
        catch (Exception) { }
        return "참조 음성을 불러오지 못했습니다: " + request.error;
    }
}
