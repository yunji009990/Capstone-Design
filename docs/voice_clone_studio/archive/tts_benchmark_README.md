# Zero-shot 한국어 TTS 벤치마크

여러 zero-shot voice cloning TTS 모델을 동일 조건(같은 화자 reference, 같은 문장, 같은 seed)에서 비교해
한국어 음성 생성 품질이 가장 좋은 모델을 선정하는 파이프라인. 추론·평가는 전부 로컬에서 수행된다
(다운로드는 셋업 단계에서만).

## 비교 모델

| ID | 모델 | 체크포인트 | ref_text 필요 |
|----|------|-----------|:---:|
| `f5tts_ko` | F5-TTS + 한국어 커뮤니티 체크포인트 | `team-lucid/F5-TTS-ko` (HF에서 사실상 유일한 한국어 F5 체크포인트, Apache-2.0) | O |
| `cosyvoice2` | Fun-CosyVoice3-0.5B (FunAudioLLM) | `FunAudioLLM/Fun-CosyVoice3-0.5B-2512` — 한국어 공식 지원 9개 언어 중 하나. 명세의 "CosyVoice 2 또는 Fun-CosyVoice 3" 중 후자 선택(한국어 학습 데이터가 훨씬 많음) | O |
| `chatterbox` | Chatterbox Multilingual (Resemble AI) | `ResembleAI/chatterbox`, `language_id="ko"` | X |
| `gptsovits` | GPT-SoVITS v2ProPlus | `lj1995/GPT-SoVITS` 사전학습 가중치, zero-shot 추론만 사용 | O |
| `qwen3tts` | Qwen3-TTS (2026-01 오픈소스 공개, Apache-2.0) | `Qwen/Qwen3-TTS-12Hz-1.7B-Base` — 한국어 공식 지원 | O(권장) |

## 요구 사항

- Windows 11 / Linux, NVIDIA GPU (VRAM 16GB 기준 작성, RTX 50xx는 cu128 torch 필수 — setup 스크립트가 처리)
- `python` 3.10+ (venv 생성용), `git`, `ffmpeg` (PATH에 있어야 함)
- 디스크 여유 ~60GB (체크포인트 + 데이터셋)
- setup 스크립트는 bash 스크립트 (Windows에서는 Git Bash로 실행)

## 실행 순서

```bash
# 1. 모델별 환경 셋업 (최초 1회 — 모델별 독립 venv, 의존성 충돌 방지)
bash adapters/f5tts_ko/setup.sh
bash adapters/cosyvoice2/setup.sh
bash adapters/chatterbox/setup.sh
bash adapters/gptsovits/setup.sh
bash adapters/qwen3tts/setup.sh
bash eval_env/setup.sh

# 이후 파이프라인 스크립트는 eval_env의 python으로 실행
EVPY=eval_env/env/Scripts/python.exe   # Linux: eval_env/env/bin/python

# 2. 테스트 화자 자동 구성 (직접 준비한 오디오가 있으면
#    data/references/<spk_id>/ 아래에 넣고 이 단계는 자동 스킵됨)
$EVPY pipeline/prepare_test_speakers.py

# 3. 벤치마크 실행
$EVPY pipeline/preprocess_refs.py      # ref.wav(6~12초 최적 구간) + ref.txt(whisper 전사)
$EVPY pipeline/build_manifest.py       # (화자 x 문장) jobs.jsonl
$EVPY pipeline/run_benchmark.py        # 전체 모델. 부분 실행: --models f5tts_ko,chatterbox
$EVPY pipeline/evaluate.py             # SECS/CER/UTMOS/RTF -> results/metrics.csv
$EVPY pipeline/report.py               # results/report.md + listening_test/manifest.json

# 4. 블라인드 청취 (리포 루트에서)
python -m http.server   # -> http://localhost:8000/listening_test/
```

## 평가 지표

| 지표 | 방법 | 의미 |
|------|------|------|
| SECS | SpeechBrain ECAPA 임베딩 코사인 유사도 (생성 vs ref) | 화자 유사도, 높을수록 좋음 |
| CER | faster-whisper large-v3 재전사 vs 원문 (한글만, 공백·부호 제거) | 발음 정확도, 낮을수록 좋음 |
| UTMOS | utmos22_strong (tarepan/SpeechMOS) | 자연스러움 예측 MOS |
| RTF | 추론시간 / 생성오디오 길이 | 속도 |
| 실패율 | 에러 + 무음/0.5초 미만 출력 비율 | 안정성 |

종합 순위 가중치(SECS 0.4 / CER 0.4 / UTMOS 0.2)는 `configs/benchmark.yaml`의 `ranking.weights`에서 조정.

## 구조

- 각 어댑터는 동일 CLI 계약: `infer.py --manifest jobs.jsonl --output-dir DIR --seed N`
  - 출력: `<dir>/<job_id>.wav` + `meta.jsonl`(추론시간·오디오길이·에러) + `model_info.json`(체크포인트 해시)
  - 이미 생성된 job은 자동 스킵(재실행 안전). `--overwrite`로 강제 재생성, `--limit N`으로 부분 테스트
- 오케스트레이터는 어댑터를 subprocess로 실행 → 모델 간 의존성 충돌 없음, 모델 종료 시 VRAM 완전 반환
- 새 모델 추가: `adapters/<new_id>/{setup.sh, infer.py}` 작성 (adapter_lib.run_adapter 사용) 후
  `configs/benchmark.yaml`의 models에 등록

## 테스트 화자 데이터

`pipeline/prepare_test_speakers.py`가 공개 데이터셋에서 8명(가능하면 남4/여4) 자동 구성:
1. **Zeroth-Korean** (OpenSLR SLR40, CC-BY-4.0) — 스튜디오 낭독체, 16kHz
2. **Emilia KO** (CC-BY-NC-4.0) — in-the-wild. HF 약관 동의 필요할 수 있음, 실패 시 Zeroth로 폴백

출처·라이선스는 `data/references/sources.json`에 기록됨.

> **주의**: Zeroth는 16kHz라 24kHz를 기대하는 모델에는 업샘플 입력이 들어간다. 모든 모델에 동일 조건이므로
> 상대 비교는 공정하지만, 절대 품질은 고품질 reference 대비 낮게 나올 수 있다 (report.md에도 명시).

## 재현성·주의사항

- 고정 seed(configs `seed: 42`), 체크포인트 sha256을 `model_info.json`에 기록
- 한 job 실패는 기록만 하고 계속 진행. 실패율은 리포트에 표기
- Chatterbox 출력에는 비가청 PerTh 워터마크가 삽입됨 (평가 영향 없음)
- GPT-SoVITS reference는 3~10초 제한 — 어댑터가 12초 세그먼트를 앞 10초로 자동 트리밍
- 오프라인 강제: `configs/benchmark.yaml`의 `inference.offline: true` (셋업에서 모든 가중치를 미리 받아둔 뒤 사용)
