using UnityEditor;

/// <summary>재생 비교 모드를 고르는 메뉴다. 선택값만 바꾸고 서버 호출·씬 저장·등록 세션 변경은 하지 않는다.
/// 선택은 다음 Play 부터 적용되므로 Play 중과 컴파일 중에는 고를 수 없다.</summary>
public static class DialoguePlaybackComparisonMenu
{
    [MenuItem(DialoguePlaybackComparison.WholeResponseMenu, false, 100)]
    public static void SelectWholeResponse() => Apply(true);

    [MenuItem(DialoguePlaybackComparison.WholeResponseMenu, true)]
    static bool ValidateWholeResponse()
    {
        Menu.SetChecked(DialoguePlaybackComparison.WholeResponseMenu,
                        DialoguePlaybackComparison.WholeResponseEnabled);
        return Selectable;
    }

    [MenuItem(DialoguePlaybackComparison.StreamingMenu, false, 101)]
    public static void SelectStreaming() => Apply(false);

    [MenuItem(DialoguePlaybackComparison.StreamingMenu, true)]
    static bool ValidateStreaming()
    {
        Menu.SetChecked(DialoguePlaybackComparison.StreamingMenu,
                        !DialoguePlaybackComparison.WholeResponseEnabled);
        return Selectable;
    }

    static bool Selectable =>
        !EditorApplication.isPlayingOrWillChangePlaymode && !EditorApplication.isCompiling;

    static void Apply(bool wholeResponse)
    {
        // 메뉴 검증과 같은 조건을 코드 경로에도 건다. 코드로 직접 불러도 Play 중 전환은 막는다.
        if (!Selectable)
        {
            UnityEngine.Debug.LogWarning("[Dialogue] 재생 모드는 Play 중·컴파일 중에 바꿀 수 없습니다.");
            return;
        }
        DialoguePlaybackComparison.WholeResponseEnabled = wholeResponse;
        UnityEngine.Debug.Log(wholeResponse
            ? "[Dialogue] 재생 모드 선택: 응답 전체 수신 후 AudioClip 재생 (다음 Play 부터)"
            : "[Dialogue] 재생 모드 선택: 수신 즉시 스트리밍 (다음 Play 부터)");
    }
}
