# KG Ontology Decision Framework

교육과정 PDF의 규정을 근거와 함께 구조화하고, 이후 Neo4j 지식 그래프와 Python 판정 엔진에서 사용하기 위한 프로젝트입니다.

현재 PoC 범위는 `2022 교육과정 -일부 발췌본.pdf`의 **PDF 128–130쪽**입니다.

## 현재 구현 흐름

```text
PDF 128–130쪽
   │
   ├─ PyMuPDF: 텍스트·표 선·글자 좌표 추출
   ├─ pdfplumber: 학수번호·학점 독립 재추출
   └─ Pydantic: 데이터 형식 검증
   │
   ▼
정규화 JSON + 자동 검증 결과 + 사람이 볼 검토 보고서
   │
   ▼
온톨로지 명세
   │
   └─ Neo4j 적재 및 판정 엔진은 다음 단계에서 구현
```

## 실행 방법

의존성 설치:

```bash
uv sync
```

### 웹 검토 화면

```bash
uv run streamlit run review_app.py
```

브라우저에서 다음 순서로 사용합니다.

1. PDF를 업로드합니다.
2. `추출 및 자동 검증 시작`을 누릅니다.
3. 경고 목록에서 항목을 선택하고 빨간 테두리로 표시된 규정집 원문을 확인합니다.
4. 확인자 이름을 입력하고 채팅창에 `운리의식을 윤리의식으로 수정해줘`처럼 지시합니다.
5. 변경 전·후 미리보기를 확인한 뒤 승인합니다.
6. 프로그램이 JSON을 다시 만들고 전체 검증을 다시 실행합니다.

업로드 원본과 검토 결과는 문서 SHA-256별로 `data/review_sessions/`에 저장되며 Git에는 포함되지 않습니다. 승인 전에는 JSON을 바꾸지 않고, 승인할 때마다 이전 결과를 세션의 `history/`에 보존합니다.

현재 웹 화면의 정규화 프로필은 **2022 교육과정 PDF 128–130쪽 전용**입니다. 130쪽보다 짧거나 구조가 다른 PDF는 임의로 해석하지 않고 오류를 표시합니다. `src/extraction/`의 어댑터 계층에 향후 OCR·이미지 추출기를 추가할 수 있습니다.

### 명령줄 사용

PDF 원문 블록 추출:

```bash
uv run python main.py extract \
  --pdf "data/raw/2022 교육과정 -일부 발췌본.pdf" \
  --pages 128-130 \
  --output data/processed/2022_curriculum_p128_130_raw.json
```

표 자동 복원 및 교차검증:

```bash
uv run python main.py normalize \
  --pdf "data/raw/2022 교육과정 -일부 발췌본.pdf" \
  --output data/processed/2022_curriculum_p128_130_normalized.json \
  --corrections data/corrections/2022_curriculum_p128_130_corrections.json
```

이 명령은 다음 두 파일을 생성합니다.

- `2022_curriculum_p128_130_normalized.json`: 온톨로지와 그래프 적재에 사용할 구조화 데이터
- `2022_curriculum_p128_130_normalized_review.md`: 사람이 확인해야 할 위치와 방법만 모은 보고서

`normalize`는 JSON과 보고서를 만든 뒤 상태에 따라 종료합니다.

- 종료 코드 `0`: 모든 검사 통과
- 종료 코드 `2`: 사람 확인이 필요한 `WARNING`이 있어 중단
- 종료 코드 `1`: 자동 사용이 불가능한 `FAIL`이 있어 중단

자연어 수정 지시를 적용해 JSON 다시 만들기:

```bash
uv run python main.py revise \
  --pdf "data/raw/2022 교육과정 -일부 발췌본.pdf" \
  --normalized data/processed/2022_curriculum_p128_130_normalized.json \
  --instruction "오탈자 후보를 추천값으로 모두 수정해줘" \
  --reviewer "확인자 이름" \
  --corrections-output data/corrections/2022_curriculum_p128_130_reviewed.json \
  --output data/processed/2022_curriculum_p128_130_revised.json
```

이 명령은 다음 순서로 동작합니다.

