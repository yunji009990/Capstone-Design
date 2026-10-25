# 남성 대기·걷기 애니메이션 출처

- 제공: Unity Technologies, Standard Assets: Characters.
- 걷기 원본: `Assets/_Standard Assets/Characters/Animation/Male/Animations/Exploration/m@WalkForwards.fbx`.
- 대기 원본: 같은 폴더의 `m@Idle.fbx` (`UnityMaleIdle.fbx`).
- 고정 버전: `8f185aa3c782d824976653dd6a6476312daad241`.
- [원본 저장소](https://github.com/Unity-Technologies/Standard-Assets-Characters/tree/8f185aa3c782d824976653dd6a6476312daad241).
- [원본 라이선스](https://github.com/Unity-Technologies/Standard-Assets-Characters/blob/8f185aa3c782d824976653dd6a6476312daad241/LICENSE.md)는 동봉한 `LICENSE.md`와 같다.
- [Unity Companion License](https://unity.com/legal/licenses/unity-companion-license).

두 FBX는 원본 바이너리를 유지한다. Import 설정의 Avatar는 각 파일에서 생성하도록 변경했다.
기존 다른 캐릭터의 Avatar GUID에 의존하지 않는다. 외부 프로젝트의 패키지·스크립트·컨트롤러는 가져오지 않았다.
Y 루트 이동은 원본 설정대로 자세에 포함하며, 신발 기준의 높이 차이를 맞추는 Import Offset은 `-0.01`이다.
당시 Scene_2는 실제 Play의 지지발 이동을 확인해 `walkSpeed=1.46m/s`로 맞췄다.
이후 사용자 재확인으로 걷기는 [Quaternius NaturalWalk](../PersonaPerformance/Source.md)·0.8m/s로 교체했다.
`UnityMaleWalk.fbx`는 이전 선택의 비교 자산이며 현재 Scene_2에는 연결하지 않는다.
현재 서서 대기는 계속 `UnityMaleIdle.fbx`의1~91프레임·3초 반복 설정을 사용한다.
