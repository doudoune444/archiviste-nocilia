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

  // ---------------------------------------------------------------------------
  // #163: Persistent "Signaler" panel — always visible in the composer footer.
  // Single entry point (not gated on citations). Two-step: submit → if not_raised →
  // offer "Envoyer quand même" (force=true). All text via textContent (no innerHTML).
  // ---------------------------------------------------------------------------

  const signalerPanel = document.getElementById("signaler-panel");
  const signalerToggle = document.getElementById("signaler-toggle-btn");
  const signalerForm = document.getElementById("signaler-form");
  const signalerClaim = document.getElementById("signaler-claim");
  const signalerSubmitBtn = document.getElementById("signaler-submit-btn");
  const signalerFeedback = document.getElementById("signaler-feedback");
  const signalerSendAnywayBtn = document.getElementById("signaler-send-anyway-btn");
  const signalerCancelBtn = document.getElementById("signaler-cancel-btn");
  const signalerSecondRow = document.getElementById("signaler-second-row");

  function resetSignaler() {
    signalerClaim.value = "";
    signalerFeedback.textContent = "";
    signalerFeedback.hidden = true;
    signalerSecondRow.hidden = true;
    signalerSubmitBtn.disabled = false;
    signalerForm.hidden = true;
    signalerToggle.setAttribute("aria-expanded", "false");
  }

  function showSignalerFeedback(text) {
    signalerFeedback.textContent = text;
    signalerFeedback.hidden = false;
  }

  function showSecondStep(outcomeMsg, reason) {
    // outcome-specific message + reason shown; raw verdict token never shown to user (#172).
    const reasonLine = reason ? reason : "";
    signalerFeedback.textContent =
      outcomeMsg + (reasonLine ? " " + reasonLine : "");
    signalerFeedback.hidden = false;
    signalerSecondRow.hidden = false;
    signalerSubmitBtn.disabled = true;
  }

  async function postReport(claim, conversationId, force) {
    const bodyObj = { claim: claim, force: force };
    if (conversationId) {
      bodyObj.conversation_id = conversationId;
    }
    const resp = await fetch("/v1/report-contradiction", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify(bodyObj),
    });
    let body = null;
    try {
      body = await resp.json();
    } catch (_) {
      // ignore parse error
    }
    // Return status so callers can distinguish 404 (ownership check) from other failures.
    return { ok: resp.ok, status: resp.status, body: body };
  }

  if (signalerToggle) {
    signalerToggle.addEventListener("click", function () {
      const isOpen = signalerForm.hidden === false;
      if (isOpen) {
        resetSignaler();
      } else {
        signalerForm.hidden = false;
        signalerToggle.setAttribute("aria-expanded", "true");
        signalerClaim.focus();
      }
    });
  }

  if (signalerSubmitBtn) {
    signalerSubmitBtn.addEventListener("click", async function () {
      const claim = signalerClaim.value.trim();
      if (!claim) {
        return;
      }
      // conversation_id from localStorage — set by ensureConversationId() during the first
      // chat POST. Never mint a fresh id for the signal path: a freshly-minted id has no
      // row in the conversations table and the gateway ownership check (A01 IDOR fix) would
      // return 404. The visitor must have an active conversation to file a signal.
      const conversationId = loadConversationId();
      if (!conversationId) {
        showSignalerFeedback(
          "Aucune conversation à signaler — posez d'abord une question."
        );
        return;
      }

      signalerSubmitBtn.disabled = true;
      signalerFeedback.hidden = true;
      signalerSecondRow.hidden = true;

      try {
        const { ok, status, body } = await postReport(claim, conversationId, false);
        if (!ok) {
          if (status === 404) {
            // Ownership check failed: the conversation id no longer resolves to this caller.
            showSignalerFeedback(
              "Aucune conversation à signaler — posez d'abord une question."
            );
          } else {
            showSignalerFeedback("Impossible d'envoyer le signalement, réessayez.");
          }
          signalerSubmitBtn.disabled = false;
          return;
        }
        // Branch on outcome (#172): confirmed/refused/indecisive.
        // Raw verdict token is never shown to the user.
        const outcome = body && body.outcome;
        const action = body && body.ticket_action;
        const reason = (body && body.reason) || "";
        const ticketPersisted =
          action === "created" || action === "incremented";
        if (outcome === "confirmed" && ticketPersisted) {
          // Judges confirmed absent/contradiction — ticket registered.
          showSignalerFeedback(
            "Incohérence confirmée — signalement enregistré." +
              (reason ? " " + reason : "")
          );
          // FIX 3: re-enable + clear so the panel is not stuck after a successful first submit.
          signalerSubmitBtn.disabled = false;
          signalerClaim.value = "";
        } else if (outcome === "confirmed") {
          // confirmed but the ticket write failed (skipped_error): never claim success.
          showSignalerFeedback("Impossible d'envoyer le signalement, réessayez.");
          signalerSubmitBtn.disabled = false;
        } else if (outcome === "refused") {
          // Judges confirmed lore is consistent.
          showSecondStep("Le lore est cohérent, signal refusé.", reason);
        } else if (outcome === "indecisive") {
          // Judges could not decide.
          showSecondStep("Les juges n'ont pas pu trancher.", reason);
        } else {
          // skipped_error or unexpected outcome value
          showSignalerFeedback("Impossible d'envoyer le signalement, réessayez.");
          signalerSubmitBtn.disabled = false;
        }
      } catch (_) {
        showSignalerFeedback("Impossible d'envoyer le signalement, réessayez.");
        signalerSubmitBtn.disabled = false;
      }
    });
  }

  if (signalerSendAnywayBtn) {
    signalerSendAnywayBtn.addEventListener("click", async function () {
      const claim = signalerClaim.value.trim();
      if (!claim) {
        return;
      }
      // Same guard as the submit handler: never mint a fresh id for the signal path.
      const conversationId = loadConversationId();
      if (!conversationId) {
        showSignalerFeedback(
          "Aucune conversation à signaler — posez d'abord une question."
        );
        return;
      }

      signalerSendAnywayBtn.disabled = true;
      signalerCancelBtn.disabled = true;

      try {
        const { ok, status, body } = await postReport(claim, conversationId, true);
        signalerSecondRow.hidden = true;
        if (!ok) {
          if (status === 404) {
            showSignalerFeedback(
              "Aucune conversation à signaler — posez d'abord une question."
            );
          } else {
            showSignalerFeedback("Impossible d'envoyer le signalement, réessayez.");
          }
          // FIX 3: re-enable both buttons so the panel is not stuck after a send-anyway failure.
          signalerSendAnywayBtn.disabled = false;
          signalerCancelBtn.disabled = false;
          return;
        }
        const action = body && body.ticket_action;
        if (action === "created" || action === "incremented") {
          showSignalerFeedback(
            "Signalement envoyé malgré l'absence de confirmation par les juges."
          );
          // FIX 3: clear + re-enable so the visitor is not left with a stuck second-step.
          signalerClaim.value = "";
          signalerSubmitBtn.disabled = false;
        } else if (action === "skipped_error") {
          // #173: non-recoverable server-side write failure — show distinct message.
          // send-anyway stays disabled (retrying cannot succeed); cancel re-enabled so
          // the visitor can dismiss the panel.
          showSignalerFeedback(
            "Le serveur n'a pas pu enregistrer le signalement. Réessayez plus tard."
          );
          signalerCancelBtn.disabled = false;
        } else {
          showSignalerFeedback("Impossible d'envoyer le signalement, réessayez.");
          signalerSendAnywayBtn.disabled = false;
          signalerCancelBtn.disabled = false;
        }
      } catch (_) {
        showSignalerFeedback("Impossible d'envoyer le signalement, réessayez.");
        signalerSendAnywayBtn.disabled = false;
        signalerCancelBtn.disabled = false;
      }
    });
  }

  if (signalerCancelBtn) {
    signalerCancelBtn.addEventListener("click", function () {
      resetSignaler();
    });
  }

  // ---------------------------------------------------------------------------
  // AC-10: submit handler — fetch POST /v1/chat, manage button state.
  // ---------------------------------------------------------------------------

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

  // #296: pre-wake the scale-to-zero worker the moment the user is about to type.
  // On the first focus of the query field we fire a fire-and-forget GET /v1/wake so
  // the worker cold-starts while the user phrases the question. A session flag keeps
  // it to a single call (no repeat per focus, no wake on page load) to keep idle cost
  // near zero. Failures are ignored: this is a best-effort warm-up, not a request.
  let hasPrewarmed = false;
  function prewarmWorker() {
    if (hasPrewarmed) {
      return;
    }
    hasPrewarmed = true;
    fetch("/v1/wake").catch(function () {});
  }

  document.getElementById("chat-form").addEventListener("submit", handleSubmit);
  document
    .getElementById("query-input")
    .addEventListener("focus", prewarmWorker);
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
