"""V3 Excel Auditor 단위 테스트 — 전부 합성 체인·합성 Excel(실공시·실평가모형 아님)."""
import datetime as dt
import hashlib

import openpyxl
import pytest

from valuationproof import excel_v3


@pytest.fixture
def chain():
    v1 = {"principal_krw": "5000000000", "conversion_price_krw": "933", "coupon_rate_pct": "0.0", "redemption_yield_pct": "3.0",
          "maturity_date": "2028-01-05", "issue_date": "2025-01-05", "refixing_floor_krw": "700"}
    v2 = dict(v1, conversion_price_krw="800", maturity_date="2028-03-05")
    return {"origin": "20250101000001", "series": "7", "versions": [
        {"rcept_no": "20250101000001", "rcept_dt": "20250101", "kind": "original", "values": v1},
        {"rcept_no": "20250301000002", "rcept_dt": "20250301", "kind": "correction", "values": v2}]}


def _wb(tmp_path, fill, name="t.xlsx"):
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Inputs"; fill(wb, ws); p = tmp_path / name; wb.save(p); return p


def _rows(ws, rows, start=3, col=1):
    for i, row in enumerate(rows, start):
        for j, v in enumerate(row): ws.cell(i, col + j, v)


LATEST = [("권면총액", 5000000000), ("전환가액", 800), ("표면이자율(%)", 0), ("만기이자율(%)", 3), ("사채만기일", dt.date(2028, 3, 5)),
          ("납입일", dt.date(2025, 1, 5)), ("최저조정가액", 700)]


def verdicts(r): return {f["field"]: f["verdict"] for f in r["findings"] if f["field"]}


def test_synonyms_and_parse_label():
    assert excel_v3.parse_label("전환가격")["field"] == "conversion_price_krw"
    assert excel_v3.parse_label("행사가액 (원/주)")["field"] == "conversion_price_krw"
    assert excel_v3.parse_label("쿠폰")["field"] == "coupon_rate_pct"
    assert excel_v3.parse_label("상환기일")["field"] == "maturity_date"
    assert excel_v3.parse_label("만기보장수익률")["field"] == "redemption_yield_pct"
    assert excel_v3.parse_label("최초 전환가액")["decoy"] is True
    assert excel_v3.parse_label("이자율").get("ambiguous")
    assert excel_v3.parse_label("평가기준일")["kind"] == "asof" and excel_v3.parse_label("효력발생일")["kind"] == "effective"
    assert excel_v3.parse_label("권면총액(억원)")["unit"] == ("억원", 1e8)


def test_clean_match_and_readonly_sha(tmp_path, chain):
    def fill(wb, ws):
        ws["A1"] = "평가기준일"; ws["B1"] = dt.date(2025, 6, 30); _rows(ws, LATEST)
    p = _wb(tmp_path, fill); before = hashlib.sha256(p.read_bytes()).hexdigest()
    r = excel_v3.audit_workbook_v3(p, chain)
    assert set(verdicts(r).values()) == {"match"} and not r["review_required"]
    assert r["workbook_sha256_before"] == r["workbook_sha256_after"] == before == hashlib.sha256(p.read_bytes()).hexdigest()
    assert all(f["approval"]["status"] == "PENDING" for f in r["findings"])


@pytest.mark.parametrize("unit,scale", [("억원", 1e8), ("천원", 1e3), ("백만원", 1e6)])
def test_sheet_unit_marker_converts_principal_only(tmp_path, chain, unit, scale):
    def fill(wb, ws):
        ws["A1"] = "평가기준일"; ws["B1"] = dt.date(2025, 6, 30); ws["A2"] = f"(단위: {unit})"
        _rows(ws, [(a, b / scale if a == "권면총액" else b) for a, b in LATEST])
    r = excel_v3.audit_workbook_v3(_wb(tmp_path, fill), chain)
    assert verdicts(r)["principal_krw"] == "match" and verdicts(r)["conversion_price_krw"] == "match"


def test_label_paren_unit_and_display_only_format(tmp_path, chain):
    def fill(wb, ws):
        ws["A1"] = "평가기준일"; ws["B1"] = dt.date(2025, 6, 30); _rows(ws, LATEST)
        ws["A3"] = "권면총액(억원)"; ws["B3"] = 50
    assert verdicts(excel_v3.audit_workbook_v3(_wb(tmp_path, fill), chain))["principal_krw"] == "match"
    assert excel_v3.fmt_unit('#,##0,,"백만원"')[0] == 1.0          # 표시만 백만원, 저장값 원
    assert excel_v3.fmt_unit('0.0"억원"')[0] == 1e8


def test_series_columns_pick_target_not_first(tmp_path, chain):
    def fill(wb, ws):
        ws["A1"] = "평가기준일"; ws["B1"] = dt.date(2025, 6, 30); ws["B2"] = "6회차"; ws["C2"] = "7회차"
        _rows(ws, [(a, b if not isinstance(b, int) else b + 1, b) for a, b in LATEST])
    r = excel_v3.audit_workbook_v3(_wb(tmp_path, fill), chain)
    assert verdicts(r)["conversion_price_krw"] == "match"
    assert next(f for f in r["findings"] if f["field"] == "conversion_price_krw")["excel"]["cell"] == "C4"


