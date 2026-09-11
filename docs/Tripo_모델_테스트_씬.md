# Tripo 모델 테스트 씬

최종 갱신: 2026-09-10. T포즈로 전처리한 서로 다른 인물 두 명에 리깅과 9개 동작을 적용했고,
두 모델 모두 편집 모드와 Play Mode에서 재생 검증을 통과했다.
생성 조건·사용량·품질 관찰은 [T포즈 전처리 실험](Tripo_T포즈_전처리_실험.md)에 정리했다.

`Assets/Scenes/Tripo_Model_Test.unity`를 더블클릭하면 로컬 모델과 `Tripo 리깅 검사` 창이 자동으로 열린다.
`Tools > Tripo > Open model test scene` 또는 기존 `Inspect local rig` 메뉴로도 같은 씬을 연다.
씬을 매번 새로 만들지 않으며, **Unity의 Play 버튼 없이도 애니메이션을 미리 볼 수 있다.**

## 사용법

1. `Tripo_Model_Test` 씬을 연다. 이전에 선택한 모델을 복원하며, 유효한 선택이 없으면 폴더 이름 내림차순의 첫 모델을 연다.
2. 검사 창의 `테스트 모델`에서 확인할 모델을 고른다.
3. `생성 원본 / 리깅 원본 / 애니메이션`으로 단계를 바꾼다.
4. `동작`에서 앉기·둘러보기·인사 등 원하는 클립을 선택하고 `재생`을 누른다.
5. 시간 슬라이더, 반복 재생, 일시 정지, 처음으로 버튼으로 자세를 비교한다.

`뼈대 겹쳐 보기`를 켜면 Scene 탭에서 관절을 확인할 수 있다. 뼈를 선택하고 회전 도구로 움직이거나
`수동 회전 되돌리기`로 복원한다. Game 탭에서는 뼈 표시 없이 표면을 볼 수 있다.
`Scene에서 모델 전체 보기`로 화면을 다시 맞추고 `모델 방향`으로 앞·옆·뒤를 비교한다.

Unity의 Play Mode에서도 같은 검사 창을 사용한다. Stop을 누르면 **같은 테스트 씬에 남고 모델이 다시 표시된다.**
다른 씬을 열면 미리보기 모델을 정리한다. 다시 테스트 씬으로 돌아오면 자동으로 불러온다.
검사 창을 닫았으면 `Tools > Tripo > Open model test scene`으로 다시 열 수 있다.

## 다른 이미지로 만든 모델 추가

`tools/_work/tripo_trial_*/` 폴더를 자동으로 검색한다. 새 결과가 만들어졌으면 `목록 새로고침`을 누른다.
다른 위치의 결과는 `모델 폴더 선택`으로 지정한다. 선택한 폴더는 이 프로젝트의 로컬 Editor 설정에 기억한다.

현재 검증 PC의 테스트 목록에는 T포즈 전처리 모델 두 개를 표시한다.

| 테스트 모델 | 내용 |
|---|---|
| `tripo_trial_20260910_tpose` | 기존 인물, 9개 동작 |
| `tripo_trial_20260910_tpose_male` | 추가한 베이지색 카디건 인물, 9개 동작 |

이전 팔짱 자세 실험 폴더에는
`.exclude-from-model-test` 파일을 두어 자동 검색과 저장된 선택 복원에서 제외했다.
기존 GLB와 실험 기록은 디스크에 보존되어 있다.
이 표식은 폴더를 직접 선택할 때와 실제 로더 비교 메뉴에서도 적용된다.

| 화면 | 읽는 파일 |
|---|---|
| 생성 원본 | `generated.glb` |
| 리깅 원본 | `rigged.glb` |
| 애니메이션 | `animated_pack.glb` 우선, 없으면 `animated.glb` |

다른 PC에서 열 때는 해당 로컬 GLB가 필요하다. 사진과 생성 모델은 기존처럼 git에서 제외된다.
모델을 찾지 못하면 검사 창에서 폴더를 선택할 수 있으며, API를 자동 호출하거나 새 모델을 생성하지 않는다.
파일을 다시 생성한 경우 같은 단계에서 다른 단계로 전환했다가 돌아오면 다시 읽는다.

