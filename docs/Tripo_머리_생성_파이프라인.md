# Tripo 머리 생성과 고정 몸체 결합

기준일: **2026-10-01**. `hj`의 `0d41f937`까지 코드와 문서를 대조했다.
9월 29일 병합 이후 전신 작업자 경로 제거, 리그만 포함한 결합 GLB 출력, 통신 재시도·얼굴 절단 보정을 반영했다.
서버 배포·준비 상태와 9월 30일 사용자가 시작한 새 등록의 모델 완료 상태를 확인했다. 새 결과의 외관 품질·VR 체험은 별도다.
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
| `Survey/core/model_pipeline.py` | 머리 생성·정리·결합·전달, 단계 복구와 조회·다운로드 재시도 |
| `Survey/blender/head_trim.py` | 밝기·채도로 의상을 찾고 목 단면으로 절단 상한 검산 |
| `Survey/blender/head_body_merge.py` | 몸체 목 위치에 머리를 배치하고 머리 본에 연결, skin 보존·클립 미포함 GLB 출력 |
| `Survey/model_worker.py` | 설정 읽기, 대기열 claim·heartbeat, 등록 API 전달 |
| `Assets/Editor/HeadPreviewWindow.cs` | 로컬 머리·몸체·결합 GLB 미리보기 |
| `tools/head_cutout.py`, `tools/head_only_trial.py` | 로컬 전처리·머리 단독 생성 실험 |
| `tools/body_prep.py` | 고정 Human FBX의 배율·재질을 정리해 작업자용 몸체 GLB 출력 |

현재 작업자는 머리 생성 경로 하나를 사용한다. T포즈 생성·얼굴 이식·Tripo 리깅·동작 요청은 수행하지 않는다.
몸체의 기존 리그를 보존하며 머리 정점을 지정 머리 본에 붙인다. 결합 GLB는 `export_animations=False`,
`export_skins=True`로 내보낸다. 동작은 Unity의 `PersonaArrival`이 Mixamo 클립으로 적용한다.
얼굴 표정 리그·립싱크·머리카락 물리를 자동 생성하는 기능은 없다.

전신 작업자 경로는 제거했다. 이전 작업 기록의 `input.pipeline`이 `head`가 아니면 오류로 멈춘다.
전신 task ID를 머리 결과로 잘라내지 않으며, 새 머리 생성을 위해서는 새 등록 작업이 필요하다.
로컬 전신·T포즈·동작 실험 도구는 별도로 남아 있다.

## 3. 설정과 준비물

작업자 설정은 `MODEL_WORKER_ENV`가 가리키는 파일이며 기본은 `Web/.env`다.
실제 키·토큰·참여자 파일은 문서나 Git에 복사하지 않는다.

| 설정 | 코드 기본·의미 | 9월 29일 서버 확인값 |
|---|---|---|
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

오른쪽 열은 **9월 29일 확인 기록**이며 10월 1일 서버를 다시 조회한 값이 아니다.
`TRIPO_PIPELINE`, `TRIPO_TPOSE`, `TRIPO_FACE_TRANSPLANT`, `TRIPO_POSE`는 현재 작업자가 읽지 않는다.
이전 `.env`에 남아 있어도 제작 경로를 바꾸지 않는다.

고정 Human 몸체를 새로 준비할 때는 Blender에서 아래 도구를 사용한다. 경로는 준비한 자산 위치로 바꾼다.

```powershell
blender --background --python tools/body_prep.py -- "C:/work/Human.fbx" "C:/work/textures" "C:/work/body.glb"
```

FBX와 스킨 메시의 변환을 적용해 스켈레톤 루트의 `0.01` 배율을 제거하고,
`avatar_BaseColor.png`·`avatar_roughness.png`를 연결한다. 스킨 없는 잔재 메시는 이 준비 단계에서 제거한다.
출력 GLB를 `HEAD_BODY_GLB`로 지정한다. 이 도구는 현재 Human 자산용이며 다른 몸체는 별도 검수가 필요하다.

**`auto` 구현 주의:** 워커는 이 값에서 `face_limit` 인수를 생략하지만,
`TripoClient.submit_image_to_3d()`의 기본값이 `50000`이어서 API 요청에는 5만 면이 들어간다.
요청 필드를 생략하는 클라이언트 호출은 `face_limit=None`이다. 현재 워커가 그 값을 전달하지 않으므로
`auto`를 무제한 생성으로 안내하지 않는다. 이번 문서 작업에서는 생성 코드를 변경하지 않았다.

작업자 의존성은 `Survey/requirements-worker.txt`다. 실제 처리에는 CPU 얼굴 검출·분할 라이브러리,
InsightFace `buffalo_l` 가중치, MediaPipe 분할 모델과 Blender가 필요하다.
서버 Python 3.12에서 의존성 import를 확인했다. 모의 검사가 Python 3.9에서 통과했다고 실제 ML 패키지 설치까지 검증한 것은 아니다.

## 4. 실패·복구와 품질 한계

