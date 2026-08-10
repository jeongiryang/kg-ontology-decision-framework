# 0015. ValidatedClaims 승인 경계와 Claim 전체 검증 보완

## 기본 정보

| 항목 | 내용 |
|---|---|
| 작성일 | 2026-08-11 |
| 담당자 | 정이량 |
| 사용 에이전트 | Codex |
| 작업 브랜치 | `feat/jeongiryang/evidence-answer-renderer` |
| 관련 커밋 | 이번 작업 커밋 |
| 관련 Issue/PR | Draft PR #27 |
| 작업 상태 | 완료 |

## 1. 작업 목적

PR #27 독립 재검토에서 확인된 raw Claim 렌더링 BLOCKER와 Claim metadata,
QueryPlan 사실값 출처, `SAFE_FAILURE` 본문 관련 MAJOR 문제를 수정한다. 기존
구조화 Claim 설계는 유지하면서 검증 전·후 타입과 Citation 소스를 하나의 승인
경계로 묶는다.

## 2. 요청 내용 요약

- `GroundedClaim → ClaimValidator → ValidatedClaims` 승인 경계
- raw Claim·일반 collection·단순 dataclass 복사 렌더링 차단
- `FIELD_VALUE`의 subject·unit·operator·polarity 전체 재검증
- 과목 목록 `completion_type`을 QueryPlan이 아닌 검증 결과 행에서 생성
- 오류 코드별 고정 `SAFE_FAILURE` 문구
- 승인 객체·provenance 혼합·기존 의미 반전 회귀 테스트
- 실제 Neo4j·Ollama 6문항과 데이터 불변성 검증

## 3. 작업 전 상태

- 브랜치와 원격 PR #27 Head는
  `427a976b3077234b9868f4e114f56799f89d7b52`로 일치했고 작업 트리는 clean이었다.
- PR #27에는 리뷰·일반 댓글·unresolved thread가 없었고 GitHub 단위 테스트는
  성공 상태였다.
- raw Claim에 `MAJOR_REQUIRED`를 넣어 renderer와 정상 CitationRenderer를 직접
  호출하면 “자료구조의 이수구분은 전공필수”가 `ANSWERABLE`로 반환됐다.
- 변조 ClaimBuilder가 subject를 “운영체제”로 바꾸어도 정상 Evidence와 함께
  `ANSWERABLE`로 반환됐다.
- `completion_type` 목록 Claim 값이 QueryPlan 필터에서 복사됐다.
- `SAFE_FAILURE`에 검증 실패 사실 문장을 임의로 넣을 수 있었다.
- 수정 전 Neo4j 기준은 노드 1,518개, 관계 3,260개, Evidence 511개였다.

## 4. 수행한 작업

1. `ClaimValidator`만 발급할 수 있는 immutable `ValidatedClaims`를 추가했다.
2. 승인 내용은 canonical Claim, 직접 provenance와 Citation 소스 전체의 keyed
   digest에 결합했다.
3. 일반 생성자, list·tuple raw Claim, `dataclasses.replace`와 다른 검증 실행의
   Citation 소스 혼합을 거부했다.
4. validator가 전달 Claim을 그대로 반환하지 않고 조회 행과 QueryPlan으로
   canonical Claim을 재구성한 뒤 전체 dataclass 필드를 비교하도록 변경했다.
5. `FIELD_VALUE`의 ID, subject identity·표시명, field/value, unit, operator,
   polarity와 description 기본값을 검증했다.
6. 과목 목록 `completion_type`은 결과 행의 단일 값으로 생성하고 QueryPlan은
   범위 일치 검사에만 사용하도록 변경했다.
7. renderer는 `ValidatedClaims`만 받고 승인된 렌더링 결과를 발급하도록 변경했다.
8. CitationRenderer는 별도 행 목록을 받지 않고 같은 승인 객체에 묶인 Citation
   소스만 사용하도록 변경했다.
9. `ChatResponse.safe_failure()`와 오류 코드별 중앙 안전 문구를 추가하고 임의
   본문을 거부했다.
10. 패키지 루트 공개 API에서 `GroundedClaim`을 제거했다.
11. `CurriculumChatService` 생성자에서 내부 답변 pipeline 교체 지점을 제거했다.

## 5. 변경된 파일