1. 현재 경고의 보정 가능 UID·필드에서만 자연어의 기존값을 찾습니다.
2. 대상이 정확히 하나일 때만 보정 기록을 만듭니다.
3. 원본 PDF를 다시 읽고 보정값을 적용합니다.
4. 수정 JSON과 새 검토 보고서를 만든 뒤 모든 검사를 다시 실행합니다.
5. 경고나 실패가 남으면 다시 중단합니다.

지원하는 지시 예시는 다음과 같습니다.

- `운리의식을 윤리의식으로 수정해줘`
- `Architechture를 Architecture로 바꿔줘`
- `오탈자 후보를 추천값으로 모두 수정해줘`
- `전공선택 총계 78을 54로 수정해줘`

현재는 경고에 연결된 문자열·숫자 필드의 명확한 변경만 지원합니다. 대상이 없거나 여러 개인 지시, 단순 확인 요청, 원문 유지 지시는 적용하지 않고 이유를 출력합니다. 누락된 표 행처럼 새 객체를 만들어야 하는 경우에도 임의로 데이터를 만들지 않고 추출기 수정 대상으로 남깁니다.

적용 전 제안 JSON만 확인하려면 `propose-corrections` 명령을 사용합니다.

```bash
uv run python main.py propose-corrections \
  --normalized data/processed/2022_curriculum_p128_130_normalized.json \
  --instruction "운리의식을 윤리의식으로 수정해줘" \
  --reviewer "확인자 이름" \
  --output data/corrections/2022_curriculum_p128_130_proposal.json
```

테스트:

```bash
uv run python -m unittest discover -s tests -v
```

## 자동 검증 상태

| 상태 | 의미 | 처리 방식 |
|---|---|---|
| `PASS` | 두 추출 엔진, 형식 또는 문서 합계 검사를 통과 | 다음 단계 입력으로 사용 가능 |
| `WARNING` | PDF 내부 값 충돌이나 오탈자 후보 | 원문을 유지하고 검토 보고서에 위치·확인법 표시 |
| `FAIL` | 페이지 누락, 코드 중복, 두 엔진의 학점 불일치 등 | 정규화 결과의 자동 사용 중단 |

생성 JSON을 직접 고치면 재실행 시 사라지므로, 확인한 수정은 다음 파일에 기록합니다.

```text
data/corrections/2022_curriculum_p128_130_corrections.json
```

보정 항목 예시:

```json
{
  "check_id": "검토 보고서의 검사 ID",
  "target_uid": "수정 대상 UID",
  "field": "수정할 필드",
  "value": "확인한 값",
  "reason": "확인 근거",
  "reviewer": "확인자"
}
```

보정 파일은 원본 PDF의 SHA-256이 일치할 때만 적용됩니다.

## 작업 화면 보관

PDF 추출·검토 과정의 화면 이미지는 `assets/screenshots/automated-extractor/`에 저장합니다. 기능 이미지와 개인 작업 파일을 구분하고 README와 PR 설명에서 같은 이미지를 재사용하기 위한 경로입니다.

- 작업 순서를 알 수 있도록 이미지 파일명 앞에 두 자리 번호를 붙입니다.
- 현재 자연어 처리 한계 화면은 `05-unsupported-natural-language-input.png`로 저장합니다.
- 확인자 이름, 로컬 경로, 계정처럼 공개하면 안 되는 정보는 이미지에서 제거한 뒤 커밋합니다.

## 프로젝트 구조

