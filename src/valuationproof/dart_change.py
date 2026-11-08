"""ValuationProof V2 ① Disclosure Change Tracker — DART CB 원공시와 기재정정 공시를 한 '발행 체인'으로 묶는다.

- 정정공시 원문 = [정정 표(정정 전/후, 자유서술 포함)] + [정정 반영된 전체 서식]. 필드 값은 **전체 서식**에서
  dart_table.extract 로 뽑고, 버전 사이 차이로 변경을 낸다(정정 표의 자유서술은 파싱하지 않는다).
  정정 표의 항목명(예: '9-1. 옵션에 관한 사항')은 changed_items 로만 남긴다 — 조기상환·콜옵션처럼
  구조화 필드가 없는 조건의 변경 신호.
- 연결 규칙: 정정공시의 '최초제출일' + corp_code == 원공시 접수일(또는 접수번호 앞 8자리) + corp_code.
  같은 날 같은 회사 원공시가 둘 이상이면 회차가 정확히 하나와 같을 때만 연결, 아니면 ambiguous.
  후보가 하나여도 양쪽 회차가 읽히고 다르면 series_conflict 로 비운다 — 틀리게 묶는 것보다 비우는 쪽이 낫다.
- 효력 기준: 버전의 기준일은 접수일(rcept_dt). version_at(as_of) = as_of 이전에 접수된 마지막 버전.
  (정정이 소급 효력을 갖는지 여부는 공시만으로 확정되지 않는다 → 감사 결과는 회계사 검토 대상.)
규칙은 dev(2025) 공시만 보고 만들었다.
"""
from __future__ import annotations

import re
from collections import defaultdict

from . import dart_table

FIELDS = list(dart_table.LABELS)
_FORM_START = re.compile(r"^1\.\s*사채의\s*종류\s*\|?\s*$")
_FIRST = re.compile(r"최초\s*제출일\s*:?[\s|]*(\d{4})\s*(?:년|[.\-/])\s*(\d{1,2})\s*(?:월|[.\-/])\s*(\d{1,2})")   # 2024년 1월 2일 · 2024-01-02 · 2024.1.2
_ITEM = re.compile(r"^(\d+(?:-\d+)?\.\s*[^|]{2,80}?)\s*\|\s*$")


def form_text(text: str) -> str:
    """정정 반영된 전체 서식(마지막 '1. 사채의 종류' 이후). 원공시면 원문 전체와 거의 같다."""
    lines = text.splitlines()
    idx = [i for i, l in enumerate(lines) if _FORM_START.match(l.strip())]
    return "\n".join(lines[idx[-1]:]) if idx else text


_SERIES = re.compile(r"^1\.\s*사채의\s*종류\s*\|?\s*\n\s*회\s*차\s*\|?\s*\n\s*\(?제?\s*(\d+)", re.M)


def series(text: str) -> str | None:
    """전체 서식의 '1. 사채의 종류 → 회차' 값. 같은 날 같은 회사 원공시가 여럿일 때 연결을 가르는 데만 쓴다."""
    m = _SERIES.search(form_text(text))
    return m.group(1) if m else None


def parse_correction(text: str) -> dict:
    m = _FIRST.search(text)
    first = f"{int(m.group(1)):04d}{int(m.group(2)):02d}{int(m.group(3)):02d}" if m else None
    lines = text.splitlines(); start = next((i for i, l in enumerate(lines) if "정정사항" in l), None)
    stop = next((i for i, l in enumerate(lines) if _FORM_START.match(l.strip())), len(lines))
    items = []
    if start is not None:
        for l in lines[start:stop]:
            mm = _ITEM.match(l.strip())
            if mm and mm.group(1) not in items: items.append(mm.group(1))
    return {"original_first_date": first, "changed_items": items}


def build_chains(meta: list[dict], texts: dict[str, str], norm) -> dict:
    """meta: [{rcept_no, rcept_dt, corp_code, is_correction}], texts: rcept_no→원문, norm(field, raw)→정규화값."""
    orig_by = defaultdict(list)
    for m in meta:   # 접수일(rcept_dt)과 접수번호 앞 8자리가 다른 공시가 있다(예: 번호 앞 8자리 1216·접수일 1217)
        if not m["is_correction"]:
            for day in {m["rcept_dt"], m["rcept_no"][:8]}: orig_by[(m["corp_code"], day)].append(m["rcept_no"])
    chains = {m["rcept_no"]: {"corp_code": m["corp_code"], "origin": m["rcept_no"], "versions": []}
              for m in meta if not m["is_correction"]}
    unlinked = {"no_first_date": 0, "origin_outside_window": 0, "ambiguous": 0, "no_text": 0, "series_conflict": 0}
    for m in sorted(meta, key=lambda x: x["rcept_no"]):
        t = texts.get(m["rcept_no"])
        if t is None:
            if m["is_correction"]: unlinked["no_text"] += 1
            continue
        vals = {f: norm(f, v) for f, v in dart_table.extract(form_text(t)).items()}
        if not m["is_correction"]:
            chains[m["rcept_no"]]["versions"].insert(0, {"rcept_no": m["rcept_no"], "rcept_dt": m["rcept_dt"], "kind": "original", "values": vals})
            continue
        c = parse_correction(t)
        if not c["original_first_date"]: unlinked["no_first_date"] += 1; continue
        cand = orig_by.get((m["corp_code"], c["original_first_date"]), [])
        if not cand: unlinked["origin_outside_window"] += 1; continue
        if len(cand) > 1:   # 회차가 정확히 하나와 같을 때만 연결
            sr = series(t); hit = [o for o in cand if sr and o in texts and series(texts[o]) == sr]
            if len(hit) != 1: unlinked["ambiguous"] += 1; continue
            cand = hit; unlinked["linked_by_series"] = unlinked.get("linked_by_series", 0) + 1
        else:   # 후보가 하나여도 양쪽 회차가 읽히는데 다르면 묶지 않는다(같은 날 다른 회차 원공시가 창 밖·원문 실패)
            sr = series(t); so = series(texts[cand[0]]) if cand[0] in texts else None
            if sr and so and sr != so: unlinked["series_conflict"] = unlinked.get("series_conflict", 0) + 1; continue
        chains[cand[0]]["versions"].append({"rcept_no": m["rcept_no"], "rcept_dt": m["rcept_dt"], "kind": "correction",
                                            "values": vals, "changed_items": c["changed_items"]})
    for ch in chains.values():
        ch["versions"].sort(key=lambda v: (v["kind"] != "original", v["rcept_no"]))
        ch["changes"] = diff_versions(ch["versions"])
    return {"chains": chains, "unlinked_corrections": unlinked}


def diff_versions(versions: list[dict]) -> list[dict]:
    out = []
    for a, b in zip(versions, versions[1:]):
        for f in FIELDS:
            va, vb = a["values"].get(f), b["values"].get(f)
            if va != vb: out.append({"field": f, "from": va, "to": vb, "from_rcept": a["rcept_no"], "to_rcept": b["rcept_no"], "effective_filed": b["rcept_dt"]})
    return out


def version_at(chain: dict, as_of: str) -> dict | None:
    """as_of(YYYYMMDD) 이전에 접수된 마지막 버전. 없으면 None."""
    vs = [v for v in chain["versions"] if v["rcept_dt"] <= as_of]
    return vs[-1] if vs else None
