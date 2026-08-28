/* Starlette SSE client for approved ChatResponse values. */

"use strict";

const $ = (id) => document.getElementById(id);
const SCREEN_ORDER = ["ask", "progress", "answer"];
const el = {
  status: $("status"),
  stages: document.querySelectorAll(".stages li"),
  screens: {
    ask: $("screen-ask"),
    progress: $("screen-progress"),
    answer: $("screen-answer"),
  },
  form: $("ask-form"),
  question: $("question"),
  submit: $("submit"),
  examples: $("examples"),
  askNotice: $("ask-notice"),
  spinner: $("spinner"),
  progressTitle: $("progress-title"),
  progressNow: $("progress-now"),
  progressError: $("progress-error"),
  progressBack: $("progress-back"),
  elapsed: $("elapsed"),
  asked: $("asked"),
  progressSteps: $("progress-steps"),
  progressExploration: $("progress-exploration"),
  answerQuestion: $("answer-question"),
  answerBadge: $("answer-badge"),
  answerTitle: $("answer-title"),
  clarification: $("clarification"),
  choices: $("choices"),
  choiceList: $("choice-list"),
  trail: $("answer-trail"),
  scopeNotice: $("scope-notice"),
  debugMeta: $("debug-meta"),
  timelineSection: $("timeline-section"),
  answerProgressSteps: $("answer-progress-steps"),
  answerExploration: $("answer-exploration"),
  evidenceSection: $("evidence-section"),
  evidenceSummary: $("evidence-summary"),
  evidencePages: $("evidence-pages"),
  pdfNotice: $("pdf-notice"),
  answerAgain: $("answer-again"),
  pdfModal: $("pdf-modal"),
  pdfModalTitle: $("pdf-modal-title"),
  pdfModalMeta: $("pdf-modal-meta"),
  pdfModalClose: $("pdf-modal-close"),
  pdfModalNotice: $("pdf-modal-notice"),
  pdfModalCanvas: $("pdf-modal-canvas"),
  pdfModalSource: $("pdf-modal-source"),
  pdfPrev: $("pdf-prev"),
  pdfNext: $("pdf-next"),
  pdfZoomIn: $("pdf-zoom-in"),
  pdfZoomOut: $("pdf-zoom-out"),
  pdfZoomLabel: $("pdf-zoom-label"),
};

let pdfAvailable = false;
let inFlight = false;
let activeController = null;
let elapsedTimer = null;
let clientTimeoutMs = 180000;
let pdfPageCount = 0;
let modalState = null;
let modalZoom = 1;
let queryDetailsEnabled = false;
let timelineEvents = [];
let inspectionUpdates = new Map();
// 결과 화면 탐색 패널의 펼침 상태. 사용자가 펼쳐 두면 재렌더에도 유지한다.
let explorationExpanded = false;
let expandedStages = new Set();
let graphScales = new Map();
let lastResult = null;
let clarificationPresentation = null;
const graphResizeObserver = typeof window.ResizeObserver === "function"
  ? new window.ResizeObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.target.isConnected) {
          graphResizeObserver.unobserve(entry.target);
        } else if (typeof entry.target.fitGraph === "function") {
          entry.target.fitGraph();
        }
      });
    })
  : null;

const span = (className, text) => {
  const node = document.createElement("span");
  if (className) node.className = className;
  node.textContent = text;
  return node;
};

// 되묻기로 확정된 값. 서버는 대화 상태를 들지 않으므로 브라우저가 들고 매 요청에
// 함께 보낸다. 값이 실제로 제시된 선택지였는지는 서버가 다시 만들어 대조한다.
//
// `trail` 은 화면에만 쓰는 기록이다. 서버로 보내지 않으며 조회에도 관여하지 않는다.
// 무엇을 물었고 무엇을 골라 여기까지 왔는지 사용자가 되짚을 수 있게 하는 용도다.
const CLARIFY_KEY = "evidence-chat-clarify";

function emptyClarify(question = "") {
  return { question, resolved: {}, trail: [] };
}

function loadClarify() {
  try {
    const saved = JSON.parse(sessionStorage.getItem(CLARIFY_KEY) || "null");
    if (saved && typeof saved.question === "string" && saved.resolved) {
      return { ...saved, trail: Array.isArray(saved.trail) ? saved.trail : [] };
    }
  } catch (error) {
    /* 저장된 값이 깨졌으면 그냥 새로 시작한다. */
  }
  return emptyClarify();
}

function saveClarify(state) {
  try {
    sessionStorage.setItem(CLARIFY_KEY, JSON.stringify(state));
  } catch (error) {
    /* 저장에 실패해도 이번 요청은 그대로 진행한다. */
  }
}

function clearClarify() {
  clarify = emptyClarify();
  try {
    sessionStorage.removeItem(CLARIFY_KEY);
  } catch (error) {
    /* 지우지 못해도 다음 질문에서 덮어쓴다. */
  }
}

let clarify = loadClarify();

function showScreen(name) {
  Object.entries(el.screens).forEach(([key, node]) =>
    node.classList.toggle("is-active", key === name)
  );
  const current = SCREEN_ORDER.indexOf(name);
  el.stages.forEach((item) => {
    const index = SCREEN_ORDER.indexOf(item.dataset.stage);
    item.classList.toggle("is-current", index === current);
    item.classList.toggle("is-done", index < current);
  });
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function showNotice(node, message, isError) {
  node.textContent = message;
  node.classList.toggle("error", Boolean(isError));
  node.hidden = false;
}

async function loadHealth() {
  try {
    const response = await fetch("/api/health", { cache: "no-store" });
    const data = await response.json();
    pdfAvailable = Boolean(data.pdf_mounted);
    queryDetailsEnabled = Boolean(data.show_query_details);
    clientTimeoutMs = Math.max(60000, Number(data.client_timeout_seconds || 180) * 1000);
    if (Number.isInteger(data.max_question_length)) {
      el.question.maxLength = data.max_question_length;
    }
    const dot = el.status.querySelector(".dot");
    const text = el.status.querySelector(".status-text");
    if (!data.service_ready) {
      dot.dataset.state = "error";
      text.textContent = "질의 서비스 준비 안 됨";
      showNotice(el.askNotice, data.error || "질의 서비스를 사용할 수 없습니다.", true);
    } else if (!pdfAvailable) {
      dot.dataset.state = "warn";
      text.textContent = "질의 서비스 준비됨 · PDF 미탑재";
      showNotice(el.askNotice, "발췌 PDF가 없어 근거 원문과 페이지 번호만 표시합니다.", false);
    } else {
      dot.dataset.state = "ok";
      text.textContent = "질의 서비스 준비됨 · PDF 탑재됨";
    }
    renderExamples(data.examples || []);
  } catch (_) {
    el.status.querySelector(".dot").dataset.state = "error";
    el.status.querySelector(".status-text").textContent = "서버 상태 확인 실패";
  }
}

function renderExamples(examples) {
  el.examples.replaceChildren();
  examples.forEach((text) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = text;
    button.addEventListener("click", () => {
      if (inFlight) return;
      el.question.value = text;
      autoGrow();
      el.form.requestSubmit();
    });
    el.examples.append(button);
  });
}

function autoGrow() {
  el.question.style.height = "auto";
  const style = window.getComputedStyle(el.question);
  const lineHeight = Number.parseFloat(style.lineHeight) || 26;
  const verticalPadding =
    (Number.parseFloat(style.paddingTop) || 0) +
    (Number.parseFloat(style.paddingBottom) || 0);
  const maxHeight = lineHeight * 5 + verticalPadding;
  const height = Math.min(el.question.scrollHeight, maxHeight);
  el.question.style.height = `${height}px`;
  el.question.style.overflowY = el.question.scrollHeight > maxHeight ? "auto" : "hidden";
}

el.question.addEventListener("input", autoGrow);
el.question.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    if (!inFlight) el.form.requestSubmit();
  }
});
el.form.addEventListener("submit", (event) => {
  event.preventDefault();
  const question = el.question.value.trim();
  if (question && !inFlight) {
    // 새로 입력한 질문은 앞서 채운 조건과 무관하다. 확정값을 버리고 시작한다.
    clearClarify();
    clarify = emptyClarify(question);
    saveClarify(clarify);
    ask(question);
  }
});
el.progressBack.addEventListener("click", () => {
  if (activeController) {
    activeController.abort("user_cancelled");
  } else {
    showScreen("ask");
  }
});
el.answerAgain.addEventListener("click", () => {
  el.question.value = "";
  autoGrow();
  showScreen("ask");
  el.question.focus();
});

function startElapsed() {
  const started = performance.now();
  el.elapsed.textContent = "0초";
  elapsedTimer = window.setInterval(() => {
    el.elapsed.textContent = `${Math.floor((performance.now() - started) / 1000)}초`;
  }, 250);
}

function stopElapsed() {
  if (elapsedTimer !== null) window.clearInterval(elapsedTimer);
  elapsedTimer = null;
}

async function ask(question) {
  inFlight = true;
  el.submit.disabled = true;
  el.asked.textContent = question;
  el.answerQuestion.textContent = question;
  el.progressError.hidden = true;
  el.spinner.classList.remove("is-done");
  el.progressTitle.textContent = "답변을 확인하고 있습니다";
  el.progressNow.textContent = "질문 전송됨";
  timelineEvents = [];
  inspectionUpdates = new Map();
  expandedStages = new Set();
  graphScales = new Map();
  // 새 질문은 처음부터 다시 재생한다.
  autoplayedGraphs.clear();
  explorationExpanded = false;
  lastResult = null;
  clarificationPresentation = null;
  renderTimelines();
  el.timelineSection.hidden = true;
  el.progressBack.textContent = "요청 취소";
  showScreen("progress");
  startElapsed();

  activeController = new AbortController();
  const timeout = window.setTimeout(() => activeController.abort("timeout"), clientTimeoutMs);
  let result = null;
  let failed = false;

  try {
    const response = await fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(
        Object.keys(clarify.resolved).length
          ? { question, resolved: clarify.resolved }
          : { question }
      ),
      signal: activeController.signal,
    });
    if (!response.ok || !response.body) {
      const detail = await response.json().catch(() => ({}));
      throw new Error(detail.error || `서버 오류 (HTTP ${response.status})`);
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let done = false;
    while (!done) {
      const chunk = await reader.read();
      done = chunk.done;
      buffer += decoder.decode(chunk.value || new Uint8Array(), { stream: !done });
      const frames = buffer.split("\n\n");
      buffer = frames.pop() || "";
      for (const frame of frames) {
        const line = frame.split("\n").find((row) => row.startsWith("data: "));
        if (!line) continue;
        const payload = JSON.parse(line.slice(6));
        if (payload.type === "progress") {
          el.progressNow.textContent = payload.message;
          renderProgress(payload);
        } else if (payload.type === "clarification_options") {
          clarificationPresentation = payload;
        } else if (payload.type === "inspection_update") {
          renderInspectionUpdate(payload);
        } else if (payload.type === "result") {
          result = payload;
          lastResult = payload;
        } else if (payload.type === "error") {
          failed = true;
          markTimelineFailed(payload.message, payload.error_code || "CHAT_REQUEST_FAILED");
          showNotice(el.progressError, payload.message, true);
        }
      }
    }
    if (!result && !failed) throw new Error("응답 결과가 없습니다.");
  } catch (error) {
    failed = true;
    // 스트림 처리 중 예외를 삼키면 "연결이 종료됐다"로만 보여 원인을 찾을 수 없다.
    console.error("[ask] 스트림 처리 실패", error);
    const timedOut = activeController && activeController.signal.reason === "timeout";
    markTimelineCancelled(
      timedOut ? "응답 대기 시간이 초과되었습니다." : "요청이 취소되었습니다."
    );
    showNotice(
      el.progressError,
      timedOut
        ? "응답 대기 시간이 초과되었습니다. 서버의 모델 요청은 잠시 더 실행될 수 있습니다."
        : "요청이 취소되었거나 연결이 종료되었습니다.",
      true
    );
  } finally {
    window.clearTimeout(timeout);
    stopElapsed();
    activeController = null;
    inFlight = false;
    el.submit.disabled = false;
    el.spinner.classList.add("is-done");
    el.progressTitle.textContent = failed ? "처리를 마치지 못했습니다" : "답변 완료";
    el.progressBack.textContent = "질문 다시 입력";
  }

  if (result) {
    // 렌더링에서 예외가 나도 화면은 넘어가야 한다. 종전에는 여기서 던지면
    // 사용자가 '답변 완료' 상태의 진행 화면에 갇혔고, 무엇이 잘못됐는지도
    // 알 수 없었다.
    try {
      renderAnswer(result);
      renderTimelines();
      renderExplorationPanels();
    } catch (error) {
      showNotice(
        el.progressError,
        "답변을 화면에 그리지 못했습니다. 새로고침 후 다시 시도해 주세요.",
        true
      );
    }
    showScreen("answer");
  }
}

