// ConversationLog.cs
// 오간 말을 쌓아서 보여준다.
//
// 마지막 한 마디만 보여주면 옆에서 지켜보는 사람이 흐름을 못 따라간다. 체험이 끝난
// 뒤 무슨 이야기가 오갔는지 확인할 일도 있다. 길어지므로 스크롤로 되짚어 본다.

using System.Collections;
using System.Text;
using TMPro;
using UnityEngine;
using UnityEngine.UI;

[DisallowMultipleComponent]
public class ConversationLog : MonoBehaviour
{
    [Tooltip("비워두면 씬에서 찾는다.")]
    public RaonVoiceClient voice;

    [Tooltip("글이 쌓일 자리.")]
    public TMP_Text body;

    [Tooltip("스크롤 영역. 새 말이 오면 아래로 따라간다.")]
    public ScrollRect scroll;

    [Tooltip("체험자를 가리키는 말.")]
    public string userName = "체험자";

    [Tooltip("이만큼 넘어가면 오래된 것부터 지운다. 무한정 쌓으면 느려진다.")]
    public int maxTurns = 60;

    readonly StringBuilder _text = new StringBuilder();
    int _turns;
    bool _stick = true;      // 사용자가 위로 올려 읽는 중이면 따라가지 않는다

    void Awake()
    {
        if (voice == null) voice = FindObjectOfType<RaonVoiceClient>();
        if (scroll == null) scroll = GetComponentInChildren<ScrollRect>();
        if (body == null && scroll != null && scroll.content != null)
            body = scroll.content.GetComponentInChildren<TMP_Text>();
    }

    void OnEnable()
    {
        if (voice != null) voice.OnAnswer += Append;
        if (scroll != null) scroll.onValueChanged.AddListener(OnScrolled);
        Redraw();
    }

    void OnDisable()
    {
        if (voice != null) voice.OnAnswer -= Append;
        if (scroll != null) scroll.onValueChanged.RemoveListener(OnScrolled);
    }

    void OnScrolled(Vector2 pos)
    {
        // 바닥 근처면 계속 따라가고, 위로 올려 읽는 중이면 가만히 둔다.
        _stick = pos.y <= 0.05f;
    }

    void Append(string heard, string answer)
    {
        if (!string.IsNullOrEmpty(heard))
            _text.AppendLine($"<color=#8A93A3>{userName}</color>  {heard}");
        if (!string.IsNullOrEmpty(answer))
            _text.AppendLine($"<color=#C58FA0>인물</color>  {answer}");
        _text.AppendLine();

        if (++_turns > maxTurns) Trim();
        Redraw();
        if (_stick) StartCoroutine(ToBottom());
    }

    /// <summary>오래된 절반을 잘라낸다. 한 줄씩 지우면 자주 다시 그리게 된다.</summary>
    void Trim()
    {
        string[] lines = _text.ToString().Split('\n');
        int cut = lines.Length / 2;
        _text.Clear();
        for (int i = cut; i < lines.Length; i++) _text.Append(lines[i]).Append('\n');
        _turns = maxTurns / 2;
    }

    void Redraw()
    {
        if (body == null) return;
        body.text = _turns == 0 ? "<color=#5A6172>아직 오간 말이 없습니다.</color>"
                                : _text.ToString();
    }

    /// <summary>글이 늘어난 뒤에야 높이가 정해지므로 한 프레임 기다린다.</summary>
    IEnumerator ToBottom()
    {
        yield return null;
        Canvas.ForceUpdateCanvases();
        if (scroll != null) scroll.verticalNormalizedPosition = 0f;
    }

    /// <summary>새 체험을 시작할 때 비운다.</summary>
    public void Clear()
    {
        _text.Clear();
        _turns = 0;
        _stick = true;
        Redraw();
    }
}
