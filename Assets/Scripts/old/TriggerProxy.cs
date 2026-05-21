using UnityEngine;

public class TriggerProxy : MonoBehaviour
{
    private BallCountChecker checker;

    // BallCountChecker에서 등록
    public void SetEventCheck(BallCountChecker checker)
    {
        this.checker = checker;
    }

    private void OnTriggerEnter(Collider other)
    {
        if (checker == null) return;

        checker.OnBallEnter(this, other.gameObject);
    }

    private void OnTriggerExit(Collider other)
    {
        if (checker == null) return;

        checker.OnBallExit(this, other.gameObject);
    }
}
