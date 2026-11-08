"""V4 기준일 조건 해석기 — 시점 규칙(R1~R5)·위치 근거·추가 조건 추출. 전부 합성 체인·합성 원문(실공시 아님)."""
import pytest

from valuationproof import asof_terms


def form(cp="1,000", floor="700", mat="2028년 01월 05일", conv=("2025년 02월 10일", "2027년 12월 05일"), put=True):
    opt = ("9-1. 옵션에 관한 사항 |\n사채권자는 2026년 01월 10일부터 조기상환청구권(Put Option)을 행사할 수 있다. |\n" if put
           else "9-1. 옵션에 관한 사항 |\n- |\n")
    return ("[기재정정] 머리말 1. 사채의 종류 | 앞쪽 |\n"
            "1. 사채의 종류 | 회차 | 7 | 종류 | 무기명식 사모 전환사채 |\n"
            "2. 사채의 권면(전자등록)총액 (원) | 5,000,000,000 |\n"
            "표면이자율 (%) | 0.0 |\n만기이자율 (%) | 3.0 |\n"
            f"사채만기일 | {mat} |\n"
            f"전환가액 (원/주) | {cp} |\n"
            f"전환청구기간 | 시작일 | {conv[0]} | 종료일 | {conv[1]} |\n"
            f"최저 조정가액 (원) | {floor} |\n"
            "납입일 | 2025년 01월 10일 |\n" + opt + "10. 기타 | - |\n")


def vals(cp="1000", floor="700", mat="2028-01-05"):
    return {"principal_krw": "5000000000", "conversion_price_krw": cp, "coupon_rate_pct": "0.0", "redemption_yield_pct": "3.0",
            "maturity_date": mat, "issue_date": "2025-01-10", "refixing_floor_krw": floor}


@pytest.fixture
def chain_texts():
    vs = [("20250101000001", "20250101", "original", vals(), form()),
          ("20250301000002", "20250301", "correction", vals(cp="900"), form(cp="900")),
          ("20250301000003", "20250301", "correction", vals(cp="900", floor="650"), form(cp="900", floor="650")),
          ("20250601000004", "20250601", "correction", vals(cp="900", floor="650", mat="2028-03-05"),
           form(cp="900", floor="650", mat="2028년 03월 05일", conv=("2025년 02월 10일", "2028년 02월 05일")))]
    chain = {"origin": vs[0][0], "corp_code": "SYNTH01", "series": "7",
             "versions": [{"rcept_no": r, "rcept_dt": d, "kind": k, "values": v, "link_method": "same_series"} for r, d, k, v, _ in vs]}
    return chain, {r: t for r, *_x, t in vs}


def test_r1_r2_last_filed_on_or_before_asof(chain_texts):
    ch, tx = chain_texts
    r = asof_terms.resolve(ch, tx, "2025-04-15")
    assert r["status"] == "RESOLVED" and r["selected_version"]["rcept_no"] == "20250301000003"
    assert [x["rcept_no"] for x in r["excluded_after_asof"]] == ["20250601000004"]
    assert r["terms"]["maturity_date"]["value"] == "2028-01-05"                      # 6월 정정 값은 새지 않는다
    assert r["terms"]["maturity_date"]["changes_after_asof"][0]["value"] == "2028-03-05"
    assert r["terms"]["conversion_end_date"]["value"] == "2027-12-05"


def test_correction_filed_on_asof_is_included(chain_texts):
    ch, tx = chain_texts
    r = asof_terms.resolve(ch, tx, "20250601")                                       # 접수일 = 기준일 → 포함(<=)
    assert r["selected_version"]["rcept_no"] == "20250601000004" and not r["excluded_after_asof"]
    assert r["terms"]["maturity_date"]["value"] == "2028-03-05"
    assert asof_terms.resolve(ch, tx, "2025-05-31")["terms"]["maturity_date"]["value"] == "2028-01-05"


def test_same_day_versions_take_later_one(chain_texts):
    ch, tx = chain_texts
    r = asof_terms.resolve(ch, tx, "2025-03-01")
    assert r["selected_version"]["rcept_no"] == "20250301000003" and r["terms"]["refixing_floor_krw"]["value"] == "650"