function renderProgress(payload) {
  const attempt = Number.isInteger(payload.attempt) ? payload.attempt : 0;
  let entry = [...timelineEvents]
    .reverse()
    .find(
      (item) =>
        item.phase === payload.phase &&
        item.attempt === attempt &&
        item.state === "STARTED"
    );
  if (payload.state === "STARTED") {
    if (entry) {
      entry.message = payload.message;
    } else {
      entry = {
        phase: payload.phase,
        attempt,
        state: "STARTED",
        message: payload.message,
        elapsed_ms: null,
        error_code: null,
        started_at_ms: performance.now(),
      };
      timelineEvents.push(entry);
    }
  } else {
    if (!entry) {
      entry = {
        phase: payload.phase,
        attempt,
        state: payload.state,
        message: payload.message,
        elapsed_ms: null,
        error_code: null,
        started_at_ms: null,
      };
      timelineEvents.push(entry);
    }
    entry.state = payload.state;
    entry.message = payload.message;
    entry.elapsed_ms = Number.isFinite(payload.elapsed_ms) ? payload.elapsed_ms : null;
    entry.error_code = payload.error_code || null;
  }
  renderTimelines();
}

function renderTimelines() {
  renderTimelineInto(el.progressSteps);
  renderTimelineInto(el.answerProgressSteps);
  el.timelineSection.hidden = timelineEvents.length === 0;
}

function renderTimelineInto(container) {
  container.replaceChildren();
  timelineEvents.forEach((event) => {
    const item = document.createElement("li");
    item.className = "step";
    item.dataset.phase = event.phase;
    item.dataset.attempt = String(event.attempt);
    const stateClass = {
      STARTED: "is-running",
      COMPLETED: "is-done",
      FAILED: "is-failed",
      CANCELLED: "is-cancelled",
    }[event.state];
    if (stateClass) item.classList.add(stateClass);
    const icon = { COMPLETED: "✓", FAILED: "!", CANCELLED: "×" }[event.state] || "";
    const labelText = event.error_code
      ? `${event.message} — ${event.error_code}`
      : event.message;
    const key = `${event.phase}:${event.attempt}`;
    const disclosureId = `${container.id}-${event.phase.toLowerCase()}-${event.attempt}`;
    const hasDetails = stageHasDetails(event);
    const expanded = hasDetails && expandedStages.has(key);
    const row = document.createElement(hasDetails ? "button" : "div");
    if (hasDetails) row.type = "button";
    row.className = `step-toggle${hasDetails ? "" : " is-static"}`;
    if (hasDetails) {
      row.setAttribute("aria-expanded", String(expanded));
      row.setAttribute("aria-controls", disclosureId);
    }
    row.append(span("step-icon", icon), span("step-label", labelText));
    row.append(
      span(
        "step-time",
        event.state !== "STARTED" && Number.isFinite(event.elapsed_ms)
          ? `${event.elapsed_ms}ms`
          : ""
      ),
      span("step-chevron", hasDetails ? "⌄" : "")
    );
    item.append(row);
    if (hasDetails) {
      const disclosure = document.createElement("div");
      disclosure.id = disclosureId;
      disclosure.className = "step-disclosure";
      disclosure.hidden = !expanded;
      renderStageDetail(
        disclosure,
        event,
        container === el.answerProgressSteps
      );
      row.addEventListener("click", () => {
        if (expandedStages.has(key)) expandedStages.delete(key);
        else expandedStages.add(key);
        renderTimelines();
      });
      item.append(disclosure);
    }
    container.append(item);
  });
}

function inspectionKey(stage, attempt = 0) {
  return `${stage}:${Number.isInteger(attempt) ? attempt : 0}`;
}

function latestInspection(stage) {
  return [...inspectionUpdates.values()].reverse().find((item) => item.stage === stage) || null;
}

function stageInspection(event) {
  if (event.attempt > 0) {
    return inspectionUpdates.get(inspectionKey(event.phase, event.attempt)) || null;
  }
  return inspectionUpdates.get(inspectionKey(event.phase, 0)) || null;
}

function stageHasDetails(event) {
  const inspection = stageInspection(event);
  if (!inspection || !inspection.summary || inspection.status === "FAILED") return false;
  const summary = inspection.summary;
  const allowed = {
    QUESTION_ANALYSIS: Boolean(summary.status || summary.query_plan),
    SCHEMA_SELECTION: Array.isArray(summary.labels) && summary.labels.length > 0,
    CYPHER_GENERATION: Boolean(summary.message),
    STATIC_VALIDATION: summary.read_only_syntax_verified === true,
    NEO4J_EXPLAIN: typeof summary.approved_cypher === "string",
    GRAPH_EXECUTION: Number.isInteger(summary.row_count),
    RESULT_VALIDATION: summary.direct_provenance_verified === true,
    CLAIM_BUILDING: Number.isInteger(summary.claim_count),
    ANSWER_RENDERING: Number.isInteger(summary.citation_count),
    COMPLETED: Boolean(summary.final_status),
  };
  return Boolean(allowed[event.phase]);
}

function addDetailFacts(container, values) {
  const list = document.createElement("dl");
  list.className = "stage-facts";
  Object.entries(values).forEach(([label, value]) => {
    if (value === null || value === undefined || value === "") return;
    const term = document.createElement("dt");
    term.textContent = label;
    const detail = document.createElement("dd");
    detail.textContent = typeof value === "boolean" ? (value ? "예" : "아니오") : String(value);
    list.append(term, detail);
  });
  if (list.childElementCount) container.append(list);
}

// 각 단계를 육하원칙으로 요약한다. 값은 모두 그 단계가 실제로 보고한 것에서만 온다.
// 보고되지 않은 항목은 만들지 않고 빈 칸으로 둔다.
const STAGE_5W1H = {
  QUESTION_ANALYSIS: (s) => ({
    누가: "로컬 계획 모델",
    무엇을: "질문에서 조회 계획을 세움",
    어떻게: s.query_plan && s.query_plan.selection_mode
      ? `선택 모드 ${s.query_plan.selection_mode}`
      : "선택 모드 미확정",
    왜: s.status === "READY" ? "조회에 필요한 범위가 모두 정해짐" : "범위가 덜 정해져 되물음",
    어디서: "적재된 사실 색인",
  }),
  SCHEMA_SELECTION: (s) => ({
    누가: "스키마 선택기",
    무엇을: `후보 라벨 ${s.node_label_count ?? 0}개 · 관계 ${s.relationship_count ?? 0}개를 추림`,
    어떻게: "계획의 필드와 Evidence 경로에 닿는 구조만 남김",
    왜: "모델이 온톨로지 밖 라벨을 쓰지 못하게 하기 위해",
    어디서: "ontology_spec.json",
  }),
  CYPHER_GENERATION: (s) => ({
    누가: "로컬 생성 모델",
    무엇을: "후보 Cypher 작성",
    어떻게: s.retry ? `재시도 ${s.candidate_attempt}회차` : "첫 시도",
    왜: "계획을 그래프 질의로 옮기기 위해",
    어디서: "후보 스키마 안에서만",
  }),
  STATIC_VALIDATION: (s) => ({
    누가: "Cypher 검증기",
    무엇을: "읽기 전용·온톨로지·파라미터 바인딩 검사",
    어떻게: `LIMIT ${s.limit ?? "-"} 확인, 주석 제거 정규화`,
    왜: "쓰기·미선언 라벨·값 삽입을 원천 차단",
    어디서: "DB 접속 이전 정적 단계",
  }),
  NEO4J_EXPLAIN: (s) => ({
    누가: "Neo4j 실행계획기",
    무엇을: "실행 없이 계획만 확인",
    어떻게: `연산자 ${(s.operators || []).join(", ") || "-"}`,
    왜: "전체 스캔·카테시안곱 같은 위험한 계획을 미리 거름",
    어디서: "읽기 전용 계정",
  }),
  GRAPH_EXECUTION: (s) => ({
    누가: "읽기 전용 실행기",
    무엇을: `${s.row_count ?? 0}행 조회`,
    어떻게: (s.traversal_steps || []).length
      ? `${s.traversal_steps.length}단계 탐색 · ${s.traversal_steps.reduce((a, t) => a + (t.db_hits || 0), 0)}회 DB 접근`
      : "PROFILE 미보고",
    왜: "승인된 경로로만 사실을 가져오기 위해",
    언제: s.query_elapsed_ms != null ? `${s.query_elapsed_ms}ms 소요` : "",
  }),
  RESULT_VALIDATION: (s) => ({
    누가: "결과 검증기",
    무엇을: `검증된 사실 ${s.fact_count ?? 0}건 · 근거 ${s.verified_evidence_count ?? 0}건 확인`,
    어떻게: "행마다 VERIFIED 상태와 직접 provenance 재검사",
    왜: "검증되지 않은 값이 답에 들어가지 못하게",
    어디서: "조회된 행 위에서",
  }),
  CLAIM_BUILDING: (s) => ({
    누가: "주장 검증기",
    무엇을: `주장 ${s.claim_count ?? 0}건 구성`,
    어떻게: `유형 ${(s.claim_types || []).join(", ") || "-"}`,
    왜: "문장으로 옮기기 전에 근거와 값을 묶어 두기 위해",
    어디서: "승인된 행에서만",
  }),
  ANSWER_RENDERING: (s) => ({
    누가: "결정론적 한국어 렌더러",
    무엇을: `인용 ${s.citation_count ?? 0}건과 함께 문장 생성`,
    어떻게: `최종 답변 LLM 호출 ${s.final_answer_llm_calls ?? 0}회`,
    왜: "모델이 값을 바꿔 쓰지 못하게 하기 위해",
    어디서: "검증된 Claim 위에서",
  }),
  COMPLETED: (s) => ({
    무엇을: `최종 상태 ${s.final_status ?? "-"}`,
    언제: s.total_elapsed_ms != null ? `전체 ${s.total_elapsed_ms}ms` : "",
    어떻게: `재시도 ${s.retry_count ?? 0}회`,
    왜: "모든 관문을 통과함",
  }),
};

function addFiveWOneH(container, phase, summary) {
  const build = STAGE_5W1H[phase];
  if (!build || !summary) return;
  let facts;
  try {
    facts = build(summary);
  } catch (error) {
    return;
  }
  const section = document.createElement("section");
  section.className = "stage-5w1h";
  const heading = document.createElement("h5");
  heading.textContent = "이 단계를 육하원칙으로";
  section.append(heading);
  const list = document.createElement("dl");
  list.className = "stage-facts";
  Object.entries(facts).forEach(([label, value]) => {
    if (!value) return;
    const term = document.createElement("dt");
    term.textContent = label;
    const detail = document.createElement("dd");
    detail.textContent = String(value);
    list.append(term, detail);
  });
  if (list.childElementCount) {
    section.append(list);
    container.append(section);
  }
}

function renderStageDetail(container, event, allowExplorationLinks) {
  const inspection = stageInspection(event);
  const summary = inspection ? inspection.summary : null;
  if (!summary) return;
  addFiveWOneH(container, event.phase, summary);

  if (event.phase === "QUESTION_ANALYSIS") {
    addDetailFacts(container, {
      "계획 상태": summary.status,
      "학년도": summary.query_plan && summary.query_plan.filters
        ? summary.query_plan.filters.academic_year
        : null,
      "학과": summary.query_plan && summary.query_plan.filters
        ? summary.query_plan.filters.department_id
        : null,
      "요청 필드": summary.query_plan && Array.isArray(summary.query_plan.requested_fields)
        ? summary.query_plan.requested_fields.join(", ")
        : null,
      "누락 정보": Array.isArray(summary.missing) ? summary.missing.join(", ") : null,
      "데이터 기반 되묻기": summary.clarification_available,
    });
    addInspectionItem(container, "정제된 계획", summary.query_plan);
  } else if (event.phase === "SCHEMA_SELECTION") {
    addDetailFacts(container, {
      "선택 node label 수": summary.node_label_count,
      "선택 relationship 수": summary.relationship_count,
      "선택 이유": "검증된 QueryPlan의 필드와 Evidence 경로에 필요한 구조입니다.",
    });
    addInspectionItem(container, "선택 node label", summary.labels || []);
    addInspectionItem(container, "선택 relationship type", summary.relationships || []);
    if (allowExplorationLinks) {
      addExplorationLink(container, "탐색 그래프 보기", "graph");
    }
  } else if (event.phase === "CYPHER_GENERATION") {
    addDetailFacts(container, {
      "후보 attempt": summary.candidate_attempt,
      "재시도": summary.retry,
      "상태": summary.message,
    });
  } else if (event.phase === "STATIC_VALIDATION") {
    addDetailFacts(container, {
      "읽기 전용 문법": summary.read_only_syntax_verified,
      "온톨로지 명세": summary.ontology_schema_verified,
      "파라미터 바인딩": summary.parameter_binding_verified,
      "근거 직접 경로": summary.direct_evidence_path_verified,
      "주석 제거 정규화": summary.comment_free_canonical,
      "결과 개수 제한(LIMIT)": summary.limit,
    });
  } else if (
    event.phase === "NEO4J_EXPLAIN" &&
    typeof summary.approved_cypher === "string"
  ) {
    addDetailFacts(container, {
      "EXPLAIN 연산자": (summary.operators || []).join(", "),
      "결과 개수 제한(LIMIT)": summary.limit,
    });
    if (allowExplorationLinks) {
      addExplorationLink(container, "탐색 그래프 보기", "graph");
    }
  } else if (event.phase === "GRAPH_EXECUTION") {
    const validation = latestInspection("RESULT_VALIDATION");
    addDetailFacts(container, {
      "반환 행": summary.row_count,
      "고유 Fact": validation ? validation.summary.fact_count : null,
      "검증된 근거": validation
        ? validation.summary.verified_evidence_count
        : null,
      "조회 시간": summary.query_elapsed_ms != null
        ? `${summary.query_elapsed_ms}ms`
        : null,
    });
    if (allowExplorationLinks) {
      addExplorationLink(container, "탐색 그래프 보기", "graph");
    }
  } else if (event.phase === "RESULT_VALIDATION") {
    addDetailFacts(container, {
      "검증된 사실": summary.fact_count,
      "검증된 근거": summary.verified_evidence_count,
      "사실 상태 검사": summary.fact_status_verified,
      "근거 상태 검사": summary.evidence_status_verified,
      "직접 provenance 검사": summary.direct_provenance_verified,
      "거부된 행": summary.rejected_row_count,
    });
  } else if (event.phase === "CLAIM_BUILDING") {
    addDetailFacts(container, {
      "주장 수": summary.claim_count,
      "주장 유형": Array.isArray(summary.claim_types) ? summary.claim_types.join(", ") : null,
      "집계 주장": summary.aggregate,
      "인용 대상": summary.citation_target_count,
    });
  } else if (event.phase === "ANSWER_RENDERING") {
    addDetailFacts(container, {
      "결정론적 한국어 renderer": summary.deterministic_renderer,
      "인용 수": summary.citation_count,
      "최종 답변 LLM 호출": summary.final_answer_llm_calls,
    });
  } else if (event.phase === "COMPLETED") {
    addDetailFacts(container, {
      "최종 공개 status": summary.final_status,
      "전체 처리시간": summary.total_elapsed_ms != null
        ? `${summary.total_elapsed_ms}ms`
        : null,
      "재시도 횟수": summary.retry_count,
      "인용 수": summary.citation_count,
      "요청 ID": queryDetailsEnabled && lastResult && lastResult.response
        ? lastResult.response.request_id
        : null,
    });
    addInspectionItem(container, "단계별 시간(ms)", summary.stage_timings_ms);
  }
}

