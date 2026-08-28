# Neo4j V0.2 스키마 적용 및 Verified KG 적재 가이드

## 1. 목적과 사전 조건

이 문서는 `ontology/ontology_spec.json` V0.2와 `data/verified/2026/2026_curriculum_kg_data.json`을 로컬 Neo4j에 안전하고 멱등하게 적재하는 절차를 설명한다.

사전 조건은 다음과 같다.

- CPython 3.12.3과 `uv`
- Neo4j 2026.06.0
- `localhost` 또는 `127.0.0.1`의 Bolt 포트 `7687`
- 노드와 관계가 모두 0개인 빈 `neo4j` 데이터베이스
- 실제 접속정보를 보관한 Git 제외 로컬 `.env`

각 팀원은 이 저장소의 같은 Verified bundle과 명세를 사용하되 자신의 로컬 Neo4j 데이터베이스에 독립적으로 적재한다. named volume이나 로컬 DB 내용은 공유하거나 Git에 포함하지 않는다.

`data/raw/`와 `data/verified/2026/2026_curriculum_unresolved.json`은 적재 입력이 아니다. unresolved 파일은 아직 정책·원문·단위 검토가 필요한 항목을 포함하므로 검증기가 입력을 거부한다.

## 2. 의존성 설치

잠금 파일과 정확히 일치하는 환경을 구성한다.

```bash
uv sync --locked
```

현재 직접 의존성은 공식 `neo4j` Python Driver와 `.env` 로딩을 위한 `python-dotenv`다.

## 3. 환경변수

`.env.example`을 참고해 로컬 `.env`를 작성한다.

```dotenv
NEO4J_URI=neo4j://localhost:7687
NEO4J_USER=your-neo4j-user
NEO4J_PASSWORD=
NEO4J_DATABASE=neo4j
```

`NEO4J_PASSWORD`에는 로컬 실제 값을 넣되 Git, 문서, 명령행 인수 또는 로그에 복사하지 않는다. 빈 비밀번호는 거부된다. URI는 `neo4j://localhost:7687`, `bolt://localhost:7687` 또는 호스트가 `127.0.0.1`인 동등한 로컬 URI만 허용한다.

## 4. Bundle 사전 검증

연결 없이 명세와 데이터 계약을 검증한다.

```bash
uv run python -m kg_builder.neo4j_ingest validate
```

기본 입력은 다음 두 파일이다.

- 명세: `ontology/ontology_spec.json`
- 데이터: `data/verified/2026/2026_curriculum_kg_data.json`

필요한 경우 `--spec`, `--data`, `--batch-size`를 지정할 수 있다. 기본 batch size는 500이다.

검증기는 개수, ID, endpoint, 라벨·관계·속성, 상속 필수 속성, 통제어휘, Neo4j 속성 호환성, VERIFIED Evidence 및 병렬 관계 손실 가능성을 확인한다. 중첩 map과 동일 type·endpoint의 병렬 관계는 문자열 변환이나 병합 없이 실패시킨다.

## 5. 연결 확인

```bash
uv run python -m kg_builder.neo4j_ingest check-connection
```

드라이버의 `verify_connectivity()`와 `RETURN 1 AS connected`를 모두 실행한다. 출력에는 호스트·포트, database와 성공 여부만 포함하며 비밀번호는 출력하지 않는다.

연결 성공 시 Neo4j Kernel 버전과 현재 노드·관계·Evidence 개수도 출력한다. 최초 적재 대상 DB는 노드와 관계가 모두 0이어야 한다.

## 6. 스키마 적용

```bash
uv run python -m kg_builder.neo4j_ingest apply-schema
```

온톨로지 identity rule에서 22개 고유 제약조건을 생성하고 조회용 인덱스 7개를 추가한다. 모든 문장은 `IF NOT EXISTS`를 사용한다. 정적 재현본은 `ontology/schema.cypher`이며 Python 생성 결과와 테스트로 일치 여부를 확인한다.

## 7. 적재와 멱등성 확인

```bash
uv run python -m kg_builder.neo4j_ingest load
```

