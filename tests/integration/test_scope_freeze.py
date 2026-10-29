from uuid import uuid4

from conftest import rule_for, upload_text


def test_unresolved_phase_blocks_freeze_then_explicit_alias_can_freeze(environment, project):
    client, _ = environment
    source = upload_text(client, project["id"])
    draft = client.post(
        f"/api/v1/projects/{project['id']}/rulesets/manual",
        json={"source_ids": [source["asset"]["id"]], "idempotency_key": str(uuid4())},
    ).json()
    rule = rule_for(source["blocks"][0]["id"])
    rule["scope"]["phases"] = ["phase_0"]

    def patch(revision):
        return client.patch(
            f"/api/v1/rulesets/{draft['id']}",
            json={"expected_revision": revision, "rules": [rule], "conflicts": []},
        ).json()

    edited = patch(draft["revision"])
    covered = client.post(
        f"/api/v1/rulesets/{draft['id']}/manual-coverage",
        json={"expected_revision": edited["revision"], "note": "隔离测试仅有单行文本，已人工核对全部来源。"},
    ).json()

    def freeze(revision):
        return client.post(
            f"/api/v1/rulesets/{draft['id']}/confirm",
            json={"expected_revision": revision, "kind": "experimental", "note": "范围保护隔离测试。"},
        )

    rejected = freeze(covered["revision"])
    assert rejected.status_code == 422
    assert rejected.json()["code"] == "RULE_SCOPE_UNRESOLVED"
    rule["scope"]["phases"] = ["预热期"]
    fixed = patch(covered["revision"])
    accepted = freeze(fixed["revision"])
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["rules"][0]["scope"]["phases"] == ["预热期"]
