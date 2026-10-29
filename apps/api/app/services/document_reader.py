"""Bounded, local G1 reading. Machine reconciliation never establishes semantic truth."""

import csv
import hashlib
import io
import json
import os
import re
import resource
import subprocess
import sys
import time
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import fitz
from app.errors import ReviewError
from app.services.storage import decode_text
from app.services.versioning import content_hash
from PIL import Image

PIPELINE_VERSION = "source-reading-g1-v1.1"


def issue(code, message):
    return {"code": code, "message": message}


def require_tool(name):
    import shutil

    executable = shutil.which(name)
    if not executable:
        raise ReviewError("READER_DEPENDENCY_MISSING", f"缺少读取工具 {name}，未将来源标为已读")
    return executable


def command(args, timeout):
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            timeout=timeout,
            check=False,
            env={**os.environ, "LC_ALL": "C", "OMP_THREAD_LIMIT": "1"},
        )
    except subprocess.TimeoutExpired as exc:
        raise ReviewError("READER_TIMEOUT", "本地读取超过预算，保留缺口") from exc
    if result.returncode:
        raise ReviewError("READER_TOOL_FAILED", f"本地读取工具退出 {result.returncode}，未跳过来源")
    return (result.stdout or result.stderr).decode("utf-8", errors="replace")


def normalized(text):
    return "".join(c.lower() for c in unicodedata.normalize("NFKC", text) if c.isalnum())


def text_agreement(native, visual):
    left, right = normalized(native), normalized(visual)
    if not left:
        return None
    if not right:
        return 0.0
    grams = {left[i : i + 2] for i in range(max(1, len(left) - 1))}
    return round(sum(g in right for g in grams) / len(grams), 4)


def unit(key, kind, locator, native="", ocr="", parent=None, issues=None, features=None, artifacts=None):
    return {
        "unit_key": key,
        "parent_key": parent,
        "kind": kind,
        "locator": locator,
        "native_text": native,
        "ocr_text": ocr,
        "content_hash": content_hash({"native": native, "ocr": ocr, "locator": locator}),
        "state": "BLOCKED"
        if any(
            i["code"]
            in {
                "NONBLANK_WITHOUT_TEXT",
                "DOCX_RENDER_NOT_QUALIFIED",
                "UNSUPPORTED_PACKAGE_PART",
                "UNSUPPORTED_IMAGE",
                "PDF_ATTACHMENT_NOT_READ",
                "EXTERNAL_REFERENCE_NOT_FETCHED",
            }
            for i in (issues or [])
        )
        else "NEEDS_HUMAN"
        if issues
        else "CANDIDATE",
        "issues": issues or [],
        "features": features or {},
        "artifacts": artifacts or {},
    }


def ocr_image(path, timeout, psm=3):
    text = command(
        [require_tool("tesseract"), str(path), "stdout", "-l", "chi_sim+eng", "--psm", str(psm), "tsv"],
        timeout,
    )
    paragraphs = {}
    for row in csv.DictReader(io.StringIO(text), delimiter="\t", quoting=csv.QUOTE_NONE):
        if row.get("level") != "5" or not row.get("text", "").strip():
            continue
        key = (row["block_num"], row["par_num"])
        group = paragraphs.setdefault(key, {"words": [], "boxes": [], "confidence": []})
        group["words"].append(row["text"])
        x, y, w, h = (int(row[k]) for k in ["left", "top", "width", "height"])
        group["boxes"].append((x, y, x + w, y + h))
        group["confidence"].append(float(row["conf"]))
    output = []
    for group in paragraphs.values():
        boxes = group["boxes"]
        output.append(
            {
                "text": " ".join(group["words"]),
                "bbox": [
                    min(x[0] for x in boxes),
                    min(x[1] for x in boxes),
                    max(x[2] for x in boxes),
                    max(x[3] for x in boxes),
                ],
                "confidence": round(sum(group["confidence"]) / len(group["confidence"]), 2),
            }
        )
    return {"text": "\n".join(p["text"] for p in output), "paragraphs": output}


def save_png(image, path, limits):
    if image.width * image.height > limits["max_pixels_per_render"]:
        raise ReviewError("READER_PIXEL_BUDGET", "渲染像素超过预算")
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "PNG")