`load`는 다음 순서를 한 번에 수행한다.

1. bundle 사전 검증
2. 연결 확인
3. 데이터베이스가 비어 있는지 확인
4. 제약조건과 인덱스 적용
5. 안정 ID를 기준으로 노드 `MERGE`
6. 실제 endpoint 라벨과 ID 속성을 기준으로 관계 `MERGE`
7. 첫 적재 개수와 대표 사실 검증
8. 같은 bundle을 두 번째로 적재
9. 두 번째 개수와 대표 사실 재검증

비어 있지 않은 DB에서는 기존 데이터를 삭제하거나 덮어쓸 가능성을 피하기 위해 적재를 중단한다. 자동 초기화 또는 `--clear` 옵션은 제공하지 않는다.

예상 첫 번째와 두 번째 결과는 모두 다음과 같다.

```text
nodes = 1536
relationships = 3287
Evidence = 520
```

### 기존 전용 DB의 additive 보완

이전 Verified bundle이 이미 적재된 전용 로컬 DB에 새 bundle의 누락 항목만 더할 때는
`load` 대신 다음 명령을 사용한다.

```bash
uv run python -m kg_builder.neo4j_ingest sync
```

`sync`는 현재 DB의 모든 안정 identity와 relationship type이 새 bundle의 부분집합인지
먼저 검사한다. 예상 개수보다 많거나 bundle에 없는 identity가 하나라도 있으면 실패한다.
검사를 통과한 뒤에만 `MERGE`를 두 번 실행해 생성량 0의 멱등성을 확인하며 삭제·초기화는
수행하지 않는다. unrelated/shared DB를 일반 병합 대상으로 쓰는 명령이 아니다.

## 8. 적재 결과 검증

이미 적재된 전용 데이터베이스를 다시 검증한다.

```bash
uv run python -m kg_builder.neo4j_ingest verify
```

전체 개수와 대표 사실이 다르면 종료 코드 2로 실패한다.

## 9. Neo4j Browser 대표 검증 Cypher

전체 개수를 확인한다.

```cypher
MATCH (n) RETURN count(n) AS nodes;
MATCH ()-[r]->() RETURN count(r) AS relationships;
MATCH (e:Evidence) RETURN count(e) AS evidence;
```

정정 과목과 GEA8617 편성 근거를 확인한다.

```cypher
MATCH (c:Course)
WHERE c.course_code IN ['GEA8617', 'GEA8817']
RETURN c.course_code, c.name_ko
ORDER BY c.course_code;

MATCH (o:CourseOffering {semester: 'SECOND', completion_type: 'GENERAL_ELECTIVE'})
      -[:OF_COURSE]->(c:Course {course_code: 'GEA8617'})
MATCH (o)-[:SUPPORTED_BY]->(e:Evidence {verification_status: 'VERIFIED'})
RETURN o.offering_id, o.semester, o.completion_type, e.evidence_id, e.raw_text;
```

융합프로젝트Ⅰ의 원문 오기와 정정값, 미생성 편성을 확인한다.

```cypher
MATCH (c:Course {course_code: 'GEA8817'})<-[:CORRECTS]-(x:CorrectionRecord)
MATCH (x)-[:SUPPORTED_BY]->(e:Evidence)
OPTIONAL MATCH (o:CourseOffering)-[:OF_COURSE]->(c)
RETURN c.name_ko, x.source_value, x.corrected_value,
       e.raw_text, count(o) AS offering_count;
```

실기 합계 정정을 확인한다.

```cypher
MATCH (x:CorrectionRecord)-[:CORRECTS]->(a:CurriculumAggregate)
WHERE x.correction_type = 'AGGREGATE_VALUE'
RETURN x.source_value, x.corrected_value,
       a.source_value, a.normalized_value;
```

컴퓨터공학과 전공필수 개수와 학점, 자료구조 편성을 확인한다.

