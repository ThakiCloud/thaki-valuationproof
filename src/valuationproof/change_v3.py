"""ValuationProof V3 공시 변경 추적기 — dart_change(V2)를 감싸서 검증·구조적 diff·미연결 정정 복구를 더한다.

⛔ dart_change.py 의 함수·정규식은 바꾸지 않는다(Track E 학습 데이터 빌더가 import). V2 체인을 먼저 만들고, 그 위에서만 일한다.
1) 검증: 체인마다 회사(corp_code) 일관성, 접수번호 형식, 접수일(rcept_dt) vs 접수번호 앞 8자리 날짜 차이, 접수 순서, 회차 일관성.
2) 최초 vs 최신 구조적 diff: 필드별 before/after/단위 + 공시 시점(접수일·접수번호 날짜)과 계약상 효력 시점(근거)을 분리.
   계약상 효력 시점은 공시만으로 확정되지 않는다: 납입일 전 정정 = '발행조건(납입일부터)' 후보, 납입일 뒤 정정 = REVIEW.
3) 미연결 정정 복구: V2 가 비운 정정을 유형별로 나누고, 독립 신호(회차 일치·정정 전 값 인용)가 있을 때만 'recovered'.
   신호가 없거나 후보가 여럿이면 'review'(회계사·분석가 확인 큐), 원공시가 데이터에 없으면 'unrecoverable'.
   ⛔ review 는 자동 확정하지 않는다. recovered 도 사람 검수가 아니다(규칙 신호) — link_status 에 그렇게 적는다.
"""
from __future__ import annotations

import copy
import datetime as _dt
import re
from collections import Counter, defaultdict

from . import dart_change

VERSION = "3.0.0"
FIELDS = list(dart_change.FIELDS)
UNITS = {"principal_krw": "KRW", "conversion_price_krw": "KRW/share", "refixing_floor_krw": "KRW/share", "coupon_rate_pct": "%",
         "redemption_yield_pct": "%", "maturity_date": "date", "issue_date": "date"}
WINDOW_DAYS = 3
# '최초제출일' 대체 형식: 2025,07,15 · 2025. 7. 15. · 라벨 다음 줄(셀) — V2 정규식(_FIRST)은 그대로 두고 여기서 보충
_FIRST_ALT = re.compile(r"최초\s*제출일\s*:?[\s|]*(\d{4})\s*[,.\-/년]\s*(\d{1,2})\s*[,.\-/월]\s*(\d{1,2})")
_SERIES_ALT = [re.compile(r"회\s*차\s*\|?\s*\n?\s*\|?\s*\(?제?\s*(\d{1,3})\s*(?:회|\)|\||\n)"),
               re.compile(r"제\s*(\d{1,3})\s*회\s*(?:차\s*)?(?:무기명|기명|무보증|보증|사모|공모|이권부|전환사채)")]
_DATE = re.compile(r"(\d{4})\s*(?:년|[.\-/])\s*(\d{1,2})\s*(?:월|[.\-/])\s*(\d{1,2})")


def _d(s: str) -> _dt.date: return _dt.date(int(s[:4]), int(s[4:6]), int(s[6:8]))


def series_v3(text: str | None) -> tuple[str | None, str | None]:
    """(회차, 방법). V2 dart_change.series 우선, 실패 시 서식 앞부분의 대체 패턴."""
    if not text: return None, None
    s = dart_change.series(text)
    if s: return s, "v2"
    ft = dart_change.form_text(text)[:1500]
    for rx in _SERIES_ALT:
        m = rx.search(ft)
        if m: return str(int(m.group(1))), "alt"
    return None, None


def first_date_v3(text: str) -> tuple[str | None, str | None]:
    c = dart_change.parse_correction(text)
    if c["original_first_date"]: return c["original_first_date"], "v2"
    m = _FIRST_ALT.search(text)
    if m:
        try:
            d = _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))); return d.strftime("%Y%m%d"), "alt_format"
        except ValueError: return None, "invalid"
    return None, None


def correction_section(text: str) -> str:
    lines = text.splitlines()
    s = next((i for i, l in enumerate(lines) if "정정사항" in l), None)
    e = next((i for i, l in enumerate(lines) if dart_change._FORM_START.match(l.strip())), len(lines))
    return "\n".join(lines[s:e]) if s is not None else ""


