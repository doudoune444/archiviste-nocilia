/* Dashboard auteur — fetch tickets, render, pagination, open conversation.
 * AC-14: textContent only (no innerHTML). AC-15: noopener,noreferrer.
 * AC-16: error message littéral. AC-17: empty state message littéral.
 * AC-18: "Charger plus" pagination. fetch credentials: 'same-origin'. */

'use strict';

const PAGE_LIMIT = 50;
let currentOffset = 0;
let totalTickets = 0;

/** @param {string|undefined} reqId */
function showError(reqId) {
  const errorDiv = document.getElementById('dashboard-error');
  errorDiv.textContent = 'Erreur de chargement. Réessayez.';
  if (reqId) {
    const small = document.createElement('small');
    small.textContent = 'req: ' + reqId;
    errorDiv.appendChild(small);
  }
  errorDiv.hidden = false;
}

/** @param {{ id: string, conversation_id: string, priority_score: number, category: string, question: string, created_at: string }} ticket */
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

  const tdAction = document.createElement('td');
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.dataset.action = 'open-conversation';
  btn.dataset.conversationId = ticket.conversation_id;
  btn.textContent = 'Ouvrir conversation';
  tdAction.appendChild(btn);
  tr.appendChild(tdAction);

  return tr;
}

/** @param {string} conversationId */
async function openConversation(conversationId) {
  let reqId;
  try {
    const resp = await fetch('/v1/conversations/' + conversationId + '/signed-url', {
      credentials: 'same-origin',
    });
    let reqIdHeader = resp.headers.get('X-Request-Id');
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
    window.open(body.signed_url, '_blank', 'noopener,noreferrer');
  } catch (_) {
    showError(reqId);
  }
}

/** @param {number} offset */
async function loadTickets(offset) {
  let reqId;
  try {
    const resp = await fetch('/v1/tickets?limit=' + PAGE_LIMIT + '&offset=' + offset, {
      credentials: 'same-origin',
    });
    let reqIdHeader = resp.headers.get('X-Request-Id');
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

    const tbody = document.getElementById('tickets-tbody');
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
  const container = document.getElementById('load-more-container');
  container.textContent = '';
  if (currentOffset < totalTickets) {
    const btn = document.createElement('button');
    btn.id = 'load-more';
    btn.type = 'button';
    btn.textContent = 'Charger plus';
    btn.addEventListener('click', function onLoadMore() {
      loadTickets(currentOffset);
    });
    container.appendChild(btn);
  }
}

document.getElementById('tickets-tbody').addEventListener('click', function (event) {
  const btn = event.target.closest('button[data-action="open-conversation"]');
  if (btn) {
    const id = btn.dataset.conversationId;
    if (id) {
      openConversation(id);
    }
  }
});

loadTickets(0);