```cypher
MATCH (:CurriculumVersion {curriculum_id: 'curriculum:cwnu:2026:cse'})
      -[:HAS_OFFERING]->(o:CourseOffering {completion_type: 'MAJOR_REQUIRED'})
RETURN count(o) AS offerings, sum(o.credits) AS credits;

MATCH (o:CourseOffering)-[:OF_COURSE]->(c:Course {course_code: 'CDA0008'})
RETURN c.name_ko, o.grade_year, o.semester;

MATCH (o:CourseOffering)-[:OF_COURSE]->(c:Course)
WHERE o.correction_note CONTAINS '부전공 필수'
RETURN c.course_code, c.name_ko ORDER BY c.course_code;
```

교양 규칙과 Evidence를 확인한다.

```cypher
MATCH (r:Rule)-[:SUPPORTED_BY]->(e:Evidence {verification_status: 'VERIFIED'})
WHERE r.rule_id IN [
  'rule:cwnu:2026:general:min-total-default',
  'rule:cwnu:2026:general:balanced-min-credits',
  'rule:cwnu:2026:general:transfer-exemption'
]
RETURN r.rule_id, r.value, r.unit, r.description_ko,
       collect(e.evidence_id) AS evidence_ids
ORDER BY r.rule_id;
```

기대 사실은 교양 최소 34학점, 균형교양 최소 12학점, 편입생 교양 면제다.

## 10. 2026-08-10 로컬 통합 검증 결과

비밀값을 제외한 실제 실행 결과는 다음과 같다.

| 항목 | 실제 결과 |
|---|---|
| Neo4j | Neo4j Kernel 2026.06.0 |
| 접속 endpoint | `localhost:7687` |
| database | `neo4j` |
| 적재 전 | 노드 0, 관계 0 |
| 제약조건 | 22개 |
| 프로젝트 RANGE 인덱스 | 7개 |
| 1차 적재 | 노드 1,518, 관계 3,260, Evidence 511 |
| 1차 생성 | 노드 1,518, 관계 3,260 |
| 2차 적재 | 노드 1,518, 관계 3,260, Evidence 511 |
| 2차 생성 | 노드 0, 관계 0 |
| 2차 기존 매칭 | 노드 1,518, 관계 3,260 |
| 멱등성 | PASS, 개수 증가량 0 |
| 대표 Cypher | 전체 PASS |

Neo4j 기본 LOOKUP 인덱스 2개와 고유 제약조건이 소유하는 backing index는 위 프로젝트 RANGE 인덱스 7개에 포함하지 않는다.

## 11. 2026-08-28 영어 면제 atomic Rule 보완

기존 DB 1,518/3,260/511에서 `sync`를 실행해 이미 추출된 영어 시험 Condition 9건의
VERIFIED atomic Rule·Evidence를 추가했다.

| 항목 | 실제 결과 |
|---|---|
| sync 전 | 노드 1,518, 관계 3,260, Evidence 511 |
| 1차 생성 | 노드 18, 관계 27 |
| sync 후 | 노드 1,536, 관계 3,287, Evidence 520 |
| 2차 생성 | 노드 0, 관계 0 |
| `verify` | 개수·대표 사실 PASS |

새 Evidence는 발췌 1쪽, 원본 PDF 33쪽, 인쇄 페이지 25쪽의 영어 면제 표 원문을
가리킨다. raw JSON과 PDF는 수정하지 않았다.

## 12. 오류 처리와 안전 원칙

- 환경변수 누락, 빈 비밀번호, 비로컬 URI는 연결 전에 실패한다.
- 비어 있지 않은 데이터베이스는 노드·관계 개수와 상위 라벨만 보고하고 중단한다.
- endpoint 미매칭, batch 처리 개수 불일치, 최종 개수 불일치는 성공으로 처리하지 않는다.
- 라벨·관계 타입·ID 속성은 명세 allowlist와 안전 식별자 정규식을 모두 통과해야 한다.
- 속성값과 map은 Cypher 파라미터로 전달한다.
- 데이터 삭제, database/constraint/index 삭제 기능은 제공하지 않는다.
- 실제 비밀번호는 예외 메시지나 정상 출력에 포함하지 않는다.
