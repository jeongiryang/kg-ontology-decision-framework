# 0005. 온톨로지 V0.2 확장 및 기준 데이터 마이그레이션

## 기본 정보

| 항목 | 내용 |
|---|---|
| 작성일 | 2026-08-10 |
| 담당자 | 정이량 |
| 사용 에이전트 | Codex |
| 작업 브랜치 | `feat/jeongiryang/2026-kg-data` |
| 관련 커밋 | 기준 커밋 `494edb6e547064f7fcd0f715b244cae35ef875f7` |
| 관련 Issue/PR | 없음 |
| 작업 상태 | 완료 |

## 1. 작업 목적

수정 전 Raw 기준선과 온톨로지 V0.1을 바탕으로 컴퓨터공학과 인재상·목표·역량·로드맵·권장과목·집계·학점배분을 정식 그래프로 표현하고, 승인된 원문 오류 두 건을 추적 가능한 형태로 반영한 Neo4j 적재 전 V0.2 기준 데이터를 만든다.

## 2. 요청 내용 요약

- V0.1 정의를 삭제하거나 의미 변경하지 않고 V0.2로 확장한다.
- 기존 unresolved의 7개 서술 그룹과 20개 구조 사실을 정식 노드·관계로 이관한다.
- CourseOffering의 역량 원문 배열을 유지하면서 Competency 관계로 정규화한다.
- 학수번호와 실기 합계 정정을 Evidence 원문과 분리한다.
- 해결되지 않은 학기·단위·범위·정규화 문제는 활성 unresolved로 남긴다.
- Neo4j, Cypher, LLM과 Git 게시 작업은 수행하지 않는다.

## 3. 작업 전 상태

- 브랜치는 `feat/jeongiryang/2026-kg-data`였다.
- 기준 커밋은 `494edb6`이며 작업 트리는 clean이었다.
- Raw KG와 unresolved JSON은 각각 최초 추출본과 V0.1 미해결·부분 반영 데이터로 확인됐다.
- Raw SHA-256은 다음과 같다.
  - KG: `df18a1fbc73e3f84b77a2d7b7d1109f5f85a4ce0fe231239bc14f3141ec72c94`
  - unresolved: `5bff959162db138c94e053cd7958e2c77ab46db2cdd773617f75afb2cb2812fa`
- `AGENTS.md`와 원본 PDF는 작업 대상이 아니었다.

## 4. 수행한 작업

### 온톨로지 확장

- `ontology_spec.json`의 버전을 `0.2.0`으로 올렸다.
- V0.1의 16개 노드 라벨과 14개 관계 타입을 유지하고 새 라벨 10개와 관계 타입 17개를 추가했다.
- 연계 강도, 역량 유형·역할, 로드맵 항목 유형, 집계·정정 유형과 승인 상태 통제어휘를 추가했다.
- V0.2 원문 기반 노드의 Evidence, 연계표 공란, 학점배분 공란, 로드맵·편성 분리, 권장·필수 분리와 정정 추적 불변조건을 추가했다.

### 데이터 마이그레이션

- 전공 인재상 3개, 학과 교육목표 4개, 대학 교육목표 3개와 진출 분야 3개를 노드화했다.
- 전공능력 5개와 대학 핵심역량 5개를 정규화했다.
- 3종 연계표의 57개 셀을 `Alignment`로 만들고 공란도 `NONE`으로 보존했다.
- 로드맵의 교과목·비교과·유의사항 43개를 `RoadmapEntry`로 분리했다.
- 학과 권장 교양과목 3개를 `CourseRecommendation`으로 만들었다.
- 최소전공학점제, 전공능력별 집계, 전체 전공과목·학점과 편성 시수 집계 8개를 `CurriculumAggregate`로 만들었다.
- 학점배분표 13개 행의 학기·총계 셀 117개를 `CreditAllocation`으로 만들었다.
- 기존 CourseOffering의 competency 원문 427개를 `DEVELOPS_COMPETENCY` 관계로 정규화했다.

### 승인 정정

- `GEA8617` 수식없는물리로보는세상 Course와 2학기 CourseOffering을 복구했다.
- 융합프로젝트Ⅰ은 `GEA8817` Course로 생성하고 PDF 원문 `GEA8617`은 Evidence에 유지했다.
- 융합프로젝트Ⅰ의 `이룸` 학기는 추정하지 않아 CourseOffering을 보류했다.
- 전공 실기 합계는 source value `12`, normalized/corrected value `14`로 분리했다.
- 현장실습 `4주·8주·12주`는 시간으로 환산하거나 합계에 포함하지 않았다.
- 두 정정 모두 `CorrectionRecord`, `CORRECTS`, `SUPPORTED_BY`로 추적한다.

## 5. 변경된 파일

