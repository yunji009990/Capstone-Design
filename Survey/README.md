# Survey — 설문 저장·3D 모델 라이브러리

웹 백엔드(`Web/app.py`)가 `core/`의 저장·조회 기능을 사용하고, 별도 `model_worker.py`가 생성 작업을 실행한다.
2026-09-29 현재 기본 제작은 **머리 생성 → 리깅된 고정 몸체 결합**이다.
[머리 파이프라인](../docs/Tripo_머리_생성_파이프라인.md), [서비스 분리](../docs/웹_Tripo_대화AI_서비스_분리.md),
[팀원 작업 지시서](../docs/Tripo_팀원_AI_작업지시서.md)를 실행 기준으로 삼는다.

원래는 Streamlit 설문 앱이었고 `app.py` 와 `pages/` 가 화면을 그렸다. 지금은
`Web/` 이 그 일을 전부 하므로 화면 쪽은 지웠다. 남은 것은 화면이 없는 부분 —
DB 스키마, 파일 보관, 3D 모델 작업 추적 — 뿐이다. 다시 필요하면 git 이력에 있다.

## 구성

| | |
|---|---|
| `core/database.py` | SQLite. 세션 메타 + 설문 JSON, 소프트/하드 삭제 |
| `core/storage.py` | `data/sessions/<id>/` 에 사진·음성 보관, 폐기 |
| `core/jobs.py` | 영속 생성 작업 등록·재시도 진입점 |
| `core/model_queue.py` | SQLite 대기열, 실행 권한·heartbeat·작업 상태 |
| `core/model_pipeline.py` | head/full 경로 선택, 생성·결합·전달 단계와 외부 task ID 복구 |
| `core/head_cutout.py` | 얼굴·머리카락·목 추출, 의상·배경 제거 |
| `blender/head_trim.py`, `head_body_merge.py` | 머리 정리와 고정 몸체 결합 |
| `model_worker.py` | 웹과 별도 프로세스에서 작업 실행 |
| `core/tripo.py` | Tripo image-to-3D 호출. **키가 없으면 stub** |
| `data/sessions.db` | 자동 생성 |

`data/` 는 참여자 자료라 저장소에 올라가지 않는다(`.gitignore`).

## 3D 모델 키

키가 없으면 모델 작업은 `stub` 상태가 되며 실제 Tripo 요청을 실행하지 않는다.
키는 웹 영역 설정에서 관리하며, 별도 worker가 필요한 설정을 읽는다. 웹을 실행하는 것만으로 worker가 시작되지는 않는다.
실제 head 처리에는 키 외에 몸체 GLB·Blender·얼굴 검출 가중치·MediaPipe 분할 모델도 필요하다.
몸체/Blender 누락은 full 경로 전환, 분할 준비물/얼굴 검출 실패는 head 작업 실패로 구분한다.
9월 29일 서버 준비 상태는 확인했지만 새 head 작업의 웹 등록부터 완료까지 실측은 아직 없다.

```bash
python tools/check.py --area platform
```

위 명령은 저장소 루트에서 실행하는 모의 검사다. 실제 서비스의 독립 기동은 서비스 분리 문서를 따른다.

## 쓰는 쪽

- 등록·조회·폐기 화면 — `Web/static/index.html`, `after.html`, `admin.html`
- 인물 글 만들기 — `Web/survey_v2.py` (`Web/persona.py`·`registration_input.py`는 설문 v2로 대체·삭제)

## 윤리 안전장치

화면이 `Web/` 으로 옮겨갔을 뿐 그대로다.

- 사별 시점 확인 (4주 미만 → 상담 안내 표시)
- 동의 3종 (사진 사용 · 음성 사용 · "실제 고인 아님" 이해)
- 세션 코드로 본인이 직접 폐기 (`/after`)
- 관리자에서 일괄 조회·내려받기·삭제 (`/admin`)
