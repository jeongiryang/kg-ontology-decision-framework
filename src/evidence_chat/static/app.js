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
  barFill: $("bar-fill"),
  answerQuestion: $("answer-question"),
  answerBadge: $("answer-badge"),
  answerTitle: $("answer-title"),
  clarification: $("clarification"),
  scopeNotice: $("scope-notice"),
  debugMeta: $("debug-meta"),
  evidenceSection: $("evidence-section"),
  evidenceSummary: $("evidence-summary"),
  evidencePages: $("evidence-pages"),
  pdfNotice: $("pdf-notice"),
  answerAgain: $("answer-again"),
};

let pdfAvailable = false;
let inFlight = false;
let activeController = null;
let elapsedTimer = null;
let clientTimeoutMs = 180000;

const span = (className, text) => {
  const node = document.createElement("span");
  if (className) node.className = className;
  node.textContent = text;
  return node;
};

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
    pdfAvailable = Boolean(data.pdf && data.pdf.available);
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
      showNotice(el.askNotice, data.pdf.reason || "발췌 PDF가 없습니다.", false);
    } else {
      dot.dataset.state = "ok";
      text.textContent = `질의 서비스 준비됨 · PDF ${data.pdf.page_count}p`;
      if (!data.pdf.sha256_matches && data.pdf.reason) {
        showNotice(el.askNotice, data.pdf.reason, false);
      }
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
  if (question && !inFlight) ask(question);
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
  el.barFill.style.width = "15%";
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
      body: JSON.stringify({ question }),
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
          el.barFill.style.width = payload.phase === "COMPLETED" ? "100%" : "55%";
        } else if (payload.type === "result") {
          result = payload;
        } else if (payload.type === "error") {
          failed = true;
          showNotice(el.progressError, payload.message, true);
        }
      }
    }
    if (!result && !failed) throw new Error("응답 결과가 없습니다.");
  } catch (error) {
    failed = true;
    const timedOut = activeController && activeController.signal.reason === "timeout";
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
    renderAnswer(result);
    showScreen("answer");
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
  el.scopeNotice.hidden = !presentation.scope_notice;
  el.scopeNotice.textContent = presentation.scope_notice || "";
  el.debugMeta.hidden = !presentation.debug;
  el.debugMeta.textContent = presentation.debug
    ? `request_id=${presentation.debug.request_id} · error_code=${presentation.debug.error_code || "없음"}`
    : "";
  renderEvidence(presentation);
}

function renderEvidence(presentation) {
  const pages = presentation.evidence_pages || [];
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
    quote.append(text, meta);
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

loadHealth();
autoGrow();
el.question.focus();
