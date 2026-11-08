# Release audit — thaki-valuationproof

점검일 2026-11-09 · 원본 커밋 `97fb95c919cd60fc1f0d914961dce9590fe2422b` (ThakiCloud 비공개 저장소, sha 만 기록) · 결과: **PASS**

이 파일은 공개 전에 돌린 점검의 기록입니다. 점검 스크립트는 내부 경로를 참조하므로 레포에 넣지 않았고, 결과만 옮깁니다.

## 1. 테스트

`pytest -q` → `82 passed in 1.05s` (exit 0). 전문은 [TEST_RESULTS.txt](TEST_RESULTS.txt).

## 2. 공개 표면 게이트 (release guard)

- 프로필 `personal-portfolio` (회사명은 경고, 실명·계정 핸들·내부 인프라·시크릿·커밋 신원 검사) · git 히스토리(내용·경로·커밋 메시지)·author/committer 포함.
- 차단(BLOCK) 0건. 경고는 회사명 `ThakiCloud`·조직 저장소 이름(공식 조직 공개라 의도된 표기)뿐입니다.
  - WARN · company-name · `SOURCE.json` · ThakiCloud
  - WARN · company-name · `pyproject.toml` · ThakiCloud
  - WARN · org-repo · `pyproject.toml` · ThakiCloud/thaki-valuationproof
  - WARN · company-name · `README.md` · ThakiCloud
  - WARN · org-repo · `README.md` · ThakiCloud/ValuationProof-Demo
  - WARN · company-name · `RELEASE_AUDIT.md` · ThakiCloud
  - WARN · org-repo · `RELEASE_AUDIT.md` · ThakiCloud/thaki-valuationproof
  - WARN · company-name · `space_static/index.html` · ThakiCloud
  - WARN · org-repo · `space_static/index.html` · ThakiCloud/thaki-valuationproof
  - WARN · company-name · `space_static/README.md` · ThakiCloud
  - WARN · org-repo · `space_static/README.md` · ThakiCloud/thaki-valuationproof
  - WARN · company-name · `docs/space_dependency_licenses.md` · ThakiCloud
  - WARN · org-repo · `docs/space_dependency_licenses.md` · ThakiCloud/thaki-valuationproof
  - WARN · company-name · `space/README.md` · ThakiCloud
  - WARN · org-repo · `space/README.md` · ThakiCloud/thaki-valuationproof
  - WARN · company-name · `space/app.py` · ThakiCloud
  - WARN · org-repo · `space/app.py` · ThakiCloud/thaki-valuationproof
  - WARN · company-name · `src/valuationproof.egg-info/PKG-INFO` · ThakiCloud
  - WARN · org-repo · `src/valuationproof.egg-info/PKG-INFO` · ThakiCloud/thaki-valuationproof
  - WARN · history:company-name · `git history` · 내용 75건

## 3. 시크릿·내부 정보 스캔 (트리 + xlsx 내부 XML + git 히스토리(시크릿 값))

| 항목 | 건수 |
|---|---|
| `internal_paths` | 0 |
| `s3_buckets` | 0 |
| `cluster` | 0 |
| `project_uuid` | 0 |
| `emails` | 0 |
| `personal_names` | 0 |
| `secret_patterns` | 0 |
| `private_repo_names` | 0 |
| `aihub` | 0 |
| `real_rcept_14digit` | 53 |
| `dart_corp_code_8digit` | 0 |
| `exact_value_API_K_DART` | not_in_env |
| `exact_value_DART_API_KEY` | 0 |
| `exact_value_HF_TOKEN` | 0 |

