# Zero-shot 한국어 TTS 모델 벤치마크 파이프라인 명세

이 문서는 Claude Code에게 전달하는 작업 명세다. 목표: 여러 zero-shot TTS 모델을 동일 조건에서 벤치마크하여 한국어 음성 생성 품질이 가장 좋은 모델을 선정한다.

## 배경

- 태스크: 텍스트 + 임의 화자의 reference 오디오(5~15초) → 해당 화자 목소리의 한국어 음성 생성 (zero-shot voice cloning TTS)
- 파인튜닝 없음. reference는 추론 시 조건 입력으로만 사용
- 완전 로컬 추론 (외부 API 호출 금지 — 오디오 데이터 보안 요건)
- 비영리 사용이므로 비상업 라이선스 모델도 후보에 포함 가능

## 비교 대상 모델

| ID | 모델 | 비고 |
|----|------|------|
| `f5tts_ko` | F5-TTS + 한국어 파인튜닝 체크포인트 | Hugging Face에서 한국어 커뮤니티 체크포인트 검색 후 다운로드 수·최신성 기준으로 선택. 선택한 체크포인트를 README에 기록 |
| `cosyvoice2` | CosyVoice 2 (0.5B) 또는 Fun-CosyVoice 3 | FunAudioLLM/CosyVoice 리포. zero-shot 모드 사용 |
| `chatterbox` | Chatterbox Multilingual (Resemble AI) | `language_id="ko"` 사용 |
| `gptsovits` | GPT-SoVITS (v2 이상, 한국어 지원 버전) | 파인튜닝 없이 **zero-shot 추론 모드만** 사용. reference 오디오 + 전사(`ref_text`) 필요 |
| `qwen3tts` | Qwen3-TTS (Alibaba Qwen) | 보이스 클로닝 지원 체크포인트 확인 후 사용. 한국어 지원 여부를 셋업 단계에서 검증하고, 미지원이면 README에 사유 기록 후 벤치마크에서 제외 처리 |

새 모델 추가가 쉬운 어댑터 구조로 만들 것 (아래 인터페이스 참조). Qwen3-TTS는 최근 공개된 모델이라 API·체크포인트 구조가 문서와 다를 수 있음 — 셋업 시 공식 리포 README를 우선 확인할 것.

## 핵심 설계 원칙

1. **모델별 독립 가상환경**: 세 모델은 의존성이 충돌한다(torch/transformers 버전 등). 모델별로 별도 venv 또는 conda env를 만들고, 오케스트레이터는 각 모델의 추론을 **subprocess CLI 호출**로 실행한다. 하나의 파이썬 프로세스에 전부 import하려고 시도하지 말 것.
2. **공통 어댑터 인터페이스**: 각 모델 어댑터는 동일한 CLI 계약을 따른다.
   ```
   python adapters/<model_id>/infer.py \
     --manifest <jobs.jsonl> \
     --output-dir <dir>
   ```
   - `jobs.jsonl`의 각 줄: `{"job_id": str, "text": str, "ref_audio": path, "ref_text": str}`
   - 출력: `<output-dir>/<job_id>.wav` + `<output-dir>/meta.jsonl` (job_id, 추론 소요 시간, 생성 오디오 길이, 실패 시 에러)
   - `ref_text`는 reference 오디오의 전사. F5-TTS와 CosyVoice는 필요하고 Chatterbox는 무시해도 된다.
3. **재현성**: 고정 seed, 모델 버전/체크포인트 해시를 결과에 기록.
4. **실패 허용**: 한 job 실패가 전체를 중단시키지 않는다. 실패는 기록하고 계속 진행, 최종 리포트에 실패율 표기.

## 리포지토리 구조

```
tts-benchmark/
├── README.md                  # 셋업/실행 방법
├── configs/benchmark.yaml     # 모델 목록, 경로, 평가 설정
├── data/
│   ├── references/            # 화자별 원본 오디오 (사용자가 넣음)
│   │   └── spk01/raw.wav ...
│   └── texts/eval_sentences.txt  # 평가 문장 (한 줄에 한 문장)
├── adapters/
│   ├── f5tts_ko/   (env/, infer.py, setup.sh)
│   ├── cosyvoice2/ (env/, infer.py, setup.sh)
│   ├── chatterbox/ (env/, infer.py, setup.sh)
│   ├── gptsovits/  (env/, infer.py, setup.sh)
│   └── qwen3tts/   (env/, infer.py, setup.sh)
├── pipeline/
│   ├── preprocess_refs.py     # reference 전처리·세그먼트 선정
│   ├── build_manifest.py      # (화자 × 문장) 조합으로 jobs.jsonl 생성
│   ├── run_benchmark.py       # 오케스트레이터: 모델별 subprocess 실행
│   ├── evaluate.py            # 지표 계산
│   └── report.py              # 결과 집계·리포트 생성
├── eval_env/                  # 평가 전용 venv (whisper, speechbrain 등)
├── results/
│   ├── audio/<model_id>/<job_id>.wav
│   ├── metrics.csv
│   └── report.md
└── listening_test/index.html  # 블라인드 청취용 정적 페이지
```

