"""dart_table 서식 파서 단위 테스트 — 합성 서식 텍스트(실공시 원문 아님)."""
from valuationproof import dart_table

FORM = """1. 사채의 종류 |
2. 사채의 권면(전자등록)총액 (원) |
3,000,000,000 |
4. 사채의 이율 |
표면이자율 (%) |
0.0 |
만기이자율 (%) |
2.50 |
5. 사채만기일 |
2029년 03월 05일 |
전환가액 (원/주) | 4,210 |
최저 조정가액 (원) |
- |
12. 납입일 |
2026년 03월 05일 |
"""


def test_label_next_cell_and_same_line():
    r = dart_table.extract(FORM)
    assert r["principal_krw"] == "3,000,000,000"
    assert r["conversion_price_krw"] == "4,210"          # 같은 줄 셀
    assert r["redemption_yield_pct"] == "2.50"
    assert r["maturity_date"] == "2029년 03월 05일"
    assert r["issue_date"] == "2026년 03월 05일"
    assert r["refixing_floor_krw"] is None               # '-' → 없음


def test_missing_label_absent():
    assert "principal_krw" not in dart_table.extract("전환가액 (원/주) |\n1,000 |")
