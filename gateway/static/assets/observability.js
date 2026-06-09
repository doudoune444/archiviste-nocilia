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

  // OBS-004 — quality widget for /observability.
  // AC-8: fetch /v1/quality same-origin, render 4 metrics + freshness via textContent.
  // AC-10: no_data → literal "Aucune évaluation disponible.".
  // AC-11: any non-200 or error → literal "Données indisponibles." (distinct from no_data).
  // AC-9: isolated try/catch — failure here must not affect #usage-widget / #health-widget.

  const QUALITY_UNAVAILABLE_MSG = 'Données indisponibles.';
  const QUALITY_NO_DATA_MSG = 'Aucune évaluation disponible.';

  async function loadQuality() {
    const widget = document.getElementById('quality-widget');
    if (!widget) {
      return;
    }

    let response;
    try {
      response = await fetch('/v1/quality', { credentials: 'same-origin' });
    } catch (_networkError) {
      // AC-11: network failure → unavailability message.
      widget.textContent = QUALITY_UNAVAILABLE_MSG;
      return;
    }

    if (!response.ok) {
      // AC-11: any non-200 → unavailability message.
      widget.textContent = QUALITY_UNAVAILABLE_MSG;
      return;
    }

    let data;
    try {
      data = await response.json();
    } catch (_parseError) {
      // AC-11: malformed JSON → unavailability message.
      widget.textContent = QUALITY_UNAVAILABLE_MSG;
      return;
    }

    // AC-10: no_data branch — literal message, no metrics.
    if (data.status === 'no_data') {
      widget.textContent = QUALITY_NO_DATA_MSG;
      return;
    }

    // AC-8: 4 metrics must be numbers; guard against unexpected shape.
    if (
      typeof data.faithfulness !== 'number' ||
      typeof data.answer_relevancy !== 'number' ||
      typeof data.context_precision !== 'number' ||
      typeof data.context_recall !== 'number'
    ) {
      widget.textContent = QUALITY_UNAVAILABLE_MSG;
      return;
    }

    // AC-8: render 4 metrics + freshness line via textContent (never innerHTML).
    const lines = [
      'Fidélité : ' + data.faithfulness,
      'Pertinence réponse : ' + data.answer_relevancy,
      'Précision contexte : ' + data.context_precision,
      'Rappel contexte : ' + data.context_recall,
      'Version golden set : ' + data.golden_set_version,
      'Dernière évaluation : ' + data.finished_at,
    ];

    widget.textContent = '';
    for (const line of lines) {
      const p = document.createElement('p');
      // AC-8: textContent only — CSP-safe, no innerHTML.
      p.textContent = line;
      widget.appendChild(p);
    }
  }

  document.addEventListener('DOMContentLoaded', loadStats);
  document.addEventListener('DOMContentLoaded', loadStatus);
  document.addEventListener('DOMContentLoaded', loadQuality);
}());
