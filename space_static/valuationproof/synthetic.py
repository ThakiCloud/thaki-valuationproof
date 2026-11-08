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
