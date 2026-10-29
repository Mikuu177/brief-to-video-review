from typing import Literal

from app.api.auth import current_user, project_access
from app.db import get_db
from app.errors import fail
from app.models import Asset, DocumentUnit, ReviewRun, SourceManifest, SourceManifestFile, User
from app.services.document_inventory import file_report, manifest_report, policy
from app.services.document_reader import PIPELINE_VERSION
from app.services.runs import enqueue
from app.services.source_markdown import write_source_markdown
from app.services.storage import local_path
from app.services.versioning import content_hash
from app.settings import settings
from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

router = APIRouter()


class ManifestEntry(BaseModel):
    asset_id: str
    role: Literal["primary", "supplement", "reference"]
    platform: Literal["xiaohongshu", "douyin", "bilibili", "other", "unconfirmed"]


class ManifestInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    entries: list[ManifestEntry] = Field(min_length=1, max_length=50)
    idempotency_key: str = Field(min_length=1, max_length=100)


def manifest_access(db, manifest_id, user):
    manifest = db.get(SourceManifest, manifest_id)
    if not manifest:
        fail("NOT_FOUND", "来源清单不存在", 404)
    project_access(db, manifest.project_id, user)
    return manifest


def manifest_status(db, manifest):
    run = db.get(ReviewRun, manifest.run_id) if manifest.run_id else None
    if run and run.status in ("INCOMPLETE", "CANCELED"):
        return "FAILED"
    return manifest.status


def manifest_dict(db, manifest):
    files = list(
        db.scalars(
            select(SourceManifestFile)
            .where(SourceManifestFile.manifest_id == manifest.id)
            .order_by(SourceManifestFile.created_at)
        )
    )
    return {
        "id": manifest.id,
        "project_id": manifest.project_id,
        "name": manifest.name,
        "status": manifest_status(db, manifest),
        "created_at": manifest.created_at,
        "run_id": manifest.run_id,
        "input_hash": manifest.input_hash,
        "report": manifest_report(db, manifest),
        "files": [
            {
                "id": f.id,
                "asset_id": f.asset_id,
                "manifest_id": manifest.id,
                "project_id": f.project_id,
                "filename": db.get(Asset, f.asset_id).filename,
                "source_sha256": f.source_sha256,
                "role": f.role,
                "platform": f.platform,
                "status": f.status,
                "inventory": {
                    k: v
                    for k, v in f.inventory.items()
                    if k
                    in [
                        "kind",
                        "native_page_count",
                        "independent_page_count",
                        "archive_members",
                        "physical_pages",
                    ]
                },
                "report": file_report(db, f),
            }
            for f in files
        ],
    }


