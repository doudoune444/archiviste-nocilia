// OBS-001 — usage widget for /observability.
// AC-9: fetch /v1/stats same-origin, insert conversation_count via textContent.
// AC-10: any non-200 or error → show "Données indisponibles." (no per-code branching).

(function () {
  'use strict';

  const UNAVAILABLE_MSG = 'Données indisponibles.';

  function renderUnavailable(widget) {
    widget.textContent = UNAVAILABLE_MSG;
  }

  async function loadStats() {
    const widget = document.getElementById('usage-widget');
    if (!widget) {
      return;
    }

    let response;
    try {
      response = await fetch('/v1/stats', { credentials: 'same-origin' });
    } catch (_networkError) {
      // AC-10: network failure → unavailable message, no per-code branching.
      renderUnavailable(widget);
      return;
    }

    if (!response.ok) {
      // AC-10: any non-200 status → unavailable message.
      renderUnavailable(widget);
      return;
    }

    let data;
    try {
      data = await response.json();
    } catch (_parseError) {
      // AC-10: malformed JSON → unavailable message.
      renderUnavailable(widget);
      return;
    }

    const count = data.conversation_count;
    if (typeof count !== 'number') {
      renderUnavailable(widget);
      return;
    }

    // AC-9: insert via textContent (never innerHTML).
    widget.textContent = 'Conversations : ' + count;
  }

  document.addEventListener('DOMContentLoaded', loadStats);
}());
