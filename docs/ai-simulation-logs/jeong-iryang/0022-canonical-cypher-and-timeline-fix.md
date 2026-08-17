# 0022. PR #28 canonical Cypher와 타임라인 보완

## 기본 정보

| 항목 | 내용 |
|---|---|
| 작성일 | 2026-08-18 |
| 담당자 | 정이량 |
| 사용 에이전트 | Codex |
| 작업 브랜치 | `fix/jeongiryang/evidence-chat-manual-qa` |
| 관련 커밋 | 본 작업 커밋 |
| 관련 Issue/PR | PR #28 |
| 작업 상태 | 완료 |

## 1. 작업 목적

검사용 문자열에서만 Cypher 주석을 제거하고 원본 문자열을 승인 객체와 inspection에 보관하던 문제를 수정한다. 실제 callback 전 미래 단계를 화면에 미리 만들던 동작과 취소 시 가짜 `0ms`가 남는 타임라인 문제도 함께 보완한다.

## 2. 요청 내용 요약

- line/block comment를 제거한 canonical Cypher만 정적 검증 이후 단계에 전달
- 문자열과 backtick 내부의 주석 유사 문자는 주석으로 오인하지 않음
- 닫히지 않은 token, 주석 제거 후 빈 쿼리와 토큰 결합 우회 차단
- 합성 비밀 marker가 승인 객체·실행·inspection·trace에 남지 않는지 검증
- 실제 callback이 도착한 타임라인 행만 생성
- 취소 시 실제 브라우저 시작 시각으로 경과시간을 계산하거나 시간을 생략

## 3. 작업 전 상태

- PR #28 Head는 `df54ee15f0b3a0d6007dfd49663154f71f4a85b7`이었다.
- tracked/staged 변경은 없었다.
- 기존 untracked `Zone.Identifier` 파일은 작업 범위 밖으로 유지했다.
- 합성 line/block comment에 넣은 네 marker가 모두 `ValidatedCypher.text`에 남는 것을 재현했다.
- 브라우저는 요청 시작 시 10개 미래 단계를 `WAITING`, `elapsed_ms=0`으로 선생성했다.

## 4. 수행한 작업

- lexer 결과에 실제 주석만 공백·줄바꿈으로 치환한 `canonical` 문자열을 추가했다.
- 문자열과 backtick 구간을 별도 상태로 인식해 내부 `//`, `/* */`, `https://`를 보존했다.
- 현재 제한 문법의 backtick 식별자 거부 정책은 유지했다.
- `ValidatedCypher.text`와 `PipelineOutcome.validated_cypher`가 canonical 문자열만 보유하도록 변경했다.
- `NaturalLanguageResult`도 원본 LLM 후보 대신 pipeline 승인 canonical 문자열만 반환한다.
- executor의 방어 검사는 comment가 남은 비canonical 승인 위조 객체를 거부한다.
- inspection collector는 canonical과 일치하지 않는 문자열을 후보 상태에 보관하지 않는다.
- 프론트 타임라인의 미래 단계 배열을 제거하고 실제 SSE callback 도착 시에만 행을 만든다.
- 취소된 현재 단계는 `performance.now()` 기반 실제 경과시간을 사용하며, 시작 시각이 없으면 시간을 표시하지 않는다.

## 5. 변경된 파일

| 경로 | 변경 유형 | 내용 |
|---|---|---|
| `src/kg_builder/query/cypher_validator.py` | 수정 | comment-free canonicalization과 token 상태 검증 |
| `src/kg_builder/query/safety_pipeline.py` | 수정 | 승인 canonical Cypher를 outcome에 포함 |
| `src/kg_builder/query/natural_language_service.py` | 수정 | 원본 후보 대신 승인 canonical Cypher 반환 |
| `src/evidence_chat/server.py` | 수정 | inspection의 canonical 승인 방어 |
| `src/evidence_chat/static/app.js` | 수정 | callback 기반 행 생성과 취소시간 계산 |
| `src/evidence_chat/static/app.css` | 수정 | 사용하지 않는 WAITING 스타일 제거 |
| `tests/test_dynamic_query_safety.py` | 수정 | canonical·marker·executor·trace 회귀 테스트 |
| `tests/test_evidence_chat.py` | 수정 | inspection·미래 단계·취소시간 계약 테스트 |
| `docs/text-to-cypher-safety.md` | 수정 | canonicalization과 backtick 정책 문서화 |
| `docs/evidence-chat.md` | 수정 | 실제 callback 및 취소시간 정책 문서화 |
| `docs/ai-simulation-logs/jeong-iryang/README.md` | 수정 | 로그 색인 갱신 |

## 6. 주요 결정과 이유

- 동적 Cypher 주석을 원본 그대로 승인하지 않고 lexer가 comment-free canonical 문자열을 발급한다. 문자열 리터럴을 손상하지 않으면서 모든 후속 계층에 단일 승인 문자열을 전달하기 위해서다.
- 주석 문자를 삭제해 토큰을 붙이지 않고 같은 길이의 공백 또는 줄바꿈으로 치환한다.
- backtick 구간은 안전한 lexing을 위해 보존해 인식하지만, 기존 deny-by-default 문법과 우회 방지 정책에 따라 최종 validator에서는 거부한다.
- inspection은 pipeline 계약만 신뢰하지 않고 canonical 여부를 다시 확인해 후속 adapter 실수에 의한 노출을 막는다.
- 취소 시간은 서버 완료시간으로 위장하지 않고 브라우저가 실제 STARTED event를 받은 시각부터 계산한다.

## 7. 검증

| 검증 항목 | 명령 또는 방법 | 결과 |
|---|---|---|
| 기존 노출 재현 | 합성 marker를 line/block comment에 넣고 validator 실행 | 수정 전 marker 4종 모두 승인 문자열에 존재 |
| 수정 후 marker 확인 | 동일 합성 입력으로 validator 실행 | marker 0건, comment-free 확인 |
| 지정 최소 테스트 | `uv run --no-sync pytest -q tests/test_dynamic_query_safety.py tests/test_evidence_chat.py` | 51 passed, 39 subtests passed |
| diff whitespace | `git diff --check` | 최종 커밋 전 확인 |
| GitHub Actions | PR #28 checks | push 후 확인 예정 |

첫 최소 테스트 시도 중 Starlette route 구간이 일시적으로 대기해 프로세스를 중단했다. 두 파일을 각각 실행하면 통과했고, 이후 사용자 지정 통합 명령을 그대로 재실행해 0.40초에 최종 통과했다.

## 8. 발견된 문제와 위험

- Python private seal 탈취나 임의 코드 실행은 기존 문서화된 프로세스 내부 신뢰 경계 밖이다.
- backtick 식별자는 canonical lexer에서 안전하게 구분하지만 현재 동적 문법에서는 계속 지원하지 않는다.
- 브라우저 취소는 UI 대기만 중단하며 이미 실행 중인 Ollama 작업을 즉시 종료하지 못할 수 있다.

## 9. 남은 작업

- push된 최신 Head의 GitHub Actions 결과를 확인한다.
- 현재 작성자와 다른 계정이 최신 Head에서 canonical Cypher와 타임라인을 독립 재검토해야 한다.
- 전체 unittest, 전체 pytest, Neo4j 통합, Ollama 실제 질문과 전체 PDF 검사는 이번 범위에서 실행하지 않았다.

## 10. 다음 작업 제안

PR #28 최신 Head를 독립 검토해 승인 가능 상태인지 판정한 뒤, 사용자가 병합 여부를 결정한다. PR #29는 PR #28 병합 이후 최신 `main`을 반영해 wire/API 충돌을 해결한다.
