"""DART '전환사채권발행결정' 공시 표 서식에서 조건 값을 뽑는다 (Track D v2, 결정론).

공시 원문(document.xml → 텍스트, 셀 경계 ' | ')은 표준 서식이라 값이 '라벨 셀' 바로 다음 셀에 온다.
규칙은 dev(2025 접수) 공시만 보고 만들었다. test(2026) 는 개발에 쓰지 않는다.
반환값은 원문 그대로의 문자열(정규화는 호출자 몫). 값이 '-'·빈칸이면 None.
"""
from __future__ import annotations

import re

# 필드 → 라벨 셀 정규식(셀 텍스트 전체에 매치). 첫 매치만 쓴다(같은 라벨이 뒤 절에서 반복된다).
LABELS = {
    "principal_krw": r"(\d+\.\s*)?사채의\s*권면\s*(\(전자등록\))?\s*총액\s*\(원\)",
    "coupon_rate_pct": r"표면이자율\s*\(%\)",
    "redemption_yield_pct": r"만기이자율\s*\(%\)",
    "maturity_date": r"(\d+\.\s*)?사채만기일",
    "conversion_price_krw": r"전환가액\s*\(원/주\)",
    "refixing_floor_krw": r"최저\s*조정가액\s*\(원\)",
    "issue_date": r"(\d+\.\s*)?납입일",
}
_EMPTY = {"", "-", "--", "해당사항없음", "해당사항 없음", "N/A", "없음"}


def _cells(text: str) -> list[str]:
    out = []
    for line in text.splitlines():
        for c in line.split(" | "):
            c = c.strip().rstrip("|").strip()
            if c: out.append(c)
    return out


def extract(text: str) -> dict:
    cells = _cells(text)
    res: dict = {}
    for field, pat in LABELS.items():
        rx = re.compile(rf"^{pat}$")
        for i, c in enumerate(cells):
            if rx.match(c):
                v = cells[i + 1] if i + 1 < len(cells) else ""
                res[field] = None if v in _EMPTY or rx.match(v) else v
                break
    return res
