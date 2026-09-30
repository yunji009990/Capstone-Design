# 이전 화자 분리 엔진 (NeMo MSDD, 보존 코드)

2026-09-29 확인: **현재 등록 웹은 이 엔진을 호출하지 않는다.** 9월 17일 단일 화자 파일 등록으로
정리하면서 `/extract` 경로를 제거했다. 현재 사용법은 [등록 웹](../README.md), 참조 음성 선별은
[VoxCPM2 인계](../../docs/TTS_작업인계_20260918.md) 7장을 따른다.

아래는 영상·음성에서 사람별로 참조 음성을 만들던 이전 구조와 별도 CLI의 기록이다.

원래는 바탕화면 `voice_clone_studio` 에 있었고 `VCS_DIR` 로 가리켰다. 그 PC 에서만
돌아서 **코드를 저장소로 들여왔고, 원본 18GB 는 삭제했다.** 알고리즘은 그대로다 —
새로 만들지 않았다. 그쪽 설계 문서는 [`docs/voice_clone_studio/`](../../docs/voice_clone_studio/)
에 보존돼 있다.

## 구성

| 파일 | 역할 |
|---|---|
| `extract_runner.py` | 이전 웹의 서브프로세스 진입점. 현재는 보존된 별도 CLI |
| `extract_nemo.py` | 화자별 참조 조립 — 깨끗한 조각을 골라 이어붙여 7~12초로 만든다 |
| `extract_speaker_ref.py` | 공통 도구 — 디코딩, SNR 측정, 클립 저장 |
| `nemo_diarize.py` | NeMo NeuralDiarizer 러너. **`nemo_env` 에서 따로 실행된다** |
| `nemo_conf/diar_infer_telephonic.yaml` | NeMo 추론 설정 |

## 이전 실행 구조와 환경

NeMo 는 의존성이 무거워 웹 백엔드와 같은 환경에 두면 충돌한다. 그래서
`nemo_diarize.py` 만 **격리된 `nemo_env`** 에서 subprocess 로 돌린다.

```
이전 Web/app.py (메인 환경, 현재 연결 제거)
  └─ subprocess → extract_runner.py → extract_nemo.py
       └─ subprocess → nemo_env/Scripts/python.exe nemo_diarize.py
```

`nemo_env`는 Git에 포함되지 않는다. 현재 PC에 환경·모델이 준비돼 있는지는 이번 문서 점검에서
검증하지 않았다. **현재 웹 설치에는 NeMo 환경이 필요하지 않다.**

## 별도 CLI를 복원할 때 참고할 이전 설치 절차

당시 검증 조합은 Python 3.10.11 · `nemo_toolkit` 2.7.3 · `torch` 2.13.0+cpu였다.
아래 명령은 버전을 고정하지 않으므로 당시 환경을 그대로 재현한다는 보장은 없다.

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

venv를 복사하면 원래 Python·설치 경로에 대한 참조가 남을 수 있다. 새 PC에서 그대로
동작한다고 가정하지 않고, 사용할 Python과 의존성으로 환경을 다시 준비한다.

## ffmpeg

`PATH` 에 있어야 한다. 디코딩과 24kHz 모노 변환에 쓴다.

## 메인 환경에 필요한 것

별도 추출 CLI의 의존성은 `numpy` · `soundfile` · `librosa`다. 현재 웹 환경에 모두
설치돼 있다고 가정하지 않는다.

## 당시 성능 기록

당시 **CPU 실행** 실측은 **63~99초**였다(3~10MB 입력, 6분 24초 오디오가 66초).

이전 웹은 2단계 업로드 뒤 분리를 시작해 설문 작성과 겹쳐 실행했다. 현재 웹은 이 과정을
수행하지 않으므로 위 시간을 현재 등록 지연으로 해석하지 않는다.

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

**분리기가 만든 참조는 여러 조각을 이어붙인 것이다.** 현재 VoxCPM2의 참조 선별 기준은
깨끗한 단일 화자의 연속 구간이다. 이 엔진의 출력이 그 기준을 만족한다고 가정하지 않는다.
`RAON_CONT`와 이전 웹의 화자 수 선택은 폐기된 흐름이며 현재 운영에 적용하지 않는다.
