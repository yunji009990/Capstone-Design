# STT 모델 비교 (SenseVoiceSmall · Whisper large-v3 · Qwen3-ASR-1.7B)

기준일: 2026-09-16. 공통 규칙은 [AGENTS.md](../AGENTS.md), 검사 명령은 [AI 하네스](AI_하네스.md)를 따른다.

**이 문서의 실측은 실행 당시 기준이며, 그때 운영 STT는 SenseVoiceSmall(CPU INT8)이었다.**
Whisper·Qwen은 이 비교 프로세스에서만 실행했고 종료했으며, 이 실험에서는 운영 설정·모델·게이트를 바꾸지 않았다.

> **이후 적용(2026-09-16 16:01 KST):** 사용자 결정으로 운영 STT를 **Whisper large-v3로 바꿨다.**
> 전환 내용·설정·되돌리기와 적용 뒤 검증 기록은 [Whisper STT 전환](Whisper_STT_전환.md)에 있다.
> **아래 §2~§9는 전환 전 비교 실험의 기록이며 적용 뒤의 결과가 아니다.**
> §9의 운영 상태도 이 실험 시점의 것이다.

## 1. 비교 실험 당시의 결론

**이 절은 실험 시점(전환 전)의 결론이다. 이후 2026-09-16 16:01 KST에 운영 STT를 Whisper large-v3로
바꿨다.** 아래 수치와 판정은 그대로 두고, 적용 기록은 [Whisper STT 전환](Whisper_STT_전환.md)을 본다.

- 공개 낭독(FLEURS ko_kr) 62개 입력·3회 반복, 모델당 186회, 합계 **558회 추론 전부 성공**했다.
- clean CER은 **Qwen 3.20% < Whisper 3.48% < SenseVoice 6.97%**, 잡음(SNR 20dB)에서도 순서가 같다.
- **Whisper와 Qwen의 차이는 정답 1062글자에서 오류 3개뿐이다.** 이 표본 크기로 절대 우위를 단정할 수 없다.
  SenseVoice와 두 GPU 모델의 차이(오류 74 대 37/34)는 그보다 크다.
- 지연은 발화당 평균 SenseVoice 0.140초(CPU), Whisper 0.159초, Qwen 0.262초다.
  이 값은 **STT 추론만**이며 대화 전체 응답 시간이 아니다(§4).
- **세 모델 모두** 무음 1개와 백색 잡음 1개에 대해 비어 있지 않은 전사를 냈다. STT 단독으로는 환청을 막지 못한다.
  현재 운영 게이트를 유지해야 한다(§3).
- 사용자가 정확도를 우선하므로 **다음 실제 마이크 검사 후보는 Qwen3-ASR-1.7B**, 더 빠른 대안은 Whisper large-v3였다.
  이 실험 시점에는 **교체하지 않았고**, 실제 마이크·사용자 목소리·청취 검증 뒤에 결정하기로 했다.

## 2. 실측 결과

정확도는 1회차 기준, 지연은 3회차 전부를 사용한다. 반복 간 전사는 **모든 모델·모든 입력에서 동일**했다
(`unstable_samples: 0`). 메인이 편집 거리와 지연을 독립 재계산하고, 세 보고서의 오디오 SHA-256·정답·길이가
서로 일치함을 확인했다(`cross_model_input_identity: true`).

| 모델 | 조건 | 입력 | 정답 글자 | 오류 | CER | 정규화 후 문장 완전일치 | 평균 | p95 | GPU peak |
|---|---|---|---|---|---|---|---|---|---|
| SenseVoiceSmall | clean | 30 | 1062 | 74 | **6.97%** | 7/30 | 0.140s | 0.170s | 해당 없음(CPU) |
| SenseVoiceSmall | white_snr20 | 30 | 1062 | 79 | **7.44%** | 6/30 | 0.139s | 0.170s | 해당 없음(CPU) |
| Whisper large-v3 | clean | 30 | 1062 | 37 | **3.48%** | 17/30 | 0.159s | 0.202s | 4562 MiB |
| Whisper large-v3 | white_snr20 | 30 | 1062 | 44 | **4.14%** | 18/30 | 0.159s | 0.197s | 4562 MiB |
| Qwen3-ASR-1.7B | clean | 30 | 1062 | 34 | **3.20%** | 21/30 | 0.262s | 0.344s | 5210 MiB |
| Qwen3-ASR-1.7B | white_snr20 | 30 | 1062 | 40 | **3.77%** | 20/30 | 0.261s | 0.344s | 5210 MiB |

