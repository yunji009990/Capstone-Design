"""체험 종료 후 사용자에게 노출되는 페이지.

- 감정 정리 가이드
- 전문 상담 자원 안내
- 본인 세션 코드로 데이터 즉시 폐기 기능
"""
from __future__ import annotations

import streamlit as st

from core import database, storage, styling

st.set_page_config(page_title="다시, 봄 — 체험 후", page_icon="🌷", layout="centered")
styling.inject()

st.title("체험을 마치고")
styling.gentle_note(
    "방금 체험은 강한 감정을 동반할 수 있습니다. 천천히, 한 번에 한 호흡씩 돌아오세요. "
    "아래의 짧은 안내가 도움이 될 거예요."
)

st.subheader("지금 잠시, 이렇게 해보세요")
st.markdown(
    """
    1. **3분 호흡** — 4초 들이쉬고, 6초 내쉬기를 반복해 보세요.
    2. **물 한 잔 마시기** — 몸을 현재로 되돌리는 가장 빠른 방법이에요.
    3. **누군가에게 한 줄 메시지 보내기** — 안부 한 마디면 충분합니다.
    4. **떠오른 감정 한 단어로 적어 두기** — 정리는 나중에 해도 괜찮아요.
    """
)

st.subheader("필요할 때 닿을 수 있는 곳")
st.markdown(
    """
    - **자살예방상담전화 1393** — 24시간, 전국 무료
    - **정신건강위기상담전화 1577-0199** — 24시간
    - **한국생명존중희망재단** — life.go.kr
    - **학교 학생상담센터** — 재학생 무료 상담
    """
)

st.divider()
st.subheader("내 데이터 폐기하기")
styling.gentle_warning(
    "체험에 사용된 이미지·음성·설문 내용을 지금 즉시 삭제할 수 있어요. "
    "삭제하면 복구되지 않습니다."
)

sid = st.text_input("체험 종료 시 안내받은 세션 코드 (예: 20260521-001)")
if st.button("이 코드의 데이터 모두 폐기", type="primary"):
    if not sid.strip():
        st.error("세션 코드를 입력해 주세요.")
    else:
        s = database.get_session(sid.strip())
        if not s or s["status"] == "deleted":
            st.error("해당 코드를 찾을 수 없거나 이미 폐기된 데이터입니다.")
        else:
            storage.purge_assets(sid.strip())
            database.soft_delete(sid.strip())
            st.success("폐기가 완료되었습니다. 천천히 돌아오세요.")