function markTimelineCancelled(message) {
  const running = [...timelineEvents].reverse().find((item) => item.state === "STARTED");
  if (running) {
    running.state = "CANCELLED";
    running.message = message;
    running.elapsed_ms = Number.isFinite(running.started_at_ms)
      ? Math.max(0, Math.round(performance.now() - running.started_at_ms))
      : null;
  }
  renderTimelines();
}

function markTimelineFailed(message, errorCode) {
  const running = [...timelineEvents].reverse().find((item) => item.state === "STARTED");
  if (running) {
    running.state = "FAILED";
    running.message = message;
    running.error_code = errorCode;
  }
  renderTimelines();
}

function renderInspectionUpdate(update) {
  if (!update || update.type !== "inspection_update" || update.version !== 2) return;
  if (!queryDetailsEnabled) return;
  if (update.summary && update.summary.discard_previous_candidate) {
    [...inspectionUpdates.keys()].forEach((key) => {
      if (
        key.startsWith("NEO4J_EXPLAIN:") ||
        key.startsWith("GRAPH_EXECUTION:") ||
        key.startsWith("RESULT_VALIDATION:") ||
        key.startsWith("CLAIM_BUILDING:")
      ) {
        inspectionUpdates.delete(key);
      }
    });
  }
  const attempt = Number.isInteger(update.attempt) ? update.attempt : 0;
  inspectionUpdates.set(inspectionKey(update.stage, attempt), {
    stage: update.stage,
    attempt,
    status: update.status,
    elapsed_ms: update.elapsed_ms,
    summary: update.summary || {},
  });
  renderTimelines();
  // 승인 그래프는 처리가 끝나기 전에 도착한다. 도착하는 즉시 처리 중 화면에
  // 그려야 "탐색 중"으로 보인다.
  renderExplorationPanels();
}

// 탭이 사라졌으므로 이 버튼은 탐색 그래프로 스크롤만 한다.
function addExplorationLink(container, label, tab) {
  void tab;
  if (!el.answerExploration || el.answerExploration.hidden) return;
  const button = document.createElement("button");
  button.type = "button";
  button.className = "stage-jump";
  button.textContent = label;
  button.addEventListener("click", () => {
    const fold = el.answerExploration.querySelector("details.exploration-fold");
    if (fold) {
      fold.open = true;
      explorationExpanded = true;
    }
    el.answerExploration.scrollIntoView({ behavior: "smooth", block: "start" });
  });
  container.append(button);
}

function explorationState() {
  const schema = latestInspection("SCHEMA_SELECTION");
  const explain = latestInspection("NEO4J_EXPLAIN");
  const claims = latestInspection("CLAIM_BUILDING");
  return {
    schema: schema && schema.status === "COMPLETED" ? schema.summary : null,
    explain: explain && explain.status === "COMPLETED" ? explain.summary : null,
    claims: claims && claims.status === "COMPLETED" ? claims.summary : null,
  };
}

// 처리 중 화면과 결과 화면에 같은 그래프가 떠야 한다. 두 컨테이너를 항상 함께
// 갱신해, 어느 화면으로 넘어가도 같은 것이 보이게 한다.
// 조회가 실제로 진행될 요청인지. 계획이 READY 로 끝났거나 이후 단계가 시작됐으면
// 곧 그래프가 생긴다.
function queryWillRun() {
  const analysis = latestInspection("QUESTION_ANALYSIS");
  if (analysis && analysis.summary && analysis.summary.status &&
      analysis.summary.status !== "READY") {
    return false;
  }
  return timelineEvents.some((event) =>
    ["SCHEMA_SELECTION", "CYPHER_GENERATION", "STATIC_VALIDATION", "NEO4J_EXPLAIN",
     "GRAPH_EXECUTION"].includes(event.phase)
  );
}

// 승인 전 자리표시. 어느 단계까지 왔는지 실제 타임라인에서 읽어 보여 준다.
function renderExplorationWaiting(container) {
  container.hidden = false;
  container.replaceChildren();
  const head = document.createElement("div");
  head.className = "exploration-head";
  const title = document.createElement("h3");
  title.textContent = "지식그래프 탐색";
  const description = document.createElement("p");
  const running = [...timelineEvents].reverse().find((e) => e.state === "STARTED");
  description.textContent = running
    ? `${running.message} 승인되면 여기에 탐색 경로가 나타납니다.`
    : "질의가 승인되면 여기에 탐색 경로가 나타납니다.";
  head.append(title, description);
  // 큰 자리표시 상자는 오히려 방해가 됐다. 한 줄 상태만 남긴다.
  container.append(head);
}

function renderExplorationPanels() {
  [el.progressExploration, el.answerExploration].forEach((container) => {
    if (container) renderExplorationPanel(container);
  });
}

function renderExplorationPanel(container) {
  if (graphResizeObserver) {
    container.querySelectorAll(".graph-viewport").forEach((viewport) => {
      graphResizeObserver.unobserve(viewport);
    });
  }
  container.replaceChildren();
  container.hidden = !queryDetailsEnabled;
  if (!queryDetailsEnabled) return;

  const state = explorationState();
  const availability = {
    schema: Boolean(
      state.schema &&
      Array.isArray(state.schema.labels) &&
      state.schema.labels.length
    ),
    cypher: Boolean(state.explain && typeof state.explain.approved_cypher === "string"),
    // 실제로 그래프를 탐색한 질문에만 연다. 되묻기처럼 조회가 일어나지 않은
    // 요청에는 보여 줄 탐색이 없다. 조회했는데 결과가 0건인 경우는 탐색을 한
    // 것이므로 연다(EXPLAIN 승인이 그 증거다).
    graph: Boolean(
      (state.explain && state.explain.query_graph) ||
      (state.claims && state.claims.provenance_graph)
    ),
  };
  const availableTabs = Object.keys(availability).filter((key) => availability[key]);
  // 처리 중 화면은 **탐색 그래프만** 보여 준다. 후보 스키마 목록은 승인 전이라
  // 전부 "미사용" 으로 표시돼 오해를 부르고, 지금 보고 싶은 것은 노드 탐색이다.
  // 스키마·Cypher 탭은 결과 화면에서 확인한다.
  if (container === el.progressExploration) {
    if (!availability.graph) {
      if (queryWillRun()) {
        renderExplorationWaiting(container);
        return;
      }
      container.hidden = true;
      return;
    }
    container.hidden = false;
    const head = document.createElement("div");
    head.className = "exploration-head";
    const title = document.createElement("h3");
    title.textContent = "지식그래프 탐색";
    const description = document.createElement("p");
    description.textContent = "승인된 질의가 그래프를 밟는 순서대로 재생합니다.";
    head.append(title, description);
    const panel = document.createElement("div");
    panel.id = `${container.id}-panel`;
    panel.className = "exploration-panel";
    renderGraphTab(panel, state, true);
    container.append(head, panel);
    return;
  }
  // 결과 화면도 조회 그래프 하나만 보여 준다. 선택 스키마와 승인 Cypher 는 아래
  // `처리 과정 보기` 의 해당 단계 상세에 그대로 있으므로 탭으로 나눌 이유가 없었다.
  if (!availability.graph) {
    container.hidden = true;
    return;
  }
  container.hidden = false;

  const head = document.createElement("div");
  head.className = "exploration-head";
  const title = document.createElement("h3");
  title.textContent = "지식그래프 탐색";
  const description = document.createElement("p");
  description.textContent =
    "엔진이 실제로 실행한 순서와 단계별 실측 시간 그대로 재생합니다. " +
    "실제 조회가 수십 ms 안에 끝나므로 재생도 그만큼 짧습니다.";
  head.append(title, description);

  const panel = document.createElement("div");
  panel.id = `${container.id}-panel`;
  panel.className = "exploration-panel";
  renderGraphTab(panel, state, false);

  // 처리 중 화면은 위에서 조기 반환하므로 여기까지 오는 것은 결과 화면뿐이다.
  // 결과 화면은 기본으로 접어 두고, 펼치면 최종 상태와 재생을 볼 수 있다.
  {
    const details = document.createElement("details");
    details.className = "exploration-fold";
    details.open = explorationExpanded;
    const summary = document.createElement("summary");
    summary.textContent = "탐색 과정 보기";
    details.addEventListener("toggle", () => {
      explorationExpanded = details.open;
    });
    // summary 를 붙이지 않으면 브라우저가 영어 기본 라벨("Details")을 보여 준다.
    details.append(summary, head, panel);
    container.append(details);
  }
}

function addBadges(container, title, values, kind, usedSet) {
  if (!Array.isArray(values) || !values.length) return;
  const group = document.createElement("section");
  group.className = "badge-group";
  const heading = document.createElement("h4");
  heading.textContent = title;
  const list = document.createElement("ul");
  values.forEach((value) => {
    const item = document.createElement("li");
    const used = usedSet ? usedSet.has(value) : true;
    item.className = `schema-badge is-${kind}${used ? " is-used" : " is-unused"}`;
    item.textContent = used ? `${value} · 사용됨` : `${value} · 미사용`;
    item.title = used
      ? "승인된 질의가 실제로 사용했습니다"
      : "후보로 제시했지만 최종 질의에는 쓰이지 않았습니다";
    list.append(item);
  });
  group.append(heading, list);
  container.append(group);
}

function renderSchemaTab(container, summary) {
  if (!summary) return;
  // 이 목록은 "쓴 것"이 아니라 "이 안에서만 고르라고 모델에 건넨 후보"다. 종전의
  // `선택된 node label` 표기는 실제 사용으로 읽혀 오해를 불렀다. 승인된 질의가
  // 실제로 쓴 것만 따로 표시한다.
  const explain = latestInspection("NEO4J_EXPLAIN");
  const used = explain && explain.summary ? explain.summary : {};
  const usedLabels = new Set(used.labels || []);
  const usedRels = new Set(used.relationships || []);
  const note = document.createElement("p");
  note.className = "projection-note";
  note.textContent =
    "질의를 만들 때 모델에게 건넨 후보 목록입니다. 이 안에서만 고를 수 있으며, " +
    "실제 사용 여부는 각 항목에 표시했습니다.";
  container.append(note);
  addBadges(container, "후보 node label", summary.labels, "node", usedLabels);
  addBadges(container, "후보 relationship type", summary.relationships, "relationship", usedRels);
}

function renderCypherTab(container, summary) {
  if (!summary || typeof summary.approved_cypher !== "string") return;
  addInspectionItem(
    container,
    "LLM 생성 후 안전 검증된 canonical Cypher",
    summary.approved_cypher,
    { cypher: true }
  );
  addInspectionItem(container, "정제된 파라미터", summary.parameters);
  addInspectionItem(container, "EXPLAIN 연산자", summary.operators);
  addDetailFacts(container, {
    "사용 label": (summary.labels || []).join(", "),
    "사용 relationship": (summary.relationships || []).join(", "),
    "결과 개수 제한(LIMIT)": summary.limit,
  });
}

