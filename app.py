from __future__ import annotations

import hmac
import random

import streamlit as st

from src.db import download_database, load_meetings
from src.generator import generate_meeting
from src.retrieval import CATEGORIES, search_similar, select_participants
from src.ui import result_box

st.set_page_config(page_title="회의록 자동생성", page_icon="📝", layout="centered")

st.markdown("""
<style>
.block-container {max-width: 920px; padding-top: 2rem; padding-bottom: 4rem;}
h1 {letter-spacing:-0.04em;}
div[data-testid="stRadio"] label, div[data-testid="stSelectbox"] label {font-weight:700;}
.stButton button {width:100%; border-radius:10px; font-weight:800; min-height:46px;}
</style>
""", unsafe_allow_html=True)

st.title("회의록 자동생성")
st.caption("비공개 회의록 DB 검색과 ChatGPT API를 결합하여 회의 목적·참석자·회의내용을 생성합니다.")

try:
    db_path = download_database()
    meetings = load_meetings(str(db_path))
except Exception as exc:
    st.error(f"DB를 불러오지 못했습니다. Streamlit Secrets와 GCS 권한을 확인하세요.\n\n{exc}")
    st.stop()


def _required_password() -> str:
    value = st.secrets.get("MEETING_GENERATOR_PASSWORD")
    if value is None or not str(value).strip():
        raise RuntimeError("필수 Secret이 없습니다: MEETING_GENERATOR_PASSWORD")
    return str(value)


def _create_meeting(request: dict[str, object]) -> None:
    mode = str(request["mode"])
    category = str(request.get("category") or "")
    user_input = str(request.get("user_input") or "")
    keep_exact = bool(request.get("keep_exact", False))

    query = user_input.strip() if user_input.strip() else random.choice(CATEGORIES[category])
    similar = search_similar(
        meetings,
        query=query,
        category=category,
        top_k=10,
        randomize=(mode == "카테고리만 선택하여 자동 생성"),
    )
    participants = select_participants(similar, max_people=5)
    if not participants:
        raise RuntimeError("조건에 맞는 외부 참석자를 DB에서 찾지 못했습니다. 다른 카테고리나 키워드를 사용하세요.")

    generated = generate_meeting(category or "사용자 입력 기반", user_input, similar, keep_exact)
    st.session_state["result"] = {
        "purpose": generated.meeting_purpose,
        "participants": ", ".join(participants),
        "content": "1. 회의내용\n" + "\n".join(f"- {x}" for x in generated.meeting_content)
        + "\n\n2. 향후계획\n" + "\n".join(f"- {x}" for x in generated.future_plan),
    }


@st.dialog("비밀번호 확인", dismissible=True)
def password_dialog() -> None:
    st.write("회의록 생성을 계속하려면 비밀번호를 입력하세요.")
    password = st.text_input("비밀번호", type="password", key="generation_password")

    if st.button("확인", type="primary", key="confirm_generation_password"):
        try:
            expected = _required_password()
        except RuntimeError as exc:
            st.error(str(exc))
            return

        if not hmac.compare_digest(password, expected):
            st.error("비밀번호가 올바르지 않습니다.")
            return

        request = st.session_state.get("pending_generation")
        if not request:
            st.error("생성 요청 정보가 없습니다. 창을 닫고 다시 시도하세요.")
            return

        with st.spinner("유사 회의와 실제 외부 참석자를 검색하고 있습니다..."):
            try:
                _create_meeting(request)
            except Exception as exc:
                st.error(str(exc))
                return

        st.session_state.pop("pending_generation", None)
        st.session_state.pop("generation_password", None)
        st.rerun()


mode = st.radio(
    "생성 방식",
    ["카테고리만 선택하여 자동 생성", "키워드 또는 회의 목적 입력"],
    horizontal=True,
)

category = ""
user_input = ""
keep_exact = False

if mode == "카테고리만 선택하여 자동 생성":
    category = st.selectbox("카테고리", list(CATEGORIES.keys()))
else:
    user_input = st.text_area(
        "키워드 또는 회의 목적",
        placeholder="예: 수소전환 기술의 기업수요 발굴 및 후속 공동연구 협의",
        height=100,
    )
    keep_exact = st.checkbox("입력한 문장을 회의 목적으로 그대로 사용", value=False)

col1, col2 = st.columns([3, 1])
with col1:
    generate_clicked = st.button("회의록 생성", type="primary")
with col2:
    st.button("결과 초기화", on_click=lambda: st.session_state.pop("result", None))

if generate_clicked:
    if mode == "키워드 또는 회의 목적 입력" and not user_input.strip():
        st.warning("키워드 또는 회의 목적을 입력하세요.")
    else:
        st.session_state["pending_generation"] = {
            "mode": mode,
            "category": category,
            "user_input": user_input,
            "keep_exact": keep_exact,
        }
        password_dialog()

if "result" in st.session_state:
    st.divider()
    st.subheader("생성 결과")
    r = st.session_state["result"]
    result_box("1. 회의 목적", r["purpose"], "purpose", height=145)
    result_box("2. 참석자 명단", r["participants"], "participants", height=155)
    result_box("3. 회의 내용", r["content"], "content", height=330)
