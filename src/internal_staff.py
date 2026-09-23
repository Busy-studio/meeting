"""Deterministic attendee composition for the separately managed internal roster."""
from __future__ import annotations

import re
from typing import Any

INTERNAL_ORGANIZATION = "부산대학교기술지주㈜"
_NORMALIZED_INTERNAL_ORGANIZATIONS = {"부산대학교기술지주", "부산대기술지주"}


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _normalize_organization(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).replace("주식회사", "").replace("㈜", "").replace("(주)", "").replace("（주）", "")


def merge_participants(selected_staff: list[dict[str, Any]], external_participants: str) -> str:
    """Put the checked staff first, using one internal affiliation prefix."""
    names = [_clean(" ".join(filter(None, (_clean(s.get("name")), _clean(s.get("title")))))) for s in selected_staff]
    names = [name for name in names if name]
    internal = f"{INTERNAL_ORGANIZATION} {', '.join(names)}" if names else ""
    external = str(external_participants or "").strip()
    return ", ".join(part for part in (internal, external) if part)


def selected_staff_items(selected_staff: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Preserve each checked staff member as a deterministic participant DB item."""
    result: list[dict[str, str]] = []
    for staff in selected_staff:
        name = _clean(staff.get("name"))
        if not name:
            continue
        title = _clean(staff.get("title"))
        result.append({
            "name": name,
            "normalized_name": re.sub(r"\s+", "", name),
            "organization": INTERNAL_ORGANIZATION,
            "normalized_organization": "부산대학교기술지주",
            "title": title,
            "raw_fragment": " ".join(part for part in (INTERNAL_ORGANIZATION, name, title) if part),
        })
    return result


def overlapping_staff_names(
    selected_staff: list[dict[str, Any]], external_items: list[dict[str, Any]]
) -> list[str]:
    """Flag an explicitly internal person also entered in the external text."""
    chosen = {re.sub(r"\s+", "", _clean(s.get("name"))) for s in selected_staff}
    duplicates: list[str] = []
    for person in external_items:
        normalized_org = _normalize_organization(person.get("normalized_organization") or person.get("organization"))
        name = _clean(person.get("name"))
        if normalized_org in _NORMALIZED_INTERNAL_ORGANIZATIONS and re.sub(r"\s+", "", name) in chosen and name not in duplicates:
            duplicates.append(name)
    return duplicates
