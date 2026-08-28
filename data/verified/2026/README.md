# 2026 교육과정 Verified 기준 데이터

이 디렉터리는 2026학년도 국립창원대학교 교양 이수요건과 컴퓨터공학과 교육과정을 Neo4j에 적재하기 전 사용하는 검증·마이그레이션 기준 데이터다.

## 파일 역할

| 파일 | 역할 |
|---|---|
| `2026_curriculum_kg_data.json` | 온톨로지 V0.2에 맞춘 노드·관계 기준 데이터 |
| `2026_curriculum_unresolved.json` | V0.2 이후에도 남은 활성 검토 항목, 해결된 마이그레이션과 승인 정정 이력 |

원본 Raw JSON은 `data/raw/`에 있으며 직접 수정하지 않는다. Verified 데이터는 Raw 기준선의 복사본에 V0.2 마이그레이션과 승인 정정을 적용해 생성했다.

## 출처와 버전

- 원본 PDF: `2026 교육과정(교양이수요건+컴공교육과정).pdf`
- PDF SHA-256: `8ee5ee9d45fde0b00f8c42dc5aa513a46ec6a28bed4db50af25a049ae2dac004`
- 온톨로지: `cwnu_2026_academic_curriculum_ontology_v1`
- 적용 버전: `0.2.0`

## 검증 상태

- `VERIFIED`: 원문 사실, 명세 구조와 Evidence 연결을 확인한 데이터다.
- `REVIEW_REQUIRED`: 원문 의미, 단위, 적용 범위 또는 정규화 정책의 추가 결정이 필요하다. 운영 질의의 확정 답변에 사용하지 않는다.

## 승인된 정정

1. `GEA8617`은 수식없는물리로보는세상으로 확정하고 2학기 편성을 복구했다.
2. 융합프로젝트Ⅰ의 PDF 원문 학수번호 `GEA8617`은 Evidence에 보존하고, 승인 정정값 `GEA8817`로 Course를 생성했다. `이룸` 학기는 미확정이라 CourseOffering은 만들지 않았다.
3. 전공 교과목 편성표의 실기 합계 원문 `12`와 숫자형 개별 행 합계 `14`를 모두 보존했다. 주 단위 현장실습은 시간 합계에 포함하지 않았다.
4. raw 추출에 이미 있던 영어 면제 시험 Condition 9건을 원문 표의 atomic VERIFIED
   Rule·Evidence로 보완했다. 임계값은 Condition에서 복사하며 발췌 1쪽·원본 33쪽·인쇄
   25쪽을 직접 연결한다.

정정은 `CorrectionRecord-[:CORRECTS]->대상` 및 `SUPPORTED_BY` Evidence로 추적한다.

## 여전히 unresolved인 항목

- 개설학기 `이룸`
- 현장실습 실기값 `4주`, `8주`, `12주`
- 경과조치 적용 교육과정 연도 범위
- 교양 편성표의 개설 학년 부재와 관련학과 정식 매핑
- 공통 CurriculumVersion과 Institution 관계
- ApplicabilityScope 식별자 정책
- 하계·동계 복합 학기 및 교양 이수구분 정규화 정책
- CSE 학점구조표의 공통 교양 규칙 배치
- 승인되지 않은 영문명·관련학과 오탈자 후보

`2026_curriculum_unresolved.json`의 `summary`와 활성 배열이 현재 개수의 기준이다.

## Neo4j 적재 전 확인

- `ontology/ontology_spec.json` V0.2와 노드 라벨·관계 타입·속성·통제어휘가 일치해야 한다.
- 모든 관계 endpoint와 wrapper `id`/ID 속성이 일치해야 한다.
- VERIFIED Rule, CourseOffering 및 V0.2 원문 기반 노드는 VERIFIED Evidence를 가져야 한다.
- Raw JSON 및 PDF는 적재 입력으로 직접 수정하지 않는다.
- 현재 bundle 기준은 노드 1,536, 관계 3,287, Evidence 520이다.
- 빈 DB는 `neo4j_ingest load`, 이전 bundle이 적재된 전용 DB의 additive 보완은
  subset 검사를 포함한 `neo4j_ingest sync`를 사용한다.
