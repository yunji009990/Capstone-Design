# ModernVR 도입 계획 — VR 상호작용 기반 교체

작성일: 2026-09-11
상태: 에셋 구매·다운로드 완료(Asset Store 캐시에 있음), 프로젝트 임포트 전

2026-09-13 `hj` → `jw` 병합 반영: 대화 클라이언트는 `DialogueVoiceClient`로 전환됐고 TTS는 꺼져 있다. 아래 ModernVR 단계는 도입 계획이며, 이번 병합에서 XR 로더 전환이나 리그 교체는 수행하지 않았다.

---

## 1. 왜 바꾸나

지금 상호작용 기반은 Meta XR SDK 77 에 딸린 Interaction SDK 다. 리그
(`Assets/Prefabs/Player_CRC.prefab`, OVRCameraRig + OVRHand)는 들어 있지만 실제로 붙은
상호작용이 없다.

| 항목 | Scene_1 현황 |
|---|---|
| 잡기 (Grabbable) | 0건 |
| 이동 (Teleport / Locomotion) | 0건 — 운영자가 `VRMoveControl` 로 체험자 자리를 0.1m 씩 밀어 옮긴다 |
| 원거리 선택 (Ray / Poke) | 0건 |
| 콜라이더 | MeshCollider 0, BoxCollider 1 — 카페가 통과된다 |

체험자는 스스로 움직이지 못하고, 잔·사진·유품을 집어 회상을 여는 설계는 비어 있다.
Interaction SDK 는 Interactor·Interactable·Grabbable 을 손으로 조립해야 해서 진도가 안
났다. ModernVR 은 플레이어 리그·물리 손·잡기·이동을 한 묶음으로 준다.

## 2. 패키지 안에 뭐가 있나

`Kamoo/ScriptingPhysics/ModernVR - VR Interaction Engine.unitypackage` (v1.1.0, 96MB) 를
임포트 전에 열어 본 것.

- 플레이어: `Prefabs/Player.prefab` + `Player Spawn.prefab`. Rigidbody 몸(`PhysicsBody`),
  ConfigurableJoint 물리 손(`PhysicsHand`), 이동·회전·앉기·점프(`Movement` `SmoothTurn`
  `SnapTurn` `Crouch` `Jump` `Height`)
- 잡기: `Grabbable` + GrabPoint 4종(`SmoothGrab` `AutoGrab` `SphereGrab` `LineGrab`),
  자동 손 자세(`AutoHandPose`), 원거리(`DistanceGrab` `ForceGrab`), 소켓(`Pocket`)
- 소품 프리팹: `Props/Mug` `Table` `Drawers` `Door` 등. 총·검·사다리·찌르기도 있는데 안 쓴다
- UI: `UIPointerVR` — VR 안에서 uGUI 를 누른다
- 입력: **Input System** (`Inputs/ControllerSampleActions.inputactions`), **OpenXR**
  (동봉된 `Packages/manifest.json` 이 `com.unity.xr.openxr 1.14.3` 을 요구)
- 진동: `Hand.cs` 가 `InputDevices.SendHapticImpulse` 를 쓴다 — 제공자 무관
- 문서: `Documentation/ModernVR Documentation.pdf` 26쪽. 설치·잡기·손 자세·주머니·버튼 이벤트

**컨트롤러 전용이다.** 입력이 전부 컨트롤러 버튼·스틱이고 핸드 트래킹 경로가 없다.
지금 리그의 OVRHand(핸드 트래킹)는 ModernVR 로 가면 쓰지 않는다. 체험 방식이
"손으로" 에서 "컨트롤러 쥐고" 로 바뀐다는 뜻이다.

## 3. 지금 프로젝트와 맞는 것, 안 맞는 것

| 항목 | 상태 |
|---|---|
| Unity 2022.3.62f2 | 에셋 최소 요구 버전(2022.3.62f2)과 같다 |
| URP 14 | 지원. 에셋 재질은 Built-in 이라 임포트 뒤 Render Pipeline Converter 를 한 번 돌린다 |
| Input System 1.14.0 | 이미 설치돼 있다(Meta SDK 의존성). `activeInputHandler: 2` (둘 다) |
| OpenXR 1.14.3 | 설치돼 있으나 **활성 로더는 Oculus** (`Assets/XR/Loaders/OculusLoader`). 전환해야 한다 |
| Meta XR SDK 77 | OVRManager 는 OpenXR 백엔드로도 돈다. 남길지는 1단계에서 판단 |
| `Camera.main` 의존 | `OperatorHUD` 는 Start 에서 한 번만 찾고 없으면 포기한다. `ExperienceCues` 는 붙을 때까지 매 프레임 본다 |
| `OVRInput` 의존 | `MMF_OVRHaptics` (Feel 진동). OpenXR 로 가면 `InputDevices.SendHapticImpulse` 로 바꾼다 |
| 콜라이더 | 카페에 거의 없다. 물리 몸이 바닥을 뚫는다 → 바닥·벽·테이블·의자에 넣어야 한다 |

