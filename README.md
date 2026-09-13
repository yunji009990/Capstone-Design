# 다시, 봄

Unity에서 사용자의 음성과 행동을 받아 인물 설정에 맞게 음성으로 대화하는 VR 캡스톤.
현재는 TTS를 끄고 **SenseVoiceSmall → 판정기 → Gemma → 텍스트 답변**을 검증한다.

현재 우리 담당은 **대화 AI**, T포즈·3D 제작은 팀원 담당이다.
AI 작업은 [공통 지침](AGENTS.md) → [하네스 사용법](docs/AI_하네스.md) →
[대화 AI 개발 가이드](docs/대화_AI_개발가이드.md) 순서로 시작한다.
기본 모의 검사 명령은 `python tools/check.py --area dialogue`다.

- [서버 구조와 모델](docs/서버_음성대화_구조.md)
- [웹·Tripo와 대화 AI 독립 작업·배포](docs/웹_Tripo_대화AI_서비스_분리.md)
- [Tripo 팀원·AI 작업 지시서](docs/Tripo_팀원_AI_작업지시서.md) · [개발환경 실행 가이드](docs/Tripo_개발환경_실행가이드.md)
- [등록 없는 음성 테스트 씬](docs/AI_응답_테스트_씬.md)
- [판정기 텍스트 검사 결과·재실행](docs/판정기_텍스트_검사.md)
- [서버 실행·배포](Server/README.md)
- [인물·3D 등록 웹](Web/README.md)
- [Raon 작업 이력 보관](docs/Raon_작업이력_보관.md)

Unity 2022.3.62f2. `Tools > Dialogue > Open AI test scene`을 열고 Play 후
마이크로 질문하고 텍스트 답변을 확인한다. TTS를 다시 켜면 Qwen3-TTS Base가 참조 목소리로 답한다.
답변 생성 중 끼어들면 잠시 멈추고, 발화 의도에 따라 이어 말하기·수정·주제 전환·대기를 선택한다.
