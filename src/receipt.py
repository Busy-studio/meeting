from __future__ import annotations

import base64
import json
import mimetypes
import re
from typing import Any

import httpx
import streamlit as st
from openai import OpenAI
from pydantic import BaseModel, Field, field_validator


NTS_STATUS_URL = "https://api.odcloud.kr/api/nts-businessman/v1/status"


def _secret(name: str, default: str | None = None) -> str:
    value = st.secrets.get(name, default)
    if value is None or str(value).strip() == "":
        raise RuntimeError(f"필수 Secret이 없습니다: {name}")
    return str(value)


class ReceiptData(BaseModel):
    payment_date: str | None = None
    payment_time: str | None = None
    merchant_name: str = ""
    business_number: str = ""
    total_amount: int | None = Field(default=None, ge=0)
    supply_amount: int | None = Field(default=None, ge=0)
    vat_amount: int | None = Field(default=None, ge=0)

    @field_validator("business_number", mode="before")
    @classmethod
    def _normalize_business_number(cls, value: object) -> str:
        digits = re.sub(r"\D", "", str(value or ""))
        return digits[:10] if len(digits) >= 10 else digits

    @field_validator("total_amount", "supply_amount", "vat_amount", mode="before")
    @classmethod
    def _normalize_money(cls, value: object) -> int | None:
        if value is None or value == "":
            return None
        if isinstance(value, (int, float)):
            return max(0, int(round(value)))
        digits = re.sub(r"[^0-9-]", "", str(value))
        if not digits:
            return None
        return max(0, int(digits))


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise RuntimeError("영수증 분석 결과에서 JSON을 찾을 수 없습니다.")
        return json.loads(match.group(0))


def _input_block(filename: str, content: bytes) -> dict[str, str]:
    mime = mimetypes.guess_type(filename)[0] or "application/pdf"
    encoded = base64.b64encode(content).decode("ascii")
    if mime.startswith("image/"):
        return {
            "type": "input_image",
            "image_url": f"data:{mime};base64,{encoded}",
            "detail": "high",
        }
    return {
        "type": "input_file",
        "filename": filename or "receipt.pdf",
        "file_data": encoded,
    }


def analyze_receipt(filename: str, content: bytes) -> ReceiptData:
    if not content:
        raise RuntimeError("영수증 파일이 비어 있습니다.")

    client = OpenAI(api_key=_secret("OPENAI_API_KEY"))
    model = _secret("OPENAI_MODEL", "gpt-5.6-luna")
    prompt = """
첨부된 카드 영수증 또는 결제 영수증을 읽고 아래 JSON 하나만 출력하세요.

{
  "payment_date": "YYYY-MM-DD 또는 null",
  "payment_time": "HH:MM 또는 null",
  "merchant_name": "거래처명",
  "business_number": "사업자등록번호 10자리 숫자",
  "total_amount": 총 결제금액 정수 또는 null,
  "supply_amount": 공급가액 정수 또는 null,
  "vat_amount": 부가세액 정수 또는 null
}

규칙:
- 영수증에 실제로 표시된 정보를 우선합니다.
- 승인일시/거래일시/결제일시가 여러 개면 실제 카드 결제 시점을 선택합니다.
- 사업자등록번호는 하이픈 없이 숫자 10자리만 출력합니다.
- 금액은 쉼표와 원 기호를 제거한 정수로 출력합니다.
- 공급가액이나 부가세액이 영수증에 명확히 없으면 임의로 만들지 말고 null로 둡니다.
- 합계/총액/결제금액 중 실제 최종 결제금액을 total_amount로 선택합니다.
""".strip()

    response = client.responses.create(
        model=model,
        instructions="당신은 한국 카드 영수증을 정확히 판독하는 회계 증빙 OCR 도우미입니다.",
        input=[
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    _input_block(filename, content),
                ],
            }
        ],
        reasoning={"effort": "low"},
    )
    return ReceiptData.model_validate(_extract_json(response.output_text))


def classify_tax_type(tax_type: str) -> str:
    value = str(tax_type or "").strip()
    if "간이과세자" in value:
        return "간이과세자"
    if "일반과세자" in value:
        return "일반과세자"
    return "과세유형 확인 필요"


def lookup_business_status(business_number: str) -> dict[str, str]:
    digits = re.sub(r"\D", "", str(business_number or ""))
    if len(digits) != 10:
        return {
            "business_number": digits,
            "business_status": "",
            "tax_type": "",
            "tax_label": "과세유형 확인 필요",
            "lookup_error": "사업자등록번호 10자리를 확인하지 못했습니다.",
        }

    try:
        service_key = _secret("NTS_BUSINESS_SERVICE_KEY")
    except RuntimeError as exc:
        return {
            "business_number": digits,
            "business_status": "",
            "tax_type": "",
            "tax_label": "과세유형 확인 필요",
            "lookup_error": str(exc),
        }

    try:
        response = httpx.post(
            NTS_STATUS_URL,
            params={"serviceKey": service_key},
            json={"b_no": [digits]},
            headers={"accept": "application/json", "Content-Type": "application/json"},
            timeout=20.0,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        return {
            "business_number": digits,
            "business_status": "",
            "tax_type": "",
            "tax_label": "과세유형 확인 필요",
            "lookup_error": f"국세청 사업자 상태조회 실패: {exc}",
        }

    rows = payload.get("data") or []
    if not rows:
        return {
            "business_number": digits,
            "business_status": "",
            "tax_type": "",
            "tax_label": "과세유형 확인 필요",
            "lookup_error": "국세청 조회 결과가 없습니다.",
        }

    row = rows[0] or {}
    tax_type = str(row.get("tax_type") or "").strip()
    return {
        "business_number": digits,
        "business_status": str(row.get("b_stt") or "").strip(),
        "business_status_code": str(row.get("b_stt_cd") or "").strip(),
        "tax_type": tax_type,
        "tax_type_code": str(row.get("tax_type_cd") or "").strip(),
        "tax_label": classify_tax_type(tax_type),
        "lookup_error": "",
    }