@router.get("/projects/{project_id}/source-manifests")
def list_manifests(project_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    project_access(db, project_id, user)
    rows = db.scalars(
        select(SourceManifest)
        .where(SourceManifest.project_id == project_id)
        .order_by(SourceManifest.created_at.desc())
        .limit(20)
    )
    return {
        "enabled": settings.source_integrity_enabled,
        "policy_version": policy()["version"],
        "items": [
            {"id": m.id, "name": m.name, "status": manifest_status(db, m), "created_at": m.created_at}
            for m in rows
        ],
    }


@router.post("/projects/{project_id}/source-manifests", status_code=202)
def create_manifest(
    project_id: str, body: ManifestInput, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    project_access(db, project_id, user, lock=True)
    if not settings.source_integrity_enabled:
        fail("INTEGRITY_PREVIEW_DISABLED", "来源读取预览未启用", 409)
    entries = [e.model_dump() for e in body.entries]
    ids = [e["asset_id"] for e in entries]
    if len(ids) != len(set(ids)):
        fail("DUPLICATE_SOURCE", "来源文件重复")
    existing = db.scalar(
        select(SourceManifest).where(
            SourceManifest.project_id == project_id, SourceManifest.idempotency_key == body.idempotency_key
        )
    )
    if existing:
        if existing.config["entries"] != entries or existing.name != body.name:
            fail("IDEMPOTENCY_CONFLICT", "相同提交键绑定了不同清单", 409)
        return {"manifest_id": existing.id, "run_id": existing.run_id}
    assets = []
    for entry in entries:
        asset = db.get(Asset, entry["asset_id"])
        if not asset or asset.project_id != project_id or asset.kind != "brief":
            fail("ASSET_MISMATCH", "来源必须是当前项目Brief")
        assets.append(asset)
    current_policy = policy()
    config = {
        "reader_version": PIPELINE_VERSION,
        "policy": current_policy,
        "entries": entries,
        "asset_ids": ids,
        "sources": {a.id: a.sha256 for a in assets},
        "policy_hash": content_hash(current_policy),
    }
    manifest = SourceManifest(
        project_id=project_id,
        created_by=user.id,
        name=body.name,
        idempotency_key=body.idempotency_key,
        input_hash=content_hash(config),
        config=config,
    )
    db.add(manifest)
    db.flush()
    for entry, asset in zip(entries, assets):
        db.add(
            SourceManifestFile(
                manifest_id=manifest.id,
                project_id=project_id,
                asset_id=asset.id,
                source_sha256=asset.sha256,
                role=entry["role"],
                platform=entry["platform"],
            )
        )
    run = ReviewRun(
        project_id=project_id,
        kind="source_reading",
        input_snapshot={"manifest_id": manifest.id, "manifest_hash": manifest.input_hash},
        input_hash=manifest.input_hash,
        idempotency_key=f"source-{manifest.id}",
    )
    db.add(run)
    db.flush()
    manifest.run_id = run.id
    db.commit()
    try:
        enqueue(run.id)
    except Exception:
        run.error = {"code": "QUEUE_UNAVAILABLE", "message": "来源清单已保存，等待队列恢复"}
        db.commit()
    return {"manifest_id": manifest.id, "run_id": run.id}


@router.get("/source-manifests/{manifest_id}")
def detail(manifest_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return manifest_dict(db, manifest_access(db, manifest_id, user))


@router.get("/source-manifests/{manifest_id}/units")
def list_units(
    manifest_id: str,
    file_id: str,
    parent_key: str | None = None,
    roots_only: bool = False,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    manifest_access(db, manifest_id, user)
    entry = db.get(SourceManifestFile, file_id)
    if not entry or entry.manifest_id != manifest_id:
        fail("NOT_FOUND", "文件不属于此清单", 404)
    query = select(DocumentUnit).where(DocumentUnit.file_id == file_id)
    if roots_only:
        query = query.where(DocumentUnit.parent_key.is_(None))
    if parent_key is not None:
        query = query.where(DocumentUnit.parent_key == parent_key)
    rows = list(db.scalars(query.order_by(DocumentUnit.position).offset(offset).limit(limit + 1)))
    return {
        "items": [unit_dict(u) for u in rows[:limit]],
        "has_more": len(rows) > limit,
        "offset": offset,
        "limit": limit,
    }


def unit_dict(u):
    result = {
        key: getattr(u, key)
        for key in [
            "id",
            "file_id",
            "unit_key",
            "parent_key",
            "position",
            "kind",
            "locator",
            "native_text",
            "ocr_text",
            "content_hash",
            "state",
            "issues",
            "features",
            "artifacts",
        ]
    }

    result["spans"] = [
        {
            "span_id": f"{u.id}:{channel}:0:{len(text)}",
            "unit_id": u.id,
            "channel": channel,
            "start": 0,
            "end": len(text),
            "unit_hash": u.content_hash,
        }
        for channel, text in [("native", u.native_text), ("ocr", u.ocr_text)]
        if text
    ]
    return result


@router.get("/source-manifests/{manifest_id}/units/{unit_id}/artifacts/{label}")
def artifact(
    manifest_id: str,
    unit_id: str,
    label: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    manifest_access(db, manifest_id, user)
    u = db.get(DocumentUnit, unit_id)
    entry = db.get(SourceManifestFile, u.file_id) if u else None
    if not entry or entry.manifest_id != manifest_id or label not in u.artifacts:
        fail("NOT_FOUND", "原文图像不存在", 404)
    path = local_path(f"integrity/{manifest_id}/{entry.id}/{u.artifacts[label]}")
    import hashlib

    if (
        not path.is_file()
        or hashlib.sha256(path.read_bytes()).hexdigest() != u.features["artifact_sha256"][label]
    ):
        fail("ARTIFACT_INTEGRITY_ERROR", "原文图像缺失或哈希改变", 409)
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "private, no-store"})


def markdown_entry(db, manifest_id, file_id, user, lock=False):
    manifest = manifest_access(db, manifest_id, user)
    query = select(SourceManifestFile).where(SourceManifestFile.id == file_id)
    if lock:
        query = query.with_for_update()
    entry = db.scalar(query)
    if not entry or entry.manifest_id != manifest.id:
        fail("NOT_FOUND", "文件不属于此来源清单", 404)
    if not file_report(db, entry)["processing_complete"]:
        fail("SOURCE_NOT_READY", "文件处理集合尚未对齐，不能导出为完整读取归档", 409)
    return manifest, entry


@router.post("/source-manifests/{manifest_id}/files/{file_id}/markdown")
def create_markdown(
    manifest_id: str, file_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    manifest, entry = markdown_entry(db, manifest_id, file_id, user, lock=True)
    metadata = write_source_markdown(db, manifest, entry)
    db.commit()
    return metadata


@router.get("/source-manifests/{manifest_id}/files/{file_id}/markdown")
def download_markdown(
    manifest_id: str, file_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    _, entry = markdown_entry(db, manifest_id, file_id, user)
    metadata = entry.report.get("markdown")
    path = local_path(f"integrity/{manifest_id}/{file_id}/source.md")
    import hashlib

    if not metadata or not path.is_file():
        fail("MARKDOWN_NOT_GENERATED", "读取MD尚未生成", 404)
    if hashlib.sha256(path.read_bytes()).hexdigest() != metadata["sha256"]:
        fail("MARKDOWN_INTEGRITY_ERROR", "读取MD哈希改变，拒绝导出", 409)
    asset = db.get(Asset, entry.asset_id)
    return FileResponse(
        path,
        media_type="text/markdown; charset=utf-8",
        filename=f"{asset.filename.rsplit('.', 1)[0]}-来源读取.md",
        headers={"Cache-Control": "private, no-store"},
    )
