"""G1 source reading records. Independent from legacy parse/approval state."""

import hashlib
import json
import os
import signal
import subprocess
import sys
import time

from app.errors import ReviewError
from app.models import Asset, DocumentUnit, SourceManifest, SourceManifestFile
from app.services.document_reader import PIPELINE_VERSION, issue, pdf_count, require_tool
from app.services.runs import stage
from app.services.source_markdown import write_source_markdown
from app.services.storage import ensure_local, local_path
from app.services.versioning import content_hash
from app.settings import ROOT
from sqlalchemy import select


def policy():
    return json.loads((ROOT / "docs/BRIEF_G0_SPEC_v1.json").read_text())


def preflight_file(db, manifest, entry):
    asset = db.get(Asset, entry.asset_id)
    path = ensure_local(asset.storage_key)
    if path.stat().st_size > manifest.config["policy"]["p0"]["max_file_bytes"]:
        raise ReviewError("SOURCE_CAPACITY_EXCEEDED", "文件超过预算")
    if path.suffix.lower() == ".pdf":
        count = pdf_count(path)
        entry.inventory = {
            "independent_page_count": count,
            "expected_root_keys": [f"page:{i + 1}" for i in range(count)],
        }
        if count > manifest.config["policy"]["p0"]["max_pdf_pages"]:
            raise ReviewError("SOURCE_CAPACITY_EXCEEDED", "PDF超过配置页数预算，未读取前N页")
    db.flush()
    return entry.inventory


def run_reader(path, directory, policy_path, output_path, limits):
    monitor = require_tool("ps")
    with (directory / "process.log").open("wb") as error_log:
        child = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "app.services.document_reader",
                str(path),
                str(directory),
                str(policy_path),
                str(output_path),
            ],
            stdout=subprocess.DEVNULL,
            stderr=error_log,
            start_new_session=True,
            env={**os.environ, "PYTHONPATH": str(ROOT / "apps/api")},
        )
        started = time.monotonic()
        peak_rss = 0
        try:
            while child.poll() is None:
                processes = subprocess.check_output([monitor, "-axo", "pgid=,rss="], text=True, timeout=5)
                rss = sum(
                    int(parts[1]) * 1024
                    for line in processes.splitlines()
                    if len(parts := line.split()) == 2 and parts[0] == str(child.pid)
                )
                peak_rss = max(peak_rss, rss)
                if rss > limits["memory_bytes"]:
                    raise ReviewError("READER_MEMORY_BUDGET", "读取进程组超过内存预算，未标为完成")
                if time.monotonic() - started > limits["file_timeout_seconds"]:
                    raise ReviewError("READER_TIMEOUT", "文件读取超过总预算，未认定完成")
                time.sleep(0.25)
        finally:
            # Reap the complete group we created, including an orphaned OCR grandchild.
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait()
            (directory / "resource_usage.json").write_text(
                json.dumps(
                    {
                        "peak_group_rss_bytes": peak_rss,
                        "limit_bytes": limits["memory_bytes"],
                        "guard": "parent_process_group_rss_250ms",
                    }
                )
            )
        if child.returncode or not output_path.is_file():
            raise ReviewError("READER_RESOURCE_OR_PROCESS_FAILED", "读取进程异常或触发资源预算，未认定完成")


def inspect_file(db, manifest, entry):
    asset = db.get(Asset, entry.asset_id)
    path = ensure_local(asset.storage_key)
    if hashlib.sha256(path.read_bytes()).hexdigest() != entry.source_sha256:
        raise ReviewError("SOURCE_HASH_CHANGED", "原件内容与创建清单时的SHA不一致")
    directory = local_path(f"integrity/{manifest.id}/{entry.id}")
    directory.mkdir(parents=True, exist_ok=True)
    policy_path, output_path = directory / "policy.json", directory / "result.json"
    policy_path.write_text(json.dumps(manifest.config["policy"]))
    run_reader(path, directory, policy_path, output_path, manifest.config["policy"]["reader_limits"])
    output = json.loads(output_path.read_text())
    if output.get("error"):
        raise ReviewError(output["error"]["code"], output["error"]["message"])
    entry.inventory = {**output["inventory"], "detected_unit_keys": [u["unit_key"] for u in output["units"]]}
    existing = {u.unit_key for u in db.scalars(select(DocumentUnit).where(DocumentUnit.file_id == entry.id))}
    if existing:
        raise ReviewError("INVENTORY_STATE_CONFLICT", "文件已有单元产物但无对应完成检查点，须新建读取记录")
    keys = [u["unit_key"] for u in output["units"]]
    if len(keys) != len(set(keys)):
        raise ReviewError("UNIT_ID_DUPLICATED", "读取产物单元ID重复，拒绝记为完成")
    for index, record in enumerate(output["units"]):
        artifacts = record["artifacts"]
        record["features"]["artifact_sha256"] = {
            label: hashlib.sha256((directory / name).read_bytes()).hexdigest()
            for label, name in artifacts.items()
        }
        db.add(DocumentUnit(file_id=entry.id, project_id=entry.project_id, position=index, **record))
    entry.status = "READING_FINISHED"
    entry.report = {
        "toolchain": output["toolchain"],
        "elapsed_seconds": output["elapsed_seconds"],
        "peak_rss_bytes": output["peak_rss_bytes"],
        "units": len(output["units"]),
    }
    db.flush()
    write_source_markdown(db, manifest, entry)
    return {
        "file_id": entry.id,
        "unit_count": len(output["units"]),
        "inventory_hash": content_hash(output["inventory"]),
        "report": entry.report,
    }


