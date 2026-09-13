# 다시, 봄 — 서버

대화 AI 담당의 시작 문서는 [대화 AI 개발 가이드](../docs/대화_AI_개발가이드.md)다.
기본 모의 검사는 `python tools/check.py --area dialogue`, 검사 전용 의존성은 `requirements-test.txt`다.
검사 환경 선택과 결과 파일은 [공통 하네스](../docs/AI_하네스.md)를 따른다.
등록·테스트 인물의 공통 규칙은 `gemma4_dialogue_v1`이다.
[Gemma 4 프롬프트·비교 결과](../docs/Gemma4_대화프롬프트.md)에 변경 이유, 적용 경로, 재실행 방법이 있다.

2026-09-12 웹·등록·Tripo와 대화 AI의 실행 환경·배포 경로·인물 정보 접근을 분리했다.
현재 운영 명령과 검증 결과는 [웹·Tripo·대화 AI 서비스 분리](../docs/웹_Tripo_대화AI_서비스_분리.md)를 참고한다.

2026-09-10부터 등록·대화·LLM·TTS를 별도 프로세스로 실행한다. Raon은 제거했다.
2026-09-11 현재 TTS는 비활성화·프로세스 종료 상태이며 텍스트 답변으로 판정기를 검증한다.
[텍스트 검사 결과·재실행](../docs/판정기_텍스트_검사.md)을 참고한다.
전체 설계는 [서버 음성 대화 구조](../docs/서버_음성대화_구조.md),
과거 실험은 [Raon 작업 이력](../docs/Raon_작업이력_보관.md)을 참고한다.

| 포트 | 서비스 | 환경 | 역할 |
|---|---|---|---|
| 8000 | session | `~/venv/registration` | 웹 영역의 CPU 인물·참조 WAV·3D 자료 관리 |
| 8001 (loopback) | vLLM | `~/venv/vllm` | 기존 Gemma, API 이름 `exaone` |
| 8002 | dialogue | `~/venv/dialogue` | CPU SenseVoiceSmall·VAD·상황·일반/추론 분류·중단 |
| 8003 (loopback, 현재 중지) | tts | `~/venv/qwentts` | 선택 기능: Qwen3-TTS 1.7B Base / 참조 목소리·말투 복제 |
| 8500 | web | `~/venv/web` | 등록 웹, 코드 위치 `~/webapp` |
| 없음 | tripo | `~/venv/tripo` | 별도 CPU 작업자, SQLite 작업 복구·Tripo 요청·GLB 전달 |

웹·Tripo 실행 코드: `~/webapp`. 등록 API: `~/webapp/Server/registration`.
대화 AI 실행 코드: `~/capstone-server`. 인물 원본: `~/server/sessions`. STT: `~/dialogue-models/sensevoice`.
대화 AI는 인물 원본 폴더를 직접 읽지 않고 등록 API를 조회한다.
Qwen 가중치는 Hugging Face 캐시에 있다. TTS가 Gemma 환경을 변경하거나 중복 로드하지 않는다.
서버는 공용 GPU이므로 다른 사용자의 프로세스를 종료하면 안 된다.

## 시작·종료·상태

각 서비스의 `.env.example`을 `.env`로 복사해 설정한다. 토큰은 Unity와 일치시키며 파일 권한은 600으로 둔다.
TTS worker 토큰은 `tts.env`의 `TTS_TOKEN`과 `dialogue.env`의 `DIALOGUE_TTS_TOKEN`을 맞춘다.
네트워크에 노출되는 두 서비스는 토큰이 없으면 시작 스크립트가 실행을 거부한다.

```bash
ssh raon bash /home/crc_unity/capstone-server/service.sh session start
ssh raon bash /home/crc_unity/capstone-server/service.sh dialogue start
ssh raon bash /home/crc_unity/capstone-server/service.sh dialogue status
ssh raon bash /home/crc_unity/capstone-server/service.sh dialogue stop
```

신규 운영은 웹 영역의 `~/webapp/Server/platform.sh {web|session|tripo} {start|stop|status}`와
대화 영역의 `~/capstone-server/dialogue.sh {api|tts} {start|stop|status}`를 사용한다.
기존 `service.sh session`은 `service-paths.env`를 통해 웹 영역으로 연결한다.
등록 설정은 `~/webapp/Server/session.env`, 대화 설정은 `~/capstone-server/dialogue.env`에 있다.
`PERSONA_READ_TOKEN`과 `DIALOGUE_PERSONA_TOKEN`은 인물 조회 전용이며 Unity 접속 토큰과 별개다.

Gemma는 기존 8001 프로세스를 사용한다. 꺼진 경우 `vllm_start.sh`를 별도로 실행한다.
`start.sh`, `stop.sh`, `status.sh`는 CPU 등록 서비스의 호환 진입점이다.
로그는 `session.log`, `dialogue.log`, `tts.log`. PID 파일의 명령행을 확인한 프로세스만 종료한다.
재부팅 자동 시작은 설정하지 않았다.

TTS는 `DIALOGUE_TTS_URL=`이면 사용하지 않는다. 다시 사용하려면 URL을 `http://127.0.0.1:8003`으로
설정하고 `service.sh tts start` 후 dialogue를 재시작한다. 두 서비스의 TTS 토큰은 일치해야 한다.

