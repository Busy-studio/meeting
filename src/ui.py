from __future__ import annotations

import base64
import json
from typing import Callable

import streamlit as st
import streamlit.components.v1 as components


BUSINESS_FIELDS = {
    "business_name": "name",
    "business_research_project_name": "research_project_name",
    "business_project_number": "project_number",
    "business_support_organization": "support_organization",
    "business_total_research_period": "total_research_period",
    "business_round_no": "round_no",
    "business_round_research_period": "round_research_period",
    "business_principal_affiliation": "principal_affiliation",
    "business_principal_title": "principal_title",
    "business_principal_name": "principal_name",
}


def apply_business_to_state(business: dict | None) -> None:
    business = business or {}
    for state_key, source_key in BUSINESS_FIELDS.items():
        value = business.get(source_key, "")
        if state_key == "business_round_no":
            try:
                value = int(value or 1)
            except (TypeError, ValueError):
                value = 1
        st.session_state[state_key] = value
    st.session_state["business_selected_id"] = business.get("id") or ""


def business_from_state() -> dict:
    return {
        "id": st.session_state.get("business_selected_id") or None,
        "name": str(st.session_state.get("business_name", "")).strip(),
        "research_project_name": str(st.session_state.get("business_research_project_name", "")).strip(),
        "project_number": str(st.session_state.get("business_project_number", "")).strip(),
        "support_organization": str(st.session_state.get("business_support_organization", "")).strip(),
        "total_research_period": str(st.session_state.get("business_total_research_period", "")).strip(),
        "round_no": int(st.session_state.get("business_round_no", 1) or 1),
        "round_research_period": str(st.session_state.get("business_round_research_period", "")).strip(),
        "principal_affiliation": str(st.session_state.get("business_principal_affiliation", "")).strip(),
        "principal_title": str(st.session_state.get("business_principal_title", "")).strip(),
        "principal_name": str(st.session_state.get("business_principal_name", "")).strip(),
    }


def _render_business_fields(disabled: bool) -> None:
    st.text_input("지원사업명", key="business_name", disabled=disabled)
    st.text_input("연구과제명", key="business_research_project_name", disabled=disabled)
    c1, c2 = st.columns(2)
    with c1:
        st.text_input("과제번호", key="business_project_number", disabled=disabled)
        st.text_input("총 연구기간", key="business_total_research_period", disabled=disabled)
    with c2:
        st.text_input("지원기관", key="business_support_organization", disabled=disabled)
        st.number_input(
            "차수",
            min_value=1,
            max_value=99,
            step=1,
            key="business_round_no",
            disabled=disabled,
        )
    st.text_input("해당 차 연구기간", key="business_round_research_period", disabled=disabled)
    c3, c4, c5 = st.columns(3)
    with c3:
        st.text_input("연구책임자 소속", key="business_principal_affiliation", disabled=disabled)
    with c4:
        st.text_input("연구책임자 직급", key="business_principal_title", disabled=disabled)
    with c5:
        st.text_input("연구책임자 성명", key="business_principal_name", disabled=disabled)


def render_business_form(
    businesses: list[dict],
    save_business_callback: Callable[[dict], dict] | None = None,
) -> dict:
    by_name = {str(x.get("name", "")): x for x in businesses if x.get("name")}
    options = list(by_name.keys()) + ["＋ 새로운 사업 직접 입력"]
    if not options:
        options = ["＋ 새로운 사업 직접 입력"]

    requested = st.session_state.pop("_requested_business_name", None)
    if requested in options:
        st.session_state["business_selector"] = requested

    selected = st.selectbox("지원사업", options, key="business_selector")
    is_new = selected.startswith("＋")
    last = st.session_state.get("_last_business_selector")

    if selected != last:
        apply_business_to_state(None if is_new else by_name.get(selected))
        st.session_state["_last_business_selector"] = selected
        st.session_state["_business_edit_mode"] = bool(is_new)

    edit_mode = bool(st.session_state.get("_business_edit_mode", is_new))

    with st.expander("사업 정보", expanded=True):
        _render_business_fields(disabled=(not is_new and not edit_mode))

        if is_new:
            st.caption("신규 사업은 최종 회의록 생성 시 자동으로 DB에 등록됩니다.")
        elif not edit_mode:
            if st.button("사업 정보 수정", key="business_edit_start"):
                st.session_state["_business_edit_mode"] = True
                st.rerun()
        else:
            c1, c2 = st.columns(2)
            with c1:
                if st.button("수정사항 저장", type="primary", key="business_edit_save"):
                    if save_business_callback is None:
                        st.error("사업 정보 저장 기능이 연결되지 않았습니다.")
                    else:
                        try:
                            saved = save_business_callback(business_from_state())
                            saved_name = str(saved.get("name") or "").strip()
                            if saved_name:
                                st.session_state["_requested_business_name"] = saved_name
                                st.session_state["_last_business_selector"] = saved_name
                            apply_business_to_state(saved)
                            st.session_state["_business_edit_mode"] = False
                            st.success("사업 기본정보를 저장했습니다.")
                            st.rerun()
                        except Exception as exc:
                            st.error(str(exc))
            with c2:
                if st.button("취소", key="business_edit_cancel"):
                    original = by_name.get(selected)
                    apply_business_to_state(original)
                    st.session_state["_business_edit_mode"] = False
                    st.rerun()

    return business_from_state()


def trigger_file_download(
    data: bytes,
    file_name: str,
    mime: str = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
) -> None:
    payload = base64.b64encode(data).decode("ascii")
    js_filename = json.dumps(file_name, ensure_ascii=False)
    js_mime = json.dumps(mime)
    components.html(
        f"""
        <script>
        (() => {{
          const a = document.createElement("a");
          a.href = "data:" + {js_mime} + ";base64,{payload}";
          a.download = {js_filename};
          a.style.display = "none";
          document.body.appendChild(a);
          a.click();
          setTimeout(() => a.remove(), 1000);
        }})();
        </script>
        """,
        height=0,
        width=0,
    )
