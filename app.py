from __future__ import annotations

import base64
import hashlib
import hmac
import json
import random
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import streamlit as st
import streamlit.components.v1 as components
from streamlit_cookies_controller import CookieController

from src.db import load_businesses, load_meetings, load_participant_links, save_business, save_final_meeting
from src.exporter import build_meeting_workbook, compose_content_block, export_filename
from src.generator import generate_meeting
from src.pdf_exporter import build_meeting_receipt_pdf, pdf_export_filename
from src.receipt import analyze_receipt, lookup_business_status
from src.retrieval import CATEGORIES, parse_participants_for_save, search_similar, select_participants
from src.ui import business_from_state, render_business_form


st.set_page_config(page_title="회의 뭐했니? v1.2", page_icon="📝", layout="centered")

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

st.title("회의 뭐했니? v1.2")
st.caption("더 이상 사다리타기가 두렵지 않습니다.")

AUTH_COOKIE_NAME = "meeting_generator_auth_v1"
EDIT_KEYS = ["edit_purpose", "edit_participants", "edit_meeting_content", "edit_future_plan"]


def _recalculate_amount_breakdown() -> None:
    total = int(st.session_state.get("amount_total", 0) or 0)
    if total <= 0:
        st.session_state["amount_supply"] = 0
        st.session_state["amount_vat"] = 0
        return
    supply = int(round(total / 1.1))
    vat = total - supply
    st.session_state["amount_supply"] = supply
    st.session_state["amount_vat"] = vat


def _recalculate_vat_from_supply() -> None:
    total = int(st.session_state.get("amount_total", 0) or 0)
    supply = int(st.session_state.get("amount_supply", 0) or 0)
    supply = max(0, min(supply, total))
    st.session_state["amount_supply"] = supply
    st.session_state["amount_vat"] = max(0, total - supply)


def _recalculate_supply_from_vat() -> None:
    total = int(st.session_state.get("amount_total", 0) or 0)
    vat = int(st.session_state.get("amount_vat", 0) or 0)
    vat = max(0, min(vat, total))
    st.session_state["amount_vat"] = vat
    st.session_state["amount_supply"] = max(0, total - vat)


def _compose_amount_raw() -> str:
    total = int(st.session_state.get("amount_total", 0) or 0)
    supply = int(st.session_state.get("amount_supply", 0) or 0)
    vat = int(st.session_state.get("amount_vat", 0) or 0)
    if total <= 0 and supply <= 0 and vat <= 0:
        return ""
    return f"{total:,}원(공급가액: {supply:,} + 부가세액: {vat:,})"


def _current_meeting_place() -> str:
    selected = str(st.session_state.get("meeting_place_selector", "")).strip()
    if selected == "직접 입력":
        return str(st.session_state.get("meeting_place_custom", "")).strip()
    return selected


def _format_business_number(value: object) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(digits) == 10:
        return f"{digits[:3]}-{digits[3:5]}-{digits[5:]}"
    return digits


def _receipt_meeting_window(value: str) -> str:
    try:
        center = datetime.strptime(str(value).strip(), "%H:%M")
    except ValueError:
        return ""
    start = center - timedelta(hours=1)
    end = center + timedelta(hours=1)
    return f"{start:%H:%M} ~ {end:%H:%M}"


def _apply_receipt_to_form(receipt: object) -> None:
    payment_date = str(getattr(receipt, "payment_date", "") or "").strip()
    if payment_date:
        try:
            st.session_state["meeting_date"] = datetime.strptime(payment_date, "%Y-%m-%d").date()
        except ValueError:
            pass

    payment_time = str(getattr(receipt, "payment_time", "") or "").strip()
    meeting_window = _receipt_meeting_window(payment_time)
    if meeting_window:
        st.session_state["meeting_time"] = meeting_window

    merchant_name = str(getattr(receipt, "merchant_name", "") or "").strip()
    if merchant_name:
        st.session_state["card_merchant"] = merchant_name

    total_value = getattr(receipt, "total_amount", None)
    supply_value = getattr(receipt, "supply_amount", None)
    vat_value = getattr(receipt, "vat_amount", None)

    if total_value is not None:
        total = max(0, int(total_value))
        st.session_state["amount_total"] = total
        if supply_value is None and vat_value is None:
            supply = int(round(total / 1.1)) if total else 0
            vat = total - supply
        elif supply_value is None:
            vat = max(0, min(int(vat_value or 0), total))
            supply = total - vat
        elif vat_value is None:
            supply = max(0, min(int(supply_value or 0), total))
            vat = total - supply
        else:
            supply = max(0, int(supply_value))
            vat = max(0, int(vat_value))
        st.session_state["amount_supply"] = supply
        st.session_state["amount_vat"] = vat


