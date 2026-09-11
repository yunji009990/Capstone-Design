# A.X K2 Raon-Speech 음성 대화 시스템 — Unity 클라이언트 구현 명세

> 이 문서는 Unity 측 구현을 담당할 작업자(사람 또는 AI 에이전트)를 위한 인수인계 문서입니다.
> **서버는 이미 구축 완료되어 정상 동작 중입니다. 서버 코드를 수정할 필요가 없습니다.**

작성일: 2026-07-31

> **⚠ 이 문서는 2026-07-31 스냅샷이다.** 서버 팀이 준 원본 명세를 그대로 남겨 둔 것이라
> 그때의 구조와 수치가 들어 있다. **그 뒤로 크게 달라진 것이 넷이다.**
>
> | | 이 문서 (07-31) | 지금 (08-30) |
> |---|---|---|
> | 기본 경로 | `POST /talk` (전체 버퍼링) | **`POST /talk_stream`** (프레임 스트리밍). `/talk` 은 폴백 |
> | 인물 설정 | 서버의 `persona.md`·`knowledge.md`·`voice.wav` 편집 + `POST /reload` | **웹에서 `POST /session/start`** 로 세션마다 등록. 그 셋도 `/reload` 도 **없다** |
> | 첫 소리까지 | 약 4.5초 | **1.5~2.2초** (씬의 VAD·자막까지 더한 체감은 2.3~3.0초) |
> | 합성 검증 | 켜져 있음 | **꺼져 있다** (`RAON_VERIFY=0`) |
>
> 현재 구조·엔드포인트·설정값은 [`음성대화_작업현황.md`](음성대화_작업현황.md) 를 볼 것.
> 아래는 Unity 클라이언트를 처음 만들 때의 요구사항과 참조 구현으로 남긴다.

---

## 1. 시스템 개요

플레이어가 마이크로 말을 걸면, 서버의 음성 언어 모델이 그 말을 이해하고, 미리 지정된 캐릭터 설정과 사전지식을 바탕으로 답변을 만든 뒤, 복제된 캐릭터 목소리로 합성해 돌려줍니다. Unity는 그 음성을 받아 재생합니다.

```
[Unity 클라이언트]
   │ ① 마이크 녹음 → WAV 바이트
   │ ② HTTP POST multipart/form-data
   ▼
[FastAPI 서버 : 220.69.208.201:8000]
   │ ③ 음성 인식 (0.6초)
   │ ④ 시스템 프롬프트 + 사전지식 + 사용자 음성 → 답변 텍스트 (0.6초)
   │ ⑤ 답변을 캐릭터 목소리로 합성 + 검증 (3.4초)
   ▼
   │ ⑥ audio/wav 바이트 + 헤더(인식 결과, 답변 텍스트)
[Unity] → AudioClip 변환 → AudioSource 재생
```

서버에는 21B 규모의 멀티모달 음성 모델이 상주하며, 음성 인식과 답변 생성과 음성 합성을 한 모델이 모두 처리합니다.

### 서버 환경 (참고용, 수정 대상 아님)

| 항목 | 값 |
|---|---|
| 주소 | `http://220.69.208.201:8000` (교내망 전용, 외부 접근 불가) |
| 모델 | KRAFTON/A.X-K2-Raon-Speech-21B-A3B |
| GPU | RTX PRO 6000 Blackwell 96GB (VRAM 약 40GB 점유) |
| 서버 경로 | `/home/crc_unity/server/` |
| 기동 | `./start.sh` / 종료 `./stop.sh` / 상태 `./status.sh` |

**서버는 수동으로 켜고 끕니다.** 작업 전 서버가 켜져 있는지 `/health`로 확인해야 하며, 꺼져 있으면 연결 거부가 발생합니다.

---

## 2. API 명세

모든 엔드포인트는 `http://220.69.208.201:8000` 기준입니다.
서버에 `RAON_TOKEN`이 설정된 경우에만 `X-Token` 헤더가 필요합니다. **지금은 설정돼 있으므로 필요합니다** — `start.sh` 의 값과 같아야 합니다(불일치 시 `401`).

### GET /health

서버 상태 확인. 앱 시작 시 호출해 서버 가동 여부를 판별하는 용도입니다.

