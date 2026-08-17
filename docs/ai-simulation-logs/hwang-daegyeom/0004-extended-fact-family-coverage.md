# 0004. 확장 fact family로 답변 커버리지 확대

## 기본 정보

| 항목 | 내용 |
|---|---|
| 작성일 | 2026-08-13 |
| 담당자 | 황대겸 |
| 사용 에이전트 | Claude Code (Opus 5) |
| 작업 브랜치 | feat/hwang-daegyeom/answer-coverage |
| 관련 커밋 | 없음 (작업 트리 상태) |
| 관련 Issue/PR | 없음 |
| 작업 상태 | 부분 완료 |

## 1. 작업 목적

답변 가능한 질문 범위가 좁은 원인을 코드에서 확인하고, 근거 규칙을 유지한 채 범위를 넓힌다.
"검증된 사실과 Evidence가 연결된 값만 답변에 쓴다"는 성질은 바꾸지 않는다.

## 2. 요청 내용 요약

- 답변이 제한적으로 나오는 문제를 해결한다.
- 사실을 지어내지 않는 성질은 유지한다.
- `main`은 건드리지 않고 개인 브랜치에서 작업한다.
- 확인은 로컬 GPU의 LLM을 사용하는 자연어 경로로 한다.

## 3. 작업 전 상태

`main` 기준으로 다음을 확인했다.

- `src/kg_builder/query/query_plan.py`의 `FILTER_BINDINGS` 13개가 가리키는 라벨은 7개뿐이었다.
- `src/kg_builder/answer/claim_builder.py`는 `fact_label`이 `Rule` 또는 `CourseOffering`인
  경우만 처리하고 나머지는 `ANSWER_RENDERING_UNSUPPORTED`로 거부했다.
- `ontology/llm_query_schema.json`의 `query_policy.provenance.fact_labels`에는 16개 라벨이
  이미 등재돼 있어, 하위 검증 계층은 확장을 받을 준비가 돼 있었다.
- 기준 데이터에서 Evidence·Document를 제외한 사실 노드 1,006개 중 264개(26%)가 질의로
  도달 불가능했다.
- 확장 후보 라벨의 `SUPPORTED_BY` 보유율을 세어 확인했다. CreditAllocation 117/117,
  RoadmapEntry 43/43, EducationGoal 7/7, CareerField 3/3, TalentProfile 3/3,
  CourseRecommendation 3/3으로 전부 100%였다. 반면 Condition 9건과 ConditionGroup 1건은
  `SUPPORTED_BY`가 없었다.
- 로컬 환경에는 Ollama가 설치돼 있지 않았고 `.env`에는 적재용 키 4개만 있었다.

## 4. 수행한 작업

### 4.1 질의·답변 계층 확장

1. `fact_families.py`를 추가해 확장 fact family를 선언형으로 등록했다. `SelectionMode`와
   `FilterBinding`, 필터 바인딩 표를 이 모듈로 옮기고 `query_plan.py`가 재노출하도록 했다.
2. `SelectionMode`에 6개 값을 추가하고 각 값을 fact label 하나에 1:1로 대응시켰다.
3. `QueryPlan.from_dict`에 family 계약 검증과 boolean 필터 검증을 추가했다.
4. `cypher_validator`, `schema_selector`, `cypher_generator`가 selection_mode로 확정된
   family별 바인딩과 MATCH 경로를 쓰도록 확장했다.
5. `planner`의 요청 가능 필드·필터·프롬프트에 확장 family를 반영하고, family가 선언한
   최소 필드와 기본 범위 필터를 구조적으로 보강하도록 했다.
6. `claim_builder`·`claim_validator`·`korean_renderer`에 확장 family의 Claim 변환,
   항목 단위 재검증, 한국어 렌더 분기를 추가했다.
7. 계획을 직접 지정해 실행하는 `kg_builder.answer.plan_cli`를 추가했다.
8. `tests/test_extended_fact_families.py`를 작성했다.
9. `docs/extended-fact-families.md`를 작성하고 `README.md`에 반영했다.

작업 중 기존 경로의 결함 하나를 함께 고쳤다. `build_syntax_scaffold`가 같은 속성을
범위 필터이자 요청 필드로 쓸 때 RETURN 별칭을 중복 생성해 `CYPHER_RETURN_DUPLICATE`로
거부됐다. 확장 family에서 드러났으나 기존 두 family에도 있던 문제다.

### 4.2 실측에서 드러난 문제의 수정