def pdf_count(path):
    output = command([require_tool("pdfinfo"), str(path)], 30)
    pages = re.search(r"^Pages:\s+(\d+)\s*$", output, re.M)
    if not pages:
        raise ReviewError("PDF_INVENTORY_INVALID", "独立PDF清点没有返回页数")
    return int(pages.group(1))


def read_pdf(path, directory, policy):
    limits = policy["reader_limits"]
    independent = pdf_count(path)
    units, rendered, expected = [], [], []
    with fitz.open(path) as document:
        if document.is_encrypted:
            raise ReviewError("PDF_ENCRYPTED", "PDF加密，未尝试绕过密码")
        if document.page_count != independent:
            raise ReviewError("PDF_PAGE_COUNT_MISMATCH", "PDF结构与独立清点页数不一致")
        if independent > policy["p0"]["max_pdf_pages"]:
            raise ReviewError("SOURCE_CAPACITY_EXCEEDED", "PDF超过已配置200页预览预算；未只读前200页")
        languages = command([require_tool("tesseract"), "--list-langs"], 20).splitlines()
        if not {"chi_sim", "eng"}.issubset(set(languages)):
            raise ReviewError("READER_DEPENDENCY_MISSING", "本地OCR缺少chi_sim或eng语言包")
        for index in range(document.page_count):
            page = document[index]
            key = f"page:{index + 1}"
            expected.append(key)
            image_path = directory / f"page-{index + 1}.png"
            scale = limits["base_render_dpi"] / 72
            if page.rect.width * scale * page.rect.height * scale > limits["max_pixels_per_render"]:
                raise ReviewError("READER_PIXEL_BUDGET", "页面渲染像素超预算，未缩小后谎称读全")
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            image = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
            save_png(image, image_path, limits)
            rendered.append(key)
            reading = ocr_image(image_path, limits["ocr_timeout_seconds"])
            native = page.get_text("text", sort=True)
            gray = image.convert("L")
            hist = gray.histogram()
            ink = round(sum(hist[:240]) / (image.width * image.height), 6)
            concerns = []
            agreement = text_agreement(native, reading["text"])
            if agreement is not None and agreement < 0.7:
                concerns.append(
                    issue("NATIVE_OCR_DISAGREEMENT", "原生文字与独立OCR存在差异；差异不是确定漏字结论")
                )
            if ink > 0.002 and not normalized(reading["text"]) and not normalized(native):
                concerns.append(
                    issue("NONBLANK_WITHOUT_TEXT", "原页有可见内容但两路未读到文字，不能按空白页跳过")
                )
            if not normalized(native) and normalized(reading["text"]):
                concerns.append(issue("VISUAL_ONLY_TEXT", "仅视觉OCR读取到文字，须对照原图核对"))
            page_unit = unit(
                key,
                "page",
                {"page": index + 1},
                native,
                reading["text"],
                issues=concerns,
                features={
                    "rotation_degrees": page.rotation,
                    "coordinate_space": "normalized_display_raster",
                    "width": image.width,
                    "height": image.height,
                    "ink_fraction": ink,
                    "blank_candidate": ink <= 0.002 and not native.strip() and not reading["text"].strip(),
                    "native_ocr_bigram_agreement": agreement,
                    "reading_order": "native_sort_plus_independent_raster_ocr_not_verified",
                },
                artifacts={"image": image_path.name},
            )
            units.append(page_unit)
            tiny = 0
            if len(page.get_images()) > limits["max_pdf_image_regions"]:
                raise ReviewError("PDF_IMAGE_REGION_BUDGET", "图像区域数超过预算，未跳过后续区域")
            for block_index, block in enumerate(page.get_text("dict")["blocks"]):
                x0, y0, x1, y1 = fitz.Rect(block["bbox"]) * page.rotation_matrix
                bbox = [
                    x0 / page.rect.width,
                    y0 / page.rect.height,
                    x1 / page.rect.width,
                    y1 / page.rect.height,
                ]
                is_image = block["type"] == 1
                text = "\n".join(
                    "".join(span["text"] for span in line["spans"]) for line in block.get("lines", [])
                )
                if not is_image and not text.strip():
                    continue
                flags = (
                    [
                        issue(
                            "VISUAL_REGION_REVIEW",
                            "图像可能包含示例、图中文字或非文字约束，OCR不等于理解完整",
                        )
                    ]
                    if is_image
                    else []
                )
                if any(span["size"] < 7 for line in block.get("lines", []) for span in line["spans"]):
                    flags.append(issue("TINY_NATIVE_TEXT", "存在小于7pt的文字，需要原页核对"))
                    tiny += 1
                region = unit(
                    f"{key}:native:{block_index}",
                    "image" if is_image else "text_region",
                    {"page": index + 1, "bbox": bbox},
                    text,
                    parent=key,
                    issues=flags,
                    features={"path": "pymupdf_layout"},
                )
                units.append(region)
            for region_index, paragraph in enumerate(reading["paragraphs"]):
                bbox = [
                    paragraph["bbox"][0] / image.width,
                    paragraph["bbox"][1] / image.height,
                    paragraph["bbox"][2] / image.width,
                    paragraph["bbox"][3] / image.height,
                ]
                flags = (
                    [issue("LOW_OCR_CONFIDENCE", "OCR候选置信较低，不能自动确认")]
                    if paragraph["confidence"] < 60
                    else []
                )
                units.append(
                    unit(
                        f"{key}:ocr:{region_index}",
                        "ocr_region",
                        {"page": index + 1, "bbox": bbox},
                        ocr=paragraph["text"],
                        parent=key,
                        issues=flags,
                        features={"path": "tesseract_full_raster", "confidence": paragraph["confidence"]},
                    )
                )
            for annotation_index, annotation in enumerate(page.annots() or []):
                rect = annotation.rect * page.rotation_matrix
                units.append(
                    unit(
                        f"{key}:annotation:{annotation_index}",
                        "annotation",
                        {
                            "page": index + 1,
                            "bbox": [
                                rect.x0 / page.rect.width,
                                rect.y0 / page.rect.height,
                                rect.x1 / page.rect.width,
                                rect.y1 / page.rect.height,
                            ],
                        },
                        native=annotation.info.get("content", ""),
                        parent=key,
                        issues=[issue("PDF_ANNOTATION_REVIEW", "PDF注释单独保留，不能只看正文")],
                        features={"annotation_type": annotation.type[1]},
                    )
                )
            for link_index, link in enumerate(page.get_links()):
                if link.get("uri") or link.get("file"):
                    units.append(
                        unit(
                            f"{key}:external:{link_index}",
                            "external_reference",
                            {"page": index + 1},
                            native=link.get("uri") or link["file"],
                            parent=key,
                            issues=[
                                issue(
                                    "EXTERNAL_REFERENCE_NOT_FETCHED", "外链引用未自动读取，材料范围需人工核对"
                                )
                            ],
                        )
                    )
            # A second full-raster reading does not depend on native-region detection.
            needs_retry = (
                agreement is not None
                and agreement < 0.7
                or not reading["text"].strip()
                and ink > 0.002
                or tiny > 0
            )
            if needs_retry:
                retry = ocr_image(image_path, limits["ocr_timeout_seconds"], psm=11)
                page_unit["features"]["second_ocr"] = {
                    "psm": 11,
                    "text": retry["text"],
                    "agreement": text_agreement(native, retry["text"]),
                }
            reread_regions(units, page_unit, page, directory, policy)
            unresolved = [u for u in units if u["parent_key"] == key and u["state"] != "CANDIDATE"]
            page_unit["features"]["unresolved_child_regions"] = len(unresolved)
            if unresolved:
                page_unit["issues"].append(
                    issue(
                        "CHILD_REGIONS_REQUIRE_REVIEW",
                        f"本页有{len(unresolved)}个区域待核对；页级一致不代表图中文字已读准",
                    )
                )
                page_unit["state"] = (
                    "BLOCKED" if any(u["state"] == "BLOCKED" for u in unresolved) else "NEEDS_HUMAN"
                )
            if len(units) > limits["max_units_per_file"]:
                raise ReviewError("READER_UNIT_BUDGET", "检测单元超过预算，未只保留前N个")
            (directory / "progress.json").write_text(
                json.dumps({"scanned_pages": index + 1, "expected_pages": independent, "persisted": False})
            )
            if sum(p.stat().st_size for p in directory.glob("*.png")) > limits["artifact_max_bytes"]:
                raise ReviewError("READER_ARTIFACT_BUDGET", "渲染产物超过预算，保留失败状态")
        for attachment_index, name in enumerate(document.embfile_names()):
            if len(units) >= limits["max_units_per_file"]:
                raise ReviewError("READER_UNIT_BUDGET", "附件单元超过预算，未丢弃后续附件")
            key = f"attachment:{attachment_index}"
            expected.append(key)
            units.append(
                unit(
                    key,
                    "attachment",
                    {"attachment_name": name, "scope": "document"},
                    issues=[issue("PDF_ATTACHMENT_NOT_READ", "PDF内嵌附件已登记但未读取，须独立入库")],
                    features={"attachment_info": document.embfile_info(name)},
                )
            )
    return {
        "inventory": {
            "kind": "pdf",
            "native_page_count": independent,
            "independent_page_count": independent,
            "independent_counter": "poppler_pdfinfo",
            "expected_root_keys": expected,
            "rendered_root_keys": rendered,
        },
        "units": units,
    }


