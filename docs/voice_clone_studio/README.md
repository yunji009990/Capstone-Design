# voice_clone_studio 문서 (보존본)

**이 프로젝트의 코드가 아니다.** 바탕화면 `Desktop/capstone/voice_clone_studio` 에 있던
별도 작업물의 문서만 옮겨온 것이다. 원본 18GB 는 삭제했다.

## 왜 남겼나

두 가지가 **지금 우리가 쓰는 것의 근거**라서다.

| 파일 | 무엇 |
|---|---|
| [`archive/audio_extraction_README.md`](archive/audio_extraction_README.md) | **NeMo MSDD 를 고른 이유**와 파이프라인 설명. 이 엔진은 `Web/extraction/` 으로 들여와 지금 쓰고 있다 |
| [`제거된_기능.md`](제거된_기능.md) | zero-shot 한국어 TTS **5종 벤치마크 결과표**. "왜 저 모델이 아닌가"를 물으면 답이 여기 있다 |

나머지는 설계 판단 기록이다.

| 파일 | 무엇 |
|---|---|
| [`파이프라인_구조.md`](파이프라인_구조.md) | 그쪽 시스템의 전체 구조 |
| [`음성대화_아키텍처_비교.md`](음성대화_아키텍처_비교.md) | 음성 대화 방식 비교 |
| [`archive/tts_benchmark_spec.md`](archive/tts_benchmark_spec.md) | 벤치마크 설계 — 지표 정의와 측정 방법 |
| [`archive/tts_benchmark_README.md`](archive/tts_benchmark_README.md) · [`archive/tts_benchmark_report.md`](archive/tts_benchmark_report.md) | 실행 방법과 결과 보고 |
| [`_원본_README.md`](_원본_README.md) | 원본 저장소 최상위 README |

## 주의 — 벤치마크 1위는 우리가 쓰는 모델이 아니다

그 벤치마크의 결론은 **Qwen3-TTS** 였고 한때 `TTSWeb/` 으로 따로 확인해 봤지만,
**이 프로젝트는 Qwen3-TTS 를 쓰지 않는다.** 제품이 쓰는 것은 **Raon**(`Server/`)이며,
STT·LLM·TTS 를 한 모델이 처리한다. 근거와 실측은
[`../Raon모델_분석.md`](../Raon모델_분석.md).

벤치마크가 다루지 않은 축이 있어서다 — Raon 은 **대화까지 한 모델로** 하므로
받아쓰기·답변·합성을 따로 잇지 않아도 되고, 참조 음성으로 목소리를 복제한다.
TTS 품질만 놓고 고른 순위와는 기준이 다르다.

## 문서 안 경로는 죽어 있다

`nemo_env/`, `engine/tts/`, `backend/` 같은 경로가 본문에 나오지만 **원본이 삭제돼
존재하지 않는다.** 화자 분리 엔진에 해당하는 부분은 [`../../Web/extraction/README.md`](../../Web/extraction/README.md)
에 지금 구조로 다시 적어뒀으니 그쪽을 볼 것.