첫 실행에서 확장 6종 중 2종만 답변됐다. 원인을 단계별로 확인하고 세 가지를 고쳤다.

| 증상 | 원인 | 조치 |
|---|---|---|
| 학점 배분 `RESULT_FIELD_NULL` | 117행 중 42행이 원문 빈칸이라 `allocated_credits`가 null | family 기본 필터 `source_was_blank=false` |
| 학점 배분 합계 행이 개별 행과 섞임 | 합계 행에는 학년·학기가 없음 | family 기본 필터 `is_total=false`, 합계는 별도 조회 |
| 교육목표·인재상 `missing required scope filters` | 질문에 학년도가 없어 계획 모델이 `academic_year`를 생략 | 적재 학년도가 하나뿐일 때만 planner가 보강 |
| 권장 교양 과목 `does not support filters` | 계획 모델이 `grade_year`·`semester`를 사용 | `recommended_grade_year`·`recommended_semester` 필터 추가 |

`is_total`은 필수 필드로 올렸다. 이 필드가 없으면 합계 행이 개별 학기 행과 같은 모양으로
나열돼 원문과 다르게 읽힌다. 초기 실행에서 실제로 그렇게 출력되는 것을 확인하고 수정했다.

### 4.3 계획 단계에서 답을 넓히는 경로

기존 설계는 조회 범위를 좁히지 못하면 곧바로 거절했다. 그런데 근거 계약이 요구하는 것은
정밀도가 아니라 Evidence다. 정밀도 실패를 답변 거부로 처리하던 지점을 다음과 같이 바꿨다.
어느 경로든 결과는 종전과 똑같이 `VERIFIED` 사실과 Evidence 검증을 거친다.

| 상황 | 종전 | 변경 후 |
|---|---|---|
| 어느 이수요건인지 못 고름 | 되묻기 | 확인된 이수요건 전부를 근거와 함께 보여 주고, 넓혔다는 사실을 답변 앞에 밝힘 |
| 적재 후보가 하나뿐인 학년도·학과를 되물음 | 되묻기 | 그 값으로 조회 |
| 질문에 과목명이 그대로 있는데 모델이 비움 | 계약 위반 | 번들의 과목명과 대조해 채택 |
| 모드는 틀렸으나 요청 필드가 한 family 소유 | 계약 위반 | 그 family 모드로 교정 |
| 그 모드가 쓸 수 없는 필터가 섞임 | Cypher 생성 실패 | 해당 필터를 떨어뜨리고 조회 |
| 값이 없는 규칙까지 `value`를 요청 | 결과 검증 실패 | 그 필드를 갖지 않은 규칙을 조회 대상에서 제외 |

값 목록과 필드 유무는 전부 적재된 bundle에서 읽고, 어떤 필터·필드를 쓸 수 있는지는
`fact_families`의 선언이 정한다. 질문 문자열로 분기하는 곳은 없다.

넓히는 경로가 다른 질문에 답하는 통로가 되지 않도록, 질문이 적재된 과목을 이름으로
지목했는데 계획에 그 과목을 가리키는 필터가 없으면 답하지 않고 되묻는다.

### 4.4 계획 실패의 진단 기록

계획 단계에서 멈춘 요청은 종전까지 아무 기록도 남기지 않아, 커버리지가 왜 낮은지
사후에 좁힐 수 없었다. `PLANNING` trace 단계와 `planning_attempts`를 추가해 시도마다
고른 모드, 채운 필터 이름, 요청 필드, 부족 코드, 걸린 계약 문구를 남긴다.

질문에서 온 **값**은 담지 않는다. 이름과 통제 코드만 남기며, 값은 종전 `raw_question`과
같은 `KG_QUERY_TRACE_RAW_QUESTION` 정책을 따르는 자리에만 노출된다.

### 4.5 환경 구성

로컬 LLM이 없어 자연어 경로를 확인할 수 없었다. Ollama를 설치하고 `.env`를 채웠다.

- 배포 자산 이름이 바뀌어 `ollama-linux-amd64.tgz`는 404였다. `ollama-linux-amd64.tar.zst`를 받았다.
- `sudo` 비밀번호가 필요해 시스템 설치가 불가능했고, `~/.local`에 설치했다.
- `zstd`가 없고 `tar --zstd`도 실패해 `uv run --no-project --with zstandard`로 압축을 풀었다.
- `.env`에 `NEO4J_QUERY_*`(기존 로컬 계정과 동일한 값)와 `KG_LLM_*`를 추가하고 권한 600을 유지했다.
- 절차는 `docs/environment-setup.md` 9.1절에 기록했다.

