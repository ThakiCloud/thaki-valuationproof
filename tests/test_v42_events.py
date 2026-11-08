"""V4.2 — 전환가액조정 공시 수집·연결(adjust_events), bitemporal 모드(restated/as_known), 완전성 검사 C0~C7, 공시 기반 추정 라벨,
공개본 피드백 버그 3건 회귀. 전부 합성 체인·합성 원문·가짜 fetch(실공시·네트워크 없음)."""
import io
import json
import sys
import zipfile

import openpyxl
import pytest

from valuationproof import adjust_events as A, asof_terms, audit_v4, dart_source, demo_v4
from test_v4_asof import form, vals
from test_v41_effective_date import bform

CORP = "SYNTH01"


def chain(restate=True, extra_bd=None):
    """원공시(결의 2024-12-30) + 정정(같은 결의일 = 기재 정정, 전환가액 900). 회차 7."""
    vs = [("20250101000001", "20250101", "original", vals(), bform("2024년 12월 30일")),
          ("20250301000002", "20250301", "correction", vals(cp="900"), bform("2024년 12월 30일" if restate else "2025년 02월 25일", cp="900"))]
    ch = {"origin": vs[0][0], "corp_code": CORP, "series": "7",
          "versions": [{"rcept_no": r, "rcept_dt": d, "kind": k, "values": v, "link_method": "same_series"} for r, d, k, v, _ in vs]}
    return ch, {r: t for r, *_x, t in vs}


def xml(rows, eff, kind=None, bd="-"):
    """합성 조정 공시 XML. kind 를 주면 유가증권 안내공시형('1. 구분')."""
    tr = lambda *c: "<TR>" + "".join(f"<TD>{x}</TD>" for x in c) + "</TR>"
    head = (tr("1. 구분", kind) + tr("2. 증권의 종류", "제7회 무기명식 사모 전환사채") + "<P>3. 조정에 관한 사항</P>") if kind else "<P>1. 조정에 관한 사항</P>"
    body = tr("회차", "상장여부", "조정전 전환가액 (원)", "조정후 전환가액 (원)") + "".join(tr(s, "비상장", b, a) for s, b, a in rows)
    tail = tr("4. 조정사유", "시가하락") + tr("5. 조정가액 적용일", eff) + tr("6. 이사회결의일(결정일)", bd)
    return f"<DOCUMENT><TITLE>전환가액의 조정</TITLE>{head}<TABLE>{body}</TABLE><TABLE>{tail}</TABLE></DOCUMENT>"


def zdoc(s, enc="utf-8"):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z: z.writestr("d.xml", s.encode(enc))
    return b.getvalue()


KOSDAQ = "전환가액의조정"
GUIDE = "전환가액ㆍ신주인수권행사가액ㆍ교환가액의조정(안내공시)"


class Fake:
    """가짜 OpenDART. reports = [(rcept_no, rcept_dt, report_nm, xml_text)]. 목록은 page_size 로 쪽을 나눈다."""

    def __init__(self, reports, page_size=100, fail_page=None, enc="utf-8"):
        self.reports, self.ps, self.fail_page, self.enc, self.calls = reports, page_size, fail_page, enc, []

    def __call__(self, ep, raw=False, **q):
        self.calls.append((ep, q))
        if ep == "document.xml":
            return zdoc(next(x for r, _d, _n, x in self.reports if r == q["rcept_no"]), self.enc)
        if self.fail_page and q["page_no"] == self.fail_page: raise ConnectionError("synthetic")
        items = [{"rcept_no": r, "rcept_dt": d, "report_nm": n, "corp_code": CORP} for r, d, n, _x in self.reports
                 if q["bgn_de"] <= d <= q["end_de"]] + [{"rcept_no": "20250505000099", "rcept_dt": "20250505", "report_nm": "분기보고서", "corp_code": CORP}]
        if not items: return {"status": "013"}
        pages = [items[i:i + self.ps] for i in range(0, len(items), self.ps)]
        return {"status": "000", "total_page": len(pages), "total_count": len(items), "list": pages[q["page_no"] - 1]}


