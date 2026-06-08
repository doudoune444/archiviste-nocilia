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

  // OBS-002 — health widget for /observability.
  // AC-13: fetch /v1/status same-origin, render global status + 3 deps via textContent.
  // AC-14: any non-200 or error → literal unavailability message.

  const STATUS_UNAVAILABLE_MSG = 'Santé du service indisponible.';

  function renderStatusUnavailable(widget) {
    widget.textContent = STATUS_UNAVAILABLE_MSG;
  }

  async function loadStatus() {
    const widget = document.getElementById('health-widget');
    if (!widget) {
      return;
    }

    let response;
    try {
      response = await fetch('/v1/status', { credentials: 'same-origin' });
    } catch (_networkError) {
      // AC-14: network failure → unavailability message, no per-code branching.
      renderStatusUnavailable(widget);
      return;
    }

    if (!response.ok) {
      // AC-14: any non-200 → unavailability message.
      renderStatusUnavailable(widget);
      return;
    }

    let data;
    try {
      data = await response.json();
    } catch (_parseError) {
      // AC-14: malformed JSON → unavailability message.
      renderStatusUnavailable(widget);
      return;
    }

    const globalStatus = data.status;
    const deps = data.dependencies;
    if (
      typeof globalStatus !== 'string' ||
      !deps ||
      typeof deps.postgres !== 'object' ||
      typeof deps.gcs !== 'object' ||
      typeof deps.workers !== 'object'
    ) {
      renderStatusUnavailable(widget);
      return;
    }

    // AC-13: render via textContent (never innerHTML).
    const lines = [
      'Service : ' + globalStatus,
      'postgres : ' + deps.postgres.status + ' (' + deps.postgres.latency_ms + ' ms)',
      'gcs : ' + deps.gcs.status + ' (' + deps.gcs.latency_ms + ' ms)',
      'workers : ' + deps.workers.status + ' (' + deps.workers.latency_ms + ' ms)',
    ];

    // Clear widget then append one text node per line for CSP-safe textContent rendering.
    widget.textContent = '';
    for (const line of lines) {
      const p = document.createElement('p');
      p.textContent = line;
      // AC-13: status class for CSS styling — never innerHTML.
      if (line.startsWith('Service')) {
        p.className = 'health-status health-status--' + globalStatus;
      }
      widget.appendChild(p);
    }
  }

  document.addEventListener('DOMContentLoaded', loadStats);
  document.addEventListener('DOMContentLoaded', loadStatus);
}());
