# 입장·착석·대화 클립 구성

## 2026-10-26 사용자 선택 Mixamo 동작

사용자가 Mixamo에서 현재 얼굴·몸통 결합 캐릭터에 적용해 내려받은 두 동작을 사용한다.

| 현재 파일 | 사용자 원본 | 사용 구간·설정 |
|---|---|---|
| `MixamoStandardWalk.anim` | `Standard Walk.fbx` | 전체 1.167초, 30fps, 반복·Loop Pose |
| `MixamoWaving.anim` | `Waving.fbx` | 전체 4.733초, 30fps, 한 번 재생 |

출처는 [Adobe Mixamo](https://www.mixamo.com/)다. 두 동작은 기존 CC0 자료와 구별한다.
`With Skin` FBX는 실제 얼굴이 포함된 로컬 자료이므로 저장소에 넣지 않고, Unity에서 독립 Humanoid 동작만 분리했다.
텍스처·메시·외부 자산 참조를 포함하지 않는다. 새 클립은 키프레임 압축을 끄고 가져왔다.

자동 생성한 소스 Avatar의 기준 자세에서는 팔다리 방향과 골반 높이가 잘못 해석됐다.
동일 FBX의 스킨 바인드 T포즈를 복원해 소스 Avatar를 만든 후 동작을 분리했다.
Y의 골반 움직임은 자세에 포함하며, 수평 이동·회전은 기존 경로 기능이 담당한다.
서버 몸체·리그·런타임 Avatar 생성 코드는 변경하지 않는다.

사용자 후속 요청으로 `MixamoWaving`의 손을 흔드는 구간만 조정했다.
오른팔·팔꿈치·손목6개 곡선을 손이 얼굴 옆에 올라간2초 자세를 중심으로 압축했다.
0.7~3.15초 구간에 부드럽게 적용하고, 가운데 구간의 곡선 편차는 원본의42%다.
현재 몸체의 Editor 비교에서 손·손끝의 좌우 이동 폭은 각각 약50%·49% 줄었다.
인사의 시작·끝 자세, 4.733초 길이, 나머지124개 곡선·GUID·Scene_2 연결은 유지한다.

Scene_2에서는 `MixamoStandardWalk` 위에 오른팔·손가락만 `MixamoWaving`으로 겹친다.
몸통·골반·다리·왼팔과 시선 추적은 걷기·기존 제어를 유지한다. 의자 방향으로 돌 때는 진행 중인 인사를 부드럽게 내린다.
따라서 걷는 인사는 의자에 도착하는 시점에 따라 원본4.733초보다 먼저 걷힐 수 있으며, 클립 자체 길이는 유지한다.
착석은 `SitDown.fbx`와 `Sitting Idle`을 유지하고 재생 속도만1.5배로 올렸다. 목/머리 안정화 설정도 유지한다.
이전 `NaturalWalk.anim`·`NaturalGreeting.anim`은 원본 보존용으로 남기고 Scene_2에서는 사용하지 않는다.
검사 결과·이동 속도는 `docs/머리_목_결합_수정_20261024.md`의 2026-10-26 Mixamo 적용 기록을 따른다.

## 2026-10-25 이전 선택

사용자가 기존 걷기·인사의 외관을 다시 지적해 두 동작을 다른 제작자의 클립으로 교체했다.

| 보관 파일 | 제작자·원본 | 당시 처리 |
|---|---|---|
| `NaturalWalk.anim` | [Quaternius Universal Animation Library](https://quaternius.com/packs/universalanimationlibrary.html), 무료 Standard의 `Unity/UAL1_Standard.fbx`, `Armature\|Walk_Loop` | 1.333초 반복 Humanoid 클립 분리. Scene_2 이동 속도0.8m/s |
| `NaturalGreeting.anim` | [Mesh2Motion](https://mesh2motion.org/), `human-addon-animations.glb`의 `Greeting` | Blender4.5에서 해당 동작만 FBX로 내보내 Humanoid로 임포트한 뒤 4.8초 클립 분리 |

두 클립의 라이선스는 CC0다. `LICENSE_CC0.txt`는 Mesh2Motion 원본 자산 저장소의 라이선스 전문이며,
이 라이선스는 아래의 기존 프로젝트 유래 FBX까지 다시 허가하는 의미가 아니다.
Mesh2Motion 앱 원본은 커밋 `653a9698f5f315523072f1c3ff3496100401317b`의
`static/animations/human-addon-animations.glb`다. [자산 저장소](https://github.com/Mesh2Motion/mesh2motion-assets)의
라이선스 기준 커밋은 `c5b0b6c821dbf2fa1dfe49590a6f431fd9f707ce`다.
Quaternius는 2026-10-25 공식 Itch.io의 무료 Standard를 받았다. 다운로드·원본 해시는 `Provenance.json`에 기록한다.
큰 원본 묶음과 임시 FBX는 Git 제외 작업 폴더에만 보관하고, 실행 자산은 두 `.anim`과 `.meta`로 분리했다.

`NaturalGreeting.anim`은 원본의 손 흔들기를 유지하면서 오른손 `Down-Up`·`In-Out`과
오른쪽 아래팔 `Twist In-Out` 곡선 3개를 보정했다. 시작 0.85초·종료 0.55초는 대기 자세의 손목에서
부드럽게 연결하고, 팔을 드는 초반의 과도한 아래팔 비틀림을 낮췄다. 1.2초 이후의 손 흔들기는 유지한다.
클립 길이·손가락·어깨·몸통 곡선과 `.meta` GUID는 그대로다.

현재 착석 몸동작은 아래 `SitDown.fbx`를 유지한다. Scene_2의 `PersonaArrival.sitDownPlaybackSpeed=1.3`으로
3.1초 클립을 약2.385초에 재생하고, 좌석까지의 위치 보간도 같은 시간에 맞춘다. Scene_2의 `PersonaSpawner.stabilizeHeadWhileSitting`을 켜
해당 클립이 섞이는 동안 목·머리의 상대 회전을 시작 대기 자세로 유지한다. 착석 뒤에는 기존 시선·끄덕임으로 복귀한다.
검사·시각 평가 범위는 동작 기록의 후속 절을 따른다.

## 프로젝트에 있던 클립에서 분리한 동작

프로젝트에 이미 포함된 Humanoid FBX에서 필요한 동작을 분리했다. 원본 파일과 기존 참조는 보존한다.
별도 모델·컨트롤러·패키지는 추가하지 않는다.

| 파일 | 프로젝트 원본 | 사용 프레임 / 길이 |
|---|---|---|
| `CalmGreeting.fbx` (이전 선택·현재 미연결) | `Assets/Models/use_animation/Standing Greeting.fbx` | 30~141 / 3.7초 |
| `TurnLeft90.fbx` | `Assets/Models/Left Turn.fbx` | 전체 / 0.93초 |
| `TurnRight90.fbx` | `Assets/Models/Right Turn.fbx` | 전체 / 1.63초 |
| `SitDown.fbx` | `Assets/Models/Sitting.fbx` | 18~111 / 3.1초 |
| `CalmSeatedTalk.fbx` | `Assets/Models/use_animation/Sitting Talking.fbx` | 240~420 / 6초 |

각 파일에서 Avatar를 생성하며, Y 루트 이동은 자세에 포함한다(Bake Into Pose).
수평 이동·방향은 기존 `PersonaArrival`이 경로·좌석 기준으로 제어한다. 선택 클립은 한 번씩 재생한다.
인사는 팔을 올리고 내리는 구간을 포함하고, 대화는 기존 44초 중 작은 한 손 제스처를 사용하는 구간이다.
앉은 대기는 기존 `Sitting Idle` 반복을 유지한다. 대화 동작은 기존 상반신 마스크·답변별 선택 정책을 따른다.

Scene_2의 `useSitDown`은 켜고 `sitDownOffset`은 0으로 둔다. 기존 -0.3m는 Y 루트가 추출된 옛 설정용이다.
착석 발 보정은 앉기 진행률이 아니라 `Sitting Idle`의 실제 혼합 비중을 따른다.
검사 기록은 `docs/머리_목_결합_수정_20261024.md`의 2026-10-25 나머지 동작 절을 참고한다.