ADJ1 = ("20250820000010", "20250820", KOSDAQ, xml([("7", "900", "800"), ("8", "5,000", "4,000")], "2025-08-17"))


def collected(tmp_path, reports, ch=None, counts=None, **kw):
    ch = ch or chain()[0]
    rec = A.collect_corp(CORP, A.chain_start(ch), "20251231", tmp_path / "c", Fake(reports, **kw), sleep=0)
    return A.attach(ch, rec, counts), rec


# ── 파서 ─────────────────────────────────────────────────────────────────────────────────────────
def test_parser_kosdaq_guidance_and_not_cb():
    p = A.parse_adjustment_v42(A.xml_text(zdoc(ADJ1[3])), KOSDAQ)
    assert p["status"] == "ok" and p["format"] == "kosdaq" and p["effective_dt"] == "20250817" and p["kind"] == "CB"
    assert p["rows"] == [{"series": "7", "before_krw": "900", "after_krw": "800"}, {"series": "8", "before_krw": "5000", "after_krw": "4000"}]
    g = A.parse_adjustment_v42(A.xml_text(zdoc(xml([("7", "900", "800")], "2025-08-17", kind="전환가액의 조정"))), GUIDE)
    assert g["status"] == "ok" and g["format"] == "guidance" and g["kind"] == "CB" and g["security"].startswith("제7회")
    bw = A.parse_adjustment_v42(A.xml_text(zdoc(xml([("7", "900", "800")], "2025-08-17", kind="신주인수권 행사가액의 조정"))), GUIDE)
    assert bw["status"] == "not_cb" and bw["kind"] == "BW"
    assert A.parse_adjustment_v42(A.xml_text(zdoc(xml([("7", "900", "800")], "-"))), KOSDAQ)["status"] == "no_effective_date"
    t = A.parse_adjustment_v42(A.xml_text(zdoc(ADJ1[3])), "전환가액의조정(제9회차)")
    assert t["status"] == "series_conflict_with_title" and t["title_series"] == ["9"]
    assert A.report_class("전환주식의전환가액조정") == "not_cb" and A.report_class("[기재정정]" + KOSDAQ) == "target"
    assert A.is_correction("[기재정정]전환가액의조정") and not A.is_correction(KOSDAQ) and A.report_class("분기보고서") == "other"


def test_xml_text_reads_cp949_documents():
    """2022년 이전 원문은 EUC-KR 이 많다 — UTF-8 replace 로 읽으면 라벨이 깨진다(V4.2 run1 발견). CP949 폴백."""
    s = A.xml_text(zdoc(ADJ1[3], "cp949"))
    assert "�" not in s and A.parse_adjustment_v42(s, KOSDAQ)["status"] == "ok"
    assert A.parse_adjustment_v42(dart_source._xml_text(zdoc(ADJ1[3], "cp949")), KOSDAQ)["status"] != "ok"   # V4 변환기 = 깨짐(재현)


def test_windows_even_year_two_year():
    assert A.windows("20211115", "20251107") == [("20200101", "20211231"), ("20220101", "20231231"), ("20240101", "20251107")]


# ── 수집기 ───────────────────────────────────────────────────────────────────────────────────────
def test_collect_paginates_caches_and_skips_other_reports(tmp_path):
    fk = Fake([ADJ1], page_size=1)
    rec = A.collect_corp(CORP, "20241201", "20251231", tmp_path / "c", fk, sleep=0)
    w = rec["list_windows"]
    assert len(w) == 1 and w[0]["ok"] and w[0]["pages_got"] == w[0]["total_page"] == 2 and w[0]["items_got"] == w[0]["total_count"] == 2
    assert [r["rcept_no"] for r in rec["reports"]] == [ADJ1[0]] and rec["api_calls"] == 3          # 목록 2쪽 + 원문 1건(분기보고서는 원문 안 받음)
    assert rec["reports"][0]["dart_url"].endswith(ADJ1[0]) and rec["reports"][0]["known_at"] == "20250820"
    again = A.collect_corp(CORP, "20241201", "20251231", tmp_path / "c", offline=True)
    assert again["api_calls"] == 0 and again["reports"][0]["parse"] == rec["reports"][0]["parse"]
    miss = A.collect_corp(CORP, "20241201", "20251231", tmp_path / "empty", offline=True)
    assert miss["list_windows"][0]["status"] == "CACHE_MISS" and not miss["list_windows"][0]["ok"]