## 단계별 상세

### 0단계 — 테스트 화자 데이터 자동 구성 (`pipeline/prepare_test_speakers.py`)

사용자가 직접 준비한 reference 오디오가 없으므로, 공개 한국어 음성 데이터셋에서 테스트 화자를 자동으로 구성한다. `data/references/`에 화자 폴더가 이미 존재하면 이 단계는 스킵.

**데이터 소스 (우선순위 순, 다운로드 가능한 것 사용):**

1. **Zeroth-Korean** (OpenSLR SLR40, CC-BY-4.0) — 다화자 한국어 낭독 음성, 16kHz. 화자 메타데이터가 있어 화자별 분리가 쉬움. test 세트만 받으면 용량 부담이 적음
2. **Mozilla Common Voice Korean** (CC0) — Hugging Face `mozilla-foundation/common_voice_*` 한국어 서브셋. client_id로 화자 구분. HF 토큰/약관 동의가 필요할 수 있음 — 필요 시 사용자에게 안내하고 다음 소스로 폴백
3. **Emilia 한국어 서브셋** (CC-BY-NC-4.0, HF `amphion/Emilia-Dataset`) — in-the-wild 음성이라 실사용 조건과 가장 유사. 용량이 크므로 스트리밍 모드로 필요한 만큼만

**구성 방법:**

- 화자 8명 선정: 성별 균형(남 4/여 4), 화자당 발화가 충분한(합산 60초 이상) 화자 우선
- 화자별로 발화를 이어붙여 `data/references/<spk_id>/raw.wav` 생성 (화자당 1~3분이면 충분, 그 이상 받지 말 것)
- `data/references/sources.json`에 각 화자의 출처 데이터셋·원본 화자 ID·라이선스를 기록
- 이후 1단계 전처리가 raw.wav에서 최적 세그먼트를 알아서 선정하므로, 여기서는 품질 필터링을 과하게 하지 않아도 됨

**주의:**

- Zeroth는 16kHz라 24kHz를 기대하는 모델에 업샘플 입력이 들어감. 벤치마크 조건으로는 동일하게 적용되므로 공정하지만, 절대 품질이 실사용(고품질 reference)보다 낮게 나올 수 있음을 report.md에 명시할 것
- 가능하면 소스를 섞을 것 (예: Zeroth 4명 + Common Voice 또는 Emilia 4명) — 낭독체/일상체, 스튜디오/일반 녹음 환경이 섞여야 모델의 강건성 차이가 드러남
- 다운로드는 셋업 단계에서만 수행 (오프라인 요건 유지)

### 1단계 — Reference 전처리 (`preprocess_refs.py`)

입력: `data/references/<spk_id>/` 아래의 임의 길이 오디오(수 초~수십 분).

처리:
1. ffmpeg로 모노 변환, 24kHz 리샘플 (모델 어댑터에서 각자 필요 샘플레이트로 재변환)
2. Silero VAD로 발화 구간 검출
3. 후보 구간 필터링: 길이 6~12초, 클리핑 없음(피크 < -1dBFS), 에너지 기반 SNR 추정치 상위
4. 최고 점수 구간 1개를 `data/references/<spk_id>/ref.wav`로 저장
5. faster-whisper (large-v3, `language="ko"`)로 해당 구간 전사 → `ref.txt`
6. 화자별 처리 요약을 `data/references/manifest.json`에 기록

이미 `ref.wav`가 있으면 스킵 (캐싱).

### 2단계 — 매니페스트 생성 (`build_manifest.py`)

- 평가 문장 N개 × 화자 M명 = N×M jobs. 기본 규모: 문장 30개 × 화자 5~10명
- 평가 문장이 없으면 기본 세트를 생성해서 `eval_sentences.txt`에 넣을 것. 구성: 짧은 문장(10자 내외) / 중간(30자) / 긴 문장(60자+), 숫자 포함 문장, 영어 혼용 문장, 질문문·감탄문 골고루
- 숫자·영어는 텍스트 정규화(한글 발음 변환)를 적용한 버전을 manifest에 넣되, 원문도 함께 기록

### 3단계 — 추론 실행 (`run_benchmark.py`)

- configs의 모델 목록을 순회하며 모델별로: `adapters/<id>/env` 활성화 → `infer.py` subprocess 실행
- 모델당 GPU 메모리를 완전히 반환한 뒤 다음 모델 실행 (프로세스 종료로 보장됨)
- 진행 상황 로그, 모델별 총 소요 시간 기록
- `--models f5tts_ko,chatterbox` 처럼 부분 실행 가능하게

