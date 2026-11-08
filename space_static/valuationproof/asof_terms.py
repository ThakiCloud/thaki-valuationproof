"""ValuationProof V4 기준일 조건 해석기 — 원공시 + 정정공시 사슬에서 평가 기준일 T 에 유효한 CB 조건을 고른다 (결정론).

입력: change_v3.build_chains_v3 가 만든 체인 1개 + 공시 원문(rcept_no → 텍스트) + 기준일 T(YYYYMMDD 또는 YYYY-MM-DD).
출력: 필드별 값 + 근거(rcept_no·접수일·공시 링크·원문 위치(줄 번호·문자 구간)·값이 처음 그 값이 된 공시) + 제외된 공시 목록.

⛔ 시점 규칙(테스트로 고정, tests/test_v4_asof.py):
  R1. T 이전(같은 날 포함)에 **접수된** 마지막 버전을 쓴다. 접수일(rcept_dt) 기준이다 — 효력발생일·납입일이 아니다.
  R2. T 뒤에 접수된 정정은 전부 제외하고 `excluded_after_asof` 에 남긴다(소급 적용 여부는 공시만으로 확정 불가 → 사람 검토).
  R3. T 가 원공시 접수일보다 앞이면 조건을 내지 않는다(status=NOT_YET_FILED). 추측하지 않는다.
  R4. 정정이 규칙 신호로만 연결된 경우(change_v3 복구, 사람 미검수)에는 그 버전에서 온 값에 link_review_required=True.
  R5. 납입일 뒤 정정은 계약상 효력일이 공시만으로 확정되지 않는다 → contract_effective_confirmed=False 를 값마다 단다.
  R6 (4.1, ledger/D_effective_date_1109.json). 날짜 키 = 공시에 적힌 효력일이 있으면 그 날짜, 없으면 접수일 + date_key_fallback=True.
      원공시·새 결의 정정 = 이사회결의일(결정일), 전환가액의조정 = 조정가액 적용일. 결의일이 직전 버전과 같은(또는 못 읽은) 정정 =
      기재 정정 — 소급인지 장래인지 공시만으로 모른다 → 키는 접수일, 가능 구간 [직전 버전 하한, 접수일].
  R7 (4.1, 오너 제약). 효력일이 공시로 확정되지 않아 T 에서 고를 값이 갈리는 필드는 effective_status=UNCONFIRMED_DEPENDS,
      status_ko='확인 필요(효력일 미확정)' — audit_v4 는 그 필드에 자동 일치/불일치/누락 판정을 내지 않고 사람 승인을 요구한다.
  R8 (4.1). chain['adjustments'] (전환가액의조정 공시, parse_adjustment) 가 있으면 전환가액에 반영한다 — 키 = 조정가액 적용일.
조기상환(풋)·매도청구(콜)·전환청구기간은 dart_table 서식 필드가 아니라서 이 모듈의 텍스트 규칙으로 뽑는다.
옵션은 '있다/없다 + 최초 날짜 후보'만 내고 조건 세부(행사가·비율)는 해석하지 않는다(review_required=True 고정).
⛔ dart_change.py·dart_table.py 는 수정하지 않는다(Track E 가 import).
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import re

from . import dart_change, dart_table
from . import _synthetic

VERSION = "4.1.0"
EFF_UNCONFIRMED = "확인 필요(효력일 미확정)"
DATE_KEYS = ("effective", "rcept_dt", "early")
DART_VIEW = _synthetic.DartView("https://dart.fss.or.kr/dsaf001/main.do?rcpNo={}")   # public build: synthetic rcept_no -> synthetic:// (never a real DART link)
CORE_FIELDS = list(dart_change.FIELDS)          # 7 서식 필드(dart_table.LABELS)
EXTRA_FIELDS = ["conversion_start_date", "conversion_end_date"]
OPTION_FIELDS = ["put_option", "call_option"]
ALL_FIELDS = CORE_FIELDS + EXTRA_FIELDS + OPTION_FIELDS
UNITS = {"principal_krw": "KRW", "conversion_price_krw": "KRW/share", "refixing_floor_krw": "KRW/share", "coupon_rate_pct": "%",
         "redemption_yield_pct": "%", "maturity_date": "date", "issue_date": "date", "conversion_start_date": "date",
         "conversion_end_date": "date", "put_option": "presence+date", "call_option": "presence+date"}
KO = {"principal_krw": "권면총액", "conversion_price_krw": "전환가액", "refixing_floor_krw": "리픽싱 하한(최저조정가액)",
      "coupon_rate_pct": "표면이자율", "redemption_yield_pct": "만기이자율", "maturity_date": "사채만기일", "issue_date": "납입일",
      "conversion_start_date": "전환청구 시작일", "conversion_end_date": "전환청구 종료일",
      "put_option": "조기상환청구권(풋)", "call_option": "매도청구권(콜)"}
_DATE = re.compile(r"(\d{4})\s*(?:년|[.\-/])\s*(\d{1,2})\s*(?:월|[.\-/])\s*(\d{1,2})")
_OPT_START = re.compile(r"^9-1\.\s*옵션에\s*관한\s*사항")
_OPT_END = re.compile(r"^\d{1,2}\.\s*\S")
_PUT = re.compile(r"조기상환\s*청구권|put\s*option|풋\s*옵션|사채권자[^\n]{0,80}?조기\s*상환[^\n]{0,60}?청구", re.I)
_CALL = re.compile(r"매도\s*청구권|call\s*option|콜\s*옵션|발행회사[^\n]{0,80}?(?:매도|매수)[^\n]{0,30}?청구", re.I)
_NONE = re.compile(r"^\s*(?:-|해당\s*사항\s*없음|없음|N/A)?\s*\|?\s*$")


def ymd(s: str) -> str:
    """'2025-06-30' · '20250630' · '2025.6.30' → '20250630'."""
    n = re.findall(r"\d+", str(s))
    if len(n) == 1 and len(n[0]) == 8: return n[0]
    if len(n) < 3: raise ValueError(f"bad date: {s!r}")
    d = _dt.date(int(n[0]), int(n[1]), int(n[2]))
    return d.strftime("%Y%m%d")


def iso(s: str) -> str: s = ymd(s); return f"{s[:4]}-{s[4:6]}-{s[6:]}"


def _date_iso(raw: str | None) -> str | None:
    m = _DATE.search(raw or "")
    if not m: return None
    try: return _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
    except ValueError: return None


# ── 원문 위치 ────────────────────────────────────────────────────────────────────────────────────
def _cell_spans(text: str):
    """dart_table._cells 와 같은 셀 분할 + (줄 번호, 문자 시작, 문자 끝). 위치를 근거로 남기기 위해서만 쓴다."""
    out, pos = [], 0
    for ln, line in enumerate(text.splitlines(keepends=True), 1):
        body = line.rstrip("\r\n"); off = 0
        for part in body.split(" | "):
            c = part.strip().rstrip("|").strip()
            if c:
                s = body.find(c, off); out.append((c, ln, pos + s, pos + s + len(c))); off = s + len(c)
            else: off += len(part) + 3
        pos += len(line)
    return out


def _form_offset(text: str) -> tuple[str, int, int]:
    """(서식 텍스트, 원문 안 문자 오프셋, 줄 오프셋). dart_change.form_text 와 같은 기준(마지막 '1. 사채의 종류')."""
    ft = dart_change.form_text(text)
    off = text.rfind(ft) if ft != text else 0
    return ft, max(off, 0), text[:max(off, 0)].count("\n")


def locate_core(text: str) -> dict:
    """서식 필드(dart_table.LABELS) 각각의 라벨·값 셀 위치. dart_table.extract 와 같은 '첫 매치 + 다음 셀' 규칙."""
    ft, coff, loff = _form_offset(text)
    cells = _cell_spans(ft); res = {}
    for f, pat in dart_table.LABELS.items():
        rx = re.compile(rf"^{pat}$")
        for i, (c, ln, s, e) in enumerate(cells):
            if rx.match(c):
                v = cells[i + 1] if i + 1 < len(cells) else ("", ln, e, e)
                res[f] = {"label": c, "raw": v[0], "line": v[1] + loff, "char_start": v[2] + coff, "char_end": v[3] + coff,
                          "label_line": ln + loff, "rule": f"dart_table.LABELS[{f}]"}
                break
    return res


def extract_extra(text: str) -> dict:
    """전환청구기간(시작일·종료일) — '전환청구기간 | 시작일 | <날짜> | 종료일 | <날짜>'. 서식 첫 매치만."""
    ft, coff, loff = _form_offset(text)
    cells = _cell_spans(ft); res = {}
    for i, (c, *_r) in enumerate(cells):
        if re.fullmatch(r"전환\s*청구\s*기간", c):
            win = cells[i + 1:i + 7]
            for key, lab in (("conversion_start_date", r"시작일"), ("conversion_end_date", r"종료일")):
                for j, (cc, *_x) in enumerate(win[:-1]):
                    if re.fullmatch(lab, cc):
                        v = win[j + 1]
                        res[key] = {"label": f"전환청구기간/{cc}", "raw": v[0], "value": _date_iso(v[0]), "line": v[1] + loff,
                                    "char_start": v[2] + coff, "char_end": v[3] + coff, "rule": "asof_terms.extract_extra"}
                        break
            break
    return res


def extract_options(text: str) -> dict:
    """'9-1. 옵션에 관한 사항' 절에서 풋·콜 존재와 최초 날짜 후보. 절이 없으면 status=section_missing(=미확인)."""
    ft, coff, loff = _form_offset(text)
    lines = ft.splitlines(keepends=True)
    st = next((i for i, l in enumerate(lines) if _OPT_START.match(l.strip())), None)
    if st is None:
        return {k: {"present": None, "status": "section_missing", "first_date_candidate": None} for k in OPTION_FIELDS}
    en = next((i for i in range(st + 1, len(lines)) if _OPT_END.match(lines[i].strip()) and not lines[i].strip().startswith("9-")), len(lines))
    sec = "".join(lines[st + 1:en]); sec_off = coff + sum(len(l) for l in lines[:st + 1])
    out = {}
    empty = all(_NONE.match(l) for l in sec.splitlines()) if sec.strip() else True
    for key, rx in (("put_option", _PUT), ("call_option", _CALL)):
        m = rx.search(sec)
        if empty: out[key] = {"present": False, "status": "section_empty", "first_date_candidate": None,
                              "line": st + 1 + loff, "rule": "asof_terms.extract_options"}; continue
        if not m: out[key] = {"present": False, "status": "keyword_absent(사람 확인)", "first_date_candidate": None,
                              "line": st + 1 + loff, "rule": "asof_terms.extract_options"}; continue
        d = _DATE.search(sec, m.start(), min(len(sec), m.end() + 250))
        out[key] = {"present": True, "status": "keyword_found", "keyword": m.group(0)[:40],
                    "first_date_candidate": _date_iso(d.group(0)) if d else None,
                    "line": ft[:sec_off - coff + m.start()].count("\n") + 1 + loff,
                    "char_start": sec_off + m.start(), "char_end": sec_off + m.end(), "rule": "asof_terms.extract_options"}
    return out


# ── 버전 값 ──────────────────────────────────────────────────────────────────────────────────────
def version_terms(text: str | None, core_values: dict) -> dict:
    """버전 1개의 11개 조건 + 위치. core 값은 체인에 이미 정규화된 값(dart_change 경유)을 그대로 쓴다."""
    loc = locate_core(text) if text else {}
    ex = extract_extra(text) if text else {}
    op = extract_options(text) if text else {k: {"present": None, "status": "no_text", "first_date_candidate": None} for k in OPTION_FIELDS}
    terms = {}
    for f in CORE_FIELDS: terms[f] = {"value": core_values.get(f), "locator": loc.get(f)}
    for f in EXTRA_FIELDS: terms[f] = {"value": (ex.get(f) or {}).get("value"), "locator": ex.get(f)}
    for f in OPTION_FIELDS:
        o = op[f]; terms[f] = {"value": ({"present": o["present"], "first_date_candidate": o["first_date_candidate"]} if o["present"] is not None else None),
                               "locator": o, "option_status": o["status"]}
    return terms


def _sha(t: str | None) -> str | None: return hashlib.sha256(t.encode("utf-8")).hexdigest() if t is not None else None


def terms_for(v: dict, texts: dict) -> dict:
    """버전 조건. 원문이 없고 체인 고정본의 terms_cache 가 있으면 그것을 쓴다(examples/ 재현용)."""
    t = texts.get(v["rcept_no"])
    if t is None and v.get("terms_cache"): return v["terms_cache"]
    return version_terms(t, v["values"])


def doc_sha(v: dict, texts: dict) -> str | None:
    return _sha(texts.get(v["rcept_no"])) if v["rcept_no"] in texts else v.get("doc_sha256")


# ── 효력일(R6~R8) ───────────────────────────────────────────────────────────────────────────────
_BOARD = re.compile(r"^(?:\d+\.\s*)?이사회\s*결의일\s*(?:\(\s*결정일\s*\))?$")
_ADJ_EFF = re.compile(r"^(?:\d+\.\s*)?조정\s*가액\s*적용일$")
_ADJ_SEC = re.compile(r"^1\.\s*조정에\s*관한\s*사항$")
_NUM = re.compile(r"^[\d,]+(?:\.\d+)?$")


def _ymd_or_none(raw) -> str | None:
    d = _date_iso(str(raw)) if raw else None
    return d.replace("-", "") if d else None


def board_date(text: str | None) -> str | None:
    """CB 발행결정 서식(정정 반영본)의 '이사회결의일(결정일)' 다음 셀 → YYYYMMDD. 없거나 '-' 면 None."""
    if not text: return None
    ft, _c, _l = _form_offset(text); cells = _cell_spans(ft)
    for i, (c, *_r) in enumerate(cells):
        if _BOARD.match(c): return _ymd_or_none(cells[i + 1][0]) if i + 1 < len(cells) else None
    return None


def parse_adjustment(text: str) -> dict:
    """'전환가액의조정' 공시 → 회차별 조정 전·후 전환가액 + 조정가액 적용일 + 이사회결의일. 결정론 셀 규칙(LLM 없음)."""
    cells = [c for c, *_r in _cell_spans(text)]
    rows, eff, bd = [], None, None
    i = next((k for k, c in enumerate(cells) if _ADJ_SEC.match(c)), None)
    if i is not None:
        j = i + 1; hdr = []
        while j < len(cells) and not re.match(r"^\d", cells[j]) and not cells[j].startswith("제"): hdr.append(cells[j]); j += 1
        n = len(hdr)
        ix = {k: next((h for h, x in enumerate(hdr) if re.search(p, x)), None) for k, p in (("series", r"회\s*차"), ("before", r"조정\s*전"), ("after", r"조정\s*후"))}
        while n and j + n <= len(cells) and not re.match(r"^\d+\.\s*\S", cells[j]):
            rec = cells[j:j + n]; j += n
            s = re.search(r"\d+", rec[ix["series"]]) if ix["series"] is not None else None
            b = rec[ix["before"]].replace(",", "") if ix["before"] is not None else ""
            a = rec[ix["after"]].replace(",", "") if ix["after"] is not None else ""
            if not (s and _NUM.match(a)): break
            rows.append({"series": str(int(s.group())), "before_krw": str(int(float(b))) if _NUM.match(b) else None, "after_krw": str(int(float(a)))})
    for k, c in enumerate(cells[:-1]):
        if eff is None and _ADJ_EFF.match(c): eff = _ymd_or_none(cells[k + 1])
        if bd is None and _BOARD.match(c): bd = _ymd_or_none(cells[k + 1])
    return {"rows": rows, "effective_dt": eff, "board_dt": bd, "parser": "asof_terms.parse_adjustment"}


def adjustments_for(chain: dict, adj_filings: list[dict]) -> list[dict]:
    """adj_filings = [{rcept_no, rcept_dt, corp_code, parsed: parse_adjustment(...)}] 중 체인 회사·회차와 같은 행 → chain['adjustments'] 형식."""
    out = []
    for a in adj_filings:
        if chain.get("corp_code") and a.get("corp_code") != chain["corp_code"]: continue
        for r in a["parsed"]["rows"]:
            if chain.get("series") is not None and r["series"] == str(chain["series"]):
                out.append({"rcept_no": a["rcept_no"], "rcept_dt": a["rcept_dt"], "kind": "adjustment", "effective_dt": a["parsed"]["effective_dt"],
                            "conversion_price_krw": r["after_krw"], "before_krw": r["before_krw"]})
    return sorted(out, key=lambda x: x["rcept_no"])


def version_effective(versions: list[dict], texts: dict, structured: dict | None = None) -> list[dict]:
    """버전마다 {key, lo, hi, basis, disclosed, board_dt}. key = 날짜 키, [lo, hi] = 공시만으로 가능한 효력일 구간(YYYYMMDD).
    structured = cvbdIsDecsn 레코드(rcept_no → {bddd,...}) — 있으면 그 결의일을 우선한다(구조화 필드 우선)."""
    out, prev = [], None
    for v in versions:
        if v.get("effective"): out.append(v["effective"]); prev = v["effective"]; continue
        r = v["rcept_dt"]; rec = (structured or {}).get(v["rcept_no"]) or {}
        bd, src = _ymd_or_none(rec.get("bddd")), "cvbdIsDecsn.bddd"
        if bd is None and v.get("board_dt"): bd, src = v["board_dt"], "version.board_dt"
        if bd is None: bd, src = board_date(texts.get(v["rcept_no"])), "form:이사회결의일(결정일)"
        if bd is None: src = None
        if v.get("kind") != "correction" or prev is None:
            e = ({"key": bd, "lo": min(bd, r), "hi": max(bd, r), "basis": "board_resolution", "disclosed": True} if bd
                 else {"key": r, "lo": r, "hi": r, "basis": "rcept_dt_fallback", "disclosed": False})
        elif bd and prev.get("board_dt") and bd != prev["board_dt"]:
            e = {"key": bd, "lo": min(bd, r), "hi": max(bd, r), "basis": "new_board_resolution", "disclosed": True}
        else:   # 기재 정정 — 소급/장래 미확정
            e = {"key": r, "lo": prev["lo"], "hi": r, "basis": "restatement_same_resolution" if bd else "restatement_or_unknown", "disclosed": False}
        e |= {"board_dt": bd, "board_src": src, "date_key_fallback": not e["disclosed"]}
        out.append(e); prev = e
    return out


def _adj_effective(adjs: list[dict], first_key: str | None) -> list[dict]:
    out, prev = [], first_key
    for a in adjs:
        r, e = a["rcept_dt"], a.get("effective_dt")
        d = ({"key": e, "lo": e, "hi": e, "basis": "adjustment_effective_date", "disclosed": True} if e
             else {"key": r, "lo": min(prev or r, r), "hi": r, "basis": "adjustment_rcept_dt_fallback", "disclosed": False})
        d["date_key_fallback"] = not d["disclosed"]; out.append(d); prev = d["key"]
    return out


def _pick(events: list[dict], T: str, mode: str):
    k = {"effective": "key", "early": "lo"}.get(mode)
    live = [e for e in events if (e["eff"][k] if k else e["rcept_dt"]) <= T]
    return max(live, key=lambda e: ((e["eff"][k] if k else e["rcept_dt"]), e["rcept_no"])) if live else None


def _ambiguity(events: list[dict], T: str) -> list:
    """공시만으로 효력일이 확정 안 된 사건 때문에 T 에서 가능한 값 집합. 2개 이상이면 선택이 효력일에 달려 있다."""
    sure = [e for e in events if e["eff"]["hi"] <= T]
    base = max(sure, key=lambda e: e["rcept_no"]) if sure else None
    maybe = [e for e in events if e["eff"]["lo"] <= T < e["eff"]["hi"] and (base is None or e["rcept_no"] > base["rcept_no"])]
    vals = [base["value"] if base else None] + [e["value"] for e in maybe]
    uniq = []
    for x in vals:
        if x not in uniq: uniq.append(x)
    return uniq


def resolve(chain: dict, texts: dict, as_of: str, *, date_key: str = "effective", structured: dict | None = None) -> dict:
    """체인 + 기준일 → 기준일 유효 조건. 원문이 없으면 core 값만(위치 None) 내고 extra/옵션은 미확인.
    date_key: 'effective'(기본, R6) · 'rcept_dt'(V4.0 규칙 — 측정 기준선) · 'early'(기재 정정 소급 해석, 측정용)."""
    if date_key not in DATE_KEYS: raise ValueError(f"bad date_key: {date_key}")
    T = ymd(as_of)
    vs = chain["versions"]
    effs = version_effective(vs, texts, structured)
    adjs = chain.get("adjustments") or []
    aeffs = _adj_effective(adjs, effs[0]["key"] if effs else None)
    kf = {"effective": "key", "early": "lo"}.get(date_key)
    keyof = lambda v, e: e[kf] if kf else v["rcept_dt"]
    order = sorted(range(len(vs)), key=lambda i: (keyof(vs[i], effs[i]), vs[i]["rcept_no"]))
    eff_idx = [i for i in order if keyof(vs[i], effs[i]) <= T]                                       # R1/R6
    excluded = [{"rcept_no": vs[i]["rcept_no"], "rcept_dt": vs[i]["rcept_dt"], "kind": vs[i]["kind"],
                 "reason": "filed_after_asof" if vs[i]["rcept_dt"] > T else "effective_after_asof", "date_key": keyof(vs[i], effs[i]),
                 "dart_url": DART_VIEW.format(vs[i]["rcept_no"])} for i in order if keyof(vs[i], effs[i]) > T]   # R2
    base = {"tool": "terms.resolve_asof", "version": VERSION, "chain_origin": chain.get("origin"), "corp_code": chain.get("corp_code"),
            "series": chain.get("series"), "as_of": iso(T), "versions_total": len(vs), "excluded_after_asof": excluded, "date_key_mode": date_key,
            "rule": ("효력일 키(R6): 공시에 적힌 결의일·조정가액 적용일, 없으면 접수일(fallback 표시). 효력일이 공시로 확정 안 돼 값이 갈리면 "
                     f"'{EFF_UNCONFIRMED}'(R7). 계약서 대조 전 확정 아님." if date_key == "effective" else
                     "접수일(rcept_dt) <= 기준일 인 마지막 버전(V4.0 규칙)." if date_key == "rcept_dt" else "측정용: 기재 정정을 직전 효력일로 소급")}
    if not eff_idx:                                                                                  # R3
        pend = [vs[i]["rcept_no"] for i in range(len(vs)) if effs[i]["lo"] <= T] if date_key == "effective" else []
        return base | {"status": "NOT_YET_FILED", "selected_version": None, "terms": {}, "review_required": True,
                       **({"effective_status": "UNCONFIRMED_DEPENDS", "status_ko": EFF_UNCONFIRMED, "possibly_effective": pend} if pend else {})}
    cur_i = eff_idx[-1]; cur = vs[cur_i]
    per_version = [(vs[i], terms_for(vs[i], texts)) for i in eff_idx]
    cur_terms = per_version[-1][1]
    all_terms = {i: (per_version[eff_idx.index(i)][1] if i in eff_idx else None) for i in range(len(vs))}
    issue = cur["values"].get("issue_date")
    # 전환가액 조정(R8)
    adj_ev = [{"rcept_no": a["rcept_no"], "rcept_dt": a["rcept_dt"], "value": a["conversion_price_krw"], "eff": aeffs[j], "adj": a} for j, a in enumerate(adjs)]
    terms = {}
    for f in ALL_FIELDS:
        t = cur_terms[f]; val = t["value"]
        est = cur
        for v, vt in reversed(per_version):
            if vt[f]["value"] == val: est = v
            else: break
        src_v, src_t, adj_used = cur, t, None
        if f == "conversion_price_krw" and adj_ev:
            # 조정 공시는 발행 조건 위에 얹힌 변경이다 — 기재 정정(발행 당시 조건 재기재)이 조정 값을 되돌리지 않는다.
            # 조정보다 뒤에 효력이 나는 '새 이사회결의' 정정만 조정을 덮는다.
            pick = _pick(adj_ev, T, date_key)
            ck = effs[cur_i]["lo"] if date_key == "early" else effs[cur_i]["key"]
            if pick is not None and not (effs[cur_i]["basis"] == "new_board_resolution" and date_key != "rcept_dt"
                                         and ck > (pick["eff"]["lo"] if date_key == "early" else pick["eff"]["key"])):
                adj_used = pick["adj"]; val = pick["value"]; est = adj_used
        link_rev = str(est.get("link_status", "")).startswith("recovered") or str(cur.get("link_status", "")).startswith("recovered")
        filed_after_issue = bool(issue and est["kind"] == "correction" and est["rcept_dt"] > issue.replace("-", ""))
        ekey = aeffs[adjs.index(adj_used)] if adj_used else effs[cur_i]
        rec = {"field": f, "label_ko": KO[f], "unit": UNITS[f], "value": val,
               "source": ({"rcept_no": adj_used["rcept_no"], "rcept_dt": adj_used["rcept_dt"], "kind": "adjustment", "dart_url": DART_VIEW.format(adj_used["rcept_no"]),
                           "doc_sha256": _sha(texts.get(adj_used["rcept_no"])), "locator": None, "link_method": "corp+series"} if adj_used else
                          {"rcept_no": cur["rcept_no"], "rcept_dt": cur["rcept_dt"], "kind": cur["kind"], "dart_url": DART_VIEW.format(cur["rcept_no"]),
                           "doc_sha256": doc_sha(cur, texts), "locator": src_t["locator"], "link_method": cur.get("link_method")}),
               "established_in": {"rcept_no": est["rcept_no"], "rcept_dt": est["rcept_dt"], "kind": est["kind"], "dart_url": DART_VIEW.format(est["rcept_no"])},
               "changed_since_origin": per_version[0][1][f]["value"] != val,
               "link_review_required": link_rev,                                                    # R4
               "contract_effective_confirmed": False,                                               # R5
               "contract_effective_note": ("납입일 뒤 정정 — 계약상 효력일·소급 여부 공시만으로 확정 불가" if filed_after_issue
                                           else "공시 기재 효력일/접수일 기준 선택 — 계약서 대조 전 확정 아님"),
               "date_key": {"mode": date_key, "basis": ekey["basis"] if date_key == "effective" else date_key,
                            "key_dt": (ekey["key"] if date_key == "effective" else ekey["lo"] if date_key == "early" else (adj_used or cur)["rcept_dt"]),
                            "date_key_fallback": bool(ekey["date_key_fallback"]) if date_key == "effective" else date_key == "rcept_dt"},
               "effective_status": "NOT_DEPENDENT"}
        if adj_used: rec["adjusted_by"] = {"rcept_no": adj_used["rcept_no"], "rcept_dt": adj_used["rcept_dt"], "effective_dt": adj_used.get("effective_dt"),
                                          "before_krw": adj_used.get("before_krw"), "dart_url": DART_VIEW.format(adj_used["rcept_no"])}
        if date_key == "effective":                                                                   # R7
            evs = []
            for i, v in enumerate(vs):
                tv = all_terms[i] if all_terms[i] is not None else (terms_for(v, texts) if effs[i]["lo"] <= T else None)
                if tv is not None: evs.append({"rcept_no": v["rcept_no"], "eff": effs[i], "value": tv[f]["value"]})
            alts = _ambiguity(evs, T)
            if f == "conversion_price_krw" and adj_ev:
                live_adj = [e for e in adj_ev if e["eff"]["lo"] <= T]
                late_newres = [e for e in evs if e["eff"]["basis"] == "new_board_resolution" and e["eff"]["lo"] <= T
                               and live_adj and e["eff"]["hi"] > min(x["eff"]["lo"] for x in live_adj)]
                comb = []
                for a in _ambiguity(adj_ev, T):
                    comb += alts if a is None else [a]
                comb += [e["value"] for e in late_newres]
                alts = [x for i, x in enumerate(comb) if x not in comb[:i]]
            if len(alts) > 1:
                rec |= {"effective_status": "UNCONFIRMED_DEPENDS", "status_ko": EFF_UNCONFIRMED, "alternative_values": alts,
                        "auto_verdict_allowed": False, "approval_required": True}
        if f in OPTION_FIELDS:
            rec["option_status"] = t.get("option_status"); rec["review_required"] = True
            rec["note"] = "옵션 존재·최초 날짜 후보만. 행사가·비율·기간 세부는 해석하지 않음 → 사람 확인"
        else:
            rec["review_required"] = link_rev or val is None or rec["effective_status"] == "UNCONFIRMED_DEPENDS"
        later = [x for x in vs if x["rcept_dt"] > T and f in CORE_FIELDS and x["values"].get(f) != cur["values"].get(f)]
        if later: rec["changes_after_asof"] = [{"rcept_no": x["rcept_no"], "rcept_dt": x["rcept_dt"], "value": x["values"].get(f)} for x in later]
        terms[f] = rec
    return base | {"status": "RESOLVED", "selected_version": {"rcept_no": cur["rcept_no"], "rcept_dt": cur["rcept_dt"], "kind": cur["kind"],
                   "index": cur_i, "link_method": cur.get("link_method"), "link_status": cur.get("link_status"),
                   "dart_url": DART_VIEW.format(cur["rcept_no"]), "effective": effs[cur_i]},
                   "terms": terms, "review_required": any(t["review_required"] for t in terms.values()),
                   "unconfirmed_fields": [f for f, t in terms.items() if t["effective_status"] == "UNCONFIRMED_DEPENDS"]}


def find_chain(chains: dict, corp: str | None, cb: str) -> dict:
    """--cb 는 원공시 접수번호 또는 그 체인에 속한 정정 접수번호. corp 가 주어지면 일치해야 한다."""
    ch = chains.get(cb)
    if ch is None:
        ch = next((c for c in chains.values() if any(v["rcept_no"] == cb for v in c["versions"])), None)
    if ch is None: raise KeyError(f"CB_CHAIN_NOT_FOUND: {cb}")
    if corp and ch.get("corp_code") and ch["corp_code"] != corp: raise ValueError(f"CORP_MISMATCH: chain {ch['corp_code']} != --corp {corp}")
    return ch