- 몸체 GLB 또는 Blender가 없으면 **유료 생성 전 준비물 확인에서 실패한다.** 전신 대체 경로는 없다.
- 분할 모델·관련 라이브러리가 없거나 얼굴 검출에 실패하면 머리 전처리에서 실패한다.
  이 경우 원본 사진을 자동으로 Tripo에 보내지 않는다.
- 유료 제출 전에 상태를 저장하고 응답 뒤 task ID를 기록한다. ID 없이 제출 결과가 불명확하면
  `submission_unknown`으로 멈춰 중복 제출을 피한다. 이미 받은 결과는 재사용한다.
- 작업 조회·GLB 다운로드는 408·425·429·500·502·503·504와 네트워크 오류를 일시적 오류로 구분한다.
  연속 실패 시 5→10→20→30초로 간격을 늘린다. 조회는 30분 제한, 다운로드는 최대 5회이며
  400·401·403 등의 오류와 공급자가 명시한 실패 상태는 즉시 실패로 처리한다.
- 시간 초과·다운로드 실패에도 task ID는 보존한다. 관리자 재시도는 같은 작업을 이어받으며,
  유료 POST를 다시 보내지 않는다. 공급자에서 이미 실패한 작업이나 제출 결과 불명 상태의 해결은 별도다.
- `ready`는 최종 GLB의 등록 API 전달 성공이다. 닮음·목 이음새·피부색·머리카락 품질 승인과는 다르다.
- 생성된 머리 정점 전체가 머리 본을 따른다. 긴 머리와 어깨·몸체의 충돌, 목 경계·색 차이,
  휴리스틱 절단 오류는 실제 결과에서 확인해야 한다.

9월 30일 `0d41f937`은 의상 후보에 밝기(`>0.75`)와 HSV 채도(`<0.10`) 조건을 함께 적용했다.
자동 절단은 낮은 쪽에서 가장 가는 단면을 찾아 절단 높이의 상한으로 사용한다. 옷을 찾지 못하면
기본 16% 높이를 사용한다. 밝은 피부가 흰 옷으로 분류돼 얼굴까지 잘리는 문제를 보정한 것이다.
흰머리는 밝고 무채색이므로 의상·머리카락 판정의 한계가 남아 있고 실제 흰머리 표본 검증은 없다.

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
9월 30일 18:36 KST 사용자 새 등록의 상태가 `processing`에서 `ready`로 바뀌고 등록 API에 `has_model=true`가 반영된 것을 확인했다. 새 결과의 외관 품질과 해당 인물로 Unity·VR 체험까지 이어지는 전체 검증은 남아 있다.

## 7. 고정 몸체의 애니메이션 연결 — 2026-09-30

몸체는 `Assets/Models/human/Human.fbx`와 이 자산에서 내보낸 고정 GLB를 재사용한다.
애니메이션을 붙이려고 몸체를 새로 생성하거나 다시 리깅하지 않는다.
`PersonaHumanoid`가 기존 본 이름과 웨이트를 유지하며 몸통·팔다리 22개와 손가락 30개를
Unity Humanoid에 연결한다. 이전 Tripo 리그의 매핑도 남아 있다.

| Unity 부위 | 고정 몸체의 본 |
|---|---|
| 골반 | `root.x` |
| 척추·가슴·윗가슴 | `spine_01.x`, `spine_02.x`, `spine_03.x` |
| 목·머리 | `neck.x`, `head.x` |
| 어깨·위팔·아래팔·손 | `shoulder.l/r`, `arm_stretch.l/r`, `forearm_stretch.l/r`, `hand.l/r` |
| 허벅지·종아리·발·발가락 | `thigh_stretch.l/r`, `leg_stretch.l/r`, `foot.l/r`, `toes_01.l/r` |
| 손가락 | `c_thumb1~3`, `c_index1~3`, `c_middle1~3`, `c_ring1~3`, `c_pinky1~3`의 `.l/.r` 본 |

`PersonaSpawner`가 완성 GLB를 읽고 입장 시작 시 `PersonaArrival`에 넘기면 런타임 Avatar를 만들고,
연결된 대기·인사·걷기·방향 전환·착석 클립을 재생한다.
현재 Scene_2는 착석 후 `Sitting Talking`을 평균 25%로 선택하며 연속 선택을 막는다.
끄덕임·고개 젓기 클립은 선택 연결이다. 아래 날짜별 검사는 해당 시점의 설정으로 구분한다.
시선·호흡·골반 탐색도 같은 매핑을 사용하며, 상체 마스크에 손가락을 포함한다.
Humanoid 클립과 도착 경로는 기존 `PersonaArrival`의 Inspector 연결을 사용한다.
`Human.fbx`의 Generic 임포트 설정만으로 서버 GLB에 Avatar가 전달되는 것은 아니다.
씬에 FBX를 수동 배치한 오브젝트와 `PersonaSpawner`가 로드한 인물은 별개다.

