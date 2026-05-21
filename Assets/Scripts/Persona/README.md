# PersonaSpawner — 셋업 가이드

Streamlit이 Meshy AI로 생성한 인물 모델(.glb)을 런타임에 자동 로드해 고정 스폰포인트에 배치하는 컴포넌트.

## 1. 패키지 (이미 추가됨)

`Packages/manifest.json` 에 다음이 포함되어 있어야 한다 — 이미 들어가 있음:
```json
"com.unity.cloud.gltfast": "6.14.1"
```

## 2. 씬 셋업

MainScene에서:
1. **빈 GameObject** 생성 → 이름 `PersonaSpawner`
2. 그 자식으로 **빈 GameObject** 추가 → 이름 `SpawnPoint`
3. `SpawnPoint`의 **Transform**으로 인물이 등장할 위치·회전을 잡는다 (기획안의 "공간 중앙, 피규어처럼 정면을 향해 서있는" 위치)
4. `PersonaSpawner` GameObject에 `PersonaSpawner.cs` 컴포넌트 부착
5. Inspector에서:
   - `Data Root` — Streamlit `data` 폴더 절대 경로 (기본값: `C:\Users\user\Desktop\다시봄_설문시스템\data`)
   - `Spawn Point` — 위에서 만든 SpawnPoint 드래그
   - `Target Height Meters` — 1.7 (사람 키 기준 자동 스케일링; 0이면 원본 유지)
   - `Loading Indicator` — (선택) "잠시만요…" UI GameObject

## 3. 동작 원리

```
[Streamlit 설문 제출]
   └─ data/sessions/<sid>/front.jpg 저장
   └─ data/current_session.txt = "<sid>"          ← Unity가 읽는 포인터
   └─ 백그라운드에서 Meshy 작업 시작
                  ↓ (몇 분 후)
   └─ data/sessions/<sid>/model.glb 저장 완료

[Unity 시작]
   1. current_session.txt 읽어 sid 결정
   2. data/sessions/<sid>/model.glb 가 나타날 때까지 대기 (retryIntervalSec 간격으로 폴링)
   3. glTFast.GltfImport.Load() 로 로드
   4. SpawnPoint 위치/회전에 Instantiate
   5. (선택) targetHeightMeters로 자동 스케일링
```

## 4. 테스트 (Meshy API 키 없이)

API 키 없는 stub 상태에서도 Unity 동작을 검증하려면, 임시 .glb를 직접 넣어보자:
1. 아무 무료 .glb (예: https://github.com/KhronosGroup/glTF-Sample-Models 의 `Avocado.glb`)를 다운로드
2. `data/sessions/test-001/model.glb` 위치로 복사
3. `data/current_session.txt` 에 `test-001` 한 줄 작성
4. Unity Play → SpawnPoint 위치에 모델이 떠야 정상

## 5. 자주 보는 문제

| 증상 | 원인 / 해결 |
|---|---|
| `GLB 로드 실패` 로그 | 파일 손상 또는 glTFast 미설치. Package Manager 확인. |
| 모델이 너무 크거나 작음 | `Target Height Meters` 값을 조정. 0으로 두면 원본 스케일. |
| 모델이 안 보임 (씬엔 있음) | 카메라 위치/SpawnPoint 위치 불일치. 일단 `Target Height = 0`으로 두고 Scene 뷰에서 확인. |
| 영원히 대기만 함 | `data/current_session.txt`가 비어 있거나 `model.glb`가 아직 안 만들어진 것. Streamlit 관리자 페이지 → 3D 모델 탭에서 상태 확인. |

## 6. Meshy 키 채우는 법

`C:\Users\user\Desktop\다시봄_설문시스템\.streamlit\secrets.toml`:
```toml
meshy_api_key = "msy-실제키"
```
Streamlit만 재시작하면 자동으로 실모드 전환. 코드 수정 불필요.

## 7. 빌드 시 주의

Quest 등 스탠드얼론으로 빌드할 땐 Windows 절대 경로(`C:\...`)가 의미 없어진다. 그 경우엔:
- 백엔드에 `GET /api/model/<sid>` HTTP 엔드포인트를 추가
- 본 스크립트의 `WaitForFileAsync`/`gltf.Load(path)` 부분을 `UnityWebRequest`로 .glb 바이트 다운로드 후 `gltf.LoadGltfBinary(byte[])` 로 교체

이 부분은 시연 환경이 분리 PC 또는 헤드셋 단독이 되었을 때 추가 작업으로 두면 됨.
