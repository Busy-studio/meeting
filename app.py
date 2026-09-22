from __future__ import annotations

import base64
import hashlib
import hmac
import random
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import streamlit as st
from streamlit_cookies_controller import CookieController

from src.db import load_businesses, load_meetings, load_participant_links, save_business, save_final_meeting
from src.exporter import build_meeting_workbook, compose_content_block, export_filename
from src.generator import extract_participant_identities, generate_meeting
from src.retrieval import CATEGORIES, parse_participants_for_save, search_similar, select_participants
from src.receipt import (
    amount_values_for_form,
    analyze_receipt_pdf,
    format_business_number,
    lookup_business_status,
    normalize_business_number,
    meeting_window_from_payment_time,
    parse_payment_date,
)
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
MEETING_COST_PER_PERSON = 50_000


@st.cache_resource
def _background_executor() -> ThreadPoolExecutor:
    return ThreadPoolExecutor(max_workers=4, thread_name_prefix="meeting-v12")


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
    total = max(0, int(st.session_state.get("amount_total", 0) or 0))
    supply = max(0, int(st.session_state.get("amount_supply", 0) or 0))
    supply = min(supply, total)
    st.session_state["amount_supply"] = supply
    st.session_state["amount_vat"] = total - supply


def _recalculate_supply_from_vat() -> None:
    total = max(0, int(st.session_state.get("amount_total", 0) or 0))
    vat = max(0, int(st.session_state.get("amount_vat", 0) or 0))
    vat = min(vat, total)
    st.session_state["amount_vat"] = vat
    st.session_state["amount_supply"] = total - vat


def _compose_amount_raw() -> str:
    total = int(st.session_state.get("amount_total", 0) or 0)
    supply = int(st.session_state.get("amount_supply", 0) or 0)
    vat = int(st.session_state.get("amount_vat", 0) or 0)
    if total <= 0 and supply <= 0 and vat <= 0:
        return ""
    return f"{total:,}원(공급가액: {supply:,} + 부가세액: {vat:,})"


def _receipt_business_number_changed() -> None:
    # 사용자가 OCR 결과를 수정하면 이전 국세청 조회결과는 더 이상 유효하지 않다.
    st.session_state["_receipt_status"] = {}
    st.session_state["_receipt_business_number_queried"] = ""


def _participant_analysis(raw: str) -> tuple[int, list[str]]:
    """Count distinct people and return unresolved same-name ambiguities."""
    text = str(raw or "").strip()
    if not text:
        return 0, []

    cache_key = hashlib.sha256(text.encode("utf-8")).hexdigest()
    cached = st.session_state.get("_participant_identity_cache")
    if isinstance(cached, dict) and cached.get("key") == cache_key:
        return (
            int(cached.get("count") or 0),
            [str(x) for x in (cached.get("ambiguous_names") or []) if str(x).strip()],
        )

    with st.spinner("참석자 인원수를 확인하고 있습니다."):
        analysis = extract_participant_identities(text)

    ambiguous_names = [str(x).strip() for x in analysis.ambiguous_names if str(x).strip()]
    st.session_state["_participant_identity_cache"] = {
        "key": cache_key,
        "count": len(analysis.people),
        "people": [person.model_dump() for person in analysis.people],
        "ambiguous_names": ambiguous_names,
    }
    return len(analysis.people), ambiguous_names


def _current_meeting_place() -> str:
    selected = str(st.session_state.get("meeting_place_selector", "")).strip()
    if selected == "직접 입력":
        return str(st.session_state.get("meeting_place_custom", "")).strip()
    return selected


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
    st.session_state.pop("_participant_identity_cache", None)
    future = st.session_state.pop("_meeting_future", None)
    if isinstance(future, Future):
        future.cancel()
    st.session_state.pop("_meeting_job_meta", None)
    st.session_state.pop("_meeting_generation_error", None)
    for key in EDIT_KEYS:
        st.session_state.pop(key, None)


