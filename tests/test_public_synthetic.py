"""Public-build checks: the bundled chain is fully synthetic, the offline demo needs no key/network, synthetic links never point at DART."""
import json
import socket

import pytest

from valuationproof import asof_terms, cli, synthetic


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def guard(*a, **k): raise AssertionError("network access attempted in offline mode")
    monkeypatch.setattr(socket, "create_connection", guard)
    monkeypatch.delenv("API_K_DART", raising=False); monkeypatch.delenv("DART_API_KEY", raising=False)


def test_synthetic_chain_asof_rules():
    ch, tx = synthetic.load()
    assert ch["synthetic"] and ch["corp_code"] == "SYNTH001" and len(ch["versions"]) == 4
    assert all("SYNTHETIC" in t for t in tx.values())
    r = asof_terms.resolve(ch, tx, synthetic.DEFAULT_ASOF)
    assert r["selected_version"]["rcept_no"] == "20250930990003"
    assert [x["rcept_no"] for x in r["excluded_after_asof"]] == ["20260115990004"]
    t = r["terms"]
    assert t["conversion_price_krw"]["value"] == "8500" and t["maturity_date"]["value"] == "2028-09-30"
    assert t["refixing_floor_krw"]["value"] == "7000" and t["refixing_floor_krw"]["changes_after_asof"][0]["value"] == "6000"
    assert all(v["contract_effective_confirmed"] is False for v in t.values())          # never auto-confirmed
    assert asof_terms.resolve(ch, tx, "2025-03-11")["status"] == "NOT_YET_FILED"          # before the board resolution
    early = asof_terms.resolve(ch, tx, "2025-03-13")                                        # resolved, not yet filed -> all unconfirmed
    assert early["status"] == "RESOLVED" and set(early["unconfirmed_fields"]) == set(asof_terms.ALL_FIELDS)


def test_synthetic_links_are_never_dart_urls():
    ch, tx = synthetic.load()
    js = json.dumps(asof_terms.resolve(ch, tx, synthetic.DEFAULT_ASOF), ensure_ascii=False)
    assert "dart.fss.or.kr" not in js and "synthetic://20250930990003" in js
    assert asof_terms.DART_VIEW.format("20250101000001").startswith("https://dart.fss.or.kr/")   # unregistered -> normal link


def test_cli_demo_offline(tmp_path, capsys):
    cli.main(["demo", "--out", str(tmp_path / "d")])
    out = json.loads(capsys.readouterr().out)
    assert out["all_expectations_met"] is True and len(out["cases"]) == 4
    for p in (tmp_path / "d").glob("*_report.*"):
        s = p.read_text(encoding="utf-8")
        assert "dart.fss.or.kr" not in s and ("합성" in s)
        if p.suffix == ".json":
            r = json.loads(s); assert r["auto_fix_applied"] == 0 and r["approval_status"] == "PENDING"
    fx = json.loads((tmp_path / "d" / "synthetic_chain_fixture.json").read_text(encoding="utf-8"))
    assert fx["synthetic"] is True and "사채의 종류" not in json.dumps(fx, ensure_ascii=False)


def test_cli_audit_synthetic_and_fixture(tmp_path, capsys):
    cli.main(["demo", "--out", str(tmp_path / "d")]); capsys.readouterr()
    x = tmp_path / "d" / "SYNTH_stale_correction.xlsx"
    cli.main(["audit", "--synthetic", "--asof", "2025-10-01", "--excel", str(x), "--out", str(tmp_path / "a")])
    a = json.loads(capsys.readouterr().out)
    cli.main(["audit", "--chain-file", str(tmp_path / "d" / "synthetic_chain_fixture.json"), "--asof", "2025-10-01", "--excel", str(x),
              "--out", str(tmp_path / "b")])
    b = json.loads(capsys.readouterr().out)
    assert a["errors_found"] == b["errors_found"] == 3 and a["selected_version"] == b["selected_version"] == "20250930990003"
    assert "dart.fss.or.kr" not in (tmp_path / "b" / "SYNTH_stale_correction_report.md").read_text(encoding="utf-8")


def test_live_mode_requires_user_key(tmp_path):
    with pytest.raises(SystemExit, match="API_K_DART"):
        cli.main(["resolve", "--data", str(tmp_path), "--fetch", "--corp", "00000000", "--cb", "1", "--asof", "2025-01-01"])


def test_unconfirmed_effective_date_is_never_auto_judged(tmp_path, capsys):
    from valuationproof import audit_v4
    ch, tx = synthetic.load_v42()                                       # V4.2 chain: adjustment collection complete (coverage VERIFIED)
    r = asof_terms.resolve(ch, tx, synthetic.EFFECTIVE_DATE_ASOF)      # after C1 board date, before C1 was filed
    t = r["terms"]["conversion_price_krw"]
    assert t["effective_status"] == "UNCONFIRMED_DEPENDS" and t["status_ko"] == asof_terms.EFF_UNCONFIRMED
    assert set(t["alternative_values"]) == {"10000", "8500"} and t["review_required"] and t["contract_effective_confirmed"] is False
    assert t["date_key"]["basis"] == "new_board_resolution" and t["date_key"]["date_key_fallback"] is False
    cli.main(["demo", "--out", str(tmp_path / "d")]); capsys.readouterr()
    a = audit_v4.audit(tmp_path / "d" / "SYNTH_stale_correction.xlsx", ch, tx, synthetic.EFFECTIVE_DATE_ASOF)
    f = {x["field"]: x for x in a["findings"] if x["field"]}["conversion_price_krw"]
    assert f["class"] == asof_terms.EFF_UNCONFIRMED and f["auto_verdict_allowed"] is False and f["approval_required"] is True
    paths = audit_v4.write_reports(a, tmp_path / "e", "eff")                # HTML/MD must render the 4.1 class
    assert asof_terms.EFF_UNCONFIRMED in open(paths["html"], encoding="utf-8").read()