def test_collect_error_is_recorded_and_blocks_conversion_price(tmp_path):
    ch, rec = collected(tmp_path, [ADJ1], page_size=1, fail_page=2)
    assert rec["list_windows"][0]["status"] == "ERR:ConnectionError" and not rec["list_windows"][0]["ok"]
    r = asof_terms.resolve(ch, chain()[1], "2025-09-30")
    t = r["terms"]["conversion_price_krw"]
    assert t["coverage_status"] == asof_terms.COV_UNVERIFIED and t["auto_verdict_allowed"] is False and t["approval_required"]
    assert any(x.startswith("C1_LIST_OK") for x in t["event_coverage"]["reasons"]) and r["coverage_unverified_fields"] == ["conversion_price_krw"]
    assert r["terms"]["principal_krw"].get("coverage_status") is None                     # 다른 필드는 막지 않는다


def test_mojibake_cache_is_refetched(tmp_path):
    cd = tmp_path / "c"; (cd / "doc").mkdir(parents=True)
    (cd / "doc" / f"{ADJ1[0]}.txt").write_text(dart_source._xml_text(zdoc(ADJ1[3], "cp949")), encoding="utf-8")
    fk = Fake([ADJ1], enc="cp949")
    rec = A.collect_corp(CORP, "20241201", "20251231", cd, fk, sleep=0)
    r = rec["reports"][0]
    assert r["doc_redecoded"] and r["doc_status"] == "ok" and r["parse"]["status"] == "ok" and (cd / "doc" / f"{ADJ1[0]}.mojibake.txt").exists()


# ── resolve V4.2 ─────────────────────────────────────────────────────────────────────────────────
def test_restated_applies_event_with_bitemporal_record(tmp_path):
    ch, _ = collected(tmp_path, [ADJ1]); tx = chain()[1]
    r = asof_terms.resolve(ch, tx, "2025-08-18")
    t = r["terms"]["conversion_price_krw"]
    assert r["engine"] == "4.2" and r["mode"] == "restated" and r["knowledge_cutoff"] == "2025-12-31" and r["valuation_asof"] == "2025-08-18"
    assert t["value"] == "800" and t["coverage_status"] == "VERIFIED" and t["mode"] == "restated"
    b = t["adjusted_by"]
    assert b["bitemporal"] == {"rcept_dt": "20250820", "effective_at": "20250817", "known_at": "20250820"} and b["effective_fallback"] is False
    assert b["series_evidence"]["row_series"] == "7" and b["sources"][0]["dart_url"].endswith(ADJ1[0])
    assert r["values_are"] == asof_terms.VALUE_BASIS
    with pytest.raises(ValueError): asof_terms.resolve(ch, tx, "2025-08-18", knowledge_cutoff="2025-08-18")


def test_as_known_hides_events_published_after_cutoff(tmp_path):
    ch, _ = collected(tmp_path, [ADJ1]); tx = chain()[1]
    k = asof_terms.resolve(ch, tx, "2025-08-18", mode="as_known")                        # 적용일 8/17, 공시 8/20 → 8/18 에는 몰랐다
    assert k["terms"]["conversion_price_krw"]["value"] == "900" and k["knowledge_cutoff"] == "2025-08-18"
    assert k["unknown_at_cutoff"]["adjustments"] == [{"rcept_no": ADJ1[0], "known_at": "20250820"}]
    later = asof_terms.resolve(ch, tx, "2025-08-18", mode="as_known", knowledge_cutoff="2025-09-01")
    assert later["terms"]["conversion_price_krw"]["value"] == "800" and later["terms"]["conversion_price_krw"]["mode"] == "as_known"


