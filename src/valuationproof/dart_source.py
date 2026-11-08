"""V4 CLI 의 공시 입력 (V4.2: 전환가액조정 수집은 adjust_events) — (1) 이미 수집한 로컬 데이터셋 재사용(기본), (2) 체인 고정본(JSON), (3) 발행사 1곳 소량 조회(--fetch).

데이터셋 형식은 scripts/dart_cb_corrections_fetch.py 출력과 같다: <dir>/filings_all.json + <dir>/doc/<rcept_no>.txt.
⛔ DART 키는 환경변수 API_K_DART(또는 DART_API_KEY)에서만 읽고 출력·저장하지 않는다. 조회는 발행사 1곳·목록+원문만.
⛔ 공시 원문·체인 파생물은 호출자가 준 경로에만 쓴다(Git·공유 경로 금지는 호출자 책임 — README 참조).
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import time
import urllib.parse
import urllib.request
import zipfile
from datetime import date, timedelta
from pathlib import Path

from . import asof_terms, change_v3
from ._norm import default_norm as norm

API = "https://opendart.fss.or.kr/api/"


def load_dataset(d: str | Path) -> tuple[list[dict], dict]:
    d = Path(d)
    meta = json.loads((d / "filings_all.json").read_text(encoding="utf-8"))
    texts = {m["rcept_no"]: (d / "doc" / f"{m['rcept_no']}.txt").read_text(encoding="utf-8")
             for m in meta if (d / "doc" / f"{m['rcept_no']}.txt").exists()}
    return meta, texts


def chains_from(meta: list[dict], texts: dict, corp: str | None = None) -> dict:
    if corp: meta = [m for m in meta if m["corp_code"] == corp]
    return change_v3.build_chains_v3(meta, texts, norm)["chains"]


# ── 체인 고정본: 원문 없이 재현 가능한 최소 공시 파생물(값·위치·원문 sha256) ─────────────────────────────
def export_fixture(chain: dict, texts: dict, path: str | Path, note: str = "") -> dict:
    vs = []
    for v in chain["versions"]:
        t = texts.get(v["rcept_no"])
        vs.append({k: v.get(k) for k in ("rcept_no", "rcept_dt", "kind", "values", "link_method", "link_status", "changed_items")}
                  | {"doc_sha256": hashlib.sha256(t.encode("utf-8")).hexdigest() if t else None,
                     "terms_cache": asof_terms.version_terms(t, v["values"]) if t else v.get("terms_cache"),
                     "board_dt": asof_terms.board_date(t) if t else v.get("board_dt")})
    fx = {"kind": "valuationproof.chain_fixture", "version": asof_terms.VERSION, "source": "OpenDART 공개 공시(전환사채권발행결정·기재정정)",
          "note": note or "공시 원문은 넣지 않는다. 값·원문 위치·sha256 만.", "origin": chain["origin"], "corp_code": chain.get("corp_code"),
          "series": chain.get("series"), "versions": vs}
    for k in ("adjustments", "adjustment_coverage"):                     # V4.2 — 수집한 조정 사건·완전성 기록(오프라인 재현)
        if chain.get(k) is not None: fx[k] = chain[k]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(fx, ensure_ascii=False, indent=1), encoding="utf-8")
    return fx


def load_fixture(path: str | Path) -> tuple[dict, dict]:
    fx = json.loads(Path(path).read_text(encoding="utf-8"))
    if fx.get("kind") != "valuationproof.chain_fixture": raise ValueError("NOT_A_CHAIN_FIXTURE")
    ch = {"origin": fx["origin"], "corp_code": fx["corp_code"], "series": fx["series"], "versions": fx["versions"]}
    for k in ("adjustments", "adjustment_coverage"):
        if fx.get(k) is not None: ch[k] = fx[k]
    ch["structural_diff"] = change_v3.structural_diff(ch)
    return ch, {}


# ── 소량 조회 ────────────────────────────────────────────────────────────────────────────────────
def _key() -> str:
    for v in ("API_K_DART", "DART_API_KEY"):
        if os.environ.get(v, "").strip(): return os.environ[v].strip()
    raise SystemExit("DART 키 없음 — 환경변수 API_K_DART 를 설정한다(값은 출력하지 않는다)")


def _call(ep: str, raw: bool = False, **q):
    q["crtfc_key"] = _key(); url = API + ep + "?" + urllib.parse.urlencode(q)
    for k in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "valuationproof-v4"}), timeout=60) as r: b = r.read()
            return b if raw else json.loads(b)
        except Exception as e:  # noqa: BLE001
            if k == 2: raise RuntimeError(f"{ep} 실패: {type(e).__name__}") from None
            time.sleep(2 + 3 * k)


def _xml_text(b: bytes) -> str:
    """scripts/dart_cb_fetch.xml_text 와 같은 변환(셀 경계 ' | ') — 같은 위치 규칙을 쓰기 위해 동일해야 한다."""
    z = zipfile.ZipFile(io.BytesIO(b)); parts = []
    for n in z.namelist():
        t = z.read(n).decode("utf-8", errors="replace")
        t = re.sub(r"</(TD|TH|TE|TU)>", " | ", t, flags=re.I)
        t = re.sub(r"</(TR|P|TITLE|SECTION-\d)>|<BR\s*/?>", "\n", t, flags=re.I)
        t = re.sub(r"<[^>]+>", "", t)
        parts.append(re.sub(r"[ \t ]+", " ", t))
    return "\n".join(x.strip() for x in "\n".join(parts).splitlines() if x.strip())


def fetch_corp(corp: str, out: str | Path, begin: str = "20200101", end: str | None = None, max_docs: int = 40) -> dict:
    """발행사 1곳의 CB 발행결정(정정 포함) 목록 + 원문. 이미 있는 원문은 건너뛴다. 호출 수를 돌려준다."""
    out = Path(out); (out / "doc").mkdir(parents=True, exist_ok=True)
    end = end or date.today().strftime("%Y%m%d"); calls = 0
    b = date(int(begin[:4]), int(begin[4:6]), int(begin[6:])); e = date(int(end[:4]), int(end[4:6]), int(end[6:]))
    fil = []
    while b <= e:
        w = min(e, date(b.year + 2, b.month, min(b.day, 28)) - timedelta(days=1)); page = 1   # corp_code 지정 시 기간 제한 완화
        while True:
            r = _call("list.json", corp_code=corp, bgn_de=b.strftime("%Y%m%d"), end_de=w.strftime("%Y%m%d"), pblntf_ty="B", page_no=page, page_count=100); calls += 1
            if r.get("status") == "013": break
            if r.get("status") != "000": raise RuntimeError(f"list status {r.get('status')}")
            fil += [x for x in r["list"] if "전환사채권발행결정" in x["report_nm"].replace(" ", "")]
            if page >= int(r.get("total_page", 1)): break
            page += 1
        b = w + timedelta(days=1)
    fil = sorted({x["rcept_no"]: x for x in fil}.values(), key=lambda x: x["rcept_no"])
    meta = [{"rcept_no": x["rcept_no"], "rcept_dt": x["rcept_dt"], "corp_code": x["corp_code"], "corp_cls": x.get("corp_cls"),
             "is_correction": "정정" in x["report_nm"], "report_nm": x["report_nm"]} for x in fil]
    p0 = out / "filings_all.json"
    old = json.loads(p0.read_text(encoding="utf-8")) if p0.exists() else []
    merged = sorted({m["rcept_no"]: m for m in old + meta}.values(), key=lambda m: m["rcept_no"])
    p0.write_text(json.dumps(merged, ensure_ascii=False), encoding="utf-8")
    got = 0
    for m in meta[:max_docs]:
        p = out / "doc" / f"{m['rcept_no']}.txt"
        if p.exists(): continue
        raw = _call("document.xml", raw=True, rcept_no=m["rcept_no"]); calls += 1
        if raw[:2] == b"PK": p.write_text(_xml_text(raw), encoding="utf-8"); got += 1
    return {"filings": len(meta), "docs_new": got, "api_calls": calls}
