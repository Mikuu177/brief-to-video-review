PHASE_ALIASES = {
    "上市种草期": "launch",
    "上市期": "launch",
    "预热期": "preheat",
}


def canonical_phase(phase: str) -> str:
    return PHASE_ALIASES.get(phase.strip(), phase.strip())


def validate_rule_scopes(rules: list[dict], project_phase: str):
    from app.errors import fail

    known_phases = {"launch", "preheat", canonical_phase(project_phase)}
    known_platforms = {"xiaohongshu", "douyin", "bilibili", "other", "unconfirmed"}
    for rule in rules:
        if rule["type"] not in ("must", "should") or rule["confirmation_status"] == "excluded":
            continue
        scope = rule["scope"]
        if any(not p.strip() or canonical_phase(p) not in known_phases for p in scope["phases"]) or not set(
            scope["platforms"]
        ).issubset(known_platforms):
            fail(
                "RULE_SCOPE_UNRESOLVED",
                "审核规则存在未识别的平台或阶段，请明确范围后再冻结或审核",
                rule_id=rule["id"],
                scope=scope,
            )


def applicable(rule: dict, platform: str, phase: str, kind: str) -> bool:
    scope = rule["scope"]
    if rule["type"] not in ("must", "should") or rule["confirmation_status"] == "excluded":
        return False
    return (
        platform in scope["platforms"]
        and canonical_phase(phase) in {canonical_phase(p) for p in scope["phases"]}
        and kind in scope["asset_kinds"]
    )


def assessment(rules: list[dict], checks: list[dict], complete: bool, manual_required: bool = False) -> str:
    if (
        not complete
        or len(checks) != len(rules)
        or {c["rule_id"] for c in checks} != {r["id"] for r in rules}
    ):
        return "NOT_READY"
    by_id = {rule["id"]: rule for rule in rules}
    if any(c["status"] == "FAIL" and by_id[c["rule_id"]]["severity"] in ("blocker", "major") for c in checks):
        return "NEEDS_REVISION"
    if manual_required or any(c["status"] == "UNCERTAIN" for c in checks):
        return "NEEDS_HUMAN"
    if not rules:
        return "NEEDS_HUMAN"
    return "AI_PASSED"


def validate_checks(rules: list[dict], checks: list[dict], evidence_ids: set[str]):
    from app.errors import ReviewError

    expected = {r["id"] for r in rules}
    actual = [c["rule_id"] for c in checks]
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise ReviewError("MODEL_RESPONSE_INVALID", "模型结果缺少规则、包含重复规则或未知规则")
    for check in checks:
        if not set(check["evidence_ids"]).issubset(evidence_ids):
            raise ReviewError("MODEL_RESPONSE_INVALID", "证据 ID 不存在")
        if check["status"] == "NOT_APPLICABLE":
            check["status"] = "UNCERTAIN"
            check["limitations"].append(
                "后端范围认定本项适用；模型提出NOT_APPLICABLE，未排除规则，须人工核对。"
            )
        if check["status"] == "PASS" and not check["evidence_ids"]:
            check["status"] = "UNCERTAIN"
            check["limitations"].append("缺少支持通过的证据")
        if check["status"] == "FAIL" and not check["evidence_ids"]:
            check["status"] = "UNCERTAIN"
            check["limitations"].append("候选问题缺少可核对证据")
        rule = next(r for r in rules if r["id"] == check["rule_id"])
        if rule["predicate"]["manual_required"] or rule["predicate"]["kind"] == "human_judgment":
            check["status"] = "UNCERTAIN"
            check["limitations"].append("冻结规范指定人工必审")