def inventory_run(db, run):
    manifest = db.get(SourceManifest, run.input_snapshot["manifest_id"])
    if not manifest or manifest.input_hash != run.input_snapshot["manifest_hash"]:
        raise ReviewError("VERSION_CONFLICT", "来源清单快照不匹配")
    if (
        content_hash(manifest.config) != manifest.input_hash
        or manifest.config.get("reader_version") != PIPELINE_VERSION
    ):
        raise ReviewError("VERSION_CONFLICT", "来源配置或读取器版本不匹配，须新建读取记录")
    manifest.status = "READING"
    db.commit()
    files = list(db.scalars(select(SourceManifestFile).where(SourceManifestFile.manifest_id == manifest.id)))
    expected_files = set(manifest.config["asset_ids"])
    if {f.asset_id for f in files} != expected_files:
        raise ReviewError("MANIFEST_FILES_MISSING", "来源清单文件不齐，未开始读取")
    for entry in files:
        if entry.status == "READING_FINISHED":
            continue
        try:
            stage(
                db,
                run,
                f"inventory_header:{entry.id}",
                lambda entry=entry: preflight_file(db, manifest, entry),
            )
        except ReviewError as exc:
            if exc.code == "CANCELED":
                raise
            db.rollback()
            entry = db.get(SourceManifestFile, entry.id)
            entry.status = "BLOCKED"
            entry.report = {"issues": [issue(exc.code, str(exc))]}
            db.commit()
    if (
        sum(f.inventory.get("independent_page_count", 0) for f in files)
        > manifest.config["policy"]["p0"]["max_project_pdf_pages"]
    ):
        raise ReviewError("PROJECT_CAPACITY_EXCEEDED", "项目PDF页数超过已配置预算，未开始区域读取")
    for entry in files:
        if entry.status in ("READING_FINISHED", "BLOCKED"):
            continue
        if run.cancel_requested:
            raise ReviewError("CANCELED", "已取消来源读取")
        entry.status = "READING"
        db.commit()
        try:
            stage(db, run, f"inventory:{entry.id}", lambda entry=entry: inspect_file(db, manifest, entry))
        except ReviewError as exc:
            if exc.code == "CANCELED":
                raise
            db.rollback()
            entry = db.get(SourceManifestFile, entry.id)
            entry.status = "BLOCKED"
            entry.report = {"issues": [issue(exc.code, str(exc))]}
            db.commit()
    manifest = db.get(SourceManifest, manifest.id)
    manifest.report = manifest_report(db, manifest)
    manifest.status = "READING_FINISHED" if manifest.report["processing_complete"] else "BLOCKED"
    db.commit()
    return {"manifest_id": manifest.id, **manifest.report}


