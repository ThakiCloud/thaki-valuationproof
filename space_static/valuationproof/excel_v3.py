"""ValuationProof V3 Excel Auditor — 셀 위치에 의존하지 않는 평가 Excel 조건 감사 (결정론, 읽기 전용).

V2(excel_audit.py) 대비 고친 것 (V2 결함 출처: ledger/experiments.jsonl `D-v2-excel-layout-1107`):
  - 사전 밖 라벨 read 0.1456 → 동의어 사전 + 키워드 규칙 + 괄호 단위 분리 + 수식어(최초·변경 전) 판별
  - 억원·천원 미환산(verdict 0.8483·오탐 100%) → 셀 텍스트·셀 서식·라벨 괄호·열/행 머리·'(단위: …)' 표기 환산
  - 다회차 첫 회차 고정(read 0.0) → 회차 열/블록/시트명 인식, 대상 회차만 감사, 회차 불명이면 REVIEW_REQUIRED
그 밖에: 병합 셀, Base/Bull/Bear 시나리오(기준 시나리오만 감사), 다중 시트(값 충돌은 REVIEW_REQUIRED),
수식/캐시값/외부 참조 구분(data_only 두 번 로드, 외부 참조는 확정 불가), 평가기준일 vs 효력발생일 분리.

⛔ 원본 Excel 은 바이트로 한 번 읽어 메모리에서만 연다. 저장하지 않는다. 감사 전후 sha256 을 결과에 남기고 다르면 예외.
⛔ 값을 확정할 수 없으면 verdict=REVIEW_REQUIRED + reason. 추측하지 않는다(가장 그럴듯한 셀을 고르지 않는다).
⛔ 판정은 회계사 검토 후보다. approval.status 는 항상 PENDING 으로 시작한다(자동 승인 없음).
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import io
import re
from dataclasses import dataclass, field as _f
from pathlib import Path

from . import dart_change, dart_table

VERSION = "3.0.0"
FIELDS = list(dart_change.FIELDS)

# ── 사전(코드 상수). 키는 _key() 정규화 후 비교한다. ────────────────────────────────────────────────
SYNONYMS = {
    "principal_krw": ["권면총액", "권면(전자등록)총액", "전자등록총액", "액면총액", "권면금액", "액면금액", "발행총액", "사채총액",
                      "사채발행총액", "발행금액", "사채금액", "사채원금", "원금", "발행규모", "facevalue", "faceamount", "principal",
                      "principalamount", "notional", "notionalamount", "paramount", "parvalue", "issueamount", "issuesize"],
    "conversion_price_krw": ["전환가액", "전환가격", "전환단가", "주당전환가액", "주당전환가격", "행사가액", "행사가격", "조정후전환가액",
                             "현재전환가액", "conversionprice", "exerciseprice", "strike", "strikeprice"],
    "refixing_floor_krw": ["최저조정가액", "최저조정가격", "리픽싱하한", "리픽싱한도", "리픽싱최저가", "조정하한", "전환가액조정하한",
                           "전환가조정최저한도", "시가하락조정하한", "refixingfloor", "refixfloor", "floorprice", "resetfloor", "conversionfloor"],
    "coupon_rate_pct": ["표면이자율", "표면금리", "표면이율", "표면이자", "액면이자율", "쿠폰", "쿠폰금리", "쿠폰이자율", "coupon", "couponrate",
                        "nominalrate", "nominalinterestrate"],
    "redemption_yield_pct": ["만기이자율", "만기보장수익률", "만기수익률", "보장수익률", "만기상환수익률", "만기보장이율", "ytm", "yieldtomaturity",
                             "redemptionyield", "guaranteedyield", "maturityyield"],
    "maturity_date": ["사채만기일", "만기일", "만기일자", "만기", "상환기일", "원금상환기일", "원금상환일", "만기상환일", "maturity", "maturitydate",
                      "redemptiondate", "finalmaturity"],
    "issue_date": ["납입일", "납입일자", "발행일", "발행일자", "사채발행일", "청약납입일", "issuedate", "settlementdate", "closingdate", "paymentdate"],
}
ASOF_LABELS = ["평가기준일", "평가기준일자", "가치평가기준일", "공정가치평가기준일", "측정기준일", "평가일", "기준일", "valuationdate", "asof",
               "asofdate", "measurementdate"]
EFFECTIVE_LABELS = ["효력발생일", "효력일", "변경효력일", "조정효력일", "효력발생일자", "적용일", "effectivedate", "effectivefrom"]
# 이 수식어가 붙은 라벨은 '현재 입력값'이 아니다(이전 값·이력). 입력으로 쓰지 않고 decoy 로 남긴다.
DECOY_QUALIFIERS = ["최초", "당초", "변경전", "정정전", "조정전", "이전", "종전", "과거", "이력", "히스토리", "history", "original", "initial",
                    "prior", "previous", "old", "before"]
# 키워드 규칙: (field, 모두 포함, 하나 이상 포함, 제외). 사전에서 못 찾을 때만 쓴다. 두 필드 이상 걸리면 ambiguous.
KEYWORD_RULES = [
    ("refixing_floor_krw", (), ("최저", "하한", "floor", "한도", "최저가"), ("이자", "수익률", "기간", "일")),
    ("conversion_price_krw", (), ("전환가", "전환단가", "행사가", "conversionprice", "exerciseprice", "strike"), ("최저", "하한", "floor", "한도", "기간", "주식수", "청구", "비율")),
    ("redemption_yield_pct", (), ("만기이자", "만기보장", "만기수익", "보장수익", "상환수익", "ytm", "yield"), ()),
    ("coupon_rate_pct", (), ("표면", "쿠폰", "coupon", "액면이자", "nominal"), ("만기", "총액", "금액")),
    ("maturity_date", (), ("만기", "상환기일", "상환일", "maturity", "redemptiondate"), ("이자", "수익률", "이율", "yield", "보장", "기간", "연장")),
    ("issue_date", (), ("납입일", "발행일", "issuedate", "closingdate", "settlement"), ("결의", "이사회", "청구")),
    ("principal_krw", (), ("권면", "액면총", "액면금", "발행총액", "사채총액", "발행금액", "사채금액", "원금", "face", "principal", "notional"), ("이자", "상환율", "비율", "%")),
]
UNIT_SCALE = {"조원": 1e12, "십억원": 1e9, "억원": 1e8, "천만원": 1e7, "백만원": 1e6, "만원": 1e4, "천원": 1e3, "원/주": 1.0, "원": 1.0,
              "억": 1e8, "백만": 1e6, "천": 1e3, "krwbn": 1e9, "krwmn": 1e6, "billion": 1e9, "million": 1e6, "bn": 1e9, "mn": 1e6, "krw": 1.0}
_UNIT_KEYS = sorted(UNIT_SCALE, key=len, reverse=True)
SCENARIO_TOKENS = {"base": "base", "basecase": "base", "기본": "base", "기본시나리오": "base", "중립": "base", "기준": "base", "mid": "base",
                   "bull": "bull", "bullcase": "bull", "낙관": "bull", "upside": "bull", "best": "bull",
                   "bear": "bear", "bearcase": "bear", "비관": "bear", "downside": "bear", "worst": "bear", "stress": "bear", "스트레스": "bear"}
_SERIES = re.compile(r"^(?:제\s*)?(\d{1,3})\s*(?:회차|회)(?:\s*(?:차|무기명|무보증|사모|공모|전환사채|사채|cb|분|\(.*\)|.{0,20}))?$", re.I)
_SERIES_EN = re.compile(r"^(?:series|tranche|트랜치)\s*#?\s*(\d{1,3})$", re.I)
_EXT_REF = re.compile(r"\[[^\]]+\]|'[A-Za-z]:\\|https?://", re.I)
_REF = re.compile(r"^=\s*(?:'?([^'!\[\]]+)'?!)?\$?([A-Z]{1,3})\$?(\d+)\s*$")
PCT_MAX = 30.0       # 표면·만기 이자율 상식 범위(%). 넘으면 단위 오류 후보.


def _key(s) -> str:
    return re.sub(r"[\s:_\-·.,/()\[\]（）%*#]", "", str(s)).lower()


_SYN_KEYS = {f: {_key(s) for s in v} for f, v in SYNONYMS.items()}
_ASOF_KEYS = {_key(s) for s in ASOF_LABELS}
_EFF_KEYS = {_key(s) for s in EFFECTIVE_LABELS}


def unit_in(text: str | None) -> tuple[str, float] | None:
    """텍스트 안의 금액/퍼센트 단위. '(원/주)'·'(%)' 도 돌려준다."""
    if not text: return None
    t = re.sub(r"\s", "", str(text)).lower()
    if "%" in t or "퍼센트" in t or "percent" in t: return ("%", 1.0)
    if "bp" in t and re.search(r"(?<![a-z])bps?(?![a-z])", t): return ("bp", 0.01)
    for u in _UNIT_KEYS:
        if u in t: return (u, UNIT_SCALE[u])
    return None


def parse_label(text) -> dict | None:
    """라벨 셀 텍스트 → {kind: field|asof|effective, field, unit, decoy, ambiguous}. 라벨이 아니면 None."""
    if not isinstance(text, str) or text.startswith("=") or not text.strip(): return None
    raw = text.strip()
    if len(raw) > 60: return None
    paren = " ".join(re.findall(r"[\(\[（]([^\)\]）]*)[\)\]）]", raw))
    base = re.sub(r"[\(\[（][^\)\]）]*[\)\]）]", "", raw)
    k = _key(base)
    if not k or len(k) > 30 or re.fullmatch(r"[\d,.\-]+", k): return None
    unit = unit_in(paren)
    decoy = any(q in k for q in DECOY_QUALIFIERS)
    kc = k
    for q in DECOY_QUALIFIERS: kc = kc.replace(q, "")
    if kc in _ASOF_KEYS: return {"kind": "asof", "field": None, "unit": unit, "decoy": decoy, "how": "dict"}
    if kc in _EFF_KEYS or any(e in kc for e in ("효력발생", "effective")):
        return {"kind": "effective", "field": None, "unit": unit, "decoy": decoy, "how": "dict"}
    if any(a in kc for a in ("평가기준", "valuationdate")): return {"kind": "asof", "field": None, "unit": unit, "decoy": decoy, "how": "kw"}
    hits = [f for f, ks in _SYN_KEYS.items() if kc in ks]
    how = "dict"
    if not hits:
        hits = [f for f, ks in _SYN_KEYS.items() if any(kc.startswith(s) and len(kc) - len(s) <= 2 for s in ks if len(s) >= 3)]
    if not hits:
        how = "kw"
        for f, need, anyof, none in KEYWORD_RULES:
            if all(n in kc for n in need) and any(a in kc for a in anyof) and not any(x in kc for x in none):
                hits.append(f)
        # 만기이자율·만기일 같은 겹침: 더 구체적인(이자·수익률) 쪽이 이긴다
        if "redemption_yield_pct" in hits: hits = [h for h in hits if h not in ("maturity_date", "coupon_rate_pct")]
        if "refixing_floor_krw" in hits: hits = [h for h in hits if h != "conversion_price_krw"]
    if not hits:
        if kc in ("이자율", "금리", "이율", "interestrate", "rate"): return {"kind": "field", "field": None, "ambiguous": ["coupon_rate_pct", "redemption_yield_pct"], "unit": unit, "decoy": decoy, "how": "kw"}
        return None
    if len(set(hits)) > 1: return {"kind": "field", "field": None, "ambiguous": sorted(set(hits)), "unit": unit, "decoy": decoy, "how": how}
    return {"kind": "field", "field": hits[0], "unit": unit, "decoy": decoy, "how": how}


def header_token(v) -> tuple[str, str] | None:
    """열/행/블록 머리 → ('series', '7') | ('scenario', 'base'|'bull'|'bear')."""
    if not isinstance(v, str) or not v.strip() or len(v) > 40: return None
    s = v.strip(); k = _key(re.sub(r"[\[\]]", "", s))
    if k in SCENARIO_TOKENS: return ("scenario", SCENARIO_TOKENS[k])
    for t, name in SCENARIO_TOKENS.items():
        if len(t) >= 4 and (k == t + "case" or k == t + "시나리오" or k == "시나리오" + t): return ("scenario", name)
    s2 = re.sub(r"^[\[\(<]|[\]\)>]$", "", s).strip()
    m = _SERIES.match(s2) or _SERIES_EN.match(s2)
    if m: return ("series", str(int(m.group(1))))
    return None


def fmt_unit(fmt: str | None) -> tuple[float, str | None, bool]:
    """셀 서식 → (저장값에 곱할 배수, 서식 리터럴 단위, % 서식 여부).
    '#,##0,,"백만원"' 처럼 천 단위 콤마 스케일이 있으면 저장값은 원 단위 그대로(표시만 나눔) → 배수 1."""
    if not fmt or fmt == "General": return 1.0, None, False
    lits = " ".join(re.findall(r'"([^"]*)"', fmt)) + " " + " ".join(re.findall(r"\\(.)", fmt))
    body = re.sub(r'"[^"]*"', "", fmt).split(";")[0]
    pct = "%" in body
    scaling = len(re.search(r"[0#?](,*)(?:[^0#?,]*)$", body).group(1)) if re.search(r"[0#?]", body) else 0
    u = unit_in(lits)
    if u and u[0] not in ("%", "bp") and u[1] != 1.0:
        if scaling and abs(1000 ** scaling - u[1]) < 1e-6: return 1.0, u[0], pct      # 저장값 원, 표시만 환산
        if scaling: return 1.0, u[0] + "(scale_mismatch)", pct
        return u[1], u[0], pct
    return 1.0, (u[0] if u else None), pct


def _num(v) -> tuple[float | None, tuple[str, float] | None, str | None]:
    """값 → (숫자, 텍스트 안 단위, 실패 사유)."""
    if isinstance(v, bool): return None, None, "boolean"
    if isinstance(v, (int, float)): return float(v), None, None
    if not isinstance(v, str) or not v.strip(): return None, None, "empty"
    s = v.strip().replace("₩", "").replace("\\", "")
    nums = re.findall(r"-?\d[\d,]*(?:\.\d+)?", s)
    if not nums: return None, None, "no_number"
    if len(nums) > 1: return None, None, "multiple_numbers"
    return float(nums[0].replace(",", "")), unit_in(s), None


def _excel_serial(x: float) -> _dt.date | None:
    if 20000 <= x <= 80000: return _dt.date(1899, 12, 30) + _dt.timedelta(days=int(x))
    return None


def norm_date(v, fmt: str = "") -> tuple[str | None, str | None]:
    if isinstance(v, _dt.datetime): return v.date().isoformat(), None
    if isinstance(v, _dt.date): return v.isoformat(), None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if 19000101 <= v <= 21001231 and float(v).is_integer():
            s = str(int(v)); v = s
        else:
            d = _excel_serial(float(v))
            return (d.isoformat(), "excel_serial") if d else (None, "unparseable_date")
    if not isinstance(v, str): return None, "unparseable_date"
    n = re.findall(r"\d+", v)
    if len(n) == 1 and len(n[0]) == 8: n = [n[0][:4], n[0][4:6], n[0][6:]]
    if len(n) < 3 or len(n[0]) != 4: return None, "unparseable_date"
    try: return _dt.date(int(n[0]), int(n[1]), int(n[2])).isoformat(), None
    except ValueError: return None, "invalid_date"


@dataclass
class Cand:
    field: str
    sheet: str
    cell: str
    label_cell: str
    label: str
    label_info: dict
    series: str | None = None
    scenario: str | None = None
    unit_ctx: list = _f(default_factory=list)     # [(source, unit, scale)] 우선순위 높은 순
    raw: dict = _f(default_factory=dict)


class WorkbookV3:
    """바이트로 읽어 data_only=False/True 두 번 연다. 파일은 다시 쓰지 않는다."""

    def __init__(self, path: str | Path):
        import openpyxl
        self.path = Path(path)
        self.data = self.path.read_bytes()
        self.sha256 = hashlib.sha256(self.data).hexdigest()
        self.f = openpyxl.load_workbook(io.BytesIO(self.data), data_only=False)
        self.v = openpyxl.load_workbook(io.BytesIO(self.data), data_only=True)
        self.merged = {}
        for ws in self.f.worksheets:
            m = {}
            for rg in ws.merged_cells.ranges:
                for r in range(rg.min_row, rg.max_row + 1):
                    for c in range(rg.min_col, rg.max_col + 1): m[(r, c)] = (rg.min_row, rg.min_col)
            self.merged[ws.title] = m

    def cell(self, sheet: str, row: int, col: int, depth: int = 0) -> dict:
        from openpyxl.utils import get_column_letter
        if sheet not in self.f.sheetnames: return {"sheet": sheet, "cell": f"{get_column_letter(col)}{row}", "value": None, "formula": None, "resolved_by": "missing_sheet", "number_format": None}
        anchor = self.merged[sheet].get((row, col), (row, col)); row, col = anchor
        cf, cv = self.f[sheet].cell(row, col), self.v[sheet].cell(row, col)
        formula = cf.value if isinstance(cf.value, str) and cf.value.startswith("=") else None
        info = {"sheet": sheet, "cell": cf.coordinate, "formula": formula, "number_format": cf.number_format}
        if not formula: return info | {"value": cf.value, "resolved_by": "literal"}
        if _EXT_REF.search(formula):
            return info | {"value": cv.value, "resolved_by": "external_ref"}
        if cv.value is not None: return info | {"value": cv.value, "resolved_by": "cached"}
        m = _REF.match(formula)
        if m and depth < 6:
            from openpyxl.utils import column_index_from_string
            t = self.cell(m.group(1) or sheet, int(m.group(3)), column_index_from_string(m.group(2)), depth + 1)
            return info | {"value": t["value"], "resolved_by": f"ref:{t['sheet']}!{t['cell']}" if t["resolved_by"] in ("literal", "cached") or t["resolved_by"].startswith("ref:") else t["resolved_by"],
                           "number_format": cf.number_format if cf.number_format != "General" else t["number_format"]}
        return info | {"value": None, "resolved_by": "formula_unevaluated"}

    # ── 후보 수집 ────────────────────────────────────────────────────────────────────────────────
    def scan(self) -> dict:
        cands, asof, eff, ambiguous = [], [], [], []
        for ws in self.f.worksheets:
            title = ws.title; mg = self.merged[title]
            grid = {}
            for row in ws.iter_rows():
                for c in row:
                    if (c.row, c.column) in mg and mg[(c.row, c.column)] != (c.row, c.column): continue
                    if c.value is not None and not (isinstance(c.value, str) and not c.value.strip()): grid[(c.row, c.column)] = c.value
            labels = {rc: parse_label(v) for rc, v in grid.items() if isinstance(v, str)}
            labels = {rc: li for rc, li in labels.items() if li}
            heads = {rc: header_token(v) for rc, v in grid.items() if isinstance(v, str)}
            heads = {rc: h for rc, h in heads.items() if h}
            sheet_tok = header_token(title)
            # 머리 행: 같은 행에 회차/시나리오 토큰 2개 이상
            by_row = {}
            for (r, c), h in heads.items(): by_row.setdefault(r, []).append((c, h))
            head_rows = {r: dict(v) for r, v in by_row.items() if len(v) >= 2}
            # 블록 머리: 행에 셀이 그것 하나뿐(또는 병합)인 회차/시나리오 토큰
            row_cells = {}
            for (r, c) in grid: row_cells.setdefault(r, []).append(c)
            blocks = sorted((r, h) for (r, c), h in heads.items() if r not in head_rows and len(row_cells.get(r, [])) == 1)
            # 단위 표기: '(단위: 억원)'·'단위 : 천원'
            unit_marks = sorted((r, c, unit_in(v)) for (r, c), v in grid.items() if isinstance(v, str) and "단위" in v and unit_in(v))
            # 가로 나열형(라벨 머리 행): 같은 행에 필드 라벨 2개 이상
            lab_rows = {}
            for (r, c), li in labels.items():
                if li["kind"] == "field" and li.get("field"): lab_rows.setdefault(r, []).append(c)
            trans_rows = {r for r, cs in lab_rows.items() if len(cs) >= 2}

            def ctx_for(r, c):
                ser = sheet_tok[1] if sheet_tok and sheet_tok[0] == "series" else None
                scn = sheet_tok[1] if sheet_tok and sheet_tok[0] == "scenario" else None
                for br, h in blocks:
                    if br < r:
                        if h[0] == "series": ser = h[1]
                        else: scn = h[1]
                um = [(u, ur) for ur, uc, u in unit_marks if ur < r or (ur == r and uc < c)]
                return ser, scn, (um[-1][0] if um else None)

            for (r, c), li in labels.items():
                lab_cell = f"{title}!{ws.cell(r, c).coordinate}"
                ser0, scn0, sheet_unit = ctx_for(r, c)
                targets = []      # (row, col, series, scenario, header_unit)
                hdr = max((hr for hr in head_rows if hr < r), default=None)
                if hdr is not None and any(x not in row_cells for x in range(hdr + 1, r)): hdr = None   # 빈 행 너머 머리는 다른 표
                hcols = {cc: h for cc, h in head_rows[hdr].items() if cc > c} if hdr else {}
                if r in trans_rows:
                    for rr in range(r + 1, r + 31):
                        if rr not in row_cells: break                      # 빈 행 = 표 끝
                        v = grid.get((rr, c))
                        if v is None or (isinstance(v, str) and parse_label(v)): continue
                        rh = None
                        # V4 수정(전치 표 회차 태그 유실): 가장 가까운 왼쪽 셀이 아니라 왼쪽에서 처음 나오는 회차/시나리오 머리를 쓴다.
                        # V3(35ddff0)은 바로 왼쪽 값 셀(숫자)을 잡아 태그를 잃었다 → hidden H_transposed_series read 0.19.
                        for cc in range(c - 1, 0, -1):
                            if (rr, cc) in grid:
                                h = header_token(grid[(rr, cc)])
                                if h: rh = h; break
                        targets.append((rr, c, rh[1] if rh and rh[0] == "series" else ser0, rh[1] if rh and rh[0] == "scenario" else scn0, None))
                elif hcols:
                    for cc, h in sorted(hcols.items()):
                        targets.append((r, cc, h[1] if h[0] == "series" else ser0, h[1] if h[0] == "scenario" else scn0, None))
                    # 머리 행에 회차·시나리오가 섞여 있으면(2단 머리) 위 머리 행도 본다
                    upper = max((hr for hr in head_rows if hr < hdr), default=None)
                    if upper is not None and any(x not in row_cells for x in range(upper + 1, hdr)): upper = None
                    if upper:
                        up = {cc: h for cc, h in head_rows[upper].items() if cc > c}
                        nt = []
                        for (tr, tc, s, sc, hu) in targets:
                            prev = max((cc for cc in up if cc <= tc), default=None)
                            if prev is not None:
                                h = up[prev]; s = h[1] if h[0] == "series" else s; sc = h[1] if h[0] == "scenario" else sc
                            nt.append((tr, tc, s, sc, hu))
                        targets = nt
                else:
                    tgt = None
                    for dc in range(1, 5):
                        if (r, c + dc) in mg and mg[(r, c + dc)] == (r, c): continue
                        v = grid.get((r, c + dc))
                        if v is None: continue
                        if isinstance(v, str) and (parse_label(v) or header_token(v)): break
                        if isinstance(v, str) and unit_in(v) and not re.search(r"\d", v): continue   # '원'·'%' 단위 칸
                        tgt = (r, c + dc); break
                    if tgt is None:
                        for dr in (1, 2):
                            v = grid.get((r + dr, c))
                            if v is None: continue
                            if isinstance(v, str) and (parse_label(v) or header_token(v)): break
                            tgt = (r + dr, c); break
                    if tgt: targets.append((tgt[0], tgt[1], ser0, scn0, None))
                if li["kind"] in ("asof", "effective"):
                    for (tr, tc, s, sc, _) in targets:
                        info = self.cell(title, tr, tc)
                        (asof if li["kind"] == "asof" else eff).append(info | {"label": grid[(r, c)], "label_cell": lab_cell, "decoy": li["decoy"],
                                                                              "series": s, "scenario": sc})
                    continue
                if li.get("ambiguous"):
                    ambiguous.append({"label": grid[(r, c)], "label_cell": lab_cell, "fields": li["ambiguous"]}); continue
                for (tr, tc, s, sc, hu) in targets:
                    info = self.cell(title, tr, tc)
                    uc = []
                    if li.get("unit"): uc.append(("label", li["unit"][0], li["unit"][1]))
                    if sheet_unit: uc.append(("sheet_unit_mark", sheet_unit[0], sheet_unit[1]))
                    cands.append(Cand(li["field"], title, info["cell"], lab_cell, grid[(r, c)], li, s, sc, uc, info))
        return {"cands": cands, "asof": asof, "effective": eff, "ambiguous_labels": ambiguous}


# ── 값 정규화 ─────────────────────────────────────────────────────────────────────────────────────
def normalize(c: Cand) -> dict:
    """후보 → {value(정규화 문자열)|None, unit_source, notes, review(reason)|None, alt(대체 해석)}."""
    f, info = c.field, c.raw
    v, fmt = info.get("value"), info.get("number_format") or "General"
    out = {"value": None, "unit_source": None, "notes": [], "review": None}
    if info.get("resolved_by") == "external_ref": out["review"] = "external_reference"; out["cached_value"] = _jsonable(v); return out
    if info.get("resolved_by") in ("formula_unevaluated", "missing_sheet"): out["review"] = info["resolved_by"]; return out
    if v is None or (isinstance(v, str) and not v.strip()): out["review"] = "empty_value"; return out
    if f.endswith("_date"):
        d, why = norm_date(v, fmt)
        if d is None: out["review"] = why
        else: out["value"] = d; why and out["notes"].append(why)
        return out
    x, tunit, why = _num(v)
    if x is None: out["review"] = why; return out
    scale_fmt, fmt_lit, pct_fmt = fmt_unit(fmt)
    if f.endswith("_pct"):
        lab_pct = any(u[1] == "%" for u in c.unit_ctx if u[0] == "label")
        if tunit and tunit[0] == "bp": val, src = x / 100, "cell_text_bp"
        elif tunit and tunit[0] == "%": val, src = x, "cell_text_%"
        elif pct_fmt: val, src = x * 100, "number_format_%"
        elif lab_pct: val, src = x, "label_(%)"
        elif x == 0 or x > 1: val, src = x, "plain_number_as_percent"
        elif x > PCT_MAX / 100: val, src = x, "plain_number_as_percent(fraction_implausible)"
        else:
            out["review"] = "pct_unit_ambiguous"; out["alt"] = [str(round(x, 6)), str(round(x * 100, 6))]; return out
        out["unit_source"] = src; val = round(val, 6)
        if val < 0 or val > PCT_MAX: out["review"] = "pct_implausible"; out["implausible_value"] = str(val); return out
        out["value"] = str(float(val)); return out
    # 금액(원·원/주)
    srcs = []
    if tunit and tunit[0] not in ("%", "bp"): srcs.append(("cell_text", tunit[0], tunit[1]))
    if fmt_lit and "(scale_mismatch)" in fmt_lit: out["review"] = "number_format_scale_mismatch"; return out
    if fmt_lit and scale_fmt != 1.0: srcs.append(("number_format", fmt_lit, scale_fmt))
    elif fmt_lit: srcs.append(("number_format(display_only)", fmt_lit, 1.0))
    for u in c.unit_ctx:
        if u[0] == "sheet_unit_mark" and f != "principal_krw": continue   # 표 단위 표기는 총액에만(주당 가격엔 적용 안 함)
        srcs.append(u)
    explicit = [s for s in srcs if s[0] in ("cell_text", "number_format")]
    if len({s[2] for s in explicit}) > 1: out["review"] = "unit_conflict"; out["unit_candidates"] = srcs; return out
    scale, src = (srcs[0][2], f"{srcs[0][0]}:{srcs[0][1]}") if srcs else (1.0, "assumed_won")
    if srcs and srcs[0][0] == "number_format(display_only)": scale, src = 1.0, f"number_format(display_only):{srcs[0][1]}"
    val = x * scale
    if abs(val - round(val)) > 1e-6 * max(1.0, abs(val)) and abs(val - round(val)) > 0.5: out["notes"].append("non_integer_krw")
    out["unit_source"] = src
    if f == "principal_krw" and not (1e7 <= val <= 1e13):
        out["review"] = "principal_magnitude_implausible(unit_missing?)"; out["implausible_value"] = str(int(round(val))); return out
    if f != "principal_krw" and not (1 <= val <= 1e7):
        out["review"] = "price_magnitude_implausible"; out["implausible_value"] = str(int(round(val))); return out
    out["value"] = str(int(round(val))); return out


def _jsonable(v):
    if isinstance(v, (_dt.date, _dt.datetime)): return v.isoformat()
    return v


def _eq(f, a, b) -> bool:
    if a is None or b is None: return False
    if f.endswith("_pct"):
        try: return abs(float(a) - float(b)) <= 1e-6
        except ValueError: return False
    return str(a) == str(b)


def _cand_ref(c: Cand) -> dict:
    return {"sheet": c.sheet, "cell": c.cell, "label_cell": c.label_cell, "label": c.label, "label_match": c.label_info.get("how"),
            "series": c.series, "scenario": c.scenario, "formula": c.raw.get("formula"), "raw_value": _jsonable(c.raw.get("value")),
            "resolved_by": c.raw.get("resolved_by"), "number_format": c.raw.get("number_format")}


# ── 감사 ─────────────────────────────────────────────────────────────────────────────────────────
def audit_workbook_v3(path: str | Path, chain: dict, *, as_of: str | None = None, target_series: str | None = None,
                      doc_hashes: dict | None = None) -> dict:
    """chain = dart_change 체인(+ 선택 'series'). target_series 가 없으면 chain['series'] 를 쓴다.
    doc_hashes: rcept_no → 공시 원문 텍스트 sha256 (근거 연결용; 없으면 None 으로 남긴다)."""
    path = Path(path); doc_hashes = doc_hashes or {}
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    wb = WorkbookV3(path); sc = wb.scan()
    tser = str(target_series) if target_series else (str(chain.get("series")) if chain.get("series") else None)
    asof_src = "argument" if as_of else None
    if as_of is None:
        vals = []
        for a in [a for a in sc["asof"] if not (tser and a.get("series") and a["series"] != tser)]:
            if a.get("decoy") or a["resolved_by"] in ("external_ref", "formula_unevaluated"): continue
            d, _ = norm_date(a["value"], a.get("number_format") or "")
            if d: vals.append((d, a["label_cell"]))
        if len({d for d, _ in vals}) == 1: as_of = vals[0][0].replace("-", ""); asof_src = vals[0][1]
        elif len({d for d, _ in vals}) > 1: asof_src = "conflict:" + ",".join(f"{l}={d}" for d, l in vals)
    effective = []
    effs = sc["effective"]
    if tser and any(e.get("series") == tser for e in effs): effs = [e for e in effs if e.get("series") in (tser, None)]
    for e in effs:
        d, _ = norm_date(e["value"], e.get("number_format") or "")
        effective.append({"label": e["label"], "label_cell": e["label_cell"], "cell": f"{e['sheet']}!{e['cell']}", "date": d, "series": e.get("series")})
    cur = dart_change.version_at(chain, as_of) if as_of else None
    findings, scenario_notes = [], []
    by_field = {}
    for c in sc["cands"]: by_field.setdefault(c.field, []).append(c)
    for f in FIELDS:
        exp = cur["values"].get(f) if cur else None
        fd = {"field": f, "expected": exp, "expected_rcept": cur["rcept_no"] if cur else None}
        cs = by_field.get(f, [])
        decoys = [c for c in cs if c.label_info.get("decoy")]
        cs = [c for c in cs if not c.label_info.get("decoy")]
        fd["decoys_ignored"] = [_cand_ref(c) for c in decoys]
        # 회차
        tagged = sorted({c.series for c in cs if c.series})
        if tagged:
            if tser:
                hit = [c for c in cs if c.series == tser]
                if hit: cs = hit
                else:
                    untagged = [c for c in cs if not c.series]
                    if untagged: cs = untagged
                    else:
                        findings.append(fd | {"verdict": "series_mismatch", "reason": f"Excel 회차 {tagged} 에 대상 회차 {tser} 없음",
                                              "candidates": [_cand_ref(c) for c in cs]}); continue
            elif len(tagged) > 1:
                findings.append(fd | {"verdict": "REVIEW_REQUIRED", "reason": f"series_unresolved: Excel 회차 {tagged}, 대상 회차 미상",
                                      "candidates": [_cand_ref(c) for c in cs]}); continue
        # 시나리오
        scn = {c.scenario for c in cs if c.scenario}
        if scn:
            base = [c for c in cs if c.scenario == "base"]
            others = [c for c in cs if c.scenario and c.scenario != "base"]
            if not base:
                findings.append(fd | {"verdict": "REVIEW_REQUIRED", "reason": f"scenario_base_unresolved: {sorted(scn)}",
                                      "candidates": [_cand_ref(c) for c in cs]}); continue
            for o in others:
                n = normalize(o)
                scenario_notes.append({"field": f, "scenario": o.scenario, "value": n["value"], "cell": f"{o.sheet}!{o.cell}",
                                       "equals_disclosure": _eq(f, n["value"], exp)})
            cs = base + [c for c in cs if not c.scenario]
        if not cs:
            if decoys:
                findings.append(fd | {"verdict": "REVIEW_REQUIRED", "reason": "only_qualified_labels(최초·변경 전 등) — 현재 입력값 셀을 찾지 못함"}); continue
            if exp is not None:
                findings.append(fd | {"verdict": "missing_in_model", "reason": "refixing_clause_missing" if f == "refixing_floor_krw" else "field_not_found"})
            else:
                findings.append(fd | {"verdict": "not_in_disclosure_or_model"})
            continue
        norms = [(c, normalize(c)) for c in cs]
        rev = [(c, n) for c, n in norms if n["review"]]
        ok = [(c, n) for c, n in norms if not n["review"]]
        if rev and not ok:
            c, n = rev[0]
            v = "unit_error" if n["review"] == "pct_implausible" else "REVIEW_REQUIRED"
            findings.append(fd | {"verdict": v, "reason": n["review"], "excel": _cand_ref(c), "normalization": n,
                                  "candidates": [_cand_ref(x) for x, _ in rev]}); continue
        vals = {n["value"] for _, n in ok}
        if len(vals) > 1:
            findings.append(fd | {"verdict": "REVIEW_REQUIRED", "reason": "conflicting_inputs",
                                  "candidates": [_cand_ref(c) | {"value": n["value"]} for c, n in ok]}); continue
        c, n = ok[0]; val = n["value"]
        fd |= {"model_value": val, "excel": _cand_ref(c), "unit_source": n["unit_source"], "notes": n["notes"],
               "also_found": [f"{x.sheet}!{x.cell}" for x, _ in ok[1:]]}
        if rev: fd["notes"] = n["notes"] + [f"ignored_unresolved:{x.sheet}!{x.cell}:{m['review']}" for x, m in rev]
        if cur is None: findings.append(fd | {"verdict": "REVIEW_REQUIRED", "reason": "as_of_missing" if not as_of else "no_version_filed_by_as_of"}); continue
        if exp is None: findings.append(fd | {"verdict": "not_in_disclosure"}); continue
        if _eq(f, val, exp): findings.append(fd | {"verdict": "match"}); continue
        scaled = n["unit_source"] and not n["unit_source"].startswith(("assumed_won", "number_format(display_only)")) and f == "principal_krw" \
            and any(u in n["unit_source"] for u in ("천원", "만원", "백만원", "억", "십억원", "조원", "bn", "mn", "billion", "million"))
        if scaled and exp and abs(int(val) - int(exp)) <= max(1, int(exp)) * 1e-5:
            findings.append(fd | {"verdict": "REVIEW_REQUIRED", "reason": "rounding_within_unit_precision(환산 후 반올림 차이 — 일치로 확정하지 않음)"}); continue
        older = [v for v in chain["versions"] if v["rcept_dt"] <= as_of and v is not cur and _eq(f, val, v["values"].get(f))]
        newer = [v for v in chain["versions"] if v["rcept_dt"] > as_of and _eq(f, val, v["values"].get(f))]
        verdict = "stale_version" if older else "future_version" if newer else "mismatch"
        if verdict != "mismatch": fd["matched_version_rcept"] = (older or newer)[-1]["rcept_no"]
        findings.append(fd | {"verdict": verdict})
    if sc["ambiguous_labels"]:
        for a in sc["ambiguous_labels"]:
            findings.append({"field": None, "verdict": "REVIEW_REQUIRED", "reason": f"ambiguous_label:{a['fields']}", "excel": {"label_cell": a["label_cell"], "label": a["label"]}})
    wb_doc = f"xlsx:{path.name}"
    for fd in findings:
        ev = []
        if fd.get("excel"): ev.append({"doc_id": wb_doc, "sha256": wb.sha256, "locator": f"{fd['excel'].get('sheet', '')}!{fd['excel'].get('cell', '')} (label {fd['excel'].get('label_cell')})".replace("!!", "!")})
        for cnd in fd.get("candidates", [])[:6]:
            ev.append({"doc_id": wb_doc, "sha256": wb.sha256, "locator": f"{cnd['sheet']}!{cnd['cell']}"})
        r = fd.get("expected_rcept")
        if r: ev.append({"doc_id": f"DART:{r}", "sha256": doc_hashes.get(r), "locator": f"dart_table.LABELS[{fd['field']}]" if fd.get("field") else "form"})
        if fd.get("matched_version_rcept"):
            m = fd["matched_version_rcept"]; ev.append({"doc_id": f"DART:{m}", "sha256": doc_hashes.get(m), "locator": f"dart_table.LABELS[{fd['field']}]"})
        fd["evidence"] = ev
        fd["review_required"] = fd["verdict"] not in ("match", "not_in_disclosure", "not_in_disclosure_or_model")
        fd["approval"] = {"status": "PENDING", "history": []}
    after = hashlib.sha256(path.read_bytes()).hexdigest()
    if after != before: raise RuntimeError("WORKBOOK_MODIFIED_DURING_AUDIT")
    nr = [x for x in findings if x["review_required"]]
    return {"tool": "excel.audit_v3", "version": VERSION, "workbook": str(path), "workbook_sha256_before": before, "workbook_sha256_after": after,
            "sheets": wb.f.sheetnames, "chain_origin": chain.get("origin"), "target_series": tser, "as_of": as_of, "as_of_source": asof_src,
            "effective_dates": effective, "effective_date_note": "효력발생일은 평가기준일로 쓰지 않는다(버전 선택은 평가기준일 기준 접수일).",
            "current_version": cur["rcept_no"] if cur else None, "findings": findings, "scenario_notes": scenario_notes,
            "needs_review": nr, "review_required": bool(nr),
            "dart_field_labels": {f: dart_table.LABELS[f] for f in FIELDS}}


def read_map(result: dict) -> dict:
    """field → 'Sheet!A1' (감사기가 입력값으로 읽은 셀). 평가 스크립트용."""
    return {x["field"]: f"{x['excel']['sheet']}!{x['excel']['cell']}" for x in result["findings"]
            if x.get("field") and x.get("excel") and "sheet" in x["excel"] and x.get("model_value") is not None}