**좌표계 주의:** glTF의 inverse bind 행렬은 파일 공통 좌표계 기준이다. GLB에서
본의 기준 자세를 복원할 때 메시 노드의 `0.01` 배율을 다시 곱하면 팔다리가 축소된다.
스포너는 GLB를 붙인 부모 Transform을 명시해 이 중복 배율을 방지한다.
FBX 검사는 Unity Renderer 기준 bind pose를 사용한다. 여러 메시의 본은 부모부터 복원한다.
Avatar 연결 실패 시 원래 위치·회전을 복원하고 GLB에 포함된 기본 애니메이션 재생으로 복귀한다.
기본 클립이 없는 GLB는 원래 자세를 유지한다.

Unity 메뉴 `Tools > Persona > 고정 몸체 애니메이션 검사`로 다시 확인할 수 있다.
현재 씬을 저장하거나 운영 서버를 호출하지 않고 별도 미리보기 씬에서 실제 메시를 검사한다.
고정 GLB가 `tools/_work/fixed_body_animation/body.glb`에 있으면 FBX와 GLB를 함께 검사하고,
없으면 FBX만 검사한다. 보고서의 `glbPassed`로 수행 여부를 구분한다.

- `tools/_work/fixed_body_animation/report.json`: 클립별 본 회전·메시 변형·과도한 늘어남,
  기준 자세 복원 전후 팔 길이, 시선 본 연결, 반복 연결과 실패 시 자세 복원 결과.
- 같은 폴더의 PNG: 서 있는 Idle·걷기·Sitting Idle의 CPU 스킨 결과를 렌더링한 검사 화면.
  에디터 GPU 스킨 캐시의 첫 프레임을 실제 동작 결과로 오인하지 않도록 `BakeMesh`를 사용한다.
- 9월 30일 최초 고정 몸체 연결 검사에서는 Capstone-Design Unity 2022.3.62f2에서
  FBX·서버 설정의 고정 GLB 각 8개 클립,
  위치·회전·0.7 배율이 있는 GLB, 반복 연결·실패 복원·시선 본 연결 검사를 통과했다.
  새로운 컴파일 오류는 없었다. **Play Mode 전체 도착 경로·생성 머리 결합 결과·VR 기기 검사는 별도다.**
- 공통 모의 검사 `--area all`도 565개 통과, 건너뜀 0개다.
  결과: `tools/_work/checks/20260930T035517Z-all-5e557e65/report.json`.

### 체험 시작 버튼과 입장 — 2026-09-30

Scene_2에서는 Play만 눌러서는 인물이 나타나지 않는다. 모델을 미리 받아 숨겨 두었다가 운영 화면의
**체험 시작**을 누르면 경로 첫 점에서 Idle 1초 → 걷기 → 지정 지점 인사 → 다시 걷기 → 착석한다.
`ExperienceControl.spawner`에 캐릭터 로더를 연결하고 `PersonaSpawner.waitForExperienceStart`를 켰다.
이 설정의 기본값은 꺼짐이므로 다른 씬의 자동 등장 흐름은 유지된다.

- 시작 버튼은 기존처럼 인물 등록·대화 서버 준비가 완료되어야 활성화된다.
- 모델 로딩 중에 시작했다면 완료된 뒤 입장한다. 그전에 종료하면 대기 요청도 취소한다.
- 체험 종료 시 인물을 숨기고 이동·애니메이션을 멈춘다. 같은 인물로 다시 시작하면
  받아 둔 모델을 재사용해 경로 처음부터 재생한다. 크기 배율을 다시 곱하지 않는다.
- 서버 연결 실패·세션 변경 등으로 대화가 끝나거나 체험 컨트롤이 비활성화된 경우에도 입장을 중지한다.
- 체험 시작 시 음성 대화를 여는 기존 시점은 유지한다. Sitting Talking은 착석 후 발화에서만 선택한다.

검증: 실제 Scene_2 Play에서 고정 몸체 GLB와 loopback 모의 대화 연결로 시작 버튼의 이벤트를 호출했다.
시작 전 숨김, 시작 실패, 로드 전 시작·취소, 로드 후 시작, 중복 시작 방지, 종료·재시작,
외부 대화 종료, 컨트롤 비활성화를 통과했다. 최신 `Waving`으로 인사 1회 후 다시 걸어 착석했고,
재시작 시 배율 변화는 0이었다. 공통 검사 `--area all` 565개도 통과했다.
보고서·임시 검사 소스는 Git 제외 `tools/_work/experience_arrival_20260930/`에 보관한다.
실제 음성 서버 답변·마이크 입력·VR 기기 착용 검사는 이번 버튼 검사에 포함하지 않았다.

### 바닥에 그리는 이동 경로 — 2026-09-30

Scene_2의 `입장 동선 / 04_이동 경로 (바닥)`을 `PersonaArrival.groundPath`에 연결했다.
초기 점은 기존 입구·경유지에서 가져왔다. 연결된 경로의 첫 점이 출발 위치, 마지막 점이
의자 옆에 서서 방향을 돌리는 위치다. 기존 `entrance`와 `waypoints` 참조는 대체 경로로 보관하며,
**그린 경로를 사용하는 동안에는 이 예전 마커를 옮겨도 이동 경로가 바뀌지 않는다.**