def _trigger_auto_downloads(files: list[tuple[str, bytes, str]]) -> None:
    payload = []
    for filename, content, mime in files:
        payload.append(
            {
                "filename": filename,
                "mime": mime,
                "data": base64.b64encode(content).decode("ascii"),
            }
        )
    script_data = json.dumps(payload, ensure_ascii=False)
    components.html(
        f"""
<script>
const files = {script_data};
files.forEach((file, index) => {{
  window.setTimeout(() => {{
    const link = document.createElement("a");
    link.href = "data:" + file.mime + ";base64," + file.data;
    link.download = file.filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
  }}, index * 500);
}});
</script>
""",
        height=0,
        scrolling=False,
    )


def _save_final_download(payload: dict[str, object]) -> None:
    try:
        saved = save_final_meeting(payload)
        st.session_state["saved_meeting_id"] = int(saved["meeting_id"])
        saved_business = saved.get("business") or {}
        if saved_business.get("id"):
            st.session_state["business_selected_id"] = saved_business["id"]
        st.session_state["_final_save_success"] = "최종본을 저장했습니다."
        st.session_state.pop("_final_save_error", None)
    except Exception as exc:
        st.session_state["_final_save_error"] = str(exc)
        st.session_state.pop("_final_save_success", None)

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
    st.session_state.pop("export_pdf_bytes", None)
    st.session_state.pop("export_pdf_filename", None)
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
    st.session_state.pop("export_pdf_bytes", None)
    st.session_state.pop("export_pdf_filename", None)


def _run_pending_generation() -> None:
    request = st.session_state.get("pending_generation")
    if not request:
        raise RuntimeError("생성 요청 정보가 없습니다. 다시 시도하세요.")
    _create_meeting(request)
    st.session_state.pop("pending_generation", None)
    st.session_state.pop("generation_password", None)


def _render_login_gate() -> None:
    st.subheader("비밀번호 확인")
    st.caption("무슨 회의를 했는지 궁금하시면 비밀번호를 입력하세요.")
    with st.form("initial_login_form"):
        password = st.text_input(
            "비밀번호",
            type="password",
            key="initial_login_password",
            max_chars=64,
            placeholder="비밀번호 입력",
        )
        submitted = st.form_submit_button("로그인", type="primary", use_container_width=True)

    if not submitted:
        return

    try:
        expected = _required_password()
    except RuntimeError as exc:
        st.error(str(exc))
        return

    if not hmac.compare_digest(password, expected):
        st.session_state["initial_login_password"] = ""
        st.error("비밀번호가 올바르지 않습니다.")
        return

    try:
        _remember_browser(password)
        st.session_state.pop("initial_login_password", None)
    except Exception as exc:
        st.error(str(exc))
        return

    st.rerun()


if not _browser_is_authenticated():
    _render_login_gate()
    st.stop()


try:
    businesses = load_businesses()
except Exception as exc:
    st.error(f"Supabase를 불러오지 못했습니다. Streamlit Secrets의 SUPABASE_URL / SUPABASE_SECRET_KEY를 확인하세요.\n\n{exc}")
    st.stop()

now_kr = datetime.now(ZoneInfo("Asia/Seoul"))
if "meeting_date" not in st.session_state:
    st.session_state["meeting_date"] = now_kr.date()
if "meeting_time" not in st.session_state:
    st.session_state["meeting_time"] = "11:00 ~ 13:00"
for key in ("author_name", "card_merchant"):
    st.session_state.setdefault(key, "")
st.session_state.setdefault("meeting_place_selector", "삼성산학협동관 303-2호")
st.session_state.setdefault("meeting_place_custom", "")
for key in ("amount_total", "amount_supply", "amount_vat"):
    st.session_state.setdefault(key, 0)

st.subheader("1. 영수증 업로드 (선택사항)")
uploaded_receipt = st.file_uploader(
    "영수증 스캔본",
    type=["pdf", "png", "jpg", "jpeg"],
    accept_multiple_files=False,
    key="receipt_upload",
    help="PDF 스캔본을 권장합니다. 업로드하면 결제정보와 사업자 과세유형을 자동 확인합니다.",
)