```json
{"status":"ready","vram_gb":42.9,"uptime_sec":85200,"sessions":1,"registered":1,"current":"test","ready_to_talk":true,"cont":true}
```

`status`가 `ready`가 아니면 아직 모델 로딩 중입니다. 서버 기동 후 준비까지 약 20초 걸립니다.

### POST /talk  ← 당시의 핵심 엔드포인트 (지금은 폴백)

음성을 보내고 답변 음성을 받습니다.

**요청**: `multipart/form-data`

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `file` | 파일 | 필수 | WAV 바이트. 16bit PCM 권장, 모노, 16000Hz 권장 |
| `session` | 문자열 | 선택 | 대화 맥락 구분 키. 기본값 `default` |

**응답**: `200 OK`, 본문은 `audio/wav` 바이너리

| 응답 헤더 | 설명 |
|---|---|
| `X-Heard` | 서버가 인식한 사용자 발화. **URL 인코딩됨** — 반드시 디코딩할 것 |
| `X-Answer` | 캐릭터가 생성한 답변 텍스트. **URL 인코딩됨** |
| `X-Elapsed` | 서버 처리 소요 시간(초) |

출력 오디오 형식: **24000Hz, 모노, 16bit PCM WAV**

**오류**: `503` 모델 준비 안 됨 / `401` 토큰 불일치

### POST /tts

텍스트를 캐릭터 목소리로 합성만 합니다. 미리 정해진 대사를 읽힐 때 사용합니다.

요청: form 필드 `text` (문자열) → 응답: `audio/wav`

### POST /stt

음성을 텍스트로만 변환합니다.

요청: multipart 필드 `file` → 응답: `{"text": "인식된 문장"}`

### POST /reset

특정 세션의 대화 맥락을 지웁니다. 요청: form 필드 `session` → 응답: `{"ok": true}`

### ~~POST /reload~~ — 없어졌습니다

`persona.md`·`knowledge.md` 를 다시 읽던 엔드포인트입니다. **지금은 서버에 없습니다**
(실 서버 OpenAPI 로 확인). 인물은 `POST /session/start` 로 세션마다 등록합니다.

### 이 문서에 없는 엔드포인트

그 뒤에 생긴 것들입니다. `POST /talk_stream`(기본 경로) · `POST /session/start` ·
`POST /session/{sid}/model` · `GET /session/current` · `GET /session/{sid}/model.glb` ·
`POST /session/end` · `POST /chat`(프롬프트 시험용) · `POST /mode`(억양 복제를 재시작
없이 켜고 끄기). 쓰임은 [`음성대화_작업현황.md`](음성대화_작업현황.md) §2.

---

## 3. 성능 특성 — 반드시 숙지할 것

> **아래 표는 2026-07-31 값이고 지금은 아닙니다.** 프레임 단위 스트리밍(`/talk_stream`)과
> 검증 끄기·`torch.compile` 로 **첫 소리까지 약 4.5초 → 1.5~2.2초** 가 됐습니다. 씬의
> `silenceSec`(0.8)과 자막(+0.5초)까지 더한 실제 체감은 **2.3~3.0초** 입니다. 따라서
> 아래의 「4~5초 대기를 전제로 연출하라」는 지침은 더 이상 맞지 않습니다.
>
> **총 계산량이 줄어든 것이 아니라 기다리는 지점을 옮긴 것입니다** — 합성 시간은
> `1.0초 고정비 + 오디오길이/1.8` 이라 예전에는 답이 길수록 선형으로 기다렸는데,
> 프레임이 나오는 대로 보내면서 첫 소리가 답변 길이와 무관해졌습니다.
> 수치와 과정은 [`음성대화_작업현황.md`](음성대화_작업현황.md) §3.

실측 기준입니다(2026-07-31 시점). UX 설계에 직접 영향을 줍니다.

| 구간 | 소요 | 비고 |
|---|---|---|
| 음성 인식 | 0.6초 | 대화 맥락 저장용 |
| 답변 생성 | 0.6초 | 텍스트 |
| 음성 합성 | 2.9초 | **전체의 63%** |
| 합성 검증 | 0.5초 | 생성된 음성을 재인식해 품질 검사 |
| **총 왕복** | **약 4.6초** | 네트워크 제외 |

### 결정적으로 중요한 제약