def file_report(db, entry):
    units = list(
        db.execute(
            select(DocumentUnit.unit_key, DocumentUnit.parent_key, DocumentUnit.state).where(
                DocumentUnit.file_id == entry.id
            )
        )
    )
    expected = set(entry.inventory.get("expected_root_keys", []))
    roots = [u.unit_key for u in units if u.parent_key is None]
    detected_expected = set(entry.inventory.get("detected_unit_keys", []))
    actual_keys = {u.unit_key for u in units}
    missing_detected = sorted(detected_expected - actual_keys)
    unexpected_detected = sorted(actual_keys - detected_expected)
    missing = sorted(expected - set(roots))
    unexpected = sorted(set(roots) - expected)
    completed = (
        entry.status == "READING_FINISHED"
        and bool(expected)
        and not missing
        and not unexpected
        and len(roots) == len(set(roots))
        and not missing_detected
        and not unexpected_detected
    )
    issues = [*entry.report.get("issues", [])]
    if missing_detected:
        issues.append(issue("DETECTED_UNITS_MISSING", f"缺少{len(missing_detected)}个已登记区域/结构单元"))
    if unexpected_detected:
        issues.append(issue("DETECTED_UNITS_UNEXPECTED", "出现未登记处理单元"))
    if missing:
        issues.append(issue("ROOT_UNITS_MISSING", f"缺少{len(missing)}个原文主单元"))
    if unexpected:
        issues.append(issue("ROOT_UNITS_UNEXPECTED", "出现未登记原文主单元"))
    gaps = sum(u.state in ["NEEDS_HUMAN", "BLOCKED"] for u in units)
    page_count = entry.inventory.get("independent_page_count")
    progress = None
    progress_path = local_path(f"integrity/{entry.manifest_id}/{entry.id}/progress.json")
    if entry.status == "READING" and progress_path.is_file():
        try:
            progress = json.loads(progress_path.read_text())
        except (ValueError, OSError):
            progress = None
    return {
        "processing_complete": completed,
        "expected_roots": len(expected),
        "processed_roots": len(roots),
        "missing_roots": missing,
        "missing_detected_units": missing_detected,
        "unexpected_detected_units": unexpected_detected,
        "unexpected_roots": unexpected,
        "units": len(units),
        "candidate_units": len(units) - gaps,
        "needs_human_units": gaps,
        "pages": page_count,
        "reading_status": "BLOCKED"
        if issues or any(u.state == "BLOCKED" for u in units)
        else "NEEDS_HUMAN"
        if gaps
        else "CANDIDATE",
        "issues": issues,
        "scan_progress": progress,
        "markdown": entry.report.get("markdown"),
        "validation_rounds": [
            {
                "id": "structure",
                "label": "结构对账",
                "status": "ALIGNED"
                if completed
                else "BLOCKED"
                if entry.status in ("BLOCKED", "READING_FINISHED")
                else "PENDING",
            },
            {
                "id": "reading",
                "label": "原生/OCR与区域复读",
                "status": "BLOCKED"
                if issues or any(u.state == "BLOCKED" for u in units)
                else "NEEDS_HUMAN"
                if gaps
                else "CANDIDATE"
                if completed
                else "PENDING",
                "unresolved_units": gaps,
            },
            {
                "id": "markdown",
                "label": "MD归档回查",
                "status": "ALIGNED"
                if completed and entry.report.get("markdown", {}).get("audit", {}).get("status") == "ALIGNED"
                else "BLOCKED"
                if entry.report.get("markdown")
                else "PENDING",
                "scope": "generation-time archive reconciliation",
            },
        ],
        "semantic_audit": "NOT_STARTED",
        "human_review": "NOT_RECORDED",
        "production_ready": False,
        "toolchain": entry.report.get("toolchain"),
        "elapsed_seconds": entry.report.get("elapsed_seconds"),
    }


def manifest_report(db, manifest):
    files = list(db.scalars(select(SourceManifestFile).where(SourceManifestFile.manifest_id == manifest.id)))
    reports = [file_report(db, f) for f in files]
    registered = {f.asset_id for f in files}
    expected = set(manifest.config["asset_ids"])
    pages = sum(r["pages"] or 0 for r in reports)
    capacity_exceeded = pages > manifest.config["policy"]["p0"]["max_project_pdf_pages"]
    complete = (
        expected == registered
        and bool(reports)
        and all(r["processing_complete"] for r in reports)
        and not capacity_exceeded
    )
    return {
        "processing_complete": complete,
        "file_count": len(files),
        "expected_file_count": len(expected),
        "missing_asset_ids": sorted(expected - registered),
        "unexpected_asset_ids": sorted(registered - expected),
        "issues": (
            [issue("MANIFEST_FILES_MISSING", "已登记清单有文件记录缺失，处理对账阻塞")]
            if expected - registered
            else []
        )
        + (
            [issue("MANIFEST_FILES_UNEXPECTED", "出现未登记文件记录，处理对账阻塞")]
            if registered - expected
            else []
        ),
        "completed_files": sum(r["processing_complete"] for r in reports),
        "expected_roots": sum(r["expected_roots"] for r in reports),
        "processed_roots": sum(r["processed_roots"] for r in reports),
        "pages": pages,
        "needs_human_units": sum(r["needs_human_units"] for r in reports),
        "scope_declared_complete": False,
        "semantic_audit": "NOT_STARTED",
        "human_approval": "NOT_RECORDED",
        "production_ready": False,
        "pipeline_version": PIPELINE_VERSION,
        "limits": [
            "G0/G1读取预览，不是规则提取完整性或生产批准",
            "用户尚未声明材料集合完整；尚未完成独立人工金标准标注和语义审计",
            *(["项目页数超过预算"] if capacity_exceeded else []),
        ],
    }
