using UnityEngine;

/*
 * RayReactor는 사용자가 발생하는 이벤트(시선, 손으로 잡기)를 받는 클래스입니다.
 * RayReactor receives event(eye gazing, hand holding) happened from user.
 */
[RequireComponent(typeof(Collider))]
public class RayReactor : MonoBehaviour
{
    [Tooltip("Object name used to record when user is focusing on object.")]
    public string objectName = "";

    [SerializeField]
    [Tooltip("Show effect when user is focusing on object.")]
    public bool showEffect = false;

    [SerializeField]
    [Tooltip("Effect color when user is focusing on object.")]
    private Color effectColor = Color.white;

    // Reacted는 현재 유저가 오브젝트에 focusing하고 있음을 나타냅니다.
    // Reacted means user is focusing.
    [HideInInspector]
    public bool isEyeReacted { get; set; }


    // 사용자가 오브젝트를 바라볼경우 실행됨.
    // Execute when user is gazing an object.
    private void EyeReact()
    {
        if (showEffect)
            ShowEffect();
    }

    // 사용자가 오브젝트를 바라보지 않을경우 실행됨.
    // Execute when user is not gazing an object.
    private void EyeHalt()
    {
        if (showEffect)
            HideEffect();
    }

    private void ShowEffect()
    {
        GetComponent<MeshRenderer>().material.EnableKeyword("_EMISSION");
        GetComponent<MeshRenderer>().material.SetColor("_EmissionColor", effectColor);
    }

    private void HideEffect()
    {
        GetComponent<MeshRenderer>().material.DisableKeyword("_EMISSION");
    }

    private void FixedUpdate()
    {
        // User eye focusing check
        if (isEyeReacted)
            EyeReact();
        else
            EyeHalt();
    }
}
