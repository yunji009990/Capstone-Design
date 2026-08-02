"""다시, 봄 — 사전 설문 입력 메인 앱.

7단계 wizard:
  0. 시작 + 동의 + 사전 심리 안내
  1. 기본 정보 (관계·호칭·추억·성격·말투)
  2. 감정 정보 (그리운 순간·못다 한 말·듣고 싶은 말)
  3. 대화 톤 선택
  4. 이미지 업로드 (정면 전신)
  5. 음성 업로드 (실제 목소리 / 유사 매칭 선택)
  6. 확인 및 제출 → 세션 ID 발급
"""
from __future__ import annotations

import streamlit as st

from core import database, jobs, storage, styling

# ── 페이지 기본 설정 ───────────────────────────────────────────
st.set_page_config(
    page_title="다시, 봄 — 사전 설문",
    page_icon="🌸",
    layout="centered",
)
styling.inject()


# ── 접근 코드 게이트 (외부 공개 환경 보호) ─────────────────────
def _require_access_code() -> bool:
    """secrets에 survey_access_code가 설정돼 있으면 입력 게이트를 강제.
    빈 문자열/미설정이면 게이트 비활성(LAN 전용 환경 가정)."""
    expected = (st.secrets.get("survey_access_code", "") or "").strip()
    if not expected:
        return True  # 게이트 없음
    if st.session_state.get("survey_unlocked"):
        return True

    st.title("다시, 봄")
    st.caption("초대받으신 분에게만 안내되는 접근 코드가 필요합니다.")
    styling.gentle_note(
        "이 페이지는 사전 안내를 받은 참여자에게만 열립니다. "
        "코드는 운영자가 알려드린 영문/숫자 조합으로 입력해 주세요."
    )
    code = st.text_input("접근 코드", type="password", max_chars=64)
    if st.button("입력", type="primary"):
        if code.strip().upper() == expected.upper():
            st.session_state.survey_unlocked = True
            st.rerun()
        else:
            st.error("코드가 일치하지 않습니다.")
    return False


if not _require_access_code():
    st.stop()

# ── 세션 상태 초기화 ───────────────────────────────────────────
STEP_LABELS = [
    "시작",
    "기본 정보",
    "마음",
    "대화 톤",
    "이미지",
    "음성",
    "확인",
]
TOTAL_STEPS = len(STEP_LABELS)

if "step" not in st.session_state:
    st.session_state.step = 0
if "data" not in st.session_state:
    st.session_state.data = {
        "consent": {"image": False, "voice": False, "understand": False},
        "screening": {"bereavement_weeks": None, "proceed_anyway": False},
        "basic": {
            "relation": "", "honorific": "",
            "shared_memories": ["", "", ""],
            "personality_traits": [], "personality_other": "",
            "speech_quirks": ["", "", ""],
        },
        "emotion": {"missed_moment": "", "unsaid_words": "", "wished_to_hear": ""},
        "tone_setting": "warm_comfort",
        "image_uploaded": None,   # UploadedFile (메모리)
        "voice_uploaded": None,
    }


def goto(step: int) -> None:
    st.session_state.step = max(0, min(TOTAL_STEPS - 1, step))
    st.rerun()


def progress_header():
    step = st.session_state.step
    styling.step_badge(f"단계 {step + 1} / {TOTAL_STEPS}  ·  {STEP_LABELS[step]}")
    st.progress((step + 1) / TOTAL_STEPS)


def nav_buttons(can_proceed: bool, *, last: bool = False):
    cols = st.columns([1, 1, 2])
    if st.session_state.step > 0:
        if cols[0].button("← 이전"):
            goto(st.session_state.step - 1)
    label = "제출하기" if last else "다음 →"
    if cols[2].button(label, type="primary", disabled=not can_proceed, use_container_width=True):
        if last:
            submit()
        else:
            goto(st.session_state.step + 1)


