"""RCPS(상환전환우선주)·공정가치 — **입력 스키마와 검증 체크리스트만**. 판정·가치평가는 하지 않는다.

상태는 항상 CUSTOMER_EVIDENCE_REQUIRED(고객 실증 필요). 실 계약서·실 평가모형으로 검증한 적이 없고,
합성 데이터로 '성공' 처리하지 않는다. check() 는 입력이 스키마의 필수 항목을 갖췄는지와, 사람이 확인할 항목 목록만 낸다.
"""
from __future__ import annotations

import json
from pathlib import Path

VERSION = "0.1.0-schema-only"
STATUS = "CUSTOMER_EVIDENCE_REQUIRED"
SCHEMA = Path(__file__).resolve().parent / "schemas" / "rcps_terms_input.schema.json"

CHECKLIST = [
    # 조기상환(상환권)
    {"id": "R1", "area": "조기상환", "item": "상환권 행사 주체(투자자 풋/회사 콜)와 최초·최종 행사일이 계약서 조항과 같은가"},
    {"id": "R2", "area": "조기상환", "item": "상환금액 산식(발행가+연복리 IRR / 단리 / 고정액)과 IRR, 기지급 배당 차감 여부"},
    {"id": "R3", "area": "조기상환", "item": "배당가능이익 한도 내 상환 제약을 모형이 반영했는가(상환 불능 시 처리)"},
    {"id": "R4", "area": "조기상환", "item": "평가기준일 이후 개정·주주간계약 변경이 반영되지 않았는가(기준일 규칙)"},
    # 우선배당
    {"id": "D1", "area": "우선배당", "item": "우선배당률과 기준(발행가/액면), 누적·참가 여부"},
    {"id": "D2", "area": "우선배당", "item": "미지급 누적배당이 상환금액·전환가치에 반영되는 방식"},
    # 전환비율 조정
    {"id": "C1", "area": "전환비율 조정", "item": "최초 전환비율·전환청구기간·만기 자동전환 여부"},
    {"id": "C2", "area": "전환비율 조정", "item": "시가 리픽싱 하한(최초 전환가의 %)과 조정 주기"},
    {"id": "C3", "area": "전환비율 조정", "item": "IPO 공모가 리픽싱 조건(공모가 대비 %)과 실적 리픽싱 조건"},
    {"id": "C4", "area": "전환비율 조정", "item": "희석 방지 조항(full ratchet / weighted average)과 후속 라운드 반영"},
    # 공정가치
    {"id": "F1", "area": "공정가치", "item": "평가방법(이항/몬테카를로/TF 모형)과 변동성·무위험이자율·신용스프레드 출처"},
    {"id": "F2", "area": "공정가치", "item": "주계약·파생상품 분리 여부와 회계 정책(K-IFRS 1109/1032) 일관성"},
]
_REQ = {"issue": ["issue_date", "shares", "issue_price_krw"], "redemption": ["holder_put", "first_exercise_date", "redemption_amount_basis"],
        "preferred_dividend": ["rate_pct", "cumulative", "participating"],
        "conversion": ["initial_ratio", "conversion_start_date", "conversion_end_date", "refixing"]}


def schema() -> dict:
    return json.loads(SCHEMA.read_text(encoding="utf-8"))


def check(terms: dict) -> dict:
    """구조 점검 + 체크리스트. 값의 옳고 그름이나 공정가치는 판정하지 않는다."""
    missing = [k for k in ("instrument_id", "as_of", "sources") if k not in terms]
    for sec, keys in _REQ.items():
        if sec not in terms: missing.append(sec); continue
        missing += [f"{sec}.{k}" for k in keys if k not in terms[sec]]
    srcd = {s.get("field") for s in terms.get("sources", []) if s.get("doc_sha256") and s.get("locator")}
    return {"tool": "valuationproof.rcps.check", "version": VERSION, "status": STATUS, "verdict": None,
            "missing_required": missing, "fields_without_source": sorted(set(_REQ) - {f.split(".")[0] for f in srcd}),
            "checklist": [dict(c, result="PENDING_HUMAN") for c in CHECKLIST],
            "approval_status": "PENDING", "side_effects": False,
            "not_validated": ["비상장 RCPS 계약 추출", "공정가치 검증", "실 회계사 평가모형"],
            "note": "고객 실증 필요 — 실 계약서·평가모형으로 검증 전에는 정확도·성공을 주장하지 않는다."}
