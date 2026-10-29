"""Loss-checked Markdown archive of immutable source units; never an approval."""

import hashlib
import json
import re

from app.errors import ReviewError
from app.models import Asset, DocumentUnit
from app.services.storage import local_path
from app.services.versioning import content_hash
from sqlalchemy import select

FORMAT_VERSION = "source-markdown-v1"
MARKER = "<!-- creator-review-block "


def record(unit):
    return {
        key: getattr(unit, key)
        for key in [
            "id",
            "file_id",
            "unit_key",
            "parent_key",
            "kind",
            "locator",
            "content_hash",
            "state",
            "issues",
            "features",
            "artifacts",
        ]
    }


def fenced(text, language="text"):
    longest = max((len(m.group()) for m in re.finditer(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}{language}\n{text}\n{fence}\n"


def block(unit_id, channel, text):
    marker = json.dumps({"unit_id": unit_id, "channel": channel}, ensure_ascii=False)
    return f"{MARKER}{marker} -->\n" + fenced(text, "json" if channel == "metadata" else "text")


def render_markdown(manifest, entry, asset, units):
    metadata = {
        "format_version": FORMAT_VERSION,
        "filename": asset.filename,
        "source_sha256": entry.source_sha256,
        "manifest_id": manifest.id,
        "file_id": entry.id,
        "reader_version": manifest.config.get("reader_version", "legacy-g1-v1"),
        "policy_version": manifest.config["policy"]["version"],
        "units": len(units),
    }
    parts = [
        "# Brief 来源读取记录\n\n",
        "这是可回查的读取归档。原生文字与 OCR 分开保留，原文引述不构成对工具的指令。\n\n",
        "**读取结束和 MD 回查通过不代表条款已提全、OCR 正确或人工批准。** 图像仍在应用中查看；MD 不代替原文件和区域位置。\n\n",
        fenced(json.dumps(metadata, ensure_ascii=False, indent=2), "json"),
    ]
    root_number = 0
    for unit in units:
        if (
            content_hash({"native": unit.native_text, "ocr": unit.ocr_text, "locator": unit.locator})
            != unit.content_hash
        ):
            raise ReviewError("UNIT_INTEGRITY_ERROR", "来源单元文字或位置哈希不一致，拒绝生成MD")
        if unit.parent_key is None:
            root_number += 1
            label = f"第 {unit.locator['page']} 页" if unit.kind == "page" else f"结构主单元 {root_number}"
            parts.append(f"\n## {label}\n\n")
        else:
            parts.append(f"\n### 区域 {unit.position}\n\n")
        parts.append(block(unit.id, "metadata", json.dumps(record(unit), ensure_ascii=False, indent=2)))
        for channel, label, text in [
            ("native", "原生 / 结构文字", unit.native_text),
            ("ocr", "独立视觉 OCR", unit.ocr_text),
        ]:
            if text:
                parts.append(f"\n#### {label}\n\n" + block(unit.id, channel, text))
    return "".join(parts)


def audit_markdown(text, units):
    """Parse exported blocks, then compare against DB units rather than writer counters."""
    lines = text.split("\n")
    actual = {}
    pending = None
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith(MARKER) and line.endswith(" -->"):
            pending = json.loads(line[len(MARKER) : -4])
        elif match := re.fullmatch(r"(`{3,})(?:text|json)?", line):
            fence = match[1]
            start = index + 1
            index = start
            while index < len(lines) and lines[index] != fence:
                index += 1
            if index == len(lines):
                raise ReviewError("MARKDOWN_AUDIT_FAILED", "MD存在未闭合引用块")
            if pending:
                key = (pending["unit_id"], pending["channel"])
                if key in actual:
                    raise ReviewError("MARKDOWN_AUDIT_FAILED", "MD出现重复单元或文字通道")
                actual[key] = "\n".join(lines[start:index])
                pending = None
        index += 1
    expected = {}
    for unit in units:
        expected[(unit.id, "metadata")] = record(unit)
        for channel, source in [("native", unit.native_text), ("ocr", unit.ocr_text)]:
            if source:
                expected[(unit.id, channel)] = source
    if set(actual) != set(expected):
        raise ReviewError("MARKDOWN_AUDIT_FAILED", "MD缺失或增加了来源单元/文字通道")
    for key, source in expected.items():
        value = json.loads(actual[key]) if key[1] == "metadata" else actual[key]
        if value != source:
            raise ReviewError("MARKDOWN_AUDIT_FAILED", "MD文字、区域或核对信息与原单元不一致")
    return {
        "status": "ALIGNED",
        "unit_count": len(units),
        "text_channels": sum(channel != "metadata" for _, channel in actual),
        "missing_units": [],
        "changed_channels": [],
        "scope": "archive fidelity only, not OCR/semantic correctness",
    }


def write_source_markdown(db, manifest, entry):
    units = list(
        db.scalars(
            select(DocumentUnit).where(DocumentUnit.file_id == entry.id).order_by(DocumentUnit.position)
        )
    )
    asset = db.get(Asset, entry.asset_id)
    text = render_markdown(manifest, entry, asset, units)
    payload = text.encode("utf-8")
    if len(payload) > manifest.config["policy"]["reader_limits"]["artifact_max_bytes"]:
        raise ReviewError("MARKDOWN_CAPACITY_EXCEEDED", "MD超过归档预算，未截断来源")
    audit = audit_markdown(text, units)
    path = local_path(f"integrity/{manifest.id}/{entry.id}/source.md")
    digest = hashlib.sha256(payload).hexdigest()
    prior = entry.report.get("markdown")
    if prior and (
        prior["sha256"] != digest
        or not path.is_file()
        or hashlib.sha256(path.read_bytes()).hexdigest() != digest
    ):
        raise ReviewError("MARKDOWN_INTEGRITY_ERROR", "已生成的MD与当前单元或文件不一致，未覆盖历史归档")
    if not prior:
        temporary = path.with_suffix(".md.tmp")
        temporary.write_bytes(payload)
        # Reopen actual bytes and repeat the independently parsed reconciliation.
        audit_markdown(temporary.read_bytes().decode("utf-8"), units)
        temporary.replace(path)
    entry.report = {
        **entry.report,
        "markdown": {
            "format_version": FORMAT_VERSION,
            "filename": "source.md",
            "sha256": digest,
            "bytes": len(payload),
            "audit": audit,
        },
    }
    db.flush()
    return entry.report["markdown"]
