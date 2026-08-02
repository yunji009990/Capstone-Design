# 다시, 봄 — 설문 데이터베이스 시스템

VR 체험 "다시, 봄"의 사전 설문지·이미지·음성을 수집하고, 생성형 AI 파이프라인이 사용할 수 있는 페르소나 JSON으로 저장하는 로컬 웹 앱.

## 실행 (Windows)

```powershell
cd "C:\Users\user\Desktop\다시봄_설문시스템"
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app.py --server.address=0.0.0.0 --server.port=8501
```

다른 PC에서 접속: `http://<이 PC의 IP>:8501`
(같은 와이파이/내부망 안에서만)

관리자 페이지는 사이드바의 `🔐 관리자`. 기본 비밀번호는 `.streamlit/secrets.toml`에서 변경.

## 폴더

- `app.py` — 단계형 설문 메인 앱
- `pages/` — 관리자, 체험 후 안내 (Streamlit 멀티페이지 자동 인식)
- `core/` — DB·스토리지·프롬프트 빌더·스타일링
- `data/sessions.db` — SQLite (자동 생성)
- `data/sessions/<session_id>/` — 업로드 자산 (이미지·음성)

## 데이터 흐름

```
사용자 입력 (브라우저)
   ↓
세션 ID 발급 (yyyymmdd-NNN)
   ↓
SQLite: sessions 테이블 (메타 + JSON 페이로드)
파일시스템: data/sessions/<id>/front.jpg, voice.wav
   ↓
core/prompt_builder.build_persona_prompt(session_id)
   ↓
LLM 시스템 프롬프트로 주입 → VR 클라이언트가 사용
```

## 윤리 안전장치

- 사전 심리 안내 + 사별 시점 체크 (4주 이내 → 전문 상담 권유 후 진행 의사 재확인)
- 동의 3종 (이미지 사용 · 음성 사용 · "실제 고인 아님" 이해)
- 세션 ID로 본인이 직접 삭제 가능 (체험 후 페이지)
- 관리자에서 일괄 조회·다운로드·삭제

## 관리자 비밀번호 설정

`.streamlit/secrets.toml`:
```toml
admin_password = "change_me"
```
