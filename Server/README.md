# Raon 음성 서버 (사본)

이 폴더는 **실제로 돌아가는 서버가 아니라 백업 겸 버전 관리용 사본**입니다.

실 서버는 여기서 동작합니다:

```
crc_unity@220.69.208.201:~/server        # 교내망 전용
```

Unity 클라이언트(`Assets/Scripts/Raon/`)와 짝을 이루므로 같은 리포지토리에 둡니다.
서버 쪽을 고치면 이 폴더에도 반영해 커밋해야 둘의 버전이 어긋나지 않습니다.

## 파일

| 파일 | 설명 |
|---|---|
| `app.py` | FastAPI 서버. `/talk`, `/talk_stream`, `/tts`, `/stt`, `/reset`, `/health` |
| `modeling_raon.patch` | 모델 파일(`modeling_raon.py`)에 넣은 프레임 단위 스트리밍 훅 |
| `start.sh` / `stop.sh` / `status.sh` | 기동 · 종료 · 상태 확인 |
| `*.bak` | 우리가 손대기 전 원본 |

**기본 인물도 기본 음성도 없습니다.** 인물은 웹(`Web/`)에서만 등록되고, 등록되지
않은 세션으로 들어온 요청은 **409** 로 거절합니다. 폴백을 두면 등록을 잊었을 때
오류가 아니라 엉뚱한 목소리로 답해서 코드 결함처럼 보입니다 — 실제로 한 번 겪었습니다.

## 환경변수

**두 가지가 섞이지 않게 나눠 적는다.** `start.sh` 가 직접 설정하는 것과, 설정하지 않아서
`app.py` 의 기본값이 그대로 쓰이는 것은 다르다. 뒤엣것을 바꾸려면 앞에 붙여 띄운다
(`RAON_MAX_TURNS=20 ./start.sh`).

### `start.sh` 가 설정하는 것

| 변수 | 값 | 설명 |
|---|---|---|
| `RAON_VERIFY` | `0` | 합성 검증. 끄면 약 0.5초 빨라지고 20회 중 1회 미만으로 품질 불량이 통과 |
| `RAON_CONT` | `1` | 참조의 억양·속도까지 복제(`tts_continuation`). 참조가 10초 미만이면 깨지므로 그럴 땐 `0` |
| `RAON_MEM_FRACTION` | `0.70` | 이 프로세스가 쓸 GPU 비율(=66.9GB). `0.60`(57.4GB)은 긴 대화에서 넘쳤다 |
| `RAON_TEMP` | `1.6` | **소리** 온도. 코드 기본값 `1.2` 면 「국어책 읽는 느낌」이 남는다 |
| `RAON_CONT_SILENCE` | 비움 | 생성 초반 무음 프레임. 비우면 모델 기본값 `2`. **`0` 이면 참조가 통째로 샌다** |
| `RAON_TOKEN` | — | 클라이언트(Unity·`Web/app.py`)의 값과 같아야 한다. 다르면 `401` |

### 코드 기본값 — `start.sh` 에 없다

| 변수 | 기본 | 설명 |
|---|---|---|
| `RAON_COMPILE` | `1` | `code_predictor`에 `torch.compile`. 약 0.66초 단축 |
| `RAON_FRAME_CHUNK` | `8` | 스트리밍 전송 단위(프레임). 8이면 0.64초 분량 |
| `RAON_ANSWER_TOKENS` | `200` | 답변 최대 토큰 |
| `RAON_MAX_TURNS` / `_KEEP_TURNS` | `30` / `3` | 유지할 대화 턴 수. **함부로 올리지 말 것** — 54턴에 60.8GB, 70턴에 OOM |
| `RAON_CHAT_TEMP` | `0.7` | **답변 글** 온도. 위 `RAON_TEMP`(소리)와 다른 것 |
| `RAON_JUDGE` / `_TURNS` | `1` / `3` | 답을 뽑기 전에 아는 것인지 먼저 묻는다 |
| `RAON_LEARN` / `_CHARS` / `_MIN` | `1` / `600` / `8` | 사용자가 말한 사실을 적립 |
| `RAON_SUMMARY` / `_CHARS` | `0` / `600` | 요약. **켜지 말 것** — 기억이 62/64 → 50/64 로 무너진다 |
| `RAON_REGEN_SIM` | `0.6` | 앞 답변과 꼬리가 이만큼 닮으면 다시 뽑는다 |
| `RAON_RAS` / `_WINDOW` / `_THRESHOLD` | `1` / `100` / `0.35` | 반복 루프 억제. 실측 최적값이 곧 코드 기본값 |
| `RAON_REF_TEXT` | `1` | 참조 음성 전사를 `tts_continuation` 에 넘긴다. `0` 은 빈 글을 넘기는 시험용 |

값의 근거는 [`docs/음성대화_작업현황.md`](../docs/음성대화_작업현황.md) §5.

## 서버를 새로 셋업하거나 복구할 때

```bash
# 1. 이 폴더 내용을 ~/server 로 복사
# 2. 모델 파일에 스트리밍 훅 재적용
patch ~/models/AX-K2-Raon-Speech/modeling_raon.py < ~/server/modeling_raon.patch
rm -rf ~/hf_home/modules/transformers_modules/AX_hyphen_K2_hyphen_Raon_hyphen_Speech
# 3. 기동
cd ~/server && ./start.sh
```

2번의 캐시 삭제가 중요합니다. transformers는 `trust_remote_code` 파일을
`hf_home/modules/` 로 복사해서 쓰기 때문에, 캐시를 지워야 수정본이 다시 복사됩니다.

## 주의사항

- **`mode="reduce-overhead"`를 `torch.compile`에 쓰지 말 것.** 세그폴트로 서버가 죽습니다.
  CUDA 그래프는 transformers 캐시의 in-place 변형 때문에 어차피 비활성화됩니다.
- `./start.sh`를 `tail -f` 같은 포그라운드 명령과 `&&`로 묶지 말 것.
  Ctrl-C 시 SIGINT가 서버까지 죽입니다. 두 명령을 따로 실행하세요.
- 모델 라이선스는 **CC BY-NC 4.0** — 비상업적 용도만 가능하며,
  실존 인물의 목소리를 참조 음성으로 쓰려면 사전 동의가 필요합니다.

전체 명세는 [`docs/RAON_UNITY_구현명세.md`](../docs/RAON_UNITY_구현명세.md) 참고.
