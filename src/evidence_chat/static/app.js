/* Starlette SSE client for approved ChatResponse values. */

"use strict";

const $ = (id) => document.getElementById(id);
const el = {
  status: $("status"),
  conversationToggle: $("conversation-toggle"),
  conversationPanel: $("conversation-panel"),
  conversationNew: $("conversation-new"),
  conversationClear: $("conversation-clear"),
  conversationList: $("conversation-list"),
  conversationTranscript: $("conversation-transcript"),
  jumpLatest: $("jump-latest"),
  form: $("ask-form"),
  question: $("question"),
  submit: $("submit"),
  composerStatus: $("composer-status"),
  askNotice: $("ask-notice"),
  profileForm: $("profile-form"),
  profileAdmissionYear: $("profile-admission-year"),
  profileDepartment: $("profile-department"),
  profileGrade: $("profile-grade"),
  profileAdmissionType: $("profile-admission-type"),
  profileMajorType: $("profile-major-type"),
  profileCreditTotal: $("profile-credit-total"),
  profileCreditGeneral: $("profile-credit-general"),
  profileCreditMajor: $("profile-credit-major"),
  profileCreditFree: $("profile-credit-free"),
  profileCareerGoal: $("profile-career-goal"),
  profileCourseSummary: $("profile-course-summary"),
  profileCourseList: $("profile-course-list"),
  profileReset: $("profile-reset"),
  profileNotice: $("profile-notice"),
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
let clientTimeoutMs = 180000;
let pdfPageCount = 0;
let modalState = null;
let modalZoom = 1;
let queryDetailsEnabled = false;
let timelineEvents = [];
let inspectionUpdates = new Map();
let graphScales = new Map();
let lastResult = null;
let clarificationPresentation = null;
let latestOutcome = null;
let latestConversationUpdate = null;
let latestRequestFulfillment = null;
let agentTraceEvents = [];
let currentConversationId = null;
let activeTurnId = null;
let activeAssistantMessageId = null;
let liveAssistantNode = null;
let shouldFollowLatest = true;
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

const CONVERSATION_DB = "evidence-chat-conversations";
const CONVERSATION_DB_VERSION = 3;
const CONVERSATION_STORE = "conversations";
const MESSAGE_STORE = "messages";
const CURRENT_CONVERSATION_KEY = "evidence-chat-current-conversation-v1";
const RESPONSE_STATUS_LABELS = {
  ANSWERED: "근거 확인 답변",
  NEEDS_USER_INFO: "사용자 정보 필요",
  INSUFFICIENT_EVIDENCE: "근거 부족",
  OUT_OF_SCOPE: "지원 범위 밖",
  ADVISORY: "조건부 안내",
};

function opaqueId(prefix) {
  const value = typeof crypto.randomUUID === "function"
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${prefix}:${value}`;
}

function openConversationDb() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(CONVERSATION_DB, CONVERSATION_DB_VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(CONVERSATION_STORE)) {
        db.createObjectStore(CONVERSATION_STORE, { keyPath: "conversation_id" });
      }
      if (!db.objectStoreNames.contains(MESSAGE_STORE)) {
        const store = db.createObjectStore(MESSAGE_STORE, { keyPath: "message_id" });
        store.createIndex("conversation_id", "conversation_id", { unique: false });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(new Error("대화 저장소를 열 수 없습니다."));
  });
}

async function dbRequest(storeName, mode, operation) {
  const db = await openConversationDb();
  return new Promise((resolve, reject) => {
    const transaction = db.transaction(storeName, mode);
    const request = operation(transaction.objectStore(storeName));
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(new Error("대화 저장 작업을 완료하지 못했습니다."));
    transaction.oncomplete = () => db.close();
    transaction.onerror = () => reject(new Error("대화 저장 작업을 완료하지 못했습니다."));
  });
}

async function listConversations() {
  const rows = await dbRequest(CONVERSATION_STORE, "readonly", (store) => store.getAll());
  return Array.isArray(rows)
    ? rows
      .filter((item) => item && [1, 2, 3].includes(item.version) &&
        typeof item.conversation_id === "string")
      .sort((left, right) => String(right.updated_at).localeCompare(String(left.updated_at)))
    : [];
}

async function loadConversationMessages(conversationId) {
  const rows = await dbRequest(MESSAGE_STORE, "readonly", (store) =>
    store.index("conversation_id").getAll(conversationId)
  );
  return Array.isArray(rows)
    ? rows
      .filter((item) => item && [1, 2, 3].includes(item.version) &&
        ["user", "assistant"].includes(item.role) && typeof item.content === "string")
      .sort((left, right) => String(left.created_at).localeCompare(String(right.created_at)))
    : [];
}

async function saveConversation(conversation) {
  await dbRequest(CONVERSATION_STORE, "readwrite", (store) => store.put(conversation));
}

async function saveConversationMessage(message) {
  await dbRequest(MESSAGE_STORE, "readwrite", (store) => store.put(message));
}

async function createConversation() {
  const now = new Date().toISOString();
  const conversation = {
    version: CONVERSATION_DB_VERSION,
    conversation_id: opaqueId("conversation"),
    title: "새 채팅",
    created_at: now,
    updated_at: now,
    summary: "",
    current_topic: null,
    recent_course_codes: [],
    recent_evidence_ids: [],
    pending_clarification: null,
    pending_request: null,
  };
  await saveConversation(conversation);
  currentConversationId = conversation.conversation_id;
  clearClarify();
  try { localStorage.setItem(CURRENT_CONVERSATION_KEY, currentConversationId); } catch (_) { /* no-op */ }
  await renderConversationUi();
  return conversation;
}

async function ensureConversation() {
  if (!currentConversationId) {
    try { currentConversationId = localStorage.getItem(CURRENT_CONVERSATION_KEY); } catch (_) { /* no-op */ }
  }
  if (currentConversationId) {
    const found = await dbRequest(CONVERSATION_STORE, "readonly", (store) => store.get(currentConversationId));
    if (found && [1, 2, 3].includes(found.version)) return found;
  }
  return createConversation();
}

async function renderConversationUi() {
  const conversations = await listConversations().catch(() => []);
  el.conversationList.replaceChildren();
  conversations.forEach((conversation) => {
    const item = document.createElement("li");
    const select = document.createElement("button");
    select.type = "button";
    select.className = "conversation-select";
    select.textContent = conversation.title || "새 채팅";
    select.disabled = inFlight;
    select.setAttribute("aria-current", String(conversation.conversation_id === currentConversationId));
    select.addEventListener("click", async () => {
      await saveCurrentScrollPosition();
      currentConversationId = conversation.conversation_id;
      clearClarify();
      try { localStorage.setItem(CURRENT_CONVERSATION_KEY, currentConversationId); } catch (_) { /* no-op */ }
      await renderConversationUi();
      el.conversationPanel.hidden = true;
      el.conversationToggle.setAttribute("aria-expanded", "false");
      el.question.focus();
    });
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "conversation-delete";
    remove.textContent = "삭제";
    remove.disabled = inFlight;
    remove.setAttribute("aria-label", `${conversation.title || "채팅"} 삭제`);
    remove.addEventListener("click", async () => {
      if (!window.confirm(`‘${conversation.title || "채팅"}’을 삭제할까요?`)) return;
      await deleteConversation(conversation.conversation_id);
      if (currentConversationId === conversation.conversation_id) currentConversationId = null;
      await ensureConversation();
    });
    item.append(select, remove);
    el.conversationList.append(item);
  });
  const active = currentConversationId
    ? await loadConversationMessages(currentConversationId).catch(() => [])
    : [];
  el.conversationTranscript.replaceChildren();
  active.forEach((message) => {
    el.conversationTranscript.append(renderConversationMessage(message));
  });
  if (!active.length) {
    const empty = document.createElement("p");
    empty.className = "conversation-empty";
    empty.textContent = "질문을 보내면 같은 채팅방에서 답변과 근거가 차례로 쌓입니다.";
    el.conversationTranscript.append(empty);
  }
  const selected = conversations.find((item) => item.conversation_id === currentConversationId);
  requestAnimationFrame(() => {
    el.conversationTranscript.scrollTop = Number(selected && selected.scroll_top) ||
      el.conversationTranscript.scrollHeight;
    updateJumpLatest();
  });
}

function renderConversationMessage(message) {
  const block = document.createElement("article");
  block.className = `conversation-message is-${message.role}`;
  block.dataset.turnId = message.turn_id || "";
  const role = document.createElement("strong");
  role.className = "message-role";
  role.textContent = message.role === "user" ? "나" : "학사 챗봇 답변";
  if (message.role === "assistant") role.classList.add("sr-only");
  const content = document.createElement("p");
  content.className = "message-content";
  content.textContent = message.content;
  block.append(role, content);
  if (message.role === "assistant" && message.content.length > 1800) {
    block.classList.add("is-long");
    const contentId = opaqueId("answer-content");
    content.id = contentId;
    content.classList.add("is-collapsible", "is-collapsed");
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "message-content-toggle";
    toggle.setAttribute("aria-controls", contentId);
    toggle.setAttribute("aria-expanded", "false");
    toggle.textContent = "전체 답변 펼치기";
    toggle.addEventListener("click", () => {
      const expanded = toggle.getAttribute("aria-expanded") === "true";
      toggle.setAttribute("aria-expanded", String(!expanded));
      toggle.textContent = expanded ? "전체 답변 펼치기" : "답변 접기";
      content.classList.toggle("is-collapsed", expanded);
    });
    block.append(toggle);
  }
  if (message.role === "assistant") renderAssistantDetails(block, message);
  return block;
}

function renderAssistantDetails(block, message) {
  const snapshot = message.presentation_snapshot;
  if (!snapshot || snapshot.version !== 1) {
    if (message.error) addRetryButton(block, message.source_question || "");
    return;
  }
  const result = snapshot.result;
  const response = result && result.response;
  const presentation = result && result.presentation;
  const retryable = Boolean(message.error || (response && response.status === "SAFE_FAILURE"));
  const fulfillmentStatus = snapshot.request_fulfillment &&
    snapshot.request_fulfillment.version === 1
    ? snapshot.request_fulfillment.status : null;
  const statusLabel = fulfillmentStatus === "PARTIAL" ? "일부 완료"
    : fulfillmentStatus === "UNRESOLVED" ? "확인 필요"
    : RESPONSE_STATUS_LABELS[message.response_status] || message.response_status;
  if (message.response_status || fulfillmentStatus) {
    const badge = document.createElement("span");
    badge.className = "badge turn-status";
    badge.dataset.state = fulfillmentStatus === "PARTIAL" || fulfillmentStatus === "UNRESOLVED"
      ? "INSUFFICIENT_EVIDENCE" : message.response_status;
    badge.textContent = statusLabel;
    block.insertBefore(badge, block.querySelector(".message-content"));
  }
  const normalizedMessage = String(message.content || "").replace(/\s+/g, " ").trim();
  const normalizedClarification = String(response && response.clarification || "")
    .replace(/\s+/g, " ").trim();
  if (normalizedClarification && normalizedClarification !== normalizedMessage) {
    const clarification = document.createElement("p");
    clarification.className = "message-clarification";
    clarification.textContent = response.clarification;
    block.append(clarification);
  }
  renderTurnChoices(block, message.source_question || "", response, snapshot.clarification);

  const tools = document.createElement("div");
  tools.className = "message-tools";
  if (presentation && Array.isArray(presentation.evidence_pages) && presentation.evidence_pages.length) {
    const evidenceCount = presentation.evidence_pages.reduce(
      (total, page) => total + (Array.isArray(page.evidence) ? page.evidence.length : 0), 0
    );
    tools.append(turnDisclosure(`근거 ${evidenceCount}개`, (body) =>
      renderEvidenceInto(body, presentation)));
  }
  if (Array.isArray(snapshot.timeline_events) && snapshot.timeline_events.length) {
    tools.append(turnDisclosure("처리 과정", (body) =>
      renderTimelineInto(body, snapshot.timeline_events, snapshot.inspection_updates || [])));
  }
  if (queryDetailsEnabled && Array.isArray(snapshot.inspection_updates) && snapshot.inspection_updates.length) {
    const state = explorationState(snapshot.inspection_updates);
    if (state.claims && state.claims.traversal_graph) {
      tools.append(turnDisclosure("그래프 탐색", (body) =>
        renderTurnGraph(body, state)));
    }
    if (state.explain && typeof state.explain.approved_cypher === "string") {
      tools.append(turnDisclosure("Cypher 보기", (body) =>
        renderCypherTab(body, state.explain)));
    }
  }
  if (Array.isArray(snapshot.agent_trace) && snapshot.agent_trace.length) {
    tools.append(turnDisclosure("조회 기록", (body) =>
      renderAgentTrace(body, snapshot.agent_trace)));
  }
  if (tools.childElementCount) block.append(tools);
  if (retryable) addRetryButton(block, message.source_question || "");
}

function turnDisclosure(label, renderBody) {
  const details = document.createElement("details");
  details.className = "turn-disclosure";
  const summary = document.createElement("summary");
  summary.textContent = label;
  const body = document.createElement("div");
  body.className = "turn-disclosure-body";
  details.addEventListener("toggle", () => {
    if (details.open && !body.dataset.rendered) {
      renderBody(body);
      body.dataset.rendered = "true";
    }
  });
  details.append(summary, body);
  return details;
}

function renderAgentTrace(container, events) {
  const list = document.createElement("ol");
  list.className = "agent-trace-list";
  events.forEach((event) => {
    const item = document.createElement("li");
    const label = event && typeof event.detail === "string" ? event.detail : "도구 실행";
    item.textContent = label;
    list.append(item);
  });
  container.append(list);
}

function addRetryButton(block, question) {
  if (!question) return;
  const retry = document.createElement("button");
  retry.type = "button";
  retry.className = "ghost compact retry-turn";
  retry.textContent = "이 질문 다시 시도";
  retry.addEventListener("click", () => {
    if (inFlight) return;
    clearClarify();
    clarify = emptyClarify(question);
    saveClarify(clarify);
    ask(question);
  });
  block.append(retry);
}

async function saveCurrentScrollPosition() {
  if (!currentConversationId) return;
  const conversation = await dbRequest(
    CONVERSATION_STORE, "readonly", (store) => store.get(currentConversationId)
  ).catch(() => null);
  if (!conversation) return;
  conversation.version = CONVERSATION_DB_VERSION;
  conversation.scroll_top = el.conversationTranscript.scrollTop;
  await saveConversation(conversation).catch(() => {});
}

async function deleteConversation(conversationId) {
  const messages = await loadConversationMessages(conversationId).catch(() => []);
  const db = await openConversationDb();
  await new Promise((resolve, reject) => {
    const transaction = db.transaction([CONVERSATION_STORE, MESSAGE_STORE], "readwrite");
    transaction.objectStore(CONVERSATION_STORE).delete(conversationId);
    const store = transaction.objectStore(MESSAGE_STORE);
    messages.forEach((message) => store.delete(message.message_id));
    transaction.oncomplete = resolve;
    transaction.onerror = () => reject(new Error("채팅을 삭제하지 못했습니다."));
  });
  db.close();
}

async function clearConversations() {
  if (!window.confirm("이 브라우저에 저장된 모든 채팅을 삭제할까요?")) return;
  const db = await openConversationDb();
  await new Promise((resolve, reject) => {
    const transaction = db.transaction([CONVERSATION_STORE, MESSAGE_STORE], "readwrite");
    transaction.objectStore(CONVERSATION_STORE).clear();
    transaction.objectStore(MESSAGE_STORE).clear();
    transaction.oncomplete = resolve;
    transaction.onerror = () => reject(new Error("채팅을 초기화하지 못했습니다."));
  });
  db.close();
  currentConversationId = null;
  try { localStorage.removeItem(CURRENT_CONVERSATION_KEY); } catch (_) { /* no-op */ }
  await createConversation();
}

async function beginConversationTurn(question) {
  const conversation = await ensureConversation();
  const messages = await loadConversationMessages(conversation.conversation_id);
  activeTurnId = opaqueId("turn");
  const now = new Date().toISOString();
  if (conversation.title === "새 채팅" && meaningfulConversationTitle(question)) {
    conversation.title = question.replace(/\s+/g, " ").slice(0, 36);
  }
  conversation.version = CONVERSATION_DB_VERSION;
  conversation.updated_at = now;
  await saveConversation(conversation);
  const recent = messages.slice(-8).map((message) => ({
    turn_id: message.turn_id,
    role: message.role,
    content: message.content,
    created_at: message.created_at,
    response_status: message.response_status || null,
    citation_ids: message.citation_ids || [],
    evidence_ids: message.evidence_ids || [],
  }));
  await saveConversationMessage({
    version: CONVERSATION_DB_VERSION,
    message_id: opaqueId("message"),
    conversation_id: conversation.conversation_id,
    turn_id: activeTurnId,
    role: "user",
    content: question,
    created_at: now,
    response_status: null,
    citation_ids: [],
    evidence_ids: [],
  });
  await renderConversationUi();
  scrollConversationToLatest(true);
  return {
    version: 1,
    conversation_id: conversation.conversation_id,
    turn_id: activeTurnId,
    recent_messages: recent,
    summary: conversation.summary || "",
    current_topic: conversation.current_topic || null,
    recent_course_codes: conversation.recent_course_codes || [],
    recent_evidence_ids: conversation.recent_evidence_ids || [],
    pending_clarification: conversation.pending_clarification || null,
    pending_request: conversation.pending_request || null,
  };
}

function meaningfulConversationTitle(question) {
  const normalized = question.replace(/[\s!?.,~]+/g, "");
  return normalized.length >= 4 && !/^(안녕|하이|반가워|테스트)$/.test(normalized);
}

function currentTurnSnapshot(result = lastResult, error = null) {
  return {
    version: 1,
    result,
    outcome: latestOutcome,
    clarification: clarificationPresentation,
    timeline_events: timelineEvents,
    inspection_updates: [...inspectionUpdates.values()],
    agent_trace: agentTraceEvents,
    request_fulfillment: latestRequestFulfillment,
    error,
  };
}

async function finishConversationTurn(update, snapshot = currentTurnSnapshot()) {
  if (!update || update.version !== 1 || update.conversation_id !== currentConversationId) return;
  const conversation = await ensureConversation();
  conversation.updated_at = update.created_at;
  conversation.summary = update.summary || "";
  conversation.current_topic = update.current_topic || null;
  conversation.recent_course_codes = Array.isArray(update.recent_course_codes)
    ? update.recent_course_codes : [];
  conversation.recent_evidence_ids = Array.isArray(update.evidence_ids)
    ? update.evidence_ids : [];
  conversation.pending_clarification = update.pending_clarification || null;
  conversation.pending_request = update.pending_request || null;
  conversation.version = CONVERSATION_DB_VERSION;
  await saveConversation(conversation);
  await saveConversationMessage({
    version: CONVERSATION_DB_VERSION,
    message_id: activeAssistantMessageId || opaqueId("message"),
    conversation_id: update.conversation_id,
    turn_id: update.turn_id,
    role: "assistant",
    content: update.display_answer,
    created_at: update.created_at,
    response_status: update.response_status,
    citation_ids: update.citation_ids || [],
    evidence_ids: update.evidence_ids || [],
    source_question: clarify.question || "",
    presentation_snapshot: snapshot,
  });
  await renderConversationUi();
}

async function saveFailedConversationTurn(question, message) {
  if (!currentConversationId || !activeTurnId) return;
  const now = new Date().toISOString();
  await saveConversationMessage({
    version: CONVERSATION_DB_VERSION,
    message_id: activeAssistantMessageId || opaqueId("message"),
    conversation_id: currentConversationId,
    turn_id: activeTurnId,
    role: "assistant",
    content: message,
    source_question: question,
    created_at: now,
    response_status: "SAFE_FAILURE",
    citation_ids: [],
    evidence_ids: [],
    error: true,
    presentation_snapshot: currentTurnSnapshot(null, "SAFE_FAILURE"),
  });
  await renderConversationUi();
}

el.conversationToggle.addEventListener("click", () => {
  const opened = el.conversationPanel.hidden;
  el.conversationPanel.hidden = !opened;
  el.conversationToggle.setAttribute("aria-expanded", String(opened));
});
el.conversationNew.addEventListener("click", async () => {
  if (inFlight) return;
  await saveCurrentScrollPosition();
  await createConversation();
  el.conversationPanel.hidden = true;
  el.conversationToggle.setAttribute("aria-expanded", "false");
  el.question.focus();
});
el.conversationClear.addEventListener("click", async () => {
  if (!inFlight) await clearConversations();
});

// 되묻기로 확정된 값. 서버는 대화 상태를 들지 않으므로 브라우저가 들고 매 요청에
// 함께 보낸다. 값이 실제로 제시된 선택지였는지는 서버가 다시 만들어 대조한다.
//
// `trail` 은 화면에만 쓰는 기록이다. 서버로 보내지 않으며 조회에도 관여하지 않는다.
// 무엇을 물었고 무엇을 골라 여기까지 왔는지 사용자가 되짚을 수 있게 하는 용도다.
const CLARIFY_KEY = "evidence-chat-clarify";
const PROFILE_KEY = "evidence-chat-profile-v1";
const PROFILE_VERSION = 1;

function emptyProfile() {
  return {
    version: PROFILE_VERSION,
    admission_year: null,
    curriculum_year: null,
    department_id: null,
    current_grade_year: null,
    current_semester: null,
    admission_type: null,
    major_type: null,
    completed_courses: [],
    credits: {},
    english_credentials: [],
    career_goal: null,
    note: null,
  };
}

function validProfileShape(value) {
  return value && value.version === PROFILE_VERSION &&
    Array.isArray(value.completed_courses) &&
    Array.isArray(value.english_credentials) &&
    value.credits && typeof value.credits === "object" && !Array.isArray(value.credits);
}

function loadProfile() {
  try {
    const value = JSON.parse(localStorage.getItem(PROFILE_KEY) || "null");
    return validProfileShape(value) ? value : emptyProfile();
  } catch (_) {
    return emptyProfile();
  }
}

function saveProfile(value) {
  profile = validProfileShape(value) ? value : emptyProfile();
  try {
    localStorage.setItem(PROFILE_KEY, JSON.stringify(profile));
  } catch (_) {
    showNotice(el.profileNotice, "브라우저 저장소에 사용자 정보를 저장하지 못했습니다.", true);
  }
  renderProfileForm();
}

function numericOrNull(node, minimum, maximum) {
  const raw = node.value.trim();
  if (!raw) return null;
  const value = Number(raw);
  if (!Number.isFinite(value) || value < minimum || value > maximum) {
    throw new Error(`${node.closest("label").firstChild.textContent.trim()} 값을 확인해 주세요.`);
  }
  return value;
}

function renderProfileForm() {
  if (!el.profileForm) return;
  el.profileAdmissionYear.value = profile.admission_year || "";
  el.profileDepartment.value = profile.department_id || "";
  el.profileGrade.value = profile.current_grade_year || "";
  el.profileAdmissionType.value = profile.admission_type || "";
  el.profileMajorType.value = profile.major_type || "";
  el.profileCreditTotal.value = profile.credits.total ?? "";
  el.profileCreditGeneral.value = profile.credits.general ?? "";
  el.profileCreditMajor.value = profile.credits.major ?? "";
  el.profileCreditFree.value = profile.credits.free_elective ?? "";
  el.profileCareerGoal.value = profile.career_goal || "";
  const names = profile.completed_courses.map((item) => item.name_ko).filter(Boolean);
  el.profileCourseSummary.textContent = names.length
    ? `저장된 이수 과목 ${names.length}개`
    : "채팅에서 알려 준 이수 과목이 여기에 반영됩니다.";
  el.profileCourseList.replaceChildren();
  profile.completed_courses.forEach((course) => {
    const item = document.createElement("span");
    item.className = "profile-course-item";
    item.append(span("", course.name_ko || course.course_code || "이름 없는 과목"));
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "profile-course-remove";
    remove.textContent = "삭제";
    remove.setAttribute("aria-label", `${course.name_ko || course.course_code || "과목"} 삭제`);
    remove.addEventListener("click", () => {
      saveProfile({
        ...profile,
        completed_courses: profile.completed_courses.filter(
          (candidate) => candidate.course_code !== course.course_code,
        ),
      });
      showNotice(el.profileNotice, "이수 과목을 브라우저 프로필에서 삭제했습니다.", false);
    });
    item.append(remove);
    el.profileCourseList.append(item);
  });
}

let profile = loadProfile();

if (el.profileForm) {
  el.profileForm.addEventListener("submit", (event) => {
    event.preventDefault();
    try {
      const admissionYear = numericOrNull(el.profileAdmissionYear, 1900, 9999);
      const credits = {};
      const creditInputs = {
        total: el.profileCreditTotal,
        general: el.profileCreditGeneral,
        major: el.profileCreditMajor,
        free_elective: el.profileCreditFree,
      };
      Object.entries(creditInputs).forEach(([name, node]) => {
        const value = numericOrNull(node, 0, 300);
        if (value !== null) credits[name] = value;
      });
      saveProfile({
        ...profile,
        admission_year: admissionYear,
        curriculum_year: admissionYear,
        department_id: el.profileDepartment.value || null,
        current_grade_year: numericOrNull(el.profileGrade, 1, 6),
        admission_type: el.profileAdmissionType.value || null,
        major_type: el.profileMajorType.value || null,
        credits,
        career_goal: el.profileCareerGoal.value.trim() || null,
      });
      showNotice(el.profileNotice, "이 브라우저에 사용자 정보를 저장했습니다.", false);
    } catch (error) {
      showNotice(el.profileNotice, error.message || "입력값을 확인해 주세요.", true);
    }
  });
  el.profileReset.addEventListener("click", () => {
    try { localStorage.removeItem(PROFILE_KEY); } catch (_) { /* no-op */ }
    profile = emptyProfile();
    renderProfileForm();
    showNotice(el.profileNotice, "저장된 사용자 정보를 초기화했습니다.", false);
  });
  renderProfileForm();
}

function emptyClarify(question = "") {
  return { question, resolved: {}, trail: [], conversation_id: currentConversationId };
}

function loadClarify() {
  try {
    const saved = JSON.parse(sessionStorage.getItem(CLARIFY_KEY) || "null");
    if (
      saved && typeof saved.question === "string" && saved.resolved &&
      (!saved.conversation_id || saved.conversation_id === currentConversationId)
    ) {
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

function isNearConversationBottom() {
  const remaining = el.conversationTranscript.scrollHeight -
    el.conversationTranscript.scrollTop - el.conversationTranscript.clientHeight;
  return remaining < 96;
}

function scrollConversationToLatest(force = false) {
  if (force || shouldFollowLatest || isNearConversationBottom()) {
    el.conversationTranscript.scrollTop = el.conversationTranscript.scrollHeight;
    shouldFollowLatest = true;
    el.jumpLatest.hidden = true;
  } else {
    el.jumpLatest.hidden = false;
  }
}

function updateJumpLatest() {
  shouldFollowLatest = isNearConversationBottom();
  const remaining = el.conversationTranscript.scrollHeight -
    el.conversationTranscript.scrollTop - el.conversationTranscript.clientHeight;
  el.jumpLatest.hidden = shouldFollowLatest || remaining < 240;
}

el.conversationTranscript.addEventListener("scroll", updateJumpLatest, { passive: true });
el.jumpLatest.addEventListener("click", () => scrollConversationToLatest(true));

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
    await renderConversationUi();
  } catch (_) {
    el.status.querySelector(".dot").dataset.state = "error";
    el.status.querySelector(".status-text").textContent = "서버 상태 확인 실패";
  }
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

function mountLiveAssistant() {
  const empty = el.conversationTranscript.querySelector(".conversation-empty");
  if (empty) empty.remove();
  const block = document.createElement("article");
  block.className = "conversation-message is-assistant is-pending";
  block.dataset.turnId = activeTurnId || "";
  const role = document.createElement("strong");
  role.className = "message-role";
  role.textContent = "답변 준비 중";
  const content = document.createElement("p");
  content.className = "message-content live-answer";
  content.textContent = "질문을 분석하고 있습니다…";
  const timeline = document.createElement("ol");
  timeline.className = "steps live-turn-timeline";
  timeline.setAttribute("aria-label", "현재 응답 처리 단계");
  block.append(role, content, timeline);
  el.conversationTranscript.append(block);
  liveAssistantNode = block;
  scrollConversationToLatest(true);
}

function updateLiveAssistant(message = null) {
  if (!liveAssistantNode || !liveAssistantNode.isConnected) return;
  const content = liveAssistantNode.querySelector(".live-answer");
  if (content && message) content.textContent = message;
  const timeline = liveAssistantNode.querySelector(".live-turn-timeline");
  if (timeline) renderTimelineInto(timeline, timelineEvents, [...inspectionUpdates.values()]);
  scrollConversationToLatest(false);
}

async function ask(question, displayQuestion = question) {
  inFlight = true;
  el.submit.disabled = true;
  el.form.setAttribute("aria-busy", "true");
  el.composerStatus.textContent = "답변을 확인하고 있습니다.";
  el.question.value = "";
  autoGrow();
  timelineEvents = [];
  inspectionUpdates = new Map();
  graphScales = new Map();
  lastResult = null;
  clarificationPresentation = null;
  latestOutcome = null;
  latestConversationUpdate = null;
  latestRequestFulfillment = null;
  agentTraceEvents = [];
  liveAssistantNode = null;

  activeController = new AbortController();
  const timeout = window.setTimeout(() => activeController.abort("timeout"), clientTimeoutMs);
  let result = null;
  let failed = false;
  let conversationContext = null;

  try {
    conversationContext = await beginConversationTurn(displayQuestion);
    activeAssistantMessageId = opaqueId("message");
    mountLiveAssistant();
    if (clarify.conversation_id !== conversationContext.conversation_id) {
      clearClarify();
      clarify = emptyClarify(question);
    }
    const response = await fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question,
        profile,
        conversation: conversationContext,
        ...(Object.keys(clarify.resolved).length ? { resolved: clarify.resolved } : {}),
      }),
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
          renderProgress(payload);
          updateLiveAssistant(payload.message);
        } else if (payload.type === "clarification_options") {
          clarificationPresentation = payload;
        } else if (payload.type === "profile_update" && payload.version === 1) {
          saveProfile(payload.profile);
          if (Array.isArray(payload.changed_fields) && payload.changed_fields.length) {
            showNotice(el.profileNotice, "입력한 학적 정보를 프로필에 반영했습니다.", false);
          }
        } else if (payload.type === "outcome" && payload.version === 1) {
          latestOutcome = payload;
        } else if (payload.type === "request_fulfillment" && payload.version === 1) {
          latestRequestFulfillment = payload;
        } else if (payload.type === "conversation_update" && payload.version === 1) {
          latestConversationUpdate = payload;
        } else if (payload.type === "agent_trace" && payload.version === 1) {
          agentTraceEvents.push(payload);
        } else if (payload.type === "inspection_update") {
          renderInspectionUpdate(payload);
          updateLiveAssistant();
        } else if (payload.type === "result") {
          result = payload;
          lastResult = payload;
        } else if (payload.type === "error") {
          failed = true;
          markTimelineFailed(payload.message, payload.error_code || "CHAT_REQUEST_FAILED");
          updateLiveAssistant(payload.message);
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
    updateLiveAssistant(timedOut
      ? "응답 대기 시간이 초과되었습니다. 다시 시도해 주세요."
      : "요청이 취소되었거나 연결이 종료되었습니다.");
  } finally {
    window.clearTimeout(timeout);
    activeController = null;
    inFlight = false;
    el.submit.disabled = false;
    el.form.removeAttribute("aria-busy");
    el.composerStatus.textContent = failed ? "응답을 완료하지 못했습니다." : "답변이 완료되었습니다.";
  }

  if (result) {
    try {
      if (latestConversationUpdate) {
        await finishConversationTurn(latestConversationUpdate, currentTurnSnapshot(result));
      } else {
        const response = result.response || {};
        await finishConversationTurn({
          version: 1,
          conversation_id: currentConversationId,
          turn_id: activeTurnId,
          created_at: new Date().toISOString(),
          summary: "",
          current_topic: null,
          recent_course_codes: [],
          evidence_ids: response.used_evidence_ids || [],
          pending_clarification: null,
          pending_request: null,
          display_answer: response.answer_text || "답변을 확인하지 못했습니다.",
          response_status: response.status || "SAFE_FAILURE",
          citation_ids: [],
        }, currentTurnSnapshot(result));
      }
    } catch (error) {
      await saveFailedConversationTurn(question, "답변을 화면에 저장하지 못했습니다. 다시 시도해 주세요.");
    }
  } else if (failed) {
    await saveFailedConversationTurn(question, "요청을 완료하지 못했습니다. 이 질문을 다시 시도할 수 있습니다.");
  }
  liveAssistantNode = null;
  activeAssistantMessageId = null;
  scrollConversationToLatest(true);
  el.question.focus();
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
  updateLiveAssistant();
}

function asInspectionMap(updates = inspectionUpdates) {
  if (updates instanceof Map) return updates;
  const mapped = new Map();
  (Array.isArray(updates) ? updates : []).forEach((item) => {
    if (item && typeof item.stage === "string") {
      mapped.set(inspectionKey(item.stage, item.attempt), item);
    }
  });
  return mapped;
}

function renderTimelineInto(container, events = timelineEvents, updates = inspectionUpdates) {
  if (!container.id) container.id = opaqueId("timeline").replace(/:/g, "-");
  const updateMap = asInspectionMap(updates);
  container.replaceChildren();
  events.forEach((event, eventIndex) => {
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
    const disclosureId = `${container.id || "turn"}-${event.phase.toLowerCase()}-${event.attempt}-${eventIndex}`;
    const hasDetails = stageHasDetails(event, updateMap);
    const expanded = false;
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
        false,
        updateMap
      );
      row.addEventListener("click", () => {
        disclosure.hidden = !disclosure.hidden;
        row.setAttribute("aria-expanded", String(!disclosure.hidden));
      });
      item.append(disclosure);
    }
    container.append(item);
  });
}

function inspectionKey(stage, attempt = 0) {
  return `${stage}:${Number.isInteger(attempt) ? attempt : 0}`;
}

function latestInspection(stage, updates = inspectionUpdates) {
  const updateMap = asInspectionMap(updates);
  return [...updateMap.values()].reverse().find((item) => item.stage === stage) || null;
}

function stageInspection(event, updates = inspectionUpdates) {
  const updateMap = asInspectionMap(updates);
  if (event.attempt > 0) {
    return updateMap.get(inspectionKey(event.phase, event.attempt)) || null;
  }
  return updateMap.get(inspectionKey(event.phase, 0)) || null;
}

function stageHasDetails(event, updates = inspectionUpdates) {
  const inspection = stageInspection(event, updates);
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

function addFiveWOneH(container, phase, summary) {
  const facts = {
    QUESTION_ANALYSIS: {
      "누가·대상": "현재 사용자 질문",
      "무엇을": "질의 의도와 필요한 조회 필드를 구조화",
      "언제": summary.query_plan?.filters?.academic_year
        ? `${summary.query_plan.filters.academic_year}학년도`
        : null,
      "어디에서": summary.query_plan?.filters?.department_id || "지원 교육과정 범위",
      "왜": "질문에 직접 답하는 검증 가능한 사실을 찾기 위해",
      "어떻게": "검증된 QueryPlan 계약으로 정제",
    },
    SCHEMA_SELECTION: {
      "누가·대상": `${summary.node_label_count || 0}개 노드 유형과 ${summary.relationship_count || 0}개 관계 유형`,
      "무엇을": "질문에 필요한 온톨로지 부분만 선택",
      "어디에서": "공개 온톨로지 명세",
      "왜": "허용되지 않은 스키마 접근을 막기 위해",
      "어떻게": "QueryPlan 요청 필드와 Evidence 경로에 맞춰 선택",
    },
    CYPHER_GENERATION: {
      "누가·대상": `질의 후보 ${summary.candidate_attempt || 1}차`,
      "무엇을": "읽기 전용 Cypher 후보 생성",
      "왜": "선택한 지식그래프 구조를 조회하기 위해",
      "어떻게": summary.retry ? "이전 후보를 폐기하고 다시 생성" : "구조화된 계획으로 생성",
    },
    STATIC_VALIDATION: {
      "누가·대상": "현재 Cypher 후보",
      "무엇을": "읽기 전용·스키마·파라미터·Evidence 경로 검사",
      "왜": "안전하지 않거나 근거 없는 질의를 실행하지 않기 위해",
      "어떻게": "comment-free canonical 질의에 정적 검증 적용",
    },
    NEO4J_EXPLAIN: {
      "누가·대상": "정적 검증을 통과한 동일 후보",
      "무엇을": "Neo4j 실행 계획 승인",
      "어디에서": "로컬 Neo4j의 EXPLAIN",
      "왜": "실행 전에 읽기 계획과 사용 스키마를 확인하기 위해",
      "어떻게": `${Array.isArray(summary.operators) ? summary.operators.length : 0}개 실행 연산자 확인`,
    },
    GRAPH_EXECUTION: {
      "누가·대상": `${summary.row_count || 0}개 반환 행`,
      "무엇을": "승인된 질의를 읽기 전용으로 실행",
      "어디에서": "검증된 교육과정 지식그래프",
      "왜": "질문에 대응하는 Fact와 Evidence를 찾기 위해",
      "어떻게": `${Array.isArray(summary.traversal_steps) ? summary.traversal_steps.length : 0}개 PROFILE 관찰 단계`,
    },
    RESULT_VALIDATION: {
      "누가·대상": `${summary.fact_count || 0}개 Fact와 ${summary.verified_evidence_count || 0}개 Evidence`,
      "무엇을": "검증 상태와 직접 provenance 확인",
      "왜": "근거와 직접 연결된 VERIFIED 사실만 답변에 사용하기 위해",
      "어떻게": "Fact·Evidence 상태와 직접 연결을 교차 검증",
    },
    CLAIM_BUILDING: {
      "누가·대상": `${summary.claim_count || 0}개 Claim`,
      "무엇을": "검증된 조회 결과를 구조화된 주장으로 구성",
      "왜": "각 주장과 Citation을 대응시키기 위해",
      "어떻게": `${summary.citation_target_count || 0}개 Citation 대상을 연결`,
    },
    ANSWER_RENDERING: {
      "누가·대상": "검증된 Claim",
      "무엇을": "사용자용 한국어 답변 구성",
      "왜": "검증된 범위만 자연스럽게 설명하기 위해",
      "어떻게": `${summary.citation_count || 0}개 Citation을 유지한 renderer 사용`,
    },
    COMPLETED: {
      "누가·대상": "현재 assistant 턴",
      "무엇을": `${summary.final_status || "처리 완료"} 상태로 완료`,
      "언제": Number.isFinite(summary.total_elapsed_ms)
        ? `${summary.total_elapsed_ms}ms 후`
        : null,
      "왜": "검증된 결과를 사용자에게 전달하기 위해",
      "어떻게": `${summary.retry_count || 0}회 재시도, ${summary.citation_count || 0}개 Citation`,
    },
  }[phase];
  if (!facts) return;
  const section = document.createElement("section");
  section.className = "stage-fivewoneh";
  const title = document.createElement("h4");
  title.textContent = "처리 요약";
  section.append(title);
  addDetailFacts(section, facts);
  container.append(section);
}

function renderStageDetail(container, event, allowExplorationLinks, updates = inspectionUpdates) {
  const inspection = stageInspection(event, updates);
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
    const labelNames = summary.label_names_ko || {};
    const relationshipNames = summary.relationship_names_ko || {};
    addDetailFacts(container, {
      "선택 node label 수": summary.node_label_count,
      "선택 relationship 수": summary.relationship_count,
      "선택 이유": "검증된 QueryPlan의 필드와 Evidence 경로에 필요한 구조입니다.",
    });
    addInspectionItem(
      container,
      "선택 노드 유형",
      (summary.labels || []).map((value) => `${labelNames[value] || value} (${value})`)
    );
    addInspectionItem(
      container,
      "선택 관계 유형",
      (summary.relationships || []).map(
        (value) => `${relationshipNames[value] || value} (${value})`
      )
    );
    if (allowExplorationLinks) {
      addExplorationLink(container, "선택 스키마 보기", "schema");
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
      "Evidence 직접 경로": summary.direct_evidence_path_verified,
      "comment-free canonicalization": summary.comment_free_canonical,
      "LIMIT": summary.limit,
    });
  } else if (
    event.phase === "NEO4J_EXPLAIN" &&
    typeof summary.approved_cypher === "string"
  ) {
    addDetailFacts(container, {
      "EXPLAIN 연산자": (summary.operators || []).join(", "),
      "LIMIT": summary.limit,
    });
    if (allowExplorationLinks) {
      addExplorationLink(container, "승인 Cypher 보기", "cypher");
    }
  } else if (event.phase === "GRAPH_EXECUTION") {
    const validation = latestInspection("RESULT_VALIDATION", updates);
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
    if (Array.isArray(summary.traversal_steps) && summary.traversal_steps.length) {
      const list = document.createElement("ol");
      list.className = "operator-list";
      summary.traversal_steps.forEach((step) => {
        const item = document.createElement("li");
        const title = document.createElement("strong");
        title.textContent = step.explanation_ko || step.operator || "Neo4j 실행 단계";
        const metrics = document.createElement("span");
        const share = Number.isFinite(step.share_ms) ? ` · 배분 ${step.share_ms}ms` : "";
        metrics.textContent = `행 ${step.rows || 0} · DB 접근 ${step.db_hits || 0}${share}`;
        item.append(title, metrics);
        list.append(item);
      });
      container.append(list);
    }
    if (allowExplorationLinks) {
      addExplorationLink(container, "조회 그래프 보기", "graph");
    }
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
      "sealed canonical Claim renderer": summary.deterministic_renderer,
      "Citation 수": summary.citation_count,
      "sealed canonical 생성 LLM 호출": summary.final_answer_llm_calls,
    });
  } else if (event.phase === "COMPLETED") {
    addDetailFacts(container, {
      "최종 공개 status": summary.final_status,
      "전체 처리시간": summary.total_elapsed_ms != null
        ? `${summary.total_elapsed_ms}ms`
        : null,
      "재시도 횟수": summary.retry_count,
      "Citation 수": summary.citation_count,
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
}

function addExplorationLink(container, label, tab) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "stage-jump";
  button.textContent = label;
  button.addEventListener("click", () => {
    const target = container.closest(".conversation-message")?.querySelector(".exploration");
    if (!target) return;
    target.dataset.activeTab = tab;
    target.scrollIntoView({ behavior: "smooth", block: "nearest" });
  });
  container.append(button);
}

function explorationState(updates = inspectionUpdates) {
  const schema = latestInspection("SCHEMA_SELECTION", updates);
  const explain = latestInspection("NEO4J_EXPLAIN", updates);
  const claims = latestInspection("CLAIM_BUILDING", updates);
  return {
    schema: schema && schema.status === "COMPLETED" ? schema.summary : null,
    explain: explain && explain.status === "COMPLETED" ? explain.summary : null,
    claims: claims && claims.status === "COMPLETED" ? claims.summary : null,
  };
}

function renderTurnGraph(container, state) {
  const heading = document.createElement("p");
  heading.className = "projection-note";
  heading.textContent =
    "이 답변에서 실제 승인된 Cypher와 검증 결과로 확인한 traversal만 표시합니다.";
  container.append(heading);
  renderGraphTab(container, state, true);
}

function renderExplorationPanel(container, updates = inspectionUpdates, result = lastResult) {
  if (graphResizeObserver) {
    container.querySelectorAll(".graph-viewport").forEach((viewport) => {
      graphResizeObserver.unobserve(viewport);
    });
  }
  container.replaceChildren();
  container.hidden = !queryDetailsEnabled;
  if (!queryDetailsEnabled) return;

  if (!container.id) container.id = opaqueId("exploration").replace(/:/g, "-");
  const state = explorationState(updates);
  const availability = {
    schema: Boolean(
      state.schema &&
      Array.isArray(state.schema.labels) &&
      state.schema.labels.length
    ),
    cypher: Boolean(state.explain && typeof state.explain.approved_cypher === "string"),
    graph: Boolean(
      state.explain &&
      state.explain.query_graph &&
      state.claims &&
      state.claims.provenance_graph
    ),
  };
  const availableTabs = Object.keys(availability).filter((key) => availability[key]);
  let selectedTab = container.dataset.activeTab || "schema";
  if (!availability[selectedTab] && availableTabs.length) {
    selectedTab = availableTabs[0];
    container.dataset.activeTab = selectedTab;
  }

  const head = document.createElement("div");
  head.className = "exploration-head";
  const title = document.createElement("h3");
  title.textContent = "지식그래프 탐색";
  const description = document.createElement("p");
  description.textContent =
    "처리가 끝난 뒤 실제 파이프라인에서 승인된 정적 조회 정보만 표시합니다.";
  head.append(title, description);

  const tabs = document.createElement("div");
  tabs.className = "exploration-tabs";
  tabs.setAttribute("role", "tablist");
  tabs.setAttribute("aria-label", "질의 추적 상세");
  const labels = {
    schema: "선택 스키마",
    cypher: "승인 Cypher",
    graph: "조회 그래프",
  };
  Object.entries(labels).forEach(([key, label]) => {
    const button = document.createElement("button");
    button.type = "button";
    button.id = `${container.id}-tab-${key}`;
    button.setAttribute("role", "tab");
    button.setAttribute("aria-selected", String(selectedTab === key));
    button.setAttribute("aria-controls", `${container.id}-panel`);
    button.disabled = !availability[key];
    button.title = availability[key]
      ? `${label} 보기`
      : "아직 해당 단계가 완료되지 않았습니다";
    button.textContent = label;
    button.addEventListener("click", () => {
      container.dataset.activeTab = key;
      renderExplorationPanel(container, updates, result);
    });
    tabs.append(button);
  });

  const pending = document.createElement("p");
  pending.className = "exploration-pending";
  pending.setAttribute("role", "status");
  const waiting = Object.entries(labels)
    .filter(([key]) => !availability[key])
    .map(([, label]) => label);
  pending.textContent = waiting.length
    ? `${waiting.join(" · ")}: 아직 해당 단계가 완료되지 않았습니다.`
    : "모든 추적 정보가 실제 승인 단계까지 완료되었습니다.";

  const panel = document.createElement("div");
  panel.id = `${container.id}-panel`;
  panel.className = "exploration-panel";
  panel.setAttribute("role", "tabpanel");
  panel.setAttribute("aria-labelledby", `${container.id}-tab-${selectedTab}`);
  if (!availableTabs.length) {
    const empty = document.createElement("p");
    empty.className = "exploration-empty";
    empty.textContent = "안전하게 공개할 수 있는 승인 정보가 없습니다.";
    panel.append(empty);
  } else if (selectedTab === "schema") {
    renderSchemaTab(panel, state.schema);
  } else if (selectedTab === "cypher") {
    renderCypherTab(panel, state.explain);
  } else {
    renderGraphTab(panel, state);
  }
  container.append(head, tabs, pending, panel);
}

function addBadges(container, title, values, kind) {
  if (!Array.isArray(values) || !values.length) return;
  const group = document.createElement("section");
  group.className = "badge-group";
  const heading = document.createElement("h4");
  heading.textContent = title;
  const list = document.createElement("ul");
  values.forEach((value) => {
    const item = document.createElement("li");
    item.className = `schema-badge is-${kind}`;
    item.textContent = value;
    list.append(item);
  });
  group.append(heading, list);
  container.append(group);
}

function renderSchemaTab(container, summary) {
  if (!summary) return;
  addBadges(
    container,
    "선택된 노드 유형",
    (summary.labels || []).map(
      (value) => `${summary.label_names_ko?.[value] || value} (${value})`
    ),
    "node"
  );
  addBadges(
    container,
    "선택된 관계 유형",
    (summary.relationships || []).map(
      (value) => `${summary.relationship_names_ko?.[value] || value} (${value})`
    ),
    "relationship"
  );
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
    "LIMIT": summary.limit,
  });
}

function renderGraphTab(container, state, autoplay = false) {
  const note = document.createElement("p");
  note.className = "projection-note";
  note.textContent =
    "현재 질문에 대해 실제 승인된 구조와 VERIFIED provenance만 표시한 projection입니다.";
  container.append(note);
  if (state.claims && state.claims.traversal_graph) {
    const graph = state.claims.traversal_graph;
    if ((graph.nodes || []).length > 100) {
      renderLargeGraphSummary(container, graph, { autoplay });
    } else {
      renderGraphPanel(
        container,
        "실제 질의 traversal과 VERIFIED 근거",
        graph,
        { autoplay }
      );
    }
  } else if (state.explain && state.explain.query_graph) {
    renderGraphPanel(container, "승인된 질의 구조", state.explain.query_graph);
  }
  if (
    !state.claims?.traversal_graph &&
    state.claims &&
    state.claims.provenance_graph
  ) {
    renderGraphPanel(
      container,
      "조회 결과와 VERIFIED Evidence",
      state.claims.provenance_graph
    );
  }
}

function renderLargeGraphSummary(container, graph, options = {}) {
  const panel = document.createElement("section");
  panel.className = "graph-panel graph-summary-panel";
  const heading = document.createElement("h4");
  heading.textContent = "조회 결과 요약";
  const groups = new Map();
  graph.nodes
    .filter((node) => node.node_type !== "Evidence" && node.group_name)
    .forEach((node) => groups.set(node.group_name, (groups.get(node.group_name) || 0) + 1));
  const description = document.createElement("p");
  description.textContent =
    `검증된 결과 ${[...groups.values()].reduce((sum, count) => sum + count, 0)}개를 ` +
    `${groups.size}개 영역으로 요약했습니다. 전체 노드와 근거는 필요할 때만 그립니다.`;
  const list = document.createElement("ul");
  list.className = "graph-group-summary";
  groups.forEach((count, name) => {
    const item = document.createElement("li");
    item.textContent = `${name} · ${count}과목`;
    list.append(item);
  });
  const show = document.createElement("button");
  show.type = "button";
  show.className = "ghost compact";
  show.textContent = "전체 노드 표시";
  const target = document.createElement("div");
  show.addEventListener("click", () => {
    show.disabled = true;
    show.textContent = "전체 노드를 표시했습니다";
    renderGraphPanel(target, "실제 질의 traversal과 VERIFIED 근거", graph, options);
  }, { once: true });
  panel.append(heading, description, list, show, target);
  container.append(panel);
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

function graphRelationshipCategory(relationship) {
  if (relationship === "SUPPORTED_BY" || relationship === "FROM_DOCUMENT") return "evidence";
  if (["OF_COURSE", "REQUIRES_COURSE", "HAS_OFFERING"].includes(relationship)) return "course";
  if (["HAS_RULE", "APPLIES_TO", "REQUIRES_CREDITS"].includes(relationship)) return "rule";
  return "other";
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
    graph.nodes.length > 650 ||
    graph.edges.length > 800
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
      !(
        node.group_name === undefined ||
        node.group_name === null ||
        typeof node.group_name === "string"
      ) ||
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
    item.textContent = `${source.display_name} ──${edge.relationship_ko || edge.relationship}──> ${target.display_name}`;
    list.append(item);
  });
  if (!list.childElementCount) {
    (graph.nodes || []).forEach((node) => {
      const item = document.createElement("li");
      item.textContent = `${node.display_name} (${node.node_type_ko || node.node_type})`;
      list.append(item);
    });
  }
  fallback.append(list);
  container.append(fallback);
}

function graphLabelLines(value) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  if (text.length <= 18) return [text];
  const remainder = text.slice(18);
  return [
    text.slice(0, 18),
    remainder.length > 18 ? `${remainder.slice(0, 17)}…` : remainder,
  ];
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

function buildTraversalList(graph) {
  const nodes = new Map((graph.nodes || []).map((node) => [node.id, node]));
  const edges = (graph.edges || [])
    .filter((edge) => Number.isInteger(edge.traversal_order))
    .sort((left, right) => left.traversal_order - right.traversal_order);
  if (!edges.length) return null;
  const section = document.createElement("section");
  section.className = "traversal-summary";
  const heading = document.createElement("h5");
  heading.textContent = "실제 traversal 순서";
  const list = document.createElement("ol");
  list.className = "traversal-list";
  edges.forEach((edge) => {
    const source = nodes.get(edge.source);
    const target = nodes.get(edge.target);
    if (!source || !target) return;
    const item = document.createElement("li");
    item.dataset.order = String(edge.traversal_order);
    const route = document.createElement("strong");
    route.textContent =
      `${source.display_name} → ${edge.relationship_ko || edge.relationship} → ${target.display_name}`;
    const metrics = document.createElement("span");
    const values = [];
    if (Number.isInteger(edge.rows)) values.push(`행 ${edge.rows}`);
    if (Number.isInteger(edge.db_hits)) values.push(`DB 접근 ${edge.db_hits}`);
    if (Number.isFinite(edge.share_ms)) values.push(`배분 ${edge.share_ms}ms`);
    metrics.textContent = values.join(" · ");
    item.append(route, metrics);
    list.append(item);
  });
  section.append(heading, list);
  return section;
}

function addTraversalControls(controls, svg, graph, options = {}) {
  const orders = [...new Set(
    (graph.edges || [])
      .map((edge) => edge.traversal_order)
      .filter((value) => Number.isInteger(value))
  )].sort((left, right) => left - right);
  if (!orders.length) return;
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const status = span("traversal-status", "");
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  const replay = document.createElement("button");
  replay.type = "button";
  replay.className = "inspection-action";
  replay.textContent = "탐색 순서 재생";

  const clearTimers = () => {
    (svg.traversalTimers || []).forEach((timer) => window.clearTimeout(timer));
    svg.traversalTimers = [];
  };
  const showThrough = (order) => {
    svg.querySelectorAll(".graph-edge-group[data-order]").forEach((edge) => {
      const value = Number(edge.dataset.order);
      edge.classList.toggle("is-traversed", value <= order);
      edge.classList.toggle("is-active", value === order);
    });
    svg.querySelectorAll(".graph-node[data-order]").forEach((node) => {
      node.classList.toggle("is-traversed", Number(node.dataset.order) <= order + 1);
    });
    const panel = svg.closest(".graph-panel");
    panel?.querySelectorAll(".traversal-list li[data-order]").forEach((item) => {
      const value = Number(item.dataset.order);
      item.classList.toggle("is-traversed", value <= order);
      item.classList.toggle("is-active", value === order);
    });
  };
  const play = () => {
    clearTimers();
    svg.classList.add("shows-traversal");
    if (reduced) {
      showThrough(orders.at(-1));
      status.textContent = "동작 줄이기 설정에 따라 전체 탐색 순서를 표시했습니다.";
      return;
    }
    showThrough(0);
    status.textContent = "승인된 traversal 순서를 재생합니다.";
    replay.disabled = true;
    orders.forEach((order, index) => {
      const timer = window.setTimeout(() => {
        showThrough(order);
        status.textContent = `${order}번째 실제 탐색 단계를 표시했습니다.`;
        if (index === orders.length - 1) replay.disabled = false;
      }, 420 * (index + 1));
      svg.traversalTimers.push(timer);
    });
  };
  replay.addEventListener("click", play);
  controls.append(replay, status);
  if (options.autoplay) window.requestAnimationFrame(play);
}

function renderGraphPanel(container, title, graph, options = {}) {
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
    const nodeWidth = mobile ? 244 : 218;
    const nodeHeight = 86;
    const xGap = mobile ? 0 : 290;
    const yGap = mobile ? 132 : 118;
    let maxRows = 1;
    [...columns.entries()].forEach(([column, nodes]) => {
      maxRows = Math.max(maxRows, nodes.length);
      nodes.forEach((node, index) => {
        positions.set(node.id, {
          x: 52 + columnIndex.get(column) * xGap,
          y: 52 + index * yGap,
        });
      });
    });
    const width = mobile ? 348 : Math.max(348, orderedColumns.length * xGap + 44);
    const height = Math.max(210, maxRows * yGap + 56);
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    svg.setAttribute("preserveAspectRatio", "xMidYMin meet");
    svg.style.aspectRatio = `${width} / ${height}`;

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
    graph.edges.forEach((edge, index) => {
      const source = positions.get(edge.source);
      const target = positions.get(edge.target);
      if (!source || !target) return;
      const geometry = graphEdgeGeometry(
        source,
        target,
        nodeWidth,
        nodeHeight,
        mobile,
        ((index % 3) - 1) * 14
      );
      const edgeGroup = svgNode("g", {
        class: "graph-edge-group",
        tabindex: Number.isInteger(edge.traversal_order) ? 0 : -1,
        "data-kind": graphRelationshipCategory(edge.relationship),
      });
      if (Number.isInteger(edge.traversal_order)) {
        edgeGroup.dataset.order = String(edge.traversal_order);
        edgeGroup.setAttribute(
          "aria-label",
          `${edge.traversal_order}번째, ${edge.relationship_ko || edge.relationship}`
        );
      }
      const path = svgNode("path", {
        d: geometry.path,
        class: "graph-edge",
        "marker-end": `url(#${marker.id})`,
      });
      const relationshipText = edge.relationship_ko || edge.relationship;
      const relationshipLabel = relationshipText.length > 22
        ? `${relationshipText.slice(0, 21)}…`
        : relationshipText;
      const labelWidth = Math.min(
        178,
        Math.max(70, relationshipLabel.length * 7 + 18)
      );
      const labelX = Math.min(
        width - labelWidth / 2 - 8,
        Math.max(labelWidth / 2 + 8, geometry.labelX)
      );
      const labelGroup = svgNode("g", { class: "graph-edge-label-group" });
      const labelTitle = svgNode("title");
      labelTitle.textContent = `${relationshipText} (${edge.relationship})`;
      const labelBox = svgNode("rect", {
        x: labelX - labelWidth / 2,
        y: geometry.labelY - 13,
        width: labelWidth,
        height: 22,
        rx: 6,
        ry: 6,
      });
      const label = svgNode("text", {
        x: labelX,
        y: geometry.labelY + 2,
        class: "graph-edge-label",
        "text-anchor": "middle",
      });
      label.textContent = relationshipLabel;
      labelGroup.append(labelTitle, labelBox, label);
      edgeGroup.append(path, labelGroup);
      if (Number.isInteger(edge.traversal_order)) {
        const badge = svgNode("circle", {
          cx: labelX - labelWidth / 2 - 11,
          cy: geometry.labelY - 2,
          r: 9,
          class: "graph-order-badge",
        });
        const badgeText = svgNode("text", {
          x: labelX - labelWidth / 2 - 11,
          y: geometry.labelY + 2,
          class: "graph-order-text",
          "text-anchor": "middle",
        });
        badgeText.textContent = String(edge.traversal_order);
        edgeGroup.append(badge, badgeText);
      }
      edgeLayer.append(edgeGroup);
    });

    const selected = document.createElement("p");
    selected.className = "graph-selected";
    selected.textContent = "노드를 선택하면 공개 가능한 상세가 표시됩니다.";
    graph.nodes.forEach((node) => {
      const position = positions.get(node.id);
      const nodeTypeKo = node.node_type_ko || node.node_type;
      const group = svgNode("g", {
        class: "graph-node",
        tabindex: 0,
        role: "button",
        "aria-label": `${node.display_name}, ${nodeTypeKo}, ${node.verification_status}`,
        "data-kind": graphCategory(node.node_type),
        transform: `translate(${position.x} ${position.y})`,
      });
      if (Number.isInteger(node.visit_order)) {
        group.dataset.order = String(node.visit_order);
      }
      const visual = svgNode("g", { class: "graph-node-visual" });
      const tooltip = svgNode("title");
      tooltip.textContent = `${node.display_name} · ${nodeTypeKo} (${node.node_type})`;
      const box = svgNode("rect", {
        width: nodeWidth,
        height: nodeHeight,
        rx: 11,
        ry: 11,
      });
      const name = svgNode("text", { x: 12, y: 25, class: "graph-node-name" });
      graphLabelLines(node.display_name).forEach((line, lineIndex) => {
        const textLine = svgNode("tspan", {
          x: 12,
          dy: lineIndex === 0 ? 0 : 18,
        });
        textLine.textContent = line;
        name.append(textLine);
      });
      const type = svgNode("text", { x: 12, y: 69, class: "graph-node-type" });
      type.textContent = nodeTypeKo;
      const verified = svgNode("text", {
        x: nodeWidth - 14,
        y: 20,
        class: "graph-node-check",
        "text-anchor": "end",
      });
      verified.textContent = node.citation_used ? "✓ 근거" : "✓";
      const selectNode = () => {
        const page = Number.isInteger(node.excerpt_page)
          ? ` · 발췌 PDF ${node.excerpt_page}쪽`
          : "";
        selected.textContent = `${node.display_name} · ${nodeTypeKo} (${node.node_type}) · ${node.verification_status}${page}`;
      };
      group.addEventListener("click", selectNode);
      group.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          selectNode();
        }
      });
      visual.append(box, name, type, verified);
      group.append(tooltip, visual);
      nodeLayer.append(group);
    });
    svg.append(edgeLayer, nodeLayer);
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
    addTraversalControls(controls, svg, graph, options);
    setScale(graphScales.get(graphKey) || 1);
    viewport.fitGraph = () => {
      if ((graphScales.get(graphKey) || 1) === 1) setScale(1);
    };
    if (graphResizeObserver) graphResizeObserver.observe(viewport);

    const legend = document.createElement("ul");
    legend.className = "graph-legend";
    [
      ["context", "교육과정·학과"],
      ["course", "과목·과목 개설 정보"],
      ["rule", "규칙·이수요건"],
      ["evidence", "문서 근거"],
    ].forEach(([kind, label]) => {
      const item = document.createElement("li");
      item.append(span(`legend-dot is-${kind}`, ""), span("", label));
      legend.append(item);
    });
    panel.append(controls, viewport, selected, legend);
    const traversalList = buildTraversalList(graph);
    if (traversalList) panel.append(traversalList);
  } catch (_) {
    renderGraphFallback(panel, graph);
  }
  container.append(panel);
}