작업 중 만든 `.env.bak`이 `.gitignore` 대상이 아님을 확인하고 즉시 삭제했다.

## 5. 변경된 파일

| 경로 | 변경 유형 | 내용 |
|---|---|---|
| `src/kg_builder/query/fact_families.py` | 추가 | 확장 fact family 선언, 필터 바인딩 해석 |
| `src/kg_builder/query/query_plan.py` | 수정 | family 계약·boolean 필터 검증, 선언 모듈 재노출 |
| `src/kg_builder/query/cypher_validator.py` | 수정 | family별 필터 바인딩 적용 |
| `src/kg_builder/query/schema_selector.py` | 수정 | selection_mode로 fact family 확정 |
| `src/kg_builder/llm/cypher_generator.py` | 수정 | 선언형 family 스캐폴드, RETURN 중복 제거 |
| `src/kg_builder/llm/planner.py` | 수정 | 요청 필드·필터 확장, 최소 필드·기본 필터·학년도 보강, 계획 확대·모드 교정·시도 기록 |
| `src/kg_builder/llm/models.py` | 수정 | `PlanningAttempt`·`AttemptOutcome`·확대 사유 계약 |
| `src/kg_builder/query/query_trace.py` | 수정 | `PLANNING` 단계와 `planning_attempts` 기록 |
| `src/kg_builder/query/safety_pipeline.py` | 수정 | 계획 단계용 trace 개시 입구 |
| `src/kg_builder/query/natural_language_service.py` | 수정 | 계획 실패 trace 기록, 확대 사유 전달 |
| `src/kg_builder/llm/prompts.py` | 수정 | 확장 selection_mode 지침 추가 |
| `src/kg_builder/answer/contracts.py` | 수정 | ClaimType 4종, 항목 dataclass 4종 추가 |
| `src/kg_builder/answer/claim_builder.py` | 수정 | 확장 family Claim 변환 |
| `src/kg_builder/answer/claim_validator.py` | 수정 | 확장 Claim 항목 단위 재검증 |
| `src/kg_builder/answer/korean_renderer.py` | 수정 | 확장 family 한국어 렌더 분기 |
| `src/kg_builder/answer/plan_cli.py` | 추가 | QueryPlan 직접 지정 실행 CLI |
| `ontology/llm_query_schema.json` | 재생성 | 신규 필터 7종 반영 |
| `tests/test_extended_fact_families.py` | 추가 | 선언 정합성·안전성·근거 유지·planner 보강 테스트 |
| `tests/test_failure_reporting.py` | 추가 | 모델 문장 비노출·단계별 오류 코드 고정 |
| `tests/test_planner_coverage.py` | 추가 | 확대·범위 확정·오답 방지·시도 기록 고정 |
| `src/evidence_chat/pdf_evidence.py` | 수정 | 조각난 PDF 텍스트 레이어 대응 강조 검색 |
| `src/kg_builder/answer/service.py` | 수정 | 모델 문장 차단, 확대 안내 연결 |
| `docs/extended-fact-families.md` | 추가 | 확장 범위와 확인 방법 |
| `docs/environment-setup.md` | 수정 | 9.1절 로컬 LLM(Ollama) 설치 절차 |
| `README.md` | 수정 | 확장 문서 링크와 CLI 안내 |

## 6. 주요 결정과 이유

- **기존 두 family는 선언형으로 재작성하지 않았다.** 이미 검증된 경로를 다시 쓰면 회귀
  위험만 커진다. 신규 family만 선언으로 처리했다.
- **selection_mode를 fact label에 1:1로 묶었다.** 한 질의 결과가 항상 한 라벨에서만
  나오므로 `ProvenanceContract`의 단일 fact label 계약이 그대로 유지된다.
- **확장 family는 집계 Claim을 만들지 않는다.** 학점 배분표는 원문이 합계 행을 따로
  제공한다. 항목을 더해 만든 합계는 원문에 근거가 없으므로 계산하지 않는다.
- **원문 빈칸을 0으로 바꾸지 않고 조회 범위로 분리했다.** `ResultValidator`의 null 거부는
  그대로 두고, 값이 적힌 행만 기본 대상으로 삼는 필터를 family가 선언한다.
