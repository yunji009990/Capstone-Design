# 로컬 API 시연 버전

2026-11-05 갱신. 기존 GPU 서버 버전을 보존하면서 **LLM·음성 인식만 OpenAI API**, **기존 목소리는 로컬 VoxCPM2**로 실행하는 별도 경로다.
학교 서버에 연결하지 않고 이 PC에서 Unity를 실행하기 위한 구성이다. 인터넷과 로컬 NVIDIA GPU는 필요하다.
웹 등록·사진 전처리·모델 작업자는 CPU로 실행하며 Tripo 생성은 외부 API를 사용한다. 웹만 켤 때는 GPU가 필요하지 않다.

## 다른 PC로 옮기는 친구 시연 배포본

2026-11-05 바탕화면의 `DasiBom_LocalAPI_20261105` 폴더에 Windows x64 플레이어와 독립 실행 환경을 묶었다.
이 배포본은 **테스트 → 친구** 전용이다. 준비된 사진·참조 WAV·기존 완성 GLB를 재사용하며 Tripo 작업자는 실행하지 않는다.
개발용 등록 웹과는 포트·설정·세션 저장소를 분리했다.

1. 대상 PC에 폴더 **전체**를 복사한다. `Game/DasiBom.exe`만 복사해서 실행하지 않는다.
2. Windows 사용자 환경변수 **`OPENAI_API_KEY`**를 설정한다. 시작 프로그램이 환경변수에서 읽으며 파일에 저장하지 않는다.
3. NVIDIA 드라이버와 VR 기기의 PC 연결 프로그램·OpenXR 런타임을 준비한다.
   Visual C++ 런타임이 없는 PC는 포함한 `Prerequisites/vc_redist.x64.exe`를 설치한다.
4. **`Start.cmd`**를 실행한다. 등록·웹·VoxCPM2·대화 서비스의 준비가 끝나면 브라우저가 열린다.
5. **테스트 → 친구**를 누른다. 마지막 단계에서 사진·음성·모델 등록이 끝나면 Unity 플레이어가 자동으로 열린다.
6. Unity에서 체험을 시작한다. 플레이어를 종료하면 시작 프로그램이 자신이 켠 서비스만 종료한다.
   비정상 종료 후 서비스가 남으면 **`Stop.cmd`**를 사용한다.

| 폴더·설정 | 역할 |
|---|---|
| `Game/` | Unity 2022.3.62f2, Windows64 Mono 빌드와 현재 니트 몸체 |
| `runtime/` | CPython 3.11.9 embeddable과 검증된 Python 패키지 |
| `Backend/` | 등록 웹·대화 코드, VoxCPM2 가중치·소스, 준비된 친구 자료 |
| `%LOCALAPPDATA%/DasiBom/LocalApiDemo/` | 실행 PC에서 새로 만드는 설정·접속 토큰·세션·서비스 로그 |
| `127.0.0.1`의 19000·19002·19003·19500 | 배포본 전용 등록·대화·TTS·웹 포트 |

Unity Editor·Python·Blender 설치 없이 실행하도록 구성했다. **Windows 10/11 x64·NVIDIA GPU·인터넷은 필요하다.**
LLM·STT는 OpenAI API이고 기존 목소리 합성은 PC GPU의 VoxCPM2다. 사용 비용은 설정한 OpenAI 키 계정에 발생한다.
준비된 친구 이외의 생성 요청은 배포 웹에서 차단한다. 일반 신규 사진 등록은 개발용 웹의 기존 경로를 사용한다.
배포본에는 친구의 사진·음성·모델이 포함되므로 비공개 시연 자료로 전달한다. API 키·개발 세션 기록은 복사하지 않는다.

