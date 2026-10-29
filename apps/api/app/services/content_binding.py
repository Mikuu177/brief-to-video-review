"""Bind a review to immutable, same-video content candidates without promoting claims."""

import hashlib

from app.errors import ReviewError
from app.models import VideoContentSnapshot
from app.services.storage import ensure_local
from app.services.versioning import content_hash
from app.services.video_content import content_report
from sqlalchemy import select


def bound_content(db, snapshot_id, asset, expected_hash=None):
    snapshot = db.get(VideoContentSnapshot, snapshot_id)
    if not snapshot or snapshot.project_id != asset.project_id or snapshot.asset_id != asset.id:
        raise ReviewError("CONTENT_SNAPSHOT_MISMATCH", "内容快照必须属于当前项目的同一原视频")
    if next_revision_id(db, snapshot.id):
        raise ReviewError("VERSION_CONFLICT", "此内容已有修正，请选择最新版本")
    if snapshot.source_sha256 != asset.sha256:
        raise ReviewError("SOURCE_HASH_CHANGED", "内容快照与提交原视频SHA不一致")
    if not snapshot.content_hash or not content_report(snapshot.payload)["processing_complete"]:
        raise ReviewError("CONTENT_INCOMPLETE", "内容读取尚未完成，不能接入合规审核")
    if content_hash(snapshot.payload) != snapshot.content_hash or (
        expected_hash and snapshot.content_hash != expected_hash
    ):
        raise ReviewError("VERSION_CONFLICT", "内容快照哈希不匹配")
    with ensure_local(asset.storage_key).open("rb") as source:
        if hashlib.file_digest(source, "sha256").hexdigest() != asset.sha256:
            raise ReviewError("SOURCE_HASH_CHANGED", "原视频文件哈希已变化")
    return snapshot


def content_evidence(snapshot):
    """Absence candidates and interpretations are deliberately not positive observations."""
    payload = snapshot.payload
    facts = []
    for event in payload["events"]:
        if event["status"] == "NOT_OBSERVED":
            continue
        facts.append(
            {
                "channel": "unclassified_audio" if event["channel"] == "sound_event" else event["channel"],
                "content": event["raw_content"],
                "start_ms": event["start_ms"],
                "end_ms": event["end_ms"],
                "uncertainty_ms": event.get("uncertainty_ms"),
                "origin": {
                    "source": "video_content_snapshot",
                    "snapshot_id": snapshot.id,
                    "content_hash": snapshot.content_hash,
                    "event_id": event["id"],
                    "candidate_status": event["status"],
                    "content_channel": event["channel"],
                    "speaker_id": event.get("speaker_id"),
                    "actor_id": event.get("actor_id"),
                    "object_id": event.get("object_id"),
                    "unit_ids": event.get("unit_ids", []),
                },
            }
        )
    return facts


def next_revision_id(db, snapshot_id):
    return db.scalar(
        select(VideoContentSnapshot.id).where(VideoContentSnapshot.parent_id == snapshot_id).limit(1)
    )
