# 0026. 최신 README 및 로컬 시연 배포 문서 정비

## 기본 정보

| 항목 | 내용 |
|---|---|
| 작성일 | 2026-08-18 |
| 담당자 | 정이량 |
| 사용 에이전트 | Codex |
| 작업 브랜치 | `docs/jeongiryang/readme-deployment-refresh` |
| 관련 커밋 | 본 작업 커밋 |
| 관련 Issue/PR | 본 작업 Draft PR |
| 작업 상태 | 완료 |

## 1. 작업 목적

초기 CSV 수강내역·졸업심사 골격과 최신 Evidence 기반 GraphRAG 구현이 한 README에
충돌하던 문제를 해결한다. 처음 방문한 개발자가 현재 범위, 안전 질의·답변 흐름,
로컬 실행과 제한사항을 빠르게 이해하도록 README를 재구성하고, 로컬 PC 기반
ngrok·tmux 시연 절차와 향후 정식 배포 계획을 별도 문서로 분리한다.

## 2. 요청 내용 요약

- 실제 tracked 파일, 코드 진입점, 환경변수, 의존성과 기존 상세 문서를 먼저 조사한다.
- README를 현재 2026 공통 교양·컴퓨터공학과 Evidence 질의 PoC 기준으로 전면 갱신한다.
- `docs/deployment.md`에 WSL2·tmux·ngrok 기반 외부 시연 절차를 작성한다.
- 정식 클라우드 배포와 GitHub Actions CD는 미구현으로 명확히 구분한다.
- 애플리케이션 코드, KG, 온톨로지, `.env`, PDF와 모델 파일은 변경하지 않는다.

## 3. 작업 전 상태

- README 상단은 CSV 수강내역과 `evaluator.py` 기반 전체 졸업심사를 현재 중심 기능처럼
  설명했다.
- 0바이트 초기 골격인 `main.py`, `config/settings.py`, `database/neo4j_client.py`,
  `decision_engine/evaluator.py`를 주요 실행 구조로 안내했다.
- 실제 `src/kg_builder` 자연어 질의·안전 실행·Claim 답변과 `src/evidence_chat`
  Starlette UI 설명은 문서 하단에 분산돼 있었다.
- ngrok·tmux를 이용한 현재 로컬 외부 시연 절차와 정식 배포 계획 문서가 없었다.

## 4. 수행한 작업

- `git ls-files`, `find`, `rg`로 소스·테스트·문서·workflow와 실제 CLI 진입점을 확인했다.
- `pyproject.toml`, `.env.example`, Verified bundle 문서와 설계 문서에서 Python·의존성,
  환경변수, 모델 context와 KG 개수를 교차 확인했다.
- README를 프로젝트 소개, PoC 범위, 아키텍처, 기능·제한, 실제 구조, 빠른 시작,
  Neo4j 적재, 웹·CLI 실행, 테스트와 문서 안내 순서로 재작성했다.
- `docs/deployment.md`에 로컬 시연 구조, 공식 ngrok Linux 설치, tmux 운영, 외부 검증,
  보안, 장애 확인과 향후 정식 배포 계획을 작성했다.
- ngrok 설치·agent 명령·가격 및 무료 한도 안내 링크는 ngrok 공식 문서를 기준으로 했다.

## 5. 변경된 파일

| 경로 | 변경 유형 | 내용 |
|---|---|---|
| `README.md` | 전면 수정 | 최신 Evidence 기반 GraphRAG 개요와 실행 안내 |
| `docs/deployment.md` | 신규 | 로컬 ngrok·tmux 시연 및 정식 배포 계획 |
| `docs/ai-simulation-logs/jeong-iryang/0026-readme-deployment-refresh.md` | 신규 | 본 작업 기록 |
| `docs/ai-simulation-logs/jeong-iryang/README.md` | 수정 | 로그 목록과 다음 번호 갱신 |

## 6. 주요 결정과 이유

1. README는 빠른 개요와 시작 절차에 집중하고 세부 시연 운영은 배포 문서로 분리했다.
2. LLM 역할을 QueryPlan·Cypher 후보 생성으로 한정하고 최종 답변은 검증 Claim 기반
   결정론적 Python 렌더링임을 첫 아키텍처 설명에 명시했다.
3. 로컬 PoC와 정식 배포를 혼동하지 않도록 ngrok을 임시 시연 방식으로만 설명했다.
4. ngrok plan 숫자는 변동 가능하므로 저장소에 고정하지 않고 공식 가격 페이지로
   연결했다.
5. 0바이트 초기 골격 파일은 삭제하지 않고 현재 실행 진입점이 아님을 문서화했다.

## 7. 검증

| 검증 항목 | 명령 또는 방법 | 결과 |
|---|---|---|
| lock 일관성 | `uv lock --check` | 통과, 22 packages resolved |
| whitespace | `git diff --check` | 통과 |
| 상대 링크 | README·배포 문서·작업 로그의 local Markdown target 검사 | 통과, 누락 0건 |
| 버전·환경변수·CLI | `pyproject.toml`, `.env.example`, 실제 모듈과 대조 | 확인 |
| 보호 범위 | `git diff --name-only`와 status 확인 | 의도한 문서 외 tracked 변경 없음 |
| 전체 pytest | 실행하지 않음 | 문서 전용 변경이므로 미실행 |
| Neo4j·Ollama·PDF | 실행하지 않음 | 문서 전용 변경이므로 미실행 |

## 8. 발견된 문제와 위험

- sandbox 안의 uv cache가 읽기 전용이어서 최초 `uv lock --check`가 임시 파일 생성에
  실패했다. 승인된 동일 명령을 정상 환경에서 다시 실행해 통과했다. 시스템 `python`
  명령도 없어서 문서 검증 스크립트는 프로젝트 `.venv/bin/python`으로 실행했다.
- 일부 기존 단계별 설계 문서는 작성 당시의 “다음 단계” 표현을 유지한다. README는
  현재 통합 상태를 기준으로 진입 문서 역할을 하며, 각 문서는 해당 계층의 상세 설계
  기록으로 링크한다.
- ngrok 설치 방식, 무료 plan 정책과 안내 화면은 외부 서비스 변경에 따라 달라질 수 있다.
  시연 전 공식 문서를 다시 확인해야 한다.
- 현재 외부 시연 앱에는 정식 인증이 없으므로 URL 공유 범위와 실행 시간을 제한해야 한다.

## 9. 남은 작업

- 실제 공개 시연 전 현재 PC의 tmux·ngrok·절전 설정과 모바일 외부 접속을 수동 검증한다.
- 정식 배포를 구현할 때 실제 provider, 인증, 비밀값 관리, CI/CD와 rollback 절차로
  `docs/deployment.md`의 계획 섹션을 갱신한다.

## 10. 다음 작업 제안

Draft PR에서 문서 표현과 실행 순서를 검토한 뒤, 실제 시연 직전에 ngrok 공식 문서와
로컬 health·PDF 탑재 상태를 다시 확인한다.
