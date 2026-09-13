---
description: 영역별 모의 검사를 실행한다. 기본은 대화 AI이며 운영 서버나 생성 API를 호출하지 않는다.
argument-hint: '[dialogue|platform|harness|all]'
allowed-tools: Bash, Read, Glob
---

영역: $ARGUMENTS

`AGENTS.md`와 `docs/AI_하네스.md`를 확인한다. 생략하면 `dialogue`다.
영역은 위 네 값 중 하나만 받아 인수로 전달한다. 임의 문자열을 셸 명령에 삽입하지 않는다.

프로젝트 루트에서 `python tools/check.py --area dialogue`를 실행한다. 다른 영역은 검증한 값으로 바꾼다.
환경 선택과 다른 작업 디렉터리에서의 호출은 하네스 문서를 따른다.
의존성이 없으면 실패를 보고하고 해당 모의 검사 환경을 준비한다. 운영 서버를 대신 변경하지 않는다.

종료 코드와 출력된 `report.json`을 확인한다. 전체 상태, 검사 수, 실패 로그 위치를 보고한다.
이 명령은 Unity 컴파일·실제 모델·마이크·답변 품질을 검증하지 않는다. 관련 요청이면 해당 검사 절차를 별도로 따른다.