**음성 생성 속도는 실시간의 1.26배에 불과합니다.** 초당 15.7프레임을 생성하는데 재생에 필요한 것이 초당 12.5프레임이기 때문입니다. 즉 **답변이 길어지면 그만큼 대기 시간이 선형으로 늘어납니다.** 8초짜리 답변은 합성에만 6.4초가 걸립니다.

따라서 UX 설계 시:
- 답변은 두 문장 이내로 유지됩니다(서버의 `persona.md`에 지시됨, `max_new_tokens=200`)
- 4~5초의 대기가 발생하는 것을 전제로 연출을 설계해야 합니다
- 대기 중 캐릭터가 고민하는 모션이나 "생각 중" 표시가 사실상 필수입니다

### 기타 제약

- **서버는 요청을 직렬 처리합니다.** 동시에 여러 요청이 오면 순서대로 처리되어 대기가 누적됩니다. 이전 요청이 끝나기 전에 새 요청을 보내지 않도록 클라이언트에서 막아야 합니다.
- 출력 음성 최대 길이는 약 41초입니다.
- 입력 음성이 20초를 넘으면 잡음이 생길 수 있습니다. 녹음 길이를 15초로 제한하는 것을 권장합니다.
- 20회 중 1회 미만의 확률로 합성이 실패할 수 있습니다. **검증은 지금 꺼져 있어서(`RAON_VERIFY=0`) 자동 재생성이 없습니다** — 약 0.5초를 얻는 대신 그 확률을 받아들인 것입니다.

---

## 4. Unity 구현 요구사항

### 환경

- Unity 2021.3 LTS 이상
- **Player Settings → Other Settings → `Allow downloads over HTTP` 를 `Always allowed` 로 변경 필수.** 기본값이 `Not allowed`라 HTTP 요청이 차단됩니다. 이 설정을 놓치면 원인 불명의 연결 실패가 발생합니다.
- 마이크 사용 권한 필요

### 직접 구현이 필요한 부분

Unity의 `UnityWebRequestMultimedia.GetAudioClip`은 URL을 GET할 때만 쓸 수 있고, POST 응답의 바이트 배열을 AudioClip으로 만드는 기능은 없습니다. 따라서 다음 두 가지를 직접 구현해야 하며, **아래 5장의 참조 구현에 이미 포함되어 있습니다.**

1. `AudioClip` → 16bit PCM WAV 바이트 인코딩
2. WAV 바이트 → `AudioClip` 디코딩 (RIFF 청크 파싱)

### 구현 과제 목록

**필수**

1. 참조 구현 `RaonVoiceClient.cs`를 프로젝트에 통합하고 실제 동작 검증
2. 앱 시작 시 `/health` 호출로 서버 가동 여부 확인, 꺼져 있으면 사용자에게 안내 표시
3. 요청 진행 중 중복 요청 차단 (`isWaiting` 플래그 활용)
4. 자막 UI — `OnAnswer(heard, answer)` 이벤트를 TextMeshPro 등에 연결
5. 상태 표시 UI — 녹음 중 / 전송 중 / 재생 중 구분
6. 오류 처리 — `OnError` 이벤트로 서버 다운, 타임아웃, 마이크 없음 상황 안내

**권장**

7. 대기 연출 — 4~5초 공백을 메울 캐릭터 애니메이션 또는 로딩 표시
8. 립싱크 — 재생 중 `AudioSource`의 진폭을 샘플링해 캐릭터 입 모양에 반영
9. 세션 관리 — 캐릭터별 또는 플레이어별로 다른 `sessionId` 부여, 대화 종료 시 `/reset` 호출

**선택**

10. 음성 활동 감지(VAD) — 말이 끝나면 자동으로 전송 (현재는 키를 떼야 전송)
11. 응답 캐싱 — 자주 나오는 질문의 답변 음성을 미리 만들어두고 즉시 재생
12. 서버 원격 제어 — 앱에서 서버를 켜고 끄는 기능(SSH 필요, 난이도 높음)

---

## 5. 참조 구현

아래 스크립트는 이미 작성되어 동작 검증된 기본 구현입니다. 그대로 사용하거나 확장하시면 됩니다.

`Assets/Scripts/Raon/RaonVoiceClient.cs`