def test_correction_revision_changes_value_from_its_known_at(tmp_path):
    corr = ("20250901000011", "20250901", "[기재정정]" + KOSDAQ, xml([("7", "900", "780")], "2025-08-17"))
    ch, _ = collected(tmp_path, [ADJ1, corr]); tx = chain()[1]
    ev = ch["adjustments"][0]
    assert len(ch["adjustments"]) == 1 and ev["revisions"][0]["rcept_no"] == corr[0] and ev["revisions"][0]["link_rule"] == "same_series+same_effective_dt"
    t = asof_terms.resolve(ch, tx, "2025-08-20")["terms"]["conversion_price_krw"]
    assert t["value"] == "780" and t["adjusted_by"]["revised_by"] == corr[0] and t["adjusted_by"]["bitemporal"]["known_at"] == "20250901"
    assert asof_terms.resolve(ch, tx, "2025-08-20", mode="as_known", knowledge_cutoff="2025-08-25")["terms"]["conversion_price_krw"]["value"] == "800"


def _reasons(ch, T, **kw): return asof_terms.resolve(ch, chain()[1], T, **kw)["terms"]["conversion_price_krw"]["event_coverage"]["reasons"]


def test_c5_price_continuity_and_c7_conflict(tmp_path):
    nxt = ("20251002000012", "20251002", KOSDAQ, xml([("7", "750", "700")], "2025-10-01"))       # 조정전 750 ≠ 계산 800 → 빠진 사건 의심
    ch, _ = collected(tmp_path / "a", [ADJ1, nxt])
    assert any(x.startswith("C5_PRICE_CONTINUITY") for x in _reasons(ch, "2025-09-01"))
    ok = ("20251002000012", "20251002", KOSDAQ, xml([("7", "800", "700")], "2025-10-01"))
    assert _reasons(collected(tmp_path / "b", [ADJ1, ok])[0], "2025-09-01") == []
    dup = ("20250821000013", "20250821", KOSDAQ, xml([("7", "900", "810")], "2025-08-17"))       # 같은 회차·적용일, 다른 값
    assert any(x.startswith("C7_NO_CONFLICT") for x in _reasons(collected(tmp_path / "c", [ADJ1, dup])[0], "2025-09-01"))
    same = ("20250821000013", "20250821", KOSDAQ, xml([("7", "900", "800")], "2025-08-17"))      # 같은 값 = 중복 공시, 출처 합침
    ch3 = collected(tmp_path / "d", [ADJ1, same])[0]
    assert len(ch3["adjustments"]) == 1 and len(ch3["adjustments"][0]["sources"]) == 2 and _reasons(ch3, "2025-09-01") == []


def test_c2_c3_c4_c6(tmp_path):
    ch, _ = collected(tmp_path / "a", [ADJ1])
    assert any(x.startswith("C2_LIST_RANGE") for x in _reasons(ch, "2026-03-01", mode="as_known", knowledge_cutoff="2026-03-01"))
    bad = ("20250701000014", "20250701", KOSDAQ, "<DOCUMENT><P>1. 조정에 관한 사항</P><P>표가 없다</P></DOCUMENT>")
    assert any(x.startswith("C3_ALL_PARSED_OR_UNMATCHED") for x in _reasons(collected(tmp_path / "b", [ADJ1, bad])[0], "2025-09-01"))
    assert any(x.startswith("C4_SERIES_UNAMBIGUOUS") for x in _reasons(collected(tmp_path / "c", [ADJ1], counts={"7": 2})[0], "2025-09-01"))
    orphan = ("20250901000015", "20250901", "[기재정정]" + KOSDAQ, xml([("7", "950", "850")], "2025-07-01"))
    assert any(x.startswith("C6_CORRECTIONS_LINKED") for x in _reasons(collected(tmp_path / "d", [ADJ1, orphan])[0], "2025-09-30"))
    other = ("20250701000016", "20250701", KOSDAQ, xml([("9", "900", "800")], "2025-07-01"))       # 다른 회차 = 미매칭 기록, 실패 아님
    ch5 = collected(tmp_path / "e", [ADJ1, other])[0]
    assert _reasons(ch5, "2025-09-01") == [] and ch5["adjustment_coverage"]["unmatched"][0]["reason"] == "other_series"


