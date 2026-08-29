# 정이량 AI 시뮬레이션 로그

- 폴더 담당자: 정이량
- 로그 목적: 정이량 담당 작업에서 AI 에이전트가 수행한 변경, 결정, 검증 결과, 오류와 후속 작업을 추적한다.
- 파일명 규칙: `NNNN-short-task-name.md`
  - `NNNN`은 이 폴더에서 독립적으로 증가하는 4자리 번호다.
  - 작업명은 영문 소문자 kebab-case로 작성한다.
- 현재 다음 로그 번호: `0028`
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
- [0016. ChatResponse 승인 발급 경계와 오류 코드 정제](0016-chat-response-issuance-boundary.md)
- [0017. PR #14 최신 백엔드·프론트 통합](0017-evidence-chat-integration.md)
- [0018. Evidence chat 수동 QA 핵심 보완](0018-evidence-chat-manual-qa.md)
- [0019. Evidence chat HTTP 503 진단과 과목코드 실동작 보완](0019-evidence-chat-503-diagnosis.md)
- [0020. PR #28 inspection 승인 경계와 졸업질문 분류 보완](0020-inspection-approval-and-graduation-classification.md)
- [0021. PR #28 실시간 처리 타임라인과 승인 Cypher 추적 UI](0021-realtime-query-timeline.md)
- [0022. PR #28 canonical Cypher와 타임라인 보완](0022-canonical-cypher-and-timeline-fix.md)
- [0023. PR #29 clarification·progress 통합](0023-pr29-integration.md)
- [0024. 질의 inspection 그래프 UI](0024-query-inspection-graph-ui.md)
- [0025. 실제 승인 데이터 기반 질의 탐색 UI 개선](0025-query-exploration-ui.md)
- [0026. 최신 README 및 로컬 시연 배포 문서 정비](0026-readme-deployment-refresh.md)
- [0027. 50문항 질의 정확도·브라우저 개인화·KG 보완](0027-query-personalization-kg-coverage.md)
