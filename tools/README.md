# 개발·실험 도구

우리 기본 작업은 대화 AI다. [공통 하네스](../docs/AI_하네스.md)와
[대화 AI 개발 가이드](../docs/대화_AI_개발가이드.md)를 먼저 읽는다.
`python tools/check.py --area dialogue`로 하네스·서버 모의 검사를 묶어서 실행하고 JSON·로그를 남긴다.
공통 변경은 `--area all`, 하네스만 변경하면 `--area harness`를 사용한다.
실제 모델·Unity·마이크 검사는 이 명령과 구분한다.

Tripo 팀원 인수인계는 [AI 작업 지시서](../docs/Tripo_팀원_AI_작업지시서.md)와
[개발환경 실행 가이드](../docs/Tripo_개발환경_실행가이드.md)를 따른다.
`tripo_handoff.py`는 현재 소스·문서와 삭제 목록을 ZIP으로 묶고, 새 clone의 기준 파일을 확인한 뒤 적용한다.
기존 Git index·브랜치를 변경하지 않으며 실제 키·사진·GLB는 담지 않는다.

2026-09-10 Raon 제거에 맞춰 폐기된 `/chat`·`/talk`·`/stt` API와 고정 참조 파일에
의존하던 실행기를 정리했다. 이전 조건·결과·평가 교훈은
[Raon 작업 이력](../docs/Raon_작업이력_보관.md)과 그 문서의 상세 자료에 보관한다.
과거 실행 소스는 Git 이력에서 확인할 수 있다.

현재 자동 검사는 `Server/tests/`, 실제 음성 왕복·중단 검사는
[AI 응답 테스트 씬](../docs/AI_응답_테스트_씬.md)을 사용한다.

2026-09-11부터 판정기는 `eval_turn_judge.py`와 `turn_judge_cases.json`으로 텍스트 검사한다.
일반/추론 분류와 끼어들기 방향을 운영 LLM에 요청하며 STT·TTS를 호출하지 않는다.
기본 48개와 별도 20개 사례의 결과·실행 방법은 [판정기 텍스트 검사](../docs/판정기_텍스트_검사.md)에 있다.

답변 자체는 `eval_dialogue_prompts.py`로 여러 턴 비교한다.
`dialogue_prompt_cases.json`은 기본 대본, `dialogue_prompt_holdout.json`은 별도 대본이다.
현재 Gemma를 직접 호출하며 Unity·STT·TTS·등록 API를 사용하지 않는다.
구형/규칙만 변경/전체 변경을 같은 생성 설정에서 비교하고 실제 답변을 다음 턴에 유지한다.
모의 검사에 자동 포함하지 않으며, 실행 방법과 품질 한계는
[Gemma 4 대화 프롬프트](../docs/Gemma4_대화프롬프트.md)를 참고한다.

`prompts/`의 인물·규칙 자료와 평가 대본은 보존했다. Tripo 도구, 녹음 수집과
음성 지표 계산 도구도 유지한다. 모델별 과거 성적과 현재 파이프라인 검증을 구분한다.
기존 프롬프트 품질 비교를 재개할 때는 등록 인물을 교체하는 옛 HTTP 명령 대신
테스트 연결의 임시 인물 지침과 새 WebSocket 계약을 사용한다.
