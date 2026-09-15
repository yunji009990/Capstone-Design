# TTS 재연결과 실제 동작 검사

2026-09-14. 사용자 요청에 따라 Qwen3-TTS를 다시 연결했다.
이 문서는 재연결 당시의 구절 합성 경로 기록이다. 이후 적용한 생성 중 오디오 전송과 최신 검증은
[TTS 실시간 스트리밍](TTS_실시간_스트리밍.md)을 따른다.
몸짓 반응·동작 따라 하기 등 VR 추가 기능은 현재 음성 대화 작업 이후로 보류한다.

## 재연결 당시 동작

`Unity 음성 → WebRTC VAD·SenseVoiceSmall → Gemma → Qwen3-TTS → Unity AudioSource·자막`으로 동작한다.
TTS는 기존 `Qwen/Qwen3-TTS-12Hz-1.7B-Base`와 독립 `~/venv/qwentts` 환경을 그대로 사용한다.
참조 음성·전사를 조건으로 하는 `reference_icl` 모드이며 문장별 합성 후 24kHz 모노 PCM16을 전송한다.

- 대화 API: `http://220.69.208.201:8002`, `mode=streaming_voice`.
- TTS: 서버 내부 `http://127.0.0.1:8003`, `tts_ready=true`, `tts_voice_mode=reference_icl`.
- 운영 설정: `~/capstone-server/dialogue.env`의 `DIALOGUE_TTS_URL=http://127.0.0.1:8003`.
- 두 서비스의 TTS 토큰 일치를 확인했다. 실제 값은 문서·결과 파일에 기록하지 않았다.
- TTS를 시작하고 대화 API만 재시작했다. Gemma·웹·등록·Tripo는 같은 PID와 시작 시각을 유지했다.
- 서버 실행 소스 7개는 검사 전후 SHA256이 같으며, 이번에는 실행 설정만 변경했다.
- Unity 씬·스크립트·패키지·ProjectSettings 파일은 변경하지 않았다.

설정 및 로그 백업: `/home/crc_unity/capstone-server/backups/tts_enable_20260914T025806Z`.
검사 종료 시 대화 연결 0개, TTS 작업 없음, GPU 참조 프롬프트 0개를 확인했다.
대화·TTS 로그의 ERROR, traceback, GPU 메모리 부족은 모두 0건이다.

## 실행한 검사

| 검사 | 실제 실행 범위 | 결과 |
|---|---|---|
| 기본 검사 | `python tools/check.py --area dialogue`; 하네스 11개·서버 85개 모의 검사 | 96개 통과, 건너뜀 0개 |
| Unity 음성 왕복 | 공개 WAV 참조 업로드 → 같은 WAV를 PCM 입력 → VAD·STT → Gemma → TTS → AudioSource 재생·대화 화면 | 통과 |
| Unity 텍스트 질문 | 자동 검사에서 상황 이벤트와 텍스트 질문 전송 → Gemma → TTS → AudioSource | 통과, STT는 거치지 않음 |
| Unity 끼어들기 | 합성한 PCM 발화로 재개·수정·주제 전환·대기 후 재개 | 4개 경우 모두 통과 |
| TTS와 기억 | 실제 대화 API에서 7턴, 회상·정정·선택 삭제·삭제 후 질문·초기화 | 6개 확인 조건 통과 |
| 합성 입력 확인 | 공개 참조로 검사 문장 5개 합성 후 SenseVoice로 다시 전사 | 공백·문장부호를 제외한 내용 일치 |

음성·텍스트 왕복 및 끼어들기는 Unity 2022.3.62f2의 `AI_Response_Test`에서 Play Mode로 실행했다.
재개에서는 같은 응답 ID를 유지하고 PCM 소비 위치가 4,800 → 4,800으로 유지됐다.
대기 후 재개는 14,400 → 14,400이었으며, 명시적 재개 요청 전에는 소리가 다시 시작되지 않았다.
수정·주제 전환에서는 기존 응답을 각각 한 번만 취소하고 새 답변을 끝까지 재생했다.

기억 검사는 별도 WebSocket 검사 클라이언트에서 텍스트로 입력하고 실제 TTS PCM을 받았다.
이 검사의 재생 완료는 **PCM 검증 뒤 보내는 모의 확인**이며 스피커 재생 검사는 위 Unity 검사다.
각 턴 뒤 `memory.flush`로 정리 완료를 기다렸으므로 자동 정리 시점이나 긴 대화 회상 검사를 대신하지 않는다.
좋아하는 차를 둥굴레차에서 보리차로 정정하고, 강아지 이름만 삭제했을 때 보리차 기억이 유지되는지 확인했다.
모든 답변에서 유효한 음성이 생성됐으며 삭제 확인 문장도 음성으로 생성됐다.

