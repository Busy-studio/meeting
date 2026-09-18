from __future__ import annotations

import streamlit as st


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


def render_business_form(businesses: list[dict]) -> dict:
    by_name = {str(x.get("name", "")): x for x in businesses if x.get("name")}
    options = list(by_name.keys()) + ["＋ 새로운 사업 직접 입력"]
    if not options:
        options = ["＋ 새로운 사업 직접 입력"]

    selected = st.selectbox("지원사업", options, key="business_selector")
    last = st.session_state.get("_last_business_selector")
    if selected != last:
        apply_business_to_state(None if selected.startswith("＋") else by_name.get(selected))
        st.session_state["_last_business_selector"] = selected

    with st.expander("사업 정보", expanded=True):
        st.text_input("지원사업명", key="business_name")
        st.text_input("연구과제명", key="business_research_project_name")
        c1, c2 = st.columns(2)
        with c1:
            st.text_input("과제번호", key="business_project_number")
            st.text_input("총 연구기간", key="business_total_research_period")
        with c2:
            st.text_input("지원기관", key="business_support_organization")
            st.number_input("차수", min_value=1, max_value=99, step=1, key="business_round_no")
        st.text_input("해당 차 연구기간", key="business_round_research_period")
        c3, c4, c5 = st.columns(3)
        with c3:
            st.text_input("연구책임자 소속", key="business_principal_affiliation")
        with c4:
            st.text_input("연구책임자 직급", key="business_principal_title")
        with c5:
            st.text_input("연구책임자 성명", key="business_principal_name")

    return business_from_state()