사용 순서:

1. Play를 종료하고 `Tools > Persona > 이동 경로 그리기`를 연다.
2. `위에서 보기`로 Scene 창을 맞춘다. `바닥 높이 (Y)`는 발이 닿는 바닥 높이다.
3. `경로 그리기`를 선택하고 Scene 창에서 출발 → 도착 방향으로 왼쪽 버튼을 누른 채 그린다.
   버튼을 놓으면 기존 선을 새 선으로 바꾼다. 드래그 중 Esc는 취소, Ctrl+Z는 실행 취소다.
4. `점 편집`에서는 점을 끌어 옮긴다. Shift+클릭으로 끝에 추가하고 Ctrl+클릭으로 중간에 삽입한다.
   선택한 점은 창의 버튼 또는 Delete로 지운다(최소 2개 유지).
5. `걷다가 인사`를 켜고 `Scene에서 인사 위치 찍기`를 누른 뒤 경로 근처를 클릭한다.
   분홍색 `인사` 표시를 직접 끌거나 `인사 위치 (%)`로 조정할 수도 있다. Ctrl+Z로 되돌리고 Esc로 선택을 취소한다.
6. `발밑 위치 미리보기`로 선 위의 진행 위치를 확인하고 `씬 저장` 후 Play에서 `체험 시작`을 누른다.
   현재 Scene_2는 `Idle → 걷기 → 사용자 쪽으로 회전 → Waving → 경로 방향으로 회전 → 걷기 → 의자 방향 정렬 → 부드러운 착석 → Sitting Idle`이다.

`Tools > Persona > 바닥 경로 준비`는 아직 경로가 없는 씬에서 기존 마커를 가져오는 메뉴다.
이미 연결된 경로를 덮어쓰지 않는다. 경로 창에서 `그린 경로 사용`을 끄면 예전 경유지 방식으로 돌아간다.
착석 후 대화 제스처는 아래 `앉은 대화 제스처` 설정을 따른다.

#### 경로 중간 인사

Scene_2는 `PersonaArrival.greetOnPath=true`이며 `greetPathProgress`로 인사 지점을 정한다.
최초 설정은 50%였고 Scene 창에서 사용자가 지정한 최신 위치를 그대로 사용한다.
출발점 Idle 1초는 유지하며 인사는 중간에서 한 번만 한다. `걷다가 인사`를 끄면 기존 입구 인사로 돌아간다.
인사 클립이 없으면 중간 정지를 생략한다. 이 기능은 그린 바닥 경로를 사용하며 예전 경유지 방식은 입구 인사를 유지한다.

위치는 경로 전체 길이에 대한 비율로 저장한다. 경로 모양을 다시 그리거나 점을 옮기면 새 선의 같은 진행 비율로
인사 표시도 옮겨진다. 0%는 출발점에서 인사, 100%는 끝점에서 인사 후 바로 착석 방향으로 전환한다.

이동 거리를 인사 지점에서 정확히 제한하므로 큰 프레임 간격에도 정지 위치를 지나치지 않는다.
멈춘 순간 `Greet Faces User`가 켜져 있으면 `Camera.main`의 위치를 기준으로 방향을 정하고,
`Turn Speed Deg`를 최고 속도로 삼아 가감속하며 제자리에서 회전한 뒤 인사를 재생한다. 꺼져 있거나 카메라가 없으면 진행 방향을 본다.
인사가 끝나면 제자리에서 경로 방향으로 돌아온 뒤 정지했던 누적 거리부터 다시 걷는다.
방향을 맞추는 동안에는 Idle을 재생하며, Idle이 없으면 인사 클립의 시작/마지막 자세를 유지한다.
방향이 먼저 맞아도 서 있는 자세로의 혼합을 마친 뒤 다음 클립을 시작해 전환 중 동작이 끊기는 것을 막는다.

검증 기록: `tools/_work/path_greeting_20260930/`.
실제 Scene_2에서 `걷기 → 인사방향전환 → 인사 → 경로방향전환 → 걷기 → 돌기 → 앉음`을 확인했고,
인사는 1회였다. 최종 인사 전·후 걷기를 각각 180회·191회, 정지/회전/인사 구간을 901회 관측했으며
인사 지점과 발밑 기준 위치 사이 오차는 약 0.00000003m였다. 몸체 FBX·GLB의 10개 클립 검사도 통과했다.
인사 위치 투영·Undo·출발점 지정·클립 누락·기존 입구 인사 복귀를 확인했다.
Scene GUI 마우스 이벤트로 위치 클릭·분홍색 표시 드래그·Undo·Esc 취소를 확인했고 경로 모양은 바뀌지 않았다.
임시 마우스 검사 코드는 `Assets`에서 제거하고 결과 폴더에 보관했다.
기본 검사 497개 통과: `tools/_work/checks/20260930T070025Z-dialogue-45dbfbef/report.json`.

