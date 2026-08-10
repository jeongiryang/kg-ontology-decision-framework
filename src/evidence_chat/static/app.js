/* 3단계 화면 제어와 SSE 단계 스트림 수신.
   서버가 보낸 단계 상태를 그대로 반영한다. 화면이 임의로 단계를 완료 처리하지 않는다. */

"use strict";

const $ = (id) => document.getElementById(id);
const STEP_ICON = { running: "", done: "✓", skipped: "–", failed: "!" };
const SCREEN_ORDER = ["ask", "progress", "answer"];

/* 반복되는 DOM 생성을 한곳에 모은다. */
const span = (className, text) => {
  const node = document.createElement("span");
  if (className) node.className = className;
  node.textContent = text;
  return node;
};
const fillList = (node, lines) => {
  node.replaceChildren();
  (lines || []).forEach((line) => {
    const li = document.createElement("li");
    li.textContent = line;
    node.append(li);
  });
};
const appendPre = (parent, text) => {
  const pre = document.createElement("pre");
  pre.textContent = text;
  parent.append(pre);
};

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
  asked: $("asked"),
  barFill: $("bar-fill"),
  steps: $("steps"),
  answerQuestion: $("answer-question"),
  answerBadge: $("answer-badge"),
  answerIntent: $("answer-intent"),
  answerTitle: $("answer-title"),
  answerDetails: $("answer-details"),
  answerWarnings: $("answer-warnings"),
  evidenceSummary: $("evidence-summary"),
  evidencePages: $("evidence-pages"),
  pdfNotice: $("pdf-notice"),
  answerSteps: $("answer-steps"),
  answerAgain: $("answer-again"),
};

let pdfAvailable = false;
let inFlight = false;

/* ---------- 화면 전환 ---------- */
function showScreen(name) {
  Object.entries(el.screens).forEach(([key, node]) =>
    node.classList.toggle("is-active", key === name)
  );
  const current = SCREEN_ORDER.indexOf(name);
  el.stages.forEach((li) => {
    const index = SCREEN_ORDER.indexOf(li.dataset.stage);
    li.classList.toggle("is-current", index === current);
    li.classList.toggle("is-done", index < current);
  });
  window.scrollTo({ top: 0, behavior: "smooth" });
}

/* ---------- 상태 표시 ---------- */
async function loadHealth() {
  try {
    const res = await fetch("/api/health");
    const data = await res.json();
    pdfAvailable = Boolean(data.pdf && data.pdf.available);
    const dot = el.status.querySelector(".dot");
    const text = el.status.querySelector(".status-text");
    if (!data.neo4j_connected) {
      dot.dataset.state = "error";
      text.textContent = "Neo4j 연결 안 됨";
      showNotice(el.askNotice, data.error || "Neo4j에 연결되지 않았습니다.", true);
    } else if (!pdfAvailable) {
      dot.dataset.state = "warn";
      text.textContent = `Neo4j ${data.neo4j_endpoint} · PDF 미탑재`;
      showNotice(el.askNotice, data.pdf.reason || "발췌 PDF가 없습니다.", false);
    } else {
      dot.dataset.state = "ok";
      text.textContent = `Neo4j ${data.neo4j_endpoint} · PDF ${data.pdf.page_count}p`;
      if (!data.pdf.sha256_matches && data.pdf.reason) {
        showNotice(el.askNotice, data.pdf.reason, false);
      }
    }
    renderExamples(data.examples || []);
  } catch (error) {
    el.status.querySelector(".dot").dataset.state = "error";
    el.status.querySelector(".status-text").textContent = "서버 상태 확인 실패";
  }
}

function showNotice(node, message, isError) {
  node.textContent = message;
  node.classList.toggle("error", Boolean(isError));
  node.hidden = false;
}

function renderExamples(examples) {
  el.examples.replaceChildren();
  examples.forEach((text) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = text;
    button.addEventListener("click", () => {
      el.question.value = text;
      autoGrow();
      el.form.requestSubmit();
    });
    el.examples.append(button);
  });
}

/* ---------- 입력 ---------- */
function autoGrow() {
  el.question.style.height = "auto";
  el.question.style.height = `${Math.min(el.question.scrollHeight, 180)}px`;
}
el.question.addEventListener("input", autoGrow);
el.question.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    el.form.requestSubmit();
  }
});

el.form.addEventListener("submit", (event) => {
  event.preventDefault();
  const question = el.question.value.trim();
  if (!question || inFlight) return;
  ask(question);
});

el.progressBack.addEventListener("click", () => showScreen("ask"));
el.answerAgain.addEventListener("click", () => {
  el.question.value = "";
  autoGrow();
  showScreen("ask");
  el.question.focus();
});

/* ---------- 단계 렌더링 ---------- */
function stepNode(event) {
  const li = document.createElement("li");
  li.dataset.stepId = event.step_id;
  const row = document.createElement("div");
  row.className = "step-row";
  row.append(span("step-icon", ""), span("step-label", ""), span("step-time", ""));
  li.append(row);
  return li;
}