모델 적재와 예열은 추론 지연과 별개로 기록했다.

| 모델 | 적재 | 예열 1회 |
|---|---|---|
| SenseVoiceSmall | 0.465s | 0.101s |
| Whisper large-v3 | 1.026s | 0.225s |
| Qwen3-ASR-1.7B | 4.210s | 0.487s |

GPU 최대 메모리는 `nvidia-smi`의 **자기 PID** compute-apps 사용량이다.
SenseVoice는 CPU 실행이라 자기 PID가 목록에 없어 `peak_mib: null`, 사유 `own_pid_not_listed`로 남았다.
**측정값 0 MiB가 아니다.**

## 3. 무음·잡음 입력과 고정 게이트 검사

STT에 무음 3초와 백색 잡음 3초를 그대로 넣었을 때 **세 모델 모두 비어 있지 않은 전사**를 냈다
(모델당 `hallucinated_inputs` 각 1개). CER 분모에서는 빠지며 환청 건수로만 센다.

별도로 확인한 현재 운영 경로(raw WebRTC VAD → `SileroSpeechGate`, `verified_input_v1`)는 이 입력들을 걸러 낸다.

- clean 30/30, white_snr20 30/30이 **하나 이상의 candidate로 승인**됐다.
- 무음·백색 잡음 2개는 **모두 거부**됐다.
- 각 음성 앞뒤에 1초 zero pad를 붙여 넣었고, 조건별로 1개 원음이 candidate 2개로 분할됐다.
- **전체 WAV를 Silero에 바로 넣으면 clean 29/30, noise 30/30이다.** 위 게이트 경로 결과와 혼동하지 않는다.
- 이 검사는 게이트 통과 여부만 본 것이고, **잘린 candidate의 STT 정확도를 측정한 것이 아니다.**

결과: `tools/_work/stt_compare_20260916/fixed-gate.json`.

## 4. 이 숫자가 말하지 않는 것

- **STT 단독, 전체 발화 입력이다.** VAD·잡음 제거·대화 프롬프트를 적용하지 않았다.
- 지연에는 **모델 적재·예열·파일 IO·VAD·LLM·TTS·네트워크가 들어 있지 않다.**
  이것을 전체 대화 응답 시간이나 스트리밍 첫 토큰 시간이라고 부르면 안 된다.
- **공개 낭독 ≠ 사용자 마이크.** 실제 거리·장치·주변 소음·발화 종료는 아직 측정하지 않았다.
- 정규화는 NFKC → casefold → 글자·숫자만 유지다. **숫자 발음과 숫자 표기를 의미로 맞추지 않는다**("세 시" ≠ "3시").
- **CER ≠ 의미 정확도.** 조사 한 글자와 인물 이름 오인식의 무게가 같다.
- 입력 파일 62개는 **원본 녹음 30개**에서 나왔고, 3회 반복은 새 음성이 아니다.
  표본 수는 **원본 녹음 기준 30개이며, 화자 독립성은 확인하지 않았다.**
- GPU 실험은 추가 프로세스를 **하나씩만** 올려 진행했다. 기존 서비스는 실행 중이었지만
  **발화·LLM·TTS 동시 부하 시험은 하지 않았다.**

## 5. 평가 세트 (모델 결과를 보기 전에 고정)