```csharp
using System;
using System.Collections;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.Networking;

/// <summary>
/// A.X K2 Raon-Speech 음성 서버 클라이언트.
/// 스페이스바를 누르는 동안 녹음하고, 떼면 서버로 보내 답변 음성을 받아 재생합니다.
/// </summary>
[RequireComponent(typeof(AudioSource))]
public class RaonVoiceClient : MonoBehaviour
{
    [Header("서버 설정")]
    public string serverUrl = "http://220.69.208.201:8000";
    public string token = "";
    public string sessionId = "player1";

    [Header("녹음 설정")]
    public int sampleRate = 16000;
    public int maxRecordSeconds = 15;
    public KeyCode pushToTalkKey = KeyCode.Space;

    [Header("상태 (읽기 전용)")]
    public string lastHeard = "";
    public string lastAnswer = "";
    public bool isRecording = false;
    public bool isWaiting = false;

    public event Action<string, string> OnAnswer;   // (인식된 발화, 캐릭터 답변)
    public event Action<string> OnError;

    AudioSource _audio;
    AudioClip _recClip;
    string _micDevice;

    void Awake()
    {
        _audio = GetComponent<AudioSource>();
        if (Microphone.devices.Length == 0)
        {
            Debug.LogError("[Raon] 마이크를 찾을 수 없습니다.");
            return;
        }
        _micDevice = Microphone.devices[0];
        Debug.Log($"[Raon] 마이크: {_micDevice}");
    }

    void Update()
    {
        if (Input.GetKeyDown(pushToTalkKey) && !isRecording && !isWaiting) StartRecording();
        else if (Input.GetKeyUp(pushToTalkKey) && isRecording) StopAndSend();
    }

    public void StartRecording()
    {
        if (string.IsNullOrEmpty(_micDevice)) return;
        _recClip = Microphone.Start(_micDevice, false, maxRecordSeconds, sampleRate);
        isRecording = true;
        Debug.Log("[Raon] 녹음 시작");
    }

    public void StopAndSend()
    {
        if (!isRecording) return;
        int pos = Microphone.GetPosition(_micDevice);
        Microphone.End(_micDevice);
        isRecording = false;

        if (pos <= sampleRate / 4)
        {
            Debug.LogWarning("[Raon] 녹음이 너무 짧습니다.");
            return;
        }

        byte[] wav = EncodeWav(_recClip, pos);
        Debug.Log($"[Raon] 전송 {wav.Length / 1024}KB ({pos / (float)sampleRate:F1}초)");
        StartCoroutine(SendTalk(wav));
    }

    IEnumerator SendTalk(byte[] wav)
    {
        isWaiting = true;
        float t0 = Time.realtimeSinceStartup;

        var form = new List<IMultipartFormSection>
        {
            new MultipartFormFileSection("file", wav, "input.wav", "audio/wav"),
            new MultipartFormDataSection("session", sessionId)
        };

        using (var req = UnityWebRequest.Post($"{serverUrl}/talk", form))
        {
            if (!string.IsNullOrEmpty(token)) req.SetRequestHeader("X-Token", token);
            req.timeout = 60;
            yield return req.SendWebRequest();

            if (req.result != UnityWebRequest.Result.Success)
            {
                string msg = $"요청 실패: {req.error}";
                Debug.LogError($"[Raon] {msg}");
                OnError?.Invoke(msg);
                isWaiting = false;
                yield break;
            }

            lastHeard  = UnityWebRequest.UnEscapeURL(req.GetResponseHeader("X-Heard") ?? "");
            lastAnswer = UnityWebRequest.UnEscapeURL(req.GetResponseHeader("X-Answer") ?? "");

            AudioClip clip = DecodeWav(req.downloadHandler.data, "reply");
            if (clip == null)
            {
                OnError?.Invoke("음성 디코딩 실패");
                isWaiting = false;
                yield break;
            }

            Debug.Log($"[Raon] {Time.realtimeSinceStartup - t0:F1}초 | 들은 말: {lastHeard} | 답변: {lastAnswer}");

            _audio.clip = clip;
            _audio.Play();
            OnAnswer?.Invoke(lastHeard, lastAnswer);
        }
        isWaiting = false;
    }

    public void ResetSession() { StartCoroutine(DoReset()); }

    IEnumerator DoReset()
    {
        var form = new List<IMultipartFormSection>
        {
            new MultipartFormDataSection("session", sessionId)
        };
        using (var req = UnityWebRequest.Post($"{serverUrl}/reset", form))
        {
            if (!string.IsNullOrEmpty(token)) req.SetRequestHeader("X-Token", token);
            yield return req.SendWebRequest();
            Debug.Log("[Raon] 대화 맥락 초기화");
        }
    }

    // ── WAV 인코딩 (AudioClip → 16bit PCM) ──
    static byte[] EncodeWav(AudioClip clip, int sampleCount)
    {
        int channels = clip.channels;
        var samples = new float[sampleCount * channels];
        clip.GetData(samples, 0);

        int dataBytes = samples.Length * 2;
        var buf = new byte[44 + dataBytes];
        int p = 0;

        void S(string s) { foreach (char c in s) buf[p++] = (byte)c; }
        void I(int v) { buf[p++] = (byte)v; buf[p++] = (byte)(v >> 8); buf[p++] = (byte)(v >> 16); buf[p++] = (byte)(v >> 24); }
        void H(short v) { buf[p++] = (byte)v; buf[p++] = (byte)(v >> 8); }

        S("RIFF"); I(36 + dataBytes); S("WAVE");
        S("fmt "); I(16); H(1); H((short)channels);
        I(clip.frequency); I(clip.frequency * channels * 2);
        H((short)(channels * 2)); H(16);
        S("data"); I(dataBytes);

        foreach (float f in samples)
        {
            short v = (short)(Mathf.Clamp(f, -1f, 1f) * 32767f);
            buf[p++] = (byte)v; buf[p++] = (byte)(v >> 8);
        }
        return buf;
    }

    // ── WAV 디코딩 (16bit PCM → AudioClip) ──
    static AudioClip DecodeWav(byte[] data, string name)
    {
        if (data == null || data.Length < 44) return null;
        if (data[0] != 'R' || data[1] != 'I' || data[2] != 'F' || data[3] != 'F')
        {
            Debug.LogError("[Raon] RIFF 헤더가 아닙니다.");
            return null;
        }

        int channels = 1, rate = 24000, bits = 16, dataPos = -1, dataLen = 0;
        int pos = 12;
        while (pos + 8 <= data.Length)
        {
            string id = "" + (char)data[pos] + (char)data[pos + 1] + (char)data[pos + 2] + (char)data[pos + 3];
            int size = BitConverter.ToInt32(data, pos + 4);
            int body = pos + 8;
            if (id == "fmt ")
            {
                channels = BitConverter.ToInt16(data, body + 2);
                rate     = BitConverter.ToInt32(data, body + 4);
                bits     = BitConverter.ToInt16(data, body + 14);
            }
            else if (id == "data") { dataPos = body; dataLen = size; break; }
            pos = body + size + (size % 2);
        }
        if (dataPos < 0 || bits != 16) { Debug.LogError($"[Raon] 지원하지 않는 형식 (bits={bits})"); return null; }

        dataLen = Mathf.Min(dataLen, data.Length - dataPos);
        int n = dataLen / 2;
        var samples = new float[n];
        for (int i = 0; i < n; i++)
            samples[i] = BitConverter.ToInt16(data, dataPos + i * 2) / 32768f;

        var clip = AudioClip.Create(name, n / channels, channels, rate, false);
        clip.SetData(samples, 0);
        return clip;
    }
}
```

