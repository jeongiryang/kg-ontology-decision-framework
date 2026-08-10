# 0003. 온톨로지 V1 스키마 검토

## 기본 정보

| 항목 | 내용 |
|---|---|
| 작성일 | 2026-08-10 |
| 담당자 | 정이량 |
| 사용 에이전트 | Codex |
| 작업 브랜치 | `feat/jeongiryang/2026-kg-poc` |
| 관련 커밋 | 없음 (커밋 전 작성) |
| 관련 Issue/PR | Draft PR #8 |
| 작업 상태 | 완료 |

## 1. 작업 목적

온톨로지 V1 명세의 파일 역할과 핵심 데이터 모델 결정을 명확히 하고, 스키마에 섞여 있던 competency questions를 별도 평가 fixture로 분리한다. 이후 실제 데이터 JSON, Neo4j 적재와 질의 평가가 같은 계약을 사용하도록 명세·설명·검증 기준을 정리한다.

## 2. 요청 내용 요약

- 로컬 `AGENTS.md`에 PM 역할, 공통 파일 보호와 AI 작업 로그 자동 작성 규칙을 보완한다.
- `ontology_spec.json`을 기계 판독형 스키마 명세로, `ontology-v1.md`를 사람용 설명으로 구분한다.
- 공통 교양과 컴퓨터공학과 교육과정 버전을 분리한다.
- `CourseOffering` ID 충돌 가능성과 Rule 하위 유형별 필수 속성을 검토한다.
- Document–Evidence 역관계 중복을 제거하고 VERIFIED Evidence 정책을 강화한다.
- CQ-001~CQ-010을 평가 fixture로 분리하고 구조화한다.
- 관련 파일만 커밋·push한 뒤 기존 Draft PR #8을 갱신한다.

## 3. 작업 전 상태와 기존 명세 역할

- 작업 트리와 staging 영역은 깨끗했고 기존 Draft PR #8이 현재 브랜치를 head로 사용하고 있었다.
- `ontology/ontology_spec.json`은 노드, 관계, 속성, vocabulary, ID, 불변조건과 확장 규칙을 담았지만 competency questions도 함께 포함하고 있었다.
- `docs/ontology/ontology-v1.md`는 상세 설명 문서였으나 기계 판독 명세와의 기준 우선순위가 명시적으로 드러나지 않았다.
- `Document-[:HAS_EVIDENCE]->Evidence`와 `Evidence-[:FROM_DOCUMENT]->Document`가 같은 연결을 양방향으로 중복 표현했다.
- 모든 `Rule`에 `operator`가 필수여서 서술형 경과조치 표현에 맞지 않을 수 있었다.

## 4. 수행한 작업

- 로컬 `AGENTS.md`에 정이량 PM·통합 책임자 기준, 공통 파일 보호, 자동 AI 로그 작성 규칙을 추가했다.
- `AGENTS.md`를 `.git/info/exclude`에도 등록하고 비추적·비커밋 상태를 유지했다.
- `ontology_spec.json`에 산출물 역할과 혼동하면 안 되는 파일 유형을 명시했다.
- 공통 교양을 `curriculum:cwnu:2026:common`, 컴퓨터공학과 전공을 `curriculum:cwnu:2026:cse`로 분리했다.
- `CourseOffering` ID에 학년, 학기, 이수구분을 추가했다.
- `Rule.operator`를 공통 선택 속성으로 바꾸고 학점·과목 규칙에서만 필수로 유지했다.
- `HAS_EVIDENCE`를 제거하고 `FROM_DOCUMENT`만 유지했다.
- VERIFIED 규칙·편성과 사용자 답변에 VERIFIED Evidence를 요구하도록 불변조건을 강화했다.
- competency questions를 `tests/fixtures/2026/competency_questions.json`으로 이동했다.
- answerability를 `FULL`, `PARTIAL`, `NOT_SUPPORTED` enum으로 통일했다.
- gold result, 예상 그래프 패턴과 Evidence 페이지를 구조화했다.
- 설명 문서를 14개 핵심 절로 재구성했다.

## 5. 변경된 파일

| 경로 | 변경 유형 | 내용 |
|---|---|---|
| `AGENTS.md` | 로컬 수정 | PM 역할과 자동 로그 규칙. Git 제외 및 커밋 금지 |
| `ontology/ontology_spec.json` | 수정 | 스키마 역할, curriculum 분리, ID·Rule·Evidence 정책, 평가 fixture 참조 |
| `docs/ontology/ontology-v1.md` | 수정 | 사람용 설명 역할과 핵심 설계 중심으로 재구성 |
| `tests/fixtures/2026/competency_questions.json` | 생성 | CQ-001~CQ-010 평가 계약 |
| `docs/ai-simulation-logs/jeong-iryang/0003-ontology-v1-schema-review.md` | 생성 | 본 작업 로그 |
| `docs/ai-simulation-logs/jeong-iryang/README.md` | 수정 | 로그 `0003` 링크와 다음 번호 `0004` |

프로젝트 `README.md`의 온톨로지 문서 링크는 이미 존재하므로 수정하지 않았다.

