# Tripo 개발환경과 현재 도구 사용법

기준일: 2026-09-15. 담당 범위·미구현 기능·완료 기준은 [AI 작업 지시서](Tripo_팀원_AI_작업지시서.md)를 따른다.
아래 명령은 기존 도구를 실행하는 방법이다. T포즈 자동화와 사진 전용 웹이 완성되어 있다는 뜻은 아니다.

## 1. 전달할 것

| 자료 | 전달 방법 |
|---|---|
| 프로젝트 기본 자산·Git 이력 | 팀 저장소에서 기준 커밋 clone |
| 현재 분리 코드·문서·설정 예제 | 인수인계 ZIP 적용. 기준 커밋·파일 해시는 ZIP manifest |
| Unity | 2022.3.62f2 설치. 기존 manifest/lock의 패키지 사용 |
| Python | 작업자 모의 검사·실험 도구는 3.9 이상. 웹 환경 재현은 3.12 권장 |
| Node.js | 웹 등록 검사를 포함한 `platform`/`all` 실행에 22 이상 필요. 현재 로컬·CI 기준은 24 |
| Tripo·이미지 편집 키, 서버 접속 권한 | 팀의 비밀 설정 전달 수단으로 별도 전달. 코드·문서·ZIP에는 없음 |
| 실제 사진·T포즈·GLB | 별도 자료 전달 또는 사용할 수 있는 새 사진 준비. ZIP에 없음 |

로컬에서 새 인수인계 묶음을 만들 때:

```powershell
python tools/tripo_handoff.py build --out tools/_work/handoff/tripo_handoff_20260915.zip
```

같은 이름을 덮어쓰지 않는다. 재발급 때는 다른 파일명을 지정한다.
묶음은 `.gitignore`와 명시한 소스 경로만 선택하며 Git의 현재 브랜치·index·커밋을 바꾸지 않는다.
기준 커밋 이후 삭제된 소스가 있으면 삭제 목록과 기준 해시만 전달한다. 삭제된 파일 본문을 ZIP에 넣지 않는다.
폰트 아틀라스 변경, `Scene_1`·`Scene_2` 변경, 사진·GLB·`.env`·작업 DB·Library는 제외한다.

수신자는 **자신의 새 clone**에서 `manifest.json`의 `base_commit`을 기준으로 브랜치를 만든다.
Unity를 열기 전에 ZIP의 `tripo_handoff.py`로 적용한다. 아래는 ZIP과 그 안의 `manifest.json`,
`tripo_handoff.py`를 `C:/handoff/`에 준비한 예시다. 경로는 자기 PC에 맞춘다.

```powershell
$tripoBaseCommit = (Get-Content -LiteralPath C:/handoff/manifest.json -Raw -Encoding UTF8 | ConvertFrom-Json).base_commit
git -C C:/work/Capstone-Design switch -c feat/tripo-studio $tripoBaseCommit
python C:/handoff/tripo_handoff.py check C:/handoff/tripo_handoff_20260915.zip --repo C:/work/Capstone-Design
python C:/handoff/tripo_handoff.py apply C:/handoff/tripo_handoff_20260915.zip --repo C:/work/Capstone-Design
```

도구는 기준 커밋·변경 대상 파일·해시를 모두 확인한 뒤 적용한다. 기존 로컬 수정과 충돌하면 쓰기 전에 중단한다.
다른 작업자가 사용하는 clone에 강제로 덮어쓰지 않는다. 실제 형상 기준은 짧은 커밋명보다 ZIP manifest가 우선한다.
적용된 기준을 확인한 다음 Tripo 기능 개발을 시작한다.

## 2. 키 없이 먼저 확인

명령의 작업 폴더는 수신 프로젝트 루트다. 외부 Tripo나 대화 서버를 호출하지 않는다.

```powershell
python tools/check.py --area platform
```