### 씬 설정

빈 GameObject를 만들고 위 스크립트를 붙입니다. `AudioSource`는 자동으로 추가됩니다. 인스펙터에서 `Server Url`만 확인하면 실행 가능합니다.

---

## 6. 캐릭터 설정 변경 방법

> **이 절의 방식은 없어졌습니다 (2026-08).** 서버에 `persona.md`·`knowledge.md`·
> `voice.wav` 는 없고 `POST /reload` 도 없습니다. **기본 인물도 기본 음성도 두지
> 않습니다** — 등록되지 않은 세션의 요청은 `409` 로 거절합니다. 폴백을 두면 등록을
> 잊었을 때 오류가 아니라 엉뚱한 목소리로 답해서 코드 결함처럼 보이기 때문입니다.

지금은 **웹 등록 도구**(`Web/`, **Raon 서버** `220.69.208.201:8500`)가 설문 답에서 인물 글과 사전지식을
만들고, 참조 음성과 함께 `POST /session/start` 로 세션마다 등록합니다. 서버에 ssh 로
들어가 파일을 고칠 일이 없습니다.

| 무엇을 바꾸고 싶은가 | 어디 |
|---|---|
| 말투·성격·호칭·대화 예시 | `Web/persona.py` `build_persona()` |
| 사전지식에 들어갈 사실 | `Web/persona.py` `build_knowledge()` |
| 공통 규칙(길이·기호·반말) | `Server/app.py` `BASE_RULES` |
| 설문 문항 | `Web/static/index.html` + 위 둘 |

