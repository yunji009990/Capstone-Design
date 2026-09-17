# Whisper STT 전환

기준일: 2026-09-16. 공통 규칙은 [AGENTS.md](../AGENTS.md), 검사 명령은 [AI 하네스](AI_하네스.md)를 따른다.
모델 선택 근거의 실측은 [STT 모델 비교](STT_모델_비교.md), 대화 구조는 [서버 음성 대화 구조](서버_음성대화_구조.md)에 있다.

**사용자 결정으로 운영 STT를 SenseVoiceSmall(CPU INT8)에서 Whisper large-v3(GPU)로 바꾸고,
음성 감정 분류 기능을 함께 제거했다.** 이 문서는 코드·설정·설치·되돌리기와 검증 상태를 정리한다.
**2026-09-16 16:01 KST 운영에 적용했다.** 무엇을 확인했고 무엇이 남았는지는 §6을 그대로 읽는다.

## 1. 바뀌는 것과 바뀌지 않는 것

| 구분 | 내용 |
|---|---|
| 바뀜 | `Server/realtime_audio.py`의 인식기. `WhisperFrontend`(faster-whisper)가 기본이다 |
| 바뀜 | 설정 `DIALOGUE_STT_BACKEND/DEVICE/COMPUTE_TYPE`, `DIALOGUE_MODEL_DIR` 기본값 |
| 바뀜 | `/health`의 `stt`가 실제로 적재한 frontend 이름을 돌려준다 |
| 바뀜 | **음성 감정 분류 기능을 전부 제거**했다. `Observation`에 그 항목이 없다 |
| 바뀜 | 관측의 소리 이벤트가 `unknown`, 언어는 요청에서 고정한 `ko` |
| 그대로 | WebRTC VAD 발화 구분, Silero 입력 검증 `verified_input_v1`(임계값·최소 길이 포함) |
| 그대로 | 의미 기반 끼어들기 `semantic_v1`, 응답 순서·취소·타임아웃·`response_id` |
| 그대로 | LLM·TTS 경로, 리액션 캐시, 메모리, Unity 프로토콜(새 필수 필드 없음) |
| 그대로 | `prosody`의 `duration_sec`·`rms_dbfs` 계산 방식, 전사·소리·언어 전달 경로 |

### 음성 감정 분류 제거

사용자 결정으로 **목소리 감정 분류 기능 자체를 없앴다.** `unknown` 자리표시자도 두지 않는다.

- `Observation`에서 `emotion`·`emotion_confidence` 필드를 삭제했다. 관측은 전사 `text`와
  `audio_event`·`language`·`prosody`만 담는다. 부분 전사·최종 전사·문자 입력·판정 입력
  어디에도 감정 키가 나가지 않는다.
- 되돌리기용 SenseVoice 경로도 모델이 내는 화자 감정 라벨을 읽지 않는다.
- 답변 프롬프트의 음성 감정 추정 경고와 판정 규칙의 감정 태그 문장을 지웠다.
- 사례 파일(`tools/turn_judge_cases.json`, `tools/dialogue_prompt_cases.json`)의 감정 항목을 지우고,
  음성 감정용이던 사례 4개는 실제 문장 상황에 맞는 이름으로 정리했다. 기대 경로·판정은 그대로다.
- **일반 대화에서 사용자의 감정 표현에 공감하는 인물·프롬프트 규칙은 그대로 둔다.** 이번 범위는
  목소리에서 감정을 분류하던 기능이다.
- Unity의 감정 프로퍼티·DTO·UI 행·번역·Editor 검사 제거도 끝냈다. 실제 `DialogueVoiceClient`에
  `VoiceEmotion` 속성이 없고 `VoiceAudioEvent`·`VoiceLanguage`·`VoiceDuration`·`VoiceRmsDb`는 그대로다.

## 2. 고정 설정

실제 비교 실험에서 쓴 조합을 그대로 쓴다.

