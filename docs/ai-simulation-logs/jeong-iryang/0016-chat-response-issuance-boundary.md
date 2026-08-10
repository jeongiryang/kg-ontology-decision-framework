# 0016. ChatResponse 승인 발급 경계와 오류 코드 정제

## 기본 정보

| 항목 | 내용 |
|---|---|
| 작성일 | 2026-08-11 |
| 담당자 | 정이량 |
| 사용 에이전트 | Codex |
| 작업 브랜치 | `feat/jeongiryang/evidence-answer-renderer` |
| 관련 커밋 | 이번 작업 커밋 |
| 관련 Issue/PR | Draft PR #27 |
| 작업 상태 | 완료 |

## 1. 작업 목적

PR #27 최종 독립 검토에서 확인된 공개 `ChatResponse` 위조 BLOCKER와 알 수
없는 `error_code` 노출 MAJOR를 수정한다. 기존 ClaimBuilder, ClaimValidator,
ValidatedClaims와 결정론적 한국어 renderer 구조는 유지한다.

## 2. 수정 전 재현

- 정상 `MAJOR_ELECTIVE` Claim과 VERIFIED Citation에 “자료구조는 전공필수”라는
  임의 본문을 넣은 `ChatResponse`가 `ANSWERABLE` wire JSON으로 직렬화됐다.
- 합성 내부 예외 문자열을 `safe_failure()`에 전달하면 사용자 문구는 일반화됐지만
  입력 문자열이 `error_code` 필드에 그대로 남았다.
- 작업 전 Neo4j 기준은 노드 1,518개, 관계 3,260개, Evidence 511개였다.

## 3. 수행한 작업

1. `ChatResponse`를 `init=False` immutable DTO로 바꾸고 일반 생성자를 차단했다.
2. `CitationRenderer`가 승인된 `RenderedAnswer`와 검증 Citation을 내부 승인 payload로
   묶도록 했다.
3. ANSWERABLE은 이 payload를 받는 `from_approved_answer()`만 발급하도록 했다.
4. 호출자가 answer text, Claim, Citation, Fact/Evidence ID를 개별 전달하는 API를
   제거했다.
5. clarification, 범위 밖, 미지원, unresolved, not found와 safe failure를 상태별
   factory로 분리했다.
6. `ChatErrorCode` enum을 추가하고 알려진 문자열만 보존하도록 했다.
7. 알 수 없는 문자열, 빈 값과 예외 메시지는 `UNKNOWN_SAFE_FAILURE`로 치환했다.
8. `to_dict()`가 enum의 안전한 value만 출력하고 기존 8개 wire 필드를 유지하게 했다.
9. 동일 내용의 immutable ValidatedClaims 복사는 허용하고 변경된 복사만 거부한다는
   정책을 문서와 테스트에 일치시켰다.

## 4. 변경 파일

| 경로 | 내용 |
|---|---|
| `src/kg_builder/answer/contracts.py` | sealed DTO, 상태 factory, ChatErrorCode 정제 |
| `src/kg_builder/answer/renderer.py` | 내부 승인 Answerable payload 발급 |
| `src/kg_builder/answer/service.py` | 상태별 factory만 사용하는 공식 흐름 |
| `src/kg_builder/answer/__init__.py` | 안전 오류 코드 계약 export |
| `tests/test_answer_renderer.py` | 위조 DTO와 오류 문자열 회귀 테스트 |
| `docs/evidence-answer-renderer.md` | 발급 경계·오류 코드·복사 정책·프론트 계약 |
| `README.md` | ChatResponse 사용 원칙 요약 |
| `docs/ai-simulation-logs/jeong-iryang/README.md` | 로그 인덱스 갱신 |

## 5. 핵심 결정

- `ChatResponse`는 프론트와 CLI가 읽고 직렬화하는 DTO이지 조립 API가 아니다.
- ANSWERABLE 본문과 Grounding은 같은 승인 payload에서 한 번에 가져온다.
- PR #14를 포함한 후속 UI는 `CurriculumChatService`의 반환값만 사용한다.
- unknown 오류 입력을 그대로 노출하지 않고 안정적인 enum 값으로 축약한다.
- Python private factory 탈취나 monkey patching은 같은 프로세스의 임의 코드 실행
  문제로서 기존에 문서화한 신뢰 경계 밖이다.

## 6. 검증

| 검증 | 결과 |
|---|---|
| 수정 전 합성 wire 재현 | 위조 ANSWERABLE과 error 문자열 노출 재현 |
| 수정 후 독립 위조 실행 | 직접 생성·`dataclasses.replace` 모두 `TypeError` |
| unknown 오류 정제 | `UNKNOWN_SAFE_FAILURE`, 입력 문자열 wire 미포함 |
| `uv sync --locked` | PASS |
| `uv lock --check` | PASS |
| unittest | 108개 중 103 PASS, 환경 통합 5 skip |
| pytest | 103 PASS, 5 skip, 113 subtests PASS |
| Python compile | PASS |
| schema stale | PASS |
| Neo4j 통합 | 106 PASS, 2 skip, 119 subtests PASS |
| 실제 Ollama 답변 6문항 | 1 test / 6 subtests PASS |
| whitespace | `git diff --check` PASS |

실제 6문항은 교양 최소 34학점, 균형교양 4개 영역·영역별 1과목·12학점,
편입생 교양 의무 없음, 자료구조 2학년 1학기, 전공필수 9과목·21학점,
자료구조 전공선택을 반환했다. 총 실행시간은 101.34초였고 최종 답변 LLM 호출은
0회였다.

## 7. 데이터 불변성

- Neo4j 노드 1,518개, 관계 3,260개, Evidence 511개를 유지했다.
- Raw·Verified KG, `ontology/ontology_spec.json`, `.env`, `AGENTS.md`, PDF와 모델
  파일은 수정하지 않았다.

## 8. 남은 제한사항

- 완전히 동일한 immutable 승인값 복사는 허용한다. 내용이나 digest가 달라진 복사는
  승인되지 않는다.
- private factory를 악의적으로 호출하거나 프로세스를 monkey patch하는 공격은 이번
  PoC의 Python 신뢰 경계 밖이다.
- 실제 연구실 vLLM은 이번 수정에서 실행하지 않았다.

## 9. 다음 작업

- 최신 Head에서 ChatResponse 직접 생성과 unknown 오류 코드 정제만 집중 재검토한다.
- 통과 후 사용자가 Draft 해제와 병합 여부를 결정한다.
- 병합 뒤 PR #14 프론트엔드는 응답 DTO를 직접 만들지 않고 Chat Service 결과를
  렌더링한다.
