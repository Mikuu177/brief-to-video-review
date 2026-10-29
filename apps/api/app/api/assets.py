from pathlib import Path

import fitz
from app.api.auth import current_user, project_access
from app.db import get_db
from app.errors import ReviewError, fail
from app.models import Asset, ReviewRun, SourceBlock, User
from app.services.runs import enqueue
from app.services.script_normalization import normalize_script
from app.services.storage import ensure_local, local_path, save_upload
from app.services.versioning import content_hash
from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

router = APIRouter()


def asset_access(db, asset_id, user):
    asset = db.get(Asset, asset_id)
    if asset is None:
        fail("NOT_FOUND", "素材不存在", 404)
    project_access(db, asset.project_id, user)
    return asset


def asset_dict(asset):
    return {
        "id": asset.id,
        "project_id": asset.project_id,
        "kind": asset.kind,
        "filename": asset.filename,
        "mime": asset.mime,
        "sha256": asset.sha256,
        "bytes": asset.bytes,
        "parse_status": asset.parse_status,
        "coverage": asset.coverage,
        "metadata": {k: v for k, v in asset.metadata_json.items() if k != "raw_probe"},
        "created_at": asset.created_at,
    }


def store_asset(db, project_id, filename, kind, stream):
    try:
        stored = save_upload(stream, filename, kind)
    except ReviewError as exc:
        fail(exc.code, str(exc))
    asset = Asset(project_id=project_id, filename=Path(filename).name, kind=kind, **stored)
    db.add(asset)
    db.commit()
    return asset


@router.post("/projects/{project_id}/assets", status_code=201)
def upload(
    project_id: str,
    kind: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    project_access(db, project_id, user)
    asset = store_asset(db, project_id, file.filename or "file", kind, file.file)
    return asset_dict(asset)


@router.get("/assets/{asset_id}/content")
def content(asset_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    asset = asset_access(db, asset_id, user)
    return FileResponse(
        ensure_local(asset.storage_key),
        media_type=asset.mime,
        filename=asset.filename,
        content_disposition_type="inline",
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/assets/{asset_id}/preview/{page}")
def preview(asset_id: str, page: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    asset = asset_access(db, asset_id, user)
    if asset.mime != "application/pdf":
        fail("INVALID_FILE_TYPE", "仅 PDF 支持逐页预览")
    with fitz.open(ensure_local(asset.storage_key)) as document:
        if not 1 <= page <= len(document):
            fail("NOT_FOUND", "页码不存在", 404)
        data = document[page - 1].get_pixmap(matrix=fitz.Matrix(1.5, 1.5)).tobytes("png")
    return Response(data, media_type="image/png", headers={"Cache-Control": "private, no-store"})


@router.get("/assets/{asset_id}/embedded/{block_id}")
def embedded(asset_id: str, block_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    asset_access(db, asset_id, user)
    source = db.get(SourceBlock, block_id)
    if not source or source.asset_id != asset_id or not source.locator.get("image_key"):
        fail("NOT_FOUND", "图片不存在", 404)
    return FileResponse(
        local_path(source.locator["image_key"]), headers={"Cache-Control": "private, no-store"}
    )


@router.post("/assets/{asset_id}/parse", status_code=202)
def parse(asset_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    asset = asset_access(db, asset_id, user)
    project_access(db, asset.project_id, user, lock=True)
    db.refresh(asset)
    if asset.parse_status == "COMPLETED":
        return {"asset_id": asset.id, "status": "COMPLETED"}
    pending = db.scalar(
        select(ReviewRun).where(
            ReviewRun.project_id == asset.project_id, ReviewRun.idempotency_key == f"parse-{asset.id}"
        )
    )
    if pending:
        return {"run_id": pending.id, "status": pending.status}
    snapshot = {"asset_id": asset.id, "sha256": asset.sha256}
    run = ReviewRun(
        project_id=asset.project_id,
        kind="parse",
        input_snapshot=snapshot,
        input_hash=content_hash(snapshot),
        idempotency_key=f"parse-{asset.id}",
    )
    db.add(run)
    asset.parse_status = "QUEUED"
    db.commit()
    try:
        enqueue(run.id)
    except Exception:
        run.error = {"code": "QUEUE_UNAVAILABLE", "message": "文件已保存，等待任务队列恢复"}
        db.commit()
    return {"run_id": run.id, "status": run.status}


@router.get("/assets/{asset_id}/blocks")
def blocks(asset_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    asset = asset_access(db, asset_id, user)
    rows = list(
        db.scalars(select(SourceBlock).where(SourceBlock.asset_id == asset.id).order_by(SourceBlock.position))
    )
    result = [
        {
            "id": b.id,
            "text": b.text,
            "locator": b.locator,
            "extraction_method": b.extraction_method,
            "coverage_status": b.coverage_status,
        }
        for b in rows
    ]
    return {
        "asset": asset_dict(asset),
        "blocks": result,
        "coverage": asset.coverage,
        "script_structure": normalize_script(rows) if asset.kind == "script" else None,
    }


class CoverageAck(BaseModel):
    note: str = Field(min_length=10, max_length=3000)


@router.post("/assets/{asset_id}/coverage-ack")
def acknowledge(
    asset_id: str, body: CoverageAck, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    asset = asset_access(db, asset_id, user)
    asset.metadata_json = {
        **asset.metadata_json,
        "human_coverage_ack": {"actor": user.username, "note": body.note},
    }
    db.commit()
    return asset_dict(asset)