def _set_edit_result(purpose: str, participants: str, meeting_content: str, future_plan: str) -> None:
    st.session_state["edit_purpose"] = purpose
    st.session_state["edit_participants"] = participants
    st.session_state["edit_meeting_content"] = meeting_content
    st.session_state["edit_future_plan"] = future_plan


def _prepare_meeting_generation(request: dict[str, object]):
    mode = str(request["mode"])
    category = str(request.get("category") or "")
    user_input = str(request.get("user_input") or "")
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
    return similar, participants


def _meeting_generation_job(
    category: str,
    user_input: str,
    similar,
    keep_exact: bool,
    business_name: str,
    api_key: str,
    model: str,
) -> dict[str, object]:
    generated = generate_meeting(
        category or "사용자 입력 기반",
        user_input,
        similar,
        keep_exact,
        business_name,
        api_key=api_key,
        model=model,
    )
    return generated.model_dump()


def _receipt_analysis_job(
    file_bytes: bytes,
    filename: str,
    api_key: str,
    model: str,
    nts_service_key: str,
) -> dict[str, object]:
    receipt = analyze_receipt_pdf(
        file_bytes,
        filename,
        api_key=api_key,
        model=model,
    )
    business_digits = normalize_business_number(receipt.business_number)
    status = (
        lookup_business_status(
            business_digits,
            service_key=nts_service_key,
        ).to_dict()
        if len(business_digits) == 10
        else {}
    )
    total, supply, vat, amount_warning = amount_values_for_form(receipt)
    return {
        "receipt": receipt.model_dump(),
        "business_digits": business_digits,
        "status": status,
        "amount_total": total,
        "amount_supply": supply,
        "amount_vat": vat,
        "amount_warning": amount_warning,
    }


def _apply_receipt_job_result(payload: dict[str, object], snapshot: dict[str, object]) -> None:
    receipt = dict(payload.get("receipt") or {})
    business_digits = str(payload.get("business_digits") or "")
    status = dict(payload.get("status") or {})

    receipt_date = parse_payment_date(receipt.get("payment_date"))
    if receipt_date is not None and st.session_state.get("meeting_date") == snapshot.get("meeting_date"):
        st.session_state["meeting_date"] = receipt_date

    receipt_time = meeting_window_from_payment_time(receipt.get("payment_time"))
    if receipt_time and st.session_state.get("meeting_time") == snapshot.get("meeting_time"):
        st.session_state["meeting_time"] = receipt_time

    merchant_name = str(receipt.get("merchant_name") or "").strip()
    if merchant_name and st.session_state.get("card_merchant") == snapshot.get("card_merchant"):
        st.session_state["card_merchant"] = merchant_name

    amount_keys = ("amount_total", "amount_supply", "amount_vat")
    if all(st.session_state.get(key) == snapshot.get(key) for key in amount_keys):
        st.session_state["amount_total"] = int(payload.get("amount_total") or 0)
        st.session_state["amount_supply"] = int(payload.get("amount_supply") or 0)
        st.session_state["amount_vat"] = int(payload.get("amount_vat") or 0)

    current_business_number = str(st.session_state.get("receipt_business_number") or "")
    snapshot_business_number = str(snapshot.get("receipt_business_number") or "")
    if current_business_number == snapshot_business_number:
        st.session_state["receipt_business_number"] = (
            format_business_number(business_digits)
            if len(business_digits) == 10
            else business_digits
        )
        st.session_state["_receipt_status"] = status
        st.session_state["_receipt_business_number_queried"] = (
            business_digits if status else ""
        )
    else:
        st.session_state["_receipt_status"] = {}
        st.session_state["_receipt_business_number_queried"] = ""

    st.session_state["_receipt_result"] = receipt
    st.session_state["_receipt_amount_warning"] = str(payload.get("amount_warning") or "")
    st.session_state.pop("_receipt_analysis_error", None)


