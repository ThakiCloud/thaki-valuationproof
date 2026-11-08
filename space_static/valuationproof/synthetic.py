"""FULLY SYNTHETIC disclosure chain for the offline demo — a fictional issuer, NOT a real company or a real filing.

The texts below imitate only the *cell layout* of a Korean convertible-bond (CB) issuance decision report
(label cell followed by a value cell, cells separated by " | "), so that the same deterministic parsers used on
public DART filings can be exercised offline without an API key. Every name, number, date and receipt number is
made up. Receipt numbers are registered in ``_synthetic`` so reports render them as ``synthetic://...`` links.

Chain (issuer "가상정밀 주식회사 / Example Precision Co. [SYNTHETIC]", series 1):
  ver filed       board resolution date        change
  O   2025-03-14  2025-03-12                   conversion price 10,000 KRW, floor 7,000, maturity 2028-03-31
  C1  2025-06-20  2025-06-16 (new resolution)  conversion price -> 8,500
  C2  2025-09-30  2025-06-16 (same as C1 = restatement of the latest resolution)
                                               maturity -> 2028-09-30, conversion end -> 2028-08-31, yield 4.0 -> 4.5
  C3  2026-01-15  2026-01-12 (new resolution)  refixing floor -> 6,000
The default demo valuation date (as-of) is 2025-10-01: C3 is excluded, C2 is applied, nothing depends on an unconfirmed
effective date. At as-of 2025-06-18 (after C1's board date, before C1 was filed) the conversion price depends on the
unconfirmed effective date and must come out as '확인 필요(효력일 미확정)' (see EFFECTIVE_DATE_ASOF).

V4.2 adds two SYNTHETIC conversion-price adjustment filings (전환가액의조정) for the same fictional series:
  A1  filed 2025-11-21  adjustment effective (조정가액 적용일) 2025-11-20   8,500 -> 7,800
  A1' filed 2025-12-05  [기재정정] of A1 (same series, same effective date)  8,500 -> 7,750
They are served by ``fetch`` (an in-memory stand-in for OpenDART list.json / document.xml — no network, no key) so the
real collector ``adjust_events.collect_corp`` runs unchanged. effective_at (2025-11-20) is earlier than known_at
(2025-11-21 / 2025-12-05), so the two V4.2 modes differ:
  restated (everything known by COLLECT_END)        as-of 2025-11-20 -> 7,750 (A1 as corrected by A1')
  as_known, knowledge cutoff = as-of 2025-11-20     -> 8,500 (A1 not yet published)
  as_known, knowledge cutoff 2025-11-30             -> 7,800 (A1 published, correction not yet)
``load_v42(fail=...)`` simulates a broken collection (``"list_page"`` = list request error -> C1, ``"off"`` = not collected
-> C0); the conversion price then comes out as EVENT_COVERAGE_UNVERIFIED and is never auto-judged.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import _synthetic

CORP = "SYNTH001"
ISSUER = "가상정밀 주식회사 (Example Precision Co.) [SYNTHETIC — fictional issuer]"
SERIES = "1"
DEFAULT_ASOF = "2025-10-01"
EFFECTIVE_DATE_ASOF = "2025-06-18"
BANNER = "※ 본 문서는 소프트웨어 시연용 가상(합성) 공시다. 실제 회사·실제 공시·실제 수치가 아니다. (SYNTHETIC, NOT A REAL FILING)"

_BASE = {"principal": "3,000,000,000", "coupon": "1.0", "yield": "4.0", "maturity": "2028년 03월 31일", "cp": "10,000",
         "conv_start": "2026년 03월 31일", "conv_end": "2028년 02월 29일", "floor": "7,000", "issue": "2025년 03월 31일",
         "put": "2026년 09월 30일", "call": "2026년 03월 31일"}
VERSIONS = [
    # rcept_no (made up: 8-digit date + '99' + 4 digits), rcept_dt, kind, overrides, changed items, reason
    ("20250314990001", "20250314", "original", {"board": "2025년 03월 12일"}, [], ""),
    ("20250620990002", "20250620", "correction", {"cp": "8,500", "board": "2025년 06월 16일"}, ["9. 전환에 관한 사항"], "전환가액 변경 결의(가상)"),
    ("20250930990003", "20250930", "correction", {"cp": "8,500", "board": "2025년 06월 16일", "maturity": "2028년 09월 30일", "conv_end": "2028년 08월 31일", "yield": "4.5"},
     ["4. 사채의 이율", "5. 사채만기일", "9. 전환에 관한 사항"], "기재 정정 — 만기 기재 오류 정정(가상)"),
    ("20260115990004", "20260115", "correction", {"cp": "8,500", "maturity": "2028년 09월 30일", "conv_end": "2028년 08월 31일", "yield": "4.5",
                                                   "floor": "6,000", "board": "2026년 01월 12일"}, ["9. 전환에 관한 사항"], "최저 조정가액 변경 결의(가상)"),
]


def _form(v: dict) -> list[str]:
    return ["1. 사채의 종류", "회차", SERIES, "종류", "무기명식 이권부 무보증 사모 전환사채 (가상 예시)",
            "2. 사채의 권면(전자등록)총액 (원)", v["principal"],
            "4. 사채의 이율", "표면이자율 (%)", v["coupon"], "만기이자율 (%)", v["yield"],
            "5. 사채만기일", v["maturity"],
            "8. 사채발행방법", "사모",
            "9. 전환에 관한 사항", "전환비율 (%)", "100", "전환가액 (원/주)", v["cp"],
            "전환청구기간", "시작일", v["conv_start"], "종료일", v["conv_end"],
            "시가하락에따른전환가액조정", "최저 조정가액 (원)", v["floor"],
            "9-1. 옵션에 관한 사항",
            f"(가상) 조기상환청구권(Put Option): 사채권자는 {v['put']}부터 권면금액 전부 또는 일부의 조기상환을 청구할 수 있다.",
            f"(가상) 매도청구권(Call Option): 발행회사는 {v['call']}부터 사채 일부의 매도를 청구할 수 있다.",
            "12. 납입일", v["issue"],
            "17. 이사회결의일(결정일)", v["board"],
            "22. 기타 투자판단에 참고할 사항", BANNER]


def text_for(i: int) -> str:
    rno, rdt, kind, ov, items, reason = VERSIONS[i]
    v = _BASE | ov
    head = ["주요사항보고서(전환사채권 발행결정) [SYNTHETIC]", ISSUER, BANNER]
    if kind == "correction":
        prev = _BASE | VERSIONS[i - 1][3]
        head += ["정 정 신 고 (보고)", f"{rdt[:4]}년 {rdt[4:6]}월 {rdt[6:]}일",
                 "1. 정정대상 공시서류 :", "주요사항보고서(전환사채권발행결정)",
                 "2. 정정대상 공시서류의 최초제출일 :", "2025년 03월 14일",
                 "3. 정정사항", "항 목", "정정사유", "정 정 전", "정 정 후"]
        for it in items:
            head += [it, reason, "(정정 전 서식 참조 — 가상)", "(정정 후 서식 참조 — 가상)"]
        _ = prev
    return "\n".join(x + " |" for x in head + _form(v)) + "\n"


def meta() -> list[dict]:
    return [{"rcept_no": r, "rcept_dt": d, "corp_code": CORP, "corp_cls": "E", "is_correction": k == "correction",
             "report_nm": ("[기재정정]" if k == "correction" else "") + "주요사항보고서(전환사채권발행결정) [SYNTHETIC]", "synthetic": True}
            for r, d, k, *_x in VERSIONS]


def texts() -> dict:
    return {VERSIONS[i][0]: text_for(i) for i in range(len(VERSIONS))}


def write_dataset(out: str | Path) -> Path:
    """Write the synthetic chain in the same on-disk layout as a live-DART dataset: filings_all.json + doc/<rcept_no>.txt."""
    out = Path(out); (out / "doc").mkdir(parents=True, exist_ok=True)
    (out / "filings_all.json").write_text(json.dumps(meta(), ensure_ascii=False, indent=1), encoding="utf-8")
    for r, t in texts().items(): (out / "doc" / f"{r}.txt").write_text(t, encoding="utf-8")
    (out / "SYNTHETIC_README.txt").write_text(BANNER + "\n", encoding="utf-8")
    return out


def load() -> tuple[dict, dict]:
    """(chain, texts) for the synthetic issuer, built by the same chain builder used for live DART data."""
    from . import change_v3, dart_change
    from ._norm import default_norm
    tx = texts(); _synthetic.register(tx)
    chains = change_v3.build_chains_v3(meta(), tx, default_norm)["chains"]
    ch = chains[VERSIONS[0][0]]
    ch.setdefault("series", dart_change.series(tx[ch["origin"]]) or SERIES)
    ch["synthetic"] = True
    return ch, tx


# ── V4.2: synthetic conversion-price adjustment filings + in-memory OpenDART stand-in ─────────────────────────────
ADJUSTMENTS = [
    # rcept_no (made up), rcept_dt, report_nm, 조정가액 적용일, before, after, reason
    ("20251121990005", "20251121", "전환가액의조정 [SYNTHETIC]", "2025년 11월 20일", "8,500", "7,800", "시가하락에 따른 조정(가상)"),
    ("20251205990006", "20251205", "[기재정정]전환가액의조정 [SYNTHETIC]", "2025년 11월 20일", "8,500", "7,750", "조정후 가액 기재 오류 정정(가상)"),
]
V42_ASOF = "2025-11-20"          # adjustment effective date — the modes disagree here
COLLECT_END = "2026-01-31"       # 'restated' = everything published up to this date


def adjustment_text(i: int) -> str:
    rno, rdt, nm, eff, before, after, reason = ADJUSTMENTS[i]
    cells = [nm, ISSUER, BANNER, "1. 조정에 관한 사항", "회차", "상장여부", "조정전 전환가액 (원)", "조정후 전환가액 (원)",
             SERIES, "비상장", before, after, "2. 전환가능 주식수 변동", "(가상)", "3. 조정사유", reason,
             "4. 근거 및 방법", "(가상) 사채 발행조건상 시가하락 조정 조항", "5. 조정가액 적용일", eff, "6. 이사회결의일(결정일)", "-"]
    return "\n".join(c + " |" for c in cells) + "\n"


def _listing() -> list[dict]:
    rows = [{"rcept_no": r, "rcept_dt": d, "corp_code": CORP, "report_nm": m["report_nm"]} for (r, d, *_x), m in zip(VERSIONS, meta())]
    rows += [{"rcept_no": a[0], "rcept_dt": a[1], "corp_code": CORP, "report_nm": a[2]} for a in ADJUSTMENTS]
    rows.append({"rcept_no": "20251114990007", "rcept_dt": "20251114", "corp_code": CORP, "report_nm": "분기보고서 [SYNTHETIC]"})   # not an adjustment
    return rows


def _zip_xml(text: str) -> bytes:
    import io, zipfile
    body = "".join(f"<TR><TD>{c.rstrip(' |')}</TD></TR>" for c in text.splitlines() if c.strip())
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z: z.writestr("synthetic.xml", f"<DOCUMENT><TABLE>{body}</TABLE></DOCUMENT>".encode("utf-8"))
    return b.getvalue()


class SyntheticDart:
    """In-memory stand-in for OpenDART list.json / document.xml (same call signature as dart_source._call). Never touches the network.
    fail='list_page' makes every list request fail (-> C1_LIST_OK). calls records what was asked."""

    def __init__(self, fail: str | None = None, page_size: int = 3):
        self.fail, self.ps, self.calls = fail, page_size, []

    def __call__(self, ep: str, raw: bool = False, **q):
        self.calls.append(ep)
        if ep == "document.xml":
            adj = {a[0]: i for i, a in enumerate(ADJUSTMENTS)}
            r = q["rcept_no"]
            return _zip_xml(adjustment_text(adj[r]) if r in adj else texts()[r])
        if ep != "list.json" or q.get("corp_code") != CORP: return {"status": "013"}
        if self.fail == "list_page": raise ConnectionError("synthetic list failure")
        items = sorted((x for x in _listing() if q["bgn_de"] <= x["rcept_dt"] <= q["end_de"]), key=lambda x: x["rcept_no"])
        if not items: return {"status": "013"}
        pages = [items[i:i + self.ps] for i in range(0, len(items), self.ps)]
        return {"status": "000", "total_page": len(pages), "total_count": len(items), "list": pages[int(q["page_no"]) - 1]}


fetch = SyntheticDart()


def load_v42(cache_dir: str | Path | None = None, fail: str | None = None) -> tuple[dict, dict]:
    """(chain with V4.2 adjustment events + collection record, texts). Collection runs the real adjust_events.collect_corp
    against the in-memory SyntheticDart. fail: None | 'list_page' (C1 failure) | 'off' (not collected, C0)."""
    ch, tx = load()
    return collect_v42(ch, cache_dir, fail), tx


def collect_v42(ch: dict, cache_dir: str | Path | None = None, fail: str | None = None) -> dict:
    """Attach the V4.2 synthetic collection to a synthetic chain (e.g. one loaded from the exported synthetic fixture)."""
    import tempfile
    from . import adjust_events
    _synthetic.register(a[0] for a in ADJUSTMENTS); _synthetic.register(["20251114990007"])
    if fail == "off": return adjust_events.not_collected(ch, "ADJUSTMENTS_OFF(--adjustments off)")
    cd = Path(cache_dir) if cache_dir else Path(tempfile.mkdtemp(prefix="vp_synth_adj_"))
    rec = adjust_events.collect_corp(CORP, adjust_events.chain_start(ch), COLLECT_END, cd, SyntheticDart(fail=fail), sleep=0)
    out = adjust_events.attach(ch, rec, adjust_events.series_counts([ch]))
    out["synthetic"] = True
    return out
