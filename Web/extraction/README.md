# 화자 분리 엔진 (NeMo MSDD)

영상·음성에서 **사람별로 나눠 참조 음성을 만든다.** `Web/app.py` 가 서브프로세스로 부른다.

원래는 바탕화면 `voice_clone_studio` 에 있었고 `VCS_DIR` 로 가리켰다. 그 PC 에서만
돌아서 **코드를 저장소로 들여왔고, 원본 18GB 는 삭제했다.** 알고리즘은 그대로다 —
새로 만들지 않았다. 그쪽 설계 문서는 [`docs/voice_clone_studio/`](../../docs/voice_clone_studio/)
에 보존돼 있다.

## 구성

| 파일 | 역할 |
|---|---|
| `extract_runner.py` | `Web/app.py` 가 부르는 진입점. CLI 로 받아 아래를 호출한다 |
| `extract_nemo.py` | 화자별 참조 조립 — 깨끗한 조각을 골라 이어붙여 7~12초로 만든다 |
| `extract_speaker_ref.py` | 공통 도구 — 디코딩, SNR 측정, 클립 저장 |
| `nemo_diarize.py` | NeMo NeuralDiarizer 러너. **`nemo_env` 에서 따로 실행된다** |
| `nemo_conf/diar_infer_telephonic.yaml` | NeMo 추론 설정 |

## 왜 환경이 둘인가

NeMo 는 의존성이 무거워 웹 백엔드와 같은 환경에 두면 충돌한다. 그래서
`nemo_diarize.py` 만 **격리된 `nemo_env`** 에서 subprocess 로 돌린다.

```
Web/app.py (메인 환경)
  └─ subprocess → extract_runner.py → extract_nemo.py
       └─ subprocess → nemo_env/Scripts/python.exe nemo_diarize.py
```

`nemo_env` 는 **1.8GB 라 저장소에 올라가지 않는다**(`.gitignore`). 다만 이 PC 에는
`extraction/nemo_env/` 에 실제로 놓여 있어 **환경변수 없이 그대로 돈다.** 저장소를
새로 받은 PC 에서는 아래대로 한 번 만들어야 한다.

## nemo_env 만들기

파이썬 **3.10** 이 필요하다 (검증된 조합: 3.10.11 · `nemo_toolkit` 2.7.3 · `torch` 2.13.0+cpu).

```bash
cd Web/extraction
python -m venv nemo_env
./nemo_env/Scripts/python.exe -m pip install --upgrade pip
./nemo_env/Scripts/python.exe -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
./nemo_env/Scripts/python.exe -m pip install "nemo_toolkit[asr]"
```

모델 가중치는 **최초 1회 자동으로 받아** 캐시한다. 그 뒤로는 오프라인으로 돈다.

### 다른 곳에 있는 것을 쓰려면

이미 만들어 둔 환경이 있으면 복사 대신 가리켜도 된다.

```bash
set NEMO_PY=C:\...\nemo_env\Scripts\python.exe
```

venv 는 `python.exe` 옆의 `pyvenv.cfg` 를 보고 자기 위치를 잡으므로 **폴더째 옮기거나
복사해도 동작한다.** 단 `Scripts\pip.exe` 같은 진입점 exe 에는 원래 경로가 박혀 있으니,
옮긴 환경에 패키지를 더 넣을 때는 `nemo_env\Scripts\python.exe -m pip` 로 부를 것.

## ffmpeg

`PATH` 에 있어야 한다. 디코딩과 24kHz 모노 변환에 쓴다.

## 메인 환경에 필요한 것

`numpy` · `soundfile` · `librosa` — `Web/app.py` 가 이미 쓰는 것들이라 따로 넣을 게 없다.

## 성능

**CPU 전용이다.** 실측 **63~99초** (3~10MB 입력, 6분 24초 오디오가 66초 — 길이의 약 1/6).

느리지만 문제가 안 되는 이유는 **순서** 때문이다. 웹이 목소리를 2단계에서 받아 뒤에서
분리를 돌리고, 참여자가 3~6단계 설문을 채우는 동안 끝난다. GPU 로 옮겨 아낄 시간이
이미 설문에 가려져 있어 **지금은 CPU 로 둔다.**

## 결과물

`--out-dir` 에 이렇게 쓴다.

```
result.json           화자 목록과 참조 파일 경로
full.mp3              원본 전체
talker1/ref.wav       화자 1 의 참조 (24kHz 모노)
talker1/seg01.wav …   조각들
_nemo/                NeMo 중간 산출물 (diar.json, RTTM, 임베딩)
```

`diar.json` 이 있으면 재실행 시 재사용한다.

## 주의

**분리기가 만든 참조는 여러 조각을 이어붙인 것이다.** 음색만 쓰는 `tts()` 에는 문제가
없지만, 억양까지 잇는 `tts_continuation`(`RAON_CONT=1`) 에는 맞지 않는다. 억양을
복제하려면 웹에서 **"한 명 (분리 안 함)"** 을 골라 한 사람이 10~30초 자연스럽게 말하는
음성을 그대로 넘겨야 한다. 자세한 것은 [`Web/README.md`](../README.md).