# ─────────────────────────────────────────────────────────────
# Step 0 — 시작 / 동의 / 사전 심리 안내
# ─────────────────────────────────────────────────────────────
def render_intro():
    st.title("다시, 봄")
    st.caption("그리운 사람과 다시 만나는 VR 체험을 위한 사전 설문지입니다.")

    styling.gentle_note(
        "이 설문은 체험 중 만나게 될 인물의 말투·기억·성격을 빚는 데 쓰입니다. "
        "정답이 없으니 떠오르는 대로 적어 주세요. 중간에 어렵게 느껴지면 잠시 쉬어도 괜찮습니다."
    )

    st.subheader("먼저, 확인해 주실 것이 있어요")

    styling.gentle_warning(
        "이 체험에서 만나게 될 인물은 <b>실제 고인이 아닌, 당신의 기억과 설문 답변을 바탕으로 한 재현</b>입니다. "
        "위로와 작별의 공간이지만, 실제로 돌아온 사람은 아니라는 점을 기억해 주세요.",
    )

    d = st.session_state.data["consent"]
    d["understand"] = st.checkbox(
        "이 체험의 인물이 실제 고인이 아닌 재현임을 이해합니다.",
        value=d["understand"],
    )
    d["image"] = st.checkbox(
        "고인의 이미지를 업로드하고, 체험 종료 후 안전하게 폐기되는 것에 동의합니다.",
        value=d["image"],
    )
    d["voice"] = st.checkbox(
        "고인의 음성 자료(있는 경우)를 업로드하고, 체험 종료 후 안전하게 폐기되는 것에 동의합니다.",
        value=d["voice"],
    )

    st.markdown("---")
    st.subheader("사전 심리 안내")
    _prev = st.session_state.data["screening"]["bereavement_weeks"]
    weeks_str = st.text_input(
        "사별 이후 대략 어느 정도 시간이 지나셨나요? (주 단위, 모르면 0)",
        value="" if _prev is None else str(_prev),
        placeholder="예: 8",
        max_chars=4,
    )
    weeks_str = weeks_str.strip()
    if weeks_str == "":
        weeks = 0
    elif weeks_str.isdigit():
        weeks = min(520, int(weeks_str))
    else:
        st.caption("⚠ 숫자만 입력해 주세요. (모르면 0)")
        weeks = 0
    st.session_state.data["screening"]["bereavement_weeks"] = weeks

    if 0 < weeks <= 4:
        styling.gentle_warning(
            "사별 직후 4주 이내는 감정이 가장 격해지는 시기로 알려져 있어요. "
            "이 체험은 강한 정서적 반응을 일으킬 수 있으니, 가능하다면 "
            "<b>전문 애도 상담을 먼저 권유</b>드립니다. "
            "그래도 진행하고 싶다면 아래 동의에 체크해 주세요."
        )
        st.session_state.data["screening"]["proceed_anyway"] = st.checkbox(
            "주의 안내를 읽었으며, 그래도 체험을 진행하고 싶습니다.",
            value=st.session_state.data["screening"]["proceed_anyway"],
        )
    else:
        st.session_state.data["screening"]["proceed_anyway"] = True

    consents_ok = d["understand"] and d["image"] and d["voice"]
    screening_ok = st.session_state.data["screening"]["proceed_anyway"]
    can_proceed = consents_ok and screening_ok
    if not consents_ok:
        st.caption("⚠ 세 가지 동의 항목 모두 체크해 주세요.")
    nav_buttons(can_proceed)


# ─────────────────────────────────────────────────────────────
# Step 1 — 기본 정보
# ─────────────────────────────────────────────────────────────
PERSONALITY_OPTIONS = ["다정함", "유머러스함", "차분함", "엄격함", "활달함", "걱정 많음", "다 받아줌", "고집 있음", "수다스러움", "조용함"]


