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
let approvedInspection = null;
let expandedStages = new Set();
let graphScales = new Map();
let lastResult = null;
let clarificationPresentation = null;

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
  approvedInspection = null;
  expandedStages = new Set();
  graphScales = new Map();
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
      renderInspectionPanels();
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
    const expanded = expandedStages.has(key);
    const button = document.createElement("button");
    button.type = "button";
    button.className = "step-toggle";
    button.setAttribute("aria-expanded", String(expanded));
    button.setAttribute("aria-controls", disclosureId);
    button.append(span("step-icon", icon), span("step-label", labelText));
    button.append(
      span(
        "step-time",
        event.state !== "STARTED" && Number.isFinite(event.elapsed_ms)
          ? `${event.elapsed_ms}ms`
          : ""
      ),
      span("step-chevron", "⌄")
    );
    const disclosure = document.createElement("div");
    disclosure.id = disclosureId;
    disclosure.className = "step-disclosure";
    disclosure.hidden = !expanded;
    renderStageDetail(disclosure, event);
    button.addEventListener("click", () => {
      if (expandedStages.has(key)) expandedStages.delete(key);
      else expandedStages.add(key);
      renderTimelines();
    });
    item.append(button, disclosure);
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
  return (
    inspectionUpdates.get(inspectionKey(event.phase, event.attempt)) ||
    latestInspection(event.phase)
  );
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