구현은 `PersonaGroundPath`에 로컬 점들을 저장하고, 시작할 때 누적 거리 배열을 만든 뒤 그 선 위의
위치를 보간한다. 몸의 회전 속도와 이동 위치를 분리해 코너에서 선 밖으로 밀려나는 것을 막는다.
GLB의 최종 크기와 처음 재생되는 자세에서 배치 높이를 한 번 정한다. 이동·인사 동안에는 그린 경로의
고정된 바닥 높이를 사용한다. 발의 움직임이나 바닥 콜라이더를 따라 캐릭터 전체의 Y를 바꾸지 않는다.

현재 도구는 **수평 바닥용**이다. 그리기 평면을 고정해 테이블·벽의 콜라이더에 경로가 붙지 않는다.
장애물 회피나 계단·경사면 발 IK는 구현하지 않았으므로 가구를 피하는 동선을 직접 그린다.
급한 직각보다 완만한 곡선으로 그리면 회전이 자연스럽다. 그리는 중에는 녹색, 저장된 선은 청록색,
착석 위치까지의 연결은 노란색으로 표시된다. 경로 표시는 Editor 전용이다.

검증: 코너·끝점 고정·중복점·잘못된 좌표·스냅샷·Undo/Redo·고정 몸체 신발 밑면 높이 보정을 확인했다.
Scene 창에 실제 마우스 이벤트를 보내 새 선 그리기·점 끌기·Shift 추가·Ctrl 삽입·Esc 취소도 검증하고,
검사 후 원래 동선으로 복구했다. 임시 조작 검사는 `Assets`에서 제거해 위 결과 폴더에 소스와 함께 보관한다.
실제 Scene_2 Play에서는 2.363m 경로의 걷기 361회 관측과 착석까지 확인했으며,
선과 발밑 기준 위치 사이 최대 오차는 약 0.00000024m였다(발바닥 충돌·모든 프레임의 미끄러짐 검사는 아님).
기본 검사 497개 통과. 결과는 Git 제외 경로 `tools/_work/ground_path_20260930/`와
`tools/_work/checks/20260930T055710Z-dialogue-e1a3ae20/report.json`에 보관한다.

### Scene_2 애니메이션 교체 — 2026-09-30

사용자가 제공한 `Assets/Models/use_animation`의 동작으로 교체했고 기존 클립은 턴만 사용한다.
`Tools > Persona > 도착 연출 준비`도 아래 파일을 명시적으로 선택한다. 같은 이름의 예전 파일이나
바리스타·가구 자산을 변환하지 않는다. 기존 입구·경유지·착석 지점과 캐릭터 배율은 유지한다.

| 단계 | 연결 |
|---|---|
| 서서 대기 | `use_animation/Idle.fbx`, 입구에서 기본 1초 |
| 인사 | `use_animation/Waving.fbx`, 한 번 |
| 걷기 | `use_animation/Catwalk Walk Forward 03.fbx`, 경로 이동 중 반복 |
| 턴 | 기존 `Assets/Models/Left Turn.fbx` 유지. 30도 미만 의자 방향 정렬은 Idle 사용 |
| 착석 전환 | 1.1초 동안 Sitting Idle로 혼합하고 의자 위치로 보간 |
| 앉은 기본 자세 | `use_animation/Sitting Idle.fbx`, 반복 |
| 대화 동작 | `use_animation/Sitting Talking.fbx`, 평균 답변 4회 중 1회 / 연속 선택 금지 |

서 있는 대기 시간은 캐릭터 로더의 `PersonaArrival > Initial Idle Seconds`에서 바꾼다.
0초 또는 Idle 미연결이면 바로 다음 단계로 넘어간다(중간 인사 설정이면 걷기, 아니면 입구 인사).
새 클립은 Humanoid로 임포트하며 Idle·Catwalk Walk Forward 03·Sitting Idle에
Loop Time을 적용했다. 별도 앉기 클립 대신 Sitting Idle로 천천히 자세를 섞는다.
기존 Sitting·Sitting Clap·Talking·끄덕임·고개 젓기 참조와 자동 끄덕임은 해제했다.
Sitting Talking 연결 보류는 후속 요청으로 해제했고 준비 메뉴도 새 확률 설정을 연결한다.

- 최초 교체 당시 Standing Greeting을 포함한 클립 5개를 고정 FBX와 GLB에서 각각 검사해 총 10개 재생 검사를 통과했다.
  Idle 진입·대기 생략·인사 생략, 반복 Avatar 연결과 실패 복원도 확인했다.
  결과: `tools/_work/animation_replacement/fixed_body_validation.json`.
- 실제 Scene_2 Play에서 `Idle → Standing Greeting → Walking → Left Turn → Sitting Idle`의
  상태 전환 로그와 착석 후 반복 설정을 확인하고 Game 카메라로 렌더링했다.
  Sitting Talking 미연결과 자동 대화 제스처 꺼짐도 확인했다. 검사가 끝난 뒤 Edit Mode로 돌렸다.