- 모델: `Systran/faster-whisper-large-v3`, 리비전 `edaa852ec7e145841d8ffdb056a99866b5f0a478`
- 실행: `faster-whisper==1.2.1`, `ctranslate2==4.8.2`, CUDA `float16`, **torch는 필요 없다**
- 디코딩: `language="ko"`, `beam_size=5`, `temperature=0`,
  `condition_on_previous_text=False`, `vad_filter=False`, `without_timestamps=True`
- 입력: 16 kHz mono PCM16. 다른 레이트의 참조 음성(8–96 kHz)은 **실제로 리샘플**한다.
  레이트는 정수 Hz만 받는다(문자열·소수·bool은 거절). 길이·세기는 원 PCM으로 계산한다

## 3. 직렬화·수명

- 모델 하나를 `max_workers=1` 실행기가 독점한다. faster-whisper의 세그먼트 생성기까지
  **그 워커 스레드에서 끝까지 소비**한 뒤에 반환한다. 반환 후에 남은 GPU 작업이 없다.
- 호출자가 취소돼도 이미 올라간 작업은 끝까지 돌고, 다음 호출은 그 뒤에 시작한다.
  기존 SenseVoice 경로와 같은 계약이며, 취소가 GPU 추론을 중단시키지는 못한다.
- 시작(lifespan) 중에 모델을 적재하고 무음 0.5초로 **예열**한다. CUDA 네이티브 오류는
  첫 추론에서 드러나므로 `/health`가 `ready`를 말하기 전에 여기서 걸러진다.
  **예열 텍스트는 버린다.** 자막·대화 기록·기억 어디에도 들어가지 않는다.
- 적재나 예열이 실패하면 **서버 시작이 중단된다.** 다른 백엔드로 자동 대체하지 않는다.
- `close()`는 진행 중인 작업이 끝난 뒤 실행기를 정리하고 모델 참조를 놓는다.
- 시작 도중 **뒤의 준비(Silero 등)가 실패해도** 이미 올라온 모델과 실행기를 놓는다.
  정상 종료와 시작 실패가 같은 경로로 한 번만 정리하며, 주입받은 자원은 닫지 않는다.

## 4. 설치와 설정

```bash
# 새 PC 운영 설치: STT + 필수 입력 검증기
python setup_dialogue_models.py --whisper --vad
```

`--whisper`는 고정 리비전에서 실행에 필요한 파일만 받는다(`model.bin`, `config.json`,
`tokenizer.json`, `vocabulary.json`, `preprocessor_config.json`). 받은 `model.bin`의 SHA256을
출력하므로 다른 PC 설치와 대조한다. `--all`은 **기존 의미 그대로 SenseVoice + Silero VAD**이고
Whisper를 포함하지 않는다. `--all --whisper`는 오류로 거절한다.

```
DIALOGUE_STT_BACKEND=whisper
DIALOGUE_STT_DEVICE=cuda
DIALOGUE_STT_COMPUTE_TYPE=float16
# DIALOGUE_MODEL_DIR=/home/crc_unity/dialogue-models/whisper-large-v3
```

모델 폴더가 캐시 스냅샷을 가리키는 심볼릭 링크여도 된다. 확인은 링크를 따라 한다.
`DIALOGUE_MODEL_DIR`을 설정하지 않으면 백엔드에 맞는 기본 경로를 쓴다(whisper → `~/dialogue-models/whisper-large-v3`,
sensevoice → `~/dialogue-models/sensevoice`). 로컬 폴더만 허용하고 `local_files_only=True`로 연다.
**시작 중에 모델을 내려받지 않는다.** 폴더·`model.bin`·`config.json`이 없으면 시작을 거절한다.
잘못된 백엔드·장치·연산 타입도 오류로 거절하며, CPU는 명시했을 때만 쓴다. 자동 CPU 대체는 없다.

## 5. 되돌리기

```
DIALOGUE_STT_BACKEND=sensevoice
DIALOGUE_MODEL_DIR=/home/crc_unity/dialogue-models/sensevoice
```

