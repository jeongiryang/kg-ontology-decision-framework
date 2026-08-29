# 0029. DSW 모델 벤치마크 사전 조사와 로컬 기준선

## 기본 정보

| 항목 | 내용 |
|---|---|
| 작성일 | 2026-08-29 |
| 담당자 | 정이량 |
| 사용 에이전트 | Codex |
| 작업 브랜치 | `feat/jeongiryang/agentic-multiturn-graphrag` |
| 관련 Issue/PR | PR #34 |
| 작업 상태 | 부분 완료 — DSW SSH 설정 부재로 원격 실측 미실행 |

## 1. 작업 목적

로컬 Ollama `qwen2.5-coder:14b`와 DSW 연구실 A6000 서버에서 실행 가능한 후보를 같은
GraphRAG 평가로 비교하고, 의미 정확도·tool calling·Citation·지연이 실제로 개선되는 경우에만
provider 또는 모델 기본값 변경을 판단한다.

## 2. 작업 전 상태

- `StructuredLLMClient` 뒤에 Ollama와 OpenAI-compatible adapter가 이미 존재했다.
- 두 provider 모두 환경변수로 선택하며 base URL은 loopback HTTP만 허용했다.
- `.env.example`과 로컬 LLM 문서가 SSH tunnel을 통한 연구실 vLLM 연결을 이미 설명했다.
- PR #34의 실제 평가 산출물에는 PR #10 50문항, 미공개 단일 50문항, 다중 20개/65턴의
  상태·Citation·응답시간이 있었다.

## 3. 수행한 작업

- 비밀값을 출력하지 않고 표준 사용자 SSH 설정에서 사용할 수 있는 alias 존재 여부를
  확인했다.
- SSH 설정이 없어 서버 주소나 계정을 추측하지 않고 DSW 접속을 중단했다.
- 로컬 `.env`는 값 전체를 출력하지 않고 provider·model 설정 여부, loopback 여부와 API key
  존재 여부만 확인했다.
- Ollama 프로세스, loopback endpoint와 `qwen2.5-coder:14b` 설치를 확인했다.
- 마지막 실제 SSE 결과를 합쳐 평균·중앙값·P95와 Citation coverage를 재계산했다.
- 공식 모델 카드에서 한 장 GPU 우선 후보와 제외 후보를 조사했다.
- DSW 사전 점검, 한 장 GPU 선택, 임시 server, 동일 평가, 정리 순서를 문서화했다.

## 4. 변경된 파일

| 경로 | 변경 유형 | 내용 |
|---|---|---|
| `docs/evaluations/model-provider-benchmark.md` | 추가 | 로컬 기준선, DSW 후보, 비교·정리 절차와 미실행 범위 |
| `docs/local-llm-query-pipeline.md` | 수정 | 최신 model benchmark 문서 연결과 현재 선정 상태 |
| `docs/ai-simulation-logs/jeong-iryang/0029-dsw-model-benchmark-preflight.md` | 추가 | 이번 사전 조사 기록 |
| `docs/ai-simulation-logs/jeong-iryang/README.md` | 수정 | 로그 인덱스와 다음 번호 |

## 5. 주요 결정과 이유

- DSW 주소·계정을 추측하지 않았다. 사용자 지시가 접속 정보 미구성 시 중단을 요구한다.
- provider 코드를 변경하지 않았다. Ollama/OpenAI-compatible 전환과 loopback SSH tunnel
  보안 경계가 이미 요구사항을 충족한다.
- 기본 모델을 변경하지 않았다. 후보 실측값이 한 건도 없어 개선을 입증할 수 없다.
- 후보는 공식 model card와 single-GPU 우선 조건으로만 선정했고 실행 결과처럼 기록하지
  않았다.
- 기존 115개 agentic turn의 trace 존재를 JSON schema 성공률로 오인하지 않았다. 현재
  산출물로 측정할 수 없는 지표는 미측정으로 남겼다.

## 6. 검증

| 검증 항목 | 방법 | 결과 |
|---|---|---|
| 로컬 provider 설정 | 값 비노출 상태 검사 | Ollama·14B·loopback 설정 확인 |
| 로컬 Ollama | loopback tags API | endpoint와 baseline 모델 확인 |
| PR #10 50문항 통계 | 마지막 실제 JSON 결과 재집계 | 평균 6.865s, P95 10.651s, Citation 22/22 |
| 미공개 단일 50문항 | 마지막 실제 JSON 결과 재집계 | 평균 9.338s, P95 15.941s, Citation 29/29 |
| 미공개 다중 65턴 | 마지막 실제 JSON 결과 재집계 | 평균 9.566s, P95 22.998s, Citation 43/43 |
| DSW SSH·GPU | 표준 SSH alias 확인 후 정책 적용 | BLOCKED, 원격 명령 미실행 |
| provider adapter 회귀 | `uv run --no-sync pytest -q tests/test_llm_providers.py` | 10 PASS, 19 subtests PASS |
| lockfile | `uv lock --check` | PASS |
| Markdown 상대 링크 | 변경 문서의 로컬 링크 검사 | PASS, 누락 0 |
| diff 형식·보호 범위 | `git diff --check`, 변경 경로 검사 | PASS |

## 7. 실패한 접근과 원인

- 표준 사용자 SSH config가 없어 DSW host alias를 확인할 수 없었다.
- 실제 서버 주소·계정·키를 추측하거나 다른 위치에서 탈취하지 않았다.
- 따라서 요구된 DSW preflight, 모델 실행, VRAM과 정확도 비교는 수행하지 않았다.

## 8. 남은 문제

- 정상적으로 부여된 SSH alias가 구성돼야 DSW 사전 점검을 시작할 수 있다.
- 후보별 generation 단위 JSON schema·tool calling 계수와 최대 VRAM을 실제로 측정해야 한다.
- 상주 모델 API는 연구실 협의가 확인되기 전까지 완료 범위가 아니다.

## 9. 다음 작업 제안

접속 설정이 제공되면 문서의 순서대로 빈 GPU 한 장에서 후보를 하나씩 임시 실행한다.
동일 prompt·평가셋으로 결과를 비교하고, 모든 선정 조건을 충족할 때만 별도 검토를 거쳐
기본 모델 변경을 제안한다.
