# 공통 AI 하네스

기준일: 2026-09-15. 공통 규칙은 [AGENTS.md](../AGENTS.md), 현재 개발 순서는
[대화 AI 개발 가이드](대화_AI_개발가이드.md)에 있다. 기본 담당은 대화 AI이고 T포즈·3D 제작은 팀원 작업이다.

## 1. 구성

| 계층 | 파일·도구 | 역할 |
|---|---|---|
| 공통 지침 | `AGENTS.md` | 담당 영역, 코드·문서 기준, 작업 절차, 검증·운영 경계 |
| Claude 진입점 | `CLAUDE.md` → `@AGENTS.md`, `@docs/TTS_작업인계_20260918.md` | 공통 규칙과 최신 TTS 완료 상태·남은 작업을 자동으로 읽음 |
| Claude 설정 | `.claude/settings.json` | 기존 모델 설정과 명령·파일 접근 규칙 |
| 선택적 보조 작업 | `.claude/agents/`, `.claude/commands/verify.md`, `impl.md` | 사용자가 요청한 조사·독립 검증·구현안. 메인이 적용 |
| 모의 검사 | `tools/check.py`, `Server/tests/`, `Survey/tests/`, `Web/tests/`, `tools/tests/` | 영역별 검사 실행, 실패 전파, 결과 보관 |
| 자동 실행 | `.github/workflows/checks.yml` | 소스가 GitHub에 반영된 뒤 push·PR·수동 실행에서 같은 모의 검사 사용 |
| 실제 연결·품질 | Unity MCP·AI 테스트 메뉴, `tools/eval_turn_judge.py` | 별도 선택하는 실제 연결·모델 검사 |
| 배포·인계 | `tools/service_bundle.py`, `tools/tripo_handoff.py` | 명시된 소스·해시의 전달. 검사 성공 자체가 배포는 아님 |