if uploaded_receipt is not None:
    receipt_bytes = uploaded_receipt.getvalue()
    receipt_hash = hashlib.sha256(receipt_bytes).hexdigest()
    if st.session_state.get("_receipt_hash") != receipt_hash:
        with st.spinner("영수증을 분석하고 있습니다."):
            try:
                receipt = analyze_receipt(uploaded_receipt.name, receipt_bytes)
                status = lookup_business_status(receipt.business_number)
                _apply_receipt_to_form(receipt)
                st.session_state["_receipt_hash"] = receipt_hash
                st.session_state["_receipt_present"] = True
                st.session_state["receipt_bytes"] = receipt_bytes
                st.session_state["receipt_filename"] = uploaded_receipt.name
                st.session_state["receipt_info"] = receipt.model_dump()
                st.session_state["receipt_status"] = status
                st.session_state.pop("export_pdf_bytes", None)
                st.session_state.pop("export_pdf_filename", None)
            except Exception as exc:
                st.error(f"영수증 분석에 실패했습니다: {exc}")
        if st.session_state.get("_receipt_hash") == receipt_hash:
            st.rerun()

    receipt_info = dict(st.session_state.get("receipt_info") or {})
    receipt_status = dict(st.session_state.get("receipt_status") or {})
    if receipt_info:
        biz_no = _format_business_number(receipt_info.get("business_number"))
        tax_label = str(receipt_status.get("tax_label") or "과세유형 확인 필요")
        business_status = str(receipt_status.get("business_status") or "").strip()
        status_text = " · ".join(
            item
            for item in [
                str(receipt_info.get("merchant_name") or "").strip(),
                biz_no,
                business_status,
                tax_label,
            ]
            if item
        )
        st.success(f"영수증 분석 완료: {status_text}")
        st.caption(
            f"결제일시 {receipt_info.get('payment_date') or '-'} "
            f"{receipt_info.get('payment_time') or '-'} · "
            f"총액 {int(receipt_info.get('total_amount') or 0):,}원 · "
            f"공급가액 {int(receipt_info.get('supply_amount') or 0):,}원 · "
            f"부가세액 {int(receipt_info.get('vat_amount') or 0):,}원"
        )
        lookup_error = str(receipt_status.get("lookup_error") or "").strip()
        if lookup_error:
            st.warning(lookup_error)
else:
    if st.session_state.pop("_receipt_present", False):
        for key in (
            "_receipt_hash",
            "receipt_bytes",
            "receipt_filename",
            "receipt_info",
            "receipt_status",
            "export_pdf_bytes",
            "export_pdf_filename",
        ):
            st.session_state.pop(key, None)

st.subheader("2. 사업 및 회의 기본정보")
business = render_business_form(businesses, save_business)

with st.expander("회의 기본정보", expanded=True):
    c1, c2 = st.columns(2)
    with c1:
        st.date_input("회의일자", key="meeting_date")
        st.selectbox(
            "회의장소",
            ["삼성산학협동관 303-2호", "PNU AVEC 회의실", "직접 입력"],
            key="meeting_place_selector",
        )
        if st.session_state.get("meeting_place_selector") == "직접 입력":
            st.text_input(
                "회의장소 직접 입력",
                key="meeting_place_custom",
                placeholder="회의장소를 입력하세요.",
            )
    with c2:
        st.text_input("회의시간", key="meeting_time", placeholder="예: 11:00 ~ 13:00")
        st.text_input("작성자", key="author_name")

    amount_c1, amount_c2, amount_c3 = st.columns(3)
    with amount_c1:
        st.number_input(
            "소요금액",
            min_value=0,
            step=1000,
            key="amount_total",
            on_change=_recalculate_amount_breakdown,
            help="총액을 입력하면 공급가액과 부가세액이 자동 계산됩니다.",
        )
    with amount_c2:
        st.number_input(
            "공급가액",
            min_value=0,
            step=1,
            key="amount_supply",
            on_change=_recalculate_vat_from_supply,
            help="수정하면 소요금액이 유지되도록 부가세액이 자동 조정됩니다.",
        )
    with amount_c3:
        st.number_input(
            "부가세액",
            min_value=0,
            step=1,
            key="amount_vat",
            on_change=_recalculate_supply_from_vat,
            help="수정하면 소요금액이 유지되도록 공급가액이 자동 조정됩니다.",
        )

    st.text_input("카드 사용처", key="card_merchant")

st.subheader("3. 회의내용 정보")
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
        with st.spinner("회의록을 생성하고 있습니다..."):
            try:
                _run_pending_generation()
            except Exception as exc:
                st.error(str(exc))
            else:
                st.rerun()