`http://220.69.208.201:8002/health`의 `status=ready`, `llm_ready=true`를 확인한다.
현재는 `mode=streaming_text`, `tts=false`다. 음성을 켜면 `mode=streaming_voice`, `tts_ready=true`여야 한다.
`reasoning_ready`는 추론 경로 상태다.
`prompt_version=gemma4_dialogue_v1`은 현재 적용한 공통 답변 규칙이다.
의미 기반 끼어들기 활성화는 `interruption_policy=semantic_v1`로 확인한다.
등록 API만 살아 있는 것을 음성 대화 준비 완료로 판단하지 않는다.

## 설치·배포

등록 API는 `requirements-registration.txt`, Tripo 작업자는 `Survey/requirements-worker.txt`,
대화 CPU 서비스는 `requirements-dialogue.txt`, TTS는 `requirements-tts.txt`를 사용한다.
서버 검증 환경은 Python 3.12, TTS의 torch는 2.8.0+cu128이다.
기존 vLLM 환경에 TTS 의존성을 설치하지 않는다. TTS의 `sdpa` 실행은 flash-attn 추가 설치를 요구하지 않는다.
SenseVoice 모델 설치는 `setup_dialogue_models.py --help`를 참고한다.

```powershell
python tools/service_bundle.py platform
python tools/service_bundle.py dialogue
```

영역별 명시된 파일만 묶는다. platform 번들의 대상은 `~/webapp`, dialogue 번들의 대상은
`~/capstone-server`다. 배포 전 현재 파일과 해시를 대조하고 백업한 뒤 해당 영역만 적용한다.
전체 `Server/*.py`를 일괄 복사하는 방식은 사용하지 않는다. 환경 파일·자료·모델은 번들에 포함하지 않는다.

코드 복사 후 해당 서비스만 종료·시작하고 `/health`와 로그를 확인한다.
배포한 파일은 SHA256으로 로컬과 대조한다. `.env`, 음성, 세션, 로그는 커밋하지 않는다.
다른 작업자의 수정이 있을 때 파일 전체를 덮어쓰지 말고 차이를 먼저 확인한다.

## API 호환과 변경

`/session/start`, `/session/current`, `/session/{sid}/model`, `/session/{sid}/model.glb`,
`/session/end`는 8000에 유지한다. 등록은 GPU 예열·참조 재합성을 호출하지 않는다.
8000의 구형 `/talk`, `/talk_stream`, `/chat`, `/tts`, `/stt`, `/reset`, `/mode`, `/dbg`는 폐기했다.
현재 대화·초기화·끼어들기는 8002 WebSocket으로 처리한다.
첫 `start` 메시지에 `interruption_policy="semantic_v1"`을 보내면 TTS 없이도 생성 중인 답변을 보류·판정한다.
음성 출력 연결에서는 이 기능을 지원하는 최신 Unity가 필수다.
발화 감지 시 답변을 보관하고 전사 후 기존 일반 LLM이 재개·수정·전환·대기·확인 질문을 판단한다.
텍스트 입력 검사는 STT 이후 입력을 직접 구성한다. 판정기에는 미전달 초안을 넣지 않는다.
규칙·출력 검증은 `interruption_policy.py`, HTTP 호출은 `realtime_llm.py`, 보류 상태는 `realtime_dialogue.py`에 있다.
추가 모델 로드 없이 새 답변의 일반/추론 경로도 함께 고른다. 보류 최대 120초, 판정 최대 6.5초다.
판정 중 다음 TTS 구절을 보류하지만 이미 진행 중인 GPU 계산을 일시정지하는 기능은 아니다.
Web은 `SESSION_URL`, `SESSION_TOKEN`을 사용하고 하드코딩된 접속 토큰은 제거했다.

TTS는 Base 모델의 ICL 모드로 참조 음성 코드·화자 임베딩·전사를 함께 사용한다.
등록 체험은 해당 세션의 `voice.wav`를 자동으로 읽는다. 테스트 씬은 등록 음성 또는 임시 WAV를 선택한다.
`POST /references`(multipart `voice`, 생략하면 현재 등록 음성), `GET /references/{id}/audio.wav`,
`DELETE /references/{id}`는 8002의 인증된 참조 API다. 업로드는 테스트 모드에서만 허용한다.
테스트 WebSocket `start`에 `reference_id`, `reference_text`를 전달하면 정확한 구간의 전사를 수정할 수 있다.
참조는 3–12초 연속 구간으로 제한하고 그 구간만 SenseVoice로 전사한다. 원본은 변경하지 않는다.
GPU 참조 프롬프트는 연결마다 한 번 만들고 구절마다 재사용하며 연결 종료 시 해제한다.
임시 WAV는 메모리에 최대 8개, 30분 미사용 후 만료된다. 끊긴 준비 요청의 GPU 캐시는 최대 6시간 후 정리한다.

현재 TTS는 문장별 합성 후 PCM 스트리밍이다. 모델 내부 토큰 단위 디코딩은 아직 없다.
억양·운율은 참조를 조건으로 생성하며 문장별 높낮이와 길이를 동일하게 재현하는 기능은 아니다.
`AI_Response_Test`에서는 키보드 질문과 마이크 입력을 각각 검사할 수 있다.
키보드 질문은 STT를 거치지 않는다. 참조 전사 입력은 목소리 설정에 사용한다.
사용법·검증: [AI 테스트 씬](../docs/AI_응답_테스트_씬.md).
