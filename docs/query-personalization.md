# 질의 정확도·브라우저 개인화·KG 보완 계약

## 목적과 범위

이 문서는 PR #10의 `eval/question-set-v1.md` 50문항을 기준으로 보완한 질의 처리,
브라우저 프로필, 다섯 outcome 상태와 영어 면제 규칙 KG 보완을 설명한다. 평가 질문은
런타임 정답 테이블이 아니다. 질문 원문과 문항 번호는 평가 script·결과 문서에만 있고,
런타임은 Verified bundle에서 읽은 Course identity, Rule 설명과 중앙 semantic field
규칙을 사용한다.

현재 서비스 범위는 2026학년도 공통 교양과 컴퓨터공학과 교육과정이다. 사용자 진술은
학생별 임시 상태이며 공용 Neo4j 교육과정 그래프에 저장하지 않는다.

## 호출 흐름

```text
브라우저 질문 + versioned UserProfile
→ ProfileExtractor (USER_ASSERTION)
→ 필요한 사용자 정보와 현재 KG 범위 판단
→ LocalQueryPlanner (명시 slot은 결정론적, 나머지는 LLM 구조화 계획)
→ QueryPlan / SchemaSelector
→ candidate Cypher canonicalization
→ SafetyPipeline / Neo4j EXPLAIN / execute_read
→ ResultValidator
→ canonical ValidatedClaims
→ 결정론적 한국어 renderer / CitationRenderer
→ sealed ChatResponse 8필드
→ 별도 profile_update + outcome SSE envelope
```

프로필에 있는 공개 학수번호로 후속 질문의 과목 대상을 해소할 수 있지만, 학점 합계,
자유 메모와 학적 원문을 LLM 질문에 덧붙이지 않는다. 해소된 학수번호도 일반 QueryPlan,
canonical Cypher, 정적 검증, EXPLAIN과 Evidence 검증을 그대로 거친다.

## 다섯 outcome 상태

기존 `ChatResponse`는 다음 8필드를 그대로 유지한다.

```text
request_id, status, answer_text, citations,
used_fact_ids, used_evidence_ids, clarification, error_code
```

개인화 판단은 `type=outcome`, `version=1` SSE envelope로 별도 전달한다.

| outcome | 의미 | Grounding 정책 |
|---|---|---|
| `ANSWERED` | Verified KG로 사실 또는 계산을 확정 | 모든 KG 사실에 직접 VERIFIED Citation 필요 |
| `NEEDS_USER_INFO` | 개인 계산에 필요한 최소 사용자 정보 부족 | 이미 저장된 필드는 다시 묻지 않음 |
| `INSUFFICIENT_EVIDENCE` | 범위 안 질문이나 확정 근거 부족 | 사용자 정보를 더 요구해도 해결되지 않으면 되묻지 않음 |
| `OUT_OF_SCOPE` | 2026 공통 교양·CSE 범위 밖 | Cypher로 억지 조회하지 않음 |
| `ADVISORY` | 순서·우선순위·진로처럼 단일 정답 없음 | 검증 사실과 조건부 판단을 분리 |

`ANSWERED` 계산은 Python이 사용자 진술값과 조회된 Rule 값을 분리해 수행한다. null을
0으로 바꾸지 않고, 총학점만으로 지정 필수과목·영역 충족까지 확장하지 않는다.
`ADVISORY`의 순서는 교육과정에 적재된 학년·학기만 사용할 수 있으며 선수과목, 잔여석,
실제 개설과 개인 실력을 단정하지 않는다.

## 사용자 프로필

### version 1 필드

- 입학연도와 적용 교육과정 연도
- 학과, 현재 학년·학기
- 신입·편입·전과와 전공 구분
- 안정 학수번호·표시명을 가진 이수과목
- 총·교양·전공·일반선택 이수학점
- 영어 시험 종류와 점수·등급
- 진로 목표와 사용자의 참고 메모

값은 타입, 길이, 범위와 controlled vocabulary를 서버에서 다시 검증한다. 이수과목은
Verified bundle의 Course identity resolver가 찾은 안정 학수번호로만 추가한다.
`데이터베이스개론`/`데이타베이스개론` 같은 한 글자 표기 차이는 길이가 같은 한국어
과목명에서 유일한 stable identity가 나올 때만 허용한다. 일반 명사 `프로그래밍`을
`웹프로그래밍`으로 확장하는 삽입·삭제형 fuzzy match는 허용하지 않는다.

### 저장·우선순위·정정

- 브라우저 key: `evidence-chat-profile-v1`
- 저장소: 해당 브라우저의 `localStorage`
- 서버 영구 저장과 Neo4j Student 노드 생성: 없음
- 요청 시 현재 프로필을 보내고 서버가 검증한 `profile_update version=1`을 다시 저장
- 프로필 갱신은 부수효과이며 같은 턴의 학사 질문·조회·계산을 대체하지 않음
- 현재 메시지의 명시적 값이 기존 브라우저 값보다 우선
- `42가 아니라 45학점`처럼 명시된 정정은 새 값이 우선
- 같은 메시지에 서로 다른 값이 있고 정정 표현이 없으면 임의 선택하지 않고
  `NEEDS_USER_INFO`
