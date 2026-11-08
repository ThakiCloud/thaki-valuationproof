"""[V4.2 에서 engine="4.1" 고정 — V4.1 동작 재현 회귀 시험] V4 기준일 Excel 대조 — 분류(불일치·누락·미확인)·오탐 확정 방지·보고서·CLI·데모. 전부 합성 체인·합성 Excel."""
import datetime as dt
import hashlib
import json
import sys

import openpyxl
import pytest

from valuationproof import asof_terms, audit_v4, dart_source, demo_v4
from test_v4_asof import form, vals


@pytest.fixture
def ct():
    vs = [("20250101000001", "20250101", "original", vals(), form()),
          ("20250301000002", "20250301", "correction", vals(cp="900"), form(cp="900")),
          ("20250601000004", "20250601", "correction", vals(cp="900", mat="2028-03-05"),
           form(cp="900", mat="2028년 03월 05일", conv=("2025년 02월 10일", "2028년 02월 05일")))]
    chain = {"origin": vs[0][0], "corp_code": "SYNTH01", "series": "7",
             "versions": [{"rcept_no": r, "rcept_dt": d, "kind": k, "values": v, "link_method": "same_series"} for r, d, k, v, _ in vs]}
    return chain, {r: t for r, *_x, t in vs}


def cur(ch, tx, T):
    return {f: t["value"] for f, t in asof_terms.resolve(ch, tx, T, engine="4.1")["terms"].items()}


def wb(tmp_path, T, values, name="SYNTH_t.xlsx", labels=None, series="7"):
    w = openpyxl.Workbook(); ws = w.active; ws.title = "Inputs"; demo_v4._sheet(ws, T, series, values, labels)
    p = tmp_path / name; w.save(p); return p


def by(r): return {x["field"]: x for x in r["findings"] if x["field"]}


def test_normal_no_errors_no_autofix(ct, tmp_path):
    ch, tx = ct; T = "2025-12-31"; p = wb(tmp_path, T, cur(ch, tx, T)); h = hashlib.sha256(p.read_bytes()).hexdigest()
    r = audit_v4.audit(p, ch, tx, T, engine="4.1")
    assert r["errors_found"] == 0 and r["counts"]["불일치"] == 0 and r["counts"]["누락"] == 0
    assert r["auto_fix_applied"] == 0 and r["approval_status"] == "PENDING" and r["side_effects"] is False and r["ledger_ok"]
    assert r["workbook_sha256_before"] == r["workbook_sha256_after"] == h == hashlib.sha256(p.read_bytes()).hexdigest()
    assert by(r)["conversion_price_krw"]["class"] == "일치" and by(r)["conversion_end_date"]["class"] == "일치"


def test_stale_correction_and_future_correction(ct, tmp_path):
    ch, tx = ct
    T = "2025-12-31"; v = cur(ch, tx, T) | {"conversion_price_krw": "1000"}           # 3월 정정 미반영
    f = by(audit_v4.audit(wb(tmp_path, T, v), ch, tx, T, engine="4.1"))["conversion_price_krw"]
    assert f["class"] == "불일치" and f["subtype"].startswith("정정 미반영") and f["matched_version_rcept"] == "20250101000001"
    assert f["suggestion"]["requires_approval"] is True and f["suggestion"]["auto_applied"] is False
    assert any(e.get("rcept_no") == "20250601000004" and e.get("line") for e in f["evidence"])        # 공시 근거 + 원문 줄
    assert f["excel_cell"] == "Inputs!B4" and any(e.get("cell") == "Inputs!B4" for e in f["evidence"])
    T2 = "2025-04-15"; v2 = cur(ch, tx, T2) | {"maturity_date": "2028-03-05"}         # 기준일 뒤 정정 값을 미리 씀
    f2 = by(audit_v4.audit(wb(tmp_path, T2, v2, "SYNTH_f.xlsx"), ch, tx, T2, engine="4.1"))["maturity_date"]
    # 4.1 R7: 결의일 없는 기재 정정은 소급 가능성이 있어 효력일 미확정 → 자동 판정 대신 확인 필요
    assert f2["class"] == audit_v4.EFF_CLASS and f2["blocked_auto_class"] == "불일치" and f2["approval_required"] is True
    assert f2["blocked_auto_subtype"].startswith("기준일 뒤 정정 선반영") and f2["auto_verdict_allowed"] is False
    # 새 이사회결의(결의일이 기준일 뒤)로 확정되면 기존처럼 '기준일 뒤 정정 선반영' 불일치
    ch2 = {**ch, "versions": [v | {"board_dt": v["rcept_dt"]} for v in ch["versions"]]}
    f3 = by(audit_v4.audit(wb(tmp_path, T2, v2, "SYNTH_g.xlsx"), ch2, tx, T2, engine="4.1"))["maturity_date"]
    assert f3["class"] == "불일치" and f3["subtype"].startswith("기준일 뒤 정정 선반영")


