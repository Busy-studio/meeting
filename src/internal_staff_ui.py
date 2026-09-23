"""Streamlit four-column internal-staff picker and persistent roster editor."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from src.db import load_internal_staff, save_internal_staff


def _text(value: object) -> str:
    return "" if value is None or pd.isna(value) else str(value).strip()


def render_internal_staff_picker() -> tuple[list[dict], bool]:
    """Render checkboxes and management controls; return checked active staff."""
    st.markdown("**내부 인원**")
    try:
        rows = load_internal_staff()
    except Exception as exc:
        st.warning("내부 인원 명단을 불러오지 못했습니다. Supabase에 v1.2 내부 인원 마이그레이션을 적용했는지 확인하세요. 기존 참석자 명단은 계속 사용할 수 있습니다.")
        st.caption(str(exc))
        return [], False

    active_rows = [person for person in rows if person.get("is_active")]
    if not active_rows:
        st.info("등록된 재직 인원이 없습니다. 아래 내부 인원 관리에서 직원을 추가하거나 재직 여부를 변경하세요.")

    for start in range(0, len(active_rows), 4):
        cols = st.columns(4, gap="small")
        for col, person in zip(cols, active_rows[start:start + 4]):
            with col:
                label = " ".join(part for part in (str(person.get("name") or "").strip(), str(person.get("title") or "").strip()) if part)
                st.checkbox(label, key=f"internal_staff_check_{person['id']}")
    selected = [
        person for person in active_rows
        if st.session_state.get(f"internal_staff_check_{person['id']}", False)
    ]

    with st.expander("내부 인원 관리 (추가 · 수정 · 퇴사 처리)", expanded=False):
        st.caption("이름과 직급을 수정한 후 저장하세요. 퇴사자는 '재직'을 해제하면 체크박스에서 제외되며 기존 회의록은 변경되지 않습니다.")
        notice = st.session_state.pop("_internal_staff_save_success", None)
        if notice:
            st.success(notice)

        if rows:
            editor_data = pd.DataFrame(
                [{"id": p["id"], "name": p["name"], "title": p.get("title") or "", "is_active": bool(p["is_active"])} for p in rows]
            )
            editor_key = f"internal_staff_roster_editor_{st.session_state.get('_internal_staff_editor_version', 0)}"
            edited = st.data_editor(
                editor_data,
                key=editor_key,
                hide_index=True,
                num_rows="fixed",
                use_container_width=True,
                column_config={
                    "id": None,
                    "name": st.column_config.TextColumn("이름", required=True),
                    "title": st.column_config.TextColumn("직급"),
                    "is_active": st.column_config.CheckboxColumn("재직", help="해제하면 일반 선택 목록에서 제외됩니다."),
                },
            )
            if st.button("변경사항 저장", key="save_internal_staff_changes", use_container_width=True):
                updates = []
                for record in edited.to_dict(orient="records"):
                    name, title = _text(record.get("name")), _text(record.get("title"))
                    if not name:
                        st.error("직원 이름은 비워둘 수 없습니다.")
                        break
                    active = record.get("is_active")
                    updates.append({
                        "id": str(record["id"]),
                        "name": name,
                        "title": title,
                        "is_active": True if pd.isna(active) else bool(active),
                    })
                else:
                    try:
                        save_internal_staff(updates)
                    except Exception as exc:
                        st.error(f"내부 인원 저장에 실패했습니다: {exc}")
                    else:
                        st.session_state["_internal_staff_save_success"] = "내부 인원 변경사항을 저장했습니다."
                        st.session_state["_internal_staff_editor_version"] = int(st.session_state.get("_internal_staff_editor_version", 0)) + 1
                        st.rerun()

        with st.form("internal_staff_add_form", clear_on_submit=True):
            st.markdown("**신규 인원 추가**")
            col_name, col_title = st.columns(2)
            with col_name:
                new_name = st.text_input("이름", key="new_internal_staff_name")
            with col_title:
                new_title = st.text_input("직급", key="new_internal_staff_title")
            add_clicked = st.form_submit_button("인원 추가 및 저장", use_container_width=True)
        if add_clicked:
            if not new_name.strip():
                st.error("추가할 직원 이름을 입력하세요.")
            else:
                try:
                    save_internal_staff([{"name": new_name.strip(), "title": new_title.strip(), "is_active": True}])
                except Exception as exc:
                    st.error(f"내부 인원 추가에 실패했습니다: {exc}")
                else:
                    st.session_state["_internal_staff_save_success"] = "새 내부 인원을 등록했습니다."
                    st.session_state["_internal_staff_editor_version"] = int(st.session_state.get("_internal_staff_editor_version", 0)) + 1
                    st.rerun()

    return selected, True
