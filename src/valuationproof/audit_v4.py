"""ValuationProof V4 — 기준일 조건 선택 → Excel 셀 대조 → 오류 근거 보고서 (한 번에, 결정론, 읽기 전용).

흐름: asof_terms.resolve(체인, 원문, T) → excel_v3.audit_workbook_v3(같은 T) → V4 분류 + 추가 조건(전환청구기간·풋·콜) 대조
     → 보고서(JSON + Markdown + HTML). Excel 은 수정하지 않는다(감사 전후 sha256 비교). 자동 수정 없음 — 제안만, 승인 필요.

V4 분류(class):
  일치      Excel 값 == 기준일 유효 공시 값
  불일치    값이 다름 / 정정 미반영(이전 버전 값) / 기준일 뒤 정정 선반영 / 단위 오류 / 회차 오연결
  누락      공시에 있는 조건 셀을 찾지 못했고, 사전 밖 라벨일 가능성도 없음 (그래도 사람 확인 필수 — 확정 아님)
  미확인    판정 불가(외부참조·단위 모호·충돌·회차 불명·사전 밖 라벨 가능성·Excel 평가기준일 불일치 등) → 사람 검토
  해당없음  공시에도 모형에도 없음
  확인 필요(효력일 미확정)  (4.1) 효력일이 공시로 확정되지 않아 기준일 선택이 갈리는 필드 — 자동 일치/불일치/누락 판정을 내지 않는다.
            원래 규칙 결과는 blocked_auto_class 로만 남기고 사람 승인(approval_required)을 요구한다. asof_terms R7.
⛔ 알려진 약점 처리:
  - 사전 밖 동의어(V3 hidden 오경보 0.93): V3 가 missing_in_model 을 내더라도, 워크북에 사전이 못 읽은 라벨-값 쌍이 있고
    그 값이 그 필드의 형식(날짜/금액/퍼센트)과 맞으면 '누락'으로 확정하지 않고 '미확인(사전 밖 라벨 가능성)'으로 보낸다.
  - 전치 표 회차 태그 유실: excel_v3 에서 고쳤다(V4). 그래도 회차가 안 읽히면 V3 가 conflicting_inputs/series_unresolved
    (REVIEW_REQUIRED) 를 내고 여기서 '미확인' 이 된다 — 오탐 확정값을 내지 않는다.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import html
import json
import re
from collections import Counter
from pathlib import Path

from . import asof_terms, excel_v3, ledger

VERSION = "4.1.0"
EFF_CLASS = asof_terms.EFF_UNCONFIRMED
CLASSES = ("일치", "불일치", "누락", "미확인", "해당없음", EFF_CLASS)
AUTO_CLASSES = ("일치", "불일치", "누락")
HIGH_FIELDS = {"conversion_price_krw", "refixing_floor_krw", "principal_krw", "maturity_date", "put_option", "call_option"}
EXTRA_SYN = {
    "conversion_start_date": ["전환청구시작일", "전환청구기간시작일", "전환청구개시일", "전환개시일", "전환가능시작일", "전환청구가능일",
                              "conversionstart", "conversionperiodstart", "conversionstartdate"],
    "conversion_end_date": ["전환청구종료일", "전환청구기간종료일", "전환청구만료일", "전환종료일", "전환가능종료일",
                            "conversionend", "conversionperiodend", "conversionenddate"],
    "put_option": ["조기상환청구권", "조기상환청구", "조기상환청구개시일", "조기상환청구시작일", "풋옵션", "풋옵션행사개시일",
                   "put", "putoption", "putdate", "putstart", "investorput"],
    "call_option": ["매도청구권", "매도청구", "매도청구개시일", "콜옵션", "콜옵션행사개시일", "call", "calloption", "calldate",
                    "callstart", "issuercall"],
}
_EXTRA_KEYS = {f: {excel_v3._key(s) for s in v} for f, v in EXTRA_SYN.items()}
_YES = {"y", "yes", "o", "true", "있음", "유", "해당", "해당있음", "적용", "1"}
_NO = {"n", "no", "x", "false", "없음", "무", "미해당", "해당없음", "미적용", "0", "-"}


def _sha(b: bytes) -> str: return hashlib.sha256(b).hexdigest()


def _clean(v):
    if isinstance(v, _dt.datetime): return v.date().isoformat()
    v = excel_v3._jsonable(v)
    if isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}T00:00:00", v): return v[:10]
    return v


def _sev(field: str | None, cls: str, sub: str | None = None) -> str:
    if cls in ("일치", "해당없음"): return "none"
    if cls == "불일치": return "high" if (field in HIGH_FIELDS or sub in ("정정 미반영", "단위 오류", "회차 오연결")) else "medium"
    if cls == "누락": return "high" if field in ("refixing_floor_krw", "conversion_price_krw") else "medium"
    return "high" if sub == "Excel 평가기준일 불일치" else "medium"


# ── 워크북 보조 스캔: 사전이 못 읽은 라벨-값 쌍 · 추가 조건 라벨 · Excel 평가기준일 ─────────────────────────
def _kind_of(v) -> str | None:
    if isinstance(v, (_dt.date, _dt.datetime)): return "date"
    if isinstance(v, bool): return None
    if isinstance(v, (int, float)):
        if 20000 <= v <= 80000 and float(v).is_integer(): return "num_or_serial"
        return "num"
    if isinstance(v, str) and v.strip() and not v.startswith("="):
        if excel_v3.norm_date(v)[0] and re.search(r"\d{4}", v): return "date"
        x, _, why = excel_v3._num(v)
        if x is not None: return "num"
    if isinstance(v, str) and v.startswith("="): return "formula"
    return None


def _compatible(field: str, kind: str | None) -> bool:
    if kind is None: return False
    if field.endswith("_date"): return kind in ("date", "num_or_serial", "formula")
    return kind in ("num", "num_or_serial", "formula")


def side_scan(path: Path) -> dict:
    """{unknown_pairs: [...], extra: {field: [cands]}, asof_cells: [...]} — excel_v3 와 같은 바이트 읽기, 저장 없음."""
    wb = excel_v3.WorkbookV3(path)
    unknown, extra, asof = [], {f: [] for f in EXTRA_SYN}, []
    for ws in wb.f.worksheets:
        mg = wb.merged[ws.title]; grid = {}
        for row in ws.iter_rows():
            for c in row:
                if (c.row, c.column) in mg and mg[(c.row, c.column)] != (c.row, c.column): continue
                if c.value is not None and not (isinstance(c.value, str) and not c.value.strip()): grid[(c.row, c.column)] = c.value
        for (r, c), v in grid.items():
            if not isinstance(v, str) or v.startswith("=") or len(v.strip()) > 40: continue
            k = excel_v3._key(re.sub(r"[\(\[（][^\)\]）]*[\)\]）]", "", v))
            tgt = None
            for dc in (1, 2, 3):
                if (r, c + dc) in grid:
                    w = grid[(r, c + dc)]
                    if isinstance(w, str) and (excel_v3.parse_label(w) or excel_v3.header_token(w)) and not re.search(r"\d", w): break
                    tgt = (r, c + dc); break
            if tgt is None:                       # 머리 행(전치 표) 라벨: 아래 칸
                for dr in (1, 2):
                    w = grid.get((r + dr, c))
                    if w is None: continue
                    if isinstance(w, str) and (excel_v3.parse_label(w) or excel_v3.header_token(w)): break
                    tgt = (r + dr, c); break
            if tgt is None: continue
            info = wb.cell(ws.title, *tgt)
            hit = next((f for f, ks in _EXTRA_KEYS.items() if k in ks), None)
            if hit:
                extra[hit].append({"label": v, "label_cell": f"{ws.title}!{ws.cell(r, c).coordinate}", "cell": f"{ws.title}!{info['cell']}",
                                   "value": info["value"], "resolved_by": info["resolved_by"], "number_format": info["number_format"]})
                continue
            li = excel_v3.parse_label(v)
            if li and li.get("kind") == "asof" and not li.get("decoy"):
                d, _ = excel_v3.norm_date(info["value"], info.get("number_format") or "")
                asof.append({"cell": f"{ws.title}!{info['cell']}", "date": d}); continue
            if li or excel_v3.header_token(v) or "단위" in v: continue
            if re.fullmatch(r"[\d\s,.\-%원]+", v): continue
            kd = _kind_of(info["value"]) if info["resolved_by"] != "external_ref" else "formula"
            if kd: unknown.append({"label": v, "label_cell": f"{ws.title}!{ws.cell(r, c).coordinate}", "cell": f"{ws.title}!{info['cell']}",
                                   "raw_value": excel_v3._jsonable(info["value"]), "kind": kd})
    return {"unknown_pairs": unknown, "extra": extra, "asof_cells": asof}


def _opt_from_excel(v) -> tuple[dict | None, str | None]:
    """Excel 옵션 셀 → ({present, first_date_candidate}, 실패 사유)."""
    if isinstance(v, (_dt.date, _dt.datetime)) or (isinstance(v, (int, float)) and not isinstance(v, bool) and 20000 <= v <= 80000):
        d, _ = excel_v3.norm_date(v); return {"present": True, "first_date_candidate": d}, None
    if isinstance(v, bool): return {"present": v, "first_date_candidate": None}, None
    s = str(v).strip().lower() if v is not None else ""
    if s in _YES: return {"present": True, "first_date_candidate": None}, None
    if s in _NO: return {"present": False, "first_date_candidate": None}, None
    d, _ = excel_v3.norm_date(s)
    if d: return {"present": True, "first_date_candidate": d}, None
    return None, "option_value_unparsed"


# ── 분류 ─────────────────────────────────────────────────────────────────────────────────────────
def _classify_core(fd: dict, unknown: list[dict]) -> tuple[str, str | None, list]:
    v = fd["verdict"]; f = fd.get("field")
    if v == "match": return "일치", None, []
    if v == "mismatch": return "불일치", "값 불일치", []
    if v == "stale_version": return "불일치", "정정 미반영", []
    if v == "future_version": return "불일치", "기준일 뒤 정정 선반영", []
    if v == "unit_error": return "불일치", "단위 오류", []
    if v == "series_mismatch": return "불일치", "회차 오연결", []
    if v == "missing_in_model":
        comp = [u for u in unknown if _compatible(f, u["kind"])]
        if comp: return "미확인", "사전 밖 라벨 가능성(누락으로 확정하지 않음)", comp[:5]
        return "누락", "refixing 조항 누락" if f == "refixing_floor_krw" else "조건 셀 없음", []
    if v == "not_in_disclosure": return ("미확인", "공시에 없는 값이 모형에 있음", []) if fd.get("model_value") is not None else ("해당없음", None, [])
    if v == "not_in_disclosure_or_model": return "해당없음", None, []
    return "미확인", fd.get("reason") or v, []


def _excel_value(fd: dict):
    x = fd.get("excel") or {}
    return x.get("raw_value", (fd.get("normalization") or {}).get("implausible_value"))


def _suggest(cls: str, sub: str | None, cell: str | None, disc, label: str) -> dict | None:
    if cls == "불일치" and cell:
        return {"text": f"{cell} 값({label})을 기준일 유효 공시 값 {_fmt(disc)} 로 고칠지 검토", "requires_approval": True, "auto_applied": False}
    if cls == "누락":
        return {"text": f"모형에 {label}({_fmt(disc)}) 입력 셀 추가 또는 반영 위치 확인", "requires_approval": True, "auto_applied": False}
    if cls == "미확인":
        return {"text": f"{label}: 사람이 셀·근거를 확인 후 판정", "requires_approval": True, "auto_applied": False}
    return None


def audit(excel: str | Path, chain: dict, texts: dict, as_of: str, *, target_series: str | None = None) -> dict:
    p = Path(excel); before = _sha(p.read_bytes())
    terms = asof_terms.resolve(chain, texts, as_of)
    T = asof_terms.ymd(as_of)
    hashes = {v["rcept_no"]: asof_terms.doc_sha(v, texts) for v in chain["versions"]}
    v3 = excel_v3.audit_workbook_v3(p, chain, as_of=T, target_series=target_series, doc_hashes=hashes)
    side = side_scan(p)
    findings = []
    tser = v3["target_series"]

    def add(field, cls, sub, cell, xval, disc_val, term, extra=None):
        label = asof_terms.KO.get(field, field or "라벨")
        ev = []
        if term:
            s = term["source"]; loc = s.get("locator") or {}
            ev.append({"doc": f"DART:{s['rcept_no']}", "rcept_no": s["rcept_no"], "rcept_dt": s["rcept_dt"], "url": s["dart_url"], "doc_sha256": s.get("doc_sha256"),
                       "line": loc.get("line"), "char_start": loc.get("char_start"), "char_end": loc.get("char_end"), "rule": loc.get("rule"),
                       "established_in": term["established_in"]["rcept_no"]})
        if cell: ev.append({"doc": f"xlsx:{p.name}", "sha256": before, "cell": cell})
        if extra and extra.get("matched_version_rcept"):
            m = extra["matched_version_rcept"]; ev.append({"doc": f"DART:{m}", "rcept_no": m, "url": asof_terms.DART_VIEW.format(m), "role": "Excel 값과 같은 다른 버전"})
        blocked = None
        if term and term.get("effective_status") == "UNCONFIRMED_DEPENDS" and cls in AUTO_CLASSES:   # R7 — 자동 판정 금지
            blocked = {"blocked_auto_class": cls, "blocked_auto_subtype": sub, "alternative_values": term.get("alternative_values")}
            cls, sub = EFF_CLASS, f"효력일 미확정 — 기준일 선택이 효력일에 달림(후보 값 {term.get('alternative_values')}). 사람 승인 전 판정 없음"
        hr = cls != "일치" and cls != "해당없음" or bool(term and term.get("link_review_required"))
        row = {"field": field, "label_ko": label, "class": cls, "subtype": sub, "severity": _sev(field, cls, sub), "excel_cell": cell,
               "excel_value": _clean(xval), "disclosure_value": disc_val, "evidence": ev, "human_review_required": hr,
               "suggestion": _suggest(cls, sub, cell, disc_val, label), "approval_status": "PENDING"}
        if blocked: row |= blocked | {"approval_required": True, "auto_verdict_allowed": False, "suggestion": _suggest("미확인", sub, cell, disc_val, label)}
        if extra: row.update({k: v for k, v in extra.items() if v is not None})
        if row.get("matched_version_rcept") and sub in ("정정 미반영", "기준일 뒤 정정 선반영"):
            row["subtype"] = f"{sub}(Excel 값 = {row['matched_version_rcept']} 버전 값)"
        findings.append(row)

    for fd in v3["findings"]:
        f = fd.get("field")
        if f is None:
            add(None, "미확인", fd.get("reason"), (fd.get("excel") or {}).get("label_cell"), (fd.get("excel") or {}).get("label"), None, None); continue
        cls, sub, cands = _classify_core(fd, side["unknown_pairs"])
        x = fd.get("excel") or {}
        cell = f"{x['sheet']}!{x['cell']}" if x.get("sheet") else None
        t = terms["terms"].get(f)
        if t and t.get("adjusted_by") and fd.get("model_value") is not None and cls in AUTO_CLASSES:   # R8 — 전환가액 조정 공시 반영 재비교
            same = str(fd["model_value"]) == str(t["value"])
            cls, sub = ("일치", None) if same else ("불일치", f"값 불일치(전환가액 조정 {t['adjusted_by']['rcept_no']} 반영)")
            fd = fd | {"expected": t["value"]}
        extra = {"model_value_normalized": fd.get("model_value"), "v3_verdict": fd["verdict"], "v3_reason": fd.get("reason"),
                 "matched_version_rcept": fd.get("matched_version_rcept"), "unit_source": fd.get("unit_source"),
                 "possible_synonym_cells": cands or None,
                 "candidates": [f"{c['sheet']}!{c['cell']}" for c in fd.get("candidates", [])] or None}
        add(f, cls, sub, cell, _excel_value(fd), fd.get("expected"), t, extra)

    # 추가 조건(전환청구기간·옵션) — 공시 버전별 값으로 정정 미반영도 본다
    vers = [(v, asof_terms.terms_for(v, texts)) for v in chain["versions"]]
    for f in asof_terms.EXTRA_FIELDS + asof_terms.OPTION_FIELDS:
        t = terms["terms"].get(f); disc = t["value"] if t else None
        cands = side["extra"][f]
        if not cands:
            if disc is None or (f in asof_terms.OPTION_FIELDS and disc.get("present") is False):
                add(f, "해당없음", None, None, None, disc, t); continue
            add(f, "미확인", "모형에 해당 셀 없음(옵션·기간은 수식 내 반영 가능 — 누락으로 확정하지 않음)", None, None, disc, t); continue
        if len(cands) > 1 and len({json.dumps(excel_v3._jsonable(c["value"]), default=str) for c in cands}) > 1:
            add(f, "미확인", "conflicting_inputs" + (f"(대상 회차 {tser})" if tser else ""), cands[0]["cell"], cands[0]["value"], disc, t,
                {"candidates": [c["cell"] for c in cands]}); continue
        c = cands[0]
        if c["resolved_by"] in ("external_ref", "formula_unevaluated"):
            add(f, "미확인", c["resolved_by"], c["cell"], c["value"], disc, t); continue
        if f in asof_terms.OPTION_FIELDS:
            xv, why = _opt_from_excel(c["value"])
            if xv is None or disc is None: add(f, "미확인", why or "공시 옵션 절 판독 불가", c["cell"], c["value"], disc, t); continue
            if t.get("option_status") != "keyword_found" and xv["present"]:
                add(f, "미확인", f"공시 옵션 키워드 없음({t.get('option_status')}) — 모형은 있음", c["cell"], c["value"], disc, t); continue
            if xv["present"] != disc["present"]:
                add(f, "불일치", "옵션 유무 불일치", c["cell"], c["value"], disc, t); continue
            if xv["first_date_candidate"] and disc.get("first_date_candidate") and xv["first_date_candidate"] != disc["first_date_candidate"]:
                add(f, "미확인", "옵션 최초일 다름(공시 날짜는 텍스트 규칙 후보)", c["cell"], c["value"], disc, t); continue
            add(f, "일치", None, c["cell"], c["value"], disc, t); continue
        xd, why = excel_v3.norm_date(c["value"], c.get("number_format") or "")
        if xd is None: add(f, "미확인", why, c["cell"], c["value"], disc, t); continue
        if disc is None: add(f, "미확인", "공시에서 판독 못함", c["cell"], c["value"], disc, t); continue
        if xd == disc: add(f, "일치", None, c["cell"], c["value"], disc, t); continue
        older = [v for v, vt in vers if v["rcept_dt"] <= T and vt[f]["value"] == xd]
        newer = [v for v, vt in vers if v["rcept_dt"] > T and vt[f]["value"] == xd]
        sub = "정정 미반영" if older else "기준일 뒤 정정 선반영" if newer else "값 불일치"
        add(f, "불일치", sub, c["cell"], c["value"], disc, t, {"matched_version_rcept": (older or newer)[-1]["rcept_no"] if (older or newer) else None})

    # Excel 평가기준일 ≠ --asof
    xs = sorted({a["date"] for a in side["asof_cells"] if a["date"]})
    if xs and asof_terms.iso(T) not in xs:
        add(None, "미확인", "Excel 평가기준일 불일치", next(a["cell"] for a in side["asof_cells"] if a["date"]), ",".join(xs), asof_terms.iso(T), None)
        findings[-1]["severity"] = "high"
    after = _sha(p.read_bytes())
    if after != before: raise RuntimeError("WORKBOOK_MODIFIED_DURING_AUDIT")
    events: list[dict] = []
    ledger.append_event(events, "audit_v4_run", {"workbook_sha256": before, "chain_origin": chain.get("origin"), "as_of": asof_terms.iso(T),
                                                 "selected_version": (terms.get("selected_version") or {}).get("rcept_no")})
    for i, x in enumerate(findings):
        x["item_id"] = hashlib.sha256(json.dumps([before, T, x["field"], x["class"], x["subtype"], x["excel_cell"]], ensure_ascii=False, default=str).encode()).hexdigest()[:16]
        if x["class"] not in ("일치", "해당없음"): ledger.append_event(events, "finding_pending", {"item_id": x["item_id"], "class": x["class"], "field": x["field"]})
    cnt = Counter(x["class"] for x in findings)
    errors = [x for x in findings if x["class"] in ("불일치", "누락")]
    return {"tool": "valuationproof.audit_asof_v4", "version": VERSION, "workbook": p.name, "workbook_sha256_before": before, "workbook_sha256_after": after,
            "chain_origin": chain.get("origin"), "corp_code": chain.get("corp_code"), "target_series": tser, "as_of": asof_terms.iso(T),
            "selected_version": terms.get("selected_version"), "excluded_after_asof": terms["excluded_after_asof"], "terms_status": terms["status"],
            "terms": terms["terms"], "findings": findings, "counts": {k: cnt.get(k, 0) for k in CLASSES},
            "errors_found": len(errors), "needs_human_review": sum(x["human_review_required"] for x in findings),
            "auto_fix_applied": 0, "approval_status": "PENDING", "side_effects": False, "ledger": events,
            "effective_date_unconfirmed": sum(x["class"] == EFF_CLASS for x in findings), "ledger_ok": ledger.verify_events(events),
            "claim_boundary": "공개 공시(OpenDART) 기준 결정론 대조. 공시 ≠ 서명 계약. 판정은 회계사 검토 후보(PENDING). 자동 수정 없음.",
            "v3_as_of_source": v3.get("as_of_source")}


# ── 보고서 ───────────────────────────────────────────────────────────────────────────────────────
def _fmt(v) -> str:
    if v is None: return ""
    if isinstance(v, dict): return ", ".join(f"{k}={v[k]}" for k in v)
    return str(v)


def to_markdown(r: dict, title: str | None = None) -> str:
    sv = r.get("selected_version") or {}
    L = [f"# {title or 'ValuationProof 기준일 조건 대조 보고서'}", "",
         f"- 워크북: `{r['workbook']}` (sha256 {r['workbook_sha256_before'][:16]}…, 감사 전후 동일: {r['workbook_sha256_before'] == r['workbook_sha256_after']})",
         f"- 발행회사 corp_code {r.get('corp_code')} · 원공시 {r.get('chain_origin')} · 대상 회차 {r.get('target_series')}",
         f"- 평가 기준일 {r['as_of']} → 적용 공시 {sv.get('rcept_no')} (접수 {sv.get('rcept_dt')}, {sv.get('kind')})",
         f"- 기준일 뒤 접수라 제외한 정정: {', '.join(x['rcept_no'] for x in r['excluded_after_asof']) or '없음'}",
         f"- 결과: " + " · ".join(f"{k} {v}" for k, v in r["counts"].items()) + f" · 오류(불일치+누락) {r['errors_found']} · 사람 확인 {r['needs_human_review']}",
         f"- 자동 수정 0 · 승인 상태 {r['approval_status']} · 해시 체인 {r['ledger_ok']}", "",
         f"> {r['claim_boundary']}", "",
         "| # | 조건 | 분류 | 세부 | 심각도 | Excel 셀 | Excel 값 | 공시 값(기준일) | 근거 공시 | 사람 확인 |", "|---|---|---|---|---|---|---|---|---|---|"]
    for i, x in enumerate(r["findings"], 1):
        d = next((e for e in x["evidence"] if e.get("rcept_no") and e.get("line") is not None), None) or next((e for e in x["evidence"] if e.get("rcept_no")), None)
        link = f"[{d['rcept_no']}]({d['url']})" + (f" L{d['line']}" if d.get("line") else "") if d else ""
        L.append(f"| {i} | {x['label_ko']} | {x['class']} | {x['subtype'] or ''} | {x['severity']} | {x['excel_cell'] or ''} | {_fmt(x['excel_value'])} | "
                 f"{_fmt(x['disclosure_value'])} | {link} | {'예' if x['human_review_required'] else ''} |")
    L += ["", "## 수정 제안 (승인 필요 · 자동 적용 안 함)", ""]
    L += [f"- {x['label_ko']}: {x['suggestion']['text']}" for x in r["findings"] if x.get("suggestion") and x["class"] in ("불일치", "누락")] or ["- 없음"]
    L += ["", "## 기준일 유효 조건 (근거)", "", "| 조건 | 값 | 적용 공시 | 원문 위치 | 값이 정해진 공시 | 기준일 뒤 변경 |", "|---|---|---|---|---|---|"]
    for f, t in r["terms"].items():
        loc = t["source"].get("locator") or {}
        L.append(f"| {t['label_ko']} | {_fmt(t['value'])} | {t['source']['rcept_no']} | {('L' + str(loc['line'])) if loc.get('line') else ''}"
                 f"{(' chars[' + str(loc['char_start']) + ':' + str(loc['char_end']) + ']') if loc.get('char_start') is not None else ''} | "
                 f"{t['established_in']['rcept_no']} | {', '.join(c['rcept_no'] + '→' + _fmt(c['value']) for c in t.get('changes_after_asof', []))} |")
    return "\n".join(L) + "\n"


def to_html(r: dict, title: str | None = None) -> str:
    e = html.escape
    color = {"일치": "#1a7f37", "불일치": "#cf222e", "누락": "#bc4c00", "미확인": "#9a6700", "해당없음": "#6e7781", EFF_CLASS: "#8250df"}
    sv = r.get("selected_version") or {}
    rows = []
    for i, x in enumerate(r["findings"], 1):
        d = next((ev for ev in x["evidence"] if ev.get("rcept_no") and ev.get("line") is not None), None) or next((ev for ev in x["evidence"] if ev.get("rcept_no")), None)
        link = f'<a href="{e(d["url"])}">{e(d["rcept_no"])}</a>' + (f' L{d["line"]}' if d.get("line") else "") if d else ""
        rows.append(f"<tr><td>{i}</td><td>{e(x['label_ko'])}</td><td style='color:{color[x['class']]};font-weight:600'>{e(x['class'])}</td>"
                    f"<td>{e(x['subtype'] or '')}</td><td>{e(x['severity'])}</td><td><code>{e(x['excel_cell'] or '')}</code></td>"
                    f"<td>{e(_fmt(x['excel_value']))}</td><td>{e(_fmt(x['disclosure_value']))}</td><td>{link}</td>"
                    f"<td>{'예' if x['human_review_required'] else ''}</td><td>{e((x.get('suggestion') or {}).get('text', ''))}</td></tr>")
    summ = " · ".join(f"{k} {v}" for k, v in r["counts"].items())
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(title or 'ValuationProof 대조 보고서')}</title>
<style>:root{{--bg:#fff;--fg:#1f2328;--mut:#59636e;--bd:#d1d9e0}}@media (prefers-color-scheme:dark){{:root{{--bg:#0d1117;--fg:#e6edf3;--mut:#9198a1;--bd:#3d444d}}}}
body{{background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,sans-serif;margin:0 auto;max-width:1200px;padding:16px}}
table{{border-collapse:collapse;width:100%;font-size:13px}}td,th{{border:1px solid var(--bd);padding:4px 6px;vertical-align:top}}
.wrap{{overflow-x:auto}}.mut{{color:var(--mut)}}code{{font-size:12px}}</style></head><body>
<h1>{e(title or 'ValuationProof 기준일 조건 대조 보고서')}</h1>
<p>워크북 <code>{e(r['workbook'])}</code> (sha256 {e(r['workbook_sha256_before'][:16])}…, 감사 전후 동일 {r['workbook_sha256_before'] == r['workbook_sha256_after']})<br>
corp_code {e(str(r.get('corp_code')))} · 원공시 {e(str(r.get('chain_origin')))} · 회차 {e(str(r.get('target_series')))}<br>
평가 기준일 <b>{e(r['as_of'])}</b> → 적용 공시 <a href="{e(sv.get('dart_url', '#'))}">{e(str(sv.get('rcept_no')))}</a> (접수 {e(str(sv.get('rcept_dt')))})<br>
기준일 뒤 제외 정정: {e(', '.join(x['rcept_no'] for x in r['excluded_after_asof']) or '없음')}</p>
<p><b>{e(summ)}</b> · 오류(불일치+누락) {r['errors_found']} · 사람 확인 {r['needs_human_review']} · 자동 수정 0 · 승인 {e(r['approval_status'])}</p>
<p class="mut">{e(r['claim_boundary'])}</p>
<div class="wrap"><table><tr><th>#</th><th>조건</th><th>분류</th><th>세부</th><th>심각도</th><th>Excel 셀</th><th>Excel 값</th><th>공시 값</th><th>근거</th><th>사람 확인</th><th>제안(승인 필요)</th></tr>
{''.join(rows)}</table></div></body></html>
"""


def write_reports(r: dict, out_dir: str | Path, stem: str = "audit_report", title: str | None = None) -> dict:
    d = Path(out_dir); d.mkdir(parents=True, exist_ok=True)
    pj, pm, ph = d / f"{stem}.json", d / f"{stem}.md", d / f"{stem}.html"
    pj.write_text(json.dumps(r, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    pm.write_text(to_markdown(r, title), encoding="utf-8"); ph.write_text(to_html(r, title), encoding="utf-8")
    return {"json": str(pj), "md": str(pm), "html": str(ph)}