def _apply_meeting_generation_result(payload: dict[str, object], meta: dict[str, object]) -> None:
    purpose = str(payload.get("meeting_purpose") or "")
    participant_text = str(meta.get("participant_text") or "")
    meeting_content = "\n".join(f"- {x}" for x in (payload.get("meeting_content") or []))
    future_plan = "\n".join(f"- {x}" for x in (payload.get("future_plan") or []))
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
    st.session_state.pop("_meeting_generation_error", None)


@st.fragment(run_every="1s")
def _poll_background_jobs() -> None:
    changed = False

    receipt_future = st.session_state.get("_receipt_future")
    if isinstance(receipt_future, Future) and receipt_future.done():
        job_hash = str(st.session_state.get("_receipt_job_hash") or "")
        snapshot = dict(st.session_state.get("_receipt_job_snapshot") or {})
        try:
            payload = receipt_future.result()
            if job_hash and job_hash == str(st.session_state.get("_receipt_active_hash") or ""):
                _apply_receipt_job_result(payload, snapshot)
                st.session_state["_receipt_processed_hash"] = job_hash
        except Exception as exc:
            if job_hash == str(st.session_state.get("_receipt_active_hash") or ""):
                st.session_state["_receipt_analysis_error"] = str(exc)
                st.session_state["_receipt_processed_hash"] = job_hash
        finally:
            st.session_state.pop("_receipt_future", None)
            st.session_state.pop("_receipt_job_hash", None)
            st.session_state.pop("_receipt_job_snapshot", None)
        changed = True

    meeting_future = st.session_state.get("_meeting_future")
    if isinstance(meeting_future, Future) and meeting_future.done():
        meta = dict(st.session_state.get("_meeting_job_meta") or {})
        try:
            payload = meeting_future.result()
            _apply_meeting_generation_result(payload, meta)
        except Exception as exc:
            st.session_state["_meeting_generation_error"] = str(exc)
        finally:
            st.session_state.pop("_meeting_future", None)
            st.session_state.pop("_meeting_job_meta", None)
        changed = True

    if changed:
        st.rerun()


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
st.caption("실제 사용한 카드 영수증 PDF를 1회 분석해 회의 기본정보에 자동 반영합니다. 읽지 못한 항목은 바로 수기 입력 안내가 표시됩니다.")
receipt_file = st.file_uploader(
    "영수증 PDF",
    type=["pdf"],
    key="receipt_pdf",
    help="PDF 파일을 이 영역에 끌어다 놓거나 파일을 선택하세요.",
)

if receipt_file is not None:
    receipt_bytes = receipt_file.getvalue()
    receipt_hash = hashlib.sha256(receipt_bytes).hexdigest()
    if receipt_hash != st.session_state.get("_receipt_processed_hash"):
        st.session_state.pop("_receipt_result", None)
        st.session_state.pop("_receipt_status", None)
        st.session_state.pop("_receipt_amount_warning", None)
        try:
            with st.spinner("영수증을 분석하고 있습니다."):
                receipt = analyze_receipt_pdf(receipt_bytes, receipt_file.name)
                receipt_business_digits = normalize_business_number(receipt.business_number)
                receipt_status = (
                    lookup_business_status(receipt_business_digits).to_dict()
                    if len(receipt_business_digits) == 10
                    else {}
                )
                receipt_total, receipt_supply, receipt_vat, amount_warning = amount_values_for_form(receipt)

                receipt_date = parse_payment_date(receipt.payment_date)
                if receipt_date is not None:
                    st.session_state["meeting_date"] = receipt_date

                receipt_time = meeting_window_from_payment_time(receipt.payment_time)
                if receipt_time:
                    st.session_state["meeting_time"] = receipt_time

                if receipt.merchant_name:
                    st.session_state["card_merchant"] = receipt.merchant_name

                st.session_state["amount_total"] = receipt_total
                st.session_state["amount_supply"] = receipt_supply
                st.session_state["amount_vat"] = receipt_vat
                st.session_state["receipt_business_number"] = (
                    format_business_number(receipt_business_digits)
                    if len(receipt_business_digits) == 10
                    else receipt_business_digits
                )
                st.session_state["_receipt_result"] = receipt.model_dump()
                st.session_state["_receipt_status"] = receipt_status
                st.session_state["_receipt_business_number_queried"] = (
                    receipt_business_digits if receipt_status else ""
                )
                st.session_state["_receipt_amount_warning"] = amount_warning
                st.session_state["_receipt_processed_hash"] = receipt_hash
                st.session_state.pop("_receipt_analysis_error", None)
        except Exception as exc:
            st.session_state["_receipt_analysis_error"] = str(exc)