def reread_regions(units, page_unit, page, directory, policy):
    limits = policy["reader_limits"]
    candidates = [
        u
        for u in units
        if u["parent_key"] == page_unit["unit_key"]
        and any(i["code"] in ["LOW_OCR_CONFIDENCE", "TINY_NATIVE_TEXT"] for i in u["issues"])
    ]
    # The bound is a resource limit, not permission to hide remaining regions.
    selected = candidates[: limits["max_region_rereads_per_page"]]
    if len(selected) < len(candidates):
        page_unit["issues"].append(
            issue("REREAD_CAPACITY_EXCEEDED", "待重读区域超出单页预算，剩余区域保持未决")
        )
    for i, region in enumerate(selected):
        bbox = region["locator"]["bbox"]
        rect = fitz.Rect(
            bbox[0] * page.rect.width,
            bbox[1] * page.rect.height,
            bbox[2] * page.rect.width,
            bbox[3] * page.rect.height,
        )
        rect = (rect + (-2, -2, 2, 2)) & page.rect
        if rect.is_empty:
            continue
        for attempt in range(1, limits["max_retries"] + 1):
            scale = limits["max_render_dpi"] / 72
            if rect.width * scale * rect.height * scale > limits["max_pixels_per_render"]:
                region["issues"].append(issue("REGION_PIXEL_BUDGET", "区域放大超过预算，保持未决"))
                break
            crop = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=rect, alpha=False)
            name = f"region-{page.number + 1}-{i}-{attempt}.png"
            image_path = directory / name
            image = Image.open(io.BytesIO(crop.tobytes("png"))).convert("RGB")
            save_png(image, image_path, limits)
            response = ocr_image(image_path, limits["ocr_timeout_seconds"], psm=6 if attempt == 1 else 11)
            region["features"].setdefault("reread_attempts", []).append(
                {
                    "attempt": attempt,
                    "dpi": limits["max_render_dpi"],
                    "text": response["text"],
                    "confidence": [p["confidence"] for p in response["paragraphs"]],
                }
            )
            region["artifacts"][f"reread_{attempt}"] = name
            if response["text"].strip() and all(p["confidence"] >= 60 for p in response["paragraphs"]):
                break