- 애니메이션 컴파일·재생 오류는 없었다. Play 전환 중 MCP 연결 해제 오류가 있었지만 연결 복구 후
  위 검사를 완료했다. VR 헤드셋 착용 검사와 모든 프레임의 손·몸통 접촉 품질 판정은 별도다.

이후 사용자 요청으로 인사만 `Waving.fbx`로 교체했다. Humanoid·Create From This Model,
Loop Time 꺼짐으로 임포트했으며 기존 인사 위치·방향 전환·경로는 유지했다.
Waving을 포함한 최신 5개 클립도 고정 FBX·GLB 총 10개 재생 검사와 경로 검사를 통과했다.
Waving 교체 시 결과는 `tools/_work/experience_arrival_20260930/fixed_body_validation.json`,
`path_validation.json`, `play_validation.json`에 있다.
- 씬 검사에서 누락 스크립트·손상 프리팹 0개, 대화 영역 모의 검사 497개 통과를 확인했다.
  결과: `tools/_work/checks/20260930T050957Z-dialogue-11c33b7e/report.json`.

걷기는 추가 요청에 따라 `Catwalk Walk Forward 03`으로 교체했다. 약 1.1초 클립을 Humanoid로
임포트하고 Loop Time을 켰다. 인사 전·후 이동에서 모두 반복 재생하며 이동 속도는 기존 1m/s다.
이동·회전은 `PersonaArrival`이 맡고 Animator의 Root Motion은 끈 상태를 유지한다.
`도착 연출 준비` 메뉴도 새 걷기를 연결하되, 팔 위치를 보정하는 기준 Avatar는 검증된
기존 `Walking.fbx`에서 계속 읽는다. 몸체 기준 자세 자산은 변경하지 않았다.
Catwalk를 포함한 현재 동작 5개와 기존 Walking 회귀 검사를 고정 FBX·GLB에서 실행해 총 12개를 통과했다.
Scene_2 Play에서는 모의 대화 연결과 고정 GLB로 체험 시작·재시작, 인사 전후 걷기와 착석을 확인했다.
기본 검사 497개 통과. 보고서와 검사 화면은 `tools/_work/catwalk_20260930/`, 기본 검사 결과는
`tools/_work/checks/20260930T075054Z-dialogue-95d029f2/report.json`에 있다.

### 앉은 대화 제스처 — 2026-09-30

`Sitting Talking`은 착석 후 NPC 음성이 실제 재생되는 답변에서 한 번만 추첨한다.
상반신에만 겹치며 Sitting Idle은 아래층에서 계속 재생한다. 발화 종료·보류·취소 시
0.3초 동안 걷힌다. 스트리밍 조각이나 같은 답변의 재개는 다시 추첨하지 않는다.

- `Auto Talk Gesture=true`, `Talk Gesture Rate=0.25`: 전체 답변에서 평균 약 4회 중 1회.
  선택 직후 답변은 반드시 건너뛴다. 이 강제 건너뛰기까지 포함해 평균 25%가 되도록
  첫 답변은 1/4, 이후 선택 가능한 답변은 1/3로 추첨한다. 고정된 네 번째마다 실행하지 않는다.
- 체험 종료 때 선택 이력을 초기화한다. 과거 음량·시간 간격 필드는 직렬화 호환용으로만 남는다.
- `Gesture Blend Sec=0.3`에서 제스처 진입·종료 혼합 시간을 조절한다.

선택 정책 10만 회 검사에서 24,905회(24.905%) 선택됐으며 연속 선택·중복·재개·초기화 검사를 통과했다.
Scene_2 Play에서는 고정 GLB와 loopback 합성 PCM으로 이동 중 미실행, 답변당 최대 1회,
연속 선택 금지, 발화 종료·보류 시 중지, 같은 답변 재개 시 미실행, 착석 위치 유지·체험 종료 정리를 확인했다.
Play에서만 선택률을 50%로 올려 선택/건너뛰기를 확실히 관측했고 저장된 Scene_2는 25%다.
실제 운영 TTS·마이크·VR 검사는 아니다.
결과: `tools/_work/sitting_talk_20260930/selection_validation.json`, `play_validation.json`,
`fixed_body_validation.json`(고정 FBX·GLB 각 7개 클립).

### 이동·인사·착석 연결 완화 — 2026-09-30

사용자가 그린 경로·인사 위치·의자 위치·배율과 Catwalk·Waving·Sitting Idle 연결은 유지한다.

- `Walk Acceleration=2.5`, `Walk Deceleration=3.5`: 출발 시 가속하고 인사 위치·경로 끝 앞에서 감속한다.
  걷기 클립의 진행 속도도 이동 속도에 맞춘다. 경로와 정지 지점을 지나치지 않는다.
