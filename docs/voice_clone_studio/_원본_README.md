# Voice Clone Studio

다화자 오디오에서 **화자 목소리를 준비**하고(사전준비), **마이크로 대화**하는(테스트) 음성 대화 웹앱.
백엔드/프론트엔드가 분리돼 있고, 백엔드는 오디오를 받아 **STT → LLM → TTS**로 처리해 답변 음성을 돌려준다.

**self-contained** — 무거운 ML 런타임(NeMo 추출 + Qwen3-TTS)을 `engine/` 안에 모두 포함.
(원래 `Audio extraction` · `tts-benchmark` 두 프로젝트에서 필요한 부분만 이동. 제거된 기능은 [docs/제거된_기능.md](docs/제거된_기능.md).)

## 두 개의 탭 (프론트엔드)

**① 사전준비** — raw 오디오 + LLM 사전지식 프롬프트 입력
```
오디오 업로드 → NeMo 화자 분리(시각화) → 화자 선택 → 이 목소리로 준비 완료
```
선택한 화자의 reference를 1회 전사해 세션에 등록한다(zero-shot: 학습 없이 추론 시 ref만 사용).

**② 테스트** — 준비된 목소리로 대화 (대화 모드)
```
🎧 대화 모드 켜기 → (버튼 조작 없이) 말하면 자동 감지 → 답변 자동 재생 → 다시 듣기 …
```
- 마이크를 16k PCM 으로 백엔드에 계속 흘리고, **말/침묵 + 문장 완결**을 보고 발화 종료를 빨리 잡는다(스트리밍 엔드포인팅).
- 말하는 동안 **실시간 마이크 레벨 미터**(막대 시각화 + 말하는 중 표시)로 상태를 보여준다.
- 답변 재생 중엔 마이크를 멈춰 에코를 막고, 재생이 끝나면 자동으로 다시 듣는다.
- (푸시투토크 "눌러서 말하기"는 제거됨 — 대화 모드로 일원화.)

## 구조 (backend / frontend 분리)

```
voice_clone_studio/
├── backend/                 # 순수 API 서버 (Flask)
│   ├── app.py               #   /api/extract, /api/prepare, /api/turn + 정적/파일 서빙 + CORS
│   ├── config.json          #   engine 경로·인터프리터·LLM 설정
│   └── runners/
│       ├── extract_runner.py #   NeMo 추출 (cwd=engine/extraction)
│       ├── synth_runner.py   #   Qwen3 합성 (engine/tts)
│       └── stt_runner.py     #   whisper 전사 (eval_env)
├── frontend/                # 정적 UI (백엔드와 분리, CORS로 별도 호스팅도 가능)
│   ├── index.html           #   사전준비 / 테스트 탭
│   ├── app.js
│   └── styles.css
├── engine/                  # 자체 포함 런타임
│   ├── extraction/          #   NeMo MSDD (nemo_env, nemo_conf)
│   └── tts/                 #   Qwen3 (adapters/qwen3tts, eval_env, pipeline)
└── docs/                    # 설계·아키텍처·제거된 기능·원본 아카이브
```

## 파이프라인 (backend 내부)

| 단계 | 실행 | 인터프리터/서비스 |
|---|---|---|
| 화자 추출 | `extract_runner.py` 서브프로세스 (cwd=engine/extraction) | ambient `python` + `nemo_env` |
| STT | **웜 워커**(상주 프로세스, `stt_worker.py`) — 서버 시작 시 1회 로드 | `engine/tts/eval_env` |
| LLM | `app.py`에서 **스트리밍** HTTP 호출(문장 단위) | 로컬 **Ollama** (`gemma3:4b` 기본) |
| TTS | **웜 워커**(상주 프로세스, `tts_worker.py`) — 문장별 즉시 합성 | `engine/tts/adapters/qwen3tts/env` |

### 스트리밍 엔드포인팅 (대화 모드, `/api/listen/*`)
- 마이크 오디오를 16k PCM 청크로 계속 받아 **에너지로 말/침묵을 추적**(짧은 pause 후보).
- pause 후보에서 **그 시점까지 STT** → 문장이 **완결이면 즉시 종료**, 한국어 **연결어미**(…고/…서/…는데 등)로
  끝나면 **긴 pause 까지 대기**(semantic — 말을 중간에 자르지 않음).
- 임계값은 `app.py` 상단 상수: `VOICE_RMS`(발화 판정), `SHORT_PAUSE`(0.35s), `LONG_PAUSE`(1.0s),
  `MIN_SPEECH`, `MAX_UTT`. 잡음 환경이면 `VOICE_RMS`·pause 를 올려 조정.
- 종료 감지: 완결 문장 ~0.35s / 미완결 ~1.0s (침묵만 기다리는 방식보다 빠르고 덜 자름).

