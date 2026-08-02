"""'다시, 봄' 톤에 맞는 따듯한 CSS 주입.

기획안 톤: 부드러움, 따듯함, 봄. 어둡지도 차갑지도 않으며 위로의 색감.
"""
import streamlit as st

_CSS = """
<style>
    /* 전체 배경 — 따듯한 베이지 */
    .stApp {
        background: linear-gradient(180deg, #FBF7F2 0%, #F5EDE2 100%);
    }
    /* 메인 컬럼 가독성 */
    .block-container {
        padding-top: 2.2rem;
        padding-bottom: 4rem;
        max-width: 760px;
    }
    /* 제목·헤더 */
    h1, h2, h3, h4 {
        color: #4A3633;
        letter-spacing: -0.01em;
    }
    h1 { font-weight: 600; }
    /* 본문 */
    p, label, .stMarkdown {
        color: #3E2D2A;
        line-height: 1.65;
    }
    /* 카드형 컨테이너 */
    div[data-testid="stVerticalBlockBorderWrapper"] {
        background-color: #FFFFFFCC;
        border-radius: 14px;
    }
    /* Primary 버튼 — 부드러운 로즈 */
    .stButton > button[kind="primary"], .stFormSubmitButton > button {
        background: linear-gradient(135deg, #D8A2B3 0%, #C58FA0 100%);
        color: #FFFFFF;
        border: none;
        border-radius: 12px;
        padding: 0.55rem 1.4rem;
        font-weight: 500;
        box-shadow: 0 2px 6px rgba(197, 143, 160, 0.25);
        transition: transform 120ms ease;
    }
    .stButton > button[kind="primary"]:hover, .stFormSubmitButton > button:hover {
        transform: translateY(-1px);
        filter: brightness(1.04);
    }
    /* Secondary 버튼 */
    .stButton > button {
        border-radius: 12px;
        border: 1px solid #E2D2C2;
        background-color: #FFFFFF;
        color: #6B4F49;
    }
    /* 입력 필드 */
    .stTextInput input, .stTextArea textarea, .stSelectbox div[data-baseweb="select"] > div {
        background-color: #FFFFFF !important;
        border-radius: 10px !important;
        border-color: #E2D2C2 !important;
    }
    .stTextInput input:focus, .stTextArea textarea:focus {
        border-color: #C58FA0 !important;
        box-shadow: 0 0 0 2px rgba(197, 143, 160, 0.18) !important;
    }
    /* 단계 배지 */
    .step-badge {
        display: inline-block;
        background-color: #F2E0D2;
        color: #8B5A50;
        padding: 4px 12px;
        border-radius: 999px;
        font-size: 12px;
        letter-spacing: 0.04em;
        margin-bottom: 12px;
    }
    /* 부드러운 안내 박스 */
    .gentle-note {
        background-color: #F5E6D8;
        border-left: 3px solid #C58FA0;
        padding: 12px 16px;
        border-radius: 8px;
        color: #5A4540;
        font-size: 14px;
        margin: 12px 0;
        line-height: 1.6;
    }
    .gentle-warning {
        background-color: #FAE9E0;
        border-left: 3px solid #D88979;
        padding: 12px 16px;
        border-radius: 8px;
        color: #6B3E33;
        font-size: 14px;
        margin: 12px 0;
        line-height: 1.6;
    }
    /* Streamlit 기본 success/info 박스도 톤 맞춤 */
    div[data-baseweb="notification"] {
        border-radius: 10px;
    }
    /* 사이드바 살짝 다듬기 */
    section[data-testid="stSidebar"] {
        background-color: #F2E7DC;
    }
</style>
"""


def inject():
    st.markdown(_CSS, unsafe_allow_html=True)


def step_badge(text: str):
    st.markdown(f'<span class="step-badge">{text}</span>', unsafe_allow_html=True)


def gentle_note(text: str):
    st.markdown(f'<div class="gentle-note">{text}</div>', unsafe_allow_html=True)


def gentle_warning(text: str):
    st.markdown(f'<div class="gentle-warning">{text}</div>', unsafe_allow_html=True)