def archive_inventory(path, limits):
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        if len(members) > limits["max_archive_members"]:
            raise ReviewError("ARCHIVE_MEMBER_BUDGET", "Office包部件数量超过预算")
        total = sum(m.file_size for m in members)
        if total > limits["max_uncompressed_bytes"] or any(
            m.file_size / max(1, m.compress_size) > limits["max_zip_ratio"] for m in members
        ):
            raise ReviewError("ARCHIVE_SIZE_BUDGET", "Office解压大小或压缩比超过预算")
        if any(m.filename.startswith("/") or ".." in Path(m.filename).parts for m in members):
            raise ReviewError("ARCHIVE_PATH_INVALID", "Office包存在非法部件路径")
        if "word/document.xml" not in archive.namelist():
            raise ReviewError("DOCX_INVALID", "缺少Word正文部件")
        return [(m.filename, m.file_size) for m in members if not m.is_dir()]


def read_docx(path, directory, policy):
    limits = policy["reader_limits"]
    parts = archive_inventory(path, limits)
    units, expected, consumed = [], [], []
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(path) as archive:
        for name, size in parts:
            if not name.startswith("word/") or not (name.endswith(".xml") or "/media/" in name):
                continue
            key = f"part:{name}"
            expected.append(key)
            raw = archive.read(name)
            flags = []
            if "/media/" in name:
                flags.append(
                    issue("DOCX_VISUAL_NOT_QUALIFIED", "Word图片已清点，但版面与图像语义未获完整资格")
                )
                try:
                    image = Image.open(io.BytesIO(raw)).convert("RGB")
                    dest = directory / f"embedded-{len(consumed)}.png"
                    save_png(image, dest, limits)
                    reading = ocr_image(dest, limits["ocr_timeout_seconds"])
                    units.append(
                        unit(
                            key,
                            "image",
                            {"part": name},
                            ocr=reading["text"],
                            issues=flags,
                            artifacts={"image": dest.name},
                        )
                    )
                except ReviewError:
                    raise
                except (OSError, ValueError):
                    units.append(
                        unit(
                            key,
                            "unsupported_part",
                            {"part": name},
                            issues=flags + [issue("UNSUPPORTED_IMAGE", "嵌入图像无法安全解码，未丢弃该部件")],
                        )
                    )
                consumed.append(key)
                continue
            try:
                element = ET.fromstring(raw)
            except ET.ParseError as exc:
                raise ReviewError("DOCX_XML_INVALID", "Word部件XML无效") from exc
            text = "\n".join(t.text or "" for t in element.iter(ns + "t"))
            if (
                next(element.iter(ns + "ins"), None) is not None
                or next(element.iter(ns + "del"), None) is not None
            ):
                flags.append(issue("REVISION_POLICY_REQUIRED", "存在修订，须人工选择采纳视图"))
            if any(element.iter(ns + "txbxContent")):
                flags.append(issue("TEXTBOX_LAYOUT_REQUIRED", "文本框已登记，阅读顺序与版面需核对"))
            if name != "word/document.xml" and text:
                flags.append(issue("DOCX_AUXILIARY_PART", "页眉脚/脚注/批注等内容单独登记，适用关系待核对"))
            if name == "word/document.xml":
                flags.append(
                    issue("DOCX_RENDER_NOT_QUALIFIED", "已读取XML结构，独立Word版面渲染尚未资格验证")
                )
            units.append(
                unit(
                    key,
                    "document_part",
                    {"part": name},
                    native=text,
                    issues=flags,
                    features={"bytes": size, "source_sha256": hashlib.sha256(raw).hexdigest()},
                )
            )
            consumed.append(key)
            for i, paragraph in enumerate(element.iter(ns + "p")):
                text = "".join(
                    node.text or "" for node in paragraph.iter() if node.tag in [ns + "t", ns + "delText"]
                )
                if text:
                    units.append(
                        unit(
                            f"{key}:p:{i}",
                            "paragraph",
                            {"part": name, "paragraph_index": i},
                            native=text,
                            parent=key,
                            features={"view": "raw_contains_revisions_not_approved"},
                        )
                    )
            for table_index, table in enumerate(element.iter(ns + "tbl")):
                rows = table.findall(ns + "tr")
                if len(rows) > limits["max_table_rows"]:
                    raise ReviewError("TABLE_DIMENSION_BUDGET", "表格行数超预算，未截断表格")
                for ri, row in enumerate(rows):
                    cells = row.findall(ns + "tc")
                    if len(cells) > limits["max_table_columns"]:
                        raise ReviewError("TABLE_DIMENSION_BUDGET", "表格列数超预算，未截断表格")
                    for ci, cell in enumerate(cells):
                        text = "\n".join(t.text or "" for t in cell.iter(ns + "t"))
                        units.append(
                            unit(
                                f"{key}:t:{table_index}:r:{ri}:c:{ci}",
                                "table_cell",
                                {"part": name, "table_index": table_index, "row": ri, "column": ci},
                                native=text,
                                parent=key,
                                features={
                                    "merged": cell.find(".//" + ns + "gridSpan") is not None
                                    or cell.find(".//" + ns + "vMerge") is not None
                                },
                            )
                        )
        extras = [
            name
            for name, _ in parts
            if name.startswith("word/") and name not in [key[5:] for key in consumed]
        ]
        for name in extras:
            key = f"part:{name}"
            expected.append(key)
            consumed.append(key)
            units.append(
                unit(
                    key,
                    "package_part",
                    {"part": name},
                    issues=[issue("UNSUPPORTED_PACKAGE_PART", "包内关系/嵌入等部件未获语义读取资格")],
                )
            )
    return {
        "inventory": {
            "kind": "docx",
            "archive_members": len(parts),
            "parts": [n for n, _ in parts],
            "expected_root_keys": expected,
            "rendered_root_keys": consumed,
            "physical_pages": None,
        },
        "units": units,
    }


