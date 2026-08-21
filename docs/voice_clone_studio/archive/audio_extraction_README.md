# 화자 분리 · Reference 추출

여러 사람이 섞인 오디오를 **화자별로 분리**해 각 화자의 발화를 듣고,
음성 생성(voice cloning) 모델에 넣을 **깨끗한 7~12초 reference 클립**을 뽑는 도구.

## 구성

- `extract_nemo.py` — **NeMo MSDD 엔진**(정밀): 다이어리제이션 + 깨끗한 조각 조합
- `nemo_diarize.py` — NeMo NeuralDiarizer 러너(격리된 `nemo_env` 에서 subprocess 로 실행)
- `extract_speaker_ref.py` — **ECAPA 엔진**(빠름): VAD+ECAPA+KMeans 파이프라인(CLI 겸용)
- `app.py` — 로컬 웹앱(Flask), 엔진 선택 지원
- `web/index.html` — 웹 UI (업로드 · 단계 시각화 · 화자별 재생/다운로드)
- `nemo_env/` — NeMo 전용 가상환경(메인 환경과 의존성 격리)
- `nemo_conf/` — NeMo 추론 설정
- `output/`, `webdata/` — 실행 결과(자동 생성)

## 두 가지 엔진

| 엔진 | 방식 | 특징 |
|------|------|------|
| **NeMo MSDD** (기본, 정밀) | VAD(MarbleNet) + 다중스케일 TitaNet 임베딩 + 군집 + MSDD 신경 디코더 | **동시발화(overlap) 검출**. 다른 화자·overlap·클리핑 없는 **깨끗한 조각만 모아 이어붙여** 7~12초 구성. 오디오 길이의 ~1/4 시간 |
| **ECAPA** (빠름) | Silero VAD + ECAPA(SpeechBrain) 임베딩 + KMeans | 화자별 연속 단일화자 구간에서 7~12초 클립. 가볍고 빠름 |

두 엔진 모두 **로컬/오프라인** 동작(모델 가중치만 최초 1회 다운로드 후 캐시).

## NeMo MSDD 엔진 흐름

1. `nemo_diarize.py` 를 `nemo_env` 에서 실행 → 프레임 단위 화자 구간 + overlap (RTTM → `diar.json`)
2. 각 화자의 **다른 화자·overlap 이 전혀 없는** 구간만 추림
3. **무클리핑·적정 SNR** 조각만 남김 (깨끗한 조각)
4. 깨끗한 조각을 골라 페이드+짧은 침묵으로 **이어붙여 7~12초** reference 구성
5. 24kHz 모노 WAV 저장

## 실행

### 웹앱
```bash
pip install -r requirements.txt   # ffmpeg 은 별도로 PATH 에 있어야 함
python app.py
# 브라우저에서 http://127.0.0.1:5000
```
파일 업로드 → **엔진 선택**(NeMo/ECAPA) → 화자 수(알면 지정) → **단계별 결과를 보고 들으며** 화자별 클립 재생·다운로드.

웹 UI 단계 시각화: 입력(파형) → 발화 구간 → 화자 분류(색) → 동시발화(overlap) → 최종 reference.

### CLI
```bash
# NeMo 엔진
python extract_nemo.py --input test_audio.mp3 --out-dir output_nemo --speakers 2
# ECAPA 엔진
python extract_speaker_ref.py --input test_audio.mp3 --out-dir output --speakers 2
```
`--speakers` 생략 시 자동 추정.

## NeMo 설치(최초 1회)

메인 환경 의존성과 충돌을 막기 위해 **별도 venv** 사용:
```bash
python -m venv nemo_env
./nemo_env/Scripts/python.exe -m pip install --upgrade pip
./nemo_env/Scripts/python.exe -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
./nemo_env/Scripts/python.exe -m pip install "nemo_toolkit[asr]"
```

## 참고

- CPU 추론. NeMo 는 오디오 길이의 ~1/4 시간(15분 → 약 1분), ECAPA 는 수십 초~수 분.
- NeMo 는 영어 데이터 학습이라 한국어는 도메인 시프트가 있으나, 다중스케일+overlap 처리로
  ECAPA 단독보다 정밀함. 음색이 매우 비슷한 화자는 여전히 어려울 수 있음 → 화자 수 지정 권장.
- **동시발화만 있는 음성**은 음원 분리 없이는 단일 화자로 못 뽑음(해당 구간은 clip 에서 제외).
