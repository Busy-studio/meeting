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


class ParticipantIdentity(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    organization: str = Field(default="", max_length=200)
    title: str = Field(default="", max_length=100)


class ParticipantIdentityExtraction(BaseModel):
    people: list[ParticipantIdentity] = Field(default_factory=list, max_length=100)
    ambiguous_names: list[str] = Field(default_factory=list, max_length=100)


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
    *,
    api_key: str | None = None,
    model: str | None = None,
) -> GeneratedMeeting:
    resolved_api_key = str(api_key or "").strip() or _secret("OPENAI_API_KEY")
    resolved_model = str(model or "").strip() or _secret("OPENAI_MODEL", "gpt-5.6-luna")
    client = OpenAI(api_key=resolved_api_key)
    model = resolved_model
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


def extract_participant_identities(raw: str) -> ParticipantIdentityExtraction:
    """Extract distinct people and flag unresolved same-name ambiguity.

    Organization/title are optional. They are used only when the text itself
    provides them, including to distinguish same-name people. The model must
    not invent missing identity details.
    """
    text = str(raw or "").strip()
    if not text:
        return ParticipantIdentityExtraction()

    client = OpenAI(api_key=_secret("OPENAI_API_KEY"))
    model = _secret("OPENAI_PARTICIPANT_MODEL", "gpt-5.6-luna")
    prompt = f"""
다음은 사용자가 최종 수정한 회의 참석자 명단이다.

[참석자 명단]
{text}

명단에 명시된 서로 다른 사람을 추출해 JSON 하나만 출력한다.

판별 규칙:
- 사람 이름이 명시되어 있으면 소속이나 직급/직책이 없어도 1명으로 인정한다.
- 이름만 단독으로 적힌 경우도 반드시 포함한다.
- 기관명, 회사명, 학교명, 부서명, 직급, 직책, 역할명 자체는 사람으로 세지 않는다.
- 기존 DB에 없는 새로운 이름도 명단에 명시되어 있으면 포함한다.
- 한국어 이름뿐 아니라 영문 등 다른 표기의 사람 이름도 포함할 수 있다.
- organization과 title은 원문에 명시된 경우에만 기록하고, 없으면 빈 문자열로 둔다.
- 같은 이름이라도 서로 다른 소속이 명확히 적혀 있으면 서로 다른 사람으로 구분한다.
- 같은 이름이라도 직급/직책이 명확히 다르고 문맥상 서로 다른 사람으로 적혀 있으면 서로 다른 사람으로 구분한다.
- 같은 이름이 서로 다른 참석자 항목에 반복되고, 소속과 직급/직책까지 확인해도 서로 다른 사람인지 같은 사람인지 구분할 수 없으면 그 이름을 ambiguous_names에 넣는다.
- 동명이인 여부가 불명확한 경우 임의로 인원수를 확정하려고 추측하지 않는다.
- 같은 사람의 단순 반복이라고 명확한 경우에는 한 번만 포함하고 ambiguous_names에는 넣지 않는다.
- 문맥만으로 이름, 소속, 직급을 만들어내거나 보충하지 않는다.
- 확실히 사람 이름이라고 판단하기 어려운 문자열은 제외한다.
- name, organization, title은 가능한 한 원문 표기를 그대로 사용한다.

출력 형식:
{{"people":[
  {{"name":"김철수","organization":"부산대학교","title":"교수"}},
  {{"name":"이영희","organization":"","title":""}}
],"ambiguous_names":[]}}
""".strip()

    response = client.responses.create(
        model=model,
        instructions="회의 참석자 명단에서 명시된 서로 다른 사람만 보수적으로 식별하는 정보 추출기다.",
        input=prompt,
        reasoning={"effort": "low"},
        prompt_cache_options={"mode": "explicit"},
        store=False,
    )
    try:
        result = ParticipantIdentityExtraction.model_validate(_extract_json(response.output_text))
    except (ValidationError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"참석자 판별 결과 형식 검증 실패: {exc}") from exc

    # 이름은 반드시 원문에 실제로 등장해야 한다. 모델이 새 이름을 만든 경우 예산 판정에서 제외한다.
    compact_source = re.sub(r"\s+", "", text).casefold()
    verified: list[ParticipantIdentity] = []
    seen: set[tuple[str, str, str]] = set()
    for person in result.people:
        name = person.name.strip()
        organization = person.organization.strip()
        title = person.title.strip()
        if not name:
            continue
        if re.sub(r"\s+", "", name).casefold() not in compact_source:
            continue
        key = (
            re.sub(r"\s+", "", name).casefold(),
            re.sub(r"\s+", "", organization).casefold(),
            re.sub(r"\s+", "", title).casefold(),
        )
        if key in seen:
            continue
        seen.add(key)
        verified.append(
            ParticipantIdentity(name=name, organization=organization, title=title)
        )

    ambiguous_names: list[str] = []
    seen_ambiguous: set[str] = set()
    for value in result.ambiguous_names:
        name = str(value or "").strip()
        normalized = re.sub(r"\s+", "", name).casefold()
        if not name or normalized not in compact_source or normalized in seen_ambiguous:
            continue
        seen_ambiguous.add(normalized)
        ambiguous_names.append(name)

    return ParticipantIdentityExtraction(
        people=verified,
        ambiguous_names=ambiguous_names,
    )
