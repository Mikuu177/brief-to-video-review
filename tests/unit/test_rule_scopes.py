import pytest
from app.services.aggregate import applicable, validate_rule_scopes
from conftest import rule_for
from fastapi import HTTPException


def test_explicit_phase_aliases_preserve_campaign_and_material_boundaries():
    rule = rule_for("source")
    rule["scope"]["phases"] = ["上市种草期"]
    validate_rule_scopes([rule], "launch")
    assert applicable(rule, "xiaohongshu", "launch", "video")
    assert not applicable(rule, "xiaohongshu", "preheat", "video")
    assert not applicable(rule, "douyin", "launch", "video")
    rule["scope"]["phases"] = ["预热期"]
    assert applicable(rule, "xiaohongshu", "preheat", "video")
    assert not applicable(rule, "xiaohongshu", "launch", "video")
    rule["scope"]["asset_kinds"] = ["image_post"]
    assert not applicable(rule, "xiaohongshu", "preheat", "video")


@pytest.mark.parametrize("phase", ["phase_0", "all", "", "上市种草期-方向一"])
def test_unrecognized_auditable_phase_requires_explicit_resolution(phase):
    rule = rule_for("source")
    rule["scope"]["phases"] = [phase]
    with pytest.raises(HTTPException) as error:
        validate_rule_scopes([rule], "launch")
    assert error.value.detail["code"] == "RULE_SCOPE_UNRESOLVED"
    assert error.value.detail["details"]["rule_id"] == rule["id"]


def test_custom_project_phase_and_reference_facts_are_not_reinterpreted():
    rule = rule_for("source")
    rule["scope"]["phases"] = ["custom_campaign_phase"]
    validate_rule_scopes([rule], "custom_campaign_phase")
    assert applicable(rule, "xiaohongshu", "custom_campaign_phase", "video")
    rule["type"] = "fact"
    validate_rule_scopes([rule], "launch")
    assert not applicable(rule, "xiaohongshu", "launch", "video")
    rule["type"] = "must"
    rule["confirmation_status"] = "excluded"
    validate_rule_scopes([rule], "launch")


def test_unknown_platform_is_not_silently_excluded():
    rule = rule_for("source")
    rule["scope"]["platforms"] = ["小红书"]
    with pytest.raises(HTTPException) as error:
        validate_rule_scopes([rule], "launch")
    assert error.value.detail["code"] == "RULE_SCOPE_UNRESOLVED"