- `Path Facing Look Ahead=0.3`: 현재 위치 앞뒤 0.3m의 경로를 함께 보고 코너 방향을 부드럽게 잇는다.
- `Blend Sec=0.4`: 자세 사이를 SmoothStep 곡선으로 섞는다. 나가는 클립의 반복·정지 시각과 속도를 보존하고,
  Playable 시간을 직접 지정하여 그래프 자동 진행과 중복하지 않게 한다.
- 인사 전후 제자리 회전은 시작·끝에서 각속도를 줄인다. 의자 방향 정렬이 30도 미만이면 Idle을 쓰고,
  큰 회전에서 기존 턴 클립을 사용할 때는 끝까지 재생하며 회전 시간을 맞춘다.
- `Sit Transition Seconds=1.1`: 서 있는 자세에서 Sitting Idle로 내려앉으며 위치도 같은 시간 동안 의자로 보간한다.
  기존 Sitting 클립은 고정 몸체에서 종료 골반 높이가 Sitting Idle보다 약 0.34m 높아 연결하지 않았다.

Play에서 `서서대기 → 걷기 → 인사방향전환 → 인사 → 경로방향전환 → 걷기 → 돌기 → 앉는중 → 앉음`을 확인했다.
최대 이동 속도 약 1.0001m/s, 최대 회전 속도 약 111.1도/초, 착석 중 관측 위치 변화 최대 약 0.0057m,
최종 의자 위치 오차 0m였다. 발 접지 IK·가구 충돌 회피는 없으며 모든 프레임의 접촉 품질 검사는 별도다.
결과: `tools/_work/arrival_smooth_20260930/`와 위 Play 보고서.
기본 검사 497개 통과·건너뜀 0개: `tools/_work/checks/20260930T082026Z-dialogue-86c9a4b9/report.json`.

### 정지 자세·발목 보정과 고정 높이 — 2026-09-30

이동 → 인사 → 이동에서의 정지 자세와 발목 기준 자세를 수정했다.

- 감속할 때 Idle의 비중이 커지고 출발할 때 Catwalk의 비중이 커지도록 섞는다.
  인사 위치의 XZ와 Waving 연결은 유지한다.
- Walking 기준 회전을 다리까지 복사하면서 생기던 왼쪽 발목 약 37도 차이를 바로잡았다.
  다리 12개 본은 원본 몸체의 바인드 자세를 사용하며 팔·몸통·머리 보정과 자산 GUID는 유지했다.
- 신발 밑창에 맞춰 매 프레임 캐릭터를 올리고 내리는 **바닥 자동 추종은 사용자 요청으로 제거했다**.
  사용자 재생에서 캐릭터가 위아래로 튀는 문제가 확인됐다. `PersonaSoleSampler`, 바닥 조회,
  LateUpdate 높이 보정과 관련 Inspector 설정을 제거하고, 그 용도로 추가한 카페 콜라이더도 정리했다.

이제 시작할 때 첫 자세의 배치 높이를 한 번만 정하고 이동·정지·인사·회전 동안 같은 높이를 유지한다.
착석할 때만 기존 방식대로 의자 위치까지 부드럽게 내려앉는다. 경로의 XZ·인사 위치·캐릭터 배율과
발목 기준 자세 수정은 유지한다. 바닥 높이는 경로 편집 창의 `바닥 높이 (Y)`에서 조절한다.

자동 추종이 있던 당시의 보고서는 `tools/_work/foot_contact_20260930/`에 과거 기록으로 보관한다.
당시 낮은 쪽 밑창 간격 3mm 검사는 캐릭터 전체 높이의 안정성을 검증한 것이 아니었다.
현재 자동 추종 제거 확인은 `tools/_work/foot_contact_removed_20260930/`를 따른다.
실제 생성 캐릭터의 이동·정지·인사·회전 357회 관측에서 루트 Y 변화는 0m였고, 인사 1회 후 착석까지 완료했다.
경로 검사와 공통 계약 검사 565개도 통과했다(건너뜀 0개).
기본 검사 결과: `tools/_work/checks/20260930T092230Z-all-75b3fdee/report.json`.

### 앉은 고개 높이 조절 — 2026-09-30

Scene_2의 캐릭터 로더 `PersonaSpawner > 시선 > Seated Head Lift Deg`를 **15도**로 설정했다.
0이면 원래 자세, 값을 높이면 고개를 더 든다(최대 30도). 다른 씬의 기본값은 0이다.
착석 전환·앉은 상태에서만 적용하고 걷기·인사에는 적용하지 않는다.
시선 추적과 호흡 이후 목·머리가 보정량을 나눠 적용하며, 약 0.25초의 반응 시간으로 부드럽게 올라간다.
체험 종료 시 보정 상태를 초기화한다. 머리 위치를 직접 이동시키거나 앉은 클립 자체를 수정하지 않는다.

로컬 고정 몸체 GLB의 실제 Play에서 보정량 14.995도, 보정 방향 오차 0도, 팔·골반 이동 0m를 확인했다. 걷기 미적용·반복 프레임 누적 없음·체험 종료 초기화를 통과했다. 생성 머리를 포함한 외관·HMD 착용 검사는 별도다. 결과는 `tools/_work/seated_head_20260930/`에 보관한다.
공통 검사 565개 통과·건너뜀 0개: `tools/_work/checks/20260930T083320Z-all-acb4cf76/report.json`.