기존 CPU 모델과 `sherpa-onnx` 의존성을 그대로 두므로 대화 API 재시작만으로 돌아간다.
**명시적으로 지정했을 때만 동작한다.** Whisper 실패를 SenseVoice로 대신 처리하지 않는다.
되돌리면 소리 이벤트·언어가 다시 모델 판정값이 된다. 그 모델이 내는 화자 감정 라벨은
읽지도, 내보내지도 않는다.

## 6. 적용과 검증 기록

### 6.1 운영 적용 (2026-09-16 16:01 KST)

- **대화 API 8002만 다시 시작했다.** LLM·TTS·웹·등록 프로세스는 재시작하지 않았다.
- `/health`가 `status=ready`, `stt=faster-whisper whisper-large-v3 cuda/float16`,
  `llm_ready=true`, `tts_ready=true`, `connections=0`이다. 적용 전에는 `stt=SenseVoiceSmall CPU`였다.
- 입력 검증 `verified_input_v1`(silero_vad, threshold 0.7, 최소 200 ms)과 끼어들기 `semantic_v1`은
  값까지 그대로다.
- 배포한 서버 파일 7개(`realtime_audio.py`, `dialogue_server.py`, `realtime_dialogue.py`,
  `interruption_policy.py`, `setup_dialogue_models.py`, `requirements-dialogue.txt`,
  `dialogue.env.example`)의 로컬·서버 SHA256이 모두 일치한다.
- 기존 Python 패키지의 **버전 변경은 0건**이고 `pip check`를 통과했다. 되돌리기용 SenseVoice 설정은
  그대로 남아 있으며, 그 경로에도 감정 추출·감정 필드는 없다.
- 백업: 서버의 `~/capstone-server/backups/whisper_switch_20260916`.

### 6.2 정식 검사

`python tools/check.py --area all --output tools/_work/checks/20260916-whisper-no-emotion`
**319개 통과, 건너뜀 0**(하네스 94 + 서버 176 + Tripo 작업자 5 + 웹 44).

서버 176개에는 신규 `Server/tests/test_whisper_frontend.py`가 들어 있다. 고정 디코딩 설정과
`local_files_only`, 단일 실행기 직렬화와 취소 뒤 비중첩, 생성기 소비 완료, 16 kHz 직통과
non-16k 실제 리샘플, 원 PCM 기준 길이·세기, 음성 메타데이터가 `audio_event`·`language`·`prosody`뿐인지,
한글 세그먼트 연결, 없는 모델·잘못된 설정 거절, 되돌린 SenseVoice의 감정 라벨 비노출,
`/health`의 이름 정확성, 예열 실패 시 준비 거부, SenseVoice 명시 복구를 본다.
이 검사의 통과는 **배선·계약**만 보장하고 전사 품질을 보장하지 않는다.

### 6.3 실제 모델 왕복

운영 서버의 loopback으로 80 ms PCM을 보내 VAD·Silero → Whisper → Gemma → Qwen TTS → PCM 수신까지 확인했다.
공개 샘플 1개와 합성 대본 2개, 각 3회로 **9/9 성공**이다.

- 9회 모두 전사가 비어 있지 않고 `audio_event=unknown`, `language=ko`이며 **감정 필드가 없다(9/9)**.
- 9회 모두 24 kHz mono PCM 답변을 받았고 무음이 아니었다. 응답은 한 번씩만 왔고 연결은 정상 종료,
  임시 참조는 삭제됐으며 끝난 뒤 `connections=0`이다.
- 무음 3초와 백색 잡음 3초 대조 **2/2**: 어떤 이벤트도 나가지 않았다. 이는 **답변이 없는 대기 상태**의
  결과이며, 재생 중 끼어들기 거절을 검증한 것이 아니다.
- **왕복 성공이 문장 정확도 100%는 아니다.** 합성 대본 `walk`의 첫 회차에서 "설명해 줘"를
  "설명해줍니다"로 전사했고, 같은 입력의 나머지 2회는 "설명해줘"로 전사했다.