## 다른 PC에서 이어서 확인하기

커밋에는 검사 도구와 카메라·조명·바닥이 있는 씬을 포함한다. 사진, 전처리 결과, GLB,
API 응답과 검증 JSON은 git에서 제외된 `tools/_work/`에만 있으므로 저장소를 복제해도 따라오지 않는다.
모델 자료를 별도로 전달받았으면 폴더 구조를 유지해 `tools/_work/tripo_trial_*/`에 둔다.
9개 동작을 확인하려면 `animated_pack.glb`가 필요하며, 단계별 비교에는 `generated.glb`와 `rigged.glb`도 필요하다.
자료가 없으면 씬은 열리지만 인물은 표시되지 않는다. 새 모델 생성 절차는 위의 전처리 실험 문서를 따른다.

`.exclude-from-model-test` 역시 로컬 모델 폴더의 표식이다. 이전 실험 자료를 옮길 때
이 표식도 함께 유지해야 제외 상태가 유지된다. Unity의 마지막 모델 선택 설정은 PC마다 별도다.

## 구성과 검증

- `Tripo_Model_Test.unity`: 카메라·조명·바닥을 저장한 전용 씬.
- `Tripo_Model_Test_Floor.mat`: 바닥 재질.
- `TripoModelTestScene`: 씬 열기와 Play Mode 전환에 맞춰 검사 창과 미리보기를 준비한다.
- `TripoRigInspector`: 로컬 GLB 로딩, 편집 모드/Play Mode 재생, 뼈대 검사, 모델·클립 선택을 담당한다.
- `TripoAnimationTrial`: 실제 `PersonaSpawner`의 자세 고정/재생 비교를 동일 테스트 씬에서 수행한다.

미리보기 객체는 저장 대상에서 제외한다. 따라서 씬 저장 시 로컬 모델의 메쉬·재질·클립을 씬에 복제하지 않는다.
모델을 바꾸거나 씬을 떠나거나 스크립트를 다시 컴파일하면 이전 미리보기와 임포터를 정리한다.
카메라·조명·바닥은 저장된 씬 설정을 사용하므로 사용자가 편집한 구도를 매번 초기화하지 않는다.

`3. 애니메이션`에서 `Tools > Tripo > Rig view > Validate all animations`를 실행하면 선택한 모델의
모든 클립을 재생하고 관절과 메쉬 변형을 확인한다. 결과는 해당 모델 폴더에 저장한다.

- 편집 모드: `unity_motion_editor_validation.json`
- Play Mode: `unity_motion_validation.json`

실제 로더의 비교는 `Tools > Tripo > Trial > Run latest local model`이다.
이 메뉴는 가장 최근 `animated.glb`를 사용하고 테스트 씬에서 Play Mode를 시작한다.
종료는 Unity Stop 또는 `Tools > Tripo > Trial > Stop playback`으로 한다.
이전의 임시 씬 생성과 원래 씬으로 자동 복원하는 동작은 전용 씬 방식으로 대체했다.

이 씬은 Unity Editor에서 로컬 모델을 점검하는 용도이며 게임 빌드 목록에는 추가하지 않는다.

2026-09-10 검증에서는 두 모델 모두 편집 모드와 Play Mode 각각 9개 클립이 `all_motions_verified`를 통과했다.
단일 클립/9개 클립 모델 전환, 생성·리깅·애니메이션 단계 전환, 씬 재열기, Play/Stop,
스크립트 재컴파일 후 미리보기 복원도 확인했다. 모델을 표시한 채 저장해도 미리보기는 씬 파일에
포함되지 않았으며 씬 파일의 해시가 유지됐다.
씬 동작 검증 기록은 `tripo_trial_20260910_tpose/unity_test_scene_validation.json`,
두 번째 인물 추가 기록은 `tripo_trial_20260910_tpose_male/addition_validation.json`이다.
두 경로는 모두 `tools/_work/` 아래에 있다. 각 폴더에는 편집 모드와 Play Mode의 동작 검증 JSON도 있다.