Codex의 프로젝트 지침은 `AGENTS.md`를 사용한다. [OpenAI 공식 지침](https://learn.chatgpt.com/docs/agent-configuration/agents-md)
Claude Code의 `@` import로 같은 파일을 연결했다. [Claude 공식 import 설명](https://code.claude.com/docs/en/memory#import-additional-files)
다른 도구에서 자동으로 읽지 않는 경우 `AGENTS.md`와 해당 담당 가이드를 작업 입력으로 제공한다.

이 구조는 작업 규칙과 검사 실행기를 공유한다. 에이전트를 자동으로 여러 개 실행하거나 파일 편집마다 운영 검사를 돌리는 hook은 설치하지 않았다.
사용자는 수행할 작업을 지시하고, AI는 담당 경계에서 구현·검사·보고한다.

## 2. 일상적인 검사

프로젝트 루트에서 실행한다. 옵션을 생략해도 `dialogue`다.

```powershell
python tools/check.py --area dialogue
```

| 영역 | 검사 범위 | 사용할 때 |
|---|---|---|
| `dialogue` | 하네스·인계 도구 + Server 전체 모의 검사 | 우리 대화 AI 개발의 기본 |
| `harness` | 하네스·인계 도구 검사 | 공통 실행기·인계 코드 변경 |
| `platform` | 하네스 + 등록·인물 API 제공자 계약 + Tripo 작업자 + 웹 등록 검사 | 팀원·공통 플랫폼 변경 |
| `all` | 하네스 + Server 전체 + Tripo 작업자 + 웹 등록 검사 | 공통 경계 변경·CI |

Server 전체에는 대화뿐 아니라 인물 API 제공자·소비자 계약도 들어 있다.
`platform`/`all`의 Tripo는 모의 클라이언트다. 실제 생성·이미지 편집·서버 재시작·모델 다운로드는 실행하지 않는다.
웹 검사는 실제 등록 경로 함수·로컬 등록 API 및 Node.js의 가상 DOM·HTTP를 사용하는 화면 스크립트를 포함한다.
실제 브라우저 렌더링·Unity 컴파일은 별도 검사다. 2026-09-15 웹 수정의 실제 Edge 검사·서버 적용은
[웹 등록 흐름 개선](웹_등록_흐름_개선.md)에 기록했다.

실행 계획만 확인하거나 Python을 지정할 수 있다.

```powershell
python tools/check.py --area dialogue --list
python tools/check.py --area all --python .venv-dialogue/Scripts/python.exe
```

Python 선택 순서는 `--python` → `CAPSTONE_CHECK_PYTHON` → 저장소의 `.venv-dialogue` → 실행기 자체의 Python이다.
Windows의 `Scripts/python.exe`, Linux의 `bin/python`을 찾는다. 자동 패키지 설치는 하지 않는다.
다른 폴더에서는 스크립트의 절대 경로를 호출하면 된다. 검사 작업 디렉터리는 항상 이 저장소로 고정한다.

```powershell
python C:/Users/user/Documents/GitHub/Capstone-Design/tools/check.py --area dialogue
```

위 절대 경로는 현재 PC 예시다. 팀원은 자신의 clone 경로를 사용한다.

## 3. 새 PC의 모의 검사 환경

Python 3.12를 기본으로 권장하며 현재 Windows Python 3.9 환경도 검사한다.
아래 `python`이 설치한 Python을 가리키는지 먼저 확인한다. 기존 가상환경이 있으면 재생성하지 않는다.

```powershell
python --version
python -m venv .venv-dialogue
.venv-dialogue/Scripts/python.exe -m pip install -r Server/requirements-test.txt
python tools/check.py --area dialogue
```

Linux에서는 설치 명령의 실행 파일을 `.venv-dialogue/bin/python`으로 바꾼다.
이 의존성은 모의 검사 전용이다. 실제 STT 대화 서버 설치는 `Server/requirements-dialogue.txt`와
[서버 운영 문서](../Server/README.md)를 따른다. 검사 준비를 위해 실제 모델·참여자 자료·API 키를 가져오지 않는다.
일부 프로토콜 검사는 기본 VAD도 초기화하므로 작은 CPU 패키지 `webrtcvad-wheels`를 포함한다.
의존성 파일은 인코딩을 명시해 Windows Python 3.9의 기존 pip에서도 한국어 주석을 읽을 수 있다.

`platform`/`all`의 웹 스크립트 검사는 Node.js 22 이상이 필요하며, 현재 로컬·CI 기준은 24다.
`node --version`으로 확인한다. npm 패키지를 설치하지 않고 Node 내장 테스트 실행기를 쓴다.
CI는 Web 소스도 checkout하고 [공식 setup-node](https://github.com/actions/setup-node)로 Node 24를 준비한다.
`dialogue` 기본 검사에는 Node가 필요하지 않다. 웹 검사 결과에는 호출한 `.cjs` 검사 파일의 해시도 남긴다.

## 4. 결과와 실패 처리

각 실행은 `tools/_work/checks/<UTC시각>-<영역>-<실행ID>/`를 새로 만든다.

- `report.json`: 실행 영역, 선택한 Python, Git HEAD·dirty 여부·상태 해시, 검사 목록과 테스트 파일 해시, 검사 수·실패·건너뜀.
- `harness.log`, `server.log` 등: 해당 검사 프로세스의 출력. 실패한 검사는 콘솔에도 출력한다.
- 종료 코드: 전체 통과 `0`, 검사 실패/시간 초과 `1`, 잘못된 환경·출력 경로 `2`, 사용자 중단 `130`.
- 파일이 없거나 검사 0개, 전부 건너뜀, 프로세스 실패·시간 초과는 통과로 보고하지 않는다.
- 기본 제한은 검사 묶음당 180초다. 필요하면 `--timeout`으로 바꾼다.
- `--output <새 폴더>`를 지정할 수 있으며 기존 폴더를 덮어쓰지 않는다.

결과의 테스트 파일 해시는 검사 입력의 식별자다. 전체 실행 소스의 버전 고정을 대신하지 않는다.
미커밋 작업은 `git.dirty=true`로 표시되므로 비교할 변경 diff와 함께 해석한다.
결과 폴더는 Git에서 제외한다. 실제 모델·마이크 결과와 모의 검사 보고서를 구분한다.

## 5. 자동 검사와 도구 설정

GitHub Actions는 Ubuntu 22.04·Windows와 Python 3.9·3.12 조합에서 `--area all`을 실행한다.
검사 의존성만 설치하고 Unity 대형 에셋은 checkout에서 제외한다. 운영 비밀·SSH·공급자 키를 전달하지 않는다.
검사 로그·JSON은 실행별 artifact로 7일 보관한다. 이 workflow는 저장소에 반영된 뒤 실행되며 branch protection 설정은 별도다.
구성 예시는 [setup-python](https://github.com/actions/setup-python)과 [upload-artifact](https://github.com/actions/upload-artifact)의 공식 사용법을 따른다.

Claude Code는 프로젝트 루트에서 시작한다. `.claude/settings.json`의 파일 패턴을 개인 PC 절대 경로에서
프로젝트 설정 기준 경로로 바꾸고, 폐기한 평가 실행기의 허용 명령을 제거했다.
이 파일의 `Read` 규칙은 Claude 파일 도구에 적용되며 셸·다른 AI 도구 전체의 격리를 제공하지 않는다.
[공식 권한·경로 규칙](https://code.claude.com/docs/en/permissions)

개인 설정과 잠금 파일은 `.gitignore`에서 제외한다. `.claude/launch.json`의 `web`, `rec`는 기존 개별 도구 실행 설정이며
대화 AI의 전체 서버 시작이나 테스트용 환경 준비를 대신하지 않는다.
새 지침의 자동 로딩은 도구의 다음 세션에서 확인한다. 현재 세션은 갱신된 지침을 직접 읽어 적용할 수 있다.

과거 `CLAUDE.md`의 특정 PC 샌드박스·stdin·patch 실측은 공통 제약에서 제거했다.
같은 증상이 재발하면 현재 도구의 오류와 실행 환경을 조사한다. 과거 실측만으로 현재 읽기·쓰기 불가를 단정하지 않는다.

## 6. 검사 후 이어갈 작업

모의 검사 통과 후 필요한 실제 검사는 [대화 AI 개발 가이드](대화_AI_개발가이드.md)의 단계로 선택한다.
최소 보고 항목은 변경 이유, 수정 파일, 실행 명령·보고서, 실제 모델/Unity 검사 여부, 남은 제한이다.
기능 검사 통과를 대화 자연스러움·STT 정확도·음색·리깅 품질의 통과로 보고하지 않는다.

2026-09-13 정리에서는 서버 경계 검사의 Python 3.9 이벤트 루프 의존성을 수정했다.
비동기 대기를 하지 않는 테스트용 모델이 메인 스레드에서 `asyncio.Event`를 만들던 부분을 제거했다.
운영 대화 로직과 프로토콜은 이 수정의 대상이 아니다. 최종 실행 결과는 해당 실행의 `report.json`을 기준으로 확인한다.

## 7. 검증 기록

### 2026-09-18 고인 전제·사망 경위 — 최종 검사와 운영 반영

`--area all`에서 **553개(하네스 195 · 서버 296 · 작업자 5 · 웹 57) 통과, 건너뜀 0개**를 확인했다.
결과: `tools/_work/checks/20260917T160551Z-all-5dd3b644/report.json`.
웹 57개 중 한 검사가 Node 화면 검사를 실행한다. 553에 더해 세지 않는다.
수정 전 기준은 486개(하네스 195 · 서버 291)였다.

**모의 검사다.** 실제 모델 답변 품질은 별도 판정이며, 결과 경로·범주별 수동 판정·남은 관찰
여섯 가지는 `tools/_work/deceased_prompt_fix_20260917/final-report-revised.md` 에 있다.
함께 수행한 것: 실제 Edge 화면 검사 24개, 실제 Gemma **231턴**(본 대본 4인물 × 3회 171턴 +
대본 밖 60턴, 생성·API 오류 0 — **답변 품질 오류 0이라는 뜻이 아니다**),
실제 `/tokenize` 예산 재측정(최소 여유 14토큰).

**2026-09-17T16:15:29Z 운영 반영을 마쳤다.** 런타임 7개를 교체하고 대화 8002·웹 8500만
다시 시작했다. `prompt_version` 이 `gemma4_dialogue_v3`, 설문 변환기가 `survey_v2_compile_3` 다.
vLLM 8001·등록 8000·TTS 8003 은 재시작하지 않았고 현재 등록 인물도 바꾸지 않았다.
절차·백업·되돌리기·인계는
`tools/_work/deceased_prompt_fix_20260917/deployment-report.md` 를 따른다.

### 2026-09-15 웹 등록 개선 후 최신 검사

설문 재작성·예시 제거·UUID4 등록을 포함한 `--area all` 검사에서
**135개(하네스 11, 서버 112, 작업자 5, 웹 7) 통과, 건너뜀 0개**를 확인했다.
웹 검사 하나가 Node에서 11가지 화면 스크립트 동작을 실행한다. 135개에 별도 11개를 더해 보고하지 않는다.
결과: `tools/_work/checks/20260915T071210Z-all-a9fc46a0/report.json`.
인계 목록 관련 검사를 보강한 뒤 하네스 11개도 통과했다:
`tools/_work/checks/20260915T075146Z-harness-5c7e9db5/report.json`.

실제 Edge의 설문 변경·실패 복구·등록 화면 검사는 로컬 등록 응답을 사용했다.
운영 서버에서는 새 화면·인물 작성·잘못된 등록 요청 거부·배포 해시·health·로그를 확인했다.
기존 운영 인물을 바꾸는 성공 등록이나 실제 Tripo 생성은 수행하지 않았다.
검사 범위와 서버 백업은 [웹 등록 흐름 개선](웹_등록_흐름_개선.md)에 있다.
문서 최신화에서는 실행 소스·검사 파일을 추가로 바꾸지 않아 통과한 검사를 반복하지 않았다.

### 2026-09-15 대화 AI·Unity 전체 검사

웹 등록 수정 전 커밋 `8fbd6ea`에서 모의 검사 125개, 실제 판정 68개, 기억 60턴,
되물음 5개 시나리오·199턴, 음성 왕복 9회와 Unity 검사 메뉴 4개를 통과했다.
결과·지연·Quest Pro 미검증 범위는 [전체 검증 기록](전체_검증_20260915.md)에 있다.
웹 등록 개선 후의 회귀 검사는 위 135개 결과로 구분한다.

### 2026-09-15 대기 리액션 적용 당시

대화 기억·TTS 생성 스트리밍·캐릭터 대기 리액션을 포함한 `--area all` 검사에서
**125개(하네스 11, 서버 109, 작업자 5) 통과, 건너뜀 0개**를 확인했다.
결과: `tools/_work/checks/20260915T033824Z-all-a32d1b10/report.json`.
이후 문서 정리는 실행 소스·검사 파일을 바꾸지 않았으므로 같은 검사를 반복하지 않는다.

같은 구현의 실제 음성 경로 9회, Unity 참조 음성 왕복과 끼어들기 4개 경로도 통과했다.
측정 범위와 첫 리액션/본답변 시간은 [캐릭터 대기 리액션](캐릭터_대기_리액션.md)을 따른다.
원본 보고서·음성은 Git에서 제외되는 로컬/서버 검사 폴더에 있으며 새 clone에 포함되지 않는다.
문서의 결과 요약과 재실행 명령을 전달하며, 실제 Quest 장치·청취 품질 평가는 별도로 남긴다.

### 2026-09-13 하네스 도입 당시

2026-09-13 Windows Python 3.9에서 기본 `dialogue` 검사 69개, `all` 검사 74개를 통과했다.
기존 환경에만 설치된 패키지에 의존하지 않는지 확인하기 위해 새 가상환경에 `requirements-test.txt`만 설치했고,
동일한 74개(하네스·인계 11, 서버 58, 작업자 모의 검사 5)를 모두 통과했다. 건너뛴 검사는 없다.
새 환경에는 torch·sherpa-onnx·모델 가중치를 설치하지 않았다.

새 환경 결과: `tools/_work/checks/20260913T072159Z-all-375eafbf/report.json`.
실행기에는 실패·0개 검사·전부 건너뜀·시간 초과·기존 보고서 보존 검사가 포함된다.
다른 작업 디렉터리에서의 실행 계획, 문서 링크, Claude 설정·명령 형식, CI YAML·Action 버전도 확인했다.
GitHub에서의 실제 workflow 실행과 다음 AI 세션의 자동 지침 로딩은 이번 로컬 검증에 포함하지 않았다.