function renderTurnChoices(container, sourceQuestion, response, envelope) {
  const options = envelope && envelope.version === 1 && Array.isArray(envelope.options)
    ? envelope.options
    : [];
  if (!options.length) return;
  const choices = document.createElement("div");
  choices.className = "choices turn-choices";
  const prompt = response && response.clarification ? response.clarification : "추가 조건을 선택해 주세요.";
  for (const option of options) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "choice";
    button.dataset.choiceId = option.choice_id || "";
    button.textContent = option.label;
    if (option.detail && option.detail !== option.label) button.title = option.detail;
    button.addEventListener("click", () => {
      if (inFlight) return;
      clarify = {
        question: sourceQuestion,
        resolved: { ...clarify.resolved, [option.filter]: option.value },
        trail: [...(clarify.trail || []), { prompt, label: option.label }],
        conversation_id: currentConversationId,
      };
      saveClarify(clarify);
      ask(sourceQuestion, option.label);
    });
    choices.append(button);
  }
  container.append(choices);
}

function renderEvidenceInto(container, presentation) {
  const pages = presentation.evidence_pages || [];
  pdfPageCount = Number(presentation.pdf && presentation.pdf.page_count) || 0;
  const total = pages.reduce((sum, page) => sum + page.evidence.length, 0);
  if (!total) return;
  const section = document.createElement("section");
  section.className = "evidence turn-evidence";
  const heading = document.createElement("h4");
  heading.textContent = pages.length
    ? `발췌 PDF ${pages.length}개 페이지 · 근거 ${total}건`
    : "표시할 VERIFIED 근거가 없습니다.";
  section.append(heading);
  if (pages.length && presentation.pdf && !presentation.pdf.available) {
    const notice = document.createElement("p");
    notice.className = "notice";
    notice.textContent = `${presentation.pdf.reason} 페이지 번호와 Evidence 원문은 계속 표시합니다.`;
    section.append(notice);
  }
  pages.forEach((page) => section.append(pageCard(page, presentation.pdf, total)));
  container.append(section);
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
  modalState = {
    originPage: page,
    evidence,
    page: page.excerpt_page,
    returnFocus: document.activeElement,
  };
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
el.pdfModal.addEventListener("keydown", (event) => {
  if (event.key !== "Tab") return;
  const focusable = [...el.pdfModal.querySelectorAll(
    'button:not(:disabled), [href], input:not(:disabled), [tabindex]:not([tabindex="-1"])'
  )].filter((node) => !node.hidden);
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
});
el.pdfModal.addEventListener("close", () => {
  const returnFocus = modalState && modalState.returnFocus;
  if (returnFocus && typeof returnFocus.focus === "function" && returnFocus.isConnected) {
    returnFocus.focus();
  }
});
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
ensureConversation().catch(() => {
  showNotice(el.askNotice, "브라우저 채팅 저장소를 사용할 수 없어 이번 화면에서만 질문할 수 있습니다.", false);
});
autoGrow();
el.question.focus();
