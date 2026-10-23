// StartSelection.cs
// 시작화면(StartScene)에서 고른 것을 Scene_2 로 넘긴다.
//
// 씬이 바뀌면 시작화면의 오브젝트는 사라지므로 정적 값으로 들고 간다.
// Scene_2 를 Editor 에서 바로 Play 하면 아무것도 고르지 않은 상태라 기존 동작 그대로다.

public static class StartSelection
{
    public enum Background
    {
        /// <summary>Scene_2 에 저장된 배경을 그대로 쓴다.</summary>
        Keep,
        Moonlit,
        Pastel,
        Garden,
    }

    public static Background background = Background.Keep;

    /// <summary>시작화면에서 시작을 눌렀다. Scene_2 가 준비되는 대로 체험을 연다.</summary>
    public static bool startRequested;
}
