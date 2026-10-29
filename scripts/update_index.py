"""Rebuild the public project's document catalog without private runtime data."""
from pathlib import Path

root = Path(__file__).resolve().parents[1]
rows = []
for pattern in ("LICENSE", "*.md", "docs/**/*.md", "docs/**/*.json", "evaluation/**/*.json", "tests/**/*.md"):
    for path in root.glob(pattern):
        if path.name == "DOCUMENT_INDEX.md" or "runs" in path.parts or "controlled" in path.parts:
            continue
        first = "MIT开源许可" if path.name == "LICENSE" else path.read_text().splitlines()[0] if path.suffix == ".md" else "Machine-readable protocol / schema; not runtime results"
        relative = path.relative_to(root)
        target = "../" + str(relative)
        rows.append(f"| [{relative}]({target}) | {first.lstrip('# ')} |")
(root / "docs/DOCUMENT_INDEX.md").write_text("# 文档索引\n\n| 文件 | 作用 |\n| --- | --- |\n" + "\n".join(sorted(set(rows))) + "\n")
print(f"Indexed {len(set(rows))} public documents and schemas.")