def value_in(section: str, field: str, v) -> bool:
    """원공시 값이 정정공시 '정정사항' 표(정정 전 칸 포함)에 적혀 있는가 — 규칙에 안 쓴 독립 신호."""
    if v is None or not section: return False
    if field.endswith("_date"):
        return v in {f"{int(a):04d}-{int(b):02d}-{int(c):02d}" for a, b, c in _DATE.findall(section)}
    flat = section.replace(",", "")
    if field.endswith("_pct"):
        x = float(v); pats = {str(v), f"{x:g}", f"{x:.1f}", f"{x:.2f}"}
        return any(re.search(rf"(?<![\d.]){re.escape(p)}(?![\d])", flat) for p in pats)
    return re.search(rf"(?<!\d){re.escape(str(v))}(?!\d)", flat) is not None


def _signals(corr_text: str, corr_vals: dict, orig_text: str | None, orig_vals: dict) -> dict:
    cs, _ = series_v3(corr_text); os_, _ = series_v3(orig_text)
    sec = correction_section(corr_text)
    diff = [f for f in FIELDS if orig_vals.get(f) is not None and corr_vals.get(f) is not None and orig_vals[f] != corr_vals[f]]
    hits = [f for f in diff if value_in(sec, f, orig_vals[f])]
    return {"series_corr": cs, "series_orig": os_, "series": ("agree" if cs and os_ and cs == os_ else "disagree" if cs and os_ else "missing"),
            "before_values": "hit" if hits else ("miss" if diff else "no_diff"), "before_hit_fields": hits, "diff_fields": diff}


def _strong(sig: dict) -> bool:
    return sig["series"] != "disagree" and (sig["series"] == "agree" or sig["before_values"] == "hit")


# ── 검증 + 구조적 diff ───────────────────────────────────────────────────────────────────────────
def validate_chain(chain: dict, meta_by: dict, texts: dict) -> dict:
    issues = []
    o = chain["versions"][0] if chain["versions"] else None
    corps = {meta_by[v["rcept_no"]]["corp_code"] for v in chain["versions"] if v["rcept_no"] in meta_by}
    if len(corps) > 1: issues.append({"code": "corp_mismatch", "corps": sorted(corps)})
    sers = {}
    for v in chain["versions"]:
        r = v["rcept_no"]
        if not re.fullmatch(r"\d{14}", r): issues.append({"code": "rcept_no_format", "rcept_no": r})
        elif r[:8] != v["rcept_dt"]:
            issues.append({"code": "rcept_dt_differs_from_rcept_no_date", "rcept_no": r, "rcept_dt": v["rcept_dt"], "info_only": True})
        if o and v is not o and v["rcept_dt"] < o["rcept_dt"]: issues.append({"code": "correction_before_origin", "rcept_no": r})
        s, _ = series_v3(texts.get(r)); sers[r] = s
    known = {s for s in sers.values() if s}
    if len(known) > 1: issues.append({"code": "series_inconsistent_across_versions", "series": sers})
    return {"series": (known.pop() if len(known) == 1 else None), "series_by_version": sers,
            "issues": issues, "ok": not [i for i in issues if not i.get("info_only")]}


def structural_diff(chain: dict) -> list[dict]:
    """최초(원공시) vs 최신 버전, 필드별. 공시 시점과 계약상 효력 시점 근거를 따로 적는다."""
    vs = chain["versions"]
    if len(vs) < 2: return []
    a, b = vs[0], vs[-1]
    issue = b["values"].get("issue_date") or a["values"].get("issue_date")
    out = []
    for f in FIELDS:
        va, vb = a["values"].get(f), b["values"].get(f)
        if va == vb: continue
        # 이 필드가 마지막으로 바뀐 버전
        last = None
        for x, y in zip(vs, vs[1:]):
            if x["values"].get(f) != y["values"].get(f): last = y
        last = last or b
        filed = last["rcept_dt"]
        if issue and filed.replace("-", "") <= issue.replace("-", ""):
            eff = {"basis": "pre_issuance_amendment", "candidate_effective": issue, "confirmed": False,
                   "note": "납입일 전 정정 — 발행조건으로 납입일부터 적용될 후보. 계약서·인수계약 확인 전 확정 아님"}
        else:
            eff = {"basis": "post_issuance_or_unknown", "candidate_effective": None, "confirmed": False,
                   "note": "납입일 뒤(또는 납입일 미상) 정정 — 소급 여부·효력일은 공시만으로 확정 불가 → REVIEW"}
        out.append({"field": f, "unit": UNITS[f], "before": va, "after": vb, "before_rcept": a["rcept_no"], "after_rcept": b["rcept_no"],
                    "changed_in_rcept": last["rcept_no"],
                    "disclosure_time": {"rcept_dt": filed, "rcept_no_date": last["rcept_no"][:8], "same_day": filed == last["rcept_no"][:8]},
                    "contract_effective": eff, "review_required": True})
    return out