- **학년도 보강은 선택지가 하나일 때만 한다.** 값을 추정하는 것이 아니라 범위를 닫는
  것이며, 학년도가 둘 이상이면 보강하지 않고 계약 위반으로 되돌린다.
- **Condition과 ConditionGroup은 등록하지 않았다.** `SUPPORTED_BY`가 없어 직접 근거 경로
  요건을 만족하지 못한다.
- **Ollama를 홈 디렉터리에 설치했다.** `sudo` 권한이 없어 시스템 설치가 불가능했고,
  프로젝트 `.venv`에 zstandard를 추가하지 않기 위해 일회성 환경으로 압축을 풀었다.

## 7. 검증

### 7.1 정적 검증

| 검증 항목 | 명령 | 결과 |
|---|---|---|
| 전체 테스트 | `uv run pytest -q` | 155 passed, 6 skipped, 155 subtests passed |
| CI 동일 경로 | `uv run python -m unittest discover -s tests` | Ran 161 tests, OK (skipped=6) |
| 확장 family 테스트 | `uv run pytest -q tests/test_extended_fact_families.py` | 36 passed, 36 subtests passed |
| 생성 스키마 정합성 | `uv run python -m kg_builder.query.schema_exporter check` | matches ontology_spec.json |
| 번들 검증 | `uv run python -m kg_builder.neo4j_ingest validate` | PASS; nodes=1518, relationships=3260, Evidence=511 |

### 7.2 계획 직접 지정 실행 (LLM 미사용)

`uv run python -m kg_builder.answer.plan_cli --all-examples` 결과 6종 전부 `ANSWERABLE`.

| selection_mode | 근거 수 |
|---|---:|
| CREDIT_ALLOCATION_LIST | 1 |
| ROADMAP_LIST | 2 |
| EDUCATION_GOAL_LIST | 4 |
| CAREER_FIELD_LIST | 3 |
| TALENT_PROFILE_LIST | 3 |
| COURSE_RECOMMENDATION_LIST | 3 |

### 7.3 자연어 경로 실측 (로컬 LLM 사용)

환경: RTX 4070 Ti 12GB, Ollama 0.32.9, `qwen2.5-coder:14b`, 컨텍스트 8192.
`ollama ps` 기준 `100% GPU`, `nvidia-smi` 기준 11,099MiB / 12,282MiB 사용.

10문항 중 8문항 `ANSWERABLE`. 확장 6문항은 전부 답변됐다.

| 구분 | 질문 | 상태 | 근거 | 소요 |
|---|---|---|---:|---:|
| 확장 | 컴퓨터공학과 기초교양은 학년별로 몇 학점씩 배정돼 있어? | ANSWERABLE | 12 | 14.4s |
| 확장 | 컴퓨터공학과 1학년 1학기에 권장하는 교과목은 뭐야? | ANSWERABLE | 1 | 17.5s |
| 확장 | 컴퓨터공학과의 교육목표를 알려줘 | ANSWERABLE | 4 | 10.9s |
| 확장 | 컴퓨터공학과 졸업 후 진출 분야는 어디야? | ANSWERABLE | 3 | 10.9s |
| 확장 | 컴퓨터공학과가 지향하는 인재상은? | ANSWERABLE | 3 | 10.8s |
| 확장 | 컴퓨터공학과가 권장하는 교양 과목은 뭐가 있어? | ANSWERABLE | 3 | 12.2s |
| 기존 | 2026학년도 교양 최소 이수학점은? | ANSWERABLE | 1 | 11.0s |
| 기존 | 자료구조의 이수구분은? | CLARIFICATION_REQUIRED | 0 | 3.4s |
| 기존 | 컴퓨터공학과 전공필수 과목은? | ANSWERABLE | 9 | 13.4s |
| 기존 | 자료구조는 몇 학년 몇 학기에 개설되나? | CLARIFICATION_REQUIRED | 0 | 5.4s |

`CLARIFICATION_REQUIRED` 2건이 이번 변경 때문인지 확인하기 위해, 프롬프트의 확장 지침
12줄을 임시로 제거하고 같은 두 질문을 다시 계획시켰다. 결과는 동일하게
`CLARIFICATION_REQUIRED`였다. 학년도와 학과가 질문에 없을 때의 기존 동작이며 회귀가 아니다.
확인 후 프롬프트는 원래대로 복원했다.

### 7.4 계획 단계 병목의 원인 규명과 커버리지 재측정