### Mixamo와 게임의 팔 위치 차이 수정 — 2026-09-30

새 클립 연결 뒤에도 게임에서 손이 몸통에 들어가는 현상을 확인했다. 같은 Human용 Walking의
원본 FK와 런타임 Humanoid를 비교하니, 기존 구현이 **스킨 바인드 자세를 Humanoid의 기준 자세로
그대로 사용한 것**이 원인이었다. 일부 비교 프레임에서 손의 가로 위치가 약 8~12cm 안쪽으로
달라졌다. 앞선 컴파일·클립 재생 검사는 이 자세 차이를 검증하지 못했다.

`PersonaReferencePoseSetup`이 `Human.fbx`와 `use_animation/Walking.fbx`의 Avatar에서 기준 자세
차이를 구해 `Assets/Models/human/HumanHumanoidReferencePose.asset`에 저장한다.
`PersonaArrival.referencePose`에 연결하고, 고정 몸체의 Avatar를 만들기 전에 적용한다.
FBX·GLB의 로컬 본 축은 달라서 정면·위쪽을 기준으로 한 공통 좌표의 회전 차이를 사용한다.
몸체 메시·웨이트·본 길이·모델 파일과 기존 턴 클립은 유지한다. 이전 Tripo 리그에는 적용하지 않는다.
`Tools > Persona > 도착 연출 준비`에서 같은 보정을 다시 만들고 연결할 수 있다.

- 원본 FK·보정 전 Avatar·원본 기준 자세·보정한 GLB를 같은 걷기 시각 4개에서 비교했다.
  비교 보고서: `tools/_work/animation_pose_comparison/report.json`.
- 최종 고정 몸체 검사에서 FBX·GLB 각 5개 클립과 반복 연결·실패 복원을 통과했다.
  걷기의 검사 프레임에서 배율을 제외한 최소 양손 간격은 두 형식 모두 약 **0.5594m**다.
  원본 동작을 기준으로 0.50m 미만이면 실패하는 회귀 검사를 추가했다.
  결과: `tools/_work/animation_pose_comparison/fixed_body_validation.json`.
- 실제 Scene_2 Play에서 보정이 연결된 걷기를 일시정지해 손이 몸 밖에 있는 프레임을 확인하고,
  재개 후 Sitting Idle까지 도달했다. 화면: `tools/_work/animation_pose_comparison/game_walk_corrected.png`.
  비교용 FBX·Editor 임시 도구는 제거했고, 재현용 코드는 같은 Git 제외 폴더에 보관했다.
- 컴파일 오류 0개, 누락 스크립트·손상 프리팹 0개, 대화 모의 검사 497개 통과.
  결과: `tools/_work/checks/20260930T053018Z-dialogue-b80b9717/report.json`.
  모든 프레임의 메시 충돌을 판정한 것은 아니며 VR 헤드셋 착용 검사는 별도다.

### Play 중 리깅 확인

`Tools > Persona > 리깅 보기`를 열고 Play하면 현재 `PersonaSpawner`가 불러온 캐릭터의
실제 Skin 본과 부모 연결을 Game/Scene 화면에 겹쳐 표시한다. 창을 열어 둔 상태에서
모델 로드를 기다리며, 캐릭터가 교체되면 새 모델을 따라간다.

- `F8` 또는 `리깅 표시`로 켜고 끈다. 관절점·표시 두께·몸을 투과하는 표시도 조절할 수 있다.
- 중앙은 노랑, 캐릭터의 왼쪽은 하늘색, 오른쪽은 주황색이며 선택한 뼈는 흰색이다.
- `확인할 뼈`에서 이름과 부모·현재 위치를 읽고 Inspector에서 선택할 수 있다.
  전체 이름 표시는 `뼈 이름 표시 (Scene)`를 켜고 Scene 탭에서 확인한다.
- 스킨 메시의 본 개수와 Unity Humanoid에 연결된 본 개수를 구분해서 표시한다.
  Skin에 없는 얼굴 표정용 뼈를 새로 만들어 보여주는 기능은 아니다.
- 표시용 메시·재질은 편집 도구가 소유하며 창을 닫거나 Play를 종료하면 제거한다.
  이 도구를 여는 것만으로 모델을 생성하거나 서버 API를 호출하지 않는다.

9월 30일 Scene_2의 실제 Play에서 Skin 본 60개·부모 연결 59개·Humanoid 매핑 52개를 확인했다.
Game 카메라 렌더와 표시 끄기를 확인했으며, 리깅 표시 도구의 새 컴파일 오류는 없었다.
대화 영역 기본 검사는 497개 통과했다(`tools/_work/checks/20260930T043535Z-dialogue-07c0e4b0/report.json`).
VR 헤드셋을 착용한 검사는 수행하지 않았다.