function renderStageDetail(container, event) {
  addDetailFacts(container, {
    "단계 상태": {
      STARTED: "진행 중",
      COMPLETED: "완료",
      FAILED: "실패",
      CANCELLED: "취소됨",
    }[event.state] || event.state,
    "후보 시도": event.attempt > 0 ? event.attempt : null,
    "실제 소요시간": Number.isFinite(event.elapsed_ms) ? `${event.elapsed_ms}ms` : null,
    "공개 오류 코드": event.error_code || null,
  });
  const inspection = stageInspection(event);
  const summary = inspection ? inspection.summary : null;
  if (!summary) return;

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
      "Evidence 직접 경로": summary.direct_evidence_path_verified,
      "comment-free canonicalization": summary.comment_free_canonical,
      "LIMIT": summary.limit,
    });
  } else if (
    event.phase === "NEO4J_EXPLAIN" &&
    typeof summary.approved_cypher === "string"
  ) {
    addInspectionItem(
      container,
      "LLM 생성 후 안전 검증된 Cypher",
      summary.approved_cypher,
      { cypher: true }
    );
    addInspectionItem(container, "정제된 파라미터", summary.parameters);
    addInspectionItem(container, "EXPLAIN 연산자", summary.operators);
    addDetailFacts(container, {
      "사용 label": (summary.labels || []).join(", "),
      "사용 relationship": (summary.relationships || []).join(", "),
      "LIMIT": summary.limit,
    });
    renderGraphPanel(container, "질의 구조 그래프", summary.query_graph);
  } else if (event.phase === "GRAPH_EXECUTION") {
    const validation = latestInspection("RESULT_VALIDATION");
    const claims = latestInspection("CLAIM_BUILDING");
    addDetailFacts(container, {
      "반환 행": summary.row_count,
      "고유 Fact": validation ? validation.summary.fact_count : null,
      "VERIFIED Evidence": validation
        ? validation.summary.verified_evidence_count
        : null,
      "조회 시간": summary.query_elapsed_ms != null
        ? `${summary.query_elapsed_ms}ms`
        : null,
    });
    renderGraphPanel(
      container,
      "결과 provenance 그래프",
      claims ? claims.summary.provenance_graph : null
    );
  } else if (event.phase === "RESULT_VALIDATION") {
    addDetailFacts(container, {
      "VERIFIED Fact": summary.fact_count,
      "VERIFIED Evidence": summary.verified_evidence_count,
      "Fact 상태 검사": summary.fact_status_verified,
      "Evidence 상태 검사": summary.evidence_status_verified,
      "직접 provenance 검사": summary.direct_provenance_verified,
      "거부된 행": summary.rejected_row_count,
    });
  } else if (event.phase === "CLAIM_BUILDING") {
    addDetailFacts(container, {
      "Claim 수": summary.claim_count,
      "Claim 유형": Array.isArray(summary.claim_types) ? summary.claim_types.join(", ") : null,
      "집계 Claim": summary.aggregate,
      "Citation 대상": summary.citation_target_count,
    });
  } else if (event.phase === "ANSWER_RENDERING") {
    addDetailFacts(container, {
      "결정론적 한국어 renderer": summary.deterministic_renderer,
      "Citation 수": summary.citation_count,
      "최종 답변 LLM 호출": summary.final_answer_llm_calls,
    });
  } else if (event.phase === "COMPLETED") {
    addDetailFacts(container, {
      "최종 공개 status": summary.final_status,
      "전체 처리시간": summary.total_elapsed_ms != null
        ? `${summary.total_elapsed_ms}ms`
        : null,
      "재시도 횟수": summary.retry_count,
      "Citation 수": summary.citation_count,
      "request ID": queryDetailsEnabled && lastResult && lastResult.response
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
    approvedInspection = null;
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
  if (
    update.stage === "NEO4J_EXPLAIN" &&
    update.status === "COMPLETED" &&
    update.summary &&
    typeof update.summary.approved_cypher === "string"
  ) {
    approvedInspection = update.summary;
  }
  if (update.stage === "NEO4J_EXPLAIN" && update.status === "FAILED") {
    approvedInspection = null;
  }
  const attempt = Number.isInteger(update.attempt) ? update.attempt : 0;
  inspectionUpdates.set(inspectionKey(update.stage, attempt), {
    stage: update.stage,
    attempt,
    status: update.status,
    elapsed_ms: update.elapsed_ms,
    summary: update.summary || {},
  });
  renderInspectionPanels();
}

function renderInspectionPanels() {
  renderTimelines();
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

const SVG_NS = "http://www.w3.org/2000/svg";

function svgNode(name, attributes = {}) {
  const node = document.createElementNS(SVG_NS, name);
  Object.entries(attributes).forEach(([key, value]) => node.setAttribute(key, String(value)));
  return node;
}

function graphCategory(type) {
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
    !["QUERY_STRUCTURE", "RESULT_PROVENANCE"].includes(graph.kind) ||
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
      !["SCHEMA_APPROVED", "VERIFIED"].includes(node.verification_status)
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
    item.textContent = `${source.display_name} ──${edge.relationship}──> ${target.display_name}`;
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

function renderGraphPanel(container, title, graph) {
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
    viewport.setAttribute("aria-label", `${title} 이동 영역`);
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
    const nodeWidth = mobile ? 250 : 220;
    const nodeHeight = 66;
    const xGap = mobile ? 0 : 300;
    const yGap = mobile ? 104 : 96;
    let maxRows = 1;
    [...columns.entries()].forEach(([column, nodes]) => {
      maxRows = Math.max(maxRows, nodes.length);
      nodes.forEach((node, index) => {
        positions.set(node.id, {
          x: 40 + column * xGap,
          y: 44 + index * yGap,
        });
      });
    });
    const width = mobile ? 330 : Math.max(340, Math.max(...columns.keys()) * xGap + 300);
    const height = Math.max(180, maxRows * yGap + 48);
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    svg.setAttribute("width", String(width));
    svg.setAttribute("height", String(height));

    const definitions = svgNode("defs");
    const marker = svgNode("marker", {
      id: `arrow-${graphKey.replace(/[^a-zA-Z0-9]/g, "").slice(-20)}`,
      markerWidth: 10,
      markerHeight: 10,
      refX: 8,
      refY: 3,
      orient: "auto",
      markerUnits: "strokeWidth",
    });
    const arrow = svgNode("path", { d: "M0,0 L0,6 L9,3 z", class: "graph-arrow" });
    marker.append(arrow);
    definitions.append(marker);
    svg.append(definitions);

    const edgeLayer = svgNode("g", { class: "graph-edges" });
    const nodeLayer = svgNode("g", { class: "graph-nodes" });
    graph.edges.forEach((edge) => {
      const source = positions.get(edge.source);
      const target = positions.get(edge.target);
      if (!source || !target) return;
      const horizontal = !mobile && target.x !== source.x;
      const x1 = horizontal ? source.x + nodeWidth : source.x + nodeWidth / 2;
      const y1 = horizontal ? source.y + nodeHeight / 2 : source.y + nodeHeight;
      const x2 = horizontal ? target.x : target.x + nodeWidth / 2;
      const y2 = horizontal ? target.y + nodeHeight / 2 : target.y;
      const line = svgNode("line", {
        x1,
        y1,
        x2,
        y2,
        class: "graph-edge",
        "marker-end": `url(#${marker.id})`,
      });
      const label = svgNode("text", {
        x: (x1 + x2) / 2,
        y: (y1 + y2) / 2 - 6,
        class: "graph-edge-label",
        "text-anchor": "middle",
      });
      label.textContent = edge.relationship;
      edgeLayer.append(line, label);
    });

    const selected = document.createElement("p");
    selected.className = "graph-selected";
    selected.textContent = "노드를 선택하면 공개 가능한 상세가 표시됩니다.";
    graph.nodes.forEach((node) => {
      const position = positions.get(node.id);
      const group = svgNode("g", {
        class: "graph-node",
        tabindex: 0,
        role: "button",
        "aria-label": `${node.display_name}, ${node.node_type}`,
        "data-kind": graphCategory(node.node_type),
        transform: `translate(${position.x} ${position.y})`,
      });
      const box = svgNode("rect", {
        width: nodeWidth,
        height: nodeHeight,
        rx: 11,
        ry: 11,
      });
      const name = svgNode("text", { x: 12, y: 26, class: "graph-node-name" });
      name.textContent = node.display_name.length > 28
        ? `${node.display_name.slice(0, 27)}…`
        : node.display_name;
      const type = svgNode("text", { x: 12, y: 49, class: "graph-node-type" });
      type.textContent = node.node_type;
      const selectNode = () => {
        const page = Number.isInteger(node.excerpt_page)
          ? ` · 발췌 PDF ${node.excerpt_page}쪽`
          : "";
        selected.textContent = `${node.display_name} · ${node.node_type} · ${node.verification_status}${page}`;
      };
      group.addEventListener("click", selectNode);
      group.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          selectNode();
        }
      });
      group.append(box, name, type);
      nodeLayer.append(group);
    });
    svg.append(edgeLayer, nodeLayer);
    viewport.append(svg);

    const setScale = (next) => {
      const scale = Math.min(2, Math.max(0.6, next));
      graphScales.set(graphKey, scale);
      svg.style.width = `${width * scale}px`;
      svg.style.height = `${height * scale}px`;
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
    setScale(graphScales.get(graphKey) || 1);

    const legend = document.createElement("ul");
    legend.className = "graph-legend";
    [
      ["context", "Curriculum·Department"],
      ["course", "Course·CourseOffering"],
      ["rule", "Rule·Requirement"],
      ["evidence", "Evidence"],
    ].forEach(([kind, label]) => {
      const item = document.createElement("li");
      item.append(span(`legend-dot is-${kind}`, ""), span("", label));
      legend.append(item);
    });
    panel.append(controls, viewport, selected, legend);
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
