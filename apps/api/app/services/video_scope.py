"""Preserve the range of every limitation; a segment cannot establish global absence."""

import re


def scoped_limitations(values, start_ms, end_ms, duration_ms, source):
    whole = start_ms == 0 and end_ms >= duration_ms
    records = []
    for text in values:
        if not isinstance(text, str):
            from app.errors import ReviewError

            raise ReviewError("MODEL_RESPONSE_INVALID", "观察限制必须是原始文字")
        overclaim = not whole and bool(
            re.search(r"(全片|全视频|视频全程|整个视频|整片|全程).*(未|没有|不存在|无)", text)
        )
        records.append(
            {
                "scope": "whole_input" if whole else "interval",
                "source": source,
                "start_ms": start_ms,
                "end_ms": min(end_ms, duration_ms),
                "raw_text": text,
                "status": "SCOPE_VIOLATION" if overclaim else "UNVERIFIED",
                "effect": "不构成全片缺失证据",
            }
        )
    return records


def render_limitation(record):
    return f"[{record['source']} {record['start_ms'] / 1000:g}–{record['end_ms'] / 1000:g}秒 · {record['status']}] {record['raw_text']}"
