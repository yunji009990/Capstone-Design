# Survey — 설문 저장·3D 모델 라이브러리

웹 백엔드(`Web/app.py`)가 `core/`의 저장·조회 기능을 사용하고, 별도 `model_worker.py`가 생성 작업을 실행한다.
2026-10-01 코드 기준 제작은 **머리 생성 → 리깅된 고정 몸체 결합**이다. 이전 전신 작업자 경로는 제거했다.
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
| `core/model_pipeline.py` | 머리 생성·결합·전달, 외부 task ID 보존과 조회·다운로드 재시도 |
| `core/head_cutout.py` | 얼굴·머리카락·목 추출, 의상·배경 제거 |
| `blender/head_trim.py`, `head_body_merge.py` | 머리 정리와 고정 몸체 결합 |
| `../tools/body_prep.py` | 고정 Human FBX의 배율·재질을 정리해 작업자용 몸체 GLB 준비 |
| `model_worker.py` | 웹과 별도 프로세스에서 작업 실행 |
| `core/tripo.py` | Tripo image-to-3D 호출. **키가 없으면 stub** |
| `data/sessions.db` | 자동 생성 |

`data/` 는 참여자 자료라 저장소에 올라가지 않는다(`.gitignore`).

## 3D 모델 키

키가 없으면 모델 작업은 `stub` 상태가 되며 실제 Tripo 요청을 실행하지 않는다.
키는 웹 영역 설정에서 관리하며, 별도 worker가 필요한 설정을 읽는다. 웹을 실행하는 것만으로 worker가 시작되지는 않는다.
실제 head 처리에는 키 외에 몸체 GLB·Blender·얼굴 검출 가중치·MediaPipe 분할 모델도 필요하다.
몸체 GLB·Blender가 없거나 분할 준비물·얼굴 검출에 문제가 있으면 작업이 실패한다. 전신 자동 대체는 없다.
이전 전신 방식으로 접수된 작업은 머리 작업으로 이어받지 않는다. `TRIPO_PIPELINE`, `TRIPO_TPOSE`,
`TRIPO_FACE_TRANSPLANT`, `TRIPO_POSE`는 현재 작업자가 읽지 않는 이전 설정이다.
최종 GLB는 skin을 보존하고 애니메이션 클립은 내보내지 않는다. Unity에서 Mixamo 동작을 연결한다.
9월 30일 새 웹 등록의 모델 완료·등록 API 전달을 확인했다. 외관 품질·VR 체험은 별도 검증 범위다.

조회·다운로드의 408·425·429·500·502·503·504와 네트워크 오류는 5→10→20→30초 간격으로 재시도한다.
작업 조회는 30분 제한, 다운로드는 최대 5회다. 유료 제출 POST는 자동 재시도하지 않고,
제출 결과가 불명확하면 `submission_unknown`으로 멈춘다. 자세한 복구 조건은 머리 파이프라인 문서를 따른다.

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