def test_extra_field_stale(ct, tmp_path):
    ch, tx = ct; T = "2025-12-31"; v = cur(ch, tx, T) | {"conversion_end_date": "2027-12-05"}
    f = by(audit_v4.audit(wb(tmp_path, T, v), ch, tx, T, engine="4.1"))["conversion_end_date"]
    assert f["class"] == "불일치" and f["subtype"].startswith("정정 미반영") and f["disclosure_value"] == "2028-02-05"


def test_out_of_dictionary_label_is_unverified_not_missing(ct, tmp_path):
    ch, tx = ct; T = "2025-12-31"
    p = wb(tmp_path, T, cur(ch, tx, T), labels={"coupon_rate_pct": "이표율"})
    r = audit_v4.audit(p, ch, tx, T, engine="4.1"); f = by(r)["coupon_rate_pct"]
    assert f["class"] == "미확인" and "사전 밖" in f["subtype"] and f["possible_synonym_cells"]
    assert r["errors_found"] == 0                                                     # 오류로 확정하지 않는다


def test_truly_missing_field_is_missing(ct, tmp_path):
    ch, tx = ct; T = "2025-12-31"; v = cur(ch, tx, T); v.pop("refixing_floor_krw")
    f = by(audit_v4.audit(wb(tmp_path, T, v), ch, tx, T, engine="4.1"))["refixing_floor_krw"]
    assert f["class"] == "누락" and f["human_review_required"]


def test_option_and_period_absent_from_model_is_unverified(ct, tmp_path):
    ch, tx = ct; T = "2025-12-31"; v = cur(ch, tx, T)
    for k in ("conversion_start_date", "conversion_end_date", "put_option"): v.pop(k)
    b = by(audit_v4.audit(wb(tmp_path, T, v), ch, tx, T, engine="4.1"))
    assert b["put_option"]["class"] == "미확인" and b["conversion_start_date"]["class"] == "미확인"
    assert b["call_option"]["class"] == "해당없음"


def test_excel_asof_mismatch_flagged(ct, tmp_path):
    ch, tx = ct; p = wb(tmp_path, "2025-06-30", cur(ch, tx, "2025-12-31"))
    r = audit_v4.audit(p, ch, tx, "2025-12-31", engine="4.1")
    x = [f for f in r["findings"] if f["subtype"] == "Excel 평가기준일 불일치"]
    assert x and x[0]["class"] == "미확인" and x[0]["severity"] == "high"


