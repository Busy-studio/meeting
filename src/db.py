from __future__ import annotations

import hashlib
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st
from google.cloud import storage
from google.oauth2 import service_account


REQUIRED_COLUMNS = {
    "id", "status", "participants_raw", "meeting_purpose_raw",
    "discussion_raw", "followup_raw", "project_name", "research_project_name",
}


def _secret(name: str, default: str | None = None) -> str:
    try:
        value = st.secrets.get(name, default)
    except Exception:
        value = default
    if value is None or str(value).strip() == "":
        raise RuntimeError(f"필수 Secret이 없습니다: {name}")
    return str(value)


@st.cache_resource(show_spinner="비공개 회의록 DB를 불러오는 중입니다...")
def download_database() -> Path:
    bucket_name = _secret("GCS_BUCKET_NAME")
    object_name = _secret("GCS_OBJECT_NAME", "meeting_DB.sqlite")
    account_info: dict[str, Any] = dict(st.secrets["gcp_service_account"])

    credentials = service_account.Credentials.from_service_account_info(account_info)
    client = storage.Client(project=account_info.get("project_id"), credentials=credentials)
    blob = client.bucket(bucket_name).blob(object_name)
    if not blob.exists(client):
        raise RuntimeError(f"GCS 객체를 찾을 수 없습니다: gs://{bucket_name}/{object_name}")

    generation = str(blob.generation or "latest")
    digest = hashlib.sha256(f"{bucket_name}/{object_name}/{generation}".encode()).hexdigest()[:12]
    local_path = Path(tempfile.gettempdir()) / f"meeting_DB_{digest}.sqlite"
    if not local_path.exists() or local_path.stat().st_size == 0:
        blob.download_to_filename(str(local_path))
    return local_path


def connect_readonly(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True, check_same_thread=False)


@st.cache_data(show_spinner=False)
def load_meetings(db_path: str) -> pd.DataFrame:
    with connect_readonly(Path(db_path)) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(meetings)")}
        missing = REQUIRED_COLUMNS - cols
        if missing:
            raise RuntimeError(f"DB 필수 컬럼이 없습니다: {', '.join(sorted(missing))}")
        df = pd.read_sql_query(
            """
            SELECT id, meeting_date, participants_raw, meeting_purpose_raw,
                   meeting_content_raw, discussion_raw, followup_raw,
                   project_name, research_project_name, affiliation
            FROM meetings
            WHERE status = 'ok'
              AND COALESCE(meeting_purpose_raw, '') <> ''
            """,
            conn,
        )
    for col in df.columns:
        df[col] = df[col].fillna("").astype(str)
    return df