function renderStep(list, event) {
  let li = list.querySelector(`[data-step-id="${event.step_id}"]`);
  if (!li) {
    li = stepNode(event);
    list.append(li);
  }
  li.className = `step is-${event.status}`;
  li.querySelector(".step-icon").textContent = STEP_ICON[event.status] || "";
  li.querySelector(".step-label").textContent = `${event.index}. ${event.label}`;
  li.querySelector(".step-time").textContent =
    event.elapsed_ms === undefined ? "" : `${event.elapsed_ms}ms`;

  li.querySelectorAll(".step-detail, pre").forEach((node) => node.remove());
  if (event.detail && event.detail.length) {
    const ul = document.createElement("ul");
    ul.className = "step-detail";
    fillList(ul, event.detail);
    li.append(ul);
  }
  if (event.data && typeof event.data.cypher === "string") {
    appendPre(li, event.data.cypher);
  }
  if (event.data && event.data.parameters) {
    appendPre(li, JSON.stringify(event.data.parameters, null, 2));
  }
}

/* ---------- 질의 실행 ---------- */
async function ask(question) {
  inFlight = true;
  el.submit.disabled = true;
  el.asked.textContent = question;
  el.answerQuestion.textContent = question;
  el.steps.replaceChildren();
  el.progressError.hidden = true;
  el.spinner.classList.remove("is-done");
  el.progressTitle.textContent = "처리 중입니다";
  el.barFill.style.width = "0%";
  showScreen("progress");

  const events = [];
  let result = null;
  let failed = false;

  try {
    const response = await fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
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
        if (payload.type === "step") {
          events.push(payload);
          renderStep(el.steps, payload);
          el.progressNow.textContent = `${payload.label} · ${payload.status === "running" ? "진행 중" : "완료"}`;
          el.barFill.style.width = `${Math.round((payload.index / payload.total) * 100)}%`;
        } else if (payload.type === "result") {
          result = payload;
        } else if (payload.type === "error") {
          failed = true;
          showNotice(el.progressError, `${payload.stage} 단계 실패: ${payload.message}`, true);
        }
      }
    }
  } catch (error) {
    failed = true;
    showNotice(el.progressError, error.message, true);
  } finally {
    inFlight = false;
    el.submit.disabled = false;
    el.spinner.classList.add("is-done");
    el.progressTitle.textContent = failed ? "처리를 마치지 못했습니다" : "처리 완료";
    el.progressNow.textContent = failed
      ? "위 단계에서 원인을 확인하세요."
      : "답변 화면으로 이동합니다.";
  }

  if (result) {
    renderAnswer(result, events);
    showScreen("answer");
  }
}

/* ---------- 답변 렌더링 ---------- */
function renderAnswer(result, events) {
  const answer = result.answer;
  el.answerBadge.textContent = answer.answerability_label;
  el.answerBadge.dataset.state = answer.answerability;
  el.answerIntent.textContent = `${answer.intent_label} · ${answer.intent}`;
  el.answerTitle.textContent = answer.headline;

  fillList(el.answerDetails, answer.details);
  fillList(el.answerWarnings, answer.warnings);

  el.answerSteps.replaceChildren();
  events.forEach((event) => renderStep(el.answerSteps, event));

  renderEvidence(result);
}

function renderEvidence(result) {
  const pages = result.evidence_pages || [];
  const total = pages.reduce((sum, page) => sum + page.evidence.length, 0);
  el.evidenceSummary.textContent = pages.length
    ? `발췌 PDF ${pages.length}개 페이지 · 근거 ${total}건 (참조한 페이지만 표시)`
    : "표시할 VERIFIED 근거가 없습니다.";

  el.pdfNotice.hidden = true;
  if (pages.length && result.pdf && !result.pdf.available) {
    showNotice(
      el.pdfNotice,
      `${result.pdf.reason} 지금은 페이지 번호와 원문 텍스트만 보여 줍니다.`,
      false
    );
  }

  el.evidencePages.replaceChildren();
  pages.forEach((page) => el.evidencePages.append(pageCard(page, result.pdf)));
}

function pageCard(page, pdf) {
  const card = document.createElement("section");
  card.className = "page-card";

  const head = document.createElement("div");
  head.className = "page-head";
  head.append(
    span("page-no", `발췌 p.${page.excerpt_page}`),
    span(
      "page-sub",
      `인쇄 p.${page.printed_page} · 원본 규정집 p.${page.source_pdf_page}`
    ),
    span("page-count", `근거 ${page.evidence.length}건`)
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
    canvas.append(img);
    page.evidence.forEach((item, index) => {
      item.highlights.forEach((box) => {
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
    view.append(canvas);
  } else {
    const missing = document.createElement("p");
    missing.className = "page-missing";
    missing.textContent = "PDF 원본이 없어 페이지 이미지를 표시할 수 없습니다.";
    view.append(missing);
  }

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
    meta.append(span("", `p.${page.excerpt_page} 근거 ${index + 1}`));
    if (pdf && pdf.available) {
      meta.append(
        item.highlight_found
          ? span("", `강조 ${item.highlights.length}곳`)
          : span("no-hl", "페이지에서 위치를 찾지 못했습니다")
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