현재 니트 몸체는 `StreamingAssets/LocalDemoBody.json`과 모델 해시를 대조해 사용한다.
기존 Editor 설정과 같은 키·몸체 높이·착석 보정을 적용하고 리깅·발 위치 보정도 포함했다.
런타임 GLB의 불투명 PBR 셰이더는 `RuntimeGltfOpaque.mat`를 Resources에 포함해 빌드에서 제거되지 않게 했다.
배포 표식이 있는 플레이어는 연결 설정 누락 시 과거 학교 서버로 연결하지 않고 시작 프로그램 사용을 안내한다.

재생성은 Unity Windows64 빌드 후 `tools/package_local_demo.py <저장소 밖 배포 폴더>`를 사용한다.
XR SDK 출력 파일 충돌을 피하려면 Unity 빌드는 새 출력 폴더에 하고 완성된 `Game/` 파일을 배포 폴더에 복사한다.
패키징은 현재 로컬 친구 자료·니트 몸체 해시·모델 가중치·Python 패키지가 준비된 개발 PC에서 수행한다.
출처: [Python embeddable 배포](https://docs.python.org/3.11/using/windows.html#the-embeddable-package),
[Microsoft Visual C++ 런타임](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist?view=msvc-170).
Microsoft 설치 파일의 서명 `Valid`·서명자 `Microsoft Corporation`을 확인했다.

배포 검사 근거는 Git 제외 `tools/_work/local_demo/validation/package/`에 있다.
Windows 빌드·실제 브라우저 친구 자동 등록·준비 모델 일치·생성 작업 추가0건·플레이어 Scene_2 인물 로딩·착석·지원하지 않는 재질0개를 확인했다.
독립 배포 Python의 `voice.json`에서 실제 등록 친구의 PCM 입력·API 전사·답변 텍스트·참조 목소리 합성을 확인했다.
답변 PCM23개·3.35초·무음 아님을 확인했으며 청취 품질 판정이나 HMD 출력 지연 실측을 의미하지 않는다.
실행 중인 다른 GPU 학습이 없다는 사용자 안내와 점유 확인 후 검사했고, 마지막에 배포용 서비스를 모두 종료했다.
전체 기본 검사613개·건너뜀0개 근거는 `tools/_work/checks/20261105T032511Z-all-67ad23ef/report.json`이다.
다른 PC에서의 실행과 실제 HMD 착용·성능 검증은 별도다. HMD 미연결 검사에서는 미리보기 카메라가 실제 착석 눈높이를 나타내지 않는다.

## FAT32 USB로 전달하고 복원하기

현재 비공개 전달본은 바탕화면과 `E:\DasiBom_USB_분할`에 같은 구성으로 보관했다.
완성 폴더는 약13.36GiB, 원본 ZIP은7,929,643,865바이트(약7.38GiB)다.
FAT32의 단일 파일 한도를 넘으므로 원본 ZIP을 아래3개 파일로 나눴다. 압축을 푼 모델에도4GiB를 넘는 파일이 있다.

| 파일 | 크기·역할 |
|---|---|
| `DasiBom_LocalAPI_20261105.zip.001` | 3,221,225,472바이트 |
| `DasiBom_LocalAPI_20261105.zip.002` | 3,221,225,472바이트 |
| `DasiBom_LocalAPI_20261105.zip.003` | 1,487,192,921바이트 |
| `ZIP_합치기.cmd`, `Restore.ps1` | Windows PowerShell로 대상 PC 바탕화면에 ZIP 복원 |
| `parts.json`, `사용 방법.txt` | 길이·SHA-256 확인 정보와 실행 안내 |

1. 대상 Windows PC에 USB를 연결하고 위7개 파일을 같은 폴더에 둔다.
2. USB 폴더의 **`ZIP_합치기.cmd`**를 실행한다. Python이나 별도 분할 압축 프로그램은 필요하지 않다.
3. 복원 스크립트가 대상 PC 바탕화면에 원본 ZIP을 만들고 전체 길이와 SHA-256을 확인한다.
4. 복원된 ZIP을 **PC 내부의 NTFS·exFAT 저장소**에 압축 해제한다. FAT32 USB 위에 복원하거나 압축 해제하지 않는다.
5. 압축 해제한 폴더에서 위 배포본 실행 절차대로 환경변수·VR 실행 환경을 준비하고 **`Start.cmd`**를 실행한다.

ZIP과 압축을 푼 폴더를 함께 보관하려면 약25GB 이상의 여유 공간을 준비한다.
복원 대상에 동일한 ZIP이 있으면 해시 확인 후 재사용하고, 내용이 다른 기존 파일은 덮어쓰지 않는다.
기본 바탕화면이 FAT32 저장소라면 `Restore.ps1 -Destination '<NTFS 또는 exFAT의 전체 ZIP 경로>'`로 대상을 지정한다.
분할 파일·사진·음성·모델·배포본은 Git에 포함하지 않는다. 준비 자료는 별도의 비공개 전달본이 필요하다.

USB 복사 완료 후7개 파일의 길이를 확인했고 작은 복원 도구·안내 파일은 원본 해시도 대조했다.
Desktop 분할본의 실제 복원 실행에서는 전체 ZIP 해시가 원본과 일치했다.
근거는 `tools/_work/local_demo/validation/usb-copy-verified.json`과 복원 검사 산출물이다.
이는 USB 전달·복원 확인이며 다른 PC·HMD 실행 결과는 아니다.

## 실행 흐름

```text
Unity 마이크
  → 로컬 Silero VAD: 발화 구간 판정
  → OpenAI gpt-transcribe: 한국어 음성 인식
  → 기존 의도·끼어들기·메모리 처리 + OpenAI gpt-5.4-mini
  → 로컬 VoxCPM2: 등록된 참조 목소리로 합성
  → Unity: 24kHz PCM 재생·자막·기존 인물 동작
```

일반 답변은 `reasoning_effort=none`, 추론 경로는 `low`다. 의미 기반 끼어들기 `semantic_v1`, 취소·재응답,
전달된 답변만 반영하는 대화 기록, 핵심 기억과 대기 리액션의 기존 계약을 사용한다.
음성 인식 API는 기존 Whisper의 신뢰도 필드를 제공하지 않아 이를 임의로 채우지 않는다. 로컬 VAD와 기본 음량 검사는 유지한다.

모델·요청 형식 근거: [GPT-5.4 mini](https://developers.openai.com/api/docs/models/gpt-5.4-mini),
[음성 전사 API](https://developers.openai.com/api/reference/resources/audio/subresources/transcriptions/methods/create).
LLM의 `store`는 `false`이며 대화·인물 설정은 LLM API, 입력 음성과 참조 전사는 전사 API로 전달된다.
실제 합성은 로컬에서 수행한다. OpenAI API 사용량은 해당 키의 계정에 청구된다.

## 이 PC에서 사용

설치, 모델 다운로드, 현재 등록 인물 복사, API 연결 확인을 마쳤다.
Unity의 **Tools → 다시봄 → 대화·서버 → 실행 환경**에서 다음 순서로 사용한다.

1. **로컬 API 사용**을 선택한다.
2. **로컬 서비스 시작**을 실행한다. Console에 다섯 서비스 준비 완료가 나올 때까지 기다린다.
3. **로컬 등록 웹 열기**로 [등록 페이지](http://127.0.0.1:18500)를 연다. 기존 등록 인물을 그대로 쓸 때는 생략한다.
   준비된 친구는 **테스트 → 친구**를 누르면 바로 등록된다. 새 인물은 설문·음성·사진 등록 후 모델 생성 완료를 기다린다.
   이후 `StartScene` 또는 `Scene_2`를 Play한다.
4. 마친 뒤 Play를 종료하고 **로컬 서비스 종료**를 실행한다.

**로컬 상태 확인**은 실행·준비 여부를 Console에 표시한다. **사용법 열기**는 이 문서를 연다.
실행 환경 변경은 다음 Play부터 적용된다. 같은 메뉴의 **기존 GPU 서버 사용**으로 원래 씬의 연결 설정을 사용한다.
이 선택은 원격 서버를 자동으로 켜지 않는다. 로컬 프로세스도 별도로 종료해야 GPU 메모리가 반환된다.

씬·프리팹의 접속 값과 AI 테스트 씬의 기존 서버 EditorPrefs는 저장 변경하지 않는다.
`AI_Response_Test`도 로컬 선택을 따르되 테스트 인물·입력 방식 설정은 기존 씬의 값을 유지한다.

**등록만 할 때:** 같은 메뉴의 **로컬 등록 웹 시작 (GPU 사용 안 함)** → **로컬 등록 웹 열기**를 사용한다.
등록 API·웹·CPU 작업자만 켜므로 다른 GPU 학습 중에도 등록 준비가 가능하다. 이후 **로컬 서비스 시작**을 누르면
이미 실행된 세 서비스를 유지하고 대화·VoxCPM2를 추가로 켠다. **로컬 서비스 종료**는 로컬 다섯 서비스를 모두 대상으로 한다.

## 준비된 친구로 바로 등록

[로컬 웹](http://127.0.0.1:18500)에서 **테스트 → 친구**를 누른다. 마지막10단계로 이동해
사진·참조 음성을 채우고 기존 GLB까지 포함한 새 세션을 자동 등록한다. 완료 화면에서 사진·음성을 확인할 수 있다.
현재 인물은 새 세션으로 선택되며 원본과 이전 세션은 보존한다. **Tripo 호출과 모델 생성 작업은 없다.**
준비 자료가 없거나 바뀌면 오류를 표시하며 일반 사진 생성으로 넘어가지 않는다.

이 PC의 자료는 준비됐다. 다른 PC에서 같은 자료를 준비하려면 `import-server` 뒤 다음을 한 번 실행한다.

```powershell
.\tools\local_demo.ps1 prepare-friend
```

현재 친구의 설정이 공개 `friend` 프리셋과 일치하는지 확인하고 사진·WAV·GLB를
`.dialogue-work/local-demo/prepared-friend/`에 별도로 보관한다. 사진이 로컬에 없으면 SSH로 원본 사진만 가져온다.
일상 실행에는 학교 서버가 필요하지 않으며 자료는 Git에 넣지 않는다. 기존 GLB 바이트를 그대로 쓰므로 특정 모델 해시 기반
니트 몸체 Editor 대체 연결도 유지된다. 새 사진 생성은 아래 일반 등록 경로를 사용한다.

2026-11-05 실제 Edge에서 친구 버튼1회 → 마지막 단계 → 자동 등록·사진 표시·음성 미리 듣기 준비를 확인했다.
등록된 인물 설정·WAV·GLB와 원본의 해시가 일치했고 모델 작업 추가0건이었다.
웹 등록 완료는347ms 단일 표본이며 음성 대화 응답 지연과 다른 수치다.
근거: `tools/_work/local_demo/validation/prepared-friend-result.json`.
전체 기본 검사610개 통과·건너뜀0개: `tools/_work/checks/20261105T025426Z-all-7c03cd6a/report.json`.
이 등록 검사 당시에는 CPU 웹·등록만 검사했다. 이후 GPU 여유 확인을 거친 배포 검사는 위 별도 절을 따른다.

## 로컬 신규 등록

```text
로컬 웹(18500): 설문 + 참조 음성 + 사진
  → 로컬 등록 API(18000): 새 세션·인물 설정·참조 음성 저장
  → 로컬 CPU 작업자: 사진에서 머리 추출 → Tripo API 생성
  → 로컬 Blender: 생성 머리 다듬기·고정 몸통 결합
  → 같은 등록 API에 완성 GLB 전달
  → 로컬 모드 Unity: 새 현재 인물·모델 조회 → 체험
```

로컬과 학교 서버의 등록 자료는 자동 동기화하지 않는다. **로컬 웹에서 새로 등록한 인물은 로컬 체험에 반영된다.**
사진을 바꾸어 등록하면 Tripo 유료 생성이 실행된다. 실제 실행에는 기존 모델을 무조건 돌려주는 시험용 GLB 설정을 사용하지 않는다.
참조 음성을 바꾸면 해당 새 세션의 VoxCPM2 목소리 준비에 사용된다. 사진 없이 등록하면 새 얼굴 모델은 만들어지지 않는다.
이미 Play 중이라면 생성 완료 후 Play를 다시 시작해 새 인물을 사용한다.

학교 서버와 동일한 생성용 고정 몸통·결합 설정을 복사했다. 기존 니트 몸체의 Editor 시험 대체는 특정 모델 해시에 연결되어 있어
**새 사진으로 만든 인물에 니트 외관까지 자동 적용되지는 않는다.** 이는 등록 연결과 별개인 기존 몸체 시험 기능의 제한이다.

## 설치·재준비

Windows, Python 3.11, Git, CUDA 12.8을 지원하는 NVIDIA 드라이버가 필요하다.
이 PC에서는 RTX 5060 Ti 16GB와 PyTorch 2.8.0+cu128로 확인했다.
`OPENAI_API_KEY`는 Windows 사용자 환경 변수 또는 실행 프로세스 환경에 설정한다. 키를 소스·씬·명령 인수에 넣지 않는다.

저장소 루트에서 PowerShell로 실행한다.

```powershell
.\tools\local_demo.ps1 setup
.\tools\local_demo.ps1 setup-web
.\tools\local_demo.ps1 import-web
.\tools\local_demo.ps1 import-server
.\tools\local_demo.ps1 use-local
.\tools\local_demo.ps1 start
.\tools\local_demo.ps1 status
```

`setup`은 전용 가상환경과 VoxCPM2·Silero 모델을 준비한다. Vox 소스와 가중치 revision은 코드에 고정되어 있다.
`setup-web`은 Windows Python 3.11 웹·CPU 작업자 환경을 별도로 설치한다. Vox 환경의 NumPy·CUDA 패키지를 변경하지 않는다.
`import-web`은 SSH로 기존 생성용 몸통·분할/검출 모델·Tripo 설정을 한 번 복사하고 이 PC의 Blender 4.5를 연결한다.
웹 접근 코드와 관리자 비밀번호는 기존 서버 값을 이어받는다. 비공개 설정은 `.dialogue-work/local-demo/web.env`에만 저장한다.
로컬의 기존 설정이나 다른 자산을 자동 덮어쓰지 않는다. 가져온 뒤 일상 실행에는 SSH나 학교 서버가 필요하지 않다.
`import-server`는 SSH 별칭 `raon`으로 현재 인물의 설정·참조 WAV·완성 GLB를 한 번 읽어 복사한다.
원격 서비스가 꺼져 있어도 SSH가 연결되면 가능하다. 로컬 서비스를 먼저 종료해야 가져올 수 있다.
같은 세션의 자료가 달라졌으면 자동 덮어쓰기를 거부한다. 학교 서버에서 새로 등록한 자료만 다시 가져와야 한다.
로컬 웹에서 새 인물을 등록할 경우 `import-server`는 선택 사항이다.

등록만 실행하거나 브라우저를 여는 명령은 다음과 같다.

```powershell
.\tools\local_demo.ps1 start-web
.\tools\local_demo.ps1 open-web
```

```powershell
.\tools\local_demo.ps1 stop
.\tools\local_demo.ps1 use-server
```

## 분리와 보관 위치

| 항목 | 위치·역할 |
|---|---|
| 127.0.0.1:18000 | 인물 신규 등록·현재 인물·참조 음성·모델 조회 |
| 127.0.0.1:18002 | API 대화 처리·WebSocket·CPU VAD |
| 127.0.0.1:18003 | 로컬 VoxCPM2 |
| 127.0.0.1:18004 | CPU 모델 작업자 상태·종료 제어 |
| 127.0.0.1:18500 | 로컬 설문·음성·사진 등록 웹 |
| `UserSettings/LocalDemo.json` | 로컬 포트·모델명·생성 한도·로컬 인증 토큰 |
| `UserSettings/LocalDemoConnection.json` | Unity 선택 상태·loopback 주소·로컬 인증 토큰 |
| `.dialogue-work/local-demo/` | 복사 자료·모델·리액션 캐시·로그·프로세스 기록 |
| `.dialogue-work/local-demo/survey/` | 로컬 설문 DB·사진·생성 작업 기록 |
| `.dialogue-work/local-demo/web-workspace/` | 로컬 웹의 음성 변환 작업 자료 |
| `.dialogue-work/local-demo/web.env` | 로컬 Tripo 키·웹 접근 설정·Blender 및 생성 자산 경로 |
| `tools/_work/local_demo/` | 전용 Python 환경·고정 Vox 소스·검증 결과 |

설정·자료·모델·검증 결과는 모두 Git 제외다. OpenAI 키는 파일로 저장하지 않는다.
다섯 서비스는 loopback에서만 수신하며 원래 서버 포트 8000~8003·8500과 충돌하지 않는다.
종료는 PID·생성 시각·명령·실행 식별자를 대조해 이 실행기가 시작한 프로세스만 대상으로 한다.
로컬 설정이 잘못되면 연결 오류를 표시하며 기존 GPU 서버로 자동 전환하지 않는다.

## 동작·검증 범위

### 노트북의 음성 끊김 확인

2026-11-05 사용자가 **VRAM8GB 노트북에서 음성은 생성되지만 반복해서 끊긴다**고 보고했다.
노트북의 정확한 GPU 모델·로그·메모리 점유·합성 속도는 아직 확인하지 않았다. VRAM 용량만으로 원인을 확정하지 않는다.
LLM·STT는 OpenAI API를 사용하지만 VoxCPM2 음성 합성은 로컬 GPU에서 실행되며 Unity·VR과 자원을 공유한다.

| 확인할 항목 | 구분할 원인 |
|---|---|
| 음성 합성 중 GPU 전용 메모리 점유와 `CUDA out of memory` 오류 | GPU 메모리 부족 여부 |
| 생성된 음성 길이 대비 합성 소요 시간, 첫 PCM 이후 전송 간격 | 재생 속도를 따라가지 못하는 음성 생성 여부 |
| Unity의 `max_packet_gap`, `buffered`, `underrun_frames`, `rebuffers`와 서버의 `resp_max_gap_ms` | 음성 공급 간격과 재생 버퍼 부족 여부 |
| 음성 도착 간격이 정상인데 커지는 `max_frame_stall` | Unity 프레임 지연 여부 |

아래10월31일의2.24초 음성·마지막 PCM4.125초 기록은 **다른 GPU 작업이 함께 실행된 기존 PC의 단일 표본**이다.
노트북 실측·평균 성능·최소 VRAM 요구량으로 사용하지 않는다. 합성이 재생보다 느리면 재생 버퍼가 비어 끊길 수 있다는 진단 근거다.
이번 확인에서는 코드·합성 설정·참조 음성·배포본을 변경하지 않았다.
로그 항목의 의미는 [음성 재생 끊김 진단](음성_재생_끊김_진단.md)을 따른다.

### 기존 검사 기록

2026-11-05 로컬 웹 연결 검사:

- 전체 기본 검사 **605개 통과**, 건너뜀 0개: `tools/_work/checks/20261105T013855Z-all-6a17486a/report.json`.
- 실제 웹 페이지·등록 API·작업자 준비, 로컬 서비스 종료 후 재시작 확인. TTS·대화 서비스는 시작하지 않았다.
- 별도 임시 저장소에서 실제 설문·WAV 변환·사진 업로드 → 새 세션·작업 큐 → 작업자 모델 전달 → Unity 조회 계약 확인.
  유료 Tripo 생성만 고정 테스트 GLB로 대체했으며 실제 로컬 실행에서는 이 우회를 끈다. 기존 인물 자료의 보존도 확인했다.
- CPU MediaPipe·InsightFace 실행, 기존 머리 캐시를 이용한 Windows Blender 다듬기·몸통 결합과 GLB 리깅 보존 확인.
- Unity 2022.3.62f2의 이 프로젝트에서 메뉴 컴파일·연결 설정 검사 확인. 실제 신규 사진 유료 생성·신규 인물 HMD 체험은 미검증이다.
- 상세 결과: `tools/_work/local_demo/validation/web-registration.json`, `web-models.json`.

다음은 2026-10-31의 실제 API·음성·Unity 검사 기록이다.

실제 API와 로컬 VoxCPM2로 음성 인식 → 답변 생성 → PCM 합성, PCM 마이크 입력,
응답 중 취소와 다음 답변 복구를 확인했다. 기본 검사와 실제 모델 검사는 별도로 기록한다.
실제 검증 결과는 Git 제외 `tools/_work/local_demo/validation/`에 있다.

| 검사 | 확인 결과 |
|---|---|
| 전체 기본 검사 | 597개 통과, 건너뜀 0개. `tools/_work/checks/20261031T132311Z-all-e8143eb3/report.json` |
| API·Vox 음성 흐름 | `smoke.json`: STT·답변 음성·PCM 입력·취소·재응답 성공 |
| 실제 등록 인물 연결 | `registered-first-ready.json`: 첫 준비 21.625초, `registered-ready.json`: 캐시 재연결 1.079초. 목소리·리액션 준비 성공 |
| Unity Scene_2 Play | `unity.json`: 로컬 모드·서버 준비·인물 조회·모델 준비 모두 참, C# 오류 0개 |
| AI_Response_Test Play | 로컬 프로필·서버 준비 확인. 검사 후 Scene_2 Edit 모드로 복귀 |
| 현재 PC 합성 단일 표본 | `tts-threads1.json`: 첫 PCM 1.468초, 2.24초 음성의 마지막 PCM 4.125초 |

마지막 표본은 다른 Unity·Python GPU 작업이 실행 중인 조건이다. API 왕복·HMD 출력 지연을 포함한 수치가 아니며 평균 성능도 아니다.
현재 조건에서는 합성이 재생 속도보다 느려 끊김이 생길 수 있다. API 전환·기능 연결 통과와 매끄러운 실제 체험을 구분한다.
최초 검사에서 대기 리액션 준비 시간 초과가 있었고, 메모리 조정 뒤 등록 인물 준비에서는 리액션까지 성공했다.

Vox는 운영 서버와 같은 가중치·참조 방식·생성 10단계를 사용한다. 로컬에서만 CPU 스레드 1개와 KV 버퍼 4096을 사용하고
적재·참조 준비 뒤의 미사용 CUDA 예약 메모리를 반환한다. 합성 API의 문장 300자·참조 12초·생성 2000패치 상한은 유지한다.
다른 Unity 프로젝트·AI 프로그램·VR 런타임과 GPU 메모리를 공유하므로 실제 체험의 재생 속도·HMD 프레임은 별도 확인이 필요하다.

위10월31일 기록은 이 PC의 Unity Editor 시연 기준이다. 플레이어는 `--local-demo-config <설정 파일 경로>` 또는
`Application.persistentDataPath/LocalDemoConnection.json`으로도 연결을 지정할 수 있다.
11월5일 친구 전용 배포본은 시작 프로그램이 `--local-demo-config`로 별도 설정을 전달하고 현재 니트 몸체도 포함한다.
일반 새 얼굴 등록에 기존 니트 몸체를 자동으로 맞추는 기능을 추가한 것은 아니다.