- 동일 학수번호 과목은 중복 제거하며 UI에서 개별 삭제 또는 전체 초기화 가능
- 알 수 없는 schema version이나 손상 JSON은 브라우저에서 빈 프로필로 fallback하고,
  서버는 잘못된 payload를 거부

학과 표현과 목록 요청이 한 문장에 함께 있으면 학과는 프로필에 반영하되 목록 조회를
계속한다. 공개 답변에는 프로필 내부 계약 용어를 반복하지 않고 작은 비차단 상태만
표시한다. 학점 정정은 해당 category의 이전 값을 교체한 뒤 영역 합계·총 잔여학점 등
파생 계산을 모두 다시 수행한다.

동적 UI 문자열은 `textContent`와 안전한 DOM API만 사용한다. 프로필 원문은 runtime
trace와 서버 로그에 추가하지 않는다.

## 질문 해석의 일반 규칙

- `filters`는 질문에 이미 주어진 검색 조건, `requested_fields`는 사용자가 알고 싶은 값이다.
- 과목명·학수번호는 Verified bundle에서 생성한 resolver로 식별한다.
- 공통 교양 Course identity만 조회할 때는 CSE 관계를 억지로 요구하지 않는다.
- 여러 과목은 `course_codes` parameter 배열로 전달하고 질문 문자열에 Cypher를 조립하지 않는다.
- 학점·과목 수·학년·학기·이수구분의 역할은 Claim field별로 보존한다.
- 규칙 후보는 Verified Rule 설명의 semantic term과 controlled field를 사용하며 답 숫자를
  질문에서 Claim으로 복사하지 않는다.
- REVIEW_REQUIRED Rule만 관련된 질문은 `INSUFFICIENT_EVIDENCE`로 남긴다.
- 실시간 잔여석·시간표, 재수강·성적, 휴복학·전과 적용은 현재 근거가 없으므로 확정하지 않는다.

## 영어 면제 KG 보완

기존 raw 추출에는 영어 시험표의 `Condition` 9건이 있었지만, 개별 임계값을 확정
조회할 atomic Rule과 직접 Evidence가 Verified KG/Neo4j에 없었다. 원문이나 raw 파일을
바꾸지 않고 기존 Condition의 `subject_field`, `operator`, `value`, `unit`을 복사해 다음을
추가했다.

| 항목 | 추가량 |
|---|---:|
| VERIFIED atomic Rule | 9 |
| VERIFIED Evidence | 9 |
| 관계 (`HAS_RULE`, `SUPPORTED_BY`, `FROM_DOCUMENT`) | 27 |

Evidence는 기존 19쪽 발췌 PDF 1쪽의 표 셀 원문, 원본 PDF 33쪽, 인쇄 페이지 25쪽을
구분한다. 임계값은 migration script 안에서 새로 계산하지 않으며 기존 Condition에서
복사한다. parent 영어 면제 Rule은 각 atomic Rule의 직접 Evidence가 생긴 뒤 VERIFIED로
승격했다.

Verified bundle은 노드 1,518 → 1,536, 관계 3,260 → 3,287, Evidence 511 → 520으로
변경됐다. `neo4j_ingest sync`는 현재 DB의 모든 identity·relationship type이 새 bundle의
부분집합인지 먼저 검사한 뒤 `MERGE`만 수행한다. 삭제·초기화는 하지 않으며 두 번째
실행에서 생성량 0을 확인한다.

## 평가와 재현

PR #10 파일은 구현 브랜치에 복제하지 않는다. evaluator가 지정 Git ref의 원문을 읽어
실제 공개 `/api/ask` SSE endpoint에 각 질문을 빈 프로필과 독립 clarification 상태로
전송한다.

```bash
uv run python scripts/evaluate_question_set.py \
  --output /tmp/question-set-final.json

uv run python scripts/evaluate_personalization.py \
  --output /tmp/personalization-final.json
```

2026-08-28 실행의 질문별 기준선·최종 상태, 응답 요약, 프로필 사용 필드, Citation과
Cypher 실행 여부는 [50문항 결과표](evaluations/question-set-v1-2026-08-28.md)에 있다.
최종 실행은 50/50 기대 taxonomy 일치였고 `ANSWERED` 22건 모두 Citation이 있었다.
개인화 8개 시나리오는 정보 부족, 저장값 적용, JSON round-trip 복원, 채팅 추출,
후속 재사용, 명시적 정정, 초기화와 충돌 처리를 실제 SSE로 확인했다. 실제 브라우저의
새로고침 클릭 동작은 자동화하지 않았으며 JSON round-trip과 정적 UI 계약으로 확인했다.

## 제한사항

- 프로필은 단일 브라우저 저장소이므로 기기 간 동기화·로그인·복구를 제공하지 않는다.
- 사용자 진술의 진위를 검증하지 않고 `USER_ASSERTION` provenance로만 취급한다.
- 과목 이수여부·학점 합계만으로 성적, 재수강, 논문, 상담과 모든 졸업요건을 최종 판정하지 않는다.
- 현재 등록되지 않은 Rule/fact family와 REVIEW_REQUIRED 근거를 추측하지 않는다.
- 추천은 교육과정 편성 정보를 넘는 실시간 운영 정보나 선수관계를 생성하지 않는다.
- 같은 Python 프로세스에서 임의 코드 실행권을 얻은 공격자를 방어하는 보안 경계가 아니다.
