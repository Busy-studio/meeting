from __future__ import annotations

import base64
import hashlib
import hmac
import random
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import streamlit as st
from streamlit_cookies_controller import CookieController

from src.db import load_businesses, load_meetings, load_participant_links, save_business, save_final_meeting
from src.exporter import build_meeting_workbook, compose_content_block, export_filename
from src.generator import generate_meeting
from src.retrieval import CATEGORIES, parse_participants_for_save, search_similar, select_participants
from src.ui import business_from_state, render_business_form, trigger_file_download


st.set_page_config(page_title="회의 뭐했니? v1.1", page_icon="📝", layout="centered")

st.markdown(
    """
<style>
.block-container {max-width: 980px; padding-top: 2rem; padding-bottom: 4rem;}
h1 {letter-spacing:-0.04em;}
div[data-testid="stRadio"] label, div[data-testid="stSelectbox"] label {font-weight:700;}
.stButton button {width:100%; border-radius:10px; font-weight:800; min-height:46px;}
textarea {line-height:1.65 !important;}
</style>
""",
    unsafe_allow_html=True,
)

st.title("회의 뭐했니? v1.1")
st.caption("더 이상 사다리타기가 두렵지 않습니다.")

AUTH_COOKIE_NAME = "meeting_generator_auth_v1"
EDIT_KEYS = ["edit_purpose", "edit_participants", "edit_meeting_content", "edit_future_plan"]

if "cookie_controller" not in st.session_state:
    st.session_state["cookie_controller"] = CookieController(key="meeting_generator_cookies")