### 4단계 — 평가 (`evaluate.py`, eval_env에서 실행)

생성된 모든 wav에 대해 (평가 전 16kHz 모노로 통일):

| 지표 | 방법 | 의미 |
|------|------|------|
| **SECS** (화자 유사도) | SpeechBrain `spkrec-ecapa-voxceleb`로 생성음성 vs ref.wav 임베딩 코사인 유사도 | 높을수록 목소리가 닮음 |
| **CER** | faster-whisper large-v3로 생성 음성 재전사 → 원문 대비 CER (한글 기준, 공백·문장부호 제거 후 계산) | 낮을수록 발음 정확 |
| **UTMOS** (선택) | utmos 계열 자동 MOS 예측기. 설치 실패 시 스킵하고 리포트에 표기 | 높을수록 자연스러움 |
| **RTF** | 추론시간 / 생성오디오 길이 | 낮을수록 빠름 |
| **실패율** | 생성 실패 + 무음/이상 출력(길이 < 0.5초 등) 비율 | |

결과를 `results/metrics.csv`에 job 단위로 저장 (model_id, spk_id, text_id, secs, cer, utmos, rtf, status).

### 5단계 — 리포트 (`report.py`)

`results/report.md` 생성:
- 모델별 요약 테이블: SECS 평균±표준편차, CER 평균, UTMOS 평균, RTF, 실패율
- 화자별 breakdown (특정 화자 유형에서 무너지는 모델 식별)
- 문장 길이별 CER (긴 문장에서의 안정성)
- 종합 순위와 근거. 기본 가중치: SECS 40% / CER 40% / UTMOS 20% (RTF·실패율은 참고 지표). 가중치는 configs에서 조정 가능하게

### 6단계 — 블라인드 청취 페이지 (`listening_test/index.html`)

- 정적 HTML 하나. 같은 (화자, 문장) 조합의 모델별 생성 오디오를 모델명 가린 채 A/B/C로 나란히 재생
- 랜덤 순서 셔플, 선호 선택을 localStorage 대신 화면에 집계 표시 (단순하게)
- 로컬에서 `python -m http.server`로 열어보는 용도

## 실행 순서 (README에 명시할 것)

```bash
# 1. 모델별 환경 셋업 (최초 1회, 각자 setup.sh)
bash adapters/f5tts_ko/setup.sh
bash adapters/cosyvoice2/setup.sh
bash adapters/chatterbox/setup.sh
bash adapters/gptsovits/setup.sh
bash adapters/qwen3tts/setup.sh
bash eval_env/setup.sh

# 2. 테스트 화자 자동 구성 (직접 준비한 오디오가 있으면 data/references/<spk_id>/에 넣고 이 단계 스킵)
python pipeline/prepare_test_speakers.py

# 3. 벤치마크 실행
python pipeline/preprocess_refs.py
python pipeline/build_manifest.py
python pipeline/run_benchmark.py            # 전체 모델
python pipeline/evaluate.py
python pipeline/report.py
```

## 주의사항

- CosyVoice는 git submodule 초기화 필요 (`git clone --recursive`). ModelScope에서 체크포인트 다운로드
- F5-TTS 한국어 체크포인트는 base 모델과 vocab 파일 경로를 함께 지정해야 함
- Chatterbox 출력에는 PerTh 워터마크가 기본 삽입됨 (평가에는 영향 없음, 참고만)
- GPT-SoVITS는 WebUI 중심 리포라 CLI/파이썬 API 경로를 찾아 어댑터로 감쌀 것. 학습 관련 코드는 사용하지 않음. reference는 3~10초를 권장하므로 전처리 세그먼트가 12초에 가까우면 앞 10초로 트리밍
- Qwen3-TTS는 체크포인트 크기가 클 수 있음 — VRAM 초과 시 더 작은 사이즈 변형을 선택하고 README에 기록. 보이스 클로닝 입력 형식(reference 오디오 전달 방식)이 다른 모델과 다를 수 있으니 어댑터에서 흡수할 것
- 모든 다운로드는 셋업 단계에서만. 추론·평가 단계는 오프라인으로 동작해야 함
- GPU 1장 기준으로 작성. VRAM 부족 시 배치 크기 1로

## 완료 기준

- [ ] 공개 데이터셋에서 테스트 화자 8명 구성 완료, sources.json에 출처·라이선스 기록
- [ ] 다섯 모델 모두 동일 manifest로 추론 완료, 실패율 < 10% (셋업 단계에서 한국어 미지원이 확인되어 제외한 모델은 예외, 사유 기록 필수)
- [ ] metrics.csv와 report.md 생성
- [ ] report.md에 종합 순위 + 모델별 강약점 서술
- [ ] 블라인드 청취 페이지에서 오디오 재생 확인
- [ ] README만 보고 처음부터 재실행 가능
