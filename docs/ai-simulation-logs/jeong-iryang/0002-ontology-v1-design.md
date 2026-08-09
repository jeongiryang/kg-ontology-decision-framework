# 0002. 2026 KG PoC 온톨로지 V1 설계

## 기본 정보

| 항목 | 내용 |
|---|---|
| 작성일 | 2026-08-09 |
| 담당자 | 정이량 |
| 사용 에이전트 | Codex |
| 작업 브랜치 | `feat/jeongiryang/2026-kg-poc` |
| 관련 커밋 | 기준 커밋 `93d6af8d8a0b47e582bda2b5b7f024d6e872587f`, 작업 커밋 없음 |
| 관련 Issue/PR | PR #7 병합 완료 |
| 작업 상태 | 완료 |

## 1. 작업 목적

2026학년도 교양 이수요건과 컴퓨터공학과 교육과정 19쪽을 근거로 Neo4j labeled property graph용 온톨로지 V1을 먼저 정의한다. 이후 구조화 데이터, Neo4j 스키마와 적재 코드가 임의의 라벨·관계·식별자를 만들지 않도록 사람용 설명과 기계 판독용 명세를 함께 마련한다.

## 2. 요청 내용 요약

- PR #7 병합 상태를 확인하고 로컬 `main`을 fast-forward 동기화한다.
- 정이량 담당 브랜치 `feat/jeongiryang/2026-kg-poc`를 만든다.
- 정이량이 PM 및 통합 책임자라는 로컬 운영 기준을 `AGENTS.md`에 기록한다.
- 공식 19쪽 PDF의 메타데이터, 페이지 구조와 대표 학사규칙을 확인한다.
- 온톨로지 V1 설명 문서와 JSON 명세를 작성한다.
- README에 설명 문서 링크를 추가하고 이 작업을 로그 `0002`로 기록한다.
- Neo4j 적재·Cypher·질의응답·범용 PDF 전처리와 커밋·push·PR·merge는 수행하지 않는다.

## 3. 작업 전 상태

- PR #7은 2026-08-09(한국 표준시)에 merge commit `93d6af8d8a0b47e582bda2b5b7f024d6e872587f`로 `main`에 병합된 상태였다.
- 로컬 `main`은 clean 상태였고 `origin/main`과 일치했다.
- 대상 기능 브랜치는 로컬과 원격에 존재하지 않았다.
- 루트 `AGENTS.md`는 0바이트였고 `.gitignore`의 `AGENTS.md` 규칙으로 제외되며 Git 추적 파일이 아니었다.
- `ontology/ontology_spec.json`과 `ontology/schema.cypher`는 각각 0바이트였다.
- `data/raw/`에는 공식 19쪽 PDF 외에 기존 PDF들이 함께 존재했으며, 이번 작업은 지정된 19쪽 파일만 공식 입력으로 사용했다.

## 4. 수행한 작업

- `main`에서 `feat/jeongiryang/2026-kg-poc` 브랜치를 생성했다.
- 브랜치 소유자 형식을 `feat/<owner>/<task>`, `fix/<owner>/<task>`, `docs/<owner>/<task>`로 정리했다.
- 정이량이 PM 및 통합 책임자이며 황대겸은 공동 개발 팀원이라는 로컬 운영 기준을 작성했다.
- 교수님 프로토타입은 참고 자료이고 현재 학생 레포의 구현 베이스가 아니라는 기준을 기록했다.
- 공식 PDF의 파일명, 크기, 페이지 수, SHA-256, 텍스트 레이어와 선언된 페이지 구간을 조사했다.
- 교양 학점·영역·면제·예외와 컴퓨터공학과 학점구조·편성·졸업논문·현장실습·경과조치의 대표 구조를 확인했다.
- 과목 정체성과 연도별 편성, 규칙과 적용 범위, 조건 그룹, Evidence를 분리한 온톨로지 V1을 설계했다.
- 다른 연도·학과 추가 시 기존 데이터를 덮어쓰지 않는 확장 규칙과 멱등 ID 정책을 정의했다.
- 한글 설명 문서와 동일한 노드·관계 체계의 JSON 명세를 작성했다.

## 5. 변경된 파일

| 경로 | 변경 유형 | 내용 |
|---|---|---|
| `AGENTS.md` | 로컬 생성 | 정이량 로컬 Codex 운영 지침. Git ignore 및 비추적 유지 |
| `README.md` | 수정 | 온톨로지 V1 설명 문서 링크 한 줄 추가 |
| `docs/ontology/ontology-v1.md` | 생성 | 한글 온톨로지 V1 설계 |
| `ontology/ontology_spec.json` | 수정 | 기계 판독용 온톨로지 V1 명세 |
| `docs/ai-simulation-logs/jeong-iryang/0002-ontology-v1-design.md` | 생성 | 본 작업 로그 |
| `docs/ai-simulation-logs/jeong-iryang/README.md` | 수정 | 로그 `0002` 링크와 다음 번호 `0003` |