cookie_controller: CookieController = st.session_state["cookie_controller"]


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
    signature = hmac.new(_auth_secret().encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()
    return f"{_b64encode(payload.encode('utf-8'))}.{_b64encode(signature)}", expires_at


def _is_valid_auth_token(token: object) -> bool:
    if not isinstance(token, str) or "." not in token:
        return False
    try:
        payload_part, signature_part = token.split(".", 1)
        payload = _b64decode(payload_part).decode("utf-8")
        supplied_signature = _b64decode(signature_part)
        version, expires_text, password_fingerprint = payload.split("|", 2)
        if version != "v1" or int(expires_text) <= int(time.time()):
            return False
        if not hmac.compare_digest(password_fingerprint, _password_fingerprint(_required_password())):
            return False
        expected_signature = hmac.new(_auth_secret().encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()
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


def _clear_result() -> None:
    st.session_state.pop("result", None)
    st.session_state.pop("saved_meeting_id", None)
    st.session_state.pop("export_bytes", None)
    st.session_state.pop("export_filename", None)
    for key in EDIT_KEYS:
        st.session_state.pop(key, None)


def _set_edit_result(purpose: str, participants: str, meeting_content: str, future_plan: str) -> None:
    st.session_state["edit_purpose"] = purpose
    st.session_state["edit_participants"] = participants
    st.session_state["edit_meeting_content"] = meeting_content
    st.session_state["edit_future_plan"] = future_plan


def _create_meeting(request: dict[str, object]) -> None:
    mode = str(request["mode"])
    category = str(request.get("category") or "")
    user_input = str(request.get("user_input") or "")
    keep_exact = bool(request.get("keep_exact", False))
    business_name = str(request.get("business_name") or "")

    meetings = load_meetings()
    participant_links = load_participant_links()
    if meetings.empty:
        raise RuntimeError("Supabase에 사용할 수 있는 회의 데이터가 없습니다.")

    base_query = user_input.strip() if user_input.strip() else random.choice(CATEGORIES[category])
    query = f"{base_query} {business_name}".strip()
    similar = search_similar(
        meetings,
        query=query,
        category=category,
        top_k=10,
        randomize=(mode == "카테고리만 선택하여 자동 생성"),
    )
    participants = select_participants(similar, participant_links, max_people=5)
    if not participants:
        raise RuntimeError("조건에 맞는 외부 참석자를 DB에서 찾지 못했습니다. 다른 카테고리나 키워드를 사용하세요.")

    generated = generate_meeting(category or "사용자 입력 기반", user_input, similar, keep_exact, business_name)
    purpose = generated.meeting_purpose
    participant_text = ", ".join(participants)
    meeting_content = "\n".join(f"- {x}" for x in generated.meeting_content)
    future_plan = "\n".join(f"- {x}" for x in generated.future_plan)
    original = {
        "purpose": purpose,
        "participants": participant_text,
        "meeting_content": meeting_content,
        "future_plan": future_plan,
    }
    st.session_state["result"] = {"generated_original": original}
    _set_edit_result(purpose, participant_text, meeting_content, future_plan)
    st.session_state.pop("saved_meeting_id", None)
    st.session_state.pop("export_bytes", None)
    st.session_state.pop("export_filename", None)


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
            with st.spinner("Supabase에서 유사 회의와 실제 참석자 정보를 찾고 있습니다..."):
                _run_pending_generation()
        except Exception as exc:
            st.error(str(exc))
            return
        st.rerun()


try:
    businesses = load_businesses()
except Exception as exc:
    st.error(f"Supabase를 불러오지 못했습니다. Streamlit Secrets의 SUPABASE_URL / SUPABASE_SECRET_KEY를 확인하세요.\n\n{exc}")
    st.stop()

st.subheader("1. 사업 및 회의 기본정보")
business = render_business_form(businesses, save_business)

now_kr = datetime.now(ZoneInfo("Asia/Seoul"))
if "meeting_date" not in st.session_state:
    st.session_state["meeting_date"] = now_kr.date()
if "meeting_time" not in st.session_state:
    st.session_state["meeting_time"] = "11:00 ~ 13:00"
for key in ("author_name", "meeting_place", "card_merchant", "amount_raw"):
    st.session_state.setdefault(key, "")

with st.expander("회의 기본정보", expanded=True):
    c1, c2 = st.columns(2)
    with c1:
        st.date_input("회의일자", key="meeting_date")
        st.text_input("회의장소", key="meeting_place", placeholder="예: PNU AVEC 회의실")
        st.text_input("소요금액", key="amount_raw", placeholder="예: 340,000원(공급가액: ... + 부가세액: ...)")
    with c2:
        st.text_input("회의시간", key="meeting_time", placeholder="예: 11:00 ~ 13:00")
        st.text_input("작성자", key="author_name")
        st.text_input("카드 사용처", key="card_merchant")

st.subheader("2. 회의내용 정보")
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
    generate_clicked = st.button("회의내용 생성", type="primary")
with col2:
    st.button("결과 초기화", on_click=_clear_result)

if generate_clicked:
    business = business_from_state()
    if not business.get("name"):
        st.warning("지원사업명을 입력하세요.")
    elif mode == "키워드 또는 회의 목적 입력" and not user_input.strip():
        st.warning("키워드 또는 회의 목적을 입력하세요.")
    else:
        st.session_state["pending_generation"] = {
            "mode": mode,
            "category": category,
            "user_input": user_input,
            "keep_exact": keep_exact,
            "business_name": business["name"],
        }
        if _browser_is_authenticated():
            with st.spinner("Supabase에서 유사 회의를 찾고 GPT-5.6 Luna로 작성하고 있습니다..."):
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
    st.caption("아래 내용은 모두 수정할 수 있습니다. 최종 회의록 생성 시 현재 화면의 내용이 DB와 Excel에 반영됩니다.")
    st.text_area("회의 목적", key="edit_purpose", height=110)
    st.text_area("참석자 명단", key="edit_participants", height=150)
    st.text_area("회의 내용", key="edit_meeting_content", height=220)
    st.text_area("향후 계획", key="edit_future_plan", height=190)

    if st.button("최종 회의록 생성", type="primary", key="final_excel_generate"):
        try:
            business = business_from_state()
            if not business.get("name"):
                raise RuntimeError("지원사업명을 입력하세요.")
            purpose = str(st.session_state.get("edit_purpose", "")).strip()
            participants = str(st.session_state.get("edit_participants", "")).strip()
            meeting_content = str(st.session_state.get("edit_meeting_content", "")).strip()
            future_plan = str(st.session_state.get("edit_future_plan", "")).strip()
            if not purpose or not participants or not meeting_content:
                raise RuntimeError("회의 목적, 참석자 명단, 회의 내용은 필수입니다.")

            meeting_date = st.session_state["meeting_date"]
            form = {
                "business": business,
                "meeting_date": meeting_date,
                "meeting_time": str(st.session_state.get("meeting_time", "")).strip(),
                "author_name": str(st.session_state.get("author_name", "")).strip(),
                "meeting_place": str(st.session_state.get("meeting_place", "")).strip(),
                "card_merchant": str(st.session_state.get("card_merchant", "")).strip(),
                "amount_raw": str(st.session_state.get("amount_raw", "")).strip(),
                "participants": participants,
                "purpose": purpose,
                "meeting_content": meeting_content,
                "future_plan": future_plan,
            }
            workbook_bytes = build_meeting_workbook(form)
            content_block = compose_content_block(meeting_content, future_plan)
            payload = {
                "meeting_id": st.session_state.get("saved_meeting_id"),
                "business": business,
                "meeting_date": meeting_date.isoformat(),
                "meeting_time": form["meeting_time"],
                "author_name": form["author_name"],
                "meeting_place": form["meeting_place"],
                "card_merchant": form["card_merchant"],
                "amount_raw": form["amount_raw"],
                "participants": participants,
                "purpose": purpose,
                "meeting_content": meeting_content,
                "future_plan": future_plan,
                "content_block": content_block,
                "participant_items": parse_participants_for_save(participants),
                "generated_original": st.session_state["result"].get("generated_original", {}),
            }
            saved = save_final_meeting(payload)
            st.session_state["saved_meeting_id"] = int(saved["meeting_id"])
            saved_business = saved.get("business") or {}
            if saved_business.get("id"):
                st.session_state["business_selected_id"] = saved_business["id"]
            file_name = export_filename(
                business["name"], meeting_date, form["author_name"]
            )
            st.session_state["export_bytes"] = workbook_bytes
            st.session_state["export_filename"] = file_name
            st.success(
                f"최종본을 Supabase에 저장했습니다. "
                f"(회의 ID {saved['meeting_id']}, 수정이력 {saved['revision_no']}) "
                "Excel 다운로드를 시작합니다."
            )
            trigger_file_download(workbook_bytes, file_name)
        except Exception as exc:
            st.error(str(exc))