## 6. 확정한 설계 결정

- `ontology_spec.json`이 현재 구현 기준이며 `ontology-v1.md`는 사람용 설명이다.
- 실제 PDF 데이터 JSON, JSON Schema, Neo4j 적재 데이터, Cypher와 RDF/OWL은 서로 다른 산출물이다.
- 공통 교양 규칙은 common curriculum에 두고 학과별로 복제하지 않는다.
- `Course`와 `CourseOffering` 분리를 유지한다.
- 편성 ID는 curriculum, 학수번호, 학년, 학기, 이수구분을 조합한다.
- 완전히 같은 ID 구성요소의 중복 행에서 값이 충돌하면 variant ID를 임의 생성하지 않고 데이터 품질 오류로 처리한다.
- `CreditRequirement`와 `CourseRequirement`에는 `operator`를 요구하지만 `ExemptionRule`과 `TransitionRule`에는 일괄 강제하지 않는다.
- 문서–근거 연결은 `FROM_DOCUMENT` 한 방향만 저장하고 역방향으로 조회한다.
- VERIFIED 사실은 VERIFIED Evidence에 연결해야 하며 사용자 답변도 이를 기본 조건으로 한다.
- competency questions는 런타임 하드코딩 데이터가 아니라 회귀 평가 계약이다.

## 7. competency questions 분리

- 명세의 `competency_questions` 배열을 제거하고 `evaluation_contract_ref`만 남겼다.
- CQ-001~CQ-010을 별도 fixture의 `questions` 배열로 이동했다.
- 질문별 intent, parameters, 구조화된 `expected_result`, `expected_graph_patterns`, 숫자 페이지 필드를 정의했다.
- CQ-007 페이지를 재검증하여 발췌 16쪽·원본 261쪽·인쇄 253쪽으로 교정했다.
- 실제 사용자 질문은 문자열 비교가 아니라 intent·parameter 변환 후 Neo4j 조회로 처리해야 한다고 명시했다.

## 8. 수행한 검증

| 검증 항목 | 명령 또는 방법 | 실제 결과 |
|---|---|---|
| JSON 구문 | `python3 -m json.tool` | 명세와 fixture 모두 통과 |
| 중복 ID | Python 표준 라이브러리 검사 | 노드·관계·identity·invariant·extension·open decision·CQ ID 중복 없음 |
| 라벨·관계 참조 | endpoint와 상속 라벨 검사 | 통과 |
| vocabulary 참조 | 모든 속성 참조와 값 중복 검사 | 통과 |
| answerability | 허용 enum 집합과 질문 값 검사 | 10개 모두 통과 |
| Evidence 페이지 | 정수 필드와 발췌 구간 매핑 공식 검사 | 통과 |
| 그래프 패턴 | 관계의 시작·끝 라벨 및 하위 Rule 라벨 검사 | 통과 |
| Markdown 링크 | 로컬 상대 링크 대상 존재 검사 | 통과 |
| PDF 동일성 | `sha256sum`, `file` | 기존 검증과 동일한 SHA-256, 19쪽 확인 |
| 공백 오류 | `git diff --check` | 통과 |

## 9. 실패한 접근과 원인

첫 페이지 매핑 검증에서 CQ-007의 기존 값인 발췌 16쪽·원본 260쪽·인쇄 252쪽이 실패했다. 구간 시작점은 발췌 14쪽·원본 259쪽·인쇄 251쪽이므로 두 페이지 뒤인 발췌 16쪽은 원본 261쪽·인쇄 253쪽이다. fixture와 설명 문서를 교정한 뒤 전체 페이지 검사를 다시 통과했다.

## 10. 보류한 결정과 검증하지 못한 항목

- CSE 학점구조의 교양 합계를 common에만 둘지 CSE 집계 규칙으로도 표현할지 보류했다.
- 검토 모드에서 `REVIEW_REQUIRED` 사실을 예외 노출할지 보류했다.
- 경과조치 첫 문장의 원문 시각 검증은 완료하지 못했다.
- 실제 데이터 JSON 계약, Neo4j 제약조건·적재, Cypher와 질의응답은 구현하거나 검증하지 않았다.
- 전체 PDF 자동 추출과 전체 교과목 데이터 대조는 수행하지 않았다.

## 11. 남은 문제

- 실제 데이터 JSON의 최상위 구조와 Rule·Offering·Evidence 인스턴스 계약이 필요하다.
- 공통 curriculum과 학과 curriculum을 함께 조회하는 집계 경로를 확정해야 한다.
- `bbox` 좌표계와 원문 정정 데이터 구조를 정해야 한다.
- fixture를 실행할 테스트 코드와 gold result 비교 규약은 아직 없다.

## 12. 다음 권장 작업

1. 실제 데이터 JSON 계약을 정의한다.
2. PDF에서 대표 과목·규칙·Evidence를 소량 작성하고 사람이 검증한다.
3. Neo4j 제약조건과 멱등 적재를 구현한다.
4. fixture 기반 근거 포함 Cypher 질의 회귀 테스트를 작성한다.
