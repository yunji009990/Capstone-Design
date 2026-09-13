# 공통 AI 하네스

기준일: 2026-09-13. 공통 규칙은 [AGENTS.md](../AGENTS.md), 현재 개발 순서는
[대화 AI 개발 가이드](대화_AI_개발가이드.md)에 있다. 기본 담당은 대화 AI이고 T포즈·3D 제작은 팀원 작업이다.

## 1. 구성

| 계층 | 파일·도구 | 역할 |
|---|---|---|
| 공통 지침 | `AGENTS.md` | 담당 영역, 코드·문서 기준, 작업 절차, 검증·운영 경계 |
| Claude 진입점 | `CLAUDE.md` → `@AGENTS.md` | 같은 규칙을 가져오며 공통 규칙을 중복 작성하지 않음 |
| Claude 설정 | `.claude/settings.json` | 기존 모델 설정과 명령·파일 접근 규칙 |
| 선택적 보조 작업 | `.claude/agents/`, `.claude/commands/verify.md`, `impl.md` | 사용자가 요청한 조사·독립 검증·구현안. 메인이 적용 |
| 모의 검사 | `tools/check.py`, `Server/tests/`, `Survey/tests/`, `tools/tests/` | 영역별 검사 실행, 실패 전파, 결과 보관 |
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
| `platform` | 하네스 + 등록·인물 API 제공자 계약 + Tripo 작업자 모의 검사 | 팀원·공통 플랫폼 변경 |
| `all` | 하네스 + Server 전체 + Tripo 작업자 모의 검사 | 공통 경계 변경·CI |

Server 전체에는 대화뿐 아니라 인물 API 제공자·소비자 계약도 들어 있다.
`platform`/`all`의 Tripo는 모의 클라이언트다. 실제 생성·이미지 편집·서버 재시작·모델 다운로드는 실행하지 않는다.
현재 Web 화면 자체의 브라우저 검사와 Unity 컴파일은 이 실행기에 포함되지 않는다.

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

## 7. 이번 정리의 검증 기록

2026-09-13 Windows Python 3.9에서 기본 `dialogue` 검사 69개, `all` 검사 74개를 통과했다.
기존 환경에만 설치된 패키지에 의존하지 않는지 확인하기 위해 새 가상환경에 `requirements-test.txt`만 설치했고,
동일한 74개(하네스·인계 11, 서버 58, 작업자 모의 검사 5)를 모두 통과했다. 건너뛴 검사는 없다.
새 환경에는 torch·sherpa-onnx·모델 가중치를 설치하지 않았다.

새 환경 결과: `tools/_work/checks/20260913T072159Z-all-375eafbf/report.json`.
실행기에는 실패·0개 검사·전부 건너뜀·시간 초과·기존 보고서 보존 검사가 포함된다.
다른 작업 디렉터리에서의 실행 계획, 문서 링크, Claude 설정·명령 형식, CI YAML·Action 버전도 확인했다.
GitHub에서의 실제 workflow 실행과 다음 AI 세션의 자동 지침 로딩은 이번 로컬 검증에 포함하지 않았다.