7.3에서 남은 `CLARIFICATION_REQUIRED`를 프롬프트 보강으로 고치려 4회 시도했고 모두
효과가 없었다. 계획을 2단계로 분리한 실험은 10문항 중 0문항으로 악화돼 원복했고,
프롬프트를 55% 줄인 실험은 11문항 중 1문항으로 개선되지 않았다.

원인을 추정으로 고치고 있었음을 확인했다. `logs/query-runs/` 49건을 전수 확인한 결과
전부 `PLAN_VALIDATION` 이후 단계였고, 계획 단계에서 멈춘 요청의 기록은 0건이었다.
`natural_language_service.py`가 계획이 `READY`가 아니면 trace를 쓰지 않고 반환했기
때문이다. 커버리지 실패는 전부 이 구간이라 무엇이 왜 막혔는지 볼 수 없는 상태였다.

기록을 남기도록 고친 뒤 실패 원인 네 가지가 처음으로 확인됐다.

| 확인된 원인 | 근거 (계획 시도 기록) |
|---|---|
| Rule 질의에 `department_id`가 붙어 Cypher 경로가 없음 | `LLM_FILTER_PATH_UNSUPPORTED` |
| 값이 없는 규칙까지 `value`를 요청해 결과 검증이 전체를 막음 | 27건 중 2건이 `value` null |
| 데이터가 하나로 정한 학과를 모델이 계속 되물음 | `missing=['DEPARTMENT']` 반복 |
| 모드는 `SINGLE_COURSE`인데 요청 필드는 다른 family 소유 | `fields=['description_ko','profile_order']` |

같은 11문항 재측정 결과는 다음과 같다. 마지막 2문항은 데이터가 없어 거절하는 것이
정상 동작이므로, 답변 가능한 9문항 중 8문항이 답변됐다.

| 질문 | 변경 전 | 변경 후 | 근거 |
|---|---|---|---:|
| 교양은 최소 몇 학점이야? | SAFE_FAILURE | ANSWERABLE (확대) | 20 |
| 자료구조는 몇 학년 몇 학기에 개설되나? | CLARIFICATION | CLARIFICATION | 0 |
| 전공필수는 몇 학점 들어야 해? | SAFE_FAILURE | ANSWERABLE (확대) | 20 |
| 학과 교육목표가 뭐야? | ANSWERABLE | ANSWERABLE | 4 |
| 졸업하려면 총 몇 학점이 필요해? | SAFE_FAILURE | ANSWERABLE (확대) | 20 |
| 1학년 1학기에 뭐 들어야 해? | CLARIFICATION | ANSWERABLE | 2 |
| 부전공은 몇 학점이야? | CLARIFICATION | ANSWERABLE (확대) | 20 |
| 진로 분야에는 어떤 게 있어? | ANSWERABLE | ANSWERABLE | 3 |
| 인재상이 뭐야? | SAFE_FAILURE | ANSWERABLE | 3 |
| 2027학년도 교육과정 알려줘 | OUT_OF_SCOPE | OUT_OF_SCOPE | 0 |
| 전자공학과 전공필수 알려줘 | OUT_OF_SCOPE | OUT_OF_SCOPE | 0 |

`ANSWERABLE 2/11 → 8/11`. 측정은 로컬 Ollama `qwen2.5-coder:14b`와 적재된 Neo4j로
실행했고, 매 실행마다 계획 모델의 표본이 달라 문항별 결과가 일부 흔들린다.

측정 중 `자료구조는 몇 학년 몇 학기에 개설되나?`가 한 차례 `ANSWERABLE`로 나왔으나
내용이 권장 교양 과목 목록이었다. 근거는 붙어 있었지만 묻지 않은 것에 답한 것이므로,
질문이 지목한 과목을 계획이 담지 않으면 답하지 않도록 막았다. 그 뒤로는 되묻기로
끝난다. 커버리지 한 문항보다 오답을 내지 않는 쪽을 택했다.

### 7.5 화면 확인에서 드러난 문제와 수정

담당자가 화면에서 `균형교양 이수요건은?`을 물었을 때, 확대 경로가 확인된 이수요건 27건을
전부 보여 줘 전공 요건까지 답에 섞였다. 근거는 모두 붙어 있었으나 묻지 않은 내용이었다.

질문의 낱말과 규칙 원문이 겹치는 **낱말 수**로 조회 대상을 추리도록 고쳤다. 처음에는
겹친 **길이**로 재려 했으나, `학점이야`가 `1학점이다`와 3글자 겹치는 규칙 하나가 이겨
정작 `교양과목을 최소 34학점` 규칙이 빠졌다. 수를 먼저 보고 길이는 보조로만 쓴다.