def test_transposed_two_series_reads_target_row(ct, tmp_path):
    ch, tx = ct; T = "2025-12-31"; c = cur(ch, tx, T)
    w = openpyxl.Workbook(); ws = w.active; ws.title = "CB"
    ws["A1"] = "평가기준일"; ws["B1"] = dt.date(2025, 12, 31)
    hdr = ["회차", "권면총액", "전환가액", "최저조정가액", "사채만기일", "납입일"]
    for j, h in enumerate(hdr, 1): ws.cell(3, j, h)
    for i, (s, k) in enumerate((("8", 2), ("7", 1)), 4):
        ws.cell(i, 1, f"제{s}회"); ws.cell(i, 2, int(c["principal_krw"]) * k); ws.cell(i, 3, int(c["conversion_price_krw"]) * k)
        ws.cell(i, 4, int(c["refixing_floor_krw"]) * k)
        ws.cell(i, 5, dt.date(2028 + (k - 1) * 2, 3, 5)); ws.cell(i, 6, dt.date(2025 + (k - 1) * 2, 1, 10))
    p = tmp_path / "SYNTH_tr.xlsx"; w.save(p)
    b = by(audit_v4.audit(p, ch, tx, T, engine="4.1"))
    for f, cell in (("principal_krw", "CB!B5"), ("conversion_price_krw", "CB!C5"), ("maturity_date", "CB!E5")):
        assert b[f]["class"] == "일치" and b[f]["excel_cell"] == cell, (f, b[f])


def test_reports_contain_cell_value_link(ct, tmp_path):
    ch, tx = ct; T = "2025-12-31"; v = cur(ch, tx, T) | {"conversion_price_krw": "1000"}
    r = audit_v4.audit(wb(tmp_path, T, v), ch, tx, T, engine="4.1")
    paths = audit_v4.write_reports(r, tmp_path / "out", "rep")
    md = open(paths["md"], encoding="utf-8").read(); html = open(paths["html"], encoding="utf-8").read()
    for s in ("Inputs!B4", "1000", "900", "dart.fss.or.kr", "PENDING"):
        assert s in md and s in html
    assert json.load(open(paths["json"], encoding="utf-8"))["auto_fix_applied"] == 0


def test_cli_audit_with_fixture(ct, tmp_path, monkeypatch, capsys):
    from valuationproof import cli
    ch, tx = ct; T = "2025-12-31"; fx = tmp_path / "chain.json"; dart_source.export_fixture(ch, tx, fx)
    assert "사채의 종류" not in fx.read_text(encoding="utf-8")                        # 원문 미포함(값·위치·해시만)
    p = wb(tmp_path, T, cur(ch, tx, T) | {"maturity_date": "2029-03-05"})
    monkeypatch.setattr(sys, "argv", ["valuationproof", "audit", "--corp", "SYNTH01", "--cb", ch["origin"], "--asof", T,
                                      "--excel", str(p), "--chain-file", str(fx), "--out", str(tmp_path / "o")])
    cli.main(); out = json.loads(capsys.readouterr().out)
    assert out["errors_found"] == 1 and out["selected_version"] == "20250601000004" and out["approval_status"] == "PENDING"


def test_fixture_reproduces_dataset_result(ct, tmp_path):
    ch, tx = ct; T = "2025-12-31"; fx = tmp_path / "chain.json"; dart_source.export_fixture(ch, tx, fx)
    ch2, tx2 = dart_source.load_fixture(fx)
    a, b = asof_terms.resolve(ch, tx, T, engine="4.1"), asof_terms.resolve(ch2, tx2, T, engine="4.1")
    assert {f: t["value"] for f, t in a["terms"].items()} == {f: t["value"] for f, t in b["terms"].items()}
    assert a["terms"]["maturity_date"]["source"]["doc_sha256"] == b["terms"]["maturity_date"]["source"]["doc_sha256"]


def test_demo_v4_expectations(ct, tmp_path):
    ch, tx = ct
    s = demo_v4.run(ch, tx, tmp_path / "demo", "2025-12-31")
    assert s["all_expectations_met"], [(c["case"], c["counts"]) for c in s["cases"]]
    assert {c["case"] for c in s["cases"]} >= {"정상", "정정 미반영", "리픽싱·만기 오기재"}
    assert s["SYNTHETIC_EXCEL"] is True


def test_fetch_requires_env_key(monkeypatch, tmp_path):
    monkeypatch.delenv("API_K_DART", raising=False); monkeypatch.delenv("DART_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="API_K_DART"):
        dart_source.fetch_corp("00000000", tmp_path)
