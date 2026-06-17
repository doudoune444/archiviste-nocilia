(function () {
  "use strict";

  const STORAGE_KEY = "archiviste.conversation_id";
  const ERROR_MESSAGE = "L'archive ne répond pas. Réessayez.";

  // AC-8: read persisted conversation id; may be null if not yet set.
  function loadConversationId() {
    try {
      return localStorage.getItem(STORAGE_KEY);
    } catch (_) {
      return null;
    }
  }

  // AC-9: generate a new UUIDv4 and persist it before the first request.
  function ensureConversationId() {
    let id = loadConversationId();
    if (!id) {
      id = crypto.randomUUID();
      try {
        localStorage.setItem(STORAGE_KEY, id);
      } catch (_) {
        // AC failure mode: localStorage unavailable — id is ephemeral this session.
      }
    }
    return id;
  }

  // AC-11/AC-12/AC-13: insert an article into the conversation zone.
  function appendArticle(role, text, requestId) {
    const section = document.getElementById("conversation");
    const article = document.createElement("article");
    article.dataset.role = role;
    article.textContent = text;

    // AC-14: attach <small>req: <id></small> for error articles if id available.
    if (role === "error" && requestId) {
      const small = document.createElement("small");
      small.textContent = "req: " + requestId;
      article.appendChild(small);
    }

    section.appendChild(article);
    article.scrollIntoView({ behavior: "smooth", block: "end" });
  }

  // CIT-001: render a "Sources" list under a canon answer.
  // `citations` is the response payload: [{ source_path, chunk_ords }].
  // Off-topic / lore-gap / mystery answers carry no citations, so an empty or
  // absent list renders nothing — no misleading empty "Sources" block.
  // Each source_path is written via textContent (never innerHTML) so a poisoned
  // source path cannot inject markup (security.md output sanitization).
  function appendSources(citations) {
    if (!Array.isArray(citations) || citations.length === 0) {
      return;
    }

    const list = document.createElement("ul");
    list.className = "sources-list";
    for (const citation of citations) {
      if (!citation || typeof citation.source_path !== "string") {
        continue;
      }
      const item = document.createElement("li");
      item.textContent = citation.source_path;
      list.appendChild(item);
    }

    if (!list.firstChild) {
      return;
    }

    const section = document.getElementById("conversation");
    const sources = document.createElement("section");
    sources.dataset.role = "sources";
    sources.className = "sources";

    const heading = document.createElement("h2");
    heading.className = "sources-title";
    heading.textContent = "Sources";

    sources.appendChild(heading);
    sources.appendChild(list);

    section.appendChild(sources);
    sources.scrollIntoView({ behavior: "smooth", block: "end" });
  }

  // CTR-002: append the per-answer "Report an inconsistency" control under the
  // answer, kept separate from appendSources (SRP — sources rendering vs report
  // widget). `conversationId` is captured at render time (the active id when this
  // answer was received) and bound to this control — NOT re-read from localStorage
  // at submit, to prevent wrong-conversation attribution after reopenConversation()
  // overwrites the global key. Renders nothing when the answer carries no citations.
  function appendReportControl(citations, conversationId) {
    if (!Array.isArray(citations) || citations.length === 0) {
      return;
    }
    const section = document.getElementById("conversation");
    section.appendChild(buildReportControl(citations, conversationId));
  }

  // CTR-002: build the per-answer contradiction-report widget.
  // `citations` is the array from the response body (already validated above).
  // `conversationId` is the id captured at render time for this specific answer;
  // it must NOT be re-read from localStorage at submit (see appendSources comment).
  // Security: all text written via textContent; claim sent as JSON (never interpolated).
  function buildReportControl(citations, conversationId) {
    const wrapper = document.createElement("div");
    wrapper.className = "report-control";

    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "report-btn";
    btn.textContent = "Signaler une incohérence";

    const form = document.createElement("div");
    form.className = "report-form";
    form.hidden = true;

    const textarea = document.createElement("textarea");
    textarea.className = "report-claim";
    textarea.placeholder = "Décrivez l'incohérence constatée…";
    textarea.maxLength = 4096;
    textarea.rows = 3;

    const submitBtn = document.createElement("button");
    submitBtn.type = "button";
    submitBtn.className = "report-submit-btn";
    submitBtn.textContent = "Envoyer le signalement";

    const feedback = document.createElement("p");
    feedback.className = "report-feedback";
    feedback.hidden = true;

    form.appendChild(textarea);
    form.appendChild(submitBtn);
    form.appendChild(feedback);

    wrapper.appendChild(btn);
    wrapper.appendChild(form);

    btn.addEventListener("click", function () {
      form.hidden = !form.hidden;
      if (!form.hidden) {
        textarea.focus();
      }
    });

    submitBtn.addEventListener("click", async function () {
      const claim = textarea.value.trim();
      if (!claim) {
        return;
      }
      // CTR-002: use the conversation id captured at render time for this answer.
      // Do NOT re-read localStorage here — reopenConversation() may have overwritten
      // the global key with a different conversation since this answer was rendered.
      if (!conversationId) {
        feedback.textContent = "Impossible d'envoyer le signalement, réessayez.";
        feedback.hidden = false;
        return;
      }

      submitBtn.disabled = true;
      feedback.hidden = true;

      try {
        const resp = await fetch("/v1/report-contradiction", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "same-origin",
          body: JSON.stringify({
            claim: claim,
            conversation_id: conversationId,
            citations: citations,
          }),
        });

        if (resp.ok) {
          let body = null;
          try {
            body = await resp.json();
          } catch (_) {
            // ignore parse error — show neutral success
          }
          if (body && body.contradiction_confirmed === true) {
            feedback.textContent =
              "Merci — nous avons confirmé une incohérence et l'avons transmise aux archivistes.";
          } else {
            feedback.textContent =
              "Nous avons vérifié les sources citées et n'avons pas trouvé de contradiction.";
          }
        } else {
          // 400 or 5xx — never leak error envelope internals (security.md output sanitization).
          feedback.textContent =
            "Impossible d'envoyer le signalement, réessayez.";
        }
      } catch (_) {
        // Network drop or fetch failure.
        feedback.textContent =
          "Impossible d'envoyer le signalement, réessayez.";
      }

      feedback.hidden = false;
      submitBtn.disabled = false;
      form.hidden = true;
    });

    return wrapper;
  }

  // Extract request id from response headers or JSON body (best-effort).
  function extractRequestId(response, body) {
    const fromHeader = response.headers.get("x-request-id");
    if (fromHeader) {
      return fromHeader;
    }
    if (body && typeof body === "object" && typeof body.request_id === "string") {
      return body.request_id;
    }
    return null;
  }

  // AC-10: submit handler — fetch POST /v1/chat, manage button state.
  async function handleSubmit(event) {
    event.preventDefault();

    const form = event.currentTarget;
    const input = document.getElementById("query-input");
    const sendBtn = document.getElementById("send-btn");
    const query = input.value.trim();

    if (!query) {
      return;
    }

    // AC-9: generate/persist conversation id before emitting the request.
    const conversationId = ensureConversationId();

    // AC-15: disable send button during in-flight request.
    sendBtn.disabled = true;

    // AC-11: insert user message immediately via textContent (never innerHTML).
    appendArticle("user", query, null);
    input.value = "";

    try {
      const response = await fetch("/v1/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ query: query, conversation_id: conversationId }),
      });

      if (response.ok) {
        let body = null;
        try {
          body = await response.json();
        } catch (_) {
          // Malformed JSON — treat as error per AC-13.
        }

        if (body && typeof body.answer === "string") {
          // AC-12: render answer via textContent.
          appendArticle("assistant", body.answer, null);
          // CIT-001: render the cited sources under the answer (canon only).
          appendSources(body.citations);
          // CTR-002: bind the active conversationId to the report widget at render
          // time, not lazily at submit from localStorage (wrong-conversation guard).
          appendReportControl(body.citations, conversationId);
          // HIST-001: a first turn creates the conversation — refresh the list.
          loadHistory();
        } else {
          // AC-13: missing/invalid answer field treated as error.
          const requestId = extractRequestId(response, body);
          appendArticle("error", ERROR_MESSAGE, requestId);
        }
      } else {
        // AC-13: any non-200 status → generic error message.
        let body = null;
        try {
          body = await response.json();
        } catch (_) {
          // ignore
        }
        const requestId = extractRequestId(response, body);
        appendArticle("error", ERROR_MESSAGE, requestId);
      }
    } catch (_) {
      // AC-13: network drop, timeout, or other fetch failure.
      appendArticle("error", ERROR_MESSAGE, null);
    }

    // AC-15: re-enable send button after response (success or error).
    sendBtn.disabled = false;
  }

  // HIST-001: format an ISO timestamp for a history entry label (locale-aware,
  // falls back to a neutral label so a malformed value never throws).
  function formatTimestamp(iso) {
    if (typeof iso !== "string") {
      return "Conversation";
    }
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) {
      return "Conversation";
    }
    return date.toLocaleString("fr-FR", { dateStyle: "medium", timeStyle: "short" });
  }

  // HIST-001: human label for one conversation summary (date · N message(s)).
  function historyLabel(conversation) {
    const count = Number.isFinite(conversation.message_count)
      ? conversation.message_count
      : 0;
    const noun = count === 1 ? "message" : "messages";
    return formatTimestamp(conversation.updated_at) + " · " + count + " " + noun;
  }

  // HIST-001: render the owner-scoped conversation list. Each entry is a button
  // labelled via textContent (never innerHTML) so server data cannot inject markup.
  function renderHistory(conversations) {
    const section = document.getElementById("history");
    const list = document.getElementById("history-list");
    while (list.firstChild) {
      list.removeChild(list.firstChild);
    }
    for (const conversation of conversations) {
      if (!conversation || typeof conversation.id !== "string") {
        continue;
      }
      const item = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.conversationId = conversation.id;
      button.textContent = historyLabel(conversation);
      item.appendChild(button);
      list.appendChild(item);
    }
    section.hidden = list.firstChild === null;
  }

  // HIST-001: fetch the caller's own conversations (cookie identity, same-origin).
  async function loadHistory() {
    let response;
    try {
      response = await fetch("/v1/conversations", { credentials: "same-origin" });
    } catch (_) {
      return;
    }
    if (!response.ok) {
      return;
    }
    let body = null;
    try {
      body = await response.json();
    } catch (_) {
      return;
    }
    if (!body || !Array.isArray(body.conversations)) {
      return;
    }
    renderHistory(body.conversations);
  }

  // HIST-001: reopen a past conversation — load its turns and continue it. A
  // cross-owner id returns 404 server-side, so this no-ops on anything not owned.
  async function reopenConversation(conversationId) {
    let response;
    try {
      response = await fetch(
        "/v1/conversations/" + encodeURIComponent(conversationId) + "/messages",
        { credentials: "same-origin" },
      );
    } catch (_) {
      return;
    }
    if (!response.ok) {
      return;
    }
    let body = null;
    try {
      body = await response.json();
    } catch (_) {
      return;
    }
    if (!body || !Array.isArray(body.messages)) {
      return;
    }

    const section = document.getElementById("conversation");
    while (section.firstChild) {
      section.removeChild(section.firstChild);
    }
    for (const message of body.messages) {
      if (!message || typeof message.content !== "string") {
        continue;
      }
      const role = message.role === "assistant" ? "assistant" : "user";
      appendArticle(role, message.content, null);
    }

    try {
      localStorage.setItem(STORAGE_KEY, conversationId);
    } catch (_) {
      // localStorage unavailable — reopen still rendered, id ephemeral this session.
    }
  }

  // HIST-001: delegated click on the history list → reopen the chosen conversation.
  function handleHistoryClick(event) {
    const button = event.target.closest("button[data-conversation-id]");
    if (!button) {
      return;
    }
    reopenConversation(button.dataset.conversationId);
  }

  // AC-16: clear conversation and remove persisted conversation id.
  function handleNewConversation() {
    try {
      localStorage.removeItem(STORAGE_KEY);
    } catch (_) {
      // ignore
    }
    const section = document.getElementById("conversation");
    while (section.firstChild) {
      section.removeChild(section.firstChild);
    }
  }

  document.getElementById("chat-form").addEventListener("submit", handleSubmit);
  document
    .getElementById("new-conversation-btn")
    .addEventListener("click", handleNewConversation);
  // HIST-001: history list (delegated reopen) + initial load on page open.
  document
    .getElementById("history-list")
    .addEventListener("click", handleHistoryClick);
  loadHistory();
  // #161: on reload, restore the previously open conversation from localStorage.
  // loadConversationId() returns null when nothing was persisted — no fetch, no error.
  const activeId = loadConversationId();
  if (activeId) {
    reopenConversation(activeId);
  }
})();