### 6.4 시간과 자원 (한 시점 기록)

- 서버 진단 로그의 이번 9회 STT 소요: **평균 85.0 ms, 최대 100.1 ms.**
- 클라이언트가 잰 첫 답변 오디오는 **마지막 입력 패킷 전송 시각 기준**이다. 실제 말끝이나 Unity의
  재생 시작 지연이 아니므로 사용자 체감 대기 시간으로 읽으면 안 된다.
  일반 6회 평균 1.07초(0.46–1.50), 추론 3회 평균 8.64초(6.79–11.50)로 경로를 구분해 본다.
- GPU는 대화 프로세스(PID 2819981) 4308 MiB, 전체 92262 MiB 사용 / 4988 MiB 여유다.
  **한 시점 스냅샷이며 최대치가 아니다.** 서버 로그의 ERROR·Traceback·OOM은 0건이다.

### 6.5 참조 음성 레이트

공개 WAV를 24 kHz와 48 kHz로 리샘플해 참조 업로드 API로 올렸다. 두 경우 모두 실제 자동 전사가
나왔고 원본 길이 4.608초가 유지됐으며 구간이 잘리지 않았다. 올린 참조 2개는 모두 삭제됐다.

### 6.6 Unity

메인이 Play를 멈추고 다시 컴파일한 뒤 Play를 복구했다. 새 `Assembly-CSharp`/Editor DLL을 확인했고
`error CS`는 0건이다. 실제 `DialogueVoiceClient`에 `VoiceEmotion` 속성이 없고
`VoiceAudioEvent`·`VoiceLanguage`·`VoiceDuration`·`VoiceRmsDb`는 남아 있다. 현재 `Scene_2`는 Play 중이며
체험은 시작하지 않은 상태다(`ExperienceActive=false`, `DialogueConnected=false`).
MCP `execute_code`는 기존 Windows CodeDom 경로 길이 오류로 실패해 리소스 조회와 컴파일 결과로 확인했다.
**실제 마이크 녹음·스피커 재생·청취 평가는 하지 않았다.** Unity에서 실제 음성 대화를 시험한 것이 아니다.

### 6.7 남은 확인

- 사용자의 실제 마이크·목소리·거리·주변 소음에서의 인식 품질과 청취 판정.
- Unity에서 마이크 입력부터 스피커 재생까지의 실사용 확인과 발화 끝 → 첫 재생 체감 시간.
- 동시 부하에서의 GPU 여유와 지연 재확인.
- 감정 항목을 지운 판정 사례 68개로 실제 Gemma 판정을 **다시 측정하지 않았다.**
  [판정기 텍스트 검사](판정기_텍스트_검사.md)의 수치는 수정 전 사례 파일 기준이다.

검증 실행 결과 파일(`final-facts.json`, `live-report.json`, `postflight.json`, `deployment.json`,
`reference-rates.json`)은 Git에서 제외된 `tools/_work/whisper_switch_20260916/`에 있다.

## 7. 주의

- [STT 모델 비교](STT_모델_비교.md)의 558회는 **전환 전에 세 모델을 비교한 실험**이고, §6의 기록과
  다른 실행이다. 두 수치를 섞어 읽지 않는다. 그 비교표의 지연은 **STT 추론 구간** 기준이고,
  §6.4의 첫 답변 오디오는 마지막 입력 패킷 기준의 클라이언트 관측값이다. 둘 다 사용자 체감이
  아니므로, 체감은 발화 끝 → 첫 재생으로 따로 재야 한다.
- Whisper는 무음·잡음에도 비어 있지 않은 전사를 낼 수 있다. **입력 검증 게이트를 유지한다.**
- STT는 Gemma·TTS와 같은 GPU를 쓴다. 다른 사용자의 프로세스를 종료하지 않고,
  동시 부하에서 메모리 여유를 확인한다.