// 탐색이 어디까지 갔고 어디서 끊겼는지. 승인된 경로는 있는데 결과가 없거나 단계가
// 실패한 경우, 경로를 그대로 그리고 끊긴 지점을 ✗ 로 표시한다.
// 어느 hop 에서 0건이 됐는지는 Neo4j 가 EXPLAIN 으로 알려주지 않으므로 추정하지
// 않는다. "경로 끝까지 갔지만 일치가 없었다"까지만 말한다.
function traversalOutcome() {
  const execution = latestInspection("GRAPH_EXECUTION");
  const validation = latestInspection("RESULT_VALIDATION");
  const claims = latestInspection("CLAIM_BUILDING");
  for (const [stage, item] of [
    ["그래프 조회", execution],
    ["결과 검증", validation],
    ["사실 구성", claims],
  ]) {
    if (item && item.status === "FAILED") {
      return { failed: true, label: `${stage} 단계에서 중단됨` };
    }
  }
  if (
    execution &&
    execution.status === "COMPLETED" &&
    execution.summary &&
    execution.summary.row_count === 0
  ) {
    return {
      failed: true,
      label: "경로는 끝까지 승인됐지만 조건에 맞는 사실이 0건입니다",
    };
  }
  return { failed: false, label: "" };
}

// 엔진 실행 계획(PROFILE 실측). 그래프와 같은 요청에서 온 것만 쓴다.
function operatorPlan() {
  const execution = latestInspection("GRAPH_EXECUTION");
  const steps = execution && execution.summary ? execution.summary.traversal_steps : null;
  return Array.isArray(steps) ? steps : [];
}

function renderGraphTab(container, state, autoplay = false) {
  const outcome = traversalOutcome();
  const note = document.createElement("p");
  note.className = outcome.failed ? "projection-note is-failed" : "projection-note";
  note.textContent = outcome.failed
    ? `실행한 질의가 밟은 경로입니다. ${outcome.label}.`
    : "실제로 실행한 질의가 그래프를 밟은 경로와, 그 결과의 VERIFIED provenance입니다.";
  container.append(note);
  // 루트에서 실제 매칭된 노드까지 한 장으로. 통합 그래프를 못 만든 경우에만
  // 종전처럼 스키마 경로와 provenance 를 따로 그린다.
  const unified = state.claims ? state.claims.traversal_graph : null;
  if (unified) {
    renderGraphPanel(container, "탐색 경로", unified, { autoplay, outcome });
    return;
  }
  if (state.explain && state.explain.query_graph) {
    renderGraphPanel(container, "1. 질의 구조", state.explain.query_graph, { autoplay, outcome });
  }
  if (state.claims && state.claims.provenance_graph) {
    renderGraphPanel(
      container,
      "2. 조회 결과와 VERIFIED Evidence",
      state.claims.provenance_graph
    );
  }
}

function addInspectionItem(container, label, value, options = {}) {
  if (value === null || value === undefined) return;
  const item = document.createElement("div");
  item.className = "inspection-item";
  const head = document.createElement("div");
  head.className = "inspection-head";
  const title = document.createElement("strong");
  title.textContent = label;
  head.append(title);
  const body = document.createElement("pre");
  body.textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  if (options.cypher) {
    body.classList.add("cypher-code", "is-collapsed");
    const feedback = span("copy-feedback", "");
    feedback.setAttribute("role", "status");
    feedback.setAttribute("aria-live", "polite");
    const expand = document.createElement("button");
    expand.type = "button";
    expand.className = "inspection-action";
    expand.textContent = "펼치기";
    expand.addEventListener("click", () => {
      const collapsed = body.classList.toggle("is-collapsed");
      expand.textContent = collapsed ? "펼치기" : "접기";
    });
    const copy = document.createElement("button");
    copy.type = "button";
    copy.className = "inspection-action";
    copy.textContent = "Cypher 복사";
    copy.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(value);
        feedback.textContent = "복사했습니다.";
      } catch (_) {
        feedback.textContent = "복사하지 못했습니다.";
      }
    });
    head.append(expand, copy, feedback);
  }
  item.append(head, body);
  container.append(item);
}

// TODO(묶음 4): 전체 추적을 위에서 아래로 읽는 "전체 보기" 모드와 텍스트/JSON
// 내보내기 버튼. 질문 원문·시각·단계별 값이 모두 담겨 이 기록만으로 재현 가능해야 한다.
// TODO(묶음 4): 개인 데이터 유래 필드 표시와 내보내기 마스킹(기본 켬). PR #32 의
// personalized_service.py 가 병합되면 학번·이수 이력이 추적 화면에 실릴 수 있다.

const SVG_NS = "http://www.w3.org/2000/svg";

// 간선 표기: `①근거 연결` 처럼 탐색 순서와 한국어 관계명을 붙인다. 순서는 승인된
// MATCH 경로가 쓰인 차례이며 Neo4j 내부 실행 순서가 아니다. 한국어 이름이나 순서가
// 없으면(구 payload) 종전처럼 영어 관계명만 쓴다.
const ORDER_MARKS = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮";

function orderMark(order) {
  if (!Number.isInteger(order) || order < 1) return "";
  return order <= ORDER_MARKS.length ? ORDER_MARKS[order - 1] : `(${order})`;
}

function edgeRelationshipText(edge) {
  const name =
    typeof edge.relationship_ko === "string" && edge.relationship_ko.trim()
      ? edge.relationship_ko.trim()
      : edge.relationship;
  const mark = orderMark(edge.traversal_order);
  return mark ? `${mark} ${name}` : name;
}

function svgNode(name, attributes = {}) {
  const node = document.createElementNS(SVG_NS, name);
  Object.entries(attributes).forEach(([key, value]) => node.setAttribute(key, String(value)));
  return node;
}

function graphCategory(type) {
  if (type === "Question") return "context";
  if (type === "Candidate") return "other";
  if (type === "Evidence") return "evidence";
  if (["Course", "CourseOffering"].includes(type)) return "course";
  if (["Rule", "Requirement", "CreditRequirement"].includes(type)) return "rule";
  if (["Curriculum", "CurriculumVersion", "Department"].includes(type)) return "context";
  return "other";
}

function graphColumn(node) {
  const category = graphCategory(node.node_type);
  if (category === "context") return 0;
  if (category === "evidence") return 2;
  return 1;
}

function graphProjectionIsSafe(graph) {
  if (
    !graph ||
    graph.version !== 1 ||
    ![
      "QUERY_STRUCTURE",
      "RESULT_PROVENANCE",
    ].includes(graph.kind) ||
    !Array.isArray(graph.nodes) ||
    !Array.isArray(graph.edges) ||
    graph.nodes.length > 200 ||
    graph.edges.length > 300
  ) return false;
  const ids = new Set();
  for (const node of graph.nodes) {
    if (
      !node ||
      typeof node.id !== "string" ||
      !node.id.startsWith("ui:") ||
      ids.has(node.id) ||
      typeof node.display_name !== "string" ||
      typeof node.node_type !== "string" ||
      !["SCHEMA_APPROVED", "VERIFIED"].includes(
        node.verification_status
      )
    ) return false;
    ids.add(node.id);
  }
  return graph.edges.every(
    (edge) =>
      edge &&
      typeof edge.id === "string" &&
      edge.id.startsWith("ui:") &&
      ids.has(edge.source) &&
      ids.has(edge.target) &&
      typeof edge.relationship === "string"
  );
}

function renderGraphFallback(container, graph) {
  const fallback = document.createElement("div");
  fallback.className = "graph-fallback";
  const heading = document.createElement("p");
  heading.textContent = "그래프를 그리지 못해 검증된 관계를 목록으로 표시합니다.";
  fallback.append(heading);
  const nodes = new Map((graph.nodes || []).map((node) => [node.id, node]));
  const list = document.createElement("ul");
  (graph.edges || []).forEach((edge) => {
    const source = nodes.get(edge.source);
    const target = nodes.get(edge.target);
    if (!source || !target) return;
    const item = document.createElement("li");
    item.textContent = `${source.display_name} ──${edgeRelationshipText(edge)}──> ${target.display_name}`;
    list.append(item);
  });
  if (!list.childElementCount) {
    (graph.nodes || []).forEach((node) => {
      const item = document.createElement("li");
      item.textContent = `${node.display_name} (${node.node_type})`;
      list.append(item);
    });
  }
  fallback.append(list);
  container.append(fallback);
}

// 글자 수로 폭을 추정하면 한글·영문·숫자가 섞일 때 맞지 않는다. 실제 렌더 폭을
// canvas 로 잰다. 측정은 결과를 캐시해 노드마다 반복하지 않는다.
// CSS 가 시작 노드만 14px 로 키우므로(B-1) 측정도 역할별로 나눈다. 13px 로 재고
// 14px 로 그리면 뿌리 노드 이름이 상자를 넘친다.
const NODE_FONT_STACK = '"Pretendard", "Apple SD Gothic Neo", "Malgun Gothic", "Noto Sans KR", system-ui, sans-serif';
const NODE_FONT_PX = { root: 14, step: 13, evidence: 13 };
const NODE_FONT = `600 ${NODE_FONT_PX.step}px ${NODE_FONT_STACK}`;
const NODE_MAX_LINE_PX = 190;   // 이 폭을 넘으면 줄을 바꾼다. 잘라내는 기준이 아니다.
const NODE_MAX_LINES = 3;
let _measureCtx = null;
const _measureCache = new Map();

function measureText(text, role = "step") {
  const px = NODE_FONT_PX[role] || NODE_FONT_PX.step;
  const key = `${px}\u0000${text}`;
  if (_measureCache.has(key)) return _measureCache.get(key);
  if (!_measureCtx) {
    _measureCtx = document.createElement("canvas").getContext("2d");
  }
  let width;
  if (_measureCtx) {
    _measureCtx.font = `600 ${px}px ${NODE_FONT_STACK}`;
    width = _measureCtx.measureText(text).width;
  } else {
    // canvas 를 못 쓰는 환경에서는 글자 수 추정으로 물러난다.
    width = text.length * px;
  }
  _measureCache.set(key, width);
  return width;
}

// 최대 세 줄까지 자연스럽게 줄을 바꾼다. 말줄임은 세 줄로도 안 될 때만 쓰고,
// 그때도 전체 이름을 tooltip 에 남긴다.
function graphLabelLines(value, role = "step") {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  if (!text) return [""];
  if (measureText(text, role) <= NODE_MAX_LINE_PX) return [text];

  // 공백과 구분자(·, ,) 뒤를 우선 끊는다. 한국어 조사 경계를 억지로 끊지 않는다.
  const tokens = text.match(/[^\s·,]+[\s·,]*/g) || [text];
  const lines = [];
  let current = "";
  for (const token of tokens) {
    const candidate = current + token;
    if (current && measureText(candidate.trim(), role) > NODE_MAX_LINE_PX) {
      lines.push(current.trim());
      current = token;
      if (lines.length === NODE_MAX_LINES) break;
    } else {
      current = candidate;
    }
  }
  if (lines.length < NODE_MAX_LINES && current.trim()) lines.push(current.trim());

  // 한 토큰이 한 줄보다 길면 그 토큰만 글자 단위로 쪼갠다.
  const expanded = [];
  for (const line of lines) {
    if (measureText(line, role) <= NODE_MAX_LINE_PX || line.includes(" ")) {
      expanded.push(line);
      continue;
    }
    let chunk = "";
    for (const ch of line) {
      if (measureText(chunk + ch, role) > NODE_MAX_LINE_PX && chunk) {
        expanded.push(chunk);
        chunk = ch;
      } else {
        chunk += ch;
      }
    }
    if (chunk) expanded.push(chunk);
  }
  const out = expanded.slice(0, NODE_MAX_LINES);
  // 세 줄로도 못 담으면 마지막 줄만 말줄임한다. 전체 이름은 tooltip 에 있다.
  if (expanded.length > NODE_MAX_LINES && out.length) {
    const last = out[out.length - 1];
    let trimmed = last;
    while (trimmed && measureText(trimmed + "…", role) > NODE_MAX_LINE_PX) {
      trimmed = trimmed.slice(0, -1);
    }
    out[out.length - 1] = `${trimmed}…`;
  }
  return out;
}

// 노드 폭·높이는 실제 렌더 폭과 줄 수에서 나온다.
function nodeBoxWidth(lines, role = "step") {
  const longest = lines.reduce((max, line) => Math.max(max, measureText(line, role)), 0);
  return Math.max(112, Math.ceil(longest) + 34);
}

function nodeBoxHeight(lines, hasCategory) {
  return 20 + lines.length * 18 + (hasCategory ? 16 : 0);
}

function graphEdgeGeometry(source, target, nodeWidth, nodeHeight, mobile, offset) {
  if (mobile || source.x === target.x) {
    const x1 = source.x + nodeWidth / 2;
    const y1 = source.y + nodeHeight;
    const x2 = target.x + nodeWidth / 2;
    const y2 = target.y;
    const middle = (y1 + y2) / 2;
    return {
      path: `M ${x1} ${y1} C ${x1} ${middle}, ${x2} ${middle}, ${x2} ${y2}`,
      labelX: x1 + 76 + offset,
      labelY: middle - 7,
    };
  }
  const x1 = source.x + nodeWidth;
  const y1 = source.y + nodeHeight / 2;
  const x2 = target.x;
  const y2 = target.y + nodeHeight / 2;
  const middle = (x1 + x2) / 2;
  return {
    path: `M ${x1} ${y1} C ${middle} ${y1}, ${middle} ${y2}, ${x2} ${y2}`,
    labelX: middle,
    labelY: (y1 + y2) / 2 - 12 + offset,
  };
}

