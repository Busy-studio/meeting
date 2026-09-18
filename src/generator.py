from __future__ import annotations

import json
import re
from typing import Any

import pandas as pd
import streamlit as st
from openai import OpenAI
from pydantic import BaseModel, Field, ValidationError


class GeneratedMeeting(BaseModel):
    meeting_purpose: str = Field(min_length=5, max_length=220)
    meeting_content: list[str] = Field(min_length=3, max_length=3)
    future_plan: list[str] = Field(min_length=3, max_length=3)


def _secret(name: str, default: str | None = None) -> str:
    value = st.secrets.get(name, default)
    if value is None or str(value).strip() == "":
        raise RuntimeError(f"필수 Secret이 없습니다: {name}")
    return str(value)


def _references(similar: pd.DataFrame) -> str:
    chunks = []
    for i, row in enumerate(similar.head(8).itertuples(index=False), 1):
        chunks.append(
            f"[유사회의 {i}]\n"
            f"목적: {row.meeting_purpose_raw}\n"
            f"회의내용: {row.discussion_raw}\n"
            f"향후계획: {row.followup_raw}\n"
            f"사업: {row.project_name} {row.research_project_name}"
        )
    return "\n\n".join(chunks)


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise ValueError("API 응답에서 JSON을 찾을 수 없습니다.")
        return json.loads(match.group(0))


def generate_meeting(
    category: str,
    user_input: str,
    similar: pd.DataFrame,
    keep_exact_purpose: bool,
    business_name: str,
) -> GeneratedMeeting:
    client = OpenAI(api_key=_secret("OPENAI_API_KEY"))
    model = _secret("OPENAI_MODEL", "gpt-5.6-luna")
    exact_instruction = (
        "사용자가 입력한 문장을 meeting_purpose에 글자 하나 바꾸지 말고 그대로 사용한다."
        if keep_exact_purpose and user_input.strip()
        else "사용자 입력을 참고하되 회의 목적을 새로 작성하거나 명사형 문장으로 다듬는다."
    )
    prompt = f"""
지원사업: {business_name or '(미지정)'}
카테고리: {category}
사용자 입력: {user_input or '(입력 없음)'}

{exact_instruction}

아래 유사 회의는 사실성과 표현 참고자료이다. 서로 다른 회의를 자연스럽게 조합하되 특정 회의 문장을 길게 복사하지 않는다.
{_references(similar)}

반드시 다음 JSON 하나만 출력한다.
{{
  "meeting_purpose": "명사 마무리형 개조식 한 문장",
  "meeting_content": ["명사 마무리형 한 문장", "명사 마무리형 한 문장", "명사 마무리형 한 문장"],
  "future_plan": ["명사 마무리형 한 문장", "명사 마무리형 한 문장", "명사 마무리형 한 문장"]
}}

작성 규칙:
- 모든 문장은 공문서 회의록에 적합한 한국어 개조식
- '함', '했음', '진행함', '논의하였음' 같은 서술형 종결 금지
- 목적은 정확히 한 문장
- 회의내용과 향후계획은 각각 정확히 3개
- 유사 회의에서 확인되지 않은 구체적 금액, 계약체결, 확정 일정, 성과를 만들어내지 않음
- 참석자 이름이나 소속은 출력하지 않음
- 중복 문장 금지
""".strip()

    response = client.responses.create(
        model=model,
        instructions="당신은 대학 기술사업화·산학협력 회의록을 작성하는 전문 행정 실무자다.",
        input=prompt,
        reasoning={"effort": "low"},
        # GPT-5.6 uses an implicit prompt-cache breakpoint by default.
        # Explicit mode with no breakpoints disables that implicit cache usage.
        prompt_cache_options={"mode": "explicit"},
    )
    try:
        result = GeneratedMeeting.model_validate(_extract_json(response.output_text))
    except (ValidationError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"생성 결과 형식 검증 실패: {exc}") from exc
    if keep_exact_purpose and user_input.strip():
        result.meeting_purpose = user_input.strip()
    return result