import re

HEADER_ROLES = {
    "时长": "time_text",
    "时间": "time_text",
    "duration": "time_text",
    "time": "time_text",
    "场景": "scene",
    "scene": "scene",
    "画面": "visual",
    "画面内容": "visual",
    "visual": "visual",
    "台词": "dialogue",
    "台词/画外音": "dialogue",
    "口播": "dialogue",
    "dialogue": "dialogue",
    "画面参考": "reference",
    "备注": "notes",
    "reference": "reference",
}


def normalize_script(blocks):
    """Reconstruct explicitly labelled spreadsheet rows; never infer missing shot times."""
    sheets = {}
    for block in blocks:
        locator = block.locator
        if locator["kind"] != "xlsx":
            continue
        match = re.fullmatch(r"([A-Z]+)(\d+)", locator["cell"])
        if match:
            sheets.setdefault(locator["sheet"], {}).setdefault(int(match[2]), {})[match[1]] = block
    units = []
    for sheet, rows in sheets.items():
        headers = {}
        for number, cells in sorted(rows.items()):
            candidate = {
                column: HEADER_ROLES[cell.text.strip().lower()]
                for column, cell in cells.items()
                if cell.text.strip().lower() in HEADER_ROLES
            }
            if len(candidate) >= 2:
                headers = candidate
                continue
            if not headers:
                continue
            values = {
                role: cells[column].text if column in cells else None for column, role in headers.items()
            }
            if not any(values.values()):
                continue
            units.append(
                {
                    "sheet": sheet,
                    "row": number,
                    **values,
                    "start_ms": None,
                    "end_ms": None,
                    "source_ids": [cell.id for cell in cells.values()],
                }
            )
    if not units:
        units = [
            {
                "paragraph": index + 1,
                "text": block.text,
                "time_text": None,
                "start_ms": None,
                "end_ms": None,
                "source_ids": [block.id],
            }
            for index, block in enumerate(blocks)
        ]
    return {
        "units": units,
        "method": "explicit_headers" if sheets else "source_order",
        "limits": ["空白时间保持未知；未从表格顺序推测精确秒数。"],
    }