### 문장 스트리밍 (테스트 턴)
- LLM 을 **스트리밍**으로 받아 문장이 끝날 때마다 즉시 TTS → 청크(wav)를 준비되는 대로 내보낸다.
- 프론트는 도착한 청크를 **순서대로 큐 재생**(먼저 온 문장부터 재생, 뒷문장은 재생 중 생성).
- 효과: **첫 소리까지 체감 지연** ~20s → **~6s**(짧은 답변 기준). 답변 텍스트도 자라나며 표시.
- 트레이드오프: Qwen3TTS 는 실시간보다 느려(RTF~1.9) **답변이 아주 길면** 문장 사이 공백이 생길 수 있음. 기본 시스템 프롬프트가 한두 문장으로 유지하므로 보통은 끊김 없음.

### 웜 워커 (STT · TTS 상주)
- 서버 시작 시 STT·TTS 모델을 백그라운드로 **1회 예열**(TTS ~8s, STT ~10s). 이후 요청은 로드 없이 즉시.
- 워커는 localhost 소켓으로 작업을 받고, 준비상태는 status 파일로 통지(파이프 데드락 회피).
- GPU 연산은 앱 레벨 `GPU_LOCK`으로 직렬화 → 두 모델이 동시 상주해도 경합 없이 메모리만 공유.
- 시작 시 고아 워커 자동 정리(`_kill_stale_workers`) → GPU 누수 방지.
- 코드: `warm.py`, `workers/stt_worker.py`, `workers/tts_worker.py`.

## 실행

전제: ambient `python`에 flask, `ffmpeg`가 PATH, **Ollama 실행 중**(테스트 탭 LLM용).

```bash
cd voice_clone_studio/backend
python app.py
# 브라우저에서 http://127.0.0.1:5001
```

## API (프론트엔드가 호출)
- `POST /api/extract` (multipart audio, n_speakers) → `{job_id}` · `GET /api/extract/<job>` 폴링 → 화자별 ref + 시각화 데이터
- `POST /api/prepare` (json `{job, ref_file, system_prompt}`) → `{prep_id}` · `GET /api/prepare/<id>` → `{session_id, ref_text}`
- 대화 모드(스트리밍 엔드포인팅): `POST /api/listen/start` `{session_id}` → `{stream_id}` · `POST /api/listen/<sid>/push`(raw int16 16k PCM 청크) · `GET /api/listen/<sid>/state` → `{state, partial, speech, turn_id}` · `POST /api/listen/<sid>/cancel`
- 발화 종료가 감지되면 `turn_id` 가 생기고, `GET /api/turn/<id>` 폴링 → `{transcript, reply(누적), chunks:[{idx,text,audio_url}], timing}` (문장 청크가 준비되는 대로 append) 로 스트리밍 재생.
- `GET /files/<path>` 산출물(ref·답변 wav) 서빙 · `GET /config`

## 설정 (`backend/config.json`)
- `llm.model` — Ollama 모델. 기본 `gemma3:4b`. (Korean 강한 `exaone3.5:7.8b` 등으로 교체 가능)
- `llm.default_system_prompt` — 사전준비에서 프롬프트를 비우면 이게 쓰임
- 추출 엔진 NeMo·TTS Qwen3-TTS만 유지 (다른 기능 제거, [docs/제거된_기능.md](docs/제거된_기능.md))

## 성능 (RTX 5060 Ti)
- **미리듣기(TTS만)**: 웜 상태 **~6s/문장** (콜드 ~40s 대비).
- **대화 한 턴(문장 스트리밍)**: STT ~1s → **첫 소리 ~6s**(첫 문장 재생 시작). 나머지 문장은 재생 중 이어서 생성.
- 서버 부팅 시 STT·TTS·LLM 모두 예열(백그라운드). 사용자가 오디오 올리는 동안 예열 완료됨.
- 추가 최적화 여지: whisper turbo(STT 정확도↓ 속도↑), 더 빠른 TTS(RTF<1 이면 긴 답변도 무공백).
- 상세 설계·트레이드오프: [docs/음성대화_아키텍처_비교.md](docs/음성대화_아키텍처_비교.md)

## 주의
- 무거운 작업(추출·턴)은 GPU 경합 방지를 위해 **한 번에 한 건**만 처리.
- 산출물은 `backend/workspace/` 안에만 저장(자동 생성, 비워도 됨).
- 대화 모드는 브라우저 Web Audio 로 마이크를 16k PCM 청크로 만들어 백엔드에 스트리밍(엔드포인팅).

## 문서
- [docs/파이프라인_구조.md](docs/파이프라인_구조.md) — **전체 파이프라인 구조**(런타임·사전준비·대화 루프·API·파일맵)
- [docs/음성대화_아키텍처_비교.md](docs/음성대화_아키텍처_비교.md) — 캐스케이드 STT→LLM→TTS vs S2S
- [docs/제거된_기능.md](docs/제거된_기능.md) — 제거된 ECAPA·TTS 모델 + 원본 아카이브