def read_txt(path, policy):
    text = decode_text(path.read_bytes())
    if len(text) > policy["reader_limits"]["max_text_characters"]:
        raise ReviewError("TEXT_CHARACTER_BUDGET", "文本字符超过预算，未截断后续正文")
    if not text.strip():
        raise ReviewError("EMPTY_TEXT_SOURCE", "文本来源为空，未标为完整")
    lines = text.splitlines()
    units = [
        unit(f"line:{i + 1}", "line", {"line_start": i + 1, "line_end": i + 1}, native=text)
        for i, text in enumerate(lines)
    ]
    return {
        "inventory": {
            "kind": "txt",
            "line_count": len(lines),
            "characters": len(text),
            "expected_root_keys": [u["unit_key"] for u in units],
            "rendered_root_keys": [u["unit_key"] for u in units],
        },
        "units": units,
    }


def read_document(path, directory, policy):
    started = time.monotonic()
    path, directory = Path(path), Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    if path.stat().st_size > policy["p0"]["max_file_bytes"]:
        raise ReviewError("SOURCE_CAPACITY_EXCEEDED", "文件超过配置预算，未读取部分后视为完成")
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        result = read_pdf(path, directory, policy)
    elif suffix == ".docx":
        result = read_docx(path, directory, policy)
    elif suffix == ".txt":
        result = read_txt(path, policy)
    else:
        raise ReviewError("FORMAT_NOT_QUALIFIED", "该格式仅可旧版预览，未获生产读取资格")
    if len(result["units"]) > policy["reader_limits"]["max_units_per_file"]:
        raise ReviewError("READER_UNIT_BUDGET", "结构单元超过预算，未截断来源")
    if (
        sum(len(u["native_text"]) + len(u["ocr_text"]) for u in result["units"])
        > policy["reader_limits"]["max_text_characters"]
    ):
        raise ReviewError("TEXT_CHARACTER_BUDGET", "读取总字符超过预算，保留失败而非截断结果")
    result["toolchain"] = {
        "pipeline": PIPELINE_VERSION,
        "pymupdf": fitz.VersionBind,
        "memory_guard": "parent_process_group_rss_250ms",
        "poppler": command([require_tool("pdfinfo"), "-v"], 15).splitlines()[0] if suffix == ".pdf" else None,
        "ocr_languages": "chi_sim+eng" if suffix != ".txt" else None,
        "ocr": command([require_tool("tesseract"), "--version"], 15).splitlines()[0]
        if suffix != ".txt"
        else None,
    }
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    result["peak_rss_bytes"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (
        1 if sys.platform == "darwin" else 1024
    )
    return result


def main():
    path, directory, policy_file, output = sys.argv[1:]
    policy = json.loads(Path(policy_file).read_text())
    limits = policy["reader_limits"]
    resource.setrlimit(resource.RLIMIT_CPU, (limits["cpu_seconds"], limits["cpu_seconds"]))
    # The parent monitors the complete process group's physical RSS on macOS/Linux.
    # macOS rejects RLIMIT_DATA values despite reporting an unlimited hard ceiling.
    if sys.platform.startswith("linux"):
        resource.setrlimit(resource.RLIMIT_AS, (limits["memory_bytes"], limits["memory_bytes"]))
    resource.setrlimit(resource.RLIMIT_FSIZE, (limits["artifact_max_bytes"], limits["artifact_max_bytes"]))
    try:
        result = read_document(path, directory, policy)
    except Exception as exc:
        result = {
            "error": {
                "code": exc.code if isinstance(exc, ReviewError) else "READER_FAILED",
                "message": str(exc) if isinstance(exc, ReviewError) else "来源读取失败，缺口未消除",
            }
        }
    Path(output).write_text(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
