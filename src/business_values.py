"""Normalize optional business text without storing display placeholders."""


def business_text(value: object) -> str:
    text = "" if value is None else str(value).strip()
    return "" if text.lower() in {"none", "null"} else text


def business_display(value: object) -> str:
    return business_text(value) or "-"