def test_series_unknown_with_two_series_is_review(tmp_path, chain):
    chain = dict(chain); chain.pop("series")
    def fill(wb, ws):
        ws["A1"] = "평가기준일"; ws["B1"] = dt.date(2025, 6, 30); ws["B2"] = "6회차"; ws["C2"] = "7회차"; _rows(ws, [(a, b, b) for a, b in LATEST])
    r = excel_v3.audit_workbook_v3(_wb(tmp_path, fill), chain)
    assert set(verdicts(r).values()) == {"REVIEW_REQUIRED"}


def test_series_mislink_detected(tmp_path, chain):
    def fill(wb, ws):
        ws["A1"] = "평가기준일"; ws["B1"] = dt.date(2025, 6, 30); ws["A2"] = "[제8회 무보증 사모 전환사채]"; _rows(ws, LATEST)
    assert set(verdicts(excel_v3.audit_workbook_v3(_wb(tmp_path, fill), chain)).values()) == {"series_mismatch"}


def test_scenario_base_and_merged_label(tmp_path, chain):
    def fill(wb, ws):
        ws["A1"] = "평가기준일"; ws["B1"] = dt.date(2025, 6, 30); ws["C2"] = "Base"; ws["D2"] = "Bull"; ws["E2"] = "Bear"
        for i, (a, b) in enumerate(LATEST, 3):
            ws.cell(i, 1, a); ws.merge_cells(start_row=i, start_column=1, end_row=i, end_column=2)
            ws.cell(i, 3, b); ws.cell(i, 4, b); ws.cell(i, 5, b if not isinstance(b, int) else b * 2 + 1)
    r = excel_v3.audit_workbook_v3(_wb(tmp_path, fill), chain)
    assert set(verdicts(r).values()) == {"match"} and r["scenario_notes"]


def test_external_ref_and_unevaluated_formula_are_review(tmp_path, chain):
    def fill(wb, ws):
        ws["A1"] = "평가기준일"; ws["B1"] = dt.date(2025, 6, 30); _rows(ws, LATEST)
        ws["B4"] = "=[1]Terms!$B$5"; ws["B5"] = "=ROUND(F5*1,0)"
    v = verdicts(excel_v3.audit_workbook_v3(_wb(tmp_path, fill), chain))
    assert v["conversion_price_krw"] == "REVIEW_REQUIRED" and v["coupon_rate_pct"] == "REVIEW_REQUIRED"


def test_cross_sheet_ref_and_conflict(tmp_path, chain):
    def fill(wb, ws):
        inp = wb.create_sheet("Terms"); ws["A1"] = "평가기준일"; ws["B1"] = dt.date(2025, 6, 30)
        _rows(inp, LATEST, start=2)
        for i, (a, _) in enumerate(LATEST, 3): ws.cell(i, 1, a); ws.cell(i, 2, f"=Terms!B{i - 1}")
        inp["B3"] = 801      # 다른 시트 값과 충돌시키려면 Inputs 쪽을 리터럴로
        ws["B4"] = 800
    v = verdicts(excel_v3.audit_workbook_v3(_wb(tmp_path, fill), chain))
    assert v["principal_krw"] == "match" and v["conversion_price_krw"] == "REVIEW_REQUIRED"


def test_stale_maturity_pct_unit_error_and_refix_missing(tmp_path, chain):
    def fill(wb, ws):
        ws["A1"] = "평가기준일"; ws["B1"] = dt.date(2025, 6, 30)
        rows = [(a, dt.date(2028, 1, 5) if a == "사채만기일" else b) for a, b in LATEST if a != "최저조정가액"]
        _rows(ws, rows); c = ws.cell(6, 2, 3.0); c.number_format = "0.00%"   # 만기이자율 3.0 → 300%
    r = excel_v3.audit_workbook_v3(_wb(tmp_path, fill), chain); v = verdicts(r)
    assert v["maturity_date"] == "stale_version" and v["redemption_yield_pct"] == "unit_error"
    fr = next(f for f in r["findings"] if f["field"] == "refixing_floor_krw")
    assert fr["verdict"] == "missing_in_model" and fr["reason"] == "refixing_clause_missing"
    st = next(f for f in r["findings"] if f["field"] == "maturity_date")
    assert any(e["doc_id"] == "DART:20250101000001" for e in st["evidence"])


def test_pct_ambiguous_fraction_is_review(tmp_path, chain):
    def fill(wb, ws):
        ws["A1"] = "평가기준일"; ws["B1"] = dt.date(2025, 6, 30); _rows(ws, LATEST); ws["A6"] = "만기이자율"; ws["B6"] = 0.03
    assert verdicts(excel_v3.audit_workbook_v3(_wb(tmp_path, fill), chain))["redemption_yield_pct"] == "REVIEW_REQUIRED"


def test_effective_date_not_used_as_asof(tmp_path, chain):
    def fill(wb, ws):
        ws["A1"] = "효력발생일"; ws["B1"] = dt.date(2025, 2, 1); _rows(ws, LATEST)
    r = excel_v3.audit_workbook_v3(_wb(tmp_path, fill), chain)
    assert r["as_of"] is None and r["effective_dates"][0]["date"] == "2025-02-01"
    assert set(verdicts(r).values()) == {"REVIEW_REQUIRED"}


def test_decoy_label_ignored(tmp_path, chain):
    def fill(wb, ws):
        ws["A1"] = "평가기준일"; ws["B1"] = dt.date(2025, 6, 30); _rows(ws, LATEST, start=4); ws["A3"] = "최초 전환가액"; ws["B3"] = 933
    r = excel_v3.audit_workbook_v3(_wb(tmp_path, fill), chain)
    assert verdicts(r)["conversion_price_krw"] == "match"