if "result" in st.session_state:
    st.divider()
    st.caption("아래 내용은 모두 수정할 수 있습니다. 최종 회의록 생성 시 현재 화면의 내용이 DB와 Excel에 반영되며, 영수증 업로드 시 통합 PDF도 함께 생성됩니다.")
    st.text_area("회의 목적", key="edit_purpose", height=110)
    st.text_area("참석자 명단", key="edit_participants", height=150)
    st.text_area("회의 내용", key="edit_meeting_content", height=220)
    st.text_area("향후 계획", key="edit_future_plan", height=190)

    if st.session_state.pop("_final_save_success", None):
        st.success("최종본을 저장했습니다.")
    final_error = st.session_state.pop("_final_save_error", None)
    if final_error:
        st.error(final_error)

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
        meeting_place = _current_meeting_place()
        if not meeting_place:
            raise RuntimeError("회의장소를 입력하세요.")

        form = {
            "business": business,
            "meeting_date": meeting_date,
            "meeting_time": str(st.session_state.get("meeting_time", "")).strip(),
            "author_name": str(st.session_state.get("author_name", "")).strip(),
            "meeting_place": meeting_place,
            "card_merchant": str(st.session_state.get("card_merchant", "")).strip(),
            "amount_raw": _compose_amount_raw(),
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
            "meeting_place": meeting_place,
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
        file_name = export_filename(
            business["name"], meeting_date, form["author_name"]
        )

        form["content_block"] = content_block
        receipt_bytes = st.session_state.get("receipt_bytes")
        receipt_filename = str(st.session_state.get("receipt_filename") or "")
        receipt_status = dict(st.session_state.get("receipt_status") or {})

        final_clicked = st.button(
            "최종 회의록 생성",
            type="primary",
            key="final_excel_generate",
            use_container_width=True,
        )
        if final_clicked:
            _save_final_download(payload)
            if not st.session_state.get("_final_save_error"):
                st.session_state["export_bytes"] = workbook_bytes
                st.session_state["export_filename"] = file_name
                st.session_state.pop("export_pdf_bytes", None)
                st.session_state.pop("export_pdf_filename", None)

                combined_pdf_bytes = None
                combined_pdf_name = None
                if isinstance(receipt_bytes, (bytes, bytearray)) and receipt_bytes:
                    combined_pdf_bytes = build_meeting_receipt_pdf(
                        form,
                        workbook_bytes=workbook_bytes,
                        receipt_bytes=bytes(receipt_bytes),
                        receipt_filename=receipt_filename,
                        tax_label=str(receipt_status.get("tax_label") or "과세유형 확인 필요"),
                    )
                    combined_pdf_name = pdf_export_filename(file_name)

                auto_files = [
                    (
                        file_name,
                        workbook_bytes,
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )
                ]
                if combined_pdf_bytes is not None and combined_pdf_name is not None:
                    st.session_state["export_pdf_bytes"] = combined_pdf_bytes
                    st.session_state["export_pdf_filename"] = combined_pdf_name
                    auto_files.append(
                        (
                            combined_pdf_name,
                            combined_pdf_bytes,
                            "application/pdf",
                        )
                    )
                    st.success("최종본을 저장했습니다. Excel과 영수증 포함 PDF 다운로드를 시작합니다.")
                else:
                    st.success("최종본을 저장했습니다. Excel 다운로드를 시작합니다.")
                _trigger_auto_downloads(auto_files)

        saved_xlsx = st.session_state.get("export_bytes")
        saved_xlsx_name = st.session_state.get("export_filename")
        if isinstance(saved_xlsx, (bytes, bytearray)) and saved_xlsx_name:
            saved_pdf = st.session_state.get("export_pdf_bytes")
            saved_pdf_name = st.session_state.get("export_pdf_filename")
            if isinstance(saved_pdf, (bytes, bytearray)) and saved_pdf_name:
                dl1, dl2 = st.columns(2)
                with dl1:
                    st.download_button(
                        "Excel 다시 다운로드",
                        data=bytes(saved_xlsx),
                        file_name=str(saved_xlsx_name),
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        use_container_width=True,
                    )
                with dl2:
                    st.download_button(
                        "영수증 포함 PDF 다시 다운로드",
                        data=bytes(saved_pdf),
                        file_name=str(saved_pdf_name),
                        mime="application/pdf",
                        use_container_width=True,
                    )
                st.caption("브라우저에서 여러 파일 자동 다운로드를 차단한 경우 위 버튼으로 각각 받을 수 있습니다.")
            else:
                st.download_button(
                    "Excel 다시 다운로드",
                    data=bytes(saved_xlsx),
                    file_name=str(saved_xlsx_name),
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                )
    except Exception as exc:
        st.error(str(exc))