- `exact_value_*`: 로컬에 있는 OpenDART 키·HF 토큰 **값 자체**를 트리·xlsx·히스토리에서 찾은 결과(값은 출력하지 않음). 0 이어야 통과.
- `real_rcept_14digit`: 14자리 접수번호 형태 숫자. 해당 위치는 **단위 테스트의 임의 식별자**(`2025010100000N` 류, 합성 원문과 짝)뿐이며 실제 공시 내용과 무관합니다.
  공개 데모·예시 보고서의 접수번호는 `YYYYMMDD99NNNN` 형태의 가상 번호이고 `synthetic://` 로만 표시됩니다.
  ./tests/test_public_synthetic.py:37:    assert asof_terms.DART_VIEW.format("20250101000001").startswith("https://dart.fss.or.kr/")   # unregistered -> normal li
  ./tests/test_v41_effective_date.py:19:    vs = [("20250101000001", "20250101", "original", vals(), bform(o_bd) if board else form()),
  ./tests/test_v41_effective_date.py:20:          ("20250301000002", "20250301", "correction", vals(cp="900"), bform(c_bd, cp="900") if board else form(cp="900"))
  ./tests/test_v41_effective_date.py:37:    assert r["selected_version"]["rcept_no"] == "20250301000002"
  ./tests/test_v41_effective_date.py:42:    assert old["selected_version"]["rcept_no"] == "20250101000001" and old["terms"]["conversion_price_krw"]["value"] == "1

- 코드 주석에 내부 개발 기록의 식별자(트랙 이름·원장 ID·이전 버전 커밋 약칭)가 남아 있습니다. 경로·호스트·시크릿·개인 정보는 아니며, 원 코드와의 대응을 보존하려고 그대로 둡니다.

## 4. 데이터

- **실제 공시 원문·파생값 없음.** 비공개 저장소의 실제 공시 사슬 고정본(값·위치·해시)과 그것으로 만든 예시 보고서는 제외했습니다.
- 포함한 데이터: 가상 발행사 `SYNTH001` 의 가상 공시 4건(`examples/synthetic/synthetic_disclosures/`, 코드 `valuationproof/synthetic.py` 로 재생성), 합성 Excel 4종, 그 보고서(JSON·MD·HTML), 합성 사슬 고정본.
- README 의 정확도 수치는 내부에서 공개 공시로 잰 **집계값**만 옮겼습니다(원장 수치 그대로, 새로 만든 수치 없음).
- AI 허브 데이터: 사용·포함 없음(`aihub` 스캔 0). 비공개 저장소의 AI 허브 어댑터·학습 코드는 공개본에서 제외했습니다.

## 5. 외부 API 의존

- **OpenDART** (`https://opendart.fss.or.kr/api/`) — 선택. 실제 데이터 모드(`--fetch`, 또는 V4.2 `--adjustments live` 로 실제 발행사 사슬을 열 때)에서만, 사용자 본인 키(`API_K_DART` / `DART_API_KEY` 환경변수)로 `list.json`·`document.xml` 을 부릅니다. 키는 출력·저장하지 않습니다.
- 기본 모드는 오프라인 합성입니다. 합성 발행사 `SYNTH001` 은 `--adjustments live` 여도 네트워크를 쓰지 않고 메모리 안의 가짜 목록·원문 소스(`synthetic.SyntheticDart`)로 수집기를 돌립니다. 테스트가 이를 소켓 차단 상태에서 확인합니다.
- `--event-cache` 로 지정한 수집 캐시는 사용자 로컬에만 쓰입니다. 레포에는 실제 DART 응답 캐시가 없습니다.
- HF Space(정적 페이지): 방문자 브라우저가 Pyodide 런타임(cdn.jsdelivr.net, MPL-2.0)과 openpyxl·et_xmlfile(PyPI, MIT)을 받습니다. 사용자 데이터 전송 없음.
- 그 밖의 외부 API·LLM·상용 서비스: 없음. 테스트는 소켓 연결을 막은 상태에서 오프라인 데모를 확인합니다.

## 6. 의존성 라이선스

런타임 (패키지):

| Name       | Version | License     | URL                                           |
|------------|---------|-------------|-----------------------------------------------|
| et_xmlfile | 2.0.0   | MIT License | https://foss.heptapod.net/openpyxl/et_xmlfile |
| openpyxl   | 3.1.5   | MIT License | https://openpyxl.readthedocs.io               |

HF Space 실행 환경(gradio 포함, 레포에 번들하지 않음) 전체 63개 패키지 — GPL/AGPL/SSPL/Unknown 플래그 0건. 전체 표는 [docs/space_dependency_licenses.md](docs/space_dependency_licenses.md).
프로젝트 라이선스 Apache-2.0 과 충돌하는 의존성 없음.

## 7. 공개에서 제외한 것

| 구성요소 | 이유 |
|---|---|
| 실제 공시 사슬 고정본·그 예시 보고서 | 실제 공시 파생 데이터 — 재배포하지 않음 |
| AI 허브 어댑터(`prepare-71834` 등)·학습/추론 명령 | AI 허브 데이터 경로·이번 공개 범위 밖 |
| 내부 플랫폼 연동 모듈(도구 계약·라우터 연결) | 내부 시스템 전용 |
| 평가 스크립트·결과 원본(공개 공시 대량 수집·채점) | 실제 공시 파생물을 다룸. 집계 수치만 README 에 |
| 비공개 저장소의 git 히스토리 | 새 히스토리로 시작 |
| V4.2 실제 공시 회귀 픽스처(`tests/fixtures_private/`)·그 테스트(`test_v42_regression_private.py`) | 실제 DART 공시 파생 — 재배포하지 않음. 공개본은 같은 규칙을 합성 사건으로 검사 |
| OpenDART 응답 캐시(`vp42_cache` 등) | 실제 공시 원문 — 재배포하지 않음 |
| Gradio 서버형 HF Space | HF 가 조직 계정의 Gradio Space 생성을 거부(402, 유료 플랜 필요). 유료 결제는 하지 않고 정적 Space(브라우저 Pyodide)로 대체. Gradio 앱은 `space/` 에 소스로만 둠 |

## 8. 주장 경계

검증된 것(소프트웨어 검증 결과): 공개 DART 필드 기준 기준일 조건 선택(약한 정답, 정답도 같은 수집 공시에서 만든 것이라 독립 대조가 아님), V4.2 조정 공시 수집·두 시간축·수집 검사의 합성 사건 동작, 합성 워크북 대조. 값은 공시 기반 추정치입니다.
검증되지 않은 것: 계약상 효력일, 실제 회계사 평가 Excel, 계약서, 비상장 RCPS, 공정가치, 고객 실증, DART 전 조건 감사, 조정 [기재정정] 정정표 해석(C6).
