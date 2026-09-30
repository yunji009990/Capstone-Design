# Tripo 머리 생성과 고정 몸체 결합

기준일: **2026-09-29**. `hj`의 머리 생성 변경을 `135fd4da`로 `jw`에 병합했다.
서버 배포·준비 상태는 확인했으며, 신규 웹 등록부터 이 경로의 생성 완료까지는 아직 검증하지 않았다.
전체 서비스 상태는 [현재 구현 현황](현재_구현_현황.md)을 따른다.

## 1. 입력과 결과

운영 등록 웹에서 설문·단일 화자 참조 음성·사진을 제출하면 머리를 생성하고 준비된 몸체에 붙인다.
사진에 보이는 옷과 전신을 그대로 생성하는 방식이 아니다. 몸체·복장·기존 리그는 고정 자산에서 온다.
얼굴과 머리카락이 충분히 크게 보이는 정면 사진으로 시험하되, 원본과의 유사도는 결과를 보고 판정한다.

```text
웹 /publish_direct
  → 등록 API가 인물·음성을 저장하고 UUID4 발급
  → Web._record_and_model → SQLite model_jobs
  → 별도 Survey/model_worker.py
  → head_input.png (머리카락·얼굴·목, 옷·배경 제거)
  → Tripo image_to_model → head_raw.glb
  → Blender head_trim.py → head_trim.glb
  → Blender head_body_merge.py + HEAD_BODY_GLB → model.glb
  → 등록 API /session/{sid}/model 전달 성공 → ready
```

웹은 `/model/{sid}`를 조회해 진행·실패·완료를 표시한다. 사진이 없으면 모델 작업을 접수하지 않는다.
등록 웹은 참조 음성과 설문을 요구한다. 사진 전용 제작 화면·서버 작업 목록을 Unity에서 받는 기능은 별도 미구현 범위다.

## 2. 코드와 처리 범위

| 파일 | 역할 |
|---|---|
| `Survey/core/head_cutout.py` | InsightFace 얼굴 검출, MediaPipe 분할, `portrait` 프레이밍 |
| `Survey/core/model_pipeline.py` | 입력 경로 선택, 단계 기록·복구, 생성·정리·결합·전달 |
| `Survey/blender/head_trim.py` | 색·높이 휴리스틱으로 잔여 의상·목 아래를 정리하고 목 기준 기록 |
| `Survey/blender/head_body_merge.py` | 몸체 목 위치에 머리를 배치하고 지정 머리 본에 연결, GLB 출력 |
| `Survey/model_worker.py` | 설정 읽기, 대기열 claim·heartbeat, 등록 API 전달 |
| `Assets/Editor/HeadPreviewWindow.cs` | 로컬 머리·몸체·결합 GLB 미리보기 |
| `tools/head_cutout.py`, `tools/head_only_trial.py` | 로컬 전처리·머리 단독 생성 실험 |

`head`는 T포즈 생성·얼굴 이식·Tripo 리깅 가능 검사·리깅·동작 요청을 건너뛴다.
몸체의 기존 리그·애니메이션을 내보내며 머리 정점을 지정 머리 본에 붙인다.
얼굴 표정 리그·립싱크·머리카락 물리를 자동 생성하는 기능은 없다.

`full` 경로는 기존 전신 생성·선택적 T포즈/얼굴 이식·리깅·동작 처리로 남아 있다.
입력 단계에 경로를 기록하므로, 이미 시작한 작업은 설정을 바꿔도 새 작업처럼 다른 경로로 전환되지 않는다.

## 3. 설정과 준비물

작업자 설정은 `MODEL_WORKER_ENV`가 가리키는 파일이며 기본은 `Web/.env`다.
실제 키·토큰·참여자 파일은 문서나 Git에 복사하지 않는다.

| 설정 | 코드 기본·의미 | 9월 29일 서버 확인값 |
|---|---|---|
| `TRIPO_PIPELINE` | `head`; 그 외 값은 전신 경로 | `head` |
| `HEAD_BODY_GLB` | 리깅된 몸체 GLB의 별도 경로 | 설정·파일·skin 확인 |
| `BLENDER_BIN` | 비어 있으면 PATH의 `blender` | 설정·Blender 5.0.1 실행 확인 |
| `HEAD_CUTOUT_MODEL` | MediaPipe `selfie_multiclass_256x256.tflite` 경로 | 설정·파일 확인 |
| `HEAD_CUTOUT_FIT` | `portrait` | `portrait` |
| `HEAD_BODY_BONE` | 기본 `Head`; 실제 몸체 리그에 맞춤 | `head.x` |
| `HEAD_BODY_FROM` | 기본 `NeckTwist01`; 목 탐색 시작 본 | `neck.x` |
| `HEAD_BODY_SHARE` | 비어 있으면 목 굵기로 맞춤, 설정하면 키 대비 머리 비율 | `0.125` |
| `HEAD_BODY_YAW` | 머리 방향 보정 각도 | `-90` |
| `HEAD_BODY_OUT_SCALE` | 완성본 전체 배율 | `0.7` |
| `TRIPO_FACE_LIMIT` | `50000`; 현재 워커의 `auto`도 클라이언트 기본 5만 면으로 귀결됨 | `50000` |
| `TRIPO_GEOMETRY_QUALITY` | `standard` | `standard` |
| `MODEL_FIXTURE_GLB` | 사진 대신 준비된 결과를 전달하는 시험용 우회 | 비활성 |

