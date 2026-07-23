from __future__ import annotations

import html
import json

import streamlit as st
import streamlit.components.v1 as components


def result_box(title: str, text: str, key: str, height: int = 150) -> None:
    safe_title = html.escape(title)
    safe_text = html.escape(text)
    js_text = json.dumps(text, ensure_ascii=False)
    components.html(
        f"""
        <div style="font-family:Arial,'Noto Sans KR',sans-serif;border:1px solid #d7dee8;border-radius:14px;background:#fff;padding:16px 18px;box-shadow:0 3px 12px rgba(31,55,80,.06)">
          <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">
            <strong style="font-size:17px;color:#1b2b3a">{safe_title}</strong>
            <button id="btn-{key}" onclick='copyText{key}()' style="border:1px solid #1f5c99;background:#fff;color:#1f5c99;border-radius:8px;padding:6px 12px;cursor:pointer;font-weight:700">복사</button>
          </div>
          <div style="white-space:pre-wrap;line-height:1.7;font-size:15px;color:#263442;user-select:text">{safe_text}</div>
        </div>
        <script>
          async function copyText{key}() {{
            await navigator.clipboard.writeText({js_text});
            const b=document.getElementById('btn-{key}'); b.innerText='복사됨';
            setTimeout(()=>b.innerText='복사',1200);
          }}
        </script>
        """,
        height=height,
        scrolling=False,
    )
