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
  progressInspectionSection: $("progress-inspection-section"),
  progressInspectionContent: $("progress-inspection-content"),
  timelineSection: $("timeline-section"),
  answerProgressSteps: $("answer-progress-steps"),
  inspectionSection: $("inspection-section"),
  inspectionContent: $("inspection-content"),
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
  el.question.style.height = `${Math.min(el.question.scrollHeight, 180)}px`;
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
  lastResult = null;
  clarificationPresentation = null;
  renderTimelines();
  el.timelineSection.hidden = true;
  el.progressInspectionSection.hidden = true;
  el.progressInspectionContent.replaceChildren();
  el.inspectionSection.hidden = true;
  el.inspectionContent.replaceChildren();
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
    const row = document.createElement("div");
    row.className = "step-row";
    row.append(span("step-icon", icon), span("step-label", labelText));
    row.append(
      span(
        "step-time",
        event.state !== "STARTED" && Number.isFinite(event.elapsed_ms)
          ? `${event.elapsed_ms}ms`
          : ""
      )
    );
    item.append(row);
    container.append(item);
  });
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
  if (!update || update.type !== "inspection_update") return;
  queryDetailsEnabled = true;
  if (update.summary && update.summary.discard_previous_candidate) {
    approvedInspection = null;
    inspectionUpdates.delete("NEO4J_EXPLAIN");
    inspectionUpdates.delete("GRAPH_EXECUTION");
    inspectionUpdates.delete("RESULT_VALIDATION");
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
  inspectionUpdates.set(update.stage, {
    status: update.status,
    elapsed_ms: update.elapsed_ms,
    summary: update.summary || {},
  });
  renderInspectionPanels();
}

function renderInspectionPanels() {
  const visible = queryDetailsEnabled && inspectionUpdates.size > 0;
  el.progressInspectionSection.hidden = !visible;
  el.inspectionSection.hidden = !visible;
  if (!visible) {
    el.progressInspectionContent.replaceChildren();
    el.inspectionContent.replaceChildren();
    return;
  }
  if (el.screens.progress.classList.contains("is-active")) {
    el.progressInspectionSection.open = true;
  }
  renderInspectionInto(el.progressInspectionContent);
  renderInspectionInto(el.inspectionContent);
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

function renderInspectionInto(container) {
  container.replaceChildren();
  const analysis = inspectionUpdates.get("QUESTION_ANALYSIS");
  const schema = inspectionUpdates.get("SCHEMA_SELECTION");
  const graph = inspectionUpdates.get("GRAPH_EXECUTION");
  const validation = inspectionUpdates.get("RESULT_VALIDATION");
  const claims = inspectionUpdates.get("CLAIM_BUILDING");
  const answer = inspectionUpdates.get("ANSWER_RENDERING");
  const completed = inspectionUpdates.get("COMPLETED");
  addInspectionItem(container, "정제된 QueryPlan", analysis && analysis.summary.query_plan);
  addInspectionItem(container, "질문 분석 상태", analysis && analysis.summary.status);
  if (schema) {
    addInspectionItem(container, "선택된 스키마", {
      labels: schema.summary.labels || [],
      relationships: schema.summary.relationships || [],
      node_label_count: schema.summary.node_label_count || 0,
      relationship_count: schema.summary.relationship_count || 0,
    });
  }
  if (approvedInspection) {
    addInspectionItem(
      container,
      "LLM 생성 후 안전 검증된 Cypher",
      approvedInspection.approved_cypher,
      { cypher: true }
    );
    addInspectionItem(container, "정제된 파라미터", approvedInspection.parameters);
    addInspectionItem(container, "EXPLAIN 연산자", approvedInspection.operators);
    addInspectionItem(container, "사용 라벨·관계와 LIMIT", {
      labels: approvedInspection.labels || [],
      relationships: approvedInspection.relationships || [],
      limit: approvedInspection.limit,
    });
  }
  if (graph || validation || claims || answer) {
    addInspectionItem(container, "조회·검증 결과", {
      row_count: graph ? graph.summary.row_count : 0,
      fact_count: validation ? validation.summary.fact_count : 0,
      verified_evidence_count: validation
        ? validation.summary.verified_evidence_count
        : 0,
      fact_status_verified: validation
        ? validation.summary.fact_status_verified
        : false,
      evidence_status_verified: validation
        ? validation.summary.evidence_status_verified
        : false,
      direct_provenance_verified: validation
        ? validation.summary.direct_provenance_verified
        : false,
      claim_count: claims ? claims.summary.claim_count : 0,
      citation_count: answer ? answer.summary.citation_count : 0,
    });
  }
  if (completed) {
    addInspectionItem(container, "단계별 시간(ms)", completed.summary.stage_timings_ms);
    addInspectionItem(container, "전체 소요시간(ms)", completed.summary.total_elapsed_ms);
  }
  if (lastResult && lastResult.response) {
    addInspectionItem(container, "요청 식별자", lastResult.response.request_id);
  }
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