def test_not_collected_is_unverified_and_engine41_unchanged():
    ch, tx = chain()
    for c in (ch, A.not_collected(ch, "ADJUSTMENTS_OFF")):
        t = asof_terms.resolve(c, tx, "2025-09-01")["terms"]["conversion_price_krw"]
        assert t["coverage_status"] == asof_terms.COV_UNVERIFIED and t["event_coverage"]["reasons"][0].startswith("C0_COLLECTED")
        assert t["status_ko"] == asof_terms.COV_KO
    v41 = asof_terms.resolve(ch, tx, "2025-09-01", engine="4.1")
    assert v41["engine"] == "4.1" and "coverage_status" not in v41["terms"]["conversion_price_krw"] and "mode" not in v41


def test_unconfirmed_effective_date_is_labelled_disclosure_estimate(tmp_path):
    ch, _ = collected(tmp_path, [ADJ1]); tx = chain()[1]
    t = asof_terms.resolve(ch, tx, "2025-02-10")["terms"]["conversion_price_krw"]          # 기재 정정(소급 가능) 접수 전
    assert t["effective_status"] == "UNCONFIRMED_DEPENDS" and t["contract_effective_confirmed"] is False
    assert t["value_basis"] == asof_terms.VALUE_BASIS and t["value_basis_ko"] == asof_terms.VALUE_BASIS_KO


# ── CLI 기본 경로(G3) ────────────────────────────────────────────────────────────────────────────
def _cli(monkeypatch, capsys, argv):
    from valuationproof import cli
    monkeypatch.setattr(sys, "argv", ["valuationproof"] + argv); cli.main()
    return json.loads(capsys.readouterr().out)


def test_cli_terms_default_path_collects_and_reports_modes(tmp_path, monkeypatch, capsys):
    ch, tx = chain(); fx = tmp_path / "chain.json"; dart_source.export_fixture(ch, tx, fx)
    cache = tmp_path / "ev"; A.collect_corp(CORP, A.chain_start(ch), "20251231", cache, Fake([ADJ1]), sleep=0)   # 가짜 fetch 로 미리 채운 캐시
    base = ["terms", "--cb", ch["origin"], "--asof", "2025-08-18", "--chain-file", str(fx), "--event-cache", str(cache), "--collect-end", "2025-12-31"]
    out = _cli(monkeypatch, capsys, base + ["--adjustments", "cache"])
    by = {x["mode"]: x for x in out["results"]}
    assert out["engine"] == "4.2" and set(by) == {"restated", "as_known"}
    assert by["restated"]["terms"]["conversion_price_krw"]["value"] == "800" and by["restated"]["event_coverage"]["status"] == "VERIFIED"
    assert by["as_known"]["terms"]["conversion_price_krw"]["value"] == "900" and by["as_known"]["terms"]["conversion_price_krw"]["mode"] == "as_known"
    off = _cli(monkeypatch, capsys, base + ["--adjustments", "off", "--mode", "restated"])
    assert off["results"][0]["terms"]["conversion_price_krw"]["coverage_status"] == asof_terms.COV_UNVERIFIED
    miss = _cli(monkeypatch, capsys, base[:-4] + ["--event-cache", str(tmp_path / "none"), "--adjustments", "cache", "--mode", "restated"])
    assert miss["results"][0]["event_coverage"]["status"] == asof_terms.COV_UNVERIFIED            # 캐시 없음 = 확인 필요, 자동 확정 안 함
    v41 = _cli(monkeypatch, capsys, base + ["--engine", "4.1"])
    assert v41["results"][0]["mode"] == "v41" and v41["results"][0]["terms"]["conversion_price_krw"]["coverage_status"] is None


def test_fixture_roundtrip_keeps_collection_record(tmp_path):
    ch, _ = collected(tmp_path, [ADJ1]); tx = chain()[1]; fx = tmp_path / "fx.json"
    dart_source.export_fixture(ch, tx, fx); ch2, tx2 = dart_source.load_fixture(fx)
    a = asof_terms.resolve(ch, tx, "2025-08-18")["terms"]["conversion_price_krw"]
    b = asof_terms.resolve(ch2, tx2, "2025-08-18")["terms"]["conversion_price_krw"]
    assert (a["value"], a["coverage_status"]) == (b["value"], b["coverage_status"]) == ("800", "VERIFIED")
    assert "조정에 관한 사항" not in fx.read_text(encoding="utf-8")                                   # 원문 미포함