# ── 미연결 정정 복구 ─────────────────────────────────────────────────────────────────────────────
def _origin_index(meta):
    by_day, by_corp = defaultdict(list), defaultdict(list)
    for m in meta:
        if not m["is_correction"]:
            for day in {m["rcept_dt"], m["rcept_no"][:8]}: by_day[(m["corp_code"], day)].append(m["rcept_no"])
            by_corp[m["corp_code"]].append(m)
    return by_day, by_corp


def _vals(text, norm):
    from . import dart_table
    return {f: norm(f, v) for f, v in dart_table.extract(dart_change.form_text(text)).items()}


def recover(meta: list[dict], texts: dict, base: dict, norm, window_days: int = WINDOW_DAYS) -> list[dict]:
    """V2 가 연결하지 못한 정정 각각에 대해 {type, status(recovered|review|unrecoverable), rule, candidates, signals}."""
    linked = {v["rcept_no"] for c in base["chains"].values() for v in c["versions"]}
    origin_of = {v["rcept_no"]: k for k, c in base["chains"].items() for v in c["versions"]}
    by_day, by_corp = _origin_index(meta)
    corr_by_day = defaultdict(list)
    for m in meta:
        if m["is_correction"] and m["rcept_no"] in linked: corr_by_day[(m["corp_code"], m["rcept_dt"])].append(m["rcept_no"])
    rows = []
    for m in sorted((m for m in meta if m["is_correction"] and m["rcept_no"] not in linked), key=lambda x: x["rcept_no"]):
        r = m["rcept_no"]; t = texts.get(r); nm = m.get("report_nm", "")
        row = {"rcept_no": r, "rcept_dt": m["rcept_dt"], "corp_code": m["corp_code"], "report_nm_kind": nm.split("]")[0].lstrip("[") if nm.startswith("[") else nm}
        if t is None:
            k = "attachment_correction_no_main_form" if nm.startswith("[첨부정정]") else "no_text"
            rows.append(row | {"v2_reason": "no_text", "type": k, "status": "unrecoverable",
                               "note": "첨부서류 정정 — 본문 서식(document.xml) 없음. 조건 변경 아님 가능성이 크나 확인 불가" if k.startswith("attach") else "원문 미수신"})
            continue
        fd, how = first_date_v3(t)
        cvals = _vals(t, norm)
        cser, _ = series_v3(t)
        # V2 사유 재현
        v2 = None
        if dart_change.parse_correction(t)["original_first_date"] is None: v2 = "no_first_date"
        else:
            cand0 = by_day.get((m["corp_code"], fd), [])
            v2 = "origin_outside_window" if not cand0 else ("ambiguous" if len(cand0) > 1 else "series_conflict")
        row |= {"v2_reason": v2, "first_date": fd, "first_date_how": how, "series": cser}
        if fd is None:
            pool = [o for o in by_corp.get(m["corp_code"], []) if o["rcept_dt"] <= m["rcept_dt"]]
            hit = [o["rcept_no"] for o in pool if cser and series_v3(texts.get(o["rcept_no"]))[0] == cser]
            rows.append(row | {"type": "no_first_date", "status": "review" if hit else "unrecoverable", "rule": "series_only",
                               "candidates": hit, "note": "최초제출일 없음 — 회차만으로는 확정하지 않는다"}); continue
        cand = by_day.get((m["corp_code"], fd), [])
        rule = "exact_date"
        if v2 == "series_conflict":
            sig = _signals(t, cvals, texts.get(cand[0]), base["chains"][cand[0]]["versions"][0]["values"] if cand[0] in base["chains"] else {})
            rows.append(row | {"type": "series_conflict", "status": "review", "rule": "never_auto", "candidates": cand, "signals": sig}); continue
        if not cand:
            # (a) 최초제출일이 같은 회사 정정공시 날짜 → 그 정정이 속한 체인
            cd = corr_by_day.get((m["corp_code"], fd), [])
            if cd:
                cand = sorted({origin_of[x] for x in cd}); rule = "first_date_is_a_correction"
            else:
                # (b) ±window 일 안의 같은 회사 원공시 (최초제출일 ≠ 접수일·접수번호 날짜)
                near = []
                for o in by_corp.get(m["corp_code"], []):
                    for day in {o["rcept_dt"], o["rcept_no"][:8]}:
                        gap = abs((_d(day) - _d(fd)).days)
                        if 0 < gap <= window_days and o["rcept_dt"] <= m["rcept_dt"]: near.append((gap, o["rcept_no"]))
                cand = sorted({x for _, x in near}); rule = f"date_window_{window_days}d"
                row["date_gap_days"] = min((g for g, _ in near), default=None)
        if not cand:
            corp_has = bool(by_corp.get(m["corp_code"]))
            rows.append(row | {"type": "origin_not_in_dataset" if not corp_has else "origin_not_in_dataset(corp_has_other_issues)",
                               "status": "unrecoverable", "rule": "none",
                               "note": "원공시가 수집 창·보충 밖 — 같은 (회사, 최초제출일) 정정끼리 고아 묶음으로만 남긴다"}); continue
        sigs = {o: _signals(t, cvals, texts.get(o), base["chains"][o]["versions"][0]["values"] if o in base["chains"] and base["chains"][o]["versions"] else {}) for o in cand}
        strong = [o for o, s in sigs.items() if _strong(s)]
        not_conf = [o for o, s in sigs.items() if s["series"] != "disagree"]
        typ = {"exact_date": "ambiguous_same_day", "first_date_is_a_correction": "first_date_is_a_correction"}.get(rule, "first_date_off_by_days")
        if how == "alt_format": typ = "first_date_alt_format/" + typ
        if len(strong) == 1 and len(not_conf) == 1:
            rows.append(row | {"type": typ, "status": "recovered", "rule": rule, "linked_origin": strong[0], "candidates": cand, "signals": sigs[strong[0]]})
        else:
            rows.append(row | {"type": typ, "status": "review", "rule": rule, "candidates": cand, "signals": sigs,
                               "note": "독립 신호가 하나의 후보를 가리키지 않음 — 자동 확정 안 함"})
    # 고아 묶음 id
    grp = defaultdict(list)
    for x in rows:
        if x["status"] == "unrecoverable" and x.get("first_date"): grp[(x["corp_code"], x["first_date"], x.get("series"))].append(x["rcept_no"])
    for x in rows:
        k = (x["corp_code"], x.get("first_date"), x.get("series"))
        if k in grp: x["orphan_group"] = f"{k[0]}:{k[1]}:{k[2]}"; x["orphan_group_size"] = len(grp[k])
    return rows


