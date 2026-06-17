/* Public lore-gap board — fetch open tickets, render read-only.
 * BOARD-001: textContent only (never innerHTML). Read-only: no write actions. */

'use strict';

const PAGE_LIMIT = 50;
let currentOffset = 0;
let totalTickets = 0;

/** @param {string|undefined} reqId */
function showError(reqId) {
  const errorDiv = document.getElementById('board-error');
  errorDiv.textContent = 'Erreur de chargement. Réessayez.';
  if (reqId) {
    const small = document.createElement('small');
    small.textContent = 'req: ' + reqId;
    errorDiv.appendChild(small);
  }
  errorDiv.hidden = false;
}

/**
 * @param {{ id: string, priority_score: number, category: string, question: string,
 *           created_at: string, judges_not_passed: boolean }} ticket
 */
function renderRow(ticket) {
  const tr = document.createElement('tr');

  const tdPriority = document.createElement('td');
  tdPriority.textContent = String(ticket.priority_score);
  tr.appendChild(tdPriority);

  const tdCategory = document.createElement('td');
  tdCategory.textContent = ticket.category;
  tr.appendChild(tdCategory);

  const tdQuestion = document.createElement('td');
  tdQuestion.textContent = ticket.question;
  tr.appendChild(tdQuestion);

  const tdDate = document.createElement('td');
  tdDate.textContent = ticket.created_at;
  tr.appendChild(tdDate);

  // #163: only tickets force-raised by a visitor (judges_not_passed===true) carry
  // a badge; judge-confirmed, legacy and auto-created tickets (false) render
  // neutrally — no green "confirmé" claim they may not have earned.
  const tdConfirmation = document.createElement('td');
  if (ticket.judges_not_passed === true) {
    const badge = document.createElement('span');
    badge.className = 'badge-unconfirmed';
    badge.textContent = 'non confirmé par les juges';
    tdConfirmation.appendChild(badge);
  }
  tr.appendChild(tdConfirmation);

  return tr;
}

/** @param {number} offset */
async function loadTickets(offset) {
  let reqId;
  try {
    const resp = await fetch('/v1/board?limit=' + PAGE_LIMIT + '&offset=' + offset);
    const reqIdHeader = resp.headers.get('X-Request-Id');
    let body;
    try {
      body = await resp.json();
    } catch (_) {
      showError(reqIdHeader || undefined);
      return;
    }
    reqId = reqIdHeader || body.request_id;
    if (!resp.ok) {
      showError(reqId);
      return;
    }

    totalTickets = body.total;
    currentOffset = offset + body.items.length;

    const tbody = document.getElementById('board-tbody');
    if (offset === 0 && body.items.length === 0) {
      const tr = document.createElement('tr');
      const td = document.createElement('td');
      td.colSpan = 5;
      td.textContent = 'Aucun ticket ouvert.';
      tr.appendChild(td);
      tbody.appendChild(tr);
    } else {
      for (const ticket of body.items) {
        tbody.appendChild(renderRow(ticket));
      }
    }

    updateLoadMore();
  } catch (_) {
    showError(reqId);
  }
}

function updateLoadMore() {
  const container = document.getElementById('board-load-more-container');
  container.textContent = '';
  if (currentOffset < totalTickets) {
    const btn = document.createElement('button');
    btn.id = 'board-load-more';
    btn.type = 'button';
    btn.textContent = 'Charger plus';
    btn.addEventListener('click', function onLoadMore() {
      loadTickets(currentOffset);
    });
    container.appendChild(btn);
  }
}

loadTickets(0);
