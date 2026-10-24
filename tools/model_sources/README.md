# 고정 몸체 제작 원본

## 처음부터 제작한 남성 베이스 — 2026-10-24

`Male_FromScratch.blend`는 사용자 요청으로 기존 Human 메시·리그·UV·외부 텍스처를 불러오지 않고 새로 만든 몸체다.
생성 코드는 `tools/create_male_base.py`다. 단면 로프트와 직접 만든 입체를 연결하고 표면을 정리했다.
평균적인 성인 남성 비율, 발바닥~비율 확인용 머리 정수리 기준1.78m, 손가락을 편 T포즈다.

- `Male_Body_New`: 목·몸통·팔다리·손·발이 연결된 베이스 표면. 정점326,608개·폴리곤326,606개.
- `Head_ProportionGuide_New`: 특정 인물을 닮게 하지 않은 새 머리 기준 형상. 실제 생성 머리와 교체할 수 있도록 분리했다.
- `Fingernails_New`: 양손10개 손톱. 새 손 표면을 따라 배치한 별도 메시다.
- `HeadSocket`·`NeckSeam`: 향후 접합용 기준점. 현재 서버·Unity 코드에서 이 노드를 읽는 기능은 아직 없다.
- `Review_Studio`: 조명·바닥·검토 카메라. 몸체와 별도 컬렉션이다.

이 파일은 **옷과 게임용 리깅을 붙이기 전 형태 검토용 베이스**다.
게임용 면 정리·UV·피부 텍스처·리깅·애니메이션 검증이 완료된 모델로 설명하지 않는다.
기존 게임용 Human·참여자 모델을 교체하거나 서버에 배포하지 않았다.

Blender4.5.10 LTS에서 다음 명령으로 새 제작본을 재현한다.

```powershell
blender --background --python-exit-code 1 --python tools/create_male_base.py -- --render
```

같은 신규 제작 파일을 다시 저장한다. 다른 출력은 `--out <새 .blend 경로>`로 지정한다.
`--render`를 생략하면 전체 모습 PNG를 만들지 않는다.
정면·측면·후면·사선과 손 확대를 확인하며 어깨·골반 경계와 손가락 사이 연결을 수정했다.
몸체와 머리는 각각 연결된 표면1개, 비다양체 모서리0개·Euler 특성2이며 몸체 좌우 대칭의 최대 오차는 약2.41mm다.
검사 결과와 전체 모습 렌더는 Git 제외 `tools/_work/male_from_scratch/geometry.json`, `male_overview.png`다.

## 기존 운영 몸체

`Human_Male_Natural.blend`는 2026-10-12 사용자가 확인한 남성 손을 전체 몸체에 연결한 제작 사본이다.
스킨 메시 `avatar_body`와 변형 리그 `root`만 있으며 본은60개다. 검토용 본 표시·시연 액션은 없다.
의상·양손 텍스처를 파일에 포함했다. 원래 Auto-Rig Pro 제어 리그가 있는
`Assets/Models/human/Human.blend`는 보존했으며 **수정한 손은 이 제작 사본에 있다.**

Unity 기본 몸체는 `Assets/Models/human/Human.fbx`다. 10월 13일부터는 체형 키가 있는
`Human_BodyShapeControls.blend`를 출력 원본으로 쓴다. 모델을 바꾼 뒤 기준 자세도 다시 구워야 한다.
Blender 4.5.10 LTS에서 아래 명령으로 내보냈다. 경로의 `blender`는 설치된 실행 파일로 바꾼다.

```powershell
blender --background --python-exit-code 1 --python tools/body_export.py -- tools/model_sources/Human_BodyShapeControls.blend Assets/Models/human/Human.fbx
blender --background --python-exit-code 1 --python tools/body_prep.py -- Assets/Models/human/Human.fbx Assets/Models/human/tex tools/_work/fixed_body_animation/body.glb
```