// 승인된 경로를 한 hop 씩 재생한다. 여기서 재생하는 순서는 **승인된 MATCH 패턴이
// 쓰인 차례**이며 Neo4j 엔진의 내부 실행 순서가 아니다. 화면 문구도 그렇게 적는다.
const SIMULATION_STEP_MS = 900;
const runningSimulations = new Map();
// 같은 그래프를 두 번 자동재생하지 않는다. 재렌더가 잦아서 없으면 계속 처음부터 돈다.
const autoplayedGraphs = new Set();

function stopSimulation(svg) {
  const timer = runningSimulations.get(svg);
  if (timer) {
    clearTimeout(timer);
    runningSimulations.delete(svg);
  }
}

// 오른쪽 방문 순서 목록. 그래프에서 번호를 못 읽는 경우에도 순서를 글로 확인할 수
// 있어야 하고, 재생 중에는 현재 hop 이 여기서도 같이 강조된다.
function buildTraversalList(graph) {
  const list = document.createElement("ol");
  list.className = "traversal-list";
  const nodes = new Map((graph.nodes || []).map((node) => [node.id, node]));
  const ordered = (graph.edges || [])
    .filter((edge) => Number.isInteger(edge.traversal_order))
    .sort((left, right) => left.traversal_order - right.traversal_order);
  ordered.forEach((edge) => {
    const source = nodes.get(edge.source);
    const target = nodes.get(edge.target);
    if (!source || !target) return;
    const item = document.createElement("li");
    item.className = "traversal-step";
    item.dataset.order = String(edge.traversal_order);
    if (edge.relationship) item.dataset.relationship = edge.relationship;
    const mark = document.createElement("span");
    mark.className = "traversal-mark";
    mark.textContent = orderMark(edge.traversal_order) || edge.traversal_order;
    const body = document.createElement("div");
    body.className = "traversal-body";
    const hop = document.createElement("p");
    hop.className = "traversal-hop";
    // 라벨 종류가 아니라 거쳐간 노드의 원본 이름을 그대로 적는다.
    const from = document.createElement("span");
    from.className = "traversal-node";
    from.textContent = source.display_name;
    if (source.node_type_ko) {
      const c = document.createElement("i");
      c.className = "traversal-cat";
      c.textContent = source.node_type_ko;
      from.append(c);
    }
    const arrow = document.createElement("span");
    arrow.className = "traversal-arrow";
    arrow.textContent = "→";
    const to = document.createElement("span");
    to.className = "traversal-node";
    to.textContent = target.display_name;
    if (target.node_type_ko) {
      const c = document.createElement("i");
      c.className = "traversal-cat";
      c.textContent = target.node_type_ko;
      to.append(c);
    }
    hop.append(from, arrow, to);
    const rel = document.createElement("p");
    rel.className = "traversal-rel";
    // 영어 관계 타입은 목록에 찍지 않는다. 읽는 데 방해만 되고 tooltip 으로 충분하다.
    const parts = [edge.relationship_ko || edge.relationship];
    if (Number.isInteger(edge.rows)) parts.push(`${edge.rows}행`);
    if (Number.isInteger(edge.db_hits)) parts.push(`DB ${edge.db_hits}회`);
    if (Number.isFinite(edge.share_ms)) parts.push(`배분 ${edge.share_ms}ms`);
    rel.textContent = parts.join(" · ");
    rel.title = `${edge.relationship} — 시간은 Neo4j 가 단계별로 주지 않아 총 실행시간을 DB 접근 비율로 나눈 배분값입니다`;
    body.append(hop, rel);
    item.append(mark, body);
    list.append(item);
  });
  return list.childElementCount ? list : null;
}

// 엔진이 실제로 실행한 operator 순서 그대로 재생한다. Neo4j 는 BFS 로 돌지 않는다.
// NodeIndexSeek 로 시작해 Expand 와 Filter 를 번갈아 흘려보내는 파이프라인이며,
// 행 수가 늘었다 줄어드는 지점이 곧 "어디서 좁혀졌는가" 다. 층 단위 파동은 실제
// 동작이 아니라서 걷어냈다.
// 지금 어느 operator 가 도는지 글로 보여 준다. 값은 전부 PROFILE 실측이다.
const OPERATOR_KO = {
  NodeIndexSeek: "인덱스로 시작 노드 찾기",
  NodeUniqueIndexSeek: "고유 인덱스로 시작 노드 찾기",
  NodeByLabelScan: "라벨로 노드 훑기",
  "Expand(All)": "관계 타고 확장",
  Filter: "조건으로 거르기",
  Limit: "개수 제한",
  Projection: "필요한 값만 뽑기",
  ProduceResults: "결과 내보내기",
  EagerAggregation: "집계",
};

function renderOperatorReadout(container, plan, step) {
  container.replaceChildren();
  const list = document.createElement("ol");
  list.className = "operator-list";
  plan.forEach((item, index) => {
    const li = document.createElement("li");
    li.className = "operator-step";
    // 노드 클릭 시 이 항목을 찾기 위한 대응 키. 번호가 아니라 관계 이름으로 맞춘다.
    if (item.relationship_type) li.dataset.relationship = item.relationship_type;
    if (index < step) li.classList.add("is-done");
    if (index === step - 1) li.classList.add("is-current");
    const name = document.createElement("p");
    name.className = "operator-name";
    // 서버가 붙인 한국어 설명이 있으면 그것을 쓴다. 무엇을 확인했는지가 드러난다.
    name.textContent = item.explanation_ko
      ? `${item.order}. ${item.explanation_ko}`
      : `${item.order}. ${OPERATOR_KO[item.operator] || item.operator}`;
    const meta = document.createElement("p");
    meta.className = "operator-meta";
    const bits = [OPERATOR_KO[item.operator] || item.operator];
    if (Number.isInteger(item.rows)) bits.push(`${item.rows}행`);
    if (Number.isInteger(item.db_hits)) bits.push(`DB ${item.db_hits}회`);
    meta.textContent = bits.join(" · ");
    li.append(name, meta);
    if (item.detail) {
      const detail = document.createElement("code");
      detail.className = "operator-detail";
      detail.textContent = item.detail;
      li.append(detail);
    }
    // 목록 항목을 누르면 그 단계 상태로 이동한다.
    li.tabIndex = 0;
    li.setAttribute("role", "button");
    li.setAttribute("aria-label", `${item.order}단계로 이동`);
    const jump = () => {
      const svg = container.closest(".graph-split")?.querySelector(".graph-canvas svg");
      if (svg && typeof svg._setStep === "function") svg._setStep(item.order);
    };
    li.addEventListener("click", jump);
    li.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        jump();
      }
    });
    list.append(li);
  });
  container.append(list);
}

// 그래프가 뷰포트보다 넓으면 지금 색이 변하는 노드가 화면 밖에 있을 수 있다.
// 그러면 연출을 만들어도 보이지 않는다. 현재 단계를 따라 뷰포트를 움직인다.
// 사용자가 직접 스크롤하면 잠시 멈춘다. 따라다니면 성가시다.
const USER_SCROLL_PAUSE_MS = 4000;

function prefersReducedMotion() {
  return Boolean(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
}

function followInViewport(svg, element) {
  if (!element) return;
  const viewport = svg.closest(".graph-viewport");
  if (!viewport) return;
  if (viewport._userScrollUntil && Date.now() < viewport._userScrollUntil) return;
  const box = element.getBoundingClientRect();
  const frame = viewport.getBoundingClientRect();
  const margin = 40;
  let dx = 0;
  let dy = 0;
  if (box.left < frame.left + margin) dx = box.left - frame.left - margin;
  else if (box.right > frame.right - margin) dx = box.right - frame.right + margin;
  if (box.top < frame.top + margin) dy = box.top - frame.top - margin;
  else if (box.bottom > frame.bottom - margin) dy = box.bottom - frame.bottom + margin;
  if (!dx && !dy) return;
  viewport._autoScrolling = true;
  viewport.scrollBy({
    left: dx,
    top: dy,
    behavior: prefersReducedMotion() ? "auto" : "smooth",
  });
  window.setTimeout(() => {
    viewport._autoScrolling = false;
  }, 400);
}

function applySimulationStep(svg, step) {
  const split = svg.closest(".graph-split");
  if (split) {
    split.querySelectorAll(".traversal-step").forEach((item) => {
      const order = Number(item.dataset.order);
      item.classList.toggle("is-traversed", order <= step);
      item.classList.toggle("is-active", order === step);
    });
  }
  svg.querySelectorAll("[data-order]").forEach((element) => {
    const order = Number(element.dataset.order);
    element.classList.toggle("is-traversed", order <= step);
    element.classList.toggle("is-active", order === step);
  });
  // operator 순서에 대응하는 관계를 켠다. 대응이 없는 operator(Filter/Limit 등)는
  // 그래프 모양을 바꾸지 않으므로 직전까지의 강조를 유지한다.
  const plan = svg._operatorPlan || [];
  const reached = new Set();
  let current = null;
  plan.slice(0, step).forEach((item) => {
    if (item.relationship_type) reached.add(item.relationship_type);
  });
  const now = plan[step - 1];
  if (now && now.relationship_type) current = now.relationship_type;
  svg.querySelectorAll("[data-relationship]").forEach((element) => {
    const rel = element.dataset.relationship;
    element.classList.toggle("is-traversed", reached.has(rel));
    element.classList.toggle("is-frontier", rel === current);
  });
  svg.querySelectorAll("[data-reached-by]").forEach((element) => {
    const rel = element.dataset.reachedBy;
    element.classList.toggle("is-visited", rel === "" || reached.has(rel));
    element.classList.toggle("is-frontier", rel === current);
  });
  const readout = svg.closest(".graph-panel")?.querySelector(".operator-readout");
  if (readout) renderOperatorReadout(readout, plan, step);

  // 그래프 모양을 바꾸지 않는 단계(Filter/Limit/Projection 등)에서는 그 사실을 적는다.
  // 적지 않으면 ◀▶ 로 옮겨도 화면이 그대로라 고장으로 읽힌다. 문구의 값은 그 단계가
  // 실제로 보고한 것에서만 온다.
  // 지금 강조된 것을 따라간다. 간선이 있으면 간선, 없으면 막 도달한 노드.
  const target =
    svg.querySelector(".graph-edge-group.is-frontier") ||
    svg.querySelector(".graph-node.is-frontier");
  if (target) followInViewport(svg, target);

  const caption = svg.closest(".graph-panel")?.querySelector(".step-caption");
  if (caption) {
    const item = plan[step - 1];
    if (!item) {
      caption.hidden = true;
    } else if (item.relationship_type) {
      caption.hidden = true;
    } else {
      caption.hidden = false;
      caption.textContent =
        `${item.order}단계 · ${item.explanation_ko || item.operator}` +
        " — 그래프 구조는 바뀌지 않습니다";
    }
  }
}

// 결과 화면 재생 배율. 진행 중 화면은 1(실측 그대로), 결과 화면은 사람이 볼 수 있게
// 늘린다. 비율은 유지하고, 늘렸다는 사실과 실측값을 화면에 함께 적는다.
const REPLAY_MIN_STEP_MS = 400;

function replayFactor(plan, slowed) {
  if (!slowed) return 1;
  const positives = plan
    .map((item) => (Number.isFinite(item.share_ms) ? item.share_ms : 0))
    .filter((value) => value > 0);
  if (!positives.length) return 1;
  return REPLAY_MIN_STEP_MS / Math.min(...positives);
}

function measuredTotalMs(plan) {
  return plan.reduce(
    (sum, item) => sum + (Number.isFinite(item.share_ms) ? item.share_ms : 0),
    0
  );
}

function startSimulation(svg, maxOrder, button) {
  stopSimulation(svg);
  svg.classList.add("is-simulating", "shows-state");
  svg.classList.remove("is-complete");
  let step = 0;
  applySimulationStep(svg, step);
  button.textContent = "■ 정지";
  // 각 단계를 **그 단계의 실측 시간**만큼만 보여 준다. 늘리거나 줄이지 않는다.
  // 전체가 수십 ms 라 재생도 그만큼 짧게 끝난다. 실제와 같게 하는 것이 목적이다.
  const plan = svg._operatorPlan || [];
  const slowed = Boolean(svg._replaySlowed);
  const factor = replayFactor(plan, slowed);
  const total = measuredTotalMs(plan);
  const note = svg.closest(".graph-panel")?.querySelector(".replay-note");
  if (note) {
    note.textContent = slowed
      ? `실측 ${total.toFixed(1)}ms · 사람이 볼 수 있도록 ${Math.round(factor)}배 느리게 재생 중`
      : `실측 ${total.toFixed(1)}ms · 실제 속도로 재생 중`;
    note.hidden = false;
  }
  const tick = () => {
    step += 1;
    applySimulationStep(svg, step);
    if (step > maxOrder) {
      stopSimulation(svg);
      // 완료 상태로 넘긴다. shows-state 를 유지하므로 경로·순서 번호·흐림이 남는다.
      svg.classList.remove("is-simulating");
      svg.classList.add("is-complete", "shows-state");
      applySimulationStep(svg, (svg._operatorPlan || []).length);
      svg.querySelectorAll(".is-active").forEach((element) =>
        element.classList.remove("is-active")
      );
      button.textContent = "▶ 순서대로 다시 훑어보기";
      if (note) {
        // 재생이 끝나도 실측값은 계속 보이게 둔다.
        note.textContent = `실측 ${total.toFixed(1)}ms · 재생 완료`;
      }
      return;
    }
    const current = plan[step - 1];
    const realMs = current && Number.isFinite(current.share_ms) ? current.share_ms : 0;
    runningSimulations.set(svg, setTimeout(tick, Math.max(0, realMs * factor)));
  };
  runningSimulations.set(svg, setTimeout(tick, 0));
}

function addSimulationControl(controls, svg, graph, { autoplay = false } = {}) {
  const orders = (graph.edges || [])
    .map((edge) => edge.traversal_order)
    .filter((value) => Number.isInteger(value));
  if (!graph.ordered || !orders.length) return;
  // 재생 단위는 엔진이 실행한 operator 개수다. 지어낸 단위를 쓰지 않는다.
  const plan = svg._operatorPlan || [];
  const maxOrder = plan.length || Math.max(...orders);
  // 재생을 누르지 않아도 완료 상태가 처음부터 보인다. ▶ 는 순서를 다시 훑는 역할이다.
  svg.classList.add("is-complete", "shows-state");
  applySimulationStep(svg, maxOrder);

  // ◀ / ▶ 단계 이동. 실행이 순식간에 끝나도 사람이 되짚어 읽을 수 있어야 한다.
  let cursor = maxOrder;
  const readout = document.createElement("span");
  readout.className = "graph-step-readout";
  const setCursor = (next) => {
    cursor = Math.max(0, Math.min(maxOrder, next));
    stopSimulation(svg);
    svg.classList.remove("is-simulating");
    svg.classList.add("is-complete", "shows-state");
    // 이동은 애니메이션 없이 즉시 상태 전환한다.
    applySimulationStep(svg, cursor);
    readout.textContent = `${cursor} / ${maxOrder}`;
    prev.disabled = cursor === 0;
    next2.disabled = cursor === maxOrder;
  };
  const stepButton = (label, title, delta) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "ghost compact graph-step";
    b.textContent = label;
    b.title = title;
    b.addEventListener("click", () => setCursor(cursor + delta));
    return b;
  };
  // 결과 화면은 사람이 보라고 다시 그리는 것이므로 늘려 재생한다. 진행 중 화면은
  // 실측 그대로 둔다. 어느 쪽인지는 autoplay 로 구분된다.
  svg._replaySlowed = !autoplay;

  const prev = stepButton("◀", "한 단계 뒤로", -1);
  const next2 = stepButton("▶", "한 단계 앞으로", 1);
  controls.append(prev, readout, next2);
  const caption = document.createElement("p");
  caption.className = "step-caption";
  caption.hidden = true;
  (controls.parentElement || controls).append(caption);

  const note = document.createElement("p");
  note.className = "replay-note";
  note.hidden = true;
  const totalMs = measuredTotalMs(svg._operatorPlan || []);
  note.textContent = `실측 ${totalMs.toFixed(1)}ms`;
  note.hidden = false;
  controls.parentElement
    ? controls.parentElement.append(note)
    : controls.append(note);
  setCursor(maxOrder);
  svg._setStep = setCursor;

  const button = document.createElement("button");
  button.type = "button";
  button.className = "ghost compact graph-simulate";
  button.textContent = "▶ 순서대로 다시 훑어보기";
  button.title = "엔진이 실행한 순서 그대로, 각 단계의 실측 시간만큼 다시 보여 줍니다";
  button.addEventListener("click", () => {
    if (runningSimulations.has(svg)) {
      stopSimulation(svg);
      svg.classList.remove("is-simulating");
      // 정지해도 완료 상태로 되돌린다. 아무것도 안 보이는 상태로 두지 않는다.
      svg.classList.remove("is-simulating");
      svg.classList.add("is-complete", "shows-state");
      applySimulationStep(svg, (svg._operatorPlan || []).length);
      const split = svg.closest(".graph-split");
      if (split) {
        split.querySelectorAll(".traversal-step").forEach((item) =>
          item.classList.remove("is-traversed", "is-active")
        );
      }
      button.textContent = "▶ 탐색 재생";
      return;
    }
    startSimulation(svg, maxOrder, button);
  });
  controls.append(button);
  // 처리 중 화면은 사용자가 누르지 않아도 한 번 재생한다. 그 화면의 목적이
  // "지금 무엇을 하고 있는지" 보여 주는 것이기 때문이다. 결과 화면은 사용자가
  // 누를 때만 재생한다.
  if (autoplay && !autoplayedGraphs.has(svg.dataset.graphKey)) {
    autoplayedGraphs.add(svg.dataset.graphKey);
    startSimulation(svg, maxOrder, button);
  }
}

