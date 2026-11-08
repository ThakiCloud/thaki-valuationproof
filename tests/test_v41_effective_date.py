"""[V4.2 에서 engine="4.1" 고정 — V4.1 동작 재현 회귀 시험] V4.1 효력일 키(R6)·효력일 미확정 자동 판정 금지(R7)·전환가액 조정 반영(R8). 전부 합성 체인·합성 원문(실공시 아님)."""
import openpyxl

from valuationproof import asof_terms, audit_v4, demo_v4
from test_v4_asof import form, vals

ADJ = ("전환가액의 조정\n1. 조정에 관한 사항 |\n회차 |\n상장여부 |\n조정전 전환가액 (원) |\n조정후 전환가액 (원) |\n"
       "7 |\n비상장 |\n900 |\n800 |\n2. 전환가능주식수 변동 |\n3. 조정사유 |\n시가하락에 따른 전환가액의 조정 |\n"
       "5. 조정가액 적용일 |\n{eff} |\n6. 이사회결의일(결정일) |\n- |\n")


def bform(bd, **kw):
    """서식 끝에 이사회결의일(결정일) 줄을 붙인 합성 원문."""
    return form(**kw) + f"17. 이사회결의일(결정일) | {bd} |\n"


def chain(board=True, restate=False):
    o_bd, c_bd = "2024년 12월 30일", ("2024년 12월 30일" if restate else "2025년 02월 25일")
    vs = [("20250101000001", "20250101", "original", vals(), bform(o_bd) if board else form()),
          ("20250301000002", "20250301", "correction", vals(cp="900"), bform(c_bd, cp="900") if board else form(cp="900"))]
    ch = {"origin": vs[0][0], "corp_code": "SYNTH01", "series": "7",
          "versions": [{"rcept_no": r, "rcept_dt": d, "kind": k, "values": v, "link_method": "same_series"} for r, d, k, v, _ in vs]}
    return ch, {r: t for r, *_x, t in vs}


def test_board_date_and_adjustment_parsers():
    assert asof_terms.board_date(bform("2024년 12월 30일")) == "20241230"
    assert asof_terms.board_date(form()) is None
    p = asof_terms.parse_adjustment(ADJ.format(eff="2025-08-17"))
    assert p["rows"] == [{"series": "7", "before_krw": "900", "after_krw": "800"}]
    assert p["effective_dt"] == "20250817" and p["board_dt"] is None


def test_r6_keys_on_disclosed_board_date_with_fallback_flag():
    ch, tx = chain()
    r = asof_terms.resolve(ch, tx, "2025-02-27", engine="4.1")                     # 새 결의일(2/25) 뒤, 접수일(3/1) 전
    assert r["selected_version"]["rcept_no"] == "20250301000002"
    t = r["terms"]["conversion_price_krw"]
    assert t["value"] == "900" and t["date_key"]["basis"] == "new_board_resolution" and t["date_key"]["date_key_fallback"] is False
    assert t["effective_status"] == "UNCONFIRMED_DEPENDS" and t["status_ko"] == asof_terms.EFF_UNCONFIRMED   # 결의일~접수일 사이 = 미확정
    old = asof_terms.resolve(ch, tx, "2025-02-27", date_key="rcept_dt", engine="4.1")
    assert old["selected_version"]["rcept_no"] == "20250101000001" and old["terms"]["conversion_price_krw"]["value"] == "1000"
    ch2, tx2 = chain(board=False)
    t2 = asof_terms.resolve(ch2, tx2, "2025-03-05", engine="4.1")["terms"]["conversion_price_krw"]
    assert t2["date_key"]["date_key_fallback"] is True and t2["date_key"]["basis"].startswith("restatement")
    assert t2["effective_status"] == "NOT_DEPENDENT"                  # 기준일이 접수일 뒤면 소급 여부와 무관


def test_r7_restatement_filed_after_asof_is_unconfirmed():
    ch, tx = chain(restate=True)                                      # 결의일 같음 = 기재 정정(소급 가능)
    r = asof_terms.resolve(ch, tx, "2025-02-10", engine="4.1")
    t = r["terms"]["conversion_price_krw"]
    assert t["value"] == "1000" and t["effective_status"] == "UNCONFIRMED_DEPENDS" and set(t["alternative_values"]) == {"1000", "900"}
    assert r["terms"]["principal_krw"]["effective_status"] == "NOT_DEPENDENT"    # 정정이 안 바꾼 필드는 자동 판정 가능
    assert "conversion_price_krw" in r["unconfirmed_fields"] and t["review_required"] and t["contract_effective_confirmed"] is False