receipt_error = st.session_state.get("_receipt_analysis_error")
if receipt_error:
    st.error(receipt_error)

receipt_result = st.session_state.get("_receipt_result")
if isinstance(receipt_result, dict):
    missing_fields = []
    for field_key, field_label in (
        ("merchant_name", "카드 사용처(상호명)"),
        ("payment_date", "결제일자"),
        ("payment_time", "결제시간"),
        ("total_amount", "총액"),
        ("supply_amount", "공급가액"),
        ("vat_amount", "부가세액"),
    ):
        value = receipt_result.get(field_key)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing_fields.append(field_label)

    current_business_number = str(st.session_state.get("receipt_business_number") or "").strip()
    if len(normalize_business_number(current_business_number)) != 10:
        missing_fields.append("사업자등록번호")

    st.success("영수증 분석을 완료했습니다. 인식되거나 계산 가능한 항목을 자동 반영했습니다.")
    if missing_fields:
        st.warning(
            "OCR에서 인식하지 못했거나 확인이 필요한 항목: "
            + ", ".join(missing_fields)
            + " — 필요한 값은 직접 입력해 주세요."
        )

    merchant_name = str(receipt_result.get("merchant_name") or "확인 필요")
    payment_date_text = str(receipt_result.get("payment_date") or "확인 필요")
    payment_time_text = str(receipt_result.get("payment_time") or "확인 필요")

    number_col, lookup_col = st.columns([3, 1])
    with number_col:
        st.text_input(
            "사업자등록번호",
            key="receipt_business_number",
            placeholder="예: 123-45-67890",
            on_change=_receipt_business_number_changed,
            help="OCR 결과가 잘못되었거나 비어 있으면 직접 수정한 뒤 조회하세요.",
        )
    with lookup_col:
        st.write("")
        st.write("")
        lookup_clicked = st.button(
            "사업자 조회",
            key="receipt_business_lookup",
            use_container_width=True,
        )

    if lookup_clicked:
        lookup_digits = normalize_business_number(st.session_state.get("receipt_business_number"))
        if len(lookup_digits) != 10:
            st.warning("사업자등록번호 10자리를 확인해 주세요.")
        else:
            with st.spinner("사업자 정보를 조회하고 있습니다."):
                st.session_state["_receipt_status"] = lookup_business_status(lookup_digits).to_dict()
                st.session_state["_receipt_business_number_queried"] = lookup_digits
                receipt_result["business_number"] = lookup_digits
                st.session_state["_receipt_result"] = receipt_result

    status = st.session_state.get("_receipt_status") or {}
    queried_number = str(st.session_state.get("_receipt_business_number_queried") or "")
    current_digits = normalize_business_number(st.session_state.get("receipt_business_number"))

    if len(current_digits) == 10 and queried_number != current_digits:
        st.info("사업자등록번호가 변경되었습니다. 사업자 조회를 눌러 과세유형과 사업자 상태를 다시 확인해 주세요.")

    summary_parts = [f"실제 거래처: {merchant_name}"]
    if status.get("business_status") and queried_number == current_digits:
        summary_parts.append(f"상태: {status['business_status']}")
    if status.get("tax_type") and queried_number == current_digits:
        summary_parts.append(f"과세유형: {status['tax_type']}")
    st.caption(" · ".join(summary_parts))
    st.caption(f"결제일시: {payment_date_text} {payment_time_text}")

    intermediary = str(receipt_result.get("payment_intermediary_name") or "").strip()
    intermediary_no = format_business_number(receipt_result.get("payment_intermediary_business_number"))
    if intermediary:
        intermediary_text = intermediary
        if intermediary_no:
            intermediary_text += f" / {intermediary_no}"
        st.caption(f"결제대행/플랫폼: {intermediary_text} (거래처 자동입력에서 제외)")

    amount_warning = str(st.session_state.get("_receipt_amount_warning") or "").strip()
    if amount_warning:
        st.warning(amount_warning)

    if status.get("error"):
        if status.get("configured") is False:
            st.info(status["error"])
        else:
            st.warning(status["error"])

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
            help="직접 수정하면 소요금액 합계에 맞춰 부가세액이 자동 조정됩니다.",
        )
    with amount_c3:
        st.number_input(
            "부가세액",
            min_value=0,
            step=1,
            key="amount_vat",
            on_change=_recalculate_supply_from_vat,
            help="직접 수정하면 소요금액 합계에 맞춰 공급가액이 자동 조정됩니다.",
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
    st.caption("아래 내용은 모두 수정할 수 있습니다. 최종 회의록 생성 시 현재 화면의 내용이 DB와 Excel에 반영됩니다.")
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

        participant_items = parse_participants_for_save(participants)
        amount_total = max(0, int(st.session_state.get("amount_total", 0) or 0))
        if amount_total > 0:
            participant_count, ambiguous_names = _participant_analysis(participants)
        else:
            participant_count, ambiguous_names = 0, []
        allowed_total = participant_count * MEETING_COST_PER_PERSON
        participant_count_unknown = amount_total > 0 and participant_count <= 0
        participant_ambiguity = amount_total > 0 and bool(ambiguous_names)
        over_budget = participant_count > 0 and amount_total > allowed_total

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
            "participant_items": participant_items,
            "generated_original": st.session_state["result"].get("generated_original", {}),
        }
        file_name = export_filename(
            business["name"], meeting_date, form["author_name"]
        )

        if participant_ambiguity:
            if st.button(
                "최종 회의록 생성",
                type="primary",
                key="final_excel_generate_participant_ambiguity",
                use_container_width=True,
            ):
                st.error(
                    "동명이인 여부를 확인할 수 없습니다: "
                    + ", ".join(ambiguous_names)
                    + ". 참석자 명단에서 소속·직급·직책 등 구분 정보를 보완해 주세요."
                )
        elif participant_count_unknown:
            if st.button(
                "최종 회의록 생성",
                type="primary",
                key="final_excel_generate_participant_unknown",
                use_container_width=True,
            ):
                st.error(
                    "참석자 인원수를 확인할 수 없습니다. 참석자 명단에 사람 이름을 명확히 입력해 주세요."
                )
        elif over_budget:
            if st.button(
                "최종 회의록 생성",
                type="primary",
                key="final_excel_generate_over_budget",
                use_container_width=True,
            ):
                st.error(
                    "총예산보다 소요금액이 큽니다. "
                    f"참석자 {participant_count}명 × {MEETING_COST_PER_PERSON:,}원 = "
                    f"{allowed_total:,}원 / 소요금액 {amount_total:,}원"
                )
        else:
            st.download_button(
                "최종 회의록 생성",
                data=workbook_bytes,
                file_name=file_name,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary",
                key="final_excel_generate",
                on_click=_save_final_download,
                args=(payload,),
                use_container_width=True,
            )
    except Exception as exc:
        st.error(str(exc))