def build_chains_v3(meta: list[dict], texts: dict, norm, window_days: int = WINDOW_DAYS) -> dict:
    base = dart_change.build_chains(meta, texts, norm)            # V2 그대로
    rec = recover(meta, texts, base, norm, window_days)
    chains = copy.deepcopy(base["chains"])
    for ch in chains.values():
        for v in ch["versions"]: v.setdefault("link_method", "origin" if v["kind"] == "original" else "v2_rule")
    by_meta = {m["rcept_no"]: m for m in meta}
    for x in rec:
        if x["status"] != "recovered": continue
        t = texts[x["rcept_no"]]
        c = dart_change.parse_correction(t)
        chains[x["linked_origin"]]["versions"].append({"rcept_no": x["rcept_no"], "rcept_dt": x["rcept_dt"], "kind": "correction",
                                                       "values": _vals(t, norm), "changed_items": c["changed_items"],
                                                       "link_method": f"v3_recovery:{x['rule']}",
                                                       "link_status": "recovered_by_rule_signals(not_human_verified)"})
    for k, ch in chains.items():
        ch["versions"].sort(key=lambda v: (v["kind"] != "original", v["rcept_no"]))
        ch["changes"] = dart_change.diff_versions(ch["versions"])
        val = validate_chain(ch, by_meta, texts) if ch["versions"] else {"series": None, "issues": [], "ok": True}
        ch["series"] = val["series"]; ch["validation"] = val
        ch["structural_diff"] = structural_diff(ch)
    summ = Counter((x["type"], x["status"]) for x in rec)
    return {"tool": "change.track_v3", "version": VERSION, "chains": chains, "v2_unlinked": base["unlinked_corrections"],
            "recovery": rec, "recovery_summary": {f"{a}|{b}": n for (a, b), n in sorted(summ.items())},
            "recovery_status": dict(Counter(x["status"] for x in rec)),
            "review_queue": [{"rcept_no": x["rcept_no"], "type": x["type"], "candidates": x.get("candidates", []), "status": "PENDING",
                              "reason": x.get("note") or x["type"]} for x in rec if x["status"] == "review"]}
