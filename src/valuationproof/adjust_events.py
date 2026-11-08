"""ValuationProof V4.2 — 전환가액조정 공시 자동 수집·회차 연결·완전성 기록 (결정론, LLM 없음).

흐름: 발행사 1곳 list.json(공시유형 전체, 2년 창, 쪽 끝까지) → 대상 보고서 필터 → 원문(document.xml) → parse_adjustment_v42
     → 회차별 사건(원 조정 + [기재정정] 개정 이력) + 완전성 기록(adjustment_coverage). asof_terms.resolve(engine='4.2') 가
     이 기록으로 C1~C7 을 검사하고, 하나라도 실패하면 전환가액을 자동 확정하지 않는다(EVENT_COVERAGE_UNVERIFIED).

사건 시간축(bitemporal):
  rcept_dt      접수일(DART)
  effective_at  공시에 적힌 조정가액 적용일. 못 읽으면 rcept_dt 로 대체하고 effective_fallback=True
  known_at      공개된 날 = 접수일. [기재정정] 으로 바뀐 값은 그 정정의 접수일(revisions[].known_at)

⛔ DART 키는 환경변수 API_K_DART(또는 DART_API_KEY)에서만 읽고 출력·저장하지 않는다(dart_source._call). 오류에 URL 을 넣지 않는다.
⛔ 응답은 호출자가 준 cache_dir 에만 쓴다(Git·공유 경로 금지는 호출자 책임). 이미 있는 캐시는 다시 부르지 않는다.
⛔ V4.1 asof_terms.parse_adjustment·adjustments_for 는 바꾸지 않는다(V4.1 재현). 이 모듈의 파서는 별도다.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import time
from collections import Counter
from pathlib import Path

from . import asof_terms

VERSION = "4.2.0"
DART_VIEW = asof_terms.DART_VIEW
TARGET_NAMES = ("전환가액의조정", "전환가액ㆍ신주인수권행사가액ㆍ교환가액의조정")
NOT_CB_NAMES = ("전환주식의전환가액조정",)
_SEC = re.compile(r"^\d+\.\s*조정에\s*관한\s*사항$")
_KIND = re.compile(r"^\d+\.\s*구분$")
_SECURITY = re.compile(r"^\d+\.\s*증권의\s*종류$")
_EFF = re.compile(r"^(?:\d+\.\s*)?조정\s*가액\s*적용일$")
_BOARD = re.compile(r"^(?:\d+\.\s*)?이사회\s*결의일\s*(?:\(\s*결정일\s*\))?$")
_NUM = re.compile(r"^[\d,]+(?:\.\d+)?$")
_ROW_END = re.compile(r"^\d+\.\s*\S")


def _norm_name(n: str) -> str: return re.sub(r"\s+", "", n or "")


def report_class(report_nm: str) -> str:
    """'target' | 'not_cb' | 'other'. 보고서명(공백 제거) 기준. '[기재정정]' 접두는 무시하고 본 이름으로 판단."""
    n = _norm_name(report_nm)
    if any(x in n for x in NOT_CB_NAMES): return "not_cb"
    if any(x in n for x in TARGET_NAMES): return "target"
    return "other"


def is_correction(report_nm: str) -> bool:
    """'[기재정정]…' · '[정정]…' 처럼 앞 괄호에 '정정' 이 있으면 정정 공시."""
    n = (report_nm or "").strip()
    return n.startswith("[") and "정정" in n.split("]")[0]


def title_series(report_nm: str) -> list[str]:
    """보고서명 꼬리 '(제3회차, 제4회차 CB)' → ['3','4']. 값 판정에는 안 쓰고 회차 연결 근거로만 남긴다."""
    m = re.search(r"\(([^)]*회[^)]*)\)", report_nm or "")
    return [str(int(x)) for x in re.findall(r"\d+", m.group(1))] if m else []


def parse_adjustment_v42(text: str, report_nm: str = "") -> dict:
    """코스닥형 '전환가액의조정'(1. 조정에 관한 사항 …) · 유가증권형 '…의조정(안내공시)'(1. 구분 / 3. 조정에 관한 사항 …) 둘 다.
    반환: rows=[{series, before_krw, after_krw}], effective_dt, board_dt, kind(CB|BW|EB|UNKNOWN), status, format."""
    cells = [c for c, *_r in asof_terms._cell_spans(text or "")]
    fmt = "guidance" if "안내공시" in (report_nm or "") or any(_KIND.match(c) for c in cells[:80]) else "kosdaq"
    kind, security = ("CB", None) if fmt == "kosdaq" else ("UNKNOWN", None)
    for k, c in enumerate(cells[:-1]):
        if _KIND.match(c) and fmt == "guidance":
            v = re.sub(r"\s+", "", cells[k + 1])
            kind = "CB" if v.startswith("전환가액") else "BW" if v.startswith("신주인수권") else "EB" if v.startswith("교환가액") else "UNKNOWN"
        if _SECURITY.match(c) and security is None: security = cells[k + 1][:80]
    rows, bad = [], None
    i = next((k for k, c in enumerate(cells) if _SEC.match(c)), None)
    if i is not None:
        j = i + 1; hdr = []
        while j < len(cells) and not re.match(r"^\d", cells[j]) and not cells[j].startswith("제"): hdr.append(cells[j]); j += 1
        n = len(hdr)
        ix = {k: next((h for h, x in enumerate(hdr) if re.search(p, x)), None) for k, p in (("series", r"회\s*차"), ("before", r"조정\s*전"), ("after", r"조정\s*후"))}
        if None in ix.values(): bad = "header_unrecognized"
        while not bad and n and j + n <= len(cells) and not _ROW_END.match(cells[j]):
            rec = cells[j:j + n]; j += n
            s = re.search(r"\d+", rec[ix["series"]])
            b = rec[ix["before"]].replace(",", ""); a = rec[ix["after"]].replace(",", "")
            if not (s and _NUM.match(a)): break
            rows.append({"series": str(int(s.group())), "before_krw": str(int(float(b))) if _NUM.match(b) else None, "after_krw": str(int(float(a)))})
    eff = bd = None
    for k, c in enumerate(cells[:-1]):
        if eff is None and _EFF.match(c): eff = asof_terms._ymd_or_none(cells[k + 1])
        if bd is None and _BOARD.match(c): bd = asof_terms._ymd_or_none(cells[k + 1])
    ts = title_series(report_nm)
    if kind in ("BW", "EB"): status = "not_cb"
    elif kind == "UNKNOWN": status = "kind_unknown"
    elif bad or not rows: status = bad or "no_rows"
    elif ts and any(r["series"] not in ts for r in rows): status = "series_conflict_with_title"
    elif not eff: status = "no_effective_date"
    else: status = "ok"
    return {"rows": rows, "effective_dt": eff, "board_dt": bd, "kind": kind, "security": security, "format": fmt, "title_series": ts,
            "status": status, "parser": "adjust_events.parse_adjustment_v42"}


# ── 수집 ────────────────────────────────────────────────────────────────────────────────────────
def windows(begin: str, end: str) -> list[tuple[str, str]]:
    """짝수 해 정렬 2년 창 [YYYY0101, (YYYY+1)1231] ∩ [begin 연도 1월 1일, end]."""
    y = int(begin[:4]); y -= y % 2; out = []
    while f"{y}0101" <= end:
        out.append((f"{y}0101", min(f"{y + 1}1231", end))); y += 2
    return out


MOJIBAKE_MIN = 20


def xml_text(b: bytes) -> str:
    """dart_source._xml_text 와 같은 셀 경계 변환. 다른 점: 멤버마다 UTF-8 엄격 → 실패하면 CP949(EUC-KR) 로 읽는다.
    2022년 이전 원문은 EUC-KR 이 많다 — UTF-8 replace 로 읽으면 라벨이 깨져 파싱이 실패한다(V4.2 run1 에서 발견)."""
    import io, zipfile
    z = zipfile.ZipFile(io.BytesIO(b)); parts = []
    for n in z.namelist():
        raw = z.read(n)
        try: t = raw.decode("utf-8")
        except UnicodeDecodeError: t = raw.decode("cp949", errors="replace")
        t = re.sub(r"</(TD|TH|TE|TU)>", " | ", t, flags=re.I)
        t = re.sub(r"</(TR|P|TITLE|SECTION-\d)>|<BR\s*/?>", "\n", t, flags=re.I)
        t = re.sub(r"<[^>]+>", "", t)
        parts.append(re.sub(r"[ \t\u00a0]+", " ", t))
    return "\n".join(x.strip() for x in "\n".join(parts).splitlines() if x.strip())


def _mojibake(t: str) -> bool: return t.count("\ufffd") >= MOJIBAKE_MIN


def _default_fetch():
    from . import dart_source
    return dart_source._call


def collect_corp(corp: str, begin: str, end: str, cache_dir: str | Path, fetch=None, *, offline: bool = False, sleep: float = 0.15) -> dict:
    """발행사 1곳의 대상 조정 공시 목록·원문·파싱 결과 + 목록 완전성 기록. 오류는 예외 대신 기록(C1 실패로 이어진다).
    fetch(ep, raw=False, **q) — 기본은 dart_source._call(환경변수 키). offline=True 면 캐시만 쓴다(없으면 CACHE_MISS)."""
    cd = Path(cache_dir); (cd / "list").mkdir(parents=True, exist_ok=True); (cd / "doc").mkdir(parents=True, exist_ok=True)
    begin, end = asof_terms.ymd(begin), asof_terms.ymd(end)
    if fetch is None and not offline:
        try: fetch = _default_fetch()
        except Exception as e:  # noqa: BLE001
            fetch = None; offline = True; _err = f"FETCH_UNAVAILABLE:{type(e).__name__}"
    calls = 0; wins = []; items = []
    for b, e in windows(begin, end):
        w = {"bgn": b, "end": e, "pages_got": 0, "total_page": None, "total_count": None, "items_got": 0, "status": None, "ok": False}
        page = 1
        while True:
            p = cd / "list" / f"{corp}_{b}_{e}_{page}.json"
            if p.exists(): r = json.loads(p.read_text(encoding="utf-8"))
            elif offline or fetch is None: w["status"] = "CACHE_MISS"; break
            else:
                try:
                    r = fetch("list.json", corp_code=corp, bgn_de=b, end_de=e, page_no=page, page_count=100); calls += 1
                except SystemExit: w["status"] = "NO_DART_KEY"; break
                except Exception as ex:  # noqa: BLE001
                    w["status"] = f"ERR:{type(ex).__name__}"; break
                if r.get("status") in ("000", "013"): p.write_text(json.dumps(r, ensure_ascii=False), encoding="utf-8")
                if sleep: time.sleep(sleep)
            st = r.get("status"); w["status"] = st
            if st == "013": w.update(pages_got=0, total_page=0, total_count=0, ok=True); break
            if st != "000": break
            w["pages_got"] += 1; w["total_page"] = int(r.get("total_page", 1)); w["total_count"] = int(r.get("total_count", 0))
            w["items_got"] += len(r.get("list", [])); items += r.get("list", [])
            if page >= w["total_page"]:
                w["ok"] = w["pages_got"] == w["total_page"] and w["items_got"] == w["total_count"]; break
            page += 1
        wins.append(w)
    seen, reports = set(), []
    for x in sorted(items, key=lambda x: x["rcept_no"]):
        if x["rcept_no"] in seen: continue
        seen.add(x["rcept_no"]); cls = report_class(x["report_nm"])
        if cls == "other": continue
        nm = " ".join(x["report_nm"].split())
        rep = {"rcept_no": x["rcept_no"], "rcept_dt": x["rcept_dt"], "report_nm": nm, "dart_url": DART_VIEW.format(x["rcept_no"]),
               "is_correction": is_correction(nm), "class": cls, "known_at": x["rcept_dt"]}
        if cls == "not_cb": rep |= {"doc_status": "skipped", "parse": {"status": "not_cb", "rows": []}}; reports.append(rep); continue
        p = cd / "doc" / f"{x['rcept_no']}.txt"
        if p.exists() and _mojibake(p.read_text(encoding="utf-8")) and not offline and fetch is not None:
            p.rename(p.with_suffix(".mojibake.txt")); rep["doc_redecoded"] = True        # 깨진 캐시(인코딩) → 다시 받아 CP949 로 읽는다
        if not p.exists() and not offline and fetch is not None:
            try:
                raw = fetch("document.xml", raw=True, rcept_no=x["rcept_no"]); calls += 1
                if raw[:2] == b"PK": p.write_text(xml_text(raw), encoding="utf-8")
                else: rep["doc_error"] = "not_zip"
            except Exception as ex:  # noqa: BLE001
                rep["doc_error"] = f"ERR:{type(ex).__name__}"
            if sleep: time.sleep(sleep)
        if p.exists():
            t = p.read_text(encoding="utf-8")
            rep["doc_status"] = "ok" if not _mojibake(t) else "mojibake"; rep["parse"] = parse_adjustment_v42(t, nm)
        else:
            rep["doc_status"] = rep.get("doc_error") or "CACHE_MISS"; rep["parse"] = {"status": "no_doc", "rows": []}
        reports.append(rep)
    rec = {"tool": "adjust_events.collect_corp", "version": VERSION, "corp_code": corp, "begin": begin, "collected_through": end,
           "list_windows": wins, "reports": reports, "api_calls": calls,
           "collected_on": _dt.date.today().strftime("%Y%m%d")}
    if "_err" in locals(): rec["fetch_error"] = _err
    return rec


# ── 체인 연결 ────────────────────────────────────────────────────────────────────────────────────
START_MARGIN_DAYS = 31


def chain_start(chain: dict) -> str:
    """수집·검사 시작일 = 첫 버전 min(효력일 하한, 접수일) − 31일(공시 결의일이 접수일보다 최대 2주 앞선 실측 + 여유)."""
    vs = chain["versions"]
    d = min(min((v.get("effective") or {}).get("lo") or v["rcept_dt"], v["rcept_dt"]) for v in vs)
    return (_dt.date(int(d[:4]), int(d[4:6]), int(d[6:])) - _dt.timedelta(START_MARGIN_DAYS)).strftime("%Y%m%d")


def link_chain(chain: dict, corp_rec: dict, corp_series_counts: Counter | dict | None = None) -> tuple[list[dict], dict]:
    """발행사 수집 기록 → (이 체인 회차의 사건 목록, adjustment_coverage). 사건 = 원 조정 1건 + 정정 개정 이력(revisions).
    resolve 가 지식 시점(knowledge_cutoff)마다 사건·개정을 골라 쓰므로 여기서는 시점을 정하지 않는다."""
    ser = None if chain.get("series") in (None, "", "None") else str(int(re.search(r"\d+", str(chain["series"])).group()))
    cnt = Counter(corp_series_counts or {})
    events, problems, unmatched, corrections = [], [], [], []
    originals = [r for r in corp_rec["reports"] if r["class"] == "target" and not r["is_correction"]]
    corrs = [r for r in corp_rec["reports"] if r["class"] == "target" and r["is_correction"]]
    for r in originals:
        ps = r["parse"]; st = ps["status"]
        rows = [x for x in ps.get("rows", []) if ser is not None and x["series"] == ser]
        if st == "not_cb": unmatched.append({"rcept_no": r["rcept_no"], "reason": "not_cb"}); continue
        if st in ("no_doc", "no_rows", "header_unrecognized", "kind_unknown"):
            problems.append({"check": "C3_ALL_PARSED_OR_UNMATCHED", "rcept_no": r["rcept_no"], "known_at": r["known_at"], "reason": st}); continue
        if not rows:
            if st == "series_conflict_with_title" and ser in ps.get("title_series", []):
                problems.append({"check": "C3_ALL_PARSED_OR_UNMATCHED", "rcept_no": r["rcept_no"], "known_at": r["known_at"], "reason": st})
            else: unmatched.append({"rcept_no": r["rcept_no"], "reason": "other_series", "row_series": [x["series"] for x in ps["rows"]]})
            continue
        if st != "ok":
            problems.append({"check": "C3_ALL_PARSED_OR_UNMATCHED", "rcept_no": r["rcept_no"], "known_at": r["known_at"], "reason": st})
        for x in rows:
            eff = ps.get("effective_dt")
            events.append({"rcept_no": r["rcept_no"], "rcept_dt": r["rcept_dt"], "report_nm": r["report_nm"], "dart_url": r["dart_url"], "kind": "adjustment",
                           "effective_dt": eff, "effective_at": eff or r["rcept_dt"], "effective_fallback": eff is None, "known_at": r["known_at"],
                           "conversion_price_krw": x["after_krw"], "before_krw": x["before_krw"], "board_dt": ps.get("board_dt"), "format": ps.get("format"),
                           "series_evidence": {"chain_series": ser, "row_series": x["series"], "title_series": ps.get("title_series"),
                                               "security": ps.get("security"), "method": "corp_code+row_series"},
                           "sources": [{"rcept_no": r["rcept_no"], "report_nm": r["report_nm"], "dart_url": r["dart_url"]}], "revisions": []})
    # 같은 회차·같은 적용일: 값이 같으면 출처를 합치고(중복 공시), 다르면 충돌(C7)
    merged = {}
    for e in sorted(events, key=lambda e: e["rcept_no"]):
        k = e["effective_dt"] or f"fallback:{e['rcept_no']}"
        if k in merged:
            m = merged[k]
            if m["conversion_price_krw"] == e["conversion_price_krw"]: m["sources"] += e["sources"]; continue
            problems.append({"check": "C7_NO_CONFLICT", "rcept_no": e["rcept_no"], "known_at": e["known_at"], "reason": f"conflict_with:{m['rcept_no']}"}); continue
        merged[k] = e
    events = sorted(merged.values(), key=lambda e: (e["effective_at"], e["rcept_no"]))
    for r in corrs:
        ps = r["parse"]; st = ps["status"]
        if st == "not_cb": corrections.append({"rcept_no": r["rcept_no"], "linked_to": None, "rule": "not_cb"}); continue
        rows = [x for x in ps.get("rows", []) if ser is not None and x["series"] == ser]
        if st in ("no_doc", "no_rows", "header_unrecognized", "kind_unknown") or (not rows and ser in ps.get("title_series", [])):
            problems.append({"check": "C6_CORRECTIONS_LINKED", "rcept_no": r["rcept_no"], "known_at": r["known_at"], "reason": f"correction_{st}"}); continue
        if not rows: corrections.append({"rcept_no": r["rcept_no"], "linked_to": None, "rule": "other_series"}); continue
        for x in rows:
            eff = ps.get("effective_dt")
            tgt = next((e for e in events if eff and e["effective_dt"] == eff), None); rule = "same_series+same_effective_dt"
            if tgt is None:
                tgt = next((e for e in events if e["before_krw"] == x["before_krw"] and e["conversion_price_krw"] == x["after_krw"]), None); rule = "same_series+same_before_after"
            if tgt is None:
                problems.append({"check": "C6_CORRECTIONS_LINKED", "rcept_no": r["rcept_no"], "known_at": r["known_at"], "reason": "unlinked_correction"}); continue
            tgt["revisions"].append({"rcept_no": r["rcept_no"], "rcept_dt": r["rcept_dt"], "known_at": r["known_at"], "report_nm": r["report_nm"],
                                     "dart_url": r["dart_url"], "effective_dt": eff or tgt["effective_dt"], "conversion_price_krw": x["after_krw"],
                                     "before_krw": x["before_krw"], "link_rule": rule})
            corrections.append({"rcept_no": r["rcept_no"], "linked_to": tgt["rcept_no"], "rule": rule})
    if ser is None: problems.append({"check": "C4_SERIES_UNAMBIGUOUS", "rcept_no": None, "known_at": "00000000", "reason": "chain_series_unknown"})
    elif cnt.get(ser, 1) > 1: problems.append({"check": "C4_SERIES_UNAMBIGUOUS", "rcept_no": None, "known_at": "00000000", "reason": f"duplicate_series_chains:{cnt[ser]}"})
    cov = {"tool": "adjust_events.link_chain", "version": VERSION, "corp_code": corp_rec["corp_code"], "chain_series": ser,
           "chain_start": chain_start(chain), "collected_through": corp_rec["collected_through"], "list_windows": corp_rec["list_windows"],
           "fetch_error": corp_rec.get("fetch_error"), "problems": problems, "unmatched": unmatched, "corrections": corrections,
           "reports_seen": len(corp_rec["reports"]), "events": len(events)}
    return events, cov


def attach(chain: dict, corp_rec: dict, corp_series_counts=None) -> dict:
    """체인 사본에 adjustments(V4.2 사건) + adjustment_coverage 를 붙인다. 원본 체인은 바꾸지 않는다."""
    ev, cov = link_chain(chain, corp_rec, corp_series_counts)
    return dict(chain, adjustments=ev, adjustment_coverage=cov)


def series_counts(chains) -> dict:
    """발행사 체인 목록 → 회차별 체인 수(C4)."""
    c = Counter()
    for ch in (chains.values() if isinstance(chains, dict) else chains):
        s = ch.get("series")
        if s not in (None, "", "None"): c[str(int(re.search(r"\d+", str(s)).group()))] += 1
    return dict(c)


def not_collected(chain: dict, reason: str = "ADJUSTMENTS_NOT_COLLECTED") -> dict:
    """수집을 하지 않은(또는 못 한) 체인 — resolve 가 C0 실패로 전환가액을 막는다."""
    return dict(chain, adjustments=list(chain.get("adjustments") or []), adjustment_coverage={"tool": "adjust_events.not_collected", "version": VERSION,
                "not_collected": reason, "problems": [], "list_windows": [], "collected_through": None})