# ── 공개본 피드백 버그 3건 회귀 ───────────────────────────────────────────────────────────────────
def _wb(tmp_path, T, values, name):
    w = openpyxl.Workbook(); ws = w.active; ws.title = "Inputs"; demo_v4._sheet(ws, T, "7", values, None)
    p = tmp_path / name; w.save(p); return p


def test_bug1_html_renders_eff_and_cov_rows(tmp_path):
    ch, tx = chain(); T = "2025-02-10"
    cur = {f: t["value"] for f, t in asof_terms.resolve(ch, tx, T, engine="4.1")["terms"].items()}
    cov = audit_v4.audit(_wb(tmp_path, T, cur, "SYNTH_c.xlsx"), ch, tx, "2025-09-01")              # 수집 없음 → COV
    chv, _ = collected(tmp_path, [ADJ1])
    eff = audit_v4.audit(_wb(tmp_path, T, cur, "SYNTH_e.xlsx"), chv, tx, T)                          # 효력일 미확정 → EFF
    for r, cls in ((cov, audit_v4.COV_CLASS), (eff, audit_v4.EFF_CLASS)):
        assert any(x["class"] == cls for x in r["findings"])
        h = audit_v4.to_html(r)                                                                     # V4.1: KeyError
        assert cls in h and audit_v4.to_markdown(r)
        audit_v4.write_reports(r, tmp_path / cls[:2], "r")


def test_bug2_correction_with_original_resolution_date_not_ordered_first():
    """원(결의 12/30) → 정정1(새 결의 2/25) → 정정2(원 결의일 12/30 을 다시 적음, 값 880). V4.1 은 정정2 키를 12/30 으로 둬 값이 빠졌다."""
    vs = [("20250101000001", "20250101", "original", vals(), bform("2024년 12월 30일")),
          ("20250301000002", "20250301", "correction", vals(cp="900"), bform("2025년 02월 25일", cp="900")),
          ("20250401000003", "20250401", "correction", vals(cp="880"), bform("2024년 12월 30일", cp="880"))]
    ch = {"origin": vs[0][0], "corp_code": CORP, "series": "7",
          "versions": [{"rcept_no": r, "rcept_dt": d, "kind": k, "values": v, "link_method": "same_series"} for r, d, k, v, _ in vs]}
    tx = {r: t for r, *_x, t in vs}
    old = asof_terms.version_effective(ch["versions"], tx)
    new = asof_terms.version_effective(ch["versions"], tx, strict_new_resolution=True)
    assert old[2]["basis"] == "new_board_resolution" and old[2]["key"] == "20241230"
    assert new[2]["basis"] == "restatement_earlier_resolution_date" and new[2]["key"] == "20250401" and new[1]["key"] == "20250225"
    assert asof_terms.resolve(ch, tx, "2025-06-30", engine="4.1")["terms"]["conversion_price_krw"]["value"] == "900"   # V4.1 버그 재현
    t = asof_terms.resolve(A.not_collected(ch), tx, "2025-06-30")["terms"]["conversion_price_krw"]
    assert t["value"] == "880"


def test_bug3_blocked_row_disclosure_value_matches_selected_value(tmp_path):
    ch, _ = collected(tmp_path, [ADJ1]); tx = chain()[1]; T = "2025-02-10"
    r = audit_v4.audit(_wb(tmp_path, T, {f: t["value"] for f, t in asof_terms.resolve(ch, tx, T)["terms"].items()} | {"conversion_price_krw": "900"},
                           "SYNTH_b.xlsx"), ch, tx, T)
    f = {x["field"]: x for x in r["findings"] if x["field"]}["conversion_price_krw"]
    term = asof_terms.resolve(ch, tx, T)["terms"]["conversion_price_krw"]
    assert f["class"] == audit_v4.EFF_CLASS and f["disclosure_value"] == term["value"] and f["disclosure_value"] in f["alternative_values"]
    assert f["blocked_auto_class"] in ("불일치", "일치") and f["mode"] == "restated" and f["contract_effective_confirmed"] is False
