# Thaki-ValuationProof

**전환사채(CB) 공시·정정공시 사슬에서 평가 기준일에 유효한 조건을 고르고, 평가 Excel 의 입력 셀과 대조해 근거 보고서를 만드는 결정론 도구입니다.**
*Deterministic as-of convertible-bond terms from Korean DART disclosure/correction chains, checked against valuation workbooks, with an evidence report for human review.*

**[Demo (Hugging Face Space, synthetic)](https://huggingface.co/spaces/ThakiCloud/ValuationProof-Demo)** · **[Example reports](examples/synthetic/)** · **[Release audit](RELEASE_AUDIT.md)** · **[License](LICENSE)**

> **이 공개본의 범위.** 코드와 **완전 합성 데이터**만 들어 있습니다. 실제 공시 원문·실제 공시에서 파생한 값·실제 회계사 평가모형은 들어 있지 않습니다.
> 결과는 **회계사가 검토할 후보**이며(승인 상태 `PENDING`), 자동 수정이나 공정가치 계산은 하지 않습니다.

## 무엇을 하나

| 단계 | 모듈 | 하는 일 |
|---|---|---|
| ① 공시 사슬 추적 | `dart_change` · `change_v3` · `dart_source` | 원공시(전환사채권발행결정)와 기재정정 공시를 한 발행 사슬로 묶고, 버전마다 조건 값을 서식에서 뽑습니다 |
| ② 기준일 조건 선택 | `asof_terms` | 버전마다 **효력일**(공시에 적힌 이사회결의일·조정가액 적용일, 없으면 접수일 + `date_key_fallback = true`)을 키로 기준일 T 에 유효한 버전을 고릅니다. T 뒤 버전은 제외 목록에 남기고, 값마다 근거(접수번호·원문 줄·문자 구간·sha256·날짜 키)를 답니다 |
| ③ Excel 대조 | `excel_v3` · `audit_v4` | 평가 Excel(읽기 전용)에서 전환가액·리픽싱 하한·만기·표면/만기이자율·납입일·전환청구기간·풋/콜 셀을 찾아 기준일 값과 비교합니다 |
| ④ 근거 보고서 | `audit_v4.write_reports` | JSON·Markdown·HTML. 오류 후보마다 **셀 주소 + Excel 값 + 공시 값 + 근거 공시 + 사람 승인 필드** |
| (참고) RCPS | `rcps` | 비상장 RCPS 입력 스키마와 검토 체크리스트만. 상태는 항상 `CUSTOMER_EVIDENCE_REQUIRED`, 판정 없음 |

분류: `일치` · `불일치`(값 다름 / 정정 미반영 / 기준일 뒤 정정 선반영) · `누락` · `미확인`(사람 검토) · `해당없음` · **`확인 필요(효력일 미확정)`**.

### 안전 규칙 (코드와 테스트로 고정)

- **날짜 키 = 공시에 적힌 효력일.** 이사회결의일(결정일)·조정가액 적용일이 공시에 있으면 그 날짜, 없으면 접수일을 쓰고 `date_key_fallback = true` 로 표시합니다.
- **효력일이 확정되지 않아 값이 갈리면 자동 판정하지 않습니다.** 기준일이 결의일과 접수일 사이이거나, 기준일 뒤에 접수된 *기재 정정*(소급인지 장래인지 공시로 알 수 없음)이 값을 바꾸면
  그 필드는 `effective_status = UNCONFIRMED_DEPENDS`, 분류 **`확인 필요(효력일 미확정)` / needs review** 가 됩니다. 후보 값(`alternative_values`)을 함께 내고,
  규칙이 냈을 일치/불일치는 `blocked_auto_class` 로만 남기며 `approval_required = true`, `auto_verdict_allowed = false` 입니다.
- 계약상 효력일은 계약서로 확인하지 않았으므로 모든 값에 `contract_effective_confirmed = false` 를 답니다. 공시 ≠ 서명 계약.
- 기준일이 원 결의일보다 앞서면 조건을 내지 않습니다(`NOT_YET_FILED`). 추측하지 않습니다.
- 규칙 신호로만 이어 붙인 정정(사람 미검수)은 `link_review_required = true`.
- 사전에 없는 라벨·전치 표에서 회차를 못 읽은 경우는 `누락` 으로 확정하지 않고 `미확인` 으로 보냅니다(오탐 확정 방지).
- Excel 은 수정하지 않습니다(감사 전후 sha256 동일 확인). 수정 제안은 `requires_approval = true`, `auto_applied = false`.
- 풋·콜은 "있다/없다 + 최초 날짜 후보"만 냅니다. 행사가·비율 등 세부는 해석하지 않고 항상 사람 확인입니다.

## 설치

```bash
git clone https://github.com/ThakiCloud/thaki-valuationproof && cd thaki-valuationproof
python -m venv .venv && . .venv/bin/activate
pip install -e ".[test]"        # 런타임 의존성은 openpyxl 하나
pytest                           # 결과: TEST_RESULTS.txt
```

## 빠른 시작 — 오프라인 합성 데모 (키·네트워크 불필요)

```bash
valuationproof demo --out vp_demo
```

가상 발행사 `SYNTH001` 의 **가상 공시 사슬**(원공시 1 + 정정 3)과 **합성 평가 Excel 4종**으로 기준일 2025-10-01 대조를 돌립니다.
미리 만든 결과는 [`examples/synthetic/`](examples/synthetic/) 에 있습니다.

| 워크북 | 시나리오 | 결과 (일치/불일치/누락/미확인) | 기대 |
|---|---|---|---|
| `SYNTH_normal.xlsx` | 기준일 유효 값 그대로 | 10 / 0 / 0 / 1 | 오류 0 ✔ |
| `SYNTH_stale_correction.xlsx` | 기준일 직전 정정(만기 연장) 미반영 | 7 / 3 / 0 / 1 | 만기·만기이자율·전환청구 종료일 = `정정 미반영` ✔ |
| `SYNTH_refix_maturity_typo.xlsx` | 리픽싱 하한·만기 오기재 | 8 / 2 / 0 / 1 | `불일치` 2 ✔ |
| `SYNTH_transposed_synonym.xlsx` | 2회차 전치 표 + 사전 밖 라벨 | 5 / 0 / 0 / 6 | 오류 확정 0, `미확인` 으로만 ✔ |

기준일 2025-10-01 에서는 효력일에 값이 달리는 필드가 없어 `확인 필요` 는 0 입니다. `미확인` 1건(콜옵션)은 모형에 셀이 없을 때 "누락"으로 확정하지 않는 규칙 때문입니다. 2026-01-15 정정(리픽싱 하한 변경)은 기준일 뒤 접수라 제외되고 보고서의 `기준일 뒤 변경` 열에만 나옵니다.
합성 접수번호는 `synthetic://<번호>` 로 표시해 실제 DART 문서로 연결되지 않게 합니다.

**효력일 미확정 예시.** 합성 사슬의 정정 C1(전환가액 10,000 → 8,500)은 이사회결의일 2025-06-16, 접수일 2025-06-20 입니다.

```bash
valuationproof resolve --synthetic --asof 2025-06-18     # 전환가액·만기 등 4개 필드 = 확인 필요(효력일 미확정), 후보 값 [10000, 8500] …
```

미리 만든 결과: [`examples/synthetic/effective_date_resolve_2025-06-18.json`](examples/synthetic/effective_date_resolve_2025-06-18.json),
같은 기준일로 `SYNTH_stale_correction.xlsx` 를 대조한 보고서 [`examples/synthetic/effective_date_2025-06-18/`](examples/synthetic/effective_date_2025-06-18/).

다른 사용법:

```bash
valuationproof resolve --synthetic --asof 2025-10-01                       # 기준일 조건 + 근거 (JSON)
valuationproof audit --synthetic --asof 2025-10-01 --excel my_model.xlsx --out out/
valuationproof audit --chain-file vp_demo/synthetic_chain_fixture.json --asof 2025-10-01 --excel my_model.xlsx
valuationproof rcps-check --terms rcps_terms.json                          # 스키마·체크리스트만
```

## 실제 공시 모드 (선택, 본인 API 키 필요)

실제 공시는 이 레포에 들어 있지 않습니다. 공시는 [DART 전자공시시스템](https://dart.fss.or.kr) 에서 보고,
조회는 [OpenDART](https://opendart.fss.or.kr) 에서 **본인이 발급받은 인증키**로 합니다.

```bash
export API_K_DART=<your OpenDART key>            # 키는 환경변수에서만 읽고 출력·저장하지 않습니다
valuationproof audit --data ./dart_data --fetch --corp <8자리 corp_code> --cb <원공시 또는 정정 접수번호> \
                     --asof 2025-12-31 --excel my_model.xlsx --out out/
```

- `--fetch` 는 발행사 1곳의 CB 발행결정(정정 포함) 목록과 원문만 소량 조회해 `--data` 디렉터리에 저장합니다(호출 수를 출력).
- 받은 공시 원문·파생물은 **본인 환경에만** 두세요. 이 프로젝트는 공시 원문이나 그 파생 데이터를 재배포하지 않으며, 사용자에게도 권하지 않습니다.
- 외부 API 의존성은 **OpenDART(선택)** 하나뿐입니다. LLM·상용 API 는 쓰지 않습니다.
- **한계: 전환가액의조정 공시는 자동 수집하지 않습니다.** 리픽싱 등 `전환가액의조정` 공시는 `asof_terms.parse_adjustment` / `adjustments_for` 로 파싱해
  `chain["adjustments"]` 에 넣어야 반영됩니다. CLI 의 `--fetch` 는 CB 발행결정(정정 포함)만 받으므로, 조정 공시를 넣지 않으면 리픽싱 뒤 낡은 전환가액이 나올 수 있습니다.

## 검증된 것 / 검증되지 않은 것 (Validated / Not validated)

### 검증된 것 — 공개 DART 필드 + 합성 워크북

내부 개발 단계에서 OpenDART 공개 공시(CB 발행결정·기재정정)로 측정한 값입니다. 측정에 쓴 공시 원문·파생 데이터는 이 레포에 없고 집계 수치만 옮깁니다.

| 항목 | dev (사슬 549, 2026년 전 원공시) | test (사슬 412, 2026년 원공시, 1회 보고) |
|---|---|---|
| 기준일 조건 선택 정확도 (약한 정답 기준, 필드 micro) | **1.0** (4,050 필드) | **0.9997** (3,394 필드) |
| 정정 무시(원공시 값만) 기준선 | 0.819 | 0.8751 |
| 정정으로 값이 바뀐 필드만 | 1.0 vs 기준선 0.0 (733) | 1.0 vs 기준선 0.0 (423) |
| 시점 규칙 위반(기준일 뒤 값 유입) | 0 / 816 검사 | 0 / 390 검사 |

위 표는 V4.0(접수일 키) 규칙으로 잰 값입니다(내부 원장 `D_valuationproof_v4_1108`). 효력일 키(V4.1) 관련 수치는 아래 별도 표이며 내부 원장 `D_effective_date_1109` 에서만 옮겼습니다.

- **약한 정답(weak gold)** 입니다: 정답은 같은 공시 출처의 최신 구조화 레코드이고, 중간 버전 정답은 없으며, 독립 전문가 검증이 아닙니다. test 의 오차 1건(납입일)은 정책상 고치지 않았습니다.
- 합성 워크북: 4종 데모 기대 결과 전부 충족, 오류 확정 오탐 0. 사전 밖 동의어 합성 어휘에서 이전 버전(V3) 오경보 0.98 → V4 오류 확정 0.0(대신 `미확인` 0.364).
  전치 표 회차 읽기 정확도 합성 사후 점검 0.2026 → 1.0.

#### 효력일(V4.1) — 공개 DART 필드 검증, 계약서 검증 아님

표본: 공개 공시 60개사(대부분 2025~2026 접수). 시장 전체 비율로 일반화하지 않습니다. 효력일 = 발행사가 공시에 적은 이사회결의일·조정가액 적용일입니다.

| 측정 | 값 |
|---|---|
| 원공시 결의일 ≠ 접수일 | 19 / 92 (0.2065) |
| 전환가액 조정 적용일 ≠ 접수일 | 31 / 114 (0.2719) |
| 효력일이 접수일보다 *뒤*인 사건 | 0 — 전부 "효력이 먼저, 공시가 나중" |
| 분기말 기준 선택이 바뀌는 비율(날짜 키만): 정정 장래 해석 IA / 소급 해석 IB | 0.0035 / 0.2363 |
| 매일×필드 508,528쌍에서 표시 없이 틀린 값: V4.0 → V4.1 (IB 대비) | 108,835 → **0** (달라지는 값은 전부 `확인 필요` 로 표시) |
| 그 대가: 분기말 (사슬×분기말) 쌍 중 한 필드 이상이 사람 확인으로 넘어가는 비율 | 138 / 567 (0.2434) |
| 리픽싱 회차에서 조정 공시를 안 넣었을 때 분기말 전환가액이 낡은 비율 (V4.0) | 84 / 136 (0.6176) — `chain["adjustments"]` 로만 해소 |

- IA·IB 중 어느 해석이 맞는지는 공시로 알 수 없고, 이 도구도 주장하지 않습니다. IA 대비 0 은 정의상 자명합니다(V4.1 이 IA 키를 씀).

### 검증되지 않은 것

- **실제 회계사 평가 Excel** — 실 평가모형으로 정확도를 잰 적이 없습니다. 합성 워크북 결과를 실무 정확도로 읽지 마세요.
- **계약서 원문** — 공시 ≠ 서명 계약. 계약상 효력일·기재 정정의 소급 여부는 확인하지 않습니다(`contract_effective_confirmed = false`).
- **조정 공시 자동 수집** — CLI 는 `전환가액의조정` 공시를 받지 않습니다(위 한계).
- 알려진 표시 불일치: `확인 필요(효력일 미확정)` 행의 `공시 값(기준일)` 열은 접수일 기준 값으로 찍힐 수 있습니다(예시 보고서의 전환가액 10000). 그 행은 자동 판정이 막혀 있고 후보 값 두 개가 `세부` 열에 함께 나오므로, 판단은 후보 값으로 하세요.
- 알려진 경계 사례: 정정 서식의 이사회결의일이 *그 사이에 있었던 새 결의*보다 이른 날짜(원 결의일)로 적혀 있으면, 효력일 정렬상 그 정정이 앞선 버전으로 취급될 수 있습니다. 이런 사슬은 결과를 사람이 확인하세요.
- **비상장 RCPS** — 스키마·체크리스트만 있고 추출·판정은 없습니다.
- **공정가치** — 공정가치를 계산하거나 검증하지 않습니다.
- 고객 실증(customer validation)을 거친 적이 없습니다.

## 회계법인 공동 검증 제안

실제 평가모형(익명화 가능)으로 이 도구를 함께 검증할 회계법인·평가 실무자를 찾습니다. 검증은 귀사 환경에서 오프라인으로 돌릴 수 있고, 데이터는 밖으로 나오지 않습니다.
연락은 [ThakiCloud 웹사이트](https://thakicloud.com) 의 문의 경로로 주세요.

## 재현 정보

- 테스트 결과: [`TEST_RESULTS.txt`](TEST_RESULTS.txt)
- 추출 원본: ThakiCloud 내부(비공개) 저장소 커밋 — [`SOURCE.json`](SOURCE.json) 의 `source_commit_sha`. 공개용으로 바꾼 부분(합성 접수번호 링크 처리, 스키마 경로, 내부 모듈 의존 제거, 합성 데모 라벨)도 거기 적었습니다.
- 공개 점검 기록: [`RELEASE_AUDIT.md`](RELEASE_AUDIT.md)

## 라이선스

Apache-2.0 ([LICENSE](LICENSE)). 런타임 의존성 openpyxl 은 MIT 입니다. DART 공시의 저작권·이용 조건은 공시 제출인과 금융감독원 정책을 따르며, 이 라이선스가 공시에 적용되지 않습니다.