# ── V4.2 (synthetic adjustment filings, in-memory source — still no network) ─────────────────────────────────────
def test_v42_synthetic_collection_and_bitemporal_modes():
    from valuationproof import adjust_events
    ch, tx = synthetic.load_v42()
    cov = ch["adjustment_coverage"]
    assert cov["problems"] == [] and all(w["ok"] for w in cov["list_windows"]) and len(ch["adjustments"]) == 1
    ev = ch["adjustments"][0]
    assert (ev["effective_at"], ev["known_at"], ev["before_krw"], ev["conversion_price_krw"]) == ("20251120", "20251121", "8500", "7800")
    assert ev["revisions"][0]["rcept_no"] == "20251205990006" and ev["revisions"][0]["known_at"] == "20251205"
    T = synthetic.V42_ASOF
    rs = asof_terms.resolve(ch, tx, T)["terms"]["conversion_price_krw"]
    assert rs["value"] == "7750" and rs["mode"] == "restated" and rs["coverage_status"] == "VERIFIED"
    assert rs["adjusted_by"]["bitemporal"] == {"rcept_dt": "20251121", "effective_at": "20251120", "known_at": "20251205"}
    assert rs["value_basis"] == asof_terms.VALUE_BASIS and rs["contract_effective_confirmed"] is False
    assert asof_terms.resolve(ch, tx, T, mode="as_known")["terms"]["conversion_price_krw"]["value"] == "8500"
    assert asof_terms.resolve(ch, tx, T, mode="as_known", knowledge_cutoff="2025-11-30")["terms"]["conversion_price_krw"]["value"] == "7800"
    assert asof_terms.resolve(ch, tx, synthetic.DEFAULT_ASOF)["terms"]["conversion_price_krw"]["value"] == "8500"   # before the adjustment
    assert "dart.fss.or.kr" not in json.dumps(ch["adjustments"], ensure_ascii=False)
    assert adjust_events.report_class("분기보고서 [SYNTHETIC]") == "other"


@pytest.mark.parametrize("fail,check", [("list_page", "C1_LIST_OK"), ("off", "C0_COLLECTED")])
def test_v42_broken_collection_is_never_auto_judged(tmp_path, capsys, fail, check):
    from valuationproof import audit_v4
    ch, tx = synthetic.load_v42(fail=fail)
    t = asof_terms.resolve(ch, tx, "2025-12-31")["terms"]["conversion_price_krw"]
    assert t["coverage_status"] == asof_terms.COV_UNVERIFIED and t["auto_verdict_allowed"] is False and t["approval_required"]
    assert t["event_coverage"]["reasons"][0].startswith(check)
    cli.main(["demo", "--out", str(tmp_path / "d")]); capsys.readouterr()
    a = audit_v4.audit(tmp_path / "d" / "SYNTH_normal.xlsx", ch, tx, "2025-12-31")
    f = {x["field"]: x for x in a["findings"] if x["field"]}["conversion_price_krw"]
    assert f["class"] == audit_v4.COV_CLASS and f["auto_verdict_allowed"] is False and a["event_coverage_unverified"] == 1
    assert audit_v4.COV_CLASS in open(audit_v4.write_reports(a, tmp_path / "r", "cov")["html"], encoding="utf-8").read()


def test_v42_cli_terms_and_demo_offline(tmp_path, capsys):
    cli.main(["terms", "--synthetic", "--asof", synthetic.V42_ASOF])
    by = {x["mode"]: x for x in json.loads(capsys.readouterr().out)["results"]}
    assert by["restated"]["terms"]["conversion_price_krw"]["value"] == "7750" and by["as_known"]["terms"]["conversion_price_krw"]["value"] == "8500"
    assert by["restated"]["event_coverage"]["status"] == "VERIFIED"
    cli.main(["terms", "--synthetic", "--asof", synthetic.V42_ASOF, "--adjustments", "off", "--mode", "restated"])
    assert json.loads(capsys.readouterr().out)["results"][0]["terms"]["conversion_price_krw"]["coverage_status"] == asof_terms.COV_UNVERIFIED
    cli.main(["terms", "--synthetic", "--asof", synthetic.V42_ASOF, "--engine", "4.1"])
    v41 = json.loads(capsys.readouterr().out)["results"][0]
    assert v41["mode"] == "v41" and v41["terms"]["conversion_price_krw"]["value"] == "8500"          # V4.1: adjustments not collected
    cli.main(["demo", "--out", str(tmp_path / "d")])
    out = json.loads(capsys.readouterr().out)
    assert [x["value"] for x in out["v42_synthetic_demo"]] == ["7750", "8500", "7800", "8500", "8500"]
    assert [x["coverage_status"] for x in out["v42_synthetic_demo"]][-2:] == [asof_terms.COV_UNVERIFIED] * 2
    assert "dart.fss.or.kr" not in (tmp_path / "d" / "v42_synthetic_demo.json").read_text(encoding="utf-8")
