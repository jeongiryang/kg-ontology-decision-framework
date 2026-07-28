# PDF 자동 정규화 검토 보고서

- 원본: `2022 교육과정 -일부 발췌본.pdf`
- 대상: PDF 128, 129, 130쪽
- 결과: **WARNING**
- 자동 통과: 17건
- 확인 필요: 3건

`PASS` 항목은 두 추출 엔진, 형식 검사 또는 문서 합계 검사를 통과했다. `WARNING` 항목은 원문을 임의 수정하지 않았으며 아래 위치만 사람이 확인하면 된다.

## 1. [WARNING] 전공선택 학기별 학점 합계 검사

- 검사 ID: `allocation_total:allocation:2022:전공:전공선택`
- 기대값: `78`
- 확인값: `54`
- 이유: 같은 행의 8개 학기 값을 더한 결과와 PDF에 인쇄된 총계가 다릅니다. 자동으로 어느 값이 오자인지 결정하지 않았습니다.

### 확인 위치

| PDF 쪽 | 인쇄 쪽 | 표·구역 | 좌표 | 추출 원문 |
|---:|---:|---|---|---|
| 129 | 282 | 전공심화과정 학점배분구조표 | `[66.669, 219.535, 472.369, 229.025]` | 전공 전공선택 3 9 9 3 9 12 9 78 |

### 확인 방법

1. PDF 파일 129쪽(인쇄 282쪽)의 '5. 전공심화과정 학점배분구조표'를 엽니다.
2. '전공선택' 행의 학기별 값을 왼쪽부터 더합니다: [0, 3, 9, 9, 3, 9, 12, 9] = 54.
3. 같은 행 오른쪽 총계 78 및 바로 아래 전공 소계와 비교합니다.
4. 원본 발행기관의 정정 자료가 있으면 확인한 값을 보정 파일에 기록합니다.

### 보정 가능한 대상

- `uid=allocation:2022:전공:전공선택, field=term_credits`
- `uid=allocation:2022:전공:전공선택, field=declared_total`

원본 JSON을 직접 수정하지 말고 `data/corrections/2022_curriculum_p128_130_corrections.json`에 확인한 값과 이유를 기록한다.

## 2. [WARNING] 전공필수 학점을 세 표에서 교차 검사

- 검사 ID: `required_credit_cross_section`
- 기대값: `"세 표의 값이 일치"`
- 확인값: `{"course_catalog": 21, "degree_requirement": 21, "semester_allocation": 24}`
- 이유: PDF 128쪽의 졸업요건 및 과목표 합계는 21학점이지만, PDF 129쪽 학기배분표는 24학점으로 서로 다릅니다.

### 확인 위치

| PDF 쪽 | 인쇄 쪽 | 표·구역 | 좌표 | 추출 원문 |
|---:|---:|---|---|---|
| 128 | 281 | 기본이수 학점구조표 | `[66.667, 461.369, 472.197, 491.609]` | 컴퓨터공학과 \| 전공심화 \| 8 \| 19 \| 3 \| 30 \| \| 21 \| 57 \| 78 \| 22 \| 130 \| ○ |
| 129 | 282 | 전공심화과정 학점배분구조표 | `[66.669, 206.575, 472.369, 216.065]` | 전공필수 3 12 9 24 |
| 129 | 282 | 전공교육과정표 | `[66.669, 505.409, 472.369, 522.48]` | CDA0143 \| 고급자료구조(Advanced Data Structure) \| 3 \| 3 \| \| 2-2 \| ①③④ |

### 확인 방법

1. PDF 파일 128쪽(인쇄 281쪽) '4. 기본이수 학점구조표'의 전공필수 21학점을 확인합니다.
2. PDF 파일 129쪽(인쇄 282쪽) '5. 전공심화과정 학점배분구조표'의 전공필수 총계 24학점을 확인합니다.
3. PDF 파일 129쪽부터 시작하는 '7. 전공교육과정표'에서 전공필수 3학점 과목 7개와 0학점 과목 2개를 확인합니다. 학점 합계는 21입니다.
4. 졸업 판정에는 기본이수 학점구조표를 우선 사용하되, 외부 정정 자료가 있으면 보정 파일에 근거와 함께 기록합니다.

### 보정 가능한 대상

