# hj → jw 병합 기록

검증일: 2026-09-13. 대상 Unity: 2022.3.62f2, Windows Editor.

## 범위와 결과

`jw`의 `6bc4595`에 `hj`의 `1d8e4a3`을 병합했다. 공통 조상은 `ba1504a`다.
`jw`의 서비스 분리·실시간 대화 AI·검사 하네스와 `hj`의 Feel 4.3 연출,
COZY 3.6.23 패키지, OpenXR 준비 설정, 인물 재질·텍스처 보정과 chibi 에셋을 함께 보존했다.

OpenXR 패키지는 1.14.3이며 활성 XR 로더는 기존 Oculus다. ModernVR은 아직 도입 계획이고,
COZY 날씨를 Scene_1·Scene_2에 연결하는 작업도 이번 병합에 포함하지 않았다.
TTS는 꺼진 상태를 유지한다. 서버 배포와 원격 브랜치 push는 수행하지 않았다.

## 충돌과 연결 수정

- `.gitignore`: 대화 환경·실험 기록 제외 규칙과 COZY 데모 제외 규칙을 모두 유지했다.
- `Assets/Scripts/Raon/Fonts/Malgun SDF.asset`: 문자표와 아틀라스 텍스처를 따로 합치지 않고
  `jw`의 Dynamic TMP 캐시(488문자)를 선택했다. 원본 글꼴과 `.meta` GUID를 보존했다.
- `음성대화_작업현황.md`: 과거 이력임을 명시하고 `hj`의 Feel·ModernVR 기록을 유지했다.
  현재 Gemma 텍스트 응답·TTS 중지 상태를 안내했다.
- `ExperienceCues.cs`: `RaonVoiceClient`·`RaonVoiceUI` 참조를
  `DialogueVoiceClient`·`DialogueVoiceUI`로 바꿨다. UI의 `StatusDot` 공개 속성과
  `PersonaSpawner`의 재질·밉맵 설정은 자동 병합 결과를 확인했다.
- `ModernVR_도입계획.md`: 현재 클라이언트 이름과 기존 `ReportUnityAction` 상황 전달 경로를 반영했다.

Scene_1의 연출 → 대화·UI·체험 제어·인물 스폰 참조, Scene_1·Scene_2의 인물 스폰 → 대화 참조가
기존 파일 ID와 스크립트 GUID로 연결되는지 확인했다. AI_Response_Test와 Tripo_Model_Test의
직렬화된 씬 파일은 병합 전과 같다.

## 검사

| 검사 | 결과 | 근거 |
|---|---|---|
| 전체 모의 검사 | 79개 통과, 건너뜀 0개 | 하네스 11, 서버 63, 작업자 5 |
| Unity 컴파일 | 성공 | 새 Assembly-CSharp·Editor 어셈블리 생성, Play Mode 진입 및 새 컴포넌트 실행 |
| 실제 대화 서버 연결 | 텍스트 응답 검사 통과 | AI 테스트 메뉴, 가상 프로필과 사진 상황 입력 |
| TTS 중지 계약 | 통과 | `ttsEnabled=false`, 음성 샘플 0, 음성 출력 peak 0 |
| Feel 초기화 | 통과 | AI 테스트 씬의 임시 ExperienceCues가 대화 클라이언트를 찾고 MMF_Player 8개·빛·캔버스 생성 |
| 씬 참조·GUID | 통과 | `tools/_work/merge_hj/static.json` |
| 편집기 복구 | 완료 | Play Mode 종료, 원래 Tripo_Model_Test 복구, 저장되지 않은 씬 변경 없음 |

실행 명령:

```powershell
python tools/check.py --area all --python tools/_work/check_env_20260913/Scripts/python.exe
```

보고서는 `tools/_work/checks/20260913T140320Z-all-d94dfa50/report.json`이다.
처음 기본 Python으로 실행한 검사는 `httpx`·`fastapi`가 없어 실패했다
(`20260913T140020Z-all-bfc669e9/report.json`). 기존 별도 검사 환경으로 다시 실행해 위 결과를 얻었다.
실제 응답 기록은 `tools/_work/merge_hj/ai-text-scene-smoke.json`, Unity 관찰 기록은
`tools/_work/merge_hj/unity-evidence.json`에 보관했다. 결과 파일은 Git에서 제외한다.

실제 텍스트 검사에서 응답 조각 10개를 받았고 첫 텍스트 약 0.182초, 전체 응답 약 0.332초였다.
단일 연결 검사이며 대화 품질·응답 속도 비교 실험 결과로 일반화하지 않는다.

## 제한과 별도 작업

최종 확인한 Console에는 게임 코드·C# 컴파일 오류가 없었다. MCP transport의 연결 종료와
disposed NetworkStream 로그는 남았으며, 병합 전에도 MCP 연결 종료 로그가 있었다.
MCP `execute_code`는 Windows 컴파일 명령 길이 문제로 실패하여 실제 검사는 메뉴·컴포넌트 도구로 수행했다.

헤드셋에서의 화면 연출·진동, 생성 GLB 외형 비교, COZY 날씨 실행, ModernVR, STT 마이크,
배포용 빌드와 실제 TTS 출력은 검사하지 않았다. 임시 연출 객체는 Play Mode 종료로 제거했다.

검사 후 `Server/`에서 별도로 진행 중인 대화 메모리 관련 변경은 이번 병합 커밋에서 제외하고
작업 폴더에 보존했다. 처음 확인한 파일은 `realtime_llm.py`와 새 `dialogue_memory.py`이며
이후 다른 서버 파일에서도 변경이 확인됐다. 위 검사는 이 별도 작업의 검증을 대신하지 않는다.
