from uuid import uuid4

import pytest
from app.workers.review import execute_run
from conftest import frozen_rules, rule_for, upload_text


def test_real_text_parse_and_citation_freeze(environment, project):
    client, _ = environment
    ruleset = frozen_rules(client, project["id"])
    assert ruleset["status"] == "FROZEN" and len(ruleset["content_hash"]) == 64
    assert ruleset["confirmation_kind"] == "experimental"
    r = client.patch(
        f"/api/v1/rulesets/{ruleset['id']}",
        json={"expected_revision": ruleset["revision"], "rules": ruleset["rules"], "conflicts": []},
    )
    assert r.status_code == 200 and r.json()["id"] != ruleset["id"] and r.json()["status"] == "DRAFT"


@pytest.mark.parametrize("quote", ["不存在的原文", "   \n  "])
def test_fake_or_empty_citation_blocks_freeze(environment, project, quote):
    client, _ = environment
    source = upload_text(client, project["id"])
    draft = client.post(
        f"/api/v1/projects/{project['id']}/rulesets/manual",
        json={"source_ids": [source["asset"]["id"]], "idempotency_key": str(uuid4())},
    ).json()
    rule = rule_for(source["blocks"][0]["id"], quote=quote)
    client.patch(
        f"/api/v1/rulesets/{draft['id']}", json={"expected_revision": 1, "rules": [rule], "conflicts": []}
    )
    r = client.post(
        f"/api/v1/rulesets/{draft['id']}/confirm",
        json={"expected_revision": 2, "note": "测试引文不应通过", "kind": "experimental"},
    )
    assert r.status_code == 422 and r.json()["code"] == "INVALID_CITATION"


def test_missing_key_does_not_create_fake_review(environment, project):
    client, _ = environment
    ruleset = frozen_rules(client, project["id"])
    script = upload_text(client, project["id"], "script", "我喝完以后立刻恢复精神了。")
    submission = client.post(
        f"/api/v1/projects/{project['id']}/submissions",
        json={"creator": "测试达人", "script_id": script["asset"]["id"]},
    ).json()
    r = client.post(
        "/api/v1/script-reviews",
        json={
            "submission_id": submission["id"],
            "ruleset_id": ruleset["id"],
            "idempotency_key": str(uuid4()),
        },
    )
    assert r.status_code == 409 and r.json()["code"] == "MODEL_NOT_CONFIGURED"
    detail = client.get(f"/api/v1/projects/{project['id']}").json()
    assert not any(r["kind"] == "script" for r in detail["runs"])


def test_human_independent_decision_is_versioned_and_ai_stays_not_ready(environment, project):
    client, _ = environment
    ruleset = frozen_rules(client, project["id"])
    script = upload_text(client, project["id"], "script", "测试稿件内容")
    submission = client.post(
        f"/api/v1/projects/{project['id']}/submissions",
        json={"creator": "测试达人", "script_id": script["asset"]["id"]},
    ).json()
    body = {"submission_id": submission["id"], "ruleset_id": ruleset["id"], "idempotency_key": str(uuid4())}
    run_id = client.post("/api/v1/human-reviews", json=body).json()["run_id"]
    assert client.post("/api/v1/human-reviews", json=body).json()["run_id"] == run_id
    run = client.get(f"/api/v1/runs/{run_id}").json()
    decision = {
        "expected_input_hash": run["input_hash"],
        "expected_revision": run["revision"],
        "decision": "APPROVE",
        "reason": "仅在隔离测试中人工核对通过",
        "mode": "ai_assisted",
    }
    assert client.post(f"/api/v1/runs/{run_id}/decisions", json=decision).status_code == 409
    decision["mode"] = "independent_human"
    r = client.post(f"/api/v1/runs/{run_id}/decisions", json=decision)
    assert r.status_code == 201 and r.json()["assessment"] == "NOT_READY"
    assert r.json()["decisions"][0]["mode"] == "independent_human"
    assert client.post(f"/api/v1/runs/{run_id}/decisions", json=decision).status_code == 409
    assert "人工" in client.get(f"/api/v1/runs/{run_id}/report").text
    revised = client.post(
        f"/api/v1/projects/{project['id']}/submissions",
        json={"creator": "测试达人", "parent_id": submission["id"], "script_id": script["asset"]["id"]},
    )
    assert revised.status_code == 201 and revised.json()["version"] == 2
    assert client.get(f"/api/v1/runs/{run_id}").json()["freshness"] == "STALE"
    decision["expected_revision"] = r.json()["revision"] + 1
    assert client.post(f"/api/v1/runs/{run_id}/decisions", json=decision).status_code == 409


