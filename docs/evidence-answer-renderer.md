# VERIFIED Evidence 기반 한국어 답변 계층

## 목적과 파이프라인 위치

이 계층은 동적 Text-to-Cypher 결과를 임의의 자연어로 바로 노출하지 않는다. 기존 안전 파이프라인의 `ResultValidator`가 승인한 `VERIFIED` Fact와 직접 연결된 `VERIFIED Evidence`만 받아 한국어 답변과 Citation을 만든다.

```text
한국어 질문
→ NaturalLanguageQueryService
→ QueryPlan·SchemaSelector·CypherGenerator
→ SafetyPipeline(정적 검증 → EXPLAIN → execute_read → ResultValidator)
→ EvidenceAnswerGenerator
→ AnswerValidator
→ CitationRenderer
→ CurriculumChatService 응답
```

`CurriculumChatService`가 최종 답변의 공식 진입점이다. 답변 계층은 검증 전 Neo4j record, 미검증 Cypher 또는 executor를 직접 입력받지 않는다.

## LLM과 Python의 책임

답변 모델은 provider-neutral `StructuredLLMClient`만 사용한다. Ollama 또는 OpenAI-compatible/vLLM으로 바꿔도 답변 계약과 검증 코드는 바뀌지 않는다.

LLM이 생성하는 값은 다음 세 필드뿐이다.

```json
{
  "answer_text": "2026학년도 교양 최소 이수학점은 34학점입니다.",
  "used_fact_ids": ["rule:cwnu:2026:general:min-total-default"],
  "used_evidence_ids": ["evidence:..."]
}
```

페이지 번호, Evidence 원문, 검증 상태, Citation, 내부 오류 코드는 LLM이 생성하지 않는다. Python은 모델이 선택한 ID가 조회 결과에 존재하는지, Fact와 Evidence가 직접 연결됐는지, 둘 다 `VERIFIED`인지 검증한다. 이후 원래 조회 행에서 페이지와 원문을 복사해 Citation을 조립한다.

모델 입력에는 질문, 검증된 Fact의 허용 필드, 안정적인 Fact/Evidence ID와 필요한 길이로 제한한 Evidence excerpt만 포함한다. 비밀번호, 환경변수, Cypher와 페이지 번호는 포함하지 않는다. 모델 원문 응답과 사용자 질문은 답변 계층에서 파일로 기록하지 않는다. 기존 query trace도 기본 정책에서는 질문 원문을 저장하지 않는다.

## 최종 응답 계약

```json
{
  "request_id": "request UUID",
  "status": "ANSWERABLE",
  "answer_text": "2026학년도 교양 최소 이수학점은 34학점입니다.",
  "citations": [
    {
      "evidence_id": "evidence:...",
      "fact_ids": ["rule:..."],
      "excerpt_page": 1,
      "source_pdf_page": 33,
      "printed_page": 25,
      "source_text": "검증된 Evidence 원문"
    }
  ],
  "used_fact_ids": ["rule:..."],
  "used_evidence_ids": ["evidence:..."],
  "clarification": null,
  "error_code": null
}
```

Citation 페이지 필드는 다음을 뜻한다.

- `excerpt_page`: 19쪽 발췌 PDF 안의 페이지
- `source_pdf_page`: 전체 원본 PDF의 페이지
- `printed_page`: 문서에 인쇄된 페이지 번호
- `source_text`: Verified KG에 저장된 Evidence 원문

동일 Evidence를 여러 Fact가 함께 사용하면 Citation 한 건으로 합치고 `fact_ids`에 직접 연결된 Fact를 모은다. 모델이 선택하지 않은 Evidence는 Citation에 넣지 않는다.

## 지원 상태와 결정론적 처리