같은 변경에서 확대 시 요청 필드를 리터럴 `["rule_type", "description_ko"]`로 고정하던
것을 `rule_field_presence` 색인에서 계산하도록 바꿨다. 고른 규칙이 모두 `value`를 가지면
수치를 구조화해 답한다.

| 질문 | 변경 전 근거 | 변경 후 근거 |
|---|---:|---:|
| 균형교양 이수요건은? | 20 | 2 |
| 교양은 최소 몇 학점이야? | 20 | 5 |
| 전공필수는 몇 학점 들어야 해? | 20 | 2 |
| 부전공은 몇 학점이야? | 20 | 1 |
| 졸업하려면 총 몇 학점이 필요해? | 20 | 2 |

11문항 재측정 결과는 `ANSWERABLE 8/11`로 같고, 답변 길이만 줄었다.
`uv run pytest -q` 179 passed, 6 skipped.

### 7.6 미실행

| 항목 | 사유 |
|---|---|
| `uv run python -m evidence_chat.server` 화면 확인 | 담당자 직접 확인 대상 |
| 평가 질문셋 50문항 전체 측정 | 이번 범위 밖 |

## 8. 발견된 문제와 위험

- **Cypher 생성이 항상 안정적이지는 않다.** 첫 자연어 실행에서 학점 배분 질의가
  `CYPHER_RETURN_FIELD_MISMATCH`로 실패했다. 같은 계획으로 생성을 6회 반복했을 때는 6회
  모두 스캐폴드와 동일한 Cypher가 나오고 검증을 통과했다. 계획의 `intent` 문구가 프롬프트에
  섞이면서 출력이 흔들린 것으로 보이나, 원인을 확정하지는 못했다. 재시도는 1회로 설정돼 있다.
- **자연어 질문이 어느 selection_mode로 계획되는지는 모델 품질에 달려 있다.**
  "1학년 1학기에 권장하는 교과목"은 `ROADMAP_LIST`와 `COURSE_RECOMMENDATION_LIST` 어느
  쪽으로도 읽힐 수 있다. 이번 실행에서 어느 쪽이 선택됐는지는 근거 1건이라는 결과로만
  추정했고 확정하지 않았다.
- **학년도 보강은 현재 데이터가 2026 하나뿐이라 항상 동작한다.** 다년도 데이터가 들어오면
  자동으로 비활성화되며, 그때 학년도 없는 질문은 다시 계약 위반이 된다.
- **VRAM 여유가 크지 않다.** 14B 모델이 12GB 카드의 11.1GB를 쓴다. 다른 GPU 프로세스가
  있으면 GPU에 전부 올라가지 않을 수 있다.
- **`.gitignore`가 `CLAUDE.md`를 제외하지 않는다.** `CLAUDE.md` 본문은 10행으로 제외돼
  있다고 적고 있으나 실제 10행은 `AGENTS.md`다. `.coverage`도 추적 대상이다. 커밋 시
  포함되지 않도록 주의해야 한다.

- **넓힌 답변의 이수요건 선별은 낱말 겹침에 기댄다.** `졸업하려면 총 몇 학점이 필요해?`
  에서는 `필요해`가 `필요한 잔여학점`과 겹쳐, 정작 `졸업학점 기준은 130학점` 규칙이
  빠진다. 선별이 항상 옳지는 않으며, 확정하지 못했다는 안내를 답변 앞에 붙여 밝힌다.
- **`자료구조는 몇 학년 몇 학기에 개설되나?`는 여전히 되묻기로 끝난다.** 계획 모델이
  `ROADMAP_LIST` 같은 다른 모드를 고르면 과목명을 담을 필터가 없어 오답 방지 가드가
  막는다. 답하지 못하는 것이 오답보다 낫다고 보고 이 상태로 두었다.
- **계획 모델의 표본에 따라 문항별 결과가 흔들린다.** 같은 질문이 실행마다 다른 모드로
  계획된다. 8/11은 한 회차의 값이며 고정된 수치가 아니다.
- **응답 스키마에 모드별 필수 필터를 강제하는 방안은 시도하지 않았다.** 현재 `filters`는
  `required`가 비어 있어 빈 객체도 스키마상 유효하다. 다음 후보다.

## 9. 남은 작업

- 담당자가 `uv run python -m evidence_chat.server`로 화면에서 확장 답변, 확대 안내 문구,
  근거 표시를 확인한다.