def test_r3_before_origin_not_yet_filed(chain_texts):
    ch, tx = chain_texts
    r = asof_terms.resolve(ch, tx, "2024-12-31")
    assert r["status"] == "NOT_YET_FILED" and r["selected_version"] is None and r["review_required"] is True
    assert len(r["excluded_after_asof"]) == 4


def test_established_in_and_changed_since_origin(chain_texts):
    ch, tx = chain_texts
    t = asof_terms.resolve(ch, tx, "2025-12-31")["terms"]
    assert t["conversion_price_krw"]["established_in"]["rcept_no"] == "20250301000002" and t["conversion_price_krw"]["changed_since_origin"]
    assert t["principal_krw"]["established_in"]["rcept_no"] == "20250101000001" and not t["principal_krw"]["changed_since_origin"]
    assert t["conversion_price_krw"]["source"]["rcept_no"] == "20250601000004"       # 값 근거는 선택된 버전 원문


def test_r4_r5_recovered_link_and_effective_date(chain_texts):
    ch, tx = chain_texts
    ch["versions"][-1]["link_status"] = "recovered_by_rule_signals(not_human_verified)"
    r = asof_terms.resolve(ch, tx, "2025-12-31")
    assert all(t["link_review_required"] for t in r["terms"].values()) and r["review_required"]
    assert all(t["contract_effective_confirmed"] is False for t in r["terms"].values())
    assert "납입일 뒤 정정" in r["terms"]["maturity_date"]["contract_effective_note"]


def test_locator_points_at_value_text(chain_texts):
    ch, tx = chain_texts
    r = asof_terms.resolve(ch, tx, "2025-04-15")
    src = r["terms"]["conversion_price_krw"]["source"]; loc = src["locator"]; text = tx[src["rcept_no"]]
    assert text[loc["char_start"]:loc["char_end"]] == "900"
    assert text.splitlines()[loc["line"] - 1].startswith("전환가액")
    assert loc["line"] > 1                                                            # 머리말의 '1. 사채의 종류' 가 아니라 서식 기준
    assert src["doc_sha256"] and len(src["doc_sha256"]) == 64 and src["dart_url"].endswith(src["rcept_no"])
    e = r["terms"]["conversion_end_date"]["source"]["locator"]
    assert text[e["char_start"]:e["char_end"]] == "2027년 12월 05일"


def test_options_extraction():
    o = asof_terms.extract_options(form(put=True))
    assert o["put_option"]["present"] is True and o["put_option"]["first_date_candidate"] == "2026-01-10"
    assert o["call_option"]["status"].startswith("keyword_absent")
    assert asof_terms.extract_options(form(put=False))["put_option"]["status"] == "section_empty"
    assert asof_terms.extract_options("1. 사채의 종류 | 회차 | 7 |\n")["put_option"]["status"] == "section_missing"


def test_options_always_review_and_no_text_fallback(chain_texts):
    ch, _ = chain_texts
    r = asof_terms.resolve(ch, {}, "2025-12-31")                                     # 원문 없음 → core 값만, 위치 None
    assert r["terms"]["conversion_price_krw"]["value"] == "900" and r["terms"]["conversion_price_krw"]["source"]["locator"] is None
    assert r["terms"]["conversion_start_date"]["value"] is None and r["terms"]["conversion_start_date"]["review_required"]
    assert r["terms"]["put_option"]["review_required"] is True


def test_find_chain_errors(chain_texts):
    ch, _ = chain_texts
    chains = {ch["origin"]: ch}
    assert asof_terms.find_chain(chains, "SYNTH01", "20250301000002") is ch          # 정정 접수번호로도 찾는다
    with pytest.raises(KeyError, match="CB_CHAIN_NOT_FOUND"): asof_terms.find_chain(chains, None, "20990101000009")
    with pytest.raises(ValueError, match="CORP_MISMATCH"): asof_terms.find_chain(chains, "OTHER", ch["origin"])
