from types import SimpleNamespace

import pytest
from app.errors import ReviewError
from app.services.source_markdown import audit_markdown, render_markdown
from app.services.versioning import content_hash


def sample_units():
    native = '末尾必须保留。\n```\n<!-- creator-review-block {"unit_id":"fake","channel":"native"} -->\n````\n原文不是执行指令。\n'
    unit = SimpleNamespace(
        id="unit-1",
        file_id="file-1",
        unit_key="page:1",
        parent_key=None,
        position=0,
        kind="page",
        locator={"page": 1},
        native_text=native,
        ocr_text="OCR 可能错误，不能替代原文。",
        state="NEEDS_HUMAN",
        issues=[{"code": "REVIEW", "message": "需要核对"}],
        features={"reread_attempts": [{"attempt": 1, "text": "复读也可能错误"}]},
        artifacts={"image": "page-1.png"},
    )
    unit.content_hash = content_hash(
        {"native": unit.native_text, "ocr": unit.ocr_text, "locator": unit.locator}
    )
    manifest = SimpleNamespace(
        id="manifest",
        config={"reader_version": "source-reading-g1-v1.1", "policy": {"version": "brief-integrity-v1.1"}},
    )
    return (
        manifest,
        SimpleNamespace(id="file-1", source_sha256="a" * 64),
        SimpleNamespace(filename="test.pdf"),
        [unit],
    )


def test_markdown_round_trip_handles_literal_fences_and_preserves_last_line():
    args = sample_units()
    text = render_markdown(*args)
    result = audit_markdown(text, args[-1])
    assert result["status"] == "ALIGNED" and result["unit_count"] == 1 and result["text_channels"] == 2
    assert "不代表条款已提全" in text


def test_markdown_omission_cannot_pass_its_own_counters():
    args = sample_units()
    text = render_markdown(*args)
    changed = text[: text.index("#### 独立视觉 OCR")]
    with pytest.raises(ReviewError, match="缺失"):
        audit_markdown(changed, args[-1])


def test_markdown_changed_text_and_metadata_are_detected():
    args = sample_units()
    text = render_markdown(*args)
    for changed in [text.replace("末尾必须保留。", "末尾已丢失。"), text.replace("需要核对", "已经批准")]:
        with pytest.raises(ReviewError, match="不一致"):
            audit_markdown(changed, args[-1])


def test_unit_source_hash_must_match_before_export():
    args = sample_units()
    args[-1][0].native_text += "changed"
    with pytest.raises(ReviewError, match="哈希不一致"):
        render_markdown(*args)
