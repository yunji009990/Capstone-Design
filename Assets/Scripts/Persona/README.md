# 등록 캐릭터 로딩·이동·애니메이션 설정

기준일: 2026-10-01. 현재 등록 체험 씬은 `Assets/Scenes/Scene_2.unity`다.
등록 서버의 GLB를 glTFast로 불러오고 고정 몸체의 뼈대에 Unity Humanoid·Mixamo 동작을 연결한다.
등록 순서는 [Scene_2 체험](../../../docs/Scene_2_웹설문_테스트.md), 본 매핑·검사 이력은
[머리 파이프라인](../../../docs/Tripo_머리_생성_파이프라인.md)을 따른다.

## 1. 현재 데이터 흐름

```text
웹 설문·참조 음성·사진 등록 → UUID4 발급 + 별도 모델 작업 접수
  → Tripo 머리 생성 → Blender 정리·고정 몸체 결합
  → 등록 서버에 리그 포함 model.glb 전달
  → Unity에서 현재 세션·has_model 확인 → GLB 다운로드·로드
  → 체험 시작 → 모델 표시·경로 이동·인사·착석 + 음성 대화
```

`PersonaSpawner`는 같은 씬의 `DialogueVoiceClient`가 있으면 주소·토큰·세션을 따라간다.
없으면 자체 `serverUrl`·`token`으로 `/session/current`와 `/session/{id}/model.glb`를 조회한다.
사진·모델 없는 등록은 캐릭터 로드 조건을 충족하지 않는다. 실제 키·세션 ID는 문서에 기록하지 않는다.
현재 동작은 HTTP 기반이다. 이전 Streamlit/Meshy·`current_session.txt`·로컬 자료 폴더 안내는 적용하지 않는다.

## 2. 씬 연결과 자주 조절하는 값

Scene_2에 있는 캐릭터 로더와 입장 동선을 사용한다. glTFast 6.14.1은 프로젝트에 포함돼 있다.

| 컴포넌트·설정 | 의미·현재 Scene_2 기준 |
|---|---|
| `PersonaSpawner.spawnPoint` | 모델 배치 기준 위치·회전 |
| `targetHeightMeters` | 키 자동 보정. Scene_2는 `0`으로 원본 크기 유지 |
| `scaleMultiplier` | 얼굴·머리·몸체 전체의 추가 배율. Scene_2는 `1.2` |
| `seatedHeadLiftDeg` | 착석 후 고개 높이 보정. Scene_2는 `15`도 |
| `arrival` | 이동·인사·착석을 제어하는 `PersonaArrival` |
| `waitForExperienceStart` | Scene_2는 켬. 로드를 마쳐도 체험 시작 전에는 숨김 |
| `ExperienceControl.spawner` | 체험 시작·종료를 캐릭터 표시와 입장 연출에 연결 |
| `PersonaArrival.referencePose` | 고정 Human의 Humanoid 기준 자세 보정 자산 |
| `PersonaArrival.groundPath` | 직접 그린 바닥 경로 |
| `greetOnPath`·`greetPathProgress` | 경로 도중 인사 여부·전체 길이 대비 인사 위치 |

`scaleMultiplier`는 몸체와 얼굴에 함께 적용한다. 값을 저장하려면 Edit Mode에서 수정한다.
Play 중 수정은 확인용이며 종료하면 되돌아갈 수 있다. AI_Response_Test·Tripo_Model_Test는
목적과 접속 설정이 다른 씬이므로 설정을 서로 복사하지 않는다.

## 3. 경로와 애니메이션

`Tools > Persona > 이동 경로 그리기`에서 바닥 경로와 인사 지점을 편집한다.
Scene_2의 `입장 동선 / 04_이동 경로 (바닥)`이 현재 연결된 경로다. 가구를 피하는 경로를 직접 그린다.

```text
Idle → Catwalk Walk Forward 03 → 사용자 쪽 회전 → Waving
     → 경로 방향 회전 → Catwalk Walk Forward 03
     → 의자 방향 정렬 → 착석 전환 → Sitting Idle
```

실제 이동은 코드가 담당하며 Animator Root Motion은 끈다. 속도에 맞춰 걷기 재생과
Idle/Walk 비율을 조절하고 PlayableGraph로 동작을 섞는다. 회전은 경로·최종 착석 방향을 따라 보간한다.
현재 NavMesh 장애물 회피나 매 프레임 신발 바닥 추종은 없다. 9월 30일 사용자 요청으로
자동 바닥 추종을 제거했으며 이동·인사·회전 중 루트 높이를 고정한다.

착석 후 기본은 `Sitting Idle`이다. NPC 음성이 실제 재생되는 답변에서 `Sitting Talking`을
평균 25%로 선택하고 다음 답변은 강제로 건너뛴다. 정확히 매 네 번째마다 실행하는 규칙은 아니다.
같은 답변의 음성 조각이나 재개는 다시 추첨하지 않는다. 상체 마스크로 다리는 앉은 자세를 유지한다.

## 4. 시작·종료와 미리보기

- `체험 시작`을 누르면 음성 대화와 캐릭터 입장을 시작한다. 모델 로드 중이면 시작 요청을 보관한다.
- `체험 종료`·연결 실패 시 인물을 숨기고 연출을 멈춘다. 다시 시작하면 같은 모델을 경로 처음부터 사용한다.
- `Tools > 다시봄 > 모델 미리보기`는 로컬 GLB 확인용이며 여는 것만으로 Tripo API를 호출하지 않는다.
- `Tools > Persona > 리깅 보기`에서 Play 중 실제 Skin 본과 Humanoid 연결을 표시한다. `F8`로 표시를 전환한다.
- `Tools > Persona > 고정 몸체 애니메이션 검사`는 별도 미리보기 씬에서 FBX와 준비된 GLB의 변형을 검사한다.

현재 머리 결합 GLB는 skin을 보존하고 클립은 넣지 않는다. 걷기·인사·착석은
`Assets/Models/use_animation/` 등의 Unity 클립으로 재생한다. 기존 턴 클립과 기준 자세용 Walking은 별도 참조를 유지한다.
얼굴 표정 리그·립싱크·머리카락 물리는 자동 생성하지 않는다.

## 5. 문제 확인 순서

| 증상 | 먼저 확인할 것 |
|---|---|
| Play만 했는데 인물이 안 보임 | 체험 시작 버튼, 모델 생성 완료 여부 |
| 생성 완료인데 Unity에 안 나옴 | 등록 서버 `has_model`, 세션 일치, GLB 다운로드·로드 오류 |
| 너무 크거나 작음 | `targetHeightMeters`·`scaleMultiplier`를 함께 확인 |
| 팔다리가 접히거나 손이 몸을 통과함 | 본 매핑·기준 자세·클립 Avatar 확인. 몸체 준비 시 `tools/body_prep.py` 사용 |
| 인사 위치·방향이 이상함 | 경로 점, 인사 진행률, 사용자 카메라·최종 착석 방향 |
| 앉은 뒤 고개가 낮음 | `seatedHeadLiftDeg` 확인 |
| 얼굴이 잘리거나 목 경계가 이상함 | 서버의 머리 정리·결합 결과 확인 |

9월 30일 실제 Play·모의 음성 검사 범위와 남은 VR 장치 검증은
[현재 구현 현황](../../../docs/현재_구현_현황.md)에 있다. 이번 문서 갱신에서 Unity를 다시 실행한 것은 아니다.