function renderGraphPanel(container, title, graph, options = {}) {
  const outcome = options.outcome || { failed: false };
  if (!graph) return;
  const panel = document.createElement("section");
  panel.className = "graph-panel";
  const heading = document.createElement("h4");
  heading.textContent = title;
  panel.append(heading);
  if (!graphProjectionIsSafe(graph)) {
    renderGraphFallback(panel, graph);
    container.append(panel);
    return;
  }
  try {
    const graphKey = `${graph.kind}:${graph.nodes.map((node) => node.id).join("|")}`;
    const controls = document.createElement("div");
    controls.className = "graph-controls";
    const viewport = document.createElement("div");
    viewport.className = "graph-viewport";
    viewport.tabIndex = 0;
    viewport.addEventListener("scroll", () => {
      if (viewport._autoScrolling) return;
      viewport._userScrollUntil = Date.now() + USER_SCROLL_PAUSE_MS;
    });
    viewport.setAttribute("aria-label", `${title} 이동 영역`);
    const canvas = document.createElement("div");
    canvas.className = "graph-canvas";
    const svg = svgNode("svg", { role: "img", "aria-label": title });
    const mobile = window.matchMedia("(max-width: 640px)").matches;
    const positions = new Map();
    const columns = new Map();
    graph.nodes.forEach((node) => {
      const column = mobile ? 0 : graphColumn(node);
      if (!columns.has(column)) columns.set(column, []);
      columns.get(column).push(node);
    });
    [...columns.values()].forEach((nodes) =>
      nodes.sort((left, right) =>
        `${left.node_type}:${left.display_name}`.localeCompare(
          `${right.node_type}:${right.display_name}`,
          "ko"
        )
      )
    );
    const orderedColumns = [...columns.keys()].sort((left, right) => left - right);
    const columnIndex = new Map(
      orderedColumns.map((column, index) => [column, index])
    );
    // 루트 위, 자식 아래로 내려가는 트리 배치. 승인된 MATCH 패턴은 한 시작 노드에서
    // 여러 갈래로 뻗는 경우가 있어(예: CurriculumVersion -> Department 와
    // CurriculumVersion -> CourseOffering) 일렬로 펴면 실제 구조가 사라진다.
    // 리프를 왼쪽부터 차례로 놓고 부모를 자식들의 가운데에 세우는 방식이다.
    const R = 27;
    const xGap = mobile ? 170 : 275;
    const yGap = mobile ? 132 : 158;

    const children = new Map();
    const indegree = new Map(graph.nodes.map((n) => [n.id, 0]));
    graph.edges.forEach((e) => {
      if (!indegree.has(e.source) || !indegree.has(e.target)) return;
      if (!children.has(e.source)) children.set(e.source, []);
      children.get(e.source).push(e.target);
      indegree.set(e.target, indegree.get(e.target) + 1);
    });
    // 루트는 들어오는 간선이 없는 노드. 없으면 방문 순서가 가장 빠른 노드를 쓴다.
    let roots = graph.nodes.filter((n) => indegree.get(n.id) === 0).map((n) => n.id);
    if (!roots.length) {
      const first = [...graph.nodes].sort(
        (a, b) => (a.visit_order || 99) - (b.visit_order || 99)
      )[0];
      roots = first ? [first.id] : [];
    }

    // 형제가 많으면 한 줄로 늘어놓지 않고 여러 줄로 접는다. 29노드 케이스에서 리프
    // 18개를 한 줄에 두면 viewBox 폭이 5,000px 을 넘어 스크롤로 감당할 수 없었다.
    // 이것은 축소가 아니라 배치 변경이므로 "축소하지 마라" 방침과 충돌하지 않는다.
    //
    // 슬롯을 되감는 방식은 서로 다른 부모의 자식이 같은 x 를 쓰게 만들어 겹쳤다.
    // 대신 서브트리가 차지하는 폭을 먼저 재고, 그 폭만큼 자리를 잡아 준다.
    // 한 줄 개수를 뷰포트 폭에서 재면 안 된다. 결과 화면 패널은 기본 접힘이라
    // clientWidth 가 0 이고, 펼쳐도 좌우 2단 그리드의 좌측 칸(약 520px)이 잡혀
    // 진행 중 화면과 결과 화면의 배치가 서로 달라졌다(2026-08-29 실측:
    // 2155×1282 vs 2101×1067). 측정 시점에 좌우되지 않는 고정 기준을 쓴다.
    // 한 줄에 4개로 고정한다. 29노드 케이스에서 실측한 결과 4개일 때 가장 좁았다.
    //   4개 → 2,101px · 6개 → 2,926px (자식이 서브트리를 가지면 줄 폭이 곱으로 는다)
    // 뷰포트 폭에서 재던 종전 방식은 접힘·그리드 때문에 화면마다 값이 달라졌다.
    const perRow = 4;

    const depth = new Map();
    const shift = new Map();   // 같은 깊이 안에서 몇 번째 줄인지(부모에서 물려받는다)
    const placed = new Set();

    // 서브트리가 쓰는 가로 슬롯 수. 자식을 여러 줄로 접으면 가장 넓은 줄이 폭이 된다.
    const spanCache = new Map();
    const measuring = new Set();
    const spanOf = (id) => {
      if (spanCache.has(id)) return spanCache.get(id);
      if (measuring.has(id)) return 1;
      measuring.add(id);
      const kids = children.get(id) || [];
      let span = 1;
      if (kids.length) {
        let widest = 0;
        for (let index = 0; index < kids.length; index += perRow) {
          const row = kids.slice(index, index + perRow);
          widest = Math.max(widest, row.reduce((sum, kid) => sum + spanOf(kid), 0));
        }
        span = Math.max(1, widest);
      }
      measuring.delete(id);
      spanCache.set(id, span);
      return span;
    };

    // 서브트리 높이(줄 수). 자식을 접은 만큼 아래 깊이가 밀린다.
    const assign = (id, level, left, rowShift) => {
      if (placed.has(id)) return;
      placed.add(id);
      depth.set(id, level);
      shift.set(id, rowShift);
      const kids = (children.get(id) || []).filter((kid) => !placed.has(kid));
      const span = spanOf(id);
      if (!kids.length) {
        positions.set(id, { x: left * xGap, y: 0 });
        return;
      }
      let rowShiftForKids = rowShift;
      for (let index = 0; index < kids.length; index += perRow) {
        const row = kids.slice(index, index + perRow);
        let cursor = left;
        row.forEach((kid) => {
          assign(kid, level + 1, cursor, rowShiftForKids);
          cursor += spanOf(kid);
        });
        // 다음 줄의 자식은 그만큼 아래로. 그 자식의 서브트리도 함께 내려간다.
        rowShiftForKids += 1;
      }
      positions.set(id, { x: (left + span / 2 - 0.5) * xGap, y: 0 });
    };
    roots.forEach((id) => assign(id, 0, 0, 0));
    graph.nodes.forEach((n) => {
      if (!placed.has(n.id)) assign(n.id, 0, 0, 0);
    });

    const maxDepth = Math.max(0, ...[...depth.values()]);
    // 깊이마다 실제로 쓴 줄 수를 세어 아래 깊이를 그만큼 밀어 준다.
    const rowsAtDepth = new Map();
    depth.forEach((level, id) => {
      rowsAtDepth.set(level, Math.max(rowsAtDepth.get(level) || 1, (shift.get(id) || 0) + 1));
    });
    const depthTop = new Map();
    let cursorY = 58;
    for (let level = 0; level <= maxDepth; level += 1) {
      depthTop.set(level, cursorY);
      cursorY += (rowsAtDepth.get(level) || 1) * yGap;
    }
    const xs = [...positions.values()].map((p) => p.x);
    const minX = Math.min(...xs, 0);
    positions.forEach((p, id) => {
      p.x = p.x - minX + 70;
      p.y = depthTop.get(depth.get(id) || 0) + (shift.get(id) || 0) * yGap;
    });
    const width = Math.max(320, Math.max(...[...positions.values()].map((p) => p.x)) + 160);
    const height = Math.max(200, cursorY + 118);
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    svg.setAttribute("preserveAspectRatio", "xMinYMin meet");
    svg.style.aspectRatio = `${width} / ${height}`;
    // 자연 크기를 CSS 에 알려 준다. 뷰포트가 더 좁으면 축소 대신 가로 스크롤이 된다.
    svg.style.setProperty("--graph-natural-width", `${width}px`);

    const definitions = svgNode("defs");
    const markerId = `arrow-${graphKey.replace(/[^a-zA-Z0-9]/g, "").slice(-20)}`;
    const marker = svgNode("marker", {
      id: markerId, markerWidth: 10, markerHeight: 10,
      refX: 9, refY: 3, orient: "auto", markerUnits: "strokeWidth",
    });
    marker.append(svgNode("path", { d: "M0,0 L0,6 L9,3 z", class: "graph-arrow" }));
    definitions.append(marker);
    svg.append(definitions);

    const edgeLayer = svgNode("g", { class: "graph-edges" });
    const nodeLayer = svgNode("g", { class: "graph-nodes" });

    const boxOf = (node) => {
      const lines = graphLabelLines(node.display_name);
      return { w: nodeBoxWidth(lines), h: lines.length > 1 ? 54 : 40 };
    };
    const nodeById = new Map(graph.nodes.map((n) => [n.id, n]));

    graph.edges.forEach((edge) => {
      const a = positions.get(edge.source);
      const b = positions.get(edge.target);
      const na = nodeById.get(edge.source);
      const nb = nodeById.get(edge.target);
      if (!a || !b || !na || !nb) return;
      const group = svgNode("g", { class: "graph-edge-group" });
      if (Number.isInteger(edge.traversal_order)) {
        group.dataset.order = String(edge.traversal_order);
      }
      // 간선은 도착 노드와 같은 층에서 켜진다.
      group.dataset.relationship = edge.relationship || "";
      group.dataset.fromId = edge.source;
      group.dataset.toId = edge.target;
      const dx = b.x - a.x, dy = b.y - a.y;
      const len = Math.hypot(dx, dy) || 1;
      const ux = dx / len, uy = dy / len;
      const trim = (box) => {
        const tx = Math.abs(ux) < 1e-6 ? Infinity : box.w / 2 / Math.abs(ux);
        const ty = Math.abs(uy) < 1e-6 ? Infinity : box.h / 2 / Math.abs(uy);
        return Math.min(tx, ty);
      };
      const ta = trim(boxOf(na)), tb = trim(boxOf(nb));
      const x1 = a.x + ux * ta, y1 = a.y + uy * ta;
      const x2 = b.x - ux * (tb + 8), y2 = b.y - uy * (tb + 8);
      group.append(svgNode("path", {
        class: "graph-edge",
        d: `M${x1},${y1} L${x2},${y2}`,
        "marker-end": `url(#${markerId})`,
      }));
      // 간선 위에는 원형 순서 배지만 상시로 둔다. 관계 이름과 실측치를 함께 그리면
      // 상자가 겹쳐 읽기 어려웠다(2026-08-29 담당자 보고). 이름은 hover 로 내린다.
      const mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
      if (Number.isInteger(edge.traversal_order)) {
        group.append(svgNode("circle", { class: "graph-edge-badge", cx: mx, cy: my, r: 12 }));
        const bt = svgNode("text", {
          class: "graph-edge-badge-text", x: mx, y: my + 4, "text-anchor": "middle",
        });
        bt.textContent = edge.traversal_order;
        group.append(bt);
      }
      const relText = edge.relationship_ko || edge.relationship;
      const measured = Number.isInteger(edge.db_hits)
        ? `${Number.isInteger(edge.rows) ? edge.rows + "행 · " : ""}DB ${edge.db_hits}회`
        : "";
      // hover 때만 보이는 관계 이름 + 실측치. 겹침을 없애면서 정보는 남긴다.
      const hoverText = measured ? `${relText} · ${measured}` : relText;
      const lw = Math.ceil(measureText(hoverText)) + 18;
      const hover = svgNode("g", { class: "graph-edge-hover" });
      hover.append(svgNode("rect", {
        class: "graph-edge-label-bg",
        x: mx - lw / 2, y: my - 34, width: lw, height: 20, rx: 6,
      }));
      const label = svgNode("text", {
        class: "graph-edge-label", x: mx, y: my - 20, "text-anchor": "middle",
      });
      label.textContent = hoverText;
      hover.append(label);
      group.append(hover);
      const title = svgNode("title");
      const share = Number.isFinite(edge.share_ms) ? ` · 배분 ${edge.share_ms}ms` : "";
      title.textContent =
        `${edge.traversal_order || ""} ${relText} (${edge.relationship})${measured ? " · " + measured : ""}${share}`.trim();
      group.append(title);
      group.setAttribute("aria-label", title.textContent);
      edgeLayer.append(group);
    });

    const selected = document.createElement("p");
    selected.className = "graph-selected";
    selected.textContent = "노드를 선택하면 공개 가능한 상세가 표시됩니다.";

    const drawOrder = [...graph.nodes].sort(
      (a, b) => (depth.get(a.id) || 0) - (depth.get(b.id) || 0)
    );
    drawOrder.forEach((node) => {
      const pos = positions.get(node.id);
      // 역할을 먼저 정한다. CSS 가 시작 노드만 큰 글자를 쓰므로 측정도 그 폰트로
      // 해야 상자가 넘치지 않는다.
      const inboundEdge = graph.edges.find((e) => e.target === node.id);
      const role = !inboundEdge
        ? "root"
        : node.node_type === "Evidence"
        ? "evidence"
        : "step";
      const lines = graphLabelLines(node.display_name, role);
      // 카테고리 = 이 노드가 속한 온톨로지 라벨의 한국어 이름. 색만으로는 어느
      // 종류인지 알 수 없어 글로도 적는다.
      const category = node.node_type_ko || "";
      const w = Math.max(
        nodeBoxWidth(lines, role),
        Math.ceil(measureText(category, role)) + 34
      );
      const h = nodeBoxHeight(lines, Boolean(category));
      const group = svgNode("g", {
        class: "graph-node", tabindex: 0, role: "button",
        "aria-label": `${node.display_name}, ${node.node_type}, ${node.verification_status}`,
        "data-kind": graphCategory(node.node_type),
        transform: `translate(${pos.x} ${pos.y})`,
      });
      if (Number.isInteger(node.visit_order)) {
        group.dataset.visit = String(node.visit_order);
      }
      // 이 노드에 어느 관계를 타고 도달했는지. 루트는 빈 문자열이라 처음부터 켜진다.
      group.dataset.reachedBy = inboundEdge ? inboundEdge.relationship || "" : "";
      // 역할에 따라 크기·테두리·형태를 달리한다. 색만으로 구분하지 않는다.
      group.dataset.role = role;
      group.dataset.nodeId = node.id;
      const tip = svgNode("title");
      // 영어 라벨은 화면에 찍지 않고 tooltip 으로만 남긴다. 노드 아래에 같이 그리면
      // 간선 라벨과 겹쳐 오히려 읽기 어려웠다.
      tip.textContent = `${node.display_name} · ${node.node_type} · ${node.verification_status}`;
      group.append(tip);
      group.append(svgNode("rect", {
        class: "graph-node-box",
        x: -w / 2, y: -h / 2, width: w, height: h, rx: 14, ry: 14,
      }));
      if (category) {
        const cat = svgNode("text", {
          class: "graph-node-category", x: 0, y: -h / 2 + 16, "text-anchor": "middle",
        });
        cat.textContent = category;
        group.append(cat);
      }
      // 카테고리 아래에서 시작해 줄 수만큼 아래로 흐른다. 상자 높이가 줄 수를
      // 따라가므로 세 줄이어도 넘치지 않는다.
      const textTop = -h / 2 + (category ? 30 : 14) + 12;
      const name = svgNode("text", {
        class: "graph-node-name", x: 0, y: textTop, "text-anchor": "middle",
      });
      lines.forEach((line, i) => {
        const ts = svgNode("tspan", { x: 0, dy: i === 0 ? 0 : 18 });
        ts.textContent = line;
        name.append(ts);
      });
      group.append(name);
      const focusRelated = (on) => {
        svg.classList.toggle("has-focus", on);
        svg.querySelectorAll("[data-relationship]").forEach((edgeEl) => {
          const related =
            edgeEl.dataset.fromId === node.id || edgeEl.dataset.toId === node.id;
          edgeEl.classList.toggle("is-related", on && related);
          edgeEl.classList.toggle("is-unrelated", on && !related);
        });
      };
      group.addEventListener("mouseenter", () => focusRelated(true));
      group.addEventListener("mouseleave", () => focusRelated(false));
      group.addEventListener("focus", () => focusRelated(true));
      group.addEventListener("blur", () => focusRelated(false));
      const select = () => {
        selected.textContent =
          `${node.display_name} · ${node.node_type} · ${node.verification_status}`;
        // B-5: 오른쪽 목록의 대응 항목을 강조하고 스크롤한다.
        // 인덱스로 맞추면 안 된다. `엔진이 실행한 순서`(operator 단계)와 노드의
        // visit_order 는 서로 다른 수열이라 번호가 겹치지 않는다. 노드에 도달한
        // 관계 이름으로 대응시킨다.
        const side = svg.closest(".graph-split")?.querySelector(".traversal-side");
        if (side) {
          const reached = group.dataset.reachedBy || "";
          side.querySelectorAll("[data-relationship]").forEach((item) => {
            const hit = Boolean(reached) && item.dataset.relationship === reached;
            item.classList.toggle("is-picked", hit);
            if (hit) item.scrollIntoView({ block: "nearest", behavior: "smooth" });
          });
        }
      };
      group.addEventListener("click", select);
      group.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          select();
        }
      });
      nodeLayer.append(group);
    });
    svg.append(edgeLayer, nodeLayer);

    // 계산한 width/height 는 노드 중심 기준이라 절반 폭과 간선 배지가 경계를
    // 넘어간다. 다 그린 뒤 실제 경계를 재서 사방에 여백을 두고 다시 잡는다.
    // 축소하지 않는다. 넘치면 가로 스크롤로 본다.
    // 접힌 <details> 안에서는 getBBox 가 0 을 주거나 던진다. 그러면 자연 폭이 낡은
    // 값으로 남아 펼쳤을 때 잘린다. 펼침·크기 변화에서 다시 잰다.
    const refitViewBox = () => {
      try {
        const box = svg.getBBox();
        if (!box || !Number.isFinite(box.width) || box.width <= 0) return;
        const pad = 28;
        const vw = Math.ceil(box.width + pad * 2);
        const vh = Math.ceil(box.height + pad * 2);
        svg.setAttribute(
          "viewBox",
          `${Math.floor(box.x - pad)} ${Math.floor(box.y - pad)} ${vw} ${vh}`
        );
        svg.style.aspectRatio = `${vw} / ${vh}`;
        svg.style.setProperty("--graph-natural-width", `${vw}px`);
      } catch (error) {
        // 화면에 붙기 전에는 던질 수 있다. 그때는 계산값을 그대로 쓰고 다음 기회에 다시 잰다.
      }
    };
    svg._refitViewBox = refitViewBox;
    requestAnimationFrame(refitViewBox);
    const fold = viewport.closest("details.exploration-fold");
    if (fold && !fold._refitBound) {
      fold._refitBound = true;
      fold.addEventListener("toggle", () => {
        if (!fold.open) return;
        fold.querySelectorAll(".graph-canvas svg").forEach((node) => {
          if (typeof node._refitViewBox === "function") {
            requestAnimationFrame(node._refitViewBox);
          }
        });
      });
    }
    canvas.append(svg);
    viewport.append(canvas);

    const setScale = (next) => {
      const scale = Math.min(2, Math.max(0.6, next));
      graphScales.set(graphKey, scale);
      canvas.style.width = `${scale * 100}%`;
      viewport.classList.toggle("is-pannable", scale > 1);
      scaleLabel.textContent = `${Math.round(scale * 100)}%`;
    };
    const control = (label, action) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "inspection-action";
      button.textContent = label;
      button.addEventListener("click", action);
      return button;
    };
    const scaleLabel = span("graph-scale", "100%");
    controls.append(
      control("축소", () => setScale((graphScales.get(graphKey) || 1) - 0.2)),
      scaleLabel,
      control("확대", () => setScale((graphScales.get(graphKey) || 1) + 0.2)),
      control("화면 맞춤", () => {
        setScale(1);
        viewport.scrollTo({ top: 0, left: 0, behavior: "smooth" });
      }),
      control("초기화", () => {
        setScale(1);
        viewport.scrollTo({ top: 0, left: 0 });
        selected.textContent = "노드를 선택하면 공개 가능한 상세가 표시됩니다.";
      })
    );
    svg.dataset.graphKey = `${container.id || "panel"}:${graphKey}`;
    // 재생 단위가 operator 개수이므로 컨트롤을 만들기 전에 계획을 실어야 한다.
    svg._operatorPlan = operatorPlan();
    addSimulationControl(controls, svg, graph, options);
    setScale(graphScales.get(graphKey) || 1);
    viewport.fitGraph = () => {
      if ((graphScales.get(graphKey) || 1) === 1) setScale(1);
      // 폭이 바뀌면 경계도 바뀐다. 접혀 있다가 펼쳐진 경우도 여기로 온다.
      if (typeof svg._refitViewBox === "function") {
        requestAnimationFrame(svg._refitViewBox);
      }
    };
    if (graphResizeObserver) graphResizeObserver.observe(viewport);

    const legend = document.createElement("ul");
    legend.className = "graph-legend";
    [
      // 화면에는 한국어만 둔다. 영어 원본 라벨은 tooltip 으로 남긴다.
      ["context", "교육과정·학과", "Curriculum · Department"],
      ["course", "교과목·편성", "Course · CourseOffering"],
      ["rule", "학사규칙·요건", "Rule · Requirement"],
      ["evidence", "원문 근거", "Evidence"],
    ].forEach(([kind, label, original]) => {
      const item = document.createElement("li");
      item.title = original;
      item.append(span(`legend-dot is-${kind}`, ""), span("", label));
      legend.append(item);
    });
    // B-2 범례. 색만으로 구분하지 않도록 각 항목에 형태 표식을 함께 둔다.
    const legendBar = document.createElement("ul");
    legendBar.className = "graph-legend-bar";
    [
      ["state", "visited", "방문함"],
      ["state", "frontier", "지금 타는 중"],
      ["state", "pending", "미방문"],
      ["role", "root", "시작 노드"],
      ["role", "step", "경유 노드"],
      ["role", "evidence", "근거(Evidence)"],
    ].forEach(([kind, key, text]) => {
      const item = document.createElement("li");
      item.dataset.kind = kind;
      item.dataset.key = key;
      const mark = document.createElement("span");
      mark.className = "legend-mark";
      mark.setAttribute("aria-hidden", "true");
      const label = document.createElement("span");
      label.textContent = text;
      item.append(mark, label);
      legendBar.append(item);
    });

    const split = document.createElement("div");
    split.className = "graph-split";
    split.append(viewport);
    const plan = svg._operatorPlan || [];
    const side = document.createElement("aside");
    side.className = "traversal-side";
    const sideHead = document.createElement("h5");
    sideHead.textContent = plan.length ? "엔진이 실행한 순서" : "방문 순서";
    side.append(sideHead);
    if (plan.length) {
      const readout = document.createElement("div");
      readout.className = "operator-readout";
      renderOperatorReadout(readout, plan, plan.length);
      side.append(readout);
    } else {
      const traversal = buildTraversalList(graph);
      if (traversal) side.append(traversal);
    }
    split.append(side);
    panel.append(controls, legendBar, split, selected, legend);
  } catch (_) {
    renderGraphFallback(panel, graph);
  }
  container.append(panel);
}

