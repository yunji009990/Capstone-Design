// MicDropdownRefresh.cs
// 목록을 펼치는 순간 마이크를 다시 찾는다.
//
// 목록을 시작할 때 한 번만 채우면, 그 뒤에 꽂은 마이크나 헤드셋이 안 보인다.
// 체험 직전에 헤드셋을 연결하는 일이 잦아서 매번 재시작할 수는 없다.

using UnityEngine;
using UnityEngine.EventSystems;

[RequireComponent(typeof(TMPro.TMP_Dropdown))]
public class MicDropdownRefresh : MonoBehaviour, IPointerDownHandler
{
    [Tooltip("비워두면 씬에서 찾는다.")]
    public RaonVoiceUI ui;

    void Awake()
    {
        if (ui == null) ui = FindObjectOfType<RaonVoiceUI>();
    }

    public void OnPointerDown(PointerEventData _)
    {
        if (ui != null) ui.PopulateMicDropdown();
    }
}