Unity Edit Mode에서 `Tools > 다시봄 > 개발 도구 > 초기 설정·복구 > 기본 몸체 재질·기준 자세 재연결`을 실행한다.
이 메뉴는 의상 기존 재질, 양손 색·노멀·거칠기, 검은 소매 안감을 연결하고
`HumanHumanoidReferencePose.asset`을 Walking 기준으로 갱신한다. 씬을 저장하거나 구성하지 않는다.
`Tools > 다시봄 > 개발 도구 > 캐릭터 검사 > 고정 몸체 애니메이션`은 FBX와 준비한 GLB를 별도 미리보기 씬에서 검사한다.
`Tools > 다시봄 > 개발 도구 > 캐릭터 검사 > 기본 몸체 자산`은 재질·본 구조를 검사하고 Project 창에서 FBX를 선택한다.
메뉴 위치는2026-10-23 정리 기준이며 전체 도구는 [Unity 도구 사용법](../../docs/Unity_도구_사용법.md)을 따른다.

양손 원본의 `MaleHands_MetalRoughness.png`는 glTF 형식(G=거칠기, B=금속도)이다.
Unity URP의 `MaleHands_MetallicSmoothness.png`는 R=0, A=1−거칠기(G)로 변환한 파일이다.
두 파일을 혼용하지 않는다. 표면을 다시 제작하면 아래 방식으로 Unity 파일도 갱신한다.

```python
from pathlib import Path
from PIL import Image
p = Path("Assets/Models/human/tex/Hands")
rough = Image.open(p / "MaleHands_MetalRoughness.png").convert("RGB").getchannel("G")
zero = Image.new("L", rough.size, 0)
Image.merge("RGBA", (zero, zero, zero, rough.point(lambda x: 255-x))).save(p / "MaleHands_MetallicSmoothness.png")
```

