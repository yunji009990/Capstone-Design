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
| `app.py` | FastAPI 서버. `/talk`, `/talk_stream`, `/tts`, `/stt`, `/reset`, `/reload`, `/health` |
| `persona.md` | 캐릭터 설정. 통째로 시스템 프롬프트에 들어감. `/reload`로 재시작 없이 반영 |
| `voice.wav` | 음성 복제용 참조 음성. 교체하려면 **서버 재시작 필요** |
| `modeling_raon.patch` | 모델 파일(`modeling_raon.py`)에 넣은 프레임 단위 스트리밍 훅 |
| `start.sh` / `stop.sh` / `status.sh` | 기동 · 종료 · 상태 확인 |
| `*.bak` | 우리가 손대기 전 원본 |

## 환경변수 (`start.sh`에 설정)

| 변수 | 기본 | 설명 |
|---|---|---|
| `RAON_VERIFY` | `0` | 합성 검증. 끄면 약 0.5초 빨라지고 20회 중 1회 미만으로 품질 불량이 통과 |
| `RAON_COMPILE` | `1` | `code_predictor`에 `torch.compile`. 약 0.66초 단축 |
| `RAON_FRAME_CHUNK` | `8` | 스트리밍 전송 단위(프레임). 8이면 0.64초 분량 |
| `RAON_ANSWER_TOKENS` | `200` | 답변 최대 토큰 |
| `RAON_MAX_TURNS` | `6` | 유지할 대화 턴 수 |

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
