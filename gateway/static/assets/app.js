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
})();
