---
title: ValuationProof Demo
emoji: 📑
colorFrom: indigo
colorTo: gray
sdk: static
app_file: index.html
pinned: false
license: apache-2.0
short_description: As-of CB terms vs valuation Excel — synthetic demo
---

# ValuationProof Demo (synthetic, runs in your browser)

Static demo of [ThakiCloud/thaki-valuationproof](https://github.com/ThakiCloud/thaki-valuationproof) (V4.2, tag v4.2.0).
The package code in `valuationproof/` runs **in your browser** via Pyodide; nothing is sent to a server. Pre-generated reports are in `reports/`.

- **All data is synthetic**: a fictional issuer (`SYNTH001`) with a made-up disclosure chain (1 original + 3 corrections) and 4 synthetic workbooks.
  No real company, filing or accountant workbook. No DART key, no LLM.
- **V4.2 synthetic adjustment demo**: two made-up conversion-price adjustment filings (8,500 → 7,800, corrected to 7,750; effective 2025-11-20, published 2025-11-21 / 2025-12-05)
  are collected by the real collector from an in-memory fake OpenDART source. The page shows the same as-of date under `restated` and `as-known`, and two broken collections
  that come out as `EVENT_COVERAGE_UNVERIFIED` (needs review, never auto-judged).
- A field whose value depends on an effective date the disclosures cannot confirm is reported as **`확인 필요(효력일 미확정)` / needs review** with candidate values, and gets no automatic verdict.
- Findings are **candidates for human review** (`approval_status = PENDING`). No auto-fix, no fair-value calculation.

**Limits (V4.2 — an improved disclosure-based audit engine, not a commercially validated product):**
- Values are **disclosure-based estimates**: contract effective dates are not confirmed (`contract_effective_confirmed = false`).
- It does **not** claim to fully audit every term of real DART filings, and it does not compute fair value.
- "0 silent errors" holds **only within the evaluated public-disclosure sample (60 issuers, 102 chains) and the two interpretations (IA/IB)**.
  It is not 100% accuracy, and the reference values come from the same collected filings (not independent ground truth).
- The cost: **35.3%** of quarter-end (chain × quarter-end) pairs (200/567) need human review.
- Correction tables of adjustment filings ([기재정정] before/after tables) are not parsed yet (check C6).
- **Error-detection performance on real customer valuation workbooks is not validated.**

Third-party runtime loaded by the page: Pyodide (MPL-2.0) from cdn.jsdelivr.net, and openpyxl (MIT) from PyPI.
See the GitHub README for the "Validated / Not validated" scope.
