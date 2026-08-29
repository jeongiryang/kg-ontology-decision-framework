# 0033. 통합 후 필수 과목 대체 질문 grounding 회귀 보완

## 목적

PR #32~#35 병합 뒤 최종 `main`의 PR #10 50문항을 다시 실행하던 중, 지정 필수 과목의
일반 학점 대체 질문이 `ANSWERED`에서 `INSUFFICIENT_EVIDENCE`로 과도하게 강등되는
회귀를 수정한다.

## 재현

- 최종 `main`의 실제 `/api/ask` SSE에서 PR #10 20번 질문을 실행했다.
- sealed 응답에는 대학생활의설계를 필수로 이수한다는 VERIFIED Rule과 Citation 1건이
  있었고, 답변도 다른 과목 학점이 지정 필수 과목 이수를 대체한다고 확인되지 않았다고
  안전하게 설명했다.
- 그러나 최근 추가한 일반 course-substitution grounding guard가 pairwise 과목 대체와
  명시적 필수 과목 규칙을 구분하지 않아 최종 outcome만 `INSUFFICIENT_EVIDENCE`로
  바꿨다.

## 일반화된 수정

- 질문 문자열이나 과목명을 분기하지 않는다.
- 승인된 fact description에 `필수로 이수`라는 직접 규칙이 있고 질문이 과목 대체 판단을
  요구하면, 해당 필수성 설명을 `ANSWERED`로 유지한다.
- 답변은 대체 금지를 새로 만들지 않고, 다른 학점으로 필수 과목 이수를 대체할 수 있다는
  근거가 확인되지 않았다고만 말한다.
- 단순 CourseOffering 두 건만 있는 pairwise 대체 질문은 계속 직접 replacement Evidence를
  요구하고 `INSUFFICIENT_EVIDENCE`로 유지한다.
- 같은 재평가에서 총 부족학점을 계산했지만 어느 교양 영역인지 답하지 못한 결과가
  `ANSWERED`로 다시 승격되는 두 번째 회귀를 발견했다. Agent의 결정론적 학점 계산은
  기존 outcome에 Evidence limitation이 있으면 그 상태·문구를 지우지 못하도록 수정했다.

## 변경 파일

- `src/kg_builder/answer/personalized_service.py`
- `src/kg_builder/agent/orchestrator.py`
- `tests/test_agentic_graphrag.py`
- `tests/test_personalization.py`
- 이 로그와 정이량 로그 인덱스

## 검증

- 관련 agent·personalization·chat 테스트: 118 passed, 44 subtests passed
- 실제 PR #10 20번 `/api/ask`: `ANSWERED`, Citation 1건, 공개 오류 없음
- 실제 PR #10 25번 `/api/ask`: `INSUFFICIENT_EVIDENCE`, 확인된 학점 계산과 미확정 영역을
  구분하고 Citation 3건 유지
- `git diff --check`: 통과

최종 50+50+65 재평가와 전체 회귀·GitHub Actions 결과는 완료 뒤 이 로그에 갱신한다.

## 제한사항

- 명시적 필수 과목 규칙은 generic credit substitution을 부정하는 근거이지만, 두 과목
  사이의 개별 대체 인정 규칙을 생성하지 않는다.
- 현재 PDF·Verified KG에 직접 replacement relation이 없으면 pairwise equivalence는
  계속 확정하지 않는다.
