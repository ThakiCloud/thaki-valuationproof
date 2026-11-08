---
title: ValuationProof Demo
emoji: 📑
colorFrom: indigo
colorTo: gray
sdk: gradio
sdk_version: 6.25.0
app_file: app.py
pinned: false
license: apache-2.0
short_description: As-of CB terms vs valuation Excel — synthetic demo
---

# ValuationProof Demo (synthetic)

Offline demo of [ThakiCloud/thaki-valuationproof](https://github.com/ThakiCloud/thaki-valuationproof).

- **All data in this Space is synthetic**: a fictional issuer (`SYNTH001`) with a made-up disclosure chain
  (1 original + 3 corrections) and 4 synthetic valuation workbooks. No real company, filing or accountant workbook.
- No DART API key, no network calls, no LLM. Deterministic rules only.
- When a value depends on an effective date that the disclosures cannot confirm (e.g. as-of between a board resolution and its filing, or a later
  restatement that may be retroactive), the field is reported as **`확인 필요(효력일 미확정)` / needs review** with candidate values — no automatic verdict.
- Findings are **candidates for human review** (`approval_status = PENDING`). No auto-fix, no fair-value calculation.
- Live DART mode exists only in the CLI and needs **your own** OpenDART key (`API_K_DART`); it is not enabled here.

**Limits (V4.2 — an improved disclosure-based audit engine, not a commercially validated product):**
- Values are **disclosure-based estimates**: contract effective dates are not confirmed (`contract_effective_confirmed = false`).
- It does **not** claim to fully audit every term of real DART filings, and it does not compute fair value.
- "0 silent errors" holds **only within the evaluated public-disclosure sample (60 issuers, 102 chains) and the two interpretations (IA/IB)**.
  It is not 100% accuracy, and the reference values come from the same collected filings (not independent ground truth).
- The cost: **35.3%** of quarter-end (chain × quarter-end) pairs (200/567) need human review.
- Correction tables of adjustment filings ([기재정정] before/after tables) are not parsed yet (check C6).
- **Error-detection performance on real customer valuation workbooks is not validated.**

See the GitHub README for the "Validated / Not validated" scope.