function renderAnswer(result) {
  const response = result.response;
  const presentation = result.presentation;
  el.answerBadge.textContent = presentation.status_label;
  el.answerBadge.dataset.state = response.status;
  el.answerTitle.textContent = response.answer_text;

  el.clarification.hidden = !response.clarification;
  el.clarification.textContent = response.clarification || "";
  renderTrail();
  renderChoices(response, clarificationPresentation);
  el.scopeNotice.hidden = !presentation.scope_notice;
  el.scopeNotice.textContent = presentation.scope_notice || "";
  el.debugMeta.hidden = !presentation.debug;
  el.debugMeta.textContent = presentation.debug
    ? `request_id=${presentation.debug.request_id} · error_code=${presentation.debug.error_code || "없음"}`
    : "";
  renderEvidence(presentation);
}

// 어떤 질문에서 무엇을 골라 이 답에 닿았는지 그대로 남긴다. 선택지를 여러 번 타고
// 들어가면 처음 물은 것이 무엇이었는지 잊게 되고, 그러면 답이 맞는지도 판단할 수 없다.
// 화면 표시일 뿐이며 조회 조건은 `clarify.resolved` 가 그대로 들고 있다.
function renderTrail() {
  if (!el.trail) return;
  const steps = clarify.trail || [];
  el.trail.textContent = "";
  el.trail.hidden = steps.length === 0;
  if (!steps.length) return;
  for (const step of steps) {
    const item = document.createElement("li");
    item.className = "trail-step";
    const asked = document.createElement("span");
    asked.className = "trail-asked";
    asked.textContent = step.prompt || "되물음";
    const picked = document.createElement("span");
    picked.className = "trail-picked";
    picked.textContent = step.label;
    item.append(asked, picked);
    el.trail.append(item);
  }
}