worker 검사에는 가짜 이미지·Tripo 응답·등록 전달과 임시 DB를 쓴다. 실제 사람 자료를 만들지 않는다.
인수인계 도구 검사는 임시 Git 저장소에서 적용·충돌 중단·파일 격리·해시·Windows 줄바꿈을 확인한다.
등록·인물 조회 계약과 웹의 재작성·무작위 ID 검사도 포함한다. Python 검사 환경과 Node 설치 조건은
[공통 하네스](AI_하네스.md)를 따른다. npm 패키지나 실제 모델은 이 검사에 필요하지 않다.

## 3. Unity에서 기존 결과 확인

1. 수신 프로젝트를 Unity Hub에 추가하고 2022.3.62f2로 연다.
2. `Assets/Scenes/Tripo_Model_Test.unity` 또는 `Tools > Tripo > Open model test scene`을 연다.
3. 전달받은 모델 폴더가 있으면 `모델 폴더 선택`으로 선택한다.
4. `생성 원본 / 리깅 원본 / 애니메이션` 단계와 원하는 동작을 선택한다.
5. Play 버튼 없이도 검사 창의 `재생`으로 동작을 확인한다. `뼈대 겹쳐 보기`로 관절을 본다.

자동 검색 위치는 `tools/_work/tripo_trial_*/`이다. 예상 파일은 다음과 같다.

```text
tripo_trial_<고유이름>/
  generated.glb
  rigged.glb
  animated.glb
  animated_pack.glb
  motion_catalog.json
```

9개 동작에는 `animated_pack.glb`가 필요하다. 없으면 `animated.glb`를 읽는다.
모델 자료가 없으면 배경만 보인다. API를 자동 호출해서 모델을 만들지는 않는다.
옛 원본 자세 모델의 `.exclude-from-model-test` 표식은 자료를 전달할 때 함께 유지한다.
자세한 조작은 [모델 테스트 씬 문서](Tripo_모델_테스트_씬.md)를 참고한다.

## 4. 현재 실험 도구로 모델 만들기

지금까지의 T포즈 전처리는 대화의 `image_gen`에서 수행했다. 아래 Python 명령에 원본 사진을 넣어도
자동으로 T포즈로 바뀌지 않는다. 준비된 JPG/PNG T포즈 이미지를 입력한다.

```powershell
python tools/tripo_trial.py --image C:/work/reference_tpose.png --out tools/_work/tripo_trial_my_first_tpose --dry-run
```

`--dry-run`은 키와 네트워크 호출 없이 요청 설정을 확인한다. 실제 실행은 같은 명령에서 `--dry-run`을 뺀다.
키는 환경변수 `TRIPO_API_KEY`, 없으면 해당 clone의 `Web/.env`에서 읽는다.
성공한 생성·리깅·앉기 결과를 받은 뒤 나머지 동작을 처리한다.

```powershell
python tools/tripo_motion_pack.py --trial-dir tools/_work/tripo_trial_my_first_tpose --dry-run
python tools/tripo_motion_pack.py --trial-dir tools/_work/tripo_trial_my_first_tpose
```

한 클립만 처리할 때는 `--motion look_around` 등의 선택지를 쓴다.
동작 파일을 이미 모두 받았다면 `--build-only`로 API 호출 없이 팩만 합친다.
다른 사진에는 새 폴더를 쓴다. 같은 폴더의 입력·생성 설정을 중간에 바꾸지 않는다.
저장된 task ID가 있는 단계는 재사용하지만, `tripo_trial.py`의 최초 제출 응답을 잃은 경우에는
작업 기록이 생기기 전일 수 있다. 이 경우 Tripo 작업 이력을 확인하고 중복 생성 여부부터 판단한다.

## 5. 팀원용 웹·등록·worker 개발 환경

운영 서비스와 분리할 기본값:

| 항목 | 운영 | 개발 예시 |
|---|---|---|
| 코드 | `~/webapp` | 팀원의 별도 clone 또는 `~/tripo-dev/<담당자>/project` |
| 웹 | 8500 | 18500 |
| 등록 API | 8000 | 18000 |
| 설문/작업 DB·모델 | 운영 Survey/data | 개발 clone의 `tools/_work/tripo_dev/survey` |
| 인물 자료 | `~/server/sessions` | 개발 clone의 `tools/_work/tripo_dev/personas` |
| 설정 | 운영 `.env` | 개발 clone의 `Web/.env` |

