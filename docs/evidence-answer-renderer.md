# VERIFIED Evidence 기반 구조화 Claim 답변 계층

## 목적과 파이프라인 위치

이 계층은 자유 형식 답변 모델의 문장을 사후 단어 검사하는 방식으로 사실을 확정하지 않는다. 기존 `ResultValidator`가 승인한 `VERIFIED` Fact와 직접 연결된 `VERIFIED Evidence` 행에서 Python이 의미 역할을 보존한 Claim을 만들고, 검증된 Claim만 결정론적으로 한국어 문장과 Citation으로 조립한다.

```text
한국어 질문
→ NaturalLanguageQueryService
→ QueryPlan·SchemaSelector·CypherGenerator
→ SafetyPipeline(정적 검증 → EXPLAIN → execute_read → ResultValidator)
→ ClaimBuilder
→ ClaimValidator
→ KoreanAnswerRenderer
→ CitationRenderer
→ CurriculumChatService 응답
```

LLM은 질문 분석과 Cypher 후보 생성에만 사용한다. 최종 사실 답변의 값·단위·극성·이수구분·과목 수·학점 합계를 생성하거나 선택하지 않는다. 답변 계층은 검증 전 Neo4j record, 미검증 Cypher 또는 executor를 직접 입력받지 않는다.

## 구조화 Claim 계약

각 `GroundedClaim`은 다음 의미를 분리한다.

- Claim 유형과 안정적인 `claim_id`
- 필드, 값, 단위, 비교 연산자와 극성
- 과목 subject 또는 검증된 규칙 설명
- 직접 연결된 `(fact_id, evidence_id)` provenance 쌍

지원 Claim 유형은 다음과 같다.

| Claim 유형 | 용도 |
|---|---|
| `FIELD_VALUE` | 학년, 학기, 학점, 이수구분 |
| `NUMERIC_REQUIREMENT` | 최소학점, 영역별 과목 수 등 값·단위·연산자 |
| `BOOLEAN_POLICY` | 면제와 같은 Boolean·극성 정책 |
| `VERIFIED_RULE_TEXT` | 별도 변환 없이 사용해야 안전한 검증 규칙 문구 |
| `COURSE_LIST` | 안정적인 과목 identity와 전체 과목 목록 |
| `AGGREGATE` | 과목 수와 학점 합계를 서로 다른 필드·단위로 보존 |

예를 들어 `fact_count=2, unit=COURSE`와 `credits_sum=6, unit=CREDIT`은 서로 다른 Claim으로 재계산된다. `MAJOR_ELECTIVE`는 중앙 enum 표시 매핑을 통해서만 `전공선택`으로 렌더링된다. `EXEMPTION` Rule은 `polarity=EXEMPT`인 Claim으로만 표현된다.

숫자 Rule을 안전하게 렌더링하려면 값만으로는 부족하므로 planner는 모델이 `value`만 요청해도 구조적 의미 필드인 `rule_type`, `operator`, `unit`, `description_ko`를 결과 계약에 추가한다. 이 보완에는 정답값이나 질문별 분기가 없다.

## Python 검증과 결정론적 렌더링

`ClaimValidator`는 다음을 원본 조회 행에서 다시 계산한다.

- 모든 Fact와 Evidence의 `VERIFIED` 상태
- Claim provenance의 직접 Fact–Evidence 쌍
- 필드 값과 enum 값
- 수치 값·단위·비교 연산자의 역할
- Boolean 면제 극성
- 전체 결과 Fact의 과목 목록 커버리지
- `fact_count`와 `credits_sum`의 독립 집계
- 중복·빈·미지원 Claim

`KoreanAnswerRenderer`는 질문 원문이나 모델 응답을 사용하지 않고 Claim 유형과 필드만으로 문장을 만든다. 지원하지 않는 조합은 임의 설명 대신 `SAFE_FAILURE / ANSWER_RENDERING_UNSUPPORTED`가 된다. `DELETE`, `CREATE`, `Cypher`, `MATCH`, `API key`, `system prompt` 등 내부 표현 검사는 추가 방어선이며, Grounding의 주 방어선은 구조화 Claim이다.

## Citation 조립

페이지 번호, Evidence 원문, 검증 상태와 Citation은 LLM 입력·출력이 아니다. Python이 Claim의 직접 provenance를 Verified 조회 행과 대조한 뒤 원래 행에서 복사한다.