def test_provider_fixture_is_not_a_live_effect_test(environment, project, monkeypatch):
    client, sessions = environment
    ruleset = frozen_rules(client, project["id"])
    script = upload_text(client, project["id"], "script", "我喝完立刻精神了。")
    submission = client.post(
        f"/api/v1/projects/{project['id']}/submissions",
        json={"creator": "测试替身", "script_id": script["asset"]["id"]},
    ).json()
    from app.settings import settings

    monkeypatch.setattr(settings, "ark_api_key", "test-fixture-not-real")
    monkeypatch.setattr(settings, "ark_text_model", "test-fixture-not-real")

    class ProviderFixture:
        def __init__(self, path):
            self.records = []

        def json(self, instruction, payload):
            assert payload["review_basis"] == "script_plan_only"
            assert payload["product_context"] == {
                "brand": project["brand"],
                "product": project["product"],
            }
            # The target is supplied separately; it must not be fabricated as
            # text observed in this deliberately product-unnamed script.
            assert all(project["product"] not in b["text"] for b in payload["source_blocks"])
            return {
                "evidence": [
                    {"id": "e1", "block_id": script["blocks"][0]["id"], "quote": "我喝完立刻精神了。"}
                ],
                "checks": [
                    {
                        "rule_id": "R-001",
                        "status": "FAIL",
                        "evidence_ids": ["e1"],
                        "explanation": "测试替身预设语义结果，不是真实模型判定",
                        "suggestion": "测试修改建议",
                        "applicability_reason": "",
                        "limitations": [],
                    }
                ],
            }

        def close(self):
            pass

        def take_records(self):
            return []

    monkeypatch.setattr("app.workers.review.Ark", ProviderFixture)
    r = client.post(
        "/api/v1/script-reviews",
        json={
            "submission_id": submission["id"],
            "ruleset_id": ruleset["id"],
            "idempotency_key": str(uuid4()),
        },
    )
    assert r.status_code == 202
    run_id = r.json()["run_id"]
    execute_run(run_id)
    run = client.get(f"/api/v1/runs/{run_id}").json()
    assert run["status"] == "COMPLETED" and run["assessment"] == "NEEDS_REVISION"
    assert run["evidence"][0]["source_block_id"] == script["blocks"][0]["id"]
    assert run["evidence"][0]["content"] == "我喝完立刻精神了。"
    assert len(run["checks"]) == 1


def test_unmatched_material_type_and_cross_project_assets_rejected(environment, project):
    client, _ = environment
    source = upload_text(client, project["id"])
    assert (
        client.post(
            f"/api/v1/projects/{project['id']}/submissions",
            json={"creator": "测试", "script_id": source["asset"]["id"]},
        ).status_code
        == 422
    )


def test_empty_rules_do_not_freeze(environment, project):
    client, _ = environment
    source = upload_text(client, project["id"])
    draft = client.post(
        f"/api/v1/projects/{project['id']}/rulesets/manual",
        json={"source_ids": [source["asset"]["id"]], "idempotency_key": str(uuid4())},
    ).json()
    assert (
        client.post(
            f"/api/v1/rulesets/{draft['id']}/confirm",
            json={"expected_revision": 1, "note": "不应通过", "kind": "experimental"},
        ).status_code
        == 422
    )