같은 서버를 여러 사람이 쓰면 담당자마다 다른 포트와 폴더를 정한다.
운영용 `platform.sh`는 운영 포트·경로 기본값을 사용하므로 아래 개발 환경 기동에는 직접 uvicorn 명령을 쓴다.

Windows PowerShell 예시. `$tripoRepo`는 **팀원 자신의 개발 clone**의 절대 경로다.

```powershell
$tripoRepo = 'C:/work/Capstone-Design'
py -3.12 -m venv "$tripoRepo/tools/_work/tripo_dev/venv-web"
py -3.12 -m venv "$tripoRepo/tools/_work/tripo_dev/venv-registration"
py -3.12 -m venv "$tripoRepo/tools/_work/tripo_dev/venv-worker"
& "$tripoRepo/tools/_work/tripo_dev/venv-web/Scripts/python.exe" -m pip install -r "$tripoRepo/Web/requirements.txt"
& "$tripoRepo/tools/_work/tripo_dev/venv-registration/Scripts/python.exe" -m pip install -r "$tripoRepo/Server/requirements-registration.txt"
& "$tripoRepo/tools/_work/tripo_dev/venv-worker/Scripts/python.exe" -m pip install -r "$tripoRepo/Survey/requirements-worker.txt"
```

`Web/requirements.txt`는 2026-09-13 실제 운영 웹의 Python 3.12.3 환경에서 확인한 직접 의존성 버전이다.
플랫폼별 새 설치까지 모두 검증한 lock 파일은 아니다. Tripo 작업자는 웹/음성/GPU 패키지를 요구하지 않는다.
기존 음성 추출 기능에는 별도 FFmpeg·NeMo 환경이 필요하며 사진 제작 개발의 선행 조건으로 설치하지 않는다.

개발 clone의 `Web/.env`를 다음 내용으로 설정한다. `SESSION_TOKEN`과 `ADMIN_PASSWORD`는 새 개발용 값을 사용한다.
Tripo 키는 처음에는 비워 두어도 된다. 입력한 경로의 `<개발 clone>`은 실제 절대 경로로 바꾼다.

```dotenv
SESSION_URL=http://127.0.0.1:18000
SESSION_TOKEN=<개발용 등록 토큰>
SURVEY_DATA_DIR=<개발 clone>/tools/_work/tripo_dev/survey
ADMIN_PASSWORD=<개발용 관리자 비밀번호>
SURVEY_ACCESS_CODE=
TRIPO_API_KEY=
TRIPO_POSE=preset:sit
```

기동할 각 PowerShell 터미널에서 아래 공통 부분을 먼저 실행한다. 값은 화면에 출력하지 않는다.

```powershell
$tripoRepo = 'C:/work/Capstone-Design'
foreach ($line in [IO.File]::ReadAllLines("$tripoRepo/Web/.env")) {
    if ($line.Trim() -and -not $line.Trim().StartsWith('#') -and $line.Contains('=')) {
        $pair = $line -split '=', 2
        [Environment]::SetEnvironmentVariable($pair[0].Trim(), $pair[1].Trim(), 'Process')
    }
}
```

터미널 1 — 개발 등록 API. 인물 자료 위치를 반드시 개발 경로로 지정한다.

```powershell
$env:SESSION_DIR = "$tripoRepo/tools/_work/tripo_dev/personas"
& "$tripoRepo/tools/_work/tripo_dev/venv-registration/Scripts/python.exe" -m uvicorn registration.app:app --app-dir "$tripoRepo/Server" --host 127.0.0.1 --port 18000
```

터미널 2 — 개발 웹:

```powershell
& "$tripoRepo/tools/_work/tripo_dev/venv-web/Scripts/python.exe" -m uvicorn app:app --app-dir "$tripoRepo/Web" --host 127.0.0.1 --port 18500
```

터미널 3 — 개발 worker:

```powershell
$env:MODEL_WORKER_ENV = "$tripoRepo/Web/.env"
& "$tripoRepo/tools/_work/tripo_dev/venv-worker/Scripts/python.exe" "$tripoRepo/Survey/model_worker.py"
```