def test_r8_adjustment_keyed_on_effective_date_not_filing_date():
    ch, tx = chain()
    adj = {"rcept_no": "20250818900001", "rcept_dt": "20250818", "corp_code": "SYNTH01", "parsed": asof_terms.parse_adjustment(ADJ.format(eff="2025-08-17"))}
    ch["adjustments"] = asof_terms.adjustments_for(ch, [adj])
    on_eff = asof_terms.resolve(ch, tx, "2025-08-17", engine="4.1")["terms"]["conversion_price_krw"]
    assert on_eff["value"] == "800" and on_eff["adjusted_by"]["effective_dt"] == "20250817" and on_eff["effective_status"] == "NOT_DEPENDENT"
    assert asof_terms.resolve(ch, tx, "2025-08-17", date_key="rcept_dt", engine="4.1")["terms"]["conversion_price_krw"]["value"] == "900"
    assert asof_terms.resolve(ch, tx, "2025-08-16", engine="4.1")["terms"]["conversion_price_krw"]["value"] == "900"
    ch["adjustments"][0]["effective_dt"] = None                       # 적용일 판독 실패 → 접수일 fallback + 미확정 구간
    t = asof_terms.resolve(ch, tx, "2025-08-10", engine="4.1")["terms"]["conversion_price_krw"]
    assert t["value"] == "900" and t["effective_status"] == "UNCONFIRMED_DEPENDS"


def _wb(tmp_path, T, values, name):
    w = openpyxl.Workbook(); ws = w.active; ws.title = "Inputs"; demo_v4._sheet(ws, T, "7", values, None)
    p = tmp_path / name; w.save(p); return p


def _cur(ch, tx, T): return {f: t["value"] for f, t in asof_terms.resolve(ch, tx, T, engine="4.1")["terms"].items()}


def test_audit_blocks_auto_verdict_when_effective_date_unconfirmed(tmp_path):
    ch, tx = chain(restate=True); T = "2025-02-10"
    for cp, name in (("1000", "SYNTH_m.xlsx"), ("900", "SYNTH_n.xlsx")):      # 어느 쪽 값이든 자동 일치/불일치 금지
        r = audit_v4.audit(_wb(tmp_path, T, _cur(ch, tx, T) | {"conversion_price_krw": cp}, name), ch, tx, T, engine="4.1")
        f = {x["field"]: x for x in r["findings"] if x["field"]}["conversion_price_krw"]
        assert f["class"] == audit_v4.EFF_CLASS and f["approval_required"] and f["auto_verdict_allowed"] is False
        assert f["human_review_required"] and f["suggestion"]["requires_approval"] and not f["suggestion"]["auto_applied"]
        assert r["counts"][audit_v4.EFF_CLASS] >= 1 and r["effective_date_unconfirmed"] >= 1 and r["auto_fix_applied"] == 0
    T2 = "2025-06-30"                                                            # 정정 접수 뒤 → 기존 자동 판정 유지
    r2 = audit_v4.audit(_wb(tmp_path, T2, _cur(ch, tx, T2), "SYNTH_o.xlsx"), ch, tx, T2, engine="4.1")
    assert {x["field"]: x for x in r2["findings"] if x["field"]}["conversion_price_krw"]["class"] == "일치"


def test_audit_uses_adjustment_price(tmp_path):
    ch, tx = chain(); T = "2025-09-30"
    ch["adjustments"] = asof_terms.adjustments_for(ch, [{"rcept_no": "20250818900001", "rcept_dt": "20250818", "corp_code": "SYNTH01",
                                                          "parsed": asof_terms.parse_adjustment(ADJ.format(eff="2025-08-17"))}])
    v = _cur(ch, tx, T)
    ok = {x["field"]: x for x in audit_v4.audit(_wb(tmp_path, T, v, "SYNTH_p.xlsx"), ch, tx, T, engine="4.1")["findings"] if x["field"]}
    assert ok["conversion_price_krw"]["class"] == "일치"
    bad = {x["field"]: x for x in audit_v4.audit(_wb(tmp_path, T, v | {"conversion_price_krw": "900"}, "SYNTH_q.xlsx"), ch, tx, T, engine="4.1")["findings"] if x["field"]}
    assert bad["conversion_price_krw"]["class"] == "불일치" and "전환가액 조정" in bad["conversion_price_krw"]["subtype"]