def render_basic():
    st.title("기본 정보")
    styling.gentle_note(
        "그 사람을 떠올릴 때 가장 먼저 떠오르는 단어들을 자유롭게 적어 주세요. 짧아도 좋아요."
    )

    b = st.session_state.data["basic"]
    b["relation"] = st.text_input("그 사람과의 관계를 알려 주세요 (예: 어머니, 친구, 동생)",
                                  value=b["relation"], max_chars=20)
    b["honorific"] = st.text_input("평소 어떻게 부르셨나요? (예: 엄마, 형, 영수야)",
                                   value=b["honorific"], max_chars=20)

    st.subheader("함께한 추억")
    st.caption("떠오르는 장면 두세 가지면 충분해요.")
    for i in range(3):
        b["shared_memories"][i] = st.text_area(
            f"추억 {i + 1}",
            value=b["shared_memories"][i],
            height=70,
            placeholder="예: 둘이 처음 바다에 갔던 여름",
            label_visibility="collapsed",
        )

    st.subheader("성격")
    b["personality_traits"] = st.multiselect(
        "그 사람의 성격을 가장 잘 표현하는 것을 골라 주세요 (여러 개 가능)",
        PERSONALITY_OPTIONS,
        default=b["personality_traits"],
    )
    b["personality_other"] = st.text_input(
        "그 외에 떠오르는 성격이 있다면 자유롭게 적어 주세요",
        value=b["personality_other"],
    )

    st.subheader("자주 쓰던 말·말투")
    st.caption("입버릇처럼 자주 하던 표현이 있다면 적어 주세요. 대화 톤 재현에 큰 도움이 됩니다.")
    for i in range(3):
        b["speech_quirks"][i] = st.text_input(
            f"말투 {i + 1}",
            value=b["speech_quirks"][i],
            placeholder="예: 아이고~ / 밥은 먹었니",
            label_visibility="collapsed",
        )

    can_proceed = bool(b["relation"].strip()) and bool(b["honorific"].strip())
    if not can_proceed:
        st.caption("⚠ 관계와 호칭은 반드시 적어 주세요.")
    nav_buttons(can_proceed)


# ─────────────────────────────────────────────────────────────
# Step 2 — 감정 정보
# ─────────────────────────────────────────────────────────────
def render_emotion():
    st.title("마음")
    styling.gentle_note(
        "지금 어렵게 느껴진다면, 한 칸만 채워도 괜찮아요. 답이 어려운 칸은 비워 두셔도 됩니다."
    )

    e = st.session_state.data["emotion"]
    e["missed_moment"] = st.text_area(
        "그 사람이 가장 그리운 순간은 언제인가요?",
        value=e["missed_moment"], height=110,
        placeholder="예: 저녁상 차릴 때, 비 오는 날 우산을 챙겨 주시던 모습",
    )
    e["unsaid_words"] = st.text_area(
        "전하지 못한 말이 있다면 무엇인가요?",
        value=e["unsaid_words"], height=110,
        placeholder="천천히 적으셔도 됩니다.",
    )
    e["wished_to_hear"] = st.text_area(
        "다시 한 번 듣고 싶은 말이 있다면 무엇인가요?",
        value=e["wished_to_hear"], height=110,
    )

    nav_buttons(can_proceed=True)


# ─────────────────────────────────────────────────────────────
# Step 3 — 대화 톤
# ─────────────────────────────────────────────────────────────
TONE_OPTIONS = {
    "warm_comfort": ("따듯한 위로형",
                     "사용자를 부드럽게 위로하고 안심시키는 톤으로 응답합니다."),
    "casual_recreation": ("평소 대화 재현형",
                          "생전 평소 말투와 어조를 그대로 살려 자연스럽게 대화합니다."),
    "free_dialogue": ("자유 대화형",
                      "주제·흐름을 사용자에게 맡기고, 무엇이든 부드럽게 받아 줍니다."),
}


def render_tone():
    st.title("대화 톤")
    styling.gentle_note("체험 중 인물이 어떤 어조로 응답하길 원하는지 골라 주세요.")

    current = st.session_state.data["tone_setting"]
    for key, (label, desc) in TONE_OPTIONS.items():
        selected = (current == key)
        if st.button(
            f"{'● ' if selected else '○ '}{label}\n\n{desc}",
            key=f"tone_{key}",
            use_container_width=True,
            type="primary" if selected else "secondary",
        ):
            st.session_state.data["tone_setting"] = key
            st.rerun()

    nav_buttons(can_proceed=True)