기하·남성 체형 기준은 MakeHuman Community의 CC0 자료이며 기준 커밋은
`a8bc2d54ff0ac92e78ff71431b1023eda42bf482`이다. 피부는 Mindfront의 CC0 Aksel Skin을
[자연 피부 자료 묶음](https://static.makehumancommunity.org/assets/assetpacks/skins02.html)에서 가져왔다.
양손에 쓰인 픽셀을 재표본화 없이1024×512로 묶었다. 참여자 자료를 사용하지 않았다.
수정·검증과 출처 기록은 [몸체 수정 검토](../../docs/몸체_체형_수정_검토_20261011.md) 20~22장을 따른다.

GLB를 준비하는 것만으로 서버의 `HEAD_BODY_GLB`나 이미 등록된 캐릭터가 교체되지는 않는다.
서버 배포·등록 캐릭터 재결합은 별도 작업이다.

2026-10-13에는 사용자 요청으로 이 제작 원본의 몸체 GLB를 실제 서버 기본 경로에 반영하고
현재 등록 캐릭터의 저장된 머리로 재결합했다. 등록 API의 새 파일을 Unity가 받아 등장·착석하는 것까지 확인했다.
다른 기존 등록 캐릭터는 자동 갱신하지 않는다. 당시 적용·백업·검사 범위는 위 검토 문서23장에 있다.

## Blender 체형 조절 사본

`Human_BodyShapeControls.blend`는 위 남성 손 몸체에 체형 Shape Key를 추가한 제작 사본이다.
본60개·부모 관계·웨이트·기본 정점·UV는 같은 구조이며, 기준 형태는 모든 값이0일 때다.
몸체 `avatar_body`를 선택하고 **오브젝트 데이터(녹색 삼각형) > Shape Keys**에서 항목의 Value를 조절한다.
범위는−1~1이며 음수는 가늘게, 양수는 굵게 바꾼다.

| 항목 | 조절 범위 |
|---|---|
| `상체_폭` | 가슴과 등의 좌우 폭 |
| `상체_두께` | 상체의 앞뒤 두께 |
| `허리_폭` | 허리와 옆구리 폭 |
| `배_볼륨` | 배 앞쪽의 볼륨 |
| `팔_굵기` | 양팔 소매 굵기 |
| `다리_굵기` | 양쪽 바지 굵기 |

현재 복장과 몸체가 같은 메시여서 옷의 실루엣도 함께 변한다. 키·팔다리 길이·관절 위치는 이6개 항목의 대상이 아니다.
목·옷깃·양손·검은 소매 안감·신발은 체형 키에서 고정했다. 값 변경 후에도 기존 본으로 포즈를 만들 수 있다.
키 생성은 `tools/body_shape_keys.py`의 `add_body_shape_keys(body, rig)`로 재현한다. 기존 키가 있으면 덮어쓰지 않는다.
Blender에서51개 값·포즈 조합을 검사했으며 GLB에 형태 키6개와 스킨 본60개가 보존되는 것도 확인했다.
10월 13일 후속 작업에서 체형 키를 Unity FBX·서버 기본 몸체·현재 등록 캐릭터에도 반영했다.
기본 정점·노멀·UV·스킨 가중치는 이전 몸체와 같고, 현재 얼굴·설문·음성도 유지했다.

## Unity에서 체형 조절

결합된 얼굴과 함께 비교하려면 **Tools > 다시봄 > 캐릭터 > 얼굴·몸체 비율 조절 씬 열기**를 사용한다.
전용 씬에서 Play 없이6개 키·서있는 자세·앉은 자세를 확인한다. 미리보기와 공용 기본 설정을 별도로 저장한다.
자세한 순서는 [체형 미리보기 안내](../../docs/얼굴_몸체_체형_미리보기_20261023.md)를 따른다.
아래는 기존 카페 Play에서 직접 공용 설정을 조절하는 순서다.

1. Scene_2에서 Play하고 체험을 시작해 캐릭터를 표시한다.
2. **Tools > 다시봄 > 캐릭터 > 체형 조절 열기**로 설정 자산을 선택한다.
3. Inspector에서 상체 폭·두께, 허리 폭, 배 볼륨, 팔·다리 굵기를−1~1로 조절한다.
4. **체형 설정 저장**으로 파일에 저장한다. **기본 체형으로 되돌리기**는6개 값을0으로 만든다.

기본 설정은 `Assets/Resources/PersonaBodyShape.asset`이다. 자산을 편집하므로 Play 종료 뒤에도 값이 유지된다.
Inspector 아래에는 현재 모델의 체형 키 연결 상태가 표시된다. 처음 값은 모두0이다.
`PersonaSpawner.bodyShapeProfile`이 비어 있으면 이 기본 설정을 사용하며 새로 불러온 인물에도 적용한다.
인물마다 다르게 설정하려면 `Create > 다시봄 > 체형 설정`으로 별도 자산을 만들고 해당 로더에 연결한다.
등록 세션별 자동 저장·사진 기반 체형 추정은 구현하지 않았다. 이전에 완성된 다른 등록 인물 GLB에는 키가 없을 수 있다.

직접 배치한 FBX는 루트에 **캐릭터 체형 조절(PersonaBodyShapeController)** 컴포넌트를 추가하고
같은 설정 자산을 연결하면 Edit Mode에서도 조절된다. FBX 원본의 SkinnedMeshRenderer 가중치를
직접 만지는 대신 이 설정을 사용한다. FBX 완전 변형 값100과 glTFast 값1의 차이를 컨트롤러가 처리한다.
각 모델 인스턴스의 가중치만 바꾸므로 공유 메시·본·바인드 행렬·웨이트는 수정하지 않는다.

Unity PreviewScene에서 체형·포즈40개 조합과 기존14개 클립 검사, 실제 서버 캐릭터의 로드 전/착석 후
각12개 체형 변경 검사를 통과했다. HMD·플레이어 빌드 검사는 별도다.
상세 적용·백업·결과는 [몸체 수정 검토](../../docs/몸체_체형_수정_검토_20261011.md) 25장을 따른다.
