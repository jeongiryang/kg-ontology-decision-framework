# PR #38 traversal·Evidence UX 최신 main 통합

## 작업 목적

과거 `main`에서 작성된 PR #38의 승인 경로·PROFILE·VERIFIED Evidence 시각화를
PR #37의 연속 채팅, IndexedDB v3, 189과목 긴 목록, 1,440px 레이아웃 위에 통합한다.

## 시작 상태와 충돌

- 기준 `main`: PR #37 merge commit `a0baa1ad`
- PR #38 원본 Head: `c48c5167`
- GitHub 상태: Ready, 과거 check 성공, 최신 main과 content conflict
- 실제 content conflict는 `src/evidence_chat/static/app.js`의 그래프 탭 renderer였다.
- 자동 merge된 server, graph projection, safety pipeline, CSS와 테스트도 의미 단위로
  다시 읽어 최신 요청·보안 계약을 확인했다.

## 핵심 통합 결정

1. Query structure, PROFILE operator 관찰, 최종 Result traversal을 서로 다른 제목과
   설명으로 표시한다. PROFILE operator는 KG 노드로 만들지 않는다.
2. 최종 traversal envelope는 `RESULT_TRAVERSAL`로 구분한다.
3. ontology allowlist 밖 hop, 관계 방향이 명세와 다른 hop, 검증된 provenance pair와
   정확히 일치하지 않는 row는 projection 전체를 거부한다.
4. Neo4j가 제공하지 않는 operator별 시간 배분값 `share_ms`는 만들거나 표시하지 않는다.
   실제 전체 query elapsed와 PROFILE rows·DB hits만 유지한다.
5. PR #38의 개별 Evidence 카드와 원문/PDF 분리를 유지하되, turn의 `근거 N개`
   disclosure를 열 때만 DOM을 만든다. 페이지 이미지와 강조는 그 안에서 한 번 더
   지연 렌더링한다.
6. 100개가 넘는 traversal은 PR #37의 영역 요약을 먼저 보이고 전체 그래프는 사용자가
   요청할 때만 렌더링한다.

## 추가 보완

- 공개 graph의 한국어 이름이 명세에 없을 때 내부 영어 identifier 대신 안전한 일반
  한국어 표현을 사용한다.
- PROFILE rows·DB hits의 bool·음수 값을 0으로 정제한다.
- PR #37 평가 문서의 `50 + 50 + 65` 총합을 115가 아닌 165개 평가 항목으로 바로잡았다.
- 189 Citation과 전체 inspection SSE가 1 MiB 미만인지 실패 폐쇄 회귀 테스트를 추가했다.

## 변경 파일

- `docs/evidence-chat.md`
- `docs/evaluations/pr37-long-list-browser-regression.md`
- `docs/ai-simulation-logs/jeong-iryang/0035-pr37-long-list-browser-regression.md`
- `docs/ai-simulation-logs/jeong-iryang/0036-pr38-traversal-ux-integration.md`
- `docs/ai-simulation-logs/jeong-iryang/README.md`
- `src/evidence_chat/graph_projection.py`
- `src/evidence_chat/server.py`
- `src/evidence_chat/static/app.css`
- `src/evidence_chat/static/app.js`
- `src/kg_builder/query/safety_pipeline.py`
- `tests/test_evidence_chat.py`

## 검증

- `uv sync --locked`, `uv lock --check`, `git diff --check`: 통과
- `uv run python -m unittest discover`: 427 PASS, 6 skip
- `uv run pytest -q`: 421 PASS, 6 skip, 423 subtests PASS
- schema exporter stale check와 영어 면제 migration check: 통과
- Neo4j bundle validate/check-connection/verify: 1,536 nodes, 3,287 relationships,
  520 Evidence로 통과
- opt-in Neo4j 읽기 통합: 3 PASS, 6 subtests PASS
- Markdown 상대 링크: 누락 0건
- 실제 SSE 165개 평가 항목: 원본 50/50, 미공개 단일 50/50, 다중 턴
  65/65의 기존 상태 시퀀스 유지. `ANSWERED` Citation은 각각 22/22, 30/30,
  43/43이고 공개 오류·`SAFE_FAILURE`는 0건이었다.
- 실제 189과목 응답: 17.277초, 727,913 bytes, Citation 189개로 1 MiB 미만.
  승인 질의 구조는 5 nodes/4 edges, 최종 result traversal은 572 nodes/756 edges,
  별도 provenance projection은 378 nodes/189 edges였다.
- 실제 Chromium: 390×844 reduced-motion과 768×1024, 1280×720, 1440×900,
  1920×1080에서 가로 overflow·console error 0건. 연속 2턴과 reload 복원,
  한국어 graph label, 10단계 처리 과정/육하원칙, 13단계 PROFILE, Evidence/PDF
  modal을 확인했다. 189 Evidence card는 disclosure 전 0개, 연 뒤 189개였고,
  572-node result graph도 명시적인 `전체 노드 표시` 전에는 만들지 않았다.

- 통합 Head `5b736218`의 GitHub Actions `unit-tests`: PASS
- 이슈 #39의 165개 총합 표기와 SSE payload 상한 회귀를 처리하고 결과를 남긴 뒤
  이슈를 닫았다.

병합 후 `main` 핵심 재검증 결과는 최종 보고에 기록한다. 실행하지 않은 검증은 성공으로
기록하지 않는다.

## 보호 대상

Raw·Verified KG, 원본 PDF, `ontology/ontology_spec.json`, `.env`, 모델 파일은 수정하지
않는다. 내부 Neo4j Label·Relationship 이름도 변경하지 않는다.
