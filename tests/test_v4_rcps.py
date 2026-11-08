"""RCPS 스키마·체크리스트 — 상태는 항상 고객 실증 필요. 합성 입력으로 '성공' 판정이 나오지 않는지 본다."""
from valuationproof import rcps


def test_schema_has_three_areas():
    s = rcps.schema()
    assert {"redemption", "preferred_dividend", "conversion"} <= set(s["required"])
    assert {c["area"] for c in rcps.CHECKLIST} >= {"조기상환", "우선배당", "전환비율 조정"}


def test_check_never_succeeds_on_synthetic():
    t = {"instrument_id": "SYNTH-RCPS", "as_of": "2025-12-31", "issue": {"issue_date": "2024-01-01", "shares": 10, "issue_price_krw": 10000},
         "redemption": {"holder_put": True, "first_exercise_date": "2027-01-01", "redemption_amount_basis": "issue_price_plus_irr"},
         "preferred_dividend": {"rate_pct": 1, "cumulative": True, "participating": False},
         "conversion": {"initial_ratio": 1, "conversion_start_date": "2024-01-02", "conversion_end_date": "2034-01-01", "refixing": {}},
         "sources": [{"field": "redemption", "doc_sha256": "0" * 64, "locator": "제5조"}]}
    r = rcps.check(t)
    assert r["status"] == "CUSTOMER_EVIDENCE_REQUIRED" and r["verdict"] is None and not r["missing_required"]
    assert all(c["result"] == "PENDING_HUMAN" for c in r["checklist"]) and "issue" in r["fields_without_source"]
    assert "redemption.holder_put" in rcps.check({"redemption": {}})["missing_required"]