- `uid=requirement:2022:computer-engineering:전공심화, field=major_required_credits`
- `uid=allocation:2022:전공:전공필수, field=term_credits`
- `uid=allocation:2022:전공:전공필수, field=declared_total`

원본 JSON을 직접 수정하지 말고 `data/corrections/2022_curriculum_p128_130_corrections.json`에 확인한 값과 이유를 기록한다.

## 3. [WARNING] PDF 텍스트층의 오탈자 후보 검사

- 검사 ID: `source_spelling_candidates`
- 기대값: `[{"source_text": "운리의식", "candidate": "윤리의식"}, {"source_text": "Architechture", "candidate": "Architecture"}]`
- 확인값: `["운리의식", "Architechture"]`
- 이유: PyMuPDF와 pdfplumber는 같은 PDF 텍스트층을 사용하므로 인쇄 화면과 텍스트층의 글자가 다르면 둘 다 같은 값을 읽습니다. 자동 수정하지 않고 후보만 표시합니다.

### 확인 위치

| PDF 쪽 | 인쇄 쪽 | 표·구역 | 좌표 | 추출 원문 |
|---:|---:|---|---|---|
| 128 | 281 | talent_profile | `[112.8, 307.491, 367.868, 317.44]` | 직업의식, 운리의식을 가지고 업무에 충실하며 협력적인 인재 |
| 129 | 282 | 전공교육과정표 | `[66.669, 566.46, 472.369, 580.5]` | CDA0016 \| ※ 컴퓨터구조(Computer Architechture) \| 3 \| 3 \| \| 3-1 \| ①③④ |

### 확인 방법

1. PDF 파일 128쪽(인쇄 281쪽) '2. 전공인재상' 세 번째 문장의 '운리의식'이 화면에서 '윤리의식'인지 확인합니다.
2. PDF 파일 129쪽(인쇄 282쪽) CDA0016 영문 과목명의 'Architechture'가 발행기관 기준 오탈자인지 확인합니다.
3. 원문 표기를 그대로 보존할지 정정 표기를 사용할지 결정하고, 정정할 경우 보정 파일에 이유를 기록합니다.

### 보정 가능한 대상

- `uid=profile:2022:computer-engineering:talent_profile:3, field=text`
- `uid=course:CDA0016, field=name_en`

원본 JSON을 직접 수정하지 말고 `data/corrections/2022_curriculum_p128_130_corrections.json`에 확인한 값과 이유를 기록한다.

## 자동 통과 항목

| 검사 ID | 검사 내용 |
|---|---|
| `unique_course_codes` | 학수번호 중복 검사 |
| `major_catalog_course_count` | 전공교육과정표의 과목 수 합계 검사 |
| `major_catalog_credit_sum` | 전공교육과정표의 개설 학점 합계 검사 |
| `cross_parser_course_credits` | PyMuPDF와 pdfplumber가 읽은 학수번호·학점 비교 |
| `cross_parser_table_counts` | 두 PDF 엔진이 찾은 페이지별 표 개수 비교 |
| `program_requirement_totals` | 기본이수 학점구조표의 교양·전공·잔여학점 합계 검사 |
| `allocation_total:allocation:2022:교양:기초교양` | 기초교양 학기별 학점 합계 검사 |
| `allocation_total:allocation:2022:교양:균형교양` | 균형교양 학기별 학점 합계 검사 |
| `allocation_total:allocation:2022:교양:확대교양` | 확대교양 학기별 학점 합계 검사 |
| `allocation_total:allocation:2022:교양:교양 소계` | 교양 소계 학기별 학점 합계 검사 |
| `allocation_total:allocation:2022:전공:전공필수` | 전공필수 학기별 학점 합계 검사 |
| `allocation_total:allocation:2022:전공:전공 소계` | 전공 소계 학기별 학점 합계 검사 |
| `allocation_total:allocation:2022:전체:교양+전공` | 교양+전공 학기별 학점 합계 검사 |
| `allocation_total:allocation:2022:졸업:졸업잔여학점` | 졸업잔여학점 학기별 학점 합계 검사 |
| `allocation_total:allocation:2022:졸업:졸업학점` | 졸업학점 학기별 학점 합계 검사 |
| `competency_summary` | 과목별 전공능력 연결을 128쪽 요약표와 비교 |
| `profile_text_cross_parser` | 교육목표·인재상·전공능력 일반 문장 교차 검사 |