# ─────────────────────────────────────────────────────────────
# Step 4 — 이미지 업로드
# ─────────────────────────────────────────────────────────────
def render_image():
    st.title("이미지 업로드")
    styling.gentle_note(
        "<b>정면 전신 사진 한 장</b>을 올려 주세요. AI가 인물의 외형을 3D로 재현하는 기반이 됩니다. "
        "얼굴이 또렷이 보이고, 다른 사람이 함께 찍히지 않은 사진이 좋아요."
    )

    uploaded = st.file_uploader(
        "JPG / PNG 파일, 최대 50MB",
        type=["jpg", "jpeg", "png"],
        accept_multiple_files=False,
        key="img_uploader",
    )
    if uploaded is not None:
        st.session_state.data["image_uploaded"] = uploaded

    img = st.session_state.data["image_uploaded"]
    if img is not None:
        st.image(img, caption=f"미리보기: {img.name}", use_container_width=True)

    can_proceed = img is not None
    if not can_proceed:
        st.caption("⚠ 이미지 한 장이 필요합니다.")
    nav_buttons(can_proceed)


# ─────────────────────────────────────────────────────────────
# Step 5 — 음성 업로드
# ─────────────────────────────────────────────────────────────
def render_voice():
    st.title("음성")
    styling.gentle_note(
        "그 사람의 목소리가 담긴 녹음 파일을 올려 주세요. "
        "한 사람이 10~30초 끊지 않고 이어서 말하는 음성이 가장 좋습니다."
    )

    uploaded = st.file_uploader(
        "WAV / MP3 / M4A / OGG / FLAC, 최대 50MB",
        type=["wav", "mp3", "m4a", "ogg", "flac"],
        accept_multiple_files=False,
        key="voice_uploader",
    )
    if uploaded is not None:
        st.session_state.data["voice_uploaded"] = uploaded

    v = st.session_state.data["voice_uploaded"]
    if v is not None:
        st.audio(v)
    can_proceed = v is not None
    if not can_proceed:
        st.caption("⚠ 녹음 파일 한 개가 필요합니다.")

    nav_buttons(can_proceed)


# ─────────────────────────────────────────────────────────────
# Step 6 — 확인 및 제출
# ─────────────────────────────────────────────────────────────
def render_confirm():
    st.title("확인")
    styling.gentle_note("적어 주신 내용을 한 번 더 살펴보시고, 괜찮다면 제출해 주세요.")

    d = st.session_state.data
    b, e = d["basic"], d["emotion"]
    with st.container(border=True):
        st.markdown(f"**관계** — {b['relation']}  /  **호칭** — {b['honorific']}")
        st.markdown("**함께한 추억**")
        for m in [m for m in b["shared_memories"] if m.strip()]:
            st.markdown(f"  · {m}")
        traits = b["personality_traits"] + ([b["personality_other"]] if b["personality_other"].strip() else [])
        if traits:
            st.markdown("**성격** — " + ", ".join(traits))
        quirks = [q for q in b["speech_quirks"] if q.strip()]
        if quirks:
            st.markdown("**자주 쓰던 말** — " + " / ".join(f'"{q}"' for q in quirks))
        st.markdown("---")
        if e["missed_moment"]: st.markdown(f"**그리운 순간** — {e['missed_moment']}")
        if e["unsaid_words"]:  st.markdown(f"**전하지 못한 말** — {e['unsaid_words']}")
        if e["wished_to_hear"]: st.markdown(f"**듣고 싶은 말** — {e['wished_to_hear']}")
        st.markdown("---")
        tone_label = TONE_OPTIONS[d["tone_setting"]][0]
        st.markdown(f"**대화 톤** — {tone_label}")
        st.markdown(f"**이미지** — {'업로드됨 (' + d['image_uploaded'].name + ')' if d['image_uploaded'] else '없음'}")
        st.markdown("**음성** — " + (d['voice_uploaded'].name if d['voice_uploaded'] else '없음'))

    nav_buttons(can_proceed=True, last=True)