각 터미널에서 Ctrl+C로 해당 개발 프로세스만 종료한다. `http://127.0.0.1:18500/status`에서
worker 상태를 확인한다. 키가 비어 있으면 `configured=false`이며 실제 생성이 진행되지 않는다.
현재 웹 화면은 여전히 음성·인물 등록 흐름이다. 사진 전용 `/tripo` 화면은 작업 지시서 단계 A에서 추가한다.
현재 등록 화면은 설문 변경 시 인물 설정을 다시 만들고, `/persona`에서 받은 `survey_revision`을 등록에 보낸다.
세션 ID 입력은 없으며 `/session/start` 응답의 ID를 사용한다. 이전 예시 인물·자동 채우기를 복원하지 않는다.
사용 방법과 부분 실패 처리는 [웹 등록 흐름 개선](웹_등록_흐름_개선.md)을 따른다.

Linux 서버에서도 개발 clone의 별도 venv와 같은 환경값을 사용한다. Python 경로는 `Scripts/python.exe` 대신 `bin/python`이다.
웹을 loopback에 띄운 경우 `ssh -L 18500:127.0.0.1:18500 <팀원 SSH 별칭>`으로 연결하고 로컬 브라우저로 연다.
이 PC의 `raon` 별칭은 팀원에게 자동으로 전달되지 않는다. SSH 계정·접속 권한을 자기 PC에 설정해야 한다.

## 6. 운영 확인과 배포

다음은 **운영 서비스 조회**다. 현재 소유 영역을 확인할 때 사용한다.

```bash
ssh raon bash /home/crc_unity/webapp/Server/platform.sh web status
ssh raon bash /home/crc_unity/webapp/Server/platform.sh session status
ssh raon bash /home/crc_unity/webapp/Server/platform.sh tripo status
```

실제 운영 Tripo 키·관리자 비밀번호는 `~/webapp/Web/.env`, 등록 설정은 `~/webapp/Server/session.env`다.
대화 영역의 `~/capstone-server/dialogue.env`를 제작 설정으로 사용하지 않는다.
공유 서버는 같은 OS 계정과 자원을 쓰므로 강제 접근 차단까지 된 환경은 아니다. 작업 범위와 폴더 구분을 지킨다.

배포 파일 생성은 `python tools/service_bundle.py platform`이다. 이 명령은 ZIP 생성만 하며 원격 서버에 적용하지 않는다.
새 제작 모듈·웹 파일·requirements를 추가하면 배포 목록도 갱신한다.
대상 파일 비교·백업·배포 후 해시/health/로그 확인은 [서비스 분리 문서](웹_Tripo_대화AI_서비스_분리.md)를 따른다.
운영 worker가 처리 중인 작업이 있으면 저장된 단계와 종료 방식을 확인한 뒤 해당 서비스만 관리한다.
인수인계 ZIP은 소스 기준 재현용이다. 운영 배포용 platform ZIP과 혼동하지 않는다.

## 7. 확인한 범위

2026-09-15 웹 등록 개선 후 전체 회귀 검사 135개를 통과했다. 실제 Edge에서는 로컬 등록 응답으로
설문 변경·실패 복구·완료 화면을 확인했고, 운영 서버에는 웹·등록 소스 5개를 적용했다.
대화 AI·TTS·Tripo 작업자와 현재 등록 인물은 유지했다. 자세한 범위·보고서는 [등록 흐름 개선](웹_등록_흐름_개선.md)을 따른다.

아래는 2026-09-13 초기 인계 준비의 기록이다.

- 기존 서버는 웹·등록·worker·대화가 각각 다른 프로세스·Python 환경으로 실행됨을 확인했다.
- 인수인계 도구 5개 검사와 기존 worker 5개 검사, 총 10개가 통과했다. 외부 유료 API를 호출하지 않은 검사다.
- 새 PC의 Unity 최초 import, 이미지 편집 공급자, 신규 유료 생성, 새 웹 UI는 이 인수인계 준비에서 실행·구현한 항목이 아니다.
- 사진과 GLB가 ZIP에 없으므로 기존 두 인물의 시각 검사는 별도 자료가 있어야 한다.