- 확인이 끝나면 커밋하고 PR을 올린다. 담당자 황대겸, 확인자 정이량.
- 응답 스키마에 모드별 필수 필터를 넣어 빈 필터 객체를 생성 단계에서 막는 방안을 검증한다.
- 평가 질문셋 50문항에 대해 확장 전후 답변 가능 문항 수를 비교한다.
- Cypher 생성 불안정의 재현 조건을 좁히고, 재시도 횟수 조정이나 스캐폴드 대체 정책을 검토한다.

## 10. 다음 작업 제안

### 10.1 지금 병목은 근거가 아니라 질문 이해다

이번 작업으로 근거 계층은 의도대로 동작하는 것이 확인됐다. 데이터가 없는 질문은 거절하고,
빈 값을 채우지 않아 답변을 막았으며, 답변 문장은 Python이 조회 행으로만 만든다. 반면
계획을 직접 지정하면 확장 family 6종이 전부 답변되는데(`plan_cli --all-examples` 6/6)
자연어 경로에서는 막힌다. 데이터도 조회 경로도 있는데 입구에서 걸리는 것이다.

계획 시도 기록에서 실패의 성격이 한 가지로 모인다. Cypher 생성 실패는 0건이고, 실패는
전부 한국어 이해 쪽이다.

| 실패 | 성격 |
|---|---|
| `인재상이 뭐야?` → `SINGLE_COURSE` (3회 동일) | 의도 분류 |
| `자료구조`를 필터에 넣지 않음 | 개체 추출 |
| `균형교양`인데 어느 요건인지 모르겠다고 답함 | 질문 이해 |

주목할 점은 `인재상` 사례에서 모드는 틀렸으나 요청 필드는 `description_ko`·`profile_order`로
정확했다는 것이다. 모델은 "무엇을 알고 싶은가"는 맞히고 "어느 모드인가"를 틀린다.

### 10.2 통한 수정들의 공통점

이번에 효과가 있었던 세 가지는 모두 같은 성격이었다.

| 수정 | 실제로 한 일 |
|---|---|
| `_mode_for_fields` | 분류하지 않고 요청 필드에서 모드를 역산 |
| `_rules_related_to` | 분류하지 않고 질문과 규칙 원문을 대조 |
| `_adopt_question_values` | 추론하지 않고 질문과 적재 과목명을 대조 |

반대로 효과가 없었던 것은 모두 모델에게 더 잘 분류하라고 요구한 시도였다. 프롬프트 보강
4회, 계획 2단계 분리, 프롬프트 55% 축소가 모두 그랬다.

### 10.3 구조를 분류에서 검색으로 옮기는 방안

현재 구조는 계획 모델에게 10개 selection_mode 중 하나를 고르게 한다. fact family를
4개에서 10개로 늘리면서 선택지가 2.5배가 됐고, 그 뒤로 모드 오분류가 병목이 됐다.
family를 더 추가할수록 나빠지는 구조다.

대안은 Evidence가 붙은 노드의 텍스트를 하나의 인덱스로 만들고, 질문을 그 인덱스에
검색해 상위 후보의 **라벨에서 모드를 역산**하는 것이다.

```text
규칙 원문 27건 · 과목명 325건 · 교육목표 · 인재상 · 진로분야 · 학점배분 라벨 …

질문 ─▶ 검색 ─▶ 상위 후보 ─┬─ 후보의 라벨 → selection_mode
                            ├─ 후보의 id   → filters
                            └─ 라벨의 선언 → requested_fields
```

이 구조의 이점은 세 가지다.

- family를 추가해도 선택지가 늘지 않는다. 새 노드가 인덱스에 들어갈 뿐이다.
- 근거 계약과 성격이 같다. 검색이 곧 근거 후보를 찾는 일이고, 결과가 없으면 답할 근거가
  없다는 뜻이 되어 현재의 `NOT_FOUND`와 일치한다.
- 규모가 작다. 노드 1,518건·Evidence 511건은 전부 메모리에 올라가며 벡터 DB가 필요 없다.

LLM은 없애지 않는다. 검색이 후보를 좁힌 뒤 "이 후보 중 무엇을 묻는가"를 판단하는 역할로
남긴다. 10개 중 고르기보다 3개 중 고르기가 쉬우므로 모델에 대한 요구가 낮아진다.

### 10.4 한국어 자연어의 특성과 현재 대응 상태

