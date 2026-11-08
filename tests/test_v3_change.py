"""V3 변경 추적기 단위 테스트 — 전부 합성 공시 텍스트(실제 DART 원문 아님)."""
import hashlib
import inspect

from valuationproof import change_v3, dart_change
from valuationproof._norm import default_norm as norm


def form(series, cp="1000", mat="2028년 01월 05일", issue="2025년 01월 10일"):
    return "\n".join(["1. 사채의 종류 |", "회 차 |", f"{series} |", "2. 사채의 권면(전자등록)총액 (원) | 5,000,000,000 |",
                      "표면이자율 (%) | 0.0 |", "만기이자율 (%) | 3.0 |", f"사채만기일 | {mat} |", f"전환가액 (원/주) | {cp} |",
                      "최저 조정가액 (원) | 700 |", f"납입일 | {issue} |"])


def corr(first, series, cp, before_cp=None, mat="2028년 01월 05일"):
    head = [f"최초제출일 : {first}", "정정사항 |", "8. 전환가액 (원/주) |", f"정정 전 | {before_cp or '-'} | 정정 후 | {cp} |"]
    return "\n".join(head) + "\n" + form(series, cp=cp, mat=mat)


def M(r, corp, is_corr, nm=None): return {"rcept_no": r, "rcept_dt": r[:8], "corp_code": corp, "is_correction": is_corr, "report_nm": nm or ("[기재정정]x" if is_corr else "x")}


def test_dart_change_contract_unchanged():
    # Track E 가 import 하는 V2 시그니처·정규식 — V3 는 감싸기만 한다
    assert str(inspect.signature(dart_change.form_text)) == "(text: 'str') -> 'str'"
    assert str(inspect.signature(dart_change.parse_correction)) == "(text: 'str') -> 'dict'"
    assert str(inspect.signature(dart_change.series)) == "(text: 'str') -> 'str | None'"
    assert hashlib.sha256(dart_change._FIRST.pattern.encode()).hexdigest()[:16] == "ed722559ee49bb98"
    assert hashlib.sha256(dart_change._SERIES.pattern.encode()).hexdigest()[:16] == "719800bd4bfe2166"


def test_first_date_alt_format_and_series_alt():
    assert change_v3.first_date_v3("최초제출일 : 2025-01-02")[1] == "v2"
    assert change_v3.first_date_v3("최초제출일 : 2025,07,15") == ("20250715", "alt_format")
    assert change_v3.series_v3(form("7"))[0] == "7"
    assert change_v3.series_v3("제12회 무기명식 이권부 무보증 사모 전환사채")== ("12", "alt")


def _data():
    o1 = "20250101000001"; o2 = "20250101000002"; o3 = "20250201000003"
    c_off = "20250110000010"     # 최초제출일 1/2 (원공시 접수 1/1, 1일 차이) + 회차 일치 → recovered
    c_amb = "20250111000011"     # 같은 날 원공시 2개, 회차 안 읽힘 → review
    c_att = "20250112000012"     # 첨부정정, 원문 없음 → unrecoverable
    c_conf = "20250210000013"    # 후보 1개인데 회차 다름 → V2 series_conflict → review(never_auto)
    c_out = "20250301000014"     # 원공시 데이터에 없음 → unrecoverable
    meta = [M(o1, "A", False), M(o2, "A", False), M(o3, "B", False), M(c_off, "A", True), M(c_amb, "A", True),
            M(c_att, "A", True, "[첨부정정]x"), M(c_conf, "B", True), M(c_out, "C", True)]
    texts = {o1: form("7"), o2: form("8"), o3: form("3"),
             c_off: corr("2025-01-02", "7", "900", before_cp="1000"),
             c_amb: "최초제출일 : 2025-01-01\n정정사항 |\n" + form("").replace("회 차 |\n |", "회 차 |\n- |"),
             c_conf: corr("2025-02-01", "4", "900"), c_out: corr("2024-12-01", "1", "900")}
    return meta, texts, dict(o1=o1, o2=o2, o3=o3, c_off=c_off, c_amb=c_amb, c_att=c_att, c_conf=c_conf, c_out=c_out)


def test_recovery_types_and_no_auto_confirm():
    meta, texts, k = _data()
    res = change_v3.build_chains_v3(meta, texts, norm)
    by = {x["rcept_no"]: x for x in res["recovery"]}
    assert by[k["c_off"]]["status"] == "recovered" and by[k["c_off"]]["linked_origin"] == k["o1"]
    assert by[k["c_off"]]["type"] == "first_date_off_by_days" and by[k["c_off"]]["signals"]["series"] == "agree"
    assert by[k["c_amb"]]["status"] == "review"
    assert by[k["c_att"]]["type"] == "attachment_correction_no_main_form" and by[k["c_att"]]["status"] == "unrecoverable"
    assert by[k["c_conf"]]["type"] == "series_conflict" and by[k["c_conf"]]["status"] == "review"
    assert by[k["c_out"]]["status"] == "unrecoverable"
    # review 항목은 체인에 붙지 않는다 · 큐는 PENDING
    linked = {v["rcept_no"] for c in res["chains"].values() for v in c["versions"]}
    assert k["c_amb"] not in linked and k["c_conf"] not in linked and k["c_off"] in linked
    assert {q["rcept_no"] for q in res["review_queue"]} == {k["c_amb"], k["c_conf"]}
    assert all(q["status"] == "PENDING" for q in res["review_queue"])
    v = next(v for v in res["chains"][k["o1"]]["versions"] if v["rcept_no"] == k["c_off"])
    assert v["link_status"].endswith("(not_human_verified)") and v["link_method"].startswith("v3_recovery")


def test_structural_diff_and_effective_basis():
    meta, texts, k = _data()
    ch = change_v3.build_chains_v3(meta, texts, norm)["chains"][k["o1"]]
    d = {x["field"]: x for x in ch["structural_diff"]}
    assert d["conversion_price_krw"]["before"] == "1000" and d["conversion_price_krw"]["after"] == "900"
    assert d["conversion_price_krw"]["unit"] == "KRW/share" and d["conversion_price_krw"]["review_required"]
    eff = d["conversion_price_krw"]["contract_effective"]
    assert eff["basis"] == "pre_issuance_amendment" and eff["confirmed"] is False   # 1/10 접수 ≤ 납입일 1/10
    assert d["conversion_price_krw"]["disclosure_time"]["rcept_dt"] == k["c_off"][:8]


def test_validate_chain_flags():
    ch = {"versions": [{"rcept_no": "20250105000001", "rcept_dt": "20250105", "values": {}},
                       {"rcept_no": "20250102000002", "rcept_dt": "20250103", "values": {}}]}
    meta_by = {"20250105000001": {"corp_code": "A"}, "20250102000002": {"corp_code": "B"}}
    v = change_v3.validate_chain(ch, meta_by, {"20250105000001": form("7"), "20250102000002": form("8")})
    codes = {i["code"] for i in v["issues"]}
    assert {"corp_mismatch", "correction_before_origin", "series_inconsistent_across_versions", "rcept_dt_differs_from_rcept_no_date"} <= codes
    assert v["ok"] is False and v["series"] is None
