import re

from app.errors import ReviewError, fail
from app.models import Asset, Project, RuleSet, SourceBlock
from app.services.aggregate import validate_rule_scopes
from sqlalchemy import select


def normalize(text: str) -> str:
    return re.sub(r"\s+", "", text)


def extraction_target_current(ruleset, run):
    return (
        ruleset is not None
        and ruleset.status == "DRAFT"
        and ruleset.revision == run.input_snapshot.get("ruleset_revision")
    )


def extraction_target(db, run, lock=False):
    query = select(RuleSet).where(RuleSet.id == run.ruleset_id).execution_options(populate_existing=True)
    ruleset = db.scalar(query.with_for_update() if lock else query)
    if not extraction_target_current(ruleset, run):
        raise ReviewError("VERSION_CONFLICT", "规范已被人工修改或冻结，旧提取不得覆盖，请创建新的提取草案")
    return ruleset


def source_limits(ruleset):
    return [
        f"Brief {entry['unit']}：" + "；".join(entry.get("limitations", []))
        for entry in ruleset.extraction_coverage
        if entry["status"] == "partial"
    ]


def validate_sources(db, ruleset, accept_partial_sources=False):
    rules = ruleset.rules
    if not rules:
        fail("INPUT_MISSING", "至少需要一条经过确认的规则")
    validate_rule_scopes(rules, db.get(Project, ruleset.project_id).phase)
    ids = [rule["id"] for rule in rules]
    if len(ids) != len(set(ids)):
        fail("INVALID_RULES", "规则 ID 重复")
    blocks = {
        b.id: b for b in db.scalars(select(SourceBlock).where(SourceBlock.asset_id.in_(ruleset.source_ids)))
    }
    for rule in rules:
        if rule["confirmation_status"] == "pending":
            fail("RULESET_UNCONFIRMED", "仍有待确认规则", rule_id=rule["id"])
        if rule["confirmation_status"] == "excluded" and not rule["confirmation_note"].strip():
            fail("RULESET_UNCONFIRMED", "排除规则需要说明理由", rule_id=rule["id"])
        for ref in rule["source_refs"]:
            source = blocks.get(ref["block_id"])
            if (
                source is None
                or not normalize(ref["quote"])
                or normalize(ref["quote"]) not in normalize(source.text)
            ):
                fail("INVALID_CITATION", "引文无法在来源中逐字匹配", rule_id=rule["id"])
    for conflict in ruleset.conflicts:
        if not set(conflict["rule_ids"]).issubset(set(ids)):
            fail("INVALID_RULES", "冲突关系包含不存在的规则")
        if conflict["blocking"] and (conflict["status"] != "resolved" or not conflict["resolution"].strip()):
            fail("RULESET_UNCONFIRMED", "仍有未裁定的关键冲突", conflict_id=conflict["id"])
    for asset_id in ruleset.source_ids:
        asset = db.get(Asset, asset_id)
        acknowledged = asset.metadata_json.get("human_coverage_ack")
        if asset.parse_status not in ("COMPLETED", "PARTIAL"):
            fail("PARSE_INCOMPLETE", "来源文件解析未完成")
        allowed = ("success", "blank", "partial") if accept_partial_sources else ("success", "blank")
        if not acknowledged and any(
            p["status"] not in allowed or p.get("visual_review") == "pending" for p in asset.coverage
        ):
            fail(
                "PARSE_INCOMPLETE",
                "图片、扫描页或布局尚未核对，请完成视觉解析或人工核对说明",
                asset_id=asset.id,
            )
    expected = {
        (asset.id, entry.get("page", entry.get("sheet", "document")))
        for asset in db.scalars(select(Asset).where(Asset.id.in_(ruleset.source_ids)))
        for entry in asset.coverage
    }
    processed = {
        (x["asset_id"], x["unit"])
        for x in ruleset.extraction_coverage
        if x["status"] == "success"
        or (accept_partial_sources and x["status"] == "partial" and x.get("limitations"))
    }
    if expected - processed:
        fail("PARSE_INCOMPLETE", "规范提取记录未覆盖所有页/工作表")


def validate_model_rules(rules: list[dict], source_blocks: list):
    texts = {b.id: normalize(b.text) for b in source_blocks}
    for rule in rules:
        for ref in rule["source_refs"]:
            if (
                ref["block_id"] not in texts
                or not normalize(ref["quote"])
                or normalize(ref["quote"]) not in texts[ref["block_id"]]
            ):
                raise ReviewError("MODEL_RESPONSE_INVALID", "模型规则引文无法回源，拒绝使用该草案")