| 특성 | 예 | 현재 | 필요한 것 |
|---|---|---|---|
| 교착어(조사·어미) | 교양은/교양을/교양의 | 접두 매칭으로 임시 대응 | 형태소 분석 |
| 표현 다양성 | "졸업 요건" = "몇 학점 들어야 졸업" | 대응 없음 | 임베딩 |
| 어휘 불일치 | 사용자 "졸업학점" ↔ 문서 "졸업소요학점" | 대응 없음 | 임베딩 |
| 길이 비대칭 | 질문 10자 ↔ 규칙 원문 40자 | 겹친 낱말 수로 보정 | BM25 길이 정규화 |
| 생략 | "몇 학점이야?" (학과·학년도 없음) | 후보 하나면 확정 | 현행 유지 |
| 진짜 모호 | "1학년 1학기에 뭐 들어야 해?" | 되묻기 | 현행 유지 |

`졸업하려면 총 몇 학점이 필요해?`에서 `필요해`가 `필요한 잔여학점`과 겹쳐 엉뚱한 규칙이
선택되는 문제는 첫 번째 행에 해당한다. 형태소 분석을 넣으면 어미가 걸러진다.

### 10.5 계획 모델 선택에 대한 관찰

현재 `KG_LLM_MODEL`은 `qwen2.5-coder:14b`로, 코드 작성에 맞춰진 모델이다. 이 시스템은
LLM을 두 번 부르는데, 한국어 이해가 필요한 계획 단계에서만 실패하고 코드 작성이 필요한
Cypher 생성 단계에서는 실패가 없다.

`qwen2.5:14b`(일반 instruct)는 같은 크기·같은 VRAM이고, `exaone3.5:7.8b`(한국어 특화)는
더 작다. `.env` 한 줄 변경으로 시도할 수 있다. 다만 코드 능력이 낮아지면 Cypher 생성이
흔들릴 수 있으므로 커버리지와 함께 Cypher 생성 실패율을 같이 측정해야 한다.

이번 세션에서는 시도하지 않았다. 판단 기준이 없기 때문이다(10.6 참고).

### 10.6 먼저 측정 도구가 필요하다

11문항으로는 모델이나 검색 방식을 비교할 수 없다. 계획 모델의 표본이 실행마다 달라
같은 코드에서도 문항별 결과가 흔들리는 것을 이번 측정에서 확인했다. `eval/` 브랜치의
50문항 평가셋을 자동 측정 하네스로 붙이는 일이 다른 모든 개선보다 먼저다.

이번 세션에서 추측으로 네 번 잘못 고친 원인이 정확히 이것이었다. 계획 실패가 기록되지
않아 무엇이 막혔는지 볼 수 없었고, 문항 수가 적어 개선 여부를 판정할 수 없었다.

### 10.7 권장 순서

| 순서 | 항목 | 비고 |
|---|---|---|
| 1 | 50문항 자동 측정 하네스 | 판단 기준 확보. 선행 조건 |
| 2 | 형태소 분석 + BM25 | 교착어·길이 비대칭 해결. CPU, 의존성 1개 |
| 3 | 임베딩 추가해 하이브리드 검색 | 표현 다양성·어휘 불일치 해결. VRAM 검토 필요 |
| 4 | 검색 결과로 모드 역산(구조 전환) | family 확장에 강해짐 |
| 5 | 계획 모델 종류 재평가 | 1~4 이후에는 요구가 낮아져 있을 것 |

2까지만 해도 이번에 확인된 선별 오류는 해소된다. 4가 실질적인 구조 개선이며, 5는 그
뒤에 판단해도 늦지 않다.

이 방향은 이번 브랜치의 범위를 넘는다. 이슈로 등록해 방식을 합의한 뒤 착수한다.

### 10.8 그 밖의 후속 항목

- `Condition`·`ConditionGroup`에 Evidence 경로를 부여할지 결정한다. 결정되면 영어 면제
  조건 질문(평가셋 4번 대분류 6문항)이 열린다.
- `CurriculumAggregate`의 `aggregate_type`별 렌더 설계를 검토한다.
- `Institution` 앵커 family를 추가해 대학 교육목표 3건을 도달 가능하게 만든다.
- `.github/workflows/query-safety.yml`의 트리거 `paths`에 `src/evidence_chat/**`가 빠져
  있다. 이슈 #21과 함께 처리한다.
- `.gitignore`에 `CLAUDE.md`와 `.coverage`를 추가할지 결정한다.
