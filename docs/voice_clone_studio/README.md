# voice_clone_studio 문서 (보존본)

**이 프로젝트의 코드가 아니다.** 바탕화면 `Desktop/capstone/voice_clone_studio` 에 있던
별도 작업물의 문서만 옮겨온 것이다. 원본 18GB 는 삭제했다.

## 왜 남겼나

이전 화자 분리·음성 합성 선택의 근거를 보존하기 위해서다. 현재 운영 지침은
[현재 구현 현황](../현재_구현_현황.md)을 따른다.

| 파일 | 무엇 |
|---|---|
| [`archive/audio_extraction_README.md`](archive/audio_extraction_README.md) | **NeMo MSDD를 고른 당시 이유**. 코드는 `Web/extraction/`에 남아 있지만 9월 17일 이후 등록 웹은 호출하지 않는다 |
| [`제거된_기능.md`](제거된_기능.md) | 당시 zero-shot 한국어 TTS **5종 벤치마크 결과표**. 현재 운영 모델의 비교 평가가 아니다 |

나머지는 설계 판단 기록이다.

| 파일 | 무엇 |
|---|---|
| [`파이프라인_구조.md`](파이프라인_구조.md) | 그쪽 시스템의 전체 구조 |
| [`음성대화_아키텍처_비교.md`](음성대화_아키텍처_비교.md) | 음성 대화 방식 비교 |
| [`archive/tts_benchmark_spec.md`](archive/tts_benchmark_spec.md) | 벤치마크 설계 — 지표 정의와 측정 방법 |
| [`archive/tts_benchmark_README.md`](archive/tts_benchmark_README.md) · [`archive/tts_benchmark_report.md`](archive/tts_benchmark_report.md) | 실행 방법과 결과 보고 |
| [`_원본_README.md`](_원본_README.md) | 원본 저장소 최상위 README |

## 현재 운영과의 관계 (2026-09-29 확인)

이 보존본의 벤치마크 결론은 Qwen3-TTS였고, 이후 본 프로젝트에서는 Raon과 Qwen을 각각
운영한 시기가 있었다. **Raon은 폐기했고, 9월 18일 이후 운영 TTS는 VoxCPM2다.**
현재 경로는 Whisper large-v3 → Gemma → VoxCPM2이며 모델 교체·추가 비교를 재개하지 않는다.
[TTS 인계](../TTS_작업인계_20260918.md)의 운영 확정과 참조 음성 기준을 따른다.

아래 원본 문서에 있는 `현재`, 모델 순위, 실행 명령은 그 별도 프로젝트의 작성 시점 기준이다.
과거 Raon 선택 근거는 [모델 분석 보관본](../Raon모델_분석.md)에 남아 있다.

## 문서 안 경로는 죽어 있다

`nemo_env/`, `engine/tts/`, `backend/` 같은 경로가 본문에 나오지만 **원본이 삭제돼
존재하지 않는다.** 화자 분리 엔진에 해당하는 부분은 [`../../Web/extraction/README.md`](../../Web/extraction/README.md)
에 보존 코드의 범위와 현재 웹에서 사용하지 않는다는 점을 적었다.
