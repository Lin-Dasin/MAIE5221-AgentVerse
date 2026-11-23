"""Exercise the S3 proposal check with auditable JSON and Markdown artifacts."""

import json

import pytest

from harness.scenario_portfolio import ALLOCATION_ARTIFACT, PROPOSAL_ARTIFACT, check_portfolio

ALLOCATION = {
    "positions": [
        {"asset": "Fund A", "asset_class": "us_equity", "weight_bps": 6251, "amount_hkd": 625100},
        {
            "asset": "Fund B",
            "asset_class": "other_equity",
            "weight_bps": 1250,
            "amount_hkd": 125000,
        },
        {
            "asset": "Fund C",
            "asset_class": "fixed_income",
            "weight_bps": 1000,
            "amount_hkd": 100000,
        },
        {"asset": "Cash", "asset_class": "cash", "weight_bps": 1499, "amount_hkd": 149900},
    ],
    "stock_decline_bps": 3333,
}
PROPOSAL = """# Illustrative proposal

The investor can use the figures in the summary to understand the stress scenario.

<!-- allocation-summary:start -->

| Item | Weight | HKD |
| --- | ---: | ---: |
| US equity | 62.51% | 625,100 |
| Other equity | 12.5% | 125000 |
| Fixed income | 10 % | 100000 |
| Cash | 14.99% | HK$149,900 |
| Total equity | 75.01% | 750100 |
| Total reserve | 24.99% | HKD 249,900 |
| Total portfolio | **100%** | **1,000,000** |
| Stock decline | 33.33% | — |
| Stress loss | - | 250,008.33 |
| Stress remaining | - | 749,991.67 |

<!-- allocation-summary:end -->
"""


def _write(tmp_path, *, allocation=ALLOCATION, proposal=PROPOSAL):
    (tmp_path / "outputs").mkdir()
    (tmp_path / ALLOCATION_ARTIFACT).write_text(json.dumps(allocation), encoding="utf-8")
    (tmp_path / PROPOSAL_ARTIFACT).write_text(proposal, encoding="utf-8")


def test_verified_proposal_preserves_decimal_stress_and_allows_formatted_values(tmp_path):
    _write(tmp_path)

    assert check_portfolio(tmp_path) == []


def test_allocation_amounts_and_weights_are_checked_per_position_and_total(tmp_path):
    first, *rest = ALLOCATION["positions"]
    positions = [dict(first, weight_bps=6250, amount_hkd=1), *rest]
    _write(tmp_path, allocation=dict(ALLOCATION, positions=positions))

    assert check_portfolio(tmp_path) == [
        "Position 1 amount_hkd does not match weight_bps.",
        "Position weights must total 10000 basis points (100%).",
        "Position amounts must total HKD 1,000,000.",
    ]


def test_distinct_normalized_names_and_us_equity_are_required(tmp_path):
    first, *rest = ALLOCATION["positions"]
    positions = [dict(first, asset="  ＦＵＮＤ B  ", asset_class="other_equity"), *rest]
    _write(tmp_path, allocation=dict(ALLOCATION, positions=positions))

    assert check_portfolio(tmp_path) == [
        "Position 2 must have a nonempty, distinct asset name.",
        "Identify at least one proposed US equity position.",
    ]


def test_structured_fields_reject_coerced_values_and_extra_fields(tmp_path):
    _write(tmp_path, allocation=dict(ALLOCATION, stock_decline_bps="3333", unrelated=1))

    defects = check_portfolio(tmp_path)
    assert any("stock_decline_bps" in defect and "integer" in defect for defect in defects)
    assert any("unrelated" in defect and "Extra inputs" in defect for defect in defects)


def test_duplicate_json_keys_are_not_resolved_silently(tmp_path):
    _write(tmp_path)
    path = tmp_path / ALLOCATION_ARTIFACT
    path.write_text(path.read_text(encoding="utf-8")[:-1] + ', "stock_decline_bps": 2000}')

    assert "Duplicate JSON property names" in check_portfolio(tmp_path)[0]


def test_stale_prose_reserve_and_equation_are_flagged_after_the_table_passes(tmp_path):
    proposal = PROPOSAL + (
        "Raise bonds + cash from 15% to a more defensive allocation.\n"
        "Allocation check: 625,100 + 125,000 + 149,900 = 1,000,000.\n"
    )
    _write(tmp_path, proposal=proposal)

    assert check_portfolio(tmp_path) == [
        "An explicit sum in the proposal does not equal its stated result.",
        "A reserve baseline in the proposal contradicts the allocation summary.",
    ]


@pytest.mark.parametrize(
    ("old", "new", "defect"),
    [
        ("24.99%", "15%", "The Total reserve Weight cell"),
        ("1,000,000", "900,000", "The Total portfolio HKD cell"),
        ("250,008.33", "250,000", "The Stress loss HKD cell"),
        ("749,991.67", "750,000", "The Stress remaining HKD cell"),
    ],
)
def test_disagreement_between_summary_and_ledger_is_reported(tmp_path, old, new, defect):
    _write(tmp_path, proposal=PROPOSAL.replace(old, new))

    assert any(message.startswith(defect) for message in check_portfolio(tmp_path))


def test_summary_inside_literal_code_block_cannot_satisfy_delivery(tmp_path):
    _write(tmp_path, proposal="```markdown\n" + PROPOSAL + "\n```\n")

    assert check_portfolio(tmp_path) == [
        "Add one visible allocation summary between the required markers."
    ]