## 이번 측정값

| 항목 | 측정 |
|---|---|
| 공개 WAV 참조 | SenseVoice 제공 `test_wavs/ko.wav`, 4.608초 |
| Unity 음성 입력의 첫 글자 | 0.295초 |
| Unity 음성 입력의 첫 음성 | 1.499초 |
| Unity 음성 입력의 생성 완료 | 2.561초 |
| 생성 음성 | 90,240 samples / 24kHz, AudioSource 출력 peak 0.409 |
| 끼어들기 입력 시작 후 재생 일시정지 | 약 0.249–0.489초 |
| 끼어들기 판정 요청 | 약 0.407–0.630초 |

첫 글자·첫 음성·생성 완료는 서버가 보고한 응답 처리 지표다.
사용자가 말한 시간, VAD 종료 무음, 네트워크 및 재생 장치 지연을 모두 합친 왕복 지연이 아니다.
소수 기능 검사에서 측정한 값이며 평균 지연이나 음성 품질 점수로 사용하지 않는다.

## 다시 실행하기

1. Unity에서 `Tools > Dialogue > Open AI test scene`을 열고 Play한다.
2. 기존 접속 설정으로 `음성 대화 서버 준비 완료 · Qwen3-TTS` 표시를 확인한다.
3. `참조 목소리 · 억양 설정`에서 본인이 사용할 WAV를 선택하고 미리 듣기·전사를 확인한다.
   별도 선택이 없으면 현재 등록 음성을 사용한다. 이번 자동 검사는 공개 WAV를 명시적으로 선택했다.
4. 마이크를 선택하고 `테스트 시작`으로 대화한다. 재생 음성이 다시 마이크에 들어오지 않도록 헤드폰을 사용한다.

자동 검사에서는 실제 마이크를 열지 않는다.

- `Tools > Dialogue > Run reference upload voice check`: 공개 참조를 업로드하고 STT부터 음성 재생까지 검사.
- `Tools > Dialogue > Run text response check`: 텍스트 질문·상황 전달·음성 재생 검사. 앞서 선택한 참조를 사용.
- `Tools > Dialogue > Run voice interruption check`: 대화가 종료된 상태에서 실행. 아래 검사 WAV 5개가 필요.

공개 참조: `.dialogue-work/models/sensevoice/test_wavs/ko.wav`.
끼어들기 입력: `.dialogue-work/semantic-fixtures/{ask,resume,revise,switch,hold}.wav`.
이 입력들은 공개 참조를 조건으로 Qwen3-TTS가 합성했으며 실제 사용자 녹음이 아니다.
다른 PC에서의 준비·문장 목록은 [AI 응답 테스트 씬](AI_응답_테스트_씬.md)을 따른다.
기존 `eval_dialogue_clarifications.py`는 TTS 없는 별도 API용이다. 이를 위해 현재 운영 TTS를 끄지 않는다.

## 근거 파일과 남은 확인

- 기본 검사: `tools/_work/checks/20260914T030148Z-dialogue-06c7b235/report.json`.
- 이번 기록: `tools/_work/tts_reconnect_20260914/`의 전후 서버 상태·설정 변경 기록·합성 입력 보고·기억 보고.
- Unity 결과 사본: 같은 폴더의 `unity/ai-reference-upload.json`, `ai-text-scene-smoke.json`, `semantic-interruption-check.json` 및 화면 PNG.
- 서버 측 검사: `~/capstone-server/checks/tts_reconnect_20260914/`.

검사 당시 브랜치는 `jw`, HEAD는 `da9b6ea`이고 기존 미커밋 대화 AI 작업을 포함한다.
Unity MCP의 임의 C# 실행은 Windows 명령 길이 오류로 실패해 기존 Editor 메뉴로 검사를 진행했다.
Console에는 검사 전부터 있던 MCP 연결 종료 오류가 남아 있었고, 검사 결과에 대화·재생 예외는 없었다.
검사 후 Play Mode를 종료하고 기존의 저장된 `Tripo_Model_Test` 씬으로 복원했다.

**Unity Editor에서 공개·합성 음성 입력을 사용한 기능 검증까지 완료했다.**
실제 Quest Pro 착용 상태의 마이크·스피커·주변 잡음·에코, 실제 등록 인물 목소리의 닮음·억양 청취,
장시간 연속 사용 및 APK 빌드는 이번 검사에 포함하지 않는다. AEC와 재부팅 자동 시작은 아직 없다.