# ─────────────────────────────────────────────────────────────
# 제출
# ─────────────────────────────────────────────────────────────
def submit():
    d = st.session_state.data
    payload = {
        "basic": {
            "relation": d["basic"]["relation"].strip(),
            "honorific": d["basic"]["honorific"].strip(),
            "shared_memories": [m.strip() for m in d["basic"]["shared_memories"] if m.strip()],
            "personality_traits": d["basic"]["personality_traits"]
                + ([d["basic"]["personality_other"].strip()] if d["basic"]["personality_other"].strip() else []),
            "speech_quirks": [q.strip() for q in d["basic"]["speech_quirks"] if q.strip()],
        },
        "emotion": {k: v.strip() for k, v in d["emotion"].items()},
        "tone_setting": d["tone_setting"],
    }

    session_id = database.next_session_id()

    has_image = False
    if d["image_uploaded"] is not None:
        storage.save_image(session_id, d["image_uploaded"])
        has_image = True

    has_voice = False
    if d["voice_uploaded"] is not None:
        storage.save_voice(session_id, d["voice_uploaded"])
        has_voice = True

    database.insert_session(
        session_id=session_id,
        payload=payload,
        consent_image=d["consent"]["image"],
        consent_voice=d["consent"]["voice"],
        consent_understand=d["consent"]["understand"],
        bereavement_weeks=d["screening"]["bereavement_weeks"],
        has_image=has_image,
        has_voice=has_voice,
    )

    # 이미지가 있다면 곧바로 Meshy 3D 모델 작업을 백그라운드로 디스패치.
    # API 키 없으면 stub 상태로 즉시 마킹만 하고 끝남(네트워크 호출 X).
    if has_image:
        jobs.dispatch_model_job(session_id)

    # Unity 쪽 PersonaSpawner가 자동으로 픽업하도록 "최신 세션 ID" 포인터 파일 작성.
    # 같은 PC에서 Streamlit과 Unity가 동작할 때, 별도 네트워크 호출 없이 이 파일만 읽으면 됨.
    try:
        from pathlib import Path as _P
        ptr = _P(database.DB_PATH).parent / "current_session.txt"
        ptr.write_text(session_id, encoding="utf-8")
    except Exception:
        pass  # 포인터 작성 실패는 치명적이지 않음

    st.session_state.submitted_id = session_id
    st.session_state.step = TOTAL_STEPS  # 완료 화면으로


def render_done():
    st.balloons()
    st.title("고마워요. 잘 받았습니다.")
    sid = st.session_state.get("submitted_id", "-")
    styling.gentle_note(
        f"체험 세션 코드는 <b>{sid}</b> 입니다. "
        "체험 후 데이터를 직접 폐기하고 싶다면 사이드바의 <b>체험 후</b> 페이지에서 이 코드를 입력해 주세요."
    )

    # 3D 모델 진행 상태 미리보기
    s = database.get_session(sid) if sid != "-" else None
    if s and s.get("has_image"):
        ms = s.get("model_status", "stub")
        labels = {
            "stub": "AI 3D 모델 생성은 운영자가 별도로 처리할 예정입니다.",
            "queued": "AI가 곧 인물의 모습을 빚기 시작합니다.",
            "processing": "AI가 인물의 모습을 빚고 있어요. 잠시 기다려 주세요.",
            "ready": "AI 3D 모델 준비가 끝났습니다.",
            "failed": "AI 3D 모델 생성에 문제가 있었어요. 운영자에게 알려 주세요.",
        }
        styling.gentle_note(labels.get(ms, ms))

    if st.button("처음으로 돌아가기"):
        # 새 사용자가 같은 PC에서 이어 입력할 수 있게 모두 초기화
        for k in ("step", "data", "submitted_id"):
            st.session_state.pop(k, None)
        st.rerun()


# ─────────────────────────────────────────────────────────────
# 라우팅
# ─────────────────────────────────────────────────────────────
RENDERERS = [
    render_intro, render_basic, render_emotion,
    render_tone, render_image, render_voice, render_confirm,
]

step = st.session_state.step
if step >= TOTAL_STEPS:
    render_done()
else:
    progress_header()
    RENDERERS[step]()