function renderChoices(response, envelope) {
  // 화면 문서가 오래됐을 수 있다. 요소가 없으면 선택지만 못 보여 줄 뿐,
  // 답변과 근거는 그대로 나와야 한다.
  if (!el.choices || !el.choiceList) return;
  const options = envelope && envelope.version === 1 && Array.isArray(envelope.options)
    ? envelope.options
    : [];
  el.choiceList.textContent = "";
  el.choices.hidden = options.length === 0;
  if (!options.length) return;
  // 지금 화면에 떠 있는 되묻기 문구. 고른 뒤에는 사라지므로 여기서 기록해 둔다.
  const prompt = response.clarification || "";
  for (const option of options) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "choice";
    button.dataset.choiceId = option.choice_id || "";
    button.textContent = option.label;
    if (option.detail && option.detail !== option.label) button.title = option.detail;
    button.addEventListener("click", () => {
      if (inFlight) return;
      // 값은 서버가 준 것을 그대로 되돌려 보낸다. 화면이 값을 만들지 않는다.
      clarify = {
        question: clarify.question,
        resolved: { ...clarify.resolved, [option.filter]: option.value },
        trail: [...(clarify.trail || []), { prompt, label: option.label }],
      };
      saveClarify(clarify);
      ask(clarify.question);
    });
    el.choiceList.append(button);
  }
}

function renderEvidence(presentation) {
  const pages = presentation.evidence_pages || [];
  pdfPageCount = Number(presentation.pdf && presentation.pdf.page_count) || 0;
  const total = pages.reduce((sum, page) => sum + page.evidence.length, 0);
  el.evidenceSection.hidden = total === 0;
  el.evidenceSummary.textContent = pages.length
    ? `발췌 PDF ${pages.length}개 페이지 · 근거 ${total}건`
    : "표시할 VERIFIED 근거가 없습니다.";
  el.pdfNotice.hidden = true;
  if (pages.length && presentation.pdf && !presentation.pdf.available) {
    showNotice(
      el.pdfNotice,
      `${presentation.pdf.reason} 페이지 번호와 Evidence 원문은 계속 표시합니다.`,
      false
    );
  }
  el.evidencePages.replaceChildren();
  pages.forEach((page) => el.evidencePages.append(pageCard(page, presentation.pdf, total)));
}

function pageLabel(value, fallback = "미표기") {
  return Number.isInteger(value) ? String(value) : fallback;
}

function pageCard(page, pdf, totalEvidence) {
  const card = document.createElement("details");
  card.className = "page-card";
  card.open = totalEvidence <= 3;

  const head = document.createElement("summary");
  head.className = "page-head";
  head.append(
    span("page-no", `발췌 PDF ${pageLabel(page.excerpt_page)}쪽`),
    span(
      "page-sub",
      `원본 PDF ${pageLabel(page.source_pdf_page)}쪽 · 인쇄 페이지 ${pageLabel(page.printed_page)}쪽`
    ),
    span("page-count", `근거 ${page.evidence.length}건 보기`)
  );

  const body = document.createElement("div");
  body.className = "page-body";
  const view = document.createElement("div");
  view.className = "page-view";
  const canvas = document.createElement("div");
  canvas.className = "page-canvas";

  if (pdf && pdf.available) {
    const img = document.createElement("img");
    img.src = `/api/pdf/page/${page.excerpt_page}.png`;
    img.alt = `발췌 PDF ${page.excerpt_page}쪽`;
    img.loading = "lazy";
    img.addEventListener("error", () => {
      canvas.replaceChildren();
      const fallback = document.createElement("p");
      fallback.className = "page-missing";
      fallback.textContent = "페이지 이미지를 표시하지 못했습니다. Evidence 원문을 확인해 주세요.";
      canvas.append(fallback);
    });
    canvas.append(img);
    page.evidence.forEach((item, index) => {
      (item.highlights || []).forEach((box) => {
        const mark = document.createElement("div");
        mark.className = "hl";
        mark.dataset.owner = String(index);
        mark.style.left = `${box.x * 100}%`;
        mark.style.top = `${box.y * 100}%`;
        mark.style.width = `${box.width * 100}%`;
        mark.style.height = `${box.height * 100}%`;
        canvas.append(mark);
      });
    });
  } else {
    const missing = document.createElement("p");
    missing.className = "page-missing";
    missing.textContent = "PDF 원본이 없어 페이지 이미지를 표시할 수 없습니다.";
    canvas.append(missing);
  }
  view.append(canvas);

  const quotes = document.createElement("div");
  quotes.className = "quotes";
  page.evidence.forEach((item, index) => {
    const quote = document.createElement("div");
    quote.className = "quote";
    quote.tabIndex = 0;
    const text = document.createElement("p");
    text.className = "quote-text";
    text.textContent = item.source_text;
    const meta = document.createElement("p");
    meta.className = "quote-meta";
    meta.append(span("", `발췌 PDF ${page.excerpt_page}쪽 · 근거 ${index + 1}`));
    if (pdf && pdf.available) {
      meta.append(
        item.highlight_found
          ? span("", `강조 ${item.highlights.length}곳`)
          : span("no-hl", "원문 위치를 찾지 못했습니다")
      );
    }
    const viewButton = document.createElement("button");
    viewButton.type = "button";
    viewButton.className = "view-source";
    viewButton.textContent = "원문에서 보기";
    viewButton.disabled = !(pdf && pdf.available);
    viewButton.addEventListener("click", () => openPdfModal(page, item));
    quote.append(text, meta, viewButton);
    const focus = (on) => {
      quote.classList.toggle("is-focus", on);
      canvas.classList.toggle("has-focus", on);
      canvas.querySelectorAll(".hl").forEach((mark) => {
        mark.classList.toggle("is-focus", on && mark.dataset.owner === String(index));
      });
    };
    quote.addEventListener("mouseenter", () => focus(true));
    quote.addEventListener("mouseleave", () => focus(false));
    quote.addEventListener("focus", () => focus(true));
    quote.addEventListener("blur", () => focus(false));
    quotes.append(quote);
  });
  body.append(view, quotes);
  card.append(head, body);
  return card;
}

function openPdfModal(page, evidence) {
  if (!pdfAvailable) return;
  modalState = { originPage: page, evidence, page: page.excerpt_page };
  modalZoom = 1;
  renderPdfModal();
  el.pdfModal.showModal();
}

function renderPdfModal() {
  if (!modalState) return;
  const { originPage, evidence, page } = modalState;
  const isOrigin = page === originPage.excerpt_page;
  el.pdfModalTitle.textContent = `발췌 PDF ${page}쪽`;
  el.pdfModalMeta.textContent = isOrigin
    ? `원본 PDF ${pageLabel(originPage.source_pdf_page)}쪽 · 인쇄 페이지 ${pageLabel(originPage.printed_page)}쪽`
    : "인접 발췌 페이지";
  el.pdfModalSource.textContent = evidence.source_text;
  el.pdfModalNotice.hidden = isOrigin && evidence.highlight_found;
  el.pdfModalNotice.textContent = isOrigin
    ? "Evidence 원문 위치를 찾지 못했습니다. 페이지와 원문은 계속 확인할 수 있습니다."
    : "선택한 Evidence의 강조 표시는 원래 발췌 페이지에서만 제공됩니다.";
  el.pdfModalCanvas.replaceChildren();
  el.pdfModalCanvas.style.width = `${modalZoom * 100}%`;
  el.pdfZoomLabel.textContent = `${Math.round(modalZoom * 100)}%`;
  const img = document.createElement("img");
  img.src = `/api/pdf/page/${page}.png`;
  img.alt = `발췌 PDF ${page}쪽`;
  img.addEventListener("error", () => {
    el.pdfModalCanvas.replaceChildren();
    const fallback = document.createElement("p");
    fallback.className = "page-missing";
    fallback.textContent = "페이지 이미지를 표시하지 못했습니다. Evidence 원문은 계속 확인할 수 있습니다.";
    el.pdfModalCanvas.append(fallback);
  });
  el.pdfModalCanvas.append(img);
  if (isOrigin) {
    (evidence.highlights || []).forEach((box) => {
      const mark = document.createElement("div");
      mark.className = "hl is-focus";
      mark.style.left = `${box.x * 100}%`;
      mark.style.top = `${box.y * 100}%`;
      mark.style.width = `${box.width * 100}%`;
      mark.style.height = `${box.height * 100}%`;
      el.pdfModalCanvas.append(mark);
    });
  }
  el.pdfPrev.disabled = page <= 1;
  el.pdfNext.disabled = pdfPageCount > 0 && page >= pdfPageCount;
}

el.pdfModalClose.addEventListener("click", () => el.pdfModal.close());
el.pdfPrev.addEventListener("click", () => {
  if (modalState && modalState.page > 1) {
    modalState.page -= 1;
    renderPdfModal();
  }
});
el.pdfNext.addEventListener("click", () => {
  if (modalState && (!pdfPageCount || modalState.page < pdfPageCount)) {
    modalState.page += 1;
    renderPdfModal();
  }
});
el.pdfZoomIn.addEventListener("click", () => {
  modalZoom = Math.min(2, modalZoom + 0.25);
  renderPdfModal();
});
el.pdfZoomOut.addEventListener("click", () => {
  modalZoom = Math.max(0.5, modalZoom - 0.25);
  renderPdfModal();
});

loadHealth();
autoGrow();
el.question.focus();
