from __future__ import annotations

from typing import Any

import httpx
import pandas as pd
import streamlit as st


def _secret(name: str, default: str | None = None) -> str:
    try:
        value = st.secrets.get(name, default)
    except Exception:
        value = default
    if value is None or str(value).strip() == "":
        raise RuntimeError(f"필수 Secret이 없습니다: {name}")
    return str(value).strip()


def _supabase_key() -> str:
    try:
        return _secret("SUPABASE_SECRET_KEY")
    except RuntimeError:
        return _secret("SUPABASE_SERVICE_ROLE_KEY")


def _rpc(function_name: str, payload: dict[str, Any] | None = None) -> Any:
    url = _secret("SUPABASE_URL").rstrip("/")
    key = _supabase_key()
    headers = {
        "apikey": key,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "pnu-meeting-streamlit/1.1",
    }
    # Legacy service_role JWTs need an Authorization header. Modern sb_secret keys do not.
    if key.startswith("eyJ"):
        headers["Authorization"] = f"Bearer {key}"

    try:
        with httpx.Client(timeout=45.0) as client:
            response = client.post(
                f"{url}/rest/v1/rpc/{function_name}",
                headers=headers,
                json=payload or {},
            )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:1000]
        raise RuntimeError(f"Supabase 요청 실패 ({exc.response.status_code}): {detail}") from exc
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Supabase 연결 실패: {exc}") from exc

    if not response.content:
        return None
    return response.json()


def _frame(rows: Any, columns: list[str]) -> pd.DataFrame:
    if not isinstance(rows, list):
        rows = []
    df = pd.DataFrame(rows)
    for col in columns:
        if col not in df.columns:
            df[col] = ""
    for col in columns:
        df[col] = df[col].fillna("")
    return df[columns]


def load_meetings() -> pd.DataFrame:
    columns = [
        "id", "meeting_date", "participants_raw", "meeting_purpose_raw",
        "meeting_content_raw", "discussion_raw", "followup_raw",
        "project_name", "research_project_name", "affiliation",
    ]
    df = _frame(_rpc("meeting_app_load_meetings"), columns)
    if not df.empty:
        df["id"] = pd.to_numeric(df["id"], errors="coerce").fillna(0).astype(int)
        for col in columns[1:]:
            df[col] = df[col].astype(str)
    return df


def load_participant_links() -> pd.DataFrame:
    columns = ["meeting_id", "participant_id", "name", "organization", "title", "raw_fragment"]
    df = _frame(_rpc("meeting_app_load_participants"), columns)
    if not df.empty:
        df["meeting_id"] = pd.to_numeric(df["meeting_id"], errors="coerce").fillna(0).astype(int)
        df["participant_id"] = pd.to_numeric(df["participant_id"], errors="coerce").fillna(0).astype(int)
        for col in ["name", "organization", "title", "raw_fragment"]:
            df[col] = df[col].astype(str)
    return df


def load_businesses() -> list[dict[str, Any]]:
    rows = _rpc("meeting_app_list_businesses")
    return rows if isinstance(rows, list) else []


def save_final_meeting(payload: dict[str, Any]) -> dict[str, Any]:
    result = _rpc("meeting_app_save_final", {"payload": payload})
    if not isinstance(result, dict) or not result.get("meeting_id"):
        raise RuntimeError("Supabase에서 최종 회의록 저장 결과를 확인하지 못했습니다.")
    return result