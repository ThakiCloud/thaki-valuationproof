"""V4 데모 — 공개 공시 체인 1개 + **합성** 평가 Excel 4종 → 기준일 조건 대조 보고서.

  1 SYNTH_normal               기준일 유효 공시 값 그대로(억원 단위·전환청구기간·풋 포함) → 오류 0 기대
  2 SYNTH_stale_correction     기준일 직전 정정이 반영되지 않은 모형(그 전 버전 값) → 정정 미반영 기대
  3 SYNTH_refix_maturity_typo  리픽싱 하한·만기 오기재 → 불일치 2 기대
  4 SYNTH_transposed_synonym   2회차 전치 표 + 사전 밖 라벨 → 오류 확정 0(미확인으로만) 기대 — 알려진 약점 처리 확인
⛔ Excel 은 전부 합성(파일명 SYNTH_*). 실제 고객 평가모형이 아니다. 결과는 '합성 데모' 로만 표기한다.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from . import asof_terms, audit_v4


def _d(s: str) -> dt.date: return dt.date(int(s[:4]), int(s[5:7]), int(s[8:10]))


def pick_asof(chain: dict) -> str:
    """정정이 2개 이상이면 마지막에서 두 번째 정정 접수일 + 1일(뒤 정정 1개 이상 제외됨), 아니면 마지막 접수일 + 1일."""
    vs = chain["versions"]
    v = vs[-2] if len(vs) >= 3 else vs[-1]
    d = dt.date(int(v["rcept_dt"][:4]), int(v["rcept_dt"][4:6]), int(v["rcept_dt"][6:])) + dt.timedelta(days=1)
    return d.isoformat()


def _vals_at(chain: dict, texts: dict, idx: int) -> dict:
    v = chain["versions"][idx]; t = asof_terms.terms_for(v, texts)
    return {f: t[f]["value"] for f in asof_terms.ALL_FIELDS}


def _sheet(ws, as_of: str, series: str, vals: dict, labels: dict | None = None):
    L = {"principal_krw": "권면총액 (억원)", "conversion_price_krw": "전환가액 (원/주)", "refixing_floor_krw": "최저조정가액",
         "coupon_rate_pct": "표면이자율", "redemption_yield_pct": "만기보장수익률", "maturity_date": "사채만기일", "issue_date": "납입일",
         "conversion_start_date": "전환청구 시작일", "conversion_end_date": "전환청구 종료일", "put_option": "조기상환청구권(Put)"} | (labels or {})
    ws["A1"] = "평가기준일"; ws["B1"] = _d(as_of); ws["B1"].number_format = "yyyy-mm-dd"
    ws["A2"] = f"[제{series}회 무기명식 이권부 무보증 사모 전환사채]"; r = 3
    for f, lab in L.items():
        v = vals.get(f)
        if v is None or (f == "put_option" and not v.get("present")): continue
        ws.cell(r, 1, lab)
        if f == "principal_krw": c = ws.cell(r, 2, int(v) / 1e8); c.number_format = "#,##0.####"
        elif f.endswith("_pct"): c = ws.cell(r, 2, float(v) / 100); c.number_format = "0.00%"
        elif f.endswith("_date"): c = ws.cell(r, 2, _d(v)); c.number_format = "yyyy-mm-dd"
        elif f == "put_option": c = ws.cell(r, 2, _d(v["first_date_candidate"]) if v.get("first_date_candidate") else "Y"); c.number_format = "yyyy-mm-dd"
        else: ws.cell(r, 2, int(v))
        r += 1


def build(chain: dict, texts: dict, out: Path, as_of: str) -> list[dict]:
    import openpyxl
    out.mkdir(parents=True, exist_ok=True)
    res = asof_terms.resolve(chain, texts, as_of); sel = res["selected_version"]["index"]; ser = chain["series"]
    cur = {f: t["value"] for f, t in res["terms"].items()}
    cases = []
    # 1 정상
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Inputs"; _sheet(ws, as_of, ser, cur)
    p = out / "SYNTH_normal.xlsx"; wb.save(p); cases.append({"name": "정상", "file": p, "expect": {"errors": 0}})
    # 2 정정 미반영: 기준일 직전 버전(sel)의 정정을 빼먹은 모형 = sel-1 버전 값
    if sel >= 1:
        prev = _vals_at(chain, texts, sel - 1)
        changed = sorted(f for f in asof_terms.CORE_FIELDS + asof_terms.EXTRA_FIELDS if prev.get(f) != cur.get(f) and prev.get(f) is not None)
        wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Inputs"; _sheet(ws, as_of, ser, cur | {f: prev[f] for f in changed})
        p = out / "SYNTH_stale_correction.xlsx"; wb.save(p)
        cases.append({"name": "정정 미반영", "file": p, "expect": {"stale_fields": changed, "missed_correction": chain["versions"][sel]["rcept_no"]}})
    # 3 리픽싱·만기 오기재
    bad = dict(cur)
    if cur.get("refixing_floor_krw"): bad["refixing_floor_krw"] = str(int(cur["refixing_floor_krw"]) + 70)
    m = _d(cur["maturity_date"]); bad["maturity_date"] = m.replace(year=m.year + 1).isoformat()
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Inputs"; _sheet(ws, as_of, ser, bad)
    p = out / "SYNTH_refix_maturity_typo.xlsx"; wb.save(p)
    cases.append({"name": "리픽싱·만기 오기재", "file": p, "expect": {"mismatch_fields": [f for f in ("refixing_floor_krw", "maturity_date") if bad.get(f) != cur.get(f)]}})
    # 4 전치 표(2회차) + 사전 밖 라벨 — 오탐 확정이 없어야 한다
    other = str(int(ser) + 1)
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "CB_terms"
    ws["A1"] = "평가기준일"; ws["B1"] = _d(as_of); ws["B1"].number_format = "yyyy-mm-dd"
    cols = [("회차", None), ("권면총액", "principal_krw"), ("전환가액", "conversion_price_krw"), ("최저조정가액", "refixing_floor_krw"),
            ("사채만기일", "maturity_date"), ("납입일", "issue_date"), ("이표율", "coupon_rate_pct")]   # 마지막 = 사전·키워드 규칙 밖 라벨
    for j, (lab, _) in enumerate(cols, 1): ws.cell(3, j, lab)
    for i, s in enumerate([other, ser]):
        ws.cell(4 + i, 1, f"제{s}회")
        for j, (_, f) in enumerate(cols[1:], 2):
            v = cur.get(f)
            if v is None: continue
            if s != ser: v = str(int(v) * 2) if f.endswith("_krw") else (str(float(v) + 1) if f.endswith("_pct") else _d(v).replace(year=_d(v).year + 2).isoformat())
            c = ws.cell(4 + i, j, _d(v) if f.endswith("_date") else float(v) / 100 if f.endswith("_pct") else int(v))
            c.number_format = "yyyy-mm-dd" if f.endswith("_date") else "0.00%" if f.endswith("_pct") else "#,##0"
    p = out / "SYNTH_transposed_synonym.xlsx"; wb.save(p)
    cases.append({"name": "전치 표 + 사전 밖 라벨", "file": p, "expect": {"errors": 0, "unverified_fields": ["coupon_rate_pct"]}})
    return cases


def check(case: dict, r: dict) -> bool:
    e = case["expect"]; by = {x["field"]: x for x in r["findings"] if x["field"]}
    if "errors" in e and r["errors_found"] != e["errors"]: return False
    for f in e.get("stale_fields", []):
        if by[f]["class"] != "불일치" or not str(by[f]["subtype"]).startswith("정정 미반영"): return False
    for f in e.get("mismatch_fields", []):
        if by[f]["class"] != "불일치": return False
    for f in e.get("unverified_fields", []):
        if by[f]["class"] != "미확인": return False
    return True


def run(chain: dict, texts: dict, out: str | Path, as_of: str | None = None, data_kind: str = "") -> dict:
    out = Path(out); as_of = as_of or pick_asof(chain); rows = []
    for c in build(chain, texts, out, as_of):
        r = audit_v4.audit(c["file"], chain, texts, as_of)
        r["data_kind"] = data_kind or "공개 공시(OpenDART) 체인 + 합성 Excel(SYNTH_*)"
        if chain.get("synthetic"):   # public build: never label a synthetic chain as a public disclosure
            r["claim_boundary"] = "[합성 데이터 — 가상 발행사·가상 공시] " + str(r.get("claim_boundary", "")).replace("공개 공시(OpenDART) 기준", "합성 공시 체인 기준")
        stem = c["file"].stem
        paths = audit_v4.write_reports(r, out, stem + "_report", f"[합성 데모] {c['name']} — {c['file'].name}")
        errs = [x for x in r["findings"] if x["class"] in ("불일치", "누락")]
        rows.append({"case": c["name"], "workbook": c["file"].name, "as_of": as_of, "selected_version": r["selected_version"]["rcept_no"],
                     "excluded_after_asof": [x["rcept_no"] for x in r["excluded_after_asof"]], "counts": r["counts"],
                     "errors_found": r["errors_found"], "errors": [{"field": x["field"], "subtype": x["subtype"], "cell": x["excel_cell"],
                     "excel": x["excel_value"], "disclosure": x["disclosure_value"],
                     "rcept_no": next((ev["rcept_no"] for ev in x["evidence"] if ev.get("rcept_no")), None)} for x in errs],
                     "unverified": [{"field": x["field"], "subtype": x["subtype"]} for x in r["findings"] if x["class"] == "미확인"],
                     "expectation_met": check(c, r), "reports": paths})
    summ = {"SYNTHETIC_EXCEL": True, "chain_origin": chain["origin"], "corp_code": chain.get("corp_code"), "series": chain.get("series"),
            "as_of": as_of, "cases": rows, "all_expectations_met": all(x["expectation_met"] for x in rows)}
    (out / "demo_summary.json").write_text(json.dumps(summ, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return summ
