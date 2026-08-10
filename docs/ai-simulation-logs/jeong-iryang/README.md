# 정이량 AI 시뮬레이션 로그

- 폴더 담당자: 정이량
- 로그 목적: 정이량 담당 작업에서 AI 에이전트가 수행한 변경, 결정, 검증 결과, 오류와 후속 작업을 추적한다.
- 파일명 규칙: `NNNN-short-task-name.md`
  - `NNNN`은 이 폴더에서 독립적으로 증가하는 4자리 번호다.
  - 작업명은 영문 소문자 kebab-case로 작성한다.
- 현재 다음 로그 번호: `0016`
- 중앙 템플릿: [AI 시뮬레이션 로그 템플릿](../_template.md)
- 중앙 운영 규칙: [AI 시뮬레이션 로그 운영 규칙](../README.md)

## 로그 목록

- [0001. 프로젝트 문서화 기준 구축](0001-project-documentation-baseline.md)
- [0002. 2026 KG PoC 온톨로지 V1 설계](0002-ontology-v1-design.md)
- [0003. 온톨로지 V1 스키마 검토](0003-ontology-v1-schema-review.md)
- [0004. 2026 교육과정 데이터 큐레이션 시도](0004-2026-curriculum-data-curation.md)
- [0005. 온톨로지 V0.2 확장 및 기준 데이터 마이그레이션](0005-ontology-v02-data-migration.md)
- [0006. Neo4j V0.2 멱등 적재 구현](0006-neo4j-idempotent-ingestion.md)
- [0007. Verified KG 읽기 전용 질의·Evidence 응답 계층](0007-query-evidence-api.md)
- [0008. Text-to-Cypher 스키마·검증·실행 안전 기반](0008-text-to-cypher-safety.md)
- [0009. PR #13 Text-to-Cypher 안전성 보완](0009-text-to-cypher-security-remediation.md)
- [0010. RTX 4070 Ti 로컬 LLM Text-to-Cypher PoC](0010-local-llm-query-pipeline.md)
- [0011. LLM provider 이식성과 질의 의미 회귀 보완](0011-llm-provider-portability.md)
- [0012. LLM HTTP redirect 보안 강화](0012-llm-http-redirect-hardening.md)
- [0013. VERIFIED Evidence 기반 한국어 답변 계층](0013-evidence-answer-renderer.md)
- [0014. 구조화 Claim 기반 답변 Grounding 보안 수정](0014-structured-claim-grounding.md)
- [0015. ValidatedClaims 승인 경계와 Claim 전체 검증 보완](0015-validated-claims-approval-boundary.md)