`ontology/schema.cypher`, 소스 코드, 테스트, 의존성 파일, PDF와 기존 AI 로그는 수정하지 않았다.

## 6. 주요 결정과 이유

- 온톨로지는 RDF/OWL이 아니라 Neo4j labeled property graph 애플리케이션 스키마로 정의했다.
- `Course`에는 안정적인 과목 정체성만 두고, 학점·시수·학기·이수구분은 연도·학과별 `CourseOffering`에 둔다.
- 복합 규칙은 `Rule`과 `ApplicabilityScope`, `ConditionGroup`, `Condition`으로 분리한다.
- 학점·과목·면제·경과조치는 `Rule`의 다중 라벨로 표현해 공통 탐색과 유형별 검증을 함께 지원한다.
- 모든 확정 규칙과 편성에는 페이지·표·행의 `Evidence`를 요구한다.
- 발췌 PDF, 원본 PDF, 인쇄 페이지를 별도 속성으로 보존한다.
- `bbox`는 선택적인 `[x0, y0, x1, y1]` 숫자 배열로 정의했다.
- 현재 PDF에 없는 범위 값은 실제 데이터가 아니라 `extension_candidate` vocabulary로만 구분했다.

## 7. 검증

| 검증 항목 | 명령 또는 방법 | 결과 |
|---|---|---|
| PR과 기준 브랜치 | `gh pr view 7`, `git pull --ff-only origin main` | PR #7 병합 및 기준 커밋 동기화 확인 |
| PDF 파일 | `file`, `sha256sum`, PDF 객체·텍스트 레이어 읽기 | 824,216 bytes, 19쪽, SHA-256 확인, 텍스트 레이어 있음 |
| 페이지 매핑 | 발췌 페이지의 인쇄 페이지와 구간 제목 확인 | 발췌 1·14·19쪽이 인쇄 25·251·256쪽과 일치 |
| JSON 구문 | `python3 -m json.tool ontology/ontology_spec.json` | 통과 |
| JSON 구조 | Python 표준 라이브러리로 endpoint·vocabulary·중복 검사 | 16개 라벨, 15개 관계, 10개 vocabulary, 10개 질문; 참조 유효 |
| Markdown 링크 | 모든 로컬 상대 링크 대상 확인 | 통과 |
| Git 공백 검사 | `git diff --check` | 통과 |
| 변경 범위 | `git status --short`, `git diff --stat` | 허용된 추적 파일 5개와 ignore된 `AGENTS.md`만 변경 |
| Python 버전 | `python3 --version` | Python 3.12.3. `python` 명령은 현재 PATH에 없음 |
| 서비스·기능 테스트 | 미실행 | 문서·명세 작업이며 서비스 실행과 기능 구현은 범위 밖 |

## 8. 발견된 문제와 위험

- `data/raw/`에는 공식 19쪽 PDF만 있는 것이 아니라 기존 PDF 여러 개가 공존한다. 모두 `.gitignore` 대상이며 이번 작업에서 수정하지 않았다.
- 원본 615쪽 PDF는 이미지 중심 구조이고 현재 `pdfinfo`, `pdftotext`, PyMuPDF가 없어 원본 페이지 텍스트를 직접 대조하지 못했다.
- 발췌 PDF 텍스트 추출에서 일부 표 셀과 경과조치 문장의 읽기 순서가 섞였다. 불명확한 문장은 검증 완료 사실로 확정하지 않았다.
- 공통 교양 규칙의 버전 귀속과 검증 전 사실의 답변 허용 정책이 미결정이다.

## 9. 남은 작업

- PM과 도메인 검토자가 노드·관계·식별자·미결정 사항을 검토한다.
- 경과조치 첫 문장과 복수·연계·융합전공 표의 병합 의미를 원문 화면으로 재검증한다.
- Evidence 행 식별과 `bbox` 좌표 규약을 확정한다.
- 승인 후 제한된 구조화 데이터 형식을 정의한다.

## 10. 다음 작업 제안

1. 온톨로지 V1의 공통 교양 버전 모델과 검증 상태 답변 정책을 결정한다.
2. 대표 규칙과 교과목 편성을 수동 검증한 소규모 데이터셋으로 만든다.
3. 별도 작업에서 `ontology/schema.cypher`의 고유 제약조건과 인덱스를 작성한다.
4. 멱등 Neo4j 적재, competency question별 조회와 Evidence 포함 답변을 순차 구현한다.
