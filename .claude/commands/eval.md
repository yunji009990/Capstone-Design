---
description: 대화 AI의 기능 검사와 실제 여러 턴 품질 평가를 구분해 실행한다.
argument-hint: <기능 검사 또는 품질 평가 대상>
allowed-tools: Bash, Read, Grep, Glob
---

대상: $ARGUMENTS

`AGENTS.md`, `docs/대화_AI_개발가이드.md`, `docs/AI_하네스.md`를 먼저 읽는다.

- 기능 검사: `python tools/check.py --area dialogue`. 모의 STT/LLM/TTS·인물 API 계약 검사다.
- 판정기 실제 모델 검사: `tools/eval_turn_judge.py`. 설정과 사례·결과 해시는 `docs/판정기_텍스트_검사.md`를 따른다.
- 실제 응답/Unity 연결: `docs/AI_응답_테스트_씬.md`의 텍스트 검사 메뉴를 사용한다.
- 대화 품질: 새 테스트 연결에서 임시 인물·상황·기록을 유지하며 여러 턴을 비교한다.
  알려준 사실, 모르는 사실, 말투, 앞선 답변 일관성, 수정·전환·대기를 함께 평가하고 원문과 판정 근거를 남긴다.

기능 검사·분류 일치율·대화 품질·실제 마이크 성능을 서로의 통과 근거로 사용하지 않는다.
TTS는 꺼 둔다. 공유 서버의 현재 등록 인물을 평가용으로 교체하지 않는다.
실제 모델 평가가 요청되지 않은 단순 기능 검사에서 운영 LLM·유료 공급자를 자동 호출하지 않는다.
구형 `/chat`, `/talk`, `tools/prompt_eval.py`와 Raon 실행기는 사용하지 않는다.
