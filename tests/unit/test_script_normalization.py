from types import SimpleNamespace

from app.services.script_normalization import normalize_script
from app.services.storage import decode_text


def test_gb18030_even_byte_text_is_not_misread_as_utf16():
    assert decode_text("中文".encode("gb18030")) == "中文"
    assert decode_text("中文".encode("utf-16")) == "中文"
    assert decode_text("中文".encode("utf-8-sig")) == "中文"


def test_explicit_spreadsheet_headers_preserve_unknown_timing():
    blocks = [
        SimpleNamespace(id=str(i), text=value, locator={"kind": "xlsx", "sheet": "脚本", "cell": cell})
        for i, (cell, value) in enumerate(
            [
                ("A9", "时长"),
                ("B9", "场景"),
                ("C9", "画面内容"),
                ("D9", "台词/画外音"),
                ("C10", "拿起杯子"),
                ("D10", "我喜欢这个口味"),
            ]
        )
    ]
    result = normalize_script(blocks)
    assert result["units"][0]["visual"] == "拿起杯子"
    assert result["units"][0]["dialogue"] == "我喜欢这个口味"
    assert result["units"][0]["time_text"] is None
    assert result["units"][0]["start_ms"] is None and result["units"][0]["end_ms"] is None