| 경로 | 변경 유형 | 내용 |
|---|---|---|
| `ontology/ontology_spec.json` | 수정 | V0.2 라벨·관계·속성·통제어휘·식별자·불변조건 |
| `docs/ontology/ontology-v1.md` | 수정 | 파일명을 유지한 V0.2 사람용 설계 설명 |
| `data/verified/2026/2026_curriculum_kg_data.json` | 생성 | V0.2 Neo4j 적재 전 기준 그래프 데이터 |
| `data/verified/2026/2026_curriculum_unresolved.json` | 생성 | 남은 활성 검토 항목과 해결 이력 |
| `data/verified/2026/README.md` | 생성 | 데이터 역할·출처·상태·정정·잔여 위험 설명 |
| `docs/ai-simulation-logs/jeong-iryang/0005-ontology-v02-data-migration.md` | 생성 | 본 작업 기록 |
| `docs/ai-simulation-logs/jeong-iryang/README.md` | 수정 | 로그 `0005` 링크와 다음 번호 갱신 |

`data/raw/`의 두 JSON과 원본 PDF는 수정하지 않았다.

## 6. 주요 결정과 이유

- 연계표 셀은 직접 관계 속성이 아니라 `Alignment` 노드로 재구체화하여 셀별 Evidence와 `HIGH`·`LOW`·`NONE`을 보존했다.
- 로드맵 권장 학기는 실제 CourseOffering 개설학기와 다르므로 별도 노드로 분리했다.
- 학과 권장과목은 실제 편성·필수 규칙과 다른 의미이므로 `CourseRecommendation`으로 분리했다.
- 집계값은 원자 CourseOffering에서 다시 계산한 값과 구분되는 PDF 사실이므로 `CurriculumAggregate`로 만들었다.
- 학점배분 공란은 노드를 생략하거나 숫자 0으로 바꾸지 않고 `source_was_blank=true`로 저장했다.
- `CourseOffering.competency` 배열은 원문 보존을 위해 유지하고 정규화 관계를 추가했다.
- 비교과 `봉림소프트웨어전`은 Course를 만들지 않았다.

## 7. 검증

| 검증 항목 | 명령 또는 방법 | 실제 결과 |
|---|---|---|
| JSON 구문 | Python 3.12 `json` 및 `json.tool` | 명세, verified KG, verified unresolved 모두 통과 |
| V0.1 하위 호환 | 기준 커밋 명세와 정의별 비교 | 기존 노드·관계·통제어휘·ID·불변조건 유지 |
| 스키마 참조 | 표준 라이브러리 검사 | 26개 라벨·31개 관계 타입, 속성·상속·통제어휘 모두 통과 |
| 그래프 무결성 | ID·관계 tuple·endpoint 검사 | 중복 노드·관계 및 누락 endpoint 없음 |
| Evidence | VERIFIED 사실과 V0.2 신규 원문 노드 검사 | 조건 충족 |
| 마이그레이션 | Raw 그룹·셀 수와 목적 노드 비교 | 7개 서술 그룹, 20개 구조 사실, 13개 이력 그룹 통과 |
| 정정 | ID·원문 Evidence·source/corrected 값 검사 | 승인 정정 2건 통과 |
| Raw 불변 | 작업 전후 SHA-256 비교 | 두 Raw 파일 동일 |
| Markdown 링크 | 상대 경로 대상 확인 | 통과 |

최종 verified KG는 노드 1,518개와 관계 3,260개다. Raw 대비 노드 403개, 관계 1,314개와 Evidence 147개가 추가됐다.

## 8. 발견된 문제와 위험

- 첫 임시 검증 실행은 관계 중복 검사 괄호 오타로 실행되지 않았다. 임시 검사기만 수정했고 저장소 산출물에는 영향이 없었다.
- 두 번째 실행은 V0.1의 `any_primitive`와 boolean scalar 타입 해석이 검사기에 없어 실패했다. 명세의 기존 타입 의미를 반영한 뒤 전체 검증을 통과했다.
- 학기 `이룸`, 현장실습 주 단위, 경과조치 연도 범위 등 10개 스키마·정책 갭은 여전히 남아 있다.

## 9. 남은 작업

- 개설학기 `이룸` 의미와 현장실습 주 단위 모델을 확정한다.
- 경과조치 적용 연도 범위와 교양 관련학과 매핑을 결정한다.
- 공통 교육과정–Institution 관계와 ApplicabilityScope 식별자 정책을 정한다.
- 하계·동계 복합 학기, 교양 이수구분과 CSE 공통 교양 규칙 배치를 승인한다.
- 승인되지 않은 원문 오탈자는 계속 REVIEW_REQUIRED로 유지한다.

## 10. 다음 작업 제안

V0.2 명세와 verified 기준 데이터를 입력으로 Neo4j uniqueness·existence 제약조건을 정의하고, 노드와 관계를 멱등 적재하는 스크립트를 구현한다. 적재 후 전체 개수와 Evidence 연결을 Cypher로 대조한다.