## 4. 단계

### 1단계 — 기반 교체

브랜치 `modernvr` 에서만 한다. `main`/`hj` 는 건드리지 않는다.

1. XR Plug-in Management 에서 OpenXR 선택 → Project Validation "Fix all" → Interaction
   Profile 에 Meta Quest Touch 추가
2. ModernVR 임포트 → `Window/ModernVR/Start Screen` → "Apply Recommended Physics Settings"
   → Render Pipeline Converter
3. **`Demo/DEMO.unity` 를 헤드셋에서 먼저 돌린다.** 여기서 잡기·이동이 안 되면 더 가지 않는다
4. Scene_1: `Player_CRC` 는 비활성만 하고 `Player Spawn` 을 지금 리그 자리에 둔다.
   점프·달리기는 끈다(`Jump` 떼기, `Movement` speed 낮추기). 회전은 `SnapTurn` — 부드러운
   회전은 멀미가 난다
5. 바닥·벽·테이블·의자 콜라이더
6. 기존 것이 그대로 도는지 확인 — 대화(`DialogueVoiceClient`), 인물 스폰(`PersonaSpawner`),
   운영자 화면(`OperatorHUD` 의 시야 캡처), 연출(`ExperienceCues`)
   - Player 가 실행 중에 스폰되므로 `OperatorHUD` 는 카메라를 늦게 찾도록 고치거나,
     `Player.prefab` 을 씬에 직접 놓는다. 어느 쪽이 되는지는 해 봐야 안다
   - `MMF_OVRHaptics` 를 `InputDevices.SendHapticImpulse` 로

통과 기준: 헤드셋에서 잔을 집어 테이블에 놓을 수 있고, 스틱으로 인물 앞까지 걸어갈 수
있고, 대화·인물·운영자 화면이 전과 똑같이 되면 통과.

### 2단계 — 회상 소품

- 잔·사진·유품에 `Grabbable` + `AutoGrab` (손 자세를 비워 두면 자동으로 잡는다). 하나에
  몇 분
- 집어 든 물건을 대화에 잇는다: `Grabbable`의 잡기 이벤트에서 기존
  `DialogueVoiceClient.ReportUnityAction("지금 ○○를 들고 있다")`로 상황을 전달하는 방식을 검토한다.
  현재 WebSocket 상황 전달 경로를 사용하며, ModernVR 잡기 이벤트 연결은 아직 구현하지 않았다.
- 건네기: 인물 앞에 `Pocket`(소켓)을 두고, 거기 놓이면 인물이 반응하게

### 3단계 — 운영 부담 제거

- 체험자가 스스로 자리를 잡는다. `VRMoveControl` 은 보조로 남긴다 — 앉은 체험자의
  눈높이는 `Height` / `calibrateHeightButton` 으로 맞춘다
- 운영자 없이 체험이 성립하는지, 실사용자로 확인

### 4단계 — 정리

- 안 쓰는 것 제거: `Assets/InteractionSDK`, `Player_CRC`, 총·검·사다리 프리팹
- 결과보고서에 전후 비교

## 5. 위험

- **신규 에셋이다** (2026-08 출시, 평점 3개). 그래서 1단계 3번을 먼저 한다. 환불은
  Unity 정책상 다운로드한 뒤엔 어려운 것으로 안다 — 시간을 더 쓰기 전에 판단하는 게 목적
- OpenXR 전환이 Meta SDK 쪽을 흔들 수 있다. 브랜치에서만 하고, 로더는 언제든
  `OculusLoader` 로 되돌린다
- 물리 손은 프레임이 떨어지면 떨린다 (Updates.txt 에 저프레임 척추 글리치 수정 이력).
  PCVR 이라 여유는 있지만 카페 씬 + 인물 glb 에서 확인
- 컨트롤러로 바뀐다. 고령 체험자가 그립 버튼을 쥐고 있기 어려울 수 있다 → `AutoGrab`
  + 큰 소품 + 운영자 안내. 안 되면 잡기는 운영자 화면에서 대신 눌러 주는 길도 남긴다

## 6. 되돌리기

`Player_CRC` 는 지우지 않고 비활성만 한다. XR 로더는 `Assets/XR/Loaders/OculusLoader`
로 되돌리면 된다. 브랜치를 버리면 끝이다.
