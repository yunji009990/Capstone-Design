# Survey — 설문 저장·3D 모델 라이브러리

**앱이 아니라 라이브러리다.** 웹 백엔드(`Web/app.py`)가 `core/` 를 그대로 가져다 쓴다.

원래는 Streamlit 설문 앱이었고 `app.py` 와 `pages/` 가 화면을 그렸다. 지금은
`Web/` 이 그 일을 전부 하므로 화면 쪽은 지웠다. 남은 것은 화면이 없는 부분 —
DB 스키마, 파일 보관, 3D 모델 작업 추적 — 뿐이다. 다시 필요하면 git 이력에 있다.

## 구성

| | |
|---|---|
| `core/database.py` | SQLite. 세션 메타 + 설문 JSON, 소프트/하드 삭제 |
| `core/storage.py` | `data/sessions/<id>/` 에 사진·음성 보관, 폐기 |
| `core/jobs.py` | 사진 → 3D 모델 작업을 스레드로 띄우고 상태를 DB 에 쓴다 |
| `core/tripo.py` | Tripo image-to-3D 호출. **키가 없으면 stub** |
| `data/sessions.db` | 자동 생성 |

`data/` 는 참여자 자료라 저장소에 올라가지 않는다(`.gitignore`).

## 3D 모델 키

없으면 등록은 되고 모델만 `stub` 으로 끝난다. 대화는 정상이고 인물만 안 보인다.

```bash
TRIPO_API_KEY=tsk_... python -m uvicorn app:app --port 8500
```

## 쓰는 쪽

- 등록·조회·폐기 화면 — `Web/static/index.html`, `after.html`, `admin.html`
- 인물 글 만들기 — `Web/persona.py` (예전 `core/prompt_builder.py` 를 대체했다)

## 윤리 안전장치

화면이 `Web/` 으로 옮겨갔을 뿐 그대로다.

- 사별 시점 확인 (4주 이내 → 전문 상담 권유)
- 동의 3종 (사진 사용 · 음성 사용 · "실제 고인 아님" 이해)
- 세션 코드로 본인이 직접 폐기 (`/after`)
- 관리자에서 일괄 조회·내려받기·삭제 (`/admin`)