`Web/.env.example`의 본 이름은 일반 예시다. 저장소 `Assets/Models/human/`의 몸체를 사용할 때는
실제 리그에 맞는 `head.x`·`neck.x`를 사용한다. FBX·Blend가 저장소에 있어도 작업자용 GLB가 자동 준비되지는 않는다.
몸체를 바꾸면 비율·방향·스케일을 다시 확인한다. 위 값이 모든 몸체에 맞는 것은 아니다.

**`auto` 구현 주의:** 워커는 이 값에서 `face_limit` 인수를 생략하지만,
`TripoClient.submit_image_to_3d()`의 기본값이 `50000`이어서 API 요청에는 5만 면이 들어간다.
요청 필드를 생략하는 클라이언트 호출은 `face_limit=None`이다. 현재 워커가 그 값을 전달하지 않으므로
`auto`를 무제한 생성으로 안내하지 않는다. 이번 문서 작업에서는 생성 코드를 변경하지 않았다.

작업자 의존성은 `Survey/requirements-worker.txt`다. 실제 처리에는 CPU 얼굴 검출·분할 라이브러리,
InsightFace `buffalo_l` 가중치, MediaPipe 분할 모델과 Blender가 필요하다.
서버 Python 3.12에서 의존성 import를 확인했다. 모의 검사가 Python 3.9에서 통과했다고 실제 ML 패키지 설치까지 검증한 것은 아니다.

## 4. 실패·대체 경로와 품질 한계

- 몸체 GLB 또는 Blender가 없으면 **전신 경로로 내려가고 `pipeline_note`에 이유를 남긴다.**
  `TRIPO_PIPELINE=head` 문자열만으로 머리 경로 준비 완료라고 판단하지 않는다.
- 분할 모델·관련 라이브러리가 없거나 얼굴 검출에 실패하면 머리 전처리에서 실패한다.
  이 경우 원본 사진을 자동으로 Tripo에 보내지 않는다.
- 유료 제출 전에 상태를 저장하고 응답 뒤 task ID를 기록한다. ID 없이 제출 결과가 불명확하면
  `submission_unknown`으로 멈춰 중복 제출을 피한다. 이미 받은 결과는 재사용한다.
- `ready`는 최종 GLB의 등록 API 전달 성공이다. 닮음·목 이음새·피부색·머리카락 품질 승인과는 다르다.
- 생성된 머리 정점 전체가 머리 본을 따른다. 긴 머리와 어깨·몸체의 충돌, 목 경계·색 차이,
  휴리스틱 절단 오류는 실제 결과에서 확인해야 한다.

## 5. Tripo 모델 버전과 별도 실험

현재 운영 요청 기본값은 `Survey/core/tripo.py`의 **`v3.1-20260211`**, 텍스처·PBR 켬,
`texture_quality=detailed`, `texture_alignment=original_image`, 이미지 자동 보정 끔이다.
생성·텍스처 seed는 `20260909`이며, seed 고정이 공급자 출력의 완전한 결정성을 보장하지 않는다.
면 수·형상 품질은 위 환경변수로 바뀐다. 운영 워커는 P2 선택 환경변수를 읽지 않는다.

9월 29일 별도 로컬 P2 API 실험은 성공했지만, 그 실험 설정이 운영 머리 경로에 반영된 것은 아니다.
실험 결과와 운영 기본값, 웹사이트의 품질 등급 이름을 구분한다.
[얼굴 품질 조사](Tripo_얼굴_품질_조사_20260927.md)는 기존 전신 실험의 기각 근거를 보존한다.

## 6. 검사·미리보기

```powershell
python tools/check.py --area platform
python tools/head_cutout.py --image C:/work/photo.png --out tools/_work/head-test/head_input.png --fit portrait
python tools/head_only_trial.py --image C:/work/photo.png --out tools/_work/head-test/trial --prep cutout --fit portrait --dry-run
```

첫 명령은 모의 검사다. 뒤 두 명령은 준비된 로컬 ML 환경·가중치가 필요하고 실제 사진을 처리한다.
`head_only_trial.py --dry-run`은 전처리만 하며, 이를 빼면 유료 머리 생성 요청을 보낸다.
머리 단독 도구는 몸체 결합까지 수행하지 않는다. 실제 등록 DB를 실험용으로 사용하지 않는다.

Unity의 `Tools > 다시봄 > 모델 미리보기`에서 로컬 GLB를 고른다.
결합된 전신은 원본 크기 유지·발바닥 기준 배치를 사용하고, 머리만 있는 파일은 머리 크기를 조정한다.
미리보기 오브젝트는 `DontSaveInEditor`이며 모델을 여는 것만으로 API를 호출하지 않는다.
기존 리깅·9개 동작 실험은 [Tripo 모델 테스트 씬](Tripo_모델_테스트_씬.md)을 사용한다.

9월 29일 모의 검사 작업자 11개와 전체 565개, Unity 컴파일·메뉴 반영을 확인했다.
**신규 웹 등록→머리 생성→몸체 결합→Unity 체험의 전체 실측과 시각 품질 판정은 남아 있다.**
