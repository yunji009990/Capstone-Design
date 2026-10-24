using UnityEngine;

/// <summary>검증된 발화가 이어질 때 작은 끄덕임 한 번을 만든다. 뼈·음성·감정 판정은 소유하지 않는다.</summary>
public sealed class PersonaListeningNod
{
    float heardFor, quietFor, cooldown, elapsed = -1f;
    float confirmedSeconds;
    public float Weight { get; private set; }
    public bool StartedThisFrame { get; private set; }
    public int Count { get; private set; }

    // 서버의 speech.started/stopped가 같은 프레임에 도착해도 확정된 발화를 놓치지 않는다.
    public void ConfirmSpeech(float duration) => confirmedSeconds = Mathf.Max(0f, duration);

    public float Tick(float dt, bool listening, bool blocked, float delay,
                      float minGap, float maxGap, float duration)
    {
        dt = Mathf.Max(0f, dt);
        StartedThisFrame = false;
        cooldown = Mathf.Max(0f, cooldown - dt);
        if (blocked)
        {
            heardFor = quietFor = 0f;
            confirmedSeconds = 0f;
            elapsed = -1f;
            // 말하기·다른 제스처로 넘어가도 고개를 한 프레임에 되돌리지 않는다.
            Weight = Mathf.MoveTowards(Weight, 0f, dt * 5f);
            return Weight;
        }

        if (listening) { heardFor += dt; quietFor = 0f; }
        else
        {
            quietFor += dt;
            if (quietFor >= .4f) heardFor = 0f;
        }

        duration = Mathf.Max(.25f, duration);
        if (elapsed >= 0f)
        {
            elapsed += dt;
            if (elapsed >= duration) { elapsed = -1f; Weight = 0f; }
            else
            {
                float wave = Mathf.Sin(Mathf.PI * elapsed / duration);
                Weight = wave * wave; // 양 끝의 속도가 0인 내려갔다 돌아오기
            }
        }
        else if (Weight > 0f) Weight = Mathf.MoveTowards(Weight, 0f, dt * 5f);

        bool confirmed = confirmedSeconds >= Mathf.Max(.1f, delay);
        if ((listening || confirmed) && elapsed < 0f && Weight <= 0f &&
            Mathf.Max(heardFor, confirmedSeconds) >= Mathf.Max(.1f, delay) && cooldown <= 0f)
        {
            elapsed = 0f;
            heardFor = 0f;
            minGap = Mathf.Max(duration, minGap);
            cooldown = Random.Range(minGap, Mathf.Max(minGap, maxGap));
            StartedThisFrame = true;
            Count++;
        }
        // 한 번 받은 신호를 다음 대화·제스처가 끝날 때까지 보관해 늦게 반응하지 않는다.
        confirmedSeconds = 0f;
        return Weight;
    }

    public void Reset()
    {
        heardFor = quietFor = cooldown = Weight = 0f;
        confirmedSeconds = 0f;
        elapsed = -1f;
        Count = 0;
        StartedThisFrame = false;
    }
}
