# Scene_2 원경 수목 이미지 제작

2026-10-08, 사용자가 확인한 나무 밑동·수관 잘림을 고치기 위해 제작했다.
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

최종 이미지 생성 프롬프트:

```text
Use case: photorealistic-natural.
Asset type: transparent RGBA cutout texture for distant trees around a warm, cozy Unity VR cafe.
Primary request: a wide, complete grove of six natural deciduous trees with varied modest heights and soft green spring foliage, suitable for repeating around a distant garden.
Composition: wide landscape, roughly 3:1 aspect ratio. Straight eye-level front elevation with no perspective ground plane. All trees are fully visible from the very highest leaf down to the complete trunk and natural root flare at ground contact. Crowns may overlap gently but clear trunks and small spaces below the canopy are visible. Include 5 percent fully transparent padding at the left and right, 5 percent above every crown, and 8 percent below every root. The ground-contact points of all trunks lie on one common horizontal baseline, approximately 8 percent above the bottom of the canvas. Outer branches and roots stay completely inside the frame.
Style: realistic soft leaf detail, subdued warm green and olive, naturally irregular silhouettes, soft overcast daylight matching a calm spring garden. Vary tree shapes and trunk positions so a row does not look mechanically repeated.
Background: genuinely transparent alpha everywhere except the trees and a few tiny grass tufts hugging their root flares. No sky, no horizon, no landscape ground, no shadows spread across a floor.
Critical constraints: DO NOT crop ANY canopy, branch, trunk or root at ANY image edge. No straight rectangular cut through foliage. No cut-off trunk bottoms. Show the complete trees with visible transparent space around all four edges. No people, buildings, pots, labels or watermark.
```