```text
kg-ontology-decision-framework/
├── README.md                              # 실행 방법과 현재 구조
├── main.py                                # extract/normalize 명령 진입점
├── review_app.py                          # PDF 업로드·원문 확인·검토 채팅 화면
├── pyproject.toml                         # 직접 사용하는 Python 패키지 정의
├── uv.lock                                # 실제 설치 버전 고정
├── assets/
│   └── screenshots/
│       └── automated-extractor/           # 자동화 추출기 작업 화면
├── data/
│   ├── raw/                               # 원본 PDF, Git 추적 제외
│   ├── processed/                         # 원문·정규화 JSON과 검토 보고서
│   ├── corrections/                       # 검토자가 확인한 보정값
│   └── review_sessions/                    # 업로드·검토 세션, Git 추적 제외
├── ontology/
│   ├── ontology_spec.json                 # 현재 작성된 온톨로지 명세
│   └── schema.cypher                      # Neo4j 제약조건, 다음 단계 구현 예정
├── src/
│   ├── extraction/
│   │   ├── base.py                        # 문서 형식별 추출 어댑터 공통 인터페이스
│   │   └── pdf.py                         # 현재 텍스트 PDF 추출 어댑터
│   ├── pdf_parser/
│   │   └── extractor.py                   # 지정 페이지만 원문·좌표와 함께 추출
│   ├── normalizer/
│   │   ├── models.py                      # 정규화 데이터와 보정 형식 정의
│   │   └── curriculum.py                  # 표 복원·교차검증·보고서 생성
│   ├── reviewer/
│   │   ├── instruction.py                 # 자연어 지시를 제한된 보정 제안으로 변환
│   │   ├── evidence.py                    # PDF 원문 영역·전체 페이지 이미지 생성
│   │   ├── presentation.py                # 검사별 사람용 비교값·결정 질문·예시 구성
│   │   └── session.py                     # 업로드·보정·재검증 세션과 이력 관리
│   ├── database/neo4j_client.py            # Neo4j 연결, 다음 단계 구현 예정
│   ├── kg_builder/builder.py               # 그래프 적재, 다음 단계 구현 예정
│   └── decision_engine/evaluator.py        # 판정 엔진, 다음 단계 구현 예정
└── tests/
    ├── test_pdf_extractor.py               # 페이지 추출 검증
    ├── test_curriculum_normalizer.py       # 정규화·경고·근거 검증
    ├── test_review_instruction.py          # 자연어 제안·적용·재검증 검증
    ├── test_review_session.py              # 업로드·원문 이미지·승인 이력 검증
    ├── test_review_presentation.py         # 사람용 경고 설명과 수정 정보 검증
    ├── test_review_app.py                  # 검토 화면 기동 검증
    └── test_neo4j.py                       # Neo4j 테스트, 다음 단계 구현 예정
```

## 주요 파일 역할

| 파일 | 역할 |
|---|---|
| `review_app.py` | PDF 업로드, 경고 대기열, 원문 이미지, 채팅, 승인 화면 제공 |
| `assets/screenshots/automated-extractor/` | 자동화 추출기의 작업 화면과 PR 설명용 이미지 보관 |
| `src/extraction/base.py` | 향후 PDF·이미지·OCR 추출기를 같은 방식으로 호출하는 인터페이스 |
| `src/pdf_parser/extractor.py` | PDF 페이지 번호를 보정하고 원문·좌표·파일 해시를 저장 |
| `src/normalizer/curriculum.py` | 학수번호를 행 기준으로 사용해 표를 복원하고 두 엔진 결과 및 합계를 검증 |
| `src/normalizer/models.py` | 잘못된 필드나 자료형이 정규화 JSON에 들어가지 못하도록 검사 |
| `src/reviewer/instruction.py` | 자연어의 수정값을 현재 경고의 보정 대상으로 제한하여 제안 |
| `src/reviewer/evidence.py` | 경고 bbox를 빨간색으로 표시한 원문 이미지를 생성 |
| `src/reviewer/presentation.py` | 검사별로 PDF 값·자동 계산값·결정 질문·명령 예시를 구성 |
| `src/reviewer/session.py` | 업로드 원본과 변경 전 결과를 보존하고 승인 후 재검증 |
| `ontology/ontology_spec.json` | 그래프 노드, 관계, 고유 ID 및 근거 연결 원칙 정의 |
| `data/processed/*_review.md` | 경고의 페이지·표·좌표·확인 순서·보정 대상을 표시 |
| `data/corrections/*.json` | 원본을 바꾸지 않고 검토자의 수정값과 이유를 별도로 보존 |

현재 Neo4j 연결, 그래프 적재와 판정 엔진 파일은 빈 골격이며 아직 실행되지 않습니다.
