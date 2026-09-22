from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import httpx
import streamlit as st
from openai import OpenAI
from pydantic import BaseModel, Field, ValidationError


class ReceiptExtraction(BaseModel):
    payment_date: str | None = None
    payment_time: str | None = None
    merchant_name: str | None = None
    business_number: str | None = None
    total_amount: int | None = Field(default=None, ge=0)
    supply_amount: int | None = Field(default=None, ge=0)
    vat_amount: int | None = Field(default=None, ge=0)
    tax_exempt_amount: int | None = Field(default=None, ge=0)
    payment_intermediary_name: str | None = None
    payment_intermediary_business_number: str | None = None


@dataclass
class BusinessStatus:
    business_number: str
    business_status: str = ""
    business_status_code: str = ""
    tax_type: str = ""
    tax_type_code: str = ""
    error: str = ""
    configured: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "business_number": self.business_number,
            "business_status": self.business_status,
            "business_status_code": self.business_status_code,
            "tax_type": self.tax_type,
            "tax_type_code": self.tax_type_code,
            "error": self.error,
            "configured": self.configured,
        }


def _secret(name: str, default: str | None = None) -> str:
    try:
        value = st.secrets.get(name, default)
    except Exception:
        value = default
    if value is None or str(value).strip() == "":
        raise RuntimeError(f"필수 Secret이 없습니다: {name}")
    return str(value).strip()


def _extract_json(text: str) -> dict[str, Any]:
    text = str(text or "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise ValueError("영수증 분석 결과에서 JSON을 찾을 수 없습니다.")
        return json.loads(match.group(0))


def normalize_business_number(value: object) -> str:
    return re.sub(r"\D", "", str(value or ""))[:10]


def format_business_number(value: object) -> str:
    digits = normalize_business_number(value)
    if len(digits) != 10:
        return digits
    return f"{digits[:3]}-{digits[3:5]}-{digits[5:]}"


def _receipt_prompt() -> str:
    return """
첨부한 한국어 카드 영수증/매출전표 PDF 전체를 읽고 아래 JSON 하나만 출력한다.

가장 중요한 거래처 판별 규칙:
- 카드 결제대행사(PG), 플랫폼 운영사, 전자지급결제대행사, 중개사업자가 아니라 실제 상품 또는 서비스를 판매하고 대금을 지급받는 판매자/이용상점을 merchant로 선택한다.
- 문서에 '판매자 정보', '판매자', '이용상점', '공급자', '판매처', '매장정보' 등이 별도로 있으면 해당 영역의 상호와 사업자등록번호를 최우선으로 사용한다.
- '가맹점 정보'가 결제 플랫폼/중개사이고 별도 '판매자 정보'가 있으면 가맹점 정보는 merchant로 사용하지 않는다.
- 토스페이먼츠, 결제서비스업체, PG사처럼 결제만 대행하는 업체는 payment_intermediary에만 기록한다.
- 서로 다른 영역의 상호와 사업자등록번호를 섞지 않는다.
- 별도 판매자/이용상점 영역이 없는 일반 오프라인 영수증이면 일반적인 상호/가맹점과 그 사업자등록번호를 사용한다.
- 실제 판매자의 사업자등록번호가 확인되지 않으면 플랫폼 사업자번호를 대신 넣지 말고 business_number를 null로 둔다.

추출 규칙:
- payment_date/payment_time은 주문일자가 아니라 카드 승인/결제 일시를 우선한다.
- 날짜는 YYYY-MM-DD, 시간은 HH:MM:SS 형식으로 정규화한다.
- 사업자등록번호는 하이픈 없이 숫자 10자리로 정규화한다.
- total_amount는 실제 최종 결제 합계 금액이다.
- supply_amount, vat_amount, tax_exempt_amount는 문서에 표시된 값을 우선하며 없는 값은 추측하지 말고 null로 둔다.
- 금액은 쉼표와 '원'을 제거한 정수로 반환한다.
- 문서에 여러 거래가 있으면 실제 승인된 최종 거래 1건을 선택한다.
- 읽을 수 없는 값은 임의로 만들지 말고 null로 둔다.

{
  "payment_date": null,
  "payment_time": null,
  "merchant_name": null,
  "business_number": null,
  "total_amount": null,
  "supply_amount": null,
  "vat_amount": null,
  "tax_exempt_amount": null,
  "payment_intermediary_name": null,
  "payment_intermediary_business_number": null
}
""".strip()


def analyze_receipt_pdf(file_bytes: bytes, filename: str) -> ReceiptExtraction:
    if not file_bytes:
        raise RuntimeError("영수증 PDF가 비어 있습니다.")

    client = OpenAI(api_key=_secret("OPENAI_API_KEY"))
    model = _secret("OPENAI_RECEIPT_MODEL", _secret("OPENAI_MODEL", "gpt-5.6-luna"))

    suffix = Path(filename or "receipt.pdf").suffix.lower()
    if suffix != ".pdf":
        suffix = ".pdf"

    temp_path = ""
    uploaded_id = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(file_bytes)
            temp_path = tmp.name

        with open(temp_path, "rb") as stream:
            uploaded = client.files.create(file=stream, purpose="user_data")
        uploaded_id = uploaded.id

        response = client.responses.create(
            model=model,
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_file", "file_id": uploaded_id, "detail": "high"},
                        {"type": "input_text", "text": _receipt_prompt()},
                    ],
                }
            ],
            reasoning={"effort": "low"},
            prompt_cache_options={"mode": "explicit"},
        )
        try:
            return ReceiptExtraction.model_validate(_extract_json(response.output_text))
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"영수증 분석 결과 형식 검증 실패: {exc}") from exc
    finally:
        if uploaded_id:
            try:
                client.files.delete(uploaded_id)
            except Exception:
                pass
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def lookup_business_status(business_number: object) -> BusinessStatus:
    digits = normalize_business_number(business_number)
    if len(digits) != 10:
        return BusinessStatus(
            business_number=digits,
            error="유효한 10자리 사업자등록번호를 확인하지 못했습니다.",
        )

    try:
        service_key = _secret("NTS_BUSINESS_SERVICE_KEY")
    except RuntimeError:
        return BusinessStatus(
            business_number=digits,
            configured=False,
            error="국세청 사업자 상태조회 API Secret이 설정되지 않았습니다.",
        )

    # data.go.kr에서 Encoding 키를 복사한 경우에도 동작하도록 한 번 디코딩한다.
    service_key = unquote(service_key)
    url = "https://api.odcloud.kr/api/nts-businessman/v1/status"
    try:
        with httpx.Client(timeout=20.0) as client:
            response = client.post(
                url,
                params={"serviceKey": service_key, "returnType": "JSON"},
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                json={"b_no": [digits]},
            )
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:500]
        return BusinessStatus(
            business_number=digits,
            error=f"국세청 상태조회 실패 ({exc.response.status_code}): {detail}",
        )
    except (httpx.HTTPError, ValueError) as exc:
        return BusinessStatus(
            business_number=digits,
            error=f"국세청 상태조회 연결 실패: {exc}",
        )

    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        return BusinessStatus(
            business_number=digits,
            error="국세청 상태조회 결과가 없습니다.",
        )

    row = rows[0] if isinstance(rows[0], dict) else {}
    return BusinessStatus(
        business_number=digits,
        business_status=str(row.get("b_stt") or ""),
        business_status_code=str(row.get("b_stt_cd") or ""),
        tax_type=str(row.get("tax_type") or ""),
        tax_type_code=str(row.get("tax_type_cd") or ""),
    )


