from copy import deepcopy
from types import SimpleNamespace

import pytest
from app.errors import ReviewError
from app.workers.review import grounded_rules
from conftest import rule_for


class ProviderFixture:
    """Protocol fixture only; does not claim model accuracy."""

    def __init__(self, response):
        self.response = response
        self.calls = []

    def json(self, instruction, payload, **kwargs):
        self.calls.append(payload)
        return self.response


def test_valid_citations_do_not_trigger_another_model_call():
    rules = [rule_for("b1", quote="Product name")]
    blocks = [SimpleNamespace(id="b1", text="Product name must appear.", locator={})]
    provider = ProviderFixture({})
    result, attempted = grounded_rules(provider, rules, blocks)
    assert result == rules and not attempted and not provider.calls


def test_split_quote_repair_preserves_every_non_citation_field():
    rules = [rule_for("b1", quote="Product name must appear.")]
    original = deepcopy(rules)
    blocks = [
        SimpleNamespace(id="b1", text="Product name", locator={}),
        SimpleNamespace(id="b2", text="must appear.", locator={}),
    ]
    provider = ProviderFixture(
        {
            "citations": [
                {
                    "rule_id": rules[0]["id"],
                    "source_refs": [
                        {"block_id": "b1", "quote": "Product name"},
                        {"block_id": "b2", "quote": "must appear."},
                    ],
                }
            ]
        }
    )
    result, attempted = grounded_rules(provider, rules, blocks)
    assert attempted and len(provider.calls) == 1 and rules == original
    assert len(result[0]["source_refs"]) == 2
    assert {k: v for k, v in result[0].items() if k != "source_refs"} == {
        k: v for k, v in original[0].items() if k != "source_refs"
    }


@pytest.mark.parametrize(
    "patches",
    [
        [],
        [{"rule_id": "R-001", "source_refs": []}],
        [{"rule_id": "unknown-rule", "source_refs": [{"block_id": "b1", "quote": "Product"}]}],
        [
            {
                "rule_id": "R-001",
                "source_refs": [{"block_id": "b1", "quote": "Product"}],
                "severity": "advisory",
            }
        ],
        [{"rule_id": "R-001", "source_refs": [{"block_id": "b1", "quote": "invented"}]}],
    ],
)
def test_incomplete_changed_or_still_ungrounded_repair_remains_rejected(patches):
    rules = [rule_for("b1", quote="Ungrounded original quote")]
    blocks = [SimpleNamespace(id="b1", text="Product name must appear.", locator={})]
    provider = ProviderFixture({"citations": patches})
    with pytest.raises(ReviewError):
        grounded_rules(provider, rules, blocks)
    assert len(provider.calls) == 1


def test_unknown_transport_during_repair_is_not_reissued():
    rules = [rule_for("b1", quote="Ungrounded original quote")]
    blocks = [SimpleNamespace(id="b1", text="Product name must appear.", locator={})]

    class InterruptedProvider(ProviderFixture):
        def json(self, instruction, payload, **kwargs):
            self.calls.append(payload)
            raise ReviewError("PROVIDER_TIMEOUT", "Unknown remote state", unknown_remote=True)

    provider = InterruptedProvider({})
    with pytest.raises(ReviewError) as error:
        grounded_rules(provider, rules, blocks)
    assert error.value.unknown_remote and len(provider.calls) == 1
