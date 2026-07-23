from __future__ import annotations

import base64
import hashlib
import hmac
import random
import time
from datetime import datetime, timedelta, timezone

import streamlit as st
from streamlit_cookies_controller import CookieController

from src.db import download_database, load_meetings
from src.generator import generate_meeting
from src.retrieval import CATEGORIES, search_similar, select_participants
from src.ui import result_box

st.set_page_config(page_title="회의 뭐했니? v1.0", page_icon="📝", layout="centered")

st.markdown("""
<style>
.block-container {max-width: 920px; padding-top: 2rem; padding-bottom: 4rem;}
h1 {letter-spacing:-0.04em;}
div[data-testid="stRadio"] label, div[data-testid="stSelectbox"] label {font-weight:700;}
.stButton button {width:100%; border-radius:10px; font-weight:800; min-height:46px;}
</style>
""", unsafe_allow_html=True)

st.title("회의 뭐했니? v1.0")
st.caption("더 이상 사다리타기가 두렵지 않습니다.")

AUTH_COOKIE_NAME = "meeting_generator_auth_v1"

if "cookie_controller" not in st.session_state:
    st.session_state["cookie_controller"] = CookieController(key="meeting_generator_cookies")
cookie_controller: CookieController = st.session_state["cookie_controller"]

try:
    db_path = download_database()
    meetings = load_meetings(str(db_path))
except Exception as exc:
    st.error(f"DB를 불러오지 못했습니다. Streamlit Secrets와 GCS 권한을 확인하세요.\n\n{exc}")
    st.stop()


def _required_secret(name: str) -> str:
    value = st.secrets.get(name)
    if value is None or not str(value).strip():
        raise RuntimeError(f"필수 Secret이 없습니다: {name}")
    return str(value)


def _required_password() -> str:
    return _required_secret("MEETING_GENERATOR_PASSWORD")


def _auth_secret() -> str:
    return _required_secret("DEVICE_AUTH_SECRET")


def _auth_days() -> int:
    try:
        days = int(st.secrets.get("DEVICE_AUTH_DAYS", 365))
    except (TypeError, ValueError):
        days = 365
    return max(1, min(days, 3650))


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _password_fingerprint(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()[:16]


def _create_auth_token(password: str) -> tuple[str, int]:
    expires_at = int(time.time()) + (_auth_days() * 24 * 60 * 60)
    payload = f"v1|{expires_at}|{_password_fingerprint(password)}"
    signature = hmac.new(
        _auth_secret().encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    token = f"{_b64encode(payload.encode('utf-8'))}.{_b64encode(signature)}"
    return token, expires_at


def _is_valid_auth_token(token: object) -> bool:
    if not isinstance(token, str) or "." not in token:
        return False

    try:
        payload_part, signature_part = token.split(".", 1)
        payload = _b64decode(payload_part).decode("utf-8")
        supplied_signature = _b64decode(signature_part)
        version, expires_text, password_fingerprint = payload.split("|", 2)
        expires_at = int(expires_text)

        if version != "v1" or expires_at <= int(time.time()):
            return False
        if not hmac.compare_digest(
            password_fingerprint,
            _password_fingerprint(_required_password()),
        ):
            return False

        expected_signature = hmac.new(
            _auth_secret().encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        return hmac.compare_digest(supplied_signature, expected_signature)
    except (ValueError, TypeError, UnicodeDecodeError, RuntimeError):
        return False


def _browser_is_authenticated() -> bool:
    if st.session_state.get("browser_authenticated") is True:
        return True

    token = cookie_controller.get(AUTH_COOKIE_NAME)
    valid = _is_valid_auth_token(token)
    if valid:
        st.session_state["browser_authenticated"] = True
    return valid


def _remember_browser(password: str) -> None:
    token, expires_at = _create_auth_token(password)
    expires = datetime.fromtimestamp(expires_at, tz=timezone.utc)
    cookie_controller.set(
        AUTH_COOKIE_NAME,
        token,
        path="/",
        expires=expires,
        max_age=float(_auth_days() * 24 * 60 * 60),
        secure=True,
        same_site="strict",
    )
    st.session_state["browser_authenticated"] = True


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


def _run_pending_generation() -> None:
    request = st.session_state.get("pending_generation")
    if not request:
        raise RuntimeError("생성 요청 정보가 없습니다. 다시 시도하세요.")

    _create_meeting(request)
    st.session_state.pop("pending_generation", None)
    st.session_state.pop("generation_password", None)


@st.dialog("비밀번호 확인", dismissible=True)
def password_dialog() -> None:
    st.write("Hint: 모두가 아는 그 4자리")
    password = st.text_input("비밀번호", type="password", key="generation_password", max_chars=64)

    if st.button("확인", type="primary", key="confirm_generation_password"):
        try:
            expected = _required_password()
        except RuntimeError as exc:
            st.error(str(exc))
            return

        if not hmac.compare_digest(password, expected):
            st.session_state["generation_password"] = ""
            st.error("비밀번호가 올바르지 않습니다.")
            return

        try:
            _remember_browser(password)
            with st.spinner("유사 회의와 실제 외부 참석자를 검색하고 있습니다..."):
                _run_pending_generation()
        except Exception as exc:
            st.error(str(exc))
            return

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
    generate_clicked = st.button("생성하기", type="primary")
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

        if _browser_is_authenticated():
            with st.spinner("유사 회의와 실제 외부 참석자를 검색하고 있습니다..."):
                try:
                    _run_pending_generation()
                except Exception as exc:
                    st.error(str(exc))
                else:
                    st.rerun()
        else:
            password_dialog()

if "result" in st.session_state:
    st.divider()
    st.subheader("생성 결과")
    r = st.session_state["result"]
    result_box("1. 회의 목적", r["purpose"], "purpose", height=145)
    result_box("2. 참석자 명단", r["participants"], "participants", height=155)
    result_box("3. 회의 내용", r["content"], "content", height=330)