```json
{
  "request_id": "request UUID",
  "status": "ANSWERABLE",
  "answer_text": "자료구조의 이수구분은 전공선택입니다.",
  "citations": [
    {
      "evidence_id": "evidence:...",
      "fact_ids": ["offering:..."],
      "excerpt_page": 17,
      "source_pdf_page": 262,
      "printed_page": 254,
      "source_text": "검증된 Evidence 원문"
    }
  ],
  "used_fact_ids": ["offering:..."],
  "used_evidence_ids": ["evidence:..."],
  "clarification": null,
  "error_code": null
}
```

`GroundedClaim`은 서버 내부 감사 계약이며 기존 UI용 JSON 필드는 유지한다. Citation은 Evidence ID로 중복 제거하고 다음 키로 고정 정렬한다.

```text
excerpt_page → source_pdf_page → printed_page → evidence_id → fact_id
```

하나의 Evidence가 여러 Fact를 지원하면 `fact_ids`를 정렬한다. Citation 원문과 개수에는 별도 상한을 적용한다.

## ChatResponse 불변조건

| 상태 | 계약 |
|---|---|
| `ANSWERABLE` | 답변, Claim, Citation, Fact ID, Evidence ID가 모두 있고 clarification/error는 없음 |
| `CLARIFICATION_REQUIRED` | 안전 문구와 비어 있지 않은 clarification만 있고 Grounding 데이터는 없음 |
| `OUT_OF_SCOPE` | 범위 밖 안전 문구, Grounding 데이터 없음 |
| `UNSUPPORTED` | 미지원 안전 문구, Grounding 데이터 없음 |
| `UNRESOLVED` | 원문·정책 검토 필요 안전 문구, Grounding 데이터 없음 |
| `NOT_FOUND` | Verified 결과 없음 안전 문구, Grounding 데이터 없음 |
| `SAFE_FAILURE` | 검증 실패 본문 없이 안전 문구와 내부 오류 코드만 반환 |

비ANSWERABLE 상태는 ClaimBuilder와 renderer에 진입하지 않는다. 내부 예외 메시지와 검증 실패 본문은 사용자 응답에 포함하지 않는다.

## 실행

query 전용 Neo4j 환경변수와 provider-neutral `KG_LLM_*` 환경변수를 설정하고 실행한다. LLM client는 planner와 Cypher generator에만 쓰인다.

```bash
uv run python -m kg_builder.answer.cli \
  "2026학년도 컴퓨터공학과 전공필수 과목을 알려줘"
```

기존 구조화 조회 CLI `kg_builder.query.natural_language_cli`도 유지한다.

## 테스트

```bash
uv sync --locked
uv lock --check
uv run python -m unittest discover -s tests -v
uv run pytest -q
uv run python -m kg_builder.query.schema_exporter check
KG_NEO4J_INTEGRATION=1 uv run pytest -q
KG_LOCAL_LLM_INTEGRATION=1 uv run pytest -q tests/test_answer_integration.py -s
```

회귀 테스트는 이수구분 의미 반전, 과목 수·학점 합계 교환, 면제 극성 반전, 최소·최대 연산자 교환, 학년·학기 교환, Fact/Evidence 불일치와 Citation 비결정성을 검사한다. 통합 테스트는 임시 trace 디렉터리를 사용하고 전후 노드 1,518개, 관계 3,260개, Evidence 511개 불변을 확인한다.

## 현재 범위와 확장 방법

- 현재 결정론적 renderer는 2026학년도 공통 교양과 컴퓨터공학과에서 실제 검증한 Rule, 단일 CourseOffering, Course 목록 Claim을 지원한다.
- 새 질의는 질문 문장 분기를 추가하지 않고 ResultValidator 반환 필드에 대응하는 ClaimBuilder/ClaimValidator/renderer 조합과 회귀 fixture를 추가한다.
- 지원하지 않는 Claim 조합은 자유 형식 모델 답변으로 우회하지 않고 안전 실패한다.
- 최종 답변 모델 호출은 제거되었으므로 provider 변경은 planner/Cypher 정확도에만 영향을 준다.
- Neo4j Community Edition PoC의 계정 권한 한계와 실제 연구실 vLLM 미검증 범위는 기존 Text-to-Cypher 안전 정책을 따른다.