| 경로 | 변경 유형 | 내용 |
|---|---|---|
| `src/kg_builder/answer/__init__.py` | 수정 | 내부 Claim 타입의 공개 export 제거 |
| `src/kg_builder/answer/contracts.py` | 수정 | 고정 안전 실패 문구와 factory·불변조건 |
| `src/kg_builder/answer/claim_builder.py` | 수정 | 결과 행 기반 completion_type Claim |
| `src/kg_builder/answer/claim_validator.py` | 수정 | ValidatedClaims 승인과 canonical 전체 검증 |
| `src/kg_builder/answer/korean_renderer.py` | 수정 | 승인 Claim 전용 렌더링·결과 결합 |
| `src/kg_builder/answer/renderer.py` | 수정 | 승인 Citation 소스 전용 조립 |
| `src/kg_builder/answer/service.py` | 수정 | 공식 승인 흐름과 safe failure factory |
| `tests/test_answer_renderer.py` | 수정 | 승인·metadata·출처·오류 본문 회귀 |
| `docs/evidence-answer-renderer.md` | 수정 | 승인 계약과 Python 신뢰 경계 |
| `README.md` | 수정 | ValidatedClaims 실행 계약 요약 |
| `docs/ai-simulation-logs/jeong-iryang/README.md` | 수정 | 다음 로그 번호와 목록 |

## 6. 주요 결정과 이유

- **canonical 재구성**: caller-owned Claim을 승인하지 않고 같은 승인 행에서 다시
  만들어 subject와 의미 없는 metadata까지 한 번에 검증한다.
- **승인과 Citation 결합**: CitationRenderer에 mutable rows를 다시 전달하지 않아
  다른 실행의 정상 Evidence를 위조 Claim에 붙이는 실수를 차단한다.
- **QueryPlan과 사실값 분리**: QueryPlan은 범위, Neo4j 결과는 답변의 사실값이다.
- **keyed digest**: frozen dataclass만으로는 `replace` 시 seal 복사가 가능하므로 승인
  내용 전체에 프로세스-local keyed digest를 결합한다.
- **고정 안전 문구**: 검증 실패 본문을 `SAFE_FAILURE`로 감싸 재사용하지 못하게
  오류 코드와 사용자 문구를 중앙에서 연결한다.

## 7. 검증

| 검증 항목 | 명령 또는 방법 | 결과 |
|---|---|---|
| 의존성 | `uv sync --locked` | PASS |
| lock | `uv lock --check` | PASS |
| unittest | `uv run python -m unittest discover -s tests -v` | 107개 중 102 PASS, 환경 통합 5 skip |
| pytest | `uv run pytest -q` | 102 PASS, 5 skip, 99 subtests PASS |
| Python compile | `uv run python -m compileall -q src` | PASS |
| schema stale | `uv run python -m kg_builder.query.schema_exporter check` | PASS |
| Neo4j 통합 | `KG_NEO4J_INTEGRATION=1 uv run pytest -q` | 105 PASS, 2 skip, 105 subtests PASS |
| 실제 답변 6문항 | `KG_LOCAL_LLM_INTEGRATION=1 ... test_answer_integration.py -s` | 1 test / 6 subtests PASS |
| 공격 재현 | 독립 합성 Claim 실행 | 네 우회 모두 차단 |
| Citation 재현성 | 실제 전공필수 9행을 50회 shuffle | 출력 signature 1개 |
| whitespace | `git diff --check` | PASS |

실제 6문항은 교양 최소 34학점, 균형교양 4개 영역·영역별 1과목·12학점,
편입생 교양 의무 없음, 자료구조 2학년 1학기, 전공필수 9과목·21학점,
자료구조 전공선택을 반환했다. 총 실행시간은 99.51초였고 최종 답변 LLM 호출은
0회였다. 모델 호출은 planner와 Cypher 생성·재확인에만 사용됐다.

## 8. 발견된 문제와 위험

- Python module-private seal과 digest는 같은 프로세스에서 이미 임의 코드 실행권을
  가진 공격자를 격리하는 보안 샌드박스가 아니다.
- private sentinel·digest 함수 탈취, monkey patching, `object.__setattr__`, 메모리
  변조는 범위 밖이며 배포 프로세스의 코드 신뢰 경계로 보호해야 한다.
- 현재 renderer가 지원하지 않는 새 Claim 조합은 계속 안전 실패한다.
- 실제 연구실 vLLM은 이번 수정 범위에서 실행하지 않았다.

## 9. 남은 작업

- 최신 Head를 대상으로 별도 독립 재검토를 수행한다.
- BLOCKER·MAJOR가 없으면 사용자가 Draft 해제와 병합 여부를 결정한다.
- 병합 후 PR #14 프론트엔드와 `ChatResponse`를 연결한다.

## 10. 다음 작업 제안

독립 재검토에서 raw Claim·복사 Claim·다른 실행 Citation 혼합, FIELD_VALUE 전체
metadata, QueryPlan/결과 사실값 분리와 오류 코드별 안전 문구를 다시 변조해 확인한다.
