# 다시, 봄 — 등록 웹

2026-09-12 등록 API와 Tripo 작업자를 웹 영역에 묶고 대화 AI와 독립 실행·배포하도록 분리했다.
운영 명령·데이터 계약: [서비스 분리 문서](../docs/웹_Tripo_대화AI_서비스_분리.md).
팀원용 별도 포트·데이터·Python 환경은 [Tripo 개발환경 실행 가이드](../docs/Tripo_개발환경_실행가이드.md)를 따른다.
`requirements.txt`는 웹 환경의 직접 의존성 목록이며 등록 API·Tripo worker의 requirements와 구분한다.

영상·음성에서 참조 음성을 준비하고 인물 설정과 함께 **CPU 등록 서버**에 보낸다.
Qwen3-TTS 음성 대화는 별도 서비스이며, 등록 과정에서 GPU 모델을 로드하거나 참조 음성을 다시 합성하지 않는다.
음성 답변은 Qwen3-TTS Base로 등록된 참조 목소리·말투를 반영한다. 6–12초의 연속 녹음을 권장한다.
등록은 원래 쉼과 말하기 속도를 보존한다. 대화 서버가 최대 12초 구간을 선택하고 그 구간을 전사한다.

## 실행

웹은 `~/webapp`에서 서버의 Python 환경 `~/venv/web`으로 실행된다.
브라우저 주소는 `http://220.69.208.201:8500`이다.

```bash
ssh raon bash /home/crc_unity/webapp/web_start.sh
ssh raon bash /home/crc_unity/webapp/web_stop.sh
```

설정은 서버 `~/webapp/Web/.env`. `SESSION_URL=http://127.0.0.1:8000`,
`SESSION_TOKEN`을 등록 API의 `SESSION_TOKEN`과 일치시킨다. 코드에 기본 접속 토큰은 없다.
기존 `RAON_URL/RAON_TOKEN` 이름과 Raon bootstrap·억양 토글은 폐기했다.
파일과 환경을 고친 뒤 웹을 재시작하고 `/status`에서 등록 API 연결을 확인한다.

## 등록과 3D

동의 → 음성·사진 업로드 → 인물 정보·추억·말투 입력 → 화자·인물 확인 → 등록.
사진이 없으면 3D 생성 없이 인물만 등록된다. 웹은 SQLite에 생성 작업을 넣고,
`Survey/model_worker.py`가 별도 프로세스에서 Tripo 요청·결과 다운로드·등록 API 전달을 수행한다.
완성된 GLB가 `/session/{sid}/model`로 전달된 뒤 완료 상태가 된다. Unity가 현재 인물과 GLB를 조회한다.
웹을 재시작해도 작업 ID는 남는다. `/status`의 `model_worker`에서 작업자 상태를 확인한다.
참조 음성은 24kHz 모노로 변환하며 `/last_ref.wav`에서 실제 전송 파일을 확인할 수 있다.

서버에는 현재 화자 분리용 `nemo_env`가 없으므로 `한 명 (분리 안 함)`으로 등록한다.
화자 분리 도구 설정은 [extraction/README](extraction/README.md)를 따른다.
웹은 파일 업로드 방식이며, 브라우저 마이크를 새로 도입하면 HTTPS 연결도 준비해야 한다.

3D API 키는 `Tools > 서버 연결 상태 확인` 또는 관리자 인증이 필요한 `/admin/tripo`로 설정한다.
키의 원문은 반환하지 않는다. 참가자 자료·참조 음성·API 키·관리자 비밀번호를 저장소에 넣지 않는다.
관리자 비밀번호가 비어 있으면 관리자 API는 설정 오류로 접근을 거부한다.
Tripo 키 변경은 작업자가 다음 작업을 시작할 때 읽는다. 대화 AI 재시작은 필요하지 않다.

전체 운영: [Server/README](../Server/README.md). 과거 음성 실험: [Raon 작업 이력](../docs/Raon_작업이력_보관.md).
