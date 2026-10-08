# Scene_2 원경 수목 이미지 제작

2026-10-08, `main`의 `21aa7521` 기준이다. 사용자가 확인한 나무 밑동·수관 잘림을 고치기 위해 제작했다.
기존 `DistantTrees.png`는 줄기가 아래에서 잘린 숲 이미지였다. 크기 조정으로 원본의 잘림이 없어지지 않아,
전체 나무가 담긴 `Assets/Graphics/Scene2/DistantTreesComplete.png`로 교체했다.
원본 이미지와 그 `.meta`는 보존했다.

내장 `image_gen` 도구로 투명 배경 PNG를 생성했다. 최종 선택은 첫 생성본이며,
외곽 정리 편집본은 채택하지 않았다. 알파 클립 기준 0.4에서 첫 생성본에 수관·줄기·밑동이 모두 있고,
사방의 투명 여백은 왼쪽 19·위 38·오른쪽 17·아래 55px다. 원본 크기는 2172×724다.
표시되는 픽셀에 강한 원색의 외곽 잔여물이 없는지 읽기 전용으로 확인하고 실제 Unity 렌더링을 확인했다.

가져오기는 사방 여백을 유지하고, 가로 Repeat·세로 Clamp·알파 클립을 사용한다.
`Scene2Courtyard`는 원본 알파의 가장 낮은 실제 픽셀을 읽어 지면 높이 y=0.0004에 맞춘다.
세 메시를 반경 30m의 연속 120도 호로 배치하고, 전역 각도에서 같은 UV를 계산한다.
메시 경계에서 한 나무가 잘리지 않으며, 전체 둘레에는 이미지를 정수 횟수로 반복한다.

## 적용과 확인

`Scene2Courtyard.cs`의 `DistantTrees`가 현재 이미지를 참조하며, URP Unlit 알파 클립 재질
`Assets/Graphics/Scene2/Materials/GardenDistantTrees.mat`을 사용한다.
앞·왼쪽·뒤쪽 메시 자산은 각각 `DistantTreesGeometry.asset`, `LeftDistantTreesGeometry.asset`,
`RearDistantTreesGeometry.asset`이다. 수목판 높이 10m에는 사방의 투명 여백도 포함되므로
보이는 나무 전체의 높이가 정확히 10m인 것은 아니다. 원경 나무는 2D 이미지이며 근경 화단·식물·소품은 3D다.

수목만 수정할 때의 순서:

1. `Assets/Scenes/Scene_2.unity`를 Edit Mode로 연다.
2. `Tools > 다시봄 > 그래픽 > 수목 잘림 수정 전 촬영`으로 현재 상태를 기록한다.
3. `먼 수목 둘레 적용`으로 세 수목 메시와 전용 재질만 적용하고 저장한다.
4. `실내 반사 베이크·저장`으로 큐브맵을 갱신한다. 수목은 간접광에 기여하지 않아 조명 전체를 다시 굽지 않는다.
5. `수목 잘림 수정 후 촬영`으로 같은 구도를 대조한다.

`자연광·재질 적용`·`창밖 마당 적용`은 정원의 담장·근경 위치도 프리셋으로 다시 맞춘다.
사용자가 담장을 직접 수정한 현재 상태에서는 수목 부분 적용 메뉴를 사용한다.
정면 큰 창과 타원 창의 반사 설정은 수목 부분 적용에서 바꾸지 않는다.

촬영은 창·실내 10개와 연결부 3개의 총 13개 구도다. 이번 작업의 비교 화면은
`tools/_work/graphics_20261008_rework/roots_comparison.html`이며, 이미지 파일은 `roots_before_*`·`roots_after_*`다.
편집 도구의 출력 폴더는 실행일에 따라 `tools/_work/graphics_YYYYMMDD_rework/`로 달라진다.
HTML 비교 화면은 촬영 PNG를 모아 따로 만든 검토 자료이며 촬영 메뉴가 HTML을 자동 생성하지 않는다.
이 자료와 검사 보고서는 Git 제외 항목이므로 새 clone에는 없다.

Unity 컴파일·씬 검사, 실제 Editor 촬영과 사방 투명 여백 검사를 확인했다.
최신 모의 검사는 하네스 207·서버 302, 총 509개 통과·건너뜀 0개이며
`tools/_work/checks/20261008T095238Z-dialogue-9be1f5b6/report.json`에 있다.
최종 HMD의 양안 표시·프레임 시간과 플레이어 빌드는 검사하지 않았다.
설정·수동 배치 보존 범위는 [현재 그래픽 현황](현재_구현_현황.md#scene_2-그래픽-현재-설정)을 따른다.

## 최종 이미지 생성 프롬프트

```text
Use case: photorealistic-natural.
Asset type: transparent RGBA cutout texture for distant trees around a warm, cozy Unity VR cafe.
Primary request: a wide, complete grove of six natural deciduous trees with varied modest heights and soft green spring foliage, suitable for repeating around a distant garden.
Composition: wide landscape, roughly 3:1 aspect ratio. Straight eye-level front elevation with no perspective ground plane. All trees are fully visible from the very highest leaf down to the complete trunk and natural root flare at ground contact. Crowns may overlap gently but clear trunks and small spaces below the canopy are visible. Include 5 percent fully transparent padding at the left and right, 5 percent above every crown, and 8 percent below every root. The ground-contact points of all trunks lie on one common horizontal baseline, approximately 8 percent above the bottom of the canvas. Outer branches and roots stay completely inside the frame.
Style: realistic soft leaf detail, subdued warm green and olive, naturally irregular silhouettes, soft overcast daylight matching a calm spring garden. Vary tree shapes and trunk positions so a row does not look mechanically repeated.
Background: genuinely transparent alpha everywhere except the trees and a few tiny grass tufts hugging their root flares. No sky, no horizon, no landscape ground, no shadows spread across a floor.
Critical constraints: DO NOT crop ANY canopy, branch, trunk or root at ANY image edge. No straight rectangular cut through foliage. No cut-off trunk bottoms. Show the complete trees with visible transparent space around all four edges. No people, buildings, pots, labels or watermark.
```