def parse_payment_date(value: object) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def meeting_window_from_payment_time(value: object) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None

    matched = re.search(r"(?P<h>\d{1,2}):(?P<m>\d{2})(?::(?P<s>\d{2}))?", text)
    if not matched:
        return None

    try:
        paid_at = datetime.combine(
            date.today(),
            time(
                hour=int(matched.group("h")),
                minute=int(matched.group("m")),
                second=int(matched.group("s") or 0),
            ),
        )
    except ValueError:
        return None

    start = paid_at - timedelta(hours=1)
    end = paid_at + timedelta(hours=1)
    return f"{start:%H:%M} ~ {end:%H:%M}"


def amount_values_for_form(receipt: ReceiptExtraction) -> tuple[int, int, int, str]:
    total = int(receipt.total_amount or 0)
    supply = receipt.supply_amount
    vat = receipt.vat_amount
    exempt = int(receipt.tax_exempt_amount or 0)
    warning = ""

    if total <= 0:
        return 0, int(supply or 0), int(vat or 0), warning

    if supply is not None and vat is not None:
        supply_value = int(supply)
        vat_value = int(vat)
        # 면세가액은 부가세가 없는 공급대가이므로 화면의 공급가액 쪽에 합산하여
        # 총액 = 공급가액 + 부가세액 관계를 유지한다.
        if exempt > 0 and supply_value + vat_value + exempt == total:
            return total, supply_value + exempt, vat_value, ""
        if supply_value + vat_value == total:
            return total, supply_value, vat_value, ""
        warning = "영수증의 총액과 공급가액·부가세액 합계가 일치하지 않아 총액 기준으로 보정했습니다."
        supply_value = max(0, min(supply_value, total))
        return total, supply_value, total - supply_value, warning

    if supply is not None:
        supply_value = max(0, min(int(supply), total))
        return total, supply_value, total - supply_value, warning

    if vat is not None:
        vat_value = max(0, min(int(vat), total))
        return total, total - vat_value, vat_value, warning

    if exempt > 0 and exempt <= total:
        return total, total, 0, warning

    supply_value = int(round(total / 1.1))
    return total, supply_value, total - supply_value, "영수증에 공급가액/부가세액이 없어 총액을 기준으로 자동 계산했습니다."