쓰는 법은 [`페르소나_작성규격.md`](페르소나_작성규격.md).

**아직 같은 것** — 인물 글과 사전지식은 통째로 시스템 프롬프트에 들어갑니다. 모델의
컨텍스트가 131,072 토큰이므로 한국어 기준 8만~10만 자, A4 50~70페이지까지 그대로 넣을 수
있습니다. **별도의 검색 시스템(RAG)이 필요 없습니다.**

참조 음성은 잡음 없는 10~30초 분량이면 충분합니다. 다만 억양까지 복제하는
`tts_continuation`(`RAON_CONT=1`)은 **한 사람이 이어서 말한** 음성이어야 하고, 조각을
이어붙인 참조로는 깨집니다. 근거는 [`참조음성_실험결과.md`](참조음성_실험결과.md).

---

## 7. 트러블슈팅

| 증상 | 원인 / 해결 |
|---|---|
| 모든 요청이 즉시 실패 | Player Settings의 `Allow downloads over HTTP`가 `Not allowed`. `Always allowed`로 변경 |
| `Cannot connect to destination host` | 서버가 꺼져 있음. 서버에서 `./start.sh` 실행 |
| `503 model not ready` | 서버 기동 직후. 20초 대기 후 재시도 |
| 마이크를 찾을 수 없음 | 윈도우 개인정보 설정에서 마이크 접근 허용 확인 |
| `X-Answer`가 깨져 보임 | URL 인코딩됨. `UnityWebRequest.UnEscapeURL()`로 디코딩 |
| 응답은 오는데 소리가 안 남 | `AudioSource`의 볼륨/뮤트 확인, `DecodeWav` 반환값 null 여부 확인 |
| 답변이 너무 길어 오래 걸림 | `RAON_ANSWER_TOKENS`(기본 200)를 줄이거나 `Server/app.py` 의 `BASE_RULES` 에서 길이 규칙을 고친다. ~~`persona.md` + `/reload`~~ 는 없어졌다 |
| 여러 요청이 밀림 | 서버가 직렬 처리. `isWaiting` 중 입력 차단 |
| 학교 밖에서 접속 안 됨 | 교내망 전용. 외부 사용 시 `ssh -L 8000:localhost:8000 crc_unity@220.69.208.201` 터널 후 `http://localhost:8000` 사용 |

---

## 8. 라이선스 주의사항

이 모델은 **CC BY-NC 4.0** 라이선스입니다.

- **비상업적 용도만 허용됩니다.** 연구, 학습, 내부 프로토타입은 가능하지만 수익이 발생하는 제품에는 사용할 수 없습니다.
- **실존 인물의 목소리를 참조 음성으로 사용할 때는 반드시 사전 동의를 받아야 합니다.**
- **기본 `voice.wav` 는 이제 없습니다.** 목소리는 참여자가 웹에서 올린 것을 세션마다 등록하며, 동의 3종(사진 사용 · 음성 사용 · 「실제 고인이 아님」 이해)을 받고 있습니다.

---

## 9. 서버 운영 요약

```bash
ssh crc_unity@220.69.208.201

cd ~/server
./start.sh     # 기동 (준비까지 약 20초, VRAM 40GB 점유)
./status.sh    # 상태 및 GPU 사용량 확인
./stop.sh      # 종료 (VRAM 즉시 반납)
tail -f ~/server/server.log   # 실시간 로그
```

**GPU를 다른 사용자(`uc` 계정)와 공유 중입니다.** 상한은 `RAON_MEM_FRACTION` 으로 정하며 **지금은 0.70(=66.9GB)** 입니다 — 0.60(57.4GB)이 긴 대화에서 넘쳐 올렸습니다. 실제 사용량은 40여 GB 입니다. 작업이 끝나면 `./stop.sh`로 내려 GPU를 반납하는 것이 예의입니다.