| 상태 | 처리 |
|---|---|
| `ANSWERABLE` | LLM 초안 → Python 검증 → Citation 조립 |
| `CLARIFICATION_REQUIRED` | LLM 미호출, 추가 조건 요청과 기존 clarification 반환 |
| `OUT_OF_SCOPE` | LLM 미호출, 현재 데이터 범위 밖 안내 |
| `UNSUPPORTED` | LLM 미호출, 미지원 질문 안내 |
| `UNRESOLVED` | LLM 미호출, 원문·정책 검토 필요 안내 |
| `NOT_FOUND` | LLM 미호출, Verified 결과 없음 안내 |
| `SAFE_FAILURE` | LLM 미호출 또는 안전 실패 후 일반화된 안내 |

내부 Query 오류는 최종 사용자에게 그대로 공개하지 않는다. 답변 검증이 실패하면 오류 코드만 모델에 주어 최대 한 번 다시 생성한다. 두 번째도 실패하면 Citation 없는 `SAFE_FAILURE`와 `ANSWER_VALIDATION_FAILED`를 반환한다. 검증에 실패한 본문을 자유 텍스트 fallback으로 노출하지 않는다.

## 답변 검증 규칙

`AnswerValidator`는 다음을 거부한다.

- 빈 답변, 한국어가 없는 답변, 길이 또는 Citation 상한 초과
- 결과에 없는 Fact/Evidence ID 또는 일부 Fact 누락
- `REVIEW_REQUIRED` Fact/Evidence
- 직접 Fact–Evidence provenance가 없는 조합
- 조회 Fact·Evidence 및 결정론적 집계로 뒷받침되지 않는 숫자
- 조회 결과에 없는 과목명 등 근거 없는 한국어 엔터티
- 모델이 본문에 만든 페이지 번호
- Cypher, system prompt, API key 또는 환경변수 노출 표현

여러 과목 결과의 Fact 수와 학점 합계는 Python이 조회 행에서 계산해 모델 입력의 `derived_facts`로 제공한다. 특정 정답값을 프롬프트나 런타임 코드에 하드코딩하지 않는다.

한국어 엔터티 검사는 전체 형태소 분석기가 아니라 거부 우선의 경량 grounding 검사다. 검증된 행의 텍스트·enum 의미와 제한된 문법 어휘만 허용하므로 자연스러운 표현을 안전 실패로 거부할 수 있다. 이는 근거 없는 내용을 허용하는 것보다 안전 실패를 택한 정책이며, 후속 평가셋으로 허용 표현을 확장해야 한다.

## 실행

기존 query 전용 Neo4j 환경변수와 provider-neutral `KG_LLM_*` 환경변수를 설정하고 실행한다.

```bash
uv run python -m kg_builder.answer.cli \
  "2026학년도 컴퓨터공학과 전공필수 과목을 알려줘"
```

기존 구조화 조회 CLI `kg_builder.query.natural_language_cli`는 그대로 유지한다. 새 CLI는 프론트엔드 연결 대상인 최종 응답 JSON만 출력한다.

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

통합 테스트는 `TemporaryDirectory`에 query trace를 격리하고 테스트 전후 노드 1,518개, 관계 3,260개, Evidence 511개가 동일한지 확인한다. 실제 LLM/Neo4j가 없어서 skip된 테스트는 통과로 간주하지 않는다.

## 현재 범위와 남은 제한

- 데이터 범위는 2026학년도 공통 교양과 컴퓨터공학과 교육과정이다.
- 답변 모델은 현재 로컬 설정의 Ollama `qwen2.5-coder:14b`로 실측한다.
- OpenAI-compatible adapter는 같은 `StructuredLLMClient` 계약을 사용하지만 실제 연구실 vLLM 답변 통합은 아직 별도 검증이 필요하다.
- Neo4j Community Edition PoC에서는 서버 권한이 최종 읽기 전용 경계를 보장하지 못할 수 있다. 동적 질의 운영에서는 별도 읽기 전용 계정과 Enterprise 권한 검증이 필요하다.
- 프론트엔드는 `ChatResponse` JSON의 상태·Citation을 그대로 표시하고, 내부 Cypher나 프롬프트를 요구하지 않아야 한다.