출처: [FLEURS](https://huggingface.co/datasets/google/fleurs) `ko_kr` test (CC-BY-4.0).

- **앞 100행** 중 길이 **3~12초**인 것에서 남성 라벨 15개 + 여성 라벨 15개 = **원본 녹음 30개**를 골랐다.
  라벨은 성별 표기이므로 **30명의 서로 다른 화자라고 단정할 수 없다.**
- 평균 길이 9.478초, 합계 284.34초.
- 입력 62개 = `clean` 30 + `white_snr20` 30 + `silence` 1 + `white_noise` 1.
  **`negative`라는 condition 라벨은 실제로 쓰지 않았다.**
- 잡음 합성: 백색 잡음 **SNR 20dB**, seed = `20260916 + 행 번호`.
  신호/잡음의 **평균 전력**으로 배율을 계산하고, 합성 후 피크가 0.98을 넘으면 **전체를 같은 비율로 감쇠**했다.
  결과는 16kHz mono PCM16이다.
- 음성이 없는 2개는 각각 3초이고, 백색 잡음의 RMS는 -30 dBFS다.
- 반복 3회, 모델당 186회, 합계 558회.

### manifest 필드 불일치 (그대로 남긴다)

corpus manifest의 실제 필드 이름은 **`source_clips: 30`**이다. 실행기가 읽는 선택 필드는 `source_recordings`라
이름이 달라서, 세 보고서의 `declared_source_recordings`는 모두 **`null`**이다.
**원본 보고서는 수정하지 않았다.** 위에 적은 "원본 녹음 30개"는 corpus manifest에서 따로 확인한 값이며,
실행기가 선언을 받아 기록한 값이 아니다. 다음 실행에서 이름을 맞추면 보고서에도 남는다.

## 6. 재현 정보

- 원격 작업 경로 `~/capstone-server/checks/stt_compare_20260916`, 로컬 `tools/_work/stt_compare_20260916`.
- 원격의 `runner/tools/eval_stt_models.py`는 **이번에 만든 비교 도구의 사본**이고,
  `runner/Server/realtime_audio.py`만 **당시 운영 baseline의 사본**이다. 입력은 `corpus/manifest.json`이다.
- 환경: SenseVoice는 기존 `~/venv/dialogue`, Whisper는 기존 `~/venv/stt`,
  Qwen은 신규 격리 환경 `~/venv/stt-compare-qwen`. 세 실행 모두 `OMP_NUM_THREADS=2`, `MKL_NUM_THREADS=2`.
- 하드웨어: RTX PRO 6000 Blackwell 96GB, 시작 시 여유 9301 MiB.
- 모델 스냅샷
  - Whisper: `edaa852ec7e145841d8ffdb056a99866b5f0a478`
    (`~/.cache/huggingface/hub/models--Systran--faster-whisper-large-v3/snapshots/`)
  - Qwen: `7278e1e70fe206f11671096ffdd38061171dd6e5`
    (`~/.cache/huggingface/hub/models--Qwen--Qwen3-ASR-1.7B/snapshots/`)
- Qwen 실행 패키지: torch 2.8.0+cu128, torchaudio 2.8.0+cu128, transformers 4.57.6, qwen-asr 0.0.6, accelerate 1.12.0.
  `--gpu-memory-budget-mib 6500`을 실제로 지정했다. 이것은 **PyTorch allocator 상한이며 프로세스 전체 VRAM 상한이 아니다**
  (실제 점유는 위 표의 5210 MiB).
- 보고서 SHA-256
  - `sensevoice.json` `fd22621227977de278b9bb5ba241bc96b4161f42bf174bc1b2892aaf0acb3875`
  - `whisper.json` `8a743944bbbb1ccac956767086e1e71193832f35fb086be467e8c80505fb0ea4`
  - `qwen.json` `2e9c7998af059815400b5a18ea485979d648168117e0760434856e5f936da63f`
- 보관 위치: 위 세 파일과 `reviewed-summary.json`(독립 재계산 요약), `fixed-gate.json`이
  `tools/_work/stt_compare_20260916/`에 있다. 이 폴더는 Git에서 제외한다.
- 공식 출처: [Qwen3-ASR](https://github.com/QwenLM/Qwen3-ASR), [faster-whisper](https://github.com/SYSTRAN/faster-whisper).

## 7. 실행기 사용법

`tools/eval_stt_models.py`는 **한 번에 한 백엔드만** 실행하고, 프로세스가 끝나면서 GPU를 반납한다.
Whisper와 Qwen을 동시에 올리지 않는다.

### 같은 공개 자료로 서버에서 다시 돌리기 (Qwen 예시)

아래는 **모델 서버(Linux)에서 그 백엔드의 격리 venv로** 실행하는 명령이다.
로컬 Windows의 기본 `python`으로 GPU 실험을 돌리라는 뜻이 아니다. 백엔드마다 venv가 다르다
(SenseVoice `~/venv/dialogue`, Whisper `~/venv/stt`, Qwen `~/venv/stt-compare-qwen`).
이미 내려받은 스냅샷과 이미 만들어 둔 corpus를 그대로 쓰며, 모델·자료를 다시 내려받지 않는다.

```bash
cd ~/capstone-server/checks/stt_compare_20260916 && \
OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 \
~/venv/stt-compare-qwen/bin/python runner/tools/eval_stt_models.py \
  --backend qwen \
  --model ~/.cache/huggingface/hub/models--Qwen--Qwen3-ASR-1.7B/snapshots/7278e1e70fe206f11671096ffdd38061171dd6e5 \
  --manifest corpus/manifest.json \
  --warmup-audio ~/dialogue-models/sensevoice/test_wavs/ko.wav \
  --repeats 3 --gpu-memory-budget-mib 6500 --include-transcripts \
  --output rerun/20260917-qwen.json
```

- `--output`은 **새 파일 이름**이어야 한다. 실행기가 기존 보고서 덮어쓰기를 거부하므로 위 `sensevoice.json`·
  `whisper.json`·`qwen.json`은 안전하다. 날짜·용도를 바꿔 새 이름을 쓴다.
- 실제 예열 경로는 서버에서 SHA-256 일치를 확인했다
  (`0dc797a5c81ed30fc339d91f3da718ab02854e17ffa37cb93c4c039ac5c6bb9c`).
  보고서는 경로 대신 해시를 보관한다. 평가 표본과 같은 파일이면 실행기가 거부한다.
- 다른 백엔드는 `--backend`·`--model`·venv를 바꾸고, Whisper는 `--gpu-memory-budget-mib`를 **빼야** 한다(거부됨).
- GPU 백엔드는 한 번에 하나만 올린다. 앞 프로세스가 끝나 메모리를 반납한 뒤 다음을 실행한다.

### 새 자료로 실행할 때

| 항목 | 내용 |
|---|---|
| 고정 설정 | SenseVoice는 `Server/realtime_audio.SenseVoiceFrontend` 그대로(CPU 2스레드, `ko`, ITN). Whisper는 `float16`·`ko`·`beam_size=5`·`temperature=0`·`condition_on_previous_text=False`·`vad_filter=False`. Qwen은 `bfloat16`·`cuda:0`·`max_new_tokens=512`·`language=Korean`·타임스탬프 없음 |
| 입력 | 16kHz mono PCM16 WAV, 30초 이하. 형식이 다르면 거부하고 변환하지 않는다 |
| manifest | `{samples:[{id, audio, reference, condition?, source?}]}`. `reference`는 필수이며 빈 문자열은 "말이 없는 표본"을 뜻한다. `audio` 상대 경로는 manifest 위치 기준 |
| 예열 | `--warmup-audio`는 필수이고 평가 표본과 같은 파일이면 거부한다. 예열 시간은 지연 통계에서 뺀다 |
| 주요 인수 | `--repeats`(기본 3), `--limit`, `--compute-type`(whisper), `--gpu-memory-budget-mib`(qwen 전용, 양의 정수), `--include-transcripts`(공개 자료에만) |
| 출력 | `--output`은 새 파일이어야 한다. 실패가 하나라도 있으면 `complete=false`와 종료 코드 1이며, 단계(`model_load`/`warmup`/`inference`/`close`)와 예외 타입만 남긴다 |
| 오프라인 | 적재 직전 이 프로세스에만 `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`을 설정하고 로더에 `local_files_only=True`를 전달한다. 자동 내려받기를 하지 않는다 |

## 8. 수동 마이크 녹음 도구

실제 마이크 비교는 `tools/record_stt_samples.py`로 직접 읽어 녹음한다(표준 라이브러리 WinMM).
가상 대본 6개를 순서대로 보여 주고, **Enter를 누른 뒤에만** 문장별로 녹음한다.
Enter=저장, `r`=다시 녹음, `q`=종료다. 16kHz 16bit mono, 문장당 2~15초다.

```powershell
python tools/record_stt_samples.py --list-devices
python tools/record_stt_samples.py --output .dialogue-work/stt-mic/20260916-jw --seconds 8 --device 0
```

- `--list-devices`는 **목록만 출력하고 장치를 열거나 녹음하지 않는다.** 이 조회는 실제로 성공했다.
  현재 PC에서 Unity의 CurrentMic `마이크 (USB2.0 Device)`와 WinMM **ID 0** `마이크(USB2.0 Device)`의 이름이 일치해
  위 명령에 `--device 0`을 쓴다.
- **PC나 장치 구성이 바뀌면 목록을 다시 확인하고 ID를 다시 고른다.**
  기본값 `WAVE_MAPPER`가 Unity가 쓰는 장치와 같다는 보장이 없다.
- 저장되는 `reference`는 대본 문장이다. **실제로 말한 내용과 달라지면 `r`로 다시 녹음한다.**
  잘못 읽은 녹음을 그대로 저장하면 CER이 모델 오류가 아니라 낭독 오류를 재는 값이 된다.
- 저장된 `manifest.json`을 그대로 `eval_stt_models.py`의 `--manifest`로 넣는다. **자동 업로드는 없다.**
- 비교는 **최소 3개 원본 녹음, 각 3회**로 한다.
- 녹음 자료와 manifest·결과는 `.dialogue-work/` 아래에 두고 Git에 올리지 않는다.

**목록 조회까지만 확인했고 실제 마이크 캡처·사용자 목소리·청취 검증은 아직 하지 않았다.**

## 9. 모의 검사와 최종 검증 기록

- 통합 검사 `python tools/check.py --area dialogue --output tools/_work/checks/20260916-stt-comparison`:
  **243개(하네스 94 + 서버 149) 통과, 건너뜀 0.**
  결과: `tools/_work/checks/20260916-stt-comparison/report.json`.
  평가기 63개·녹음기 17개는 개발 중 단독으로 돌린 같은 검사들이므로 위 243개에 더해 세지 않는다.
- 이 검사 통과는 **계산과 자료 검증이 동작한다**는 뜻이고, §2의 558회 실제 모델 실험과는 다른 층이다.
- 운영 상태: **설정 변경·서버 재시작·Unity 편집을 하지 않았다.**
  대화 API health `ready`, LLM·TTS `ready`, 같은 프로세스가 그대로 유지됐고 GPU 여유도 9301 MiB로 원복했다.
- Unity는 `Scene_2`가 Play 중이었고, 체험은 `fail_microphone_stopped`로 끝난 상태였다.
  **새 STT 모델을 Unity에서 실사용 검증한 것이 아니다.** 이 마이크 실패의 원인 진단은 이번 실험 범위가 아니다.
- 결과 폴더 `tools/_work/`, `.dialogue-work/`가 Git에서 제외됨을 확인했다.

## 10. 다음

1. 실제 마이크로 같은 대본을 녹음해 세 모델을 같은 방식으로 비교한다(§8).
2. 사용자 목소리·거리·주변 소음에서의 정확도와 청취 판정을 기록한다.
3. 그 결과와 이 문서를 함께 보고 운영 STT 교체 여부를 결정한다. 교체 시에는 게이트·지연·동시 부하를 다시 본다.

**현재 운영 상태: 2026-09-16 16:01 KST에 Whisper large-v3(cuda/float16)를 적용했다.**
위 1~3은 이 실험 시점의 계획이다. 적용 기록과 남은 확인(실제 마이크·청취 판정 포함)은
[Whisper STT 전환](Whisper_STT_전환.md)에서 관리한다. 위 558회 비교는 그 적용 뒤에 다시 하지 않았다.
