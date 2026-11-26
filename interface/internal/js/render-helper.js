// Shared render helpers for the internal Legal Office console.
// Every page includes this after api-data.js and calls into it to avoid
// re-implementing the same table/card markup six times.

const ICONS = {
  search: `<svg class="search-icon" viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>`,
  bell: `<svg viewBox="0 0 24 24"><path d="M6 9a6 6 0 0 1 12 0c0 5 2 6 2 6H4s2-1 2-6"/><path d="M10 20a2 2 0 0 0 4 0"/></svg>`,
  user: `<svg viewBox="0 0 24 24"><circle cx="12" cy="8" r="4"/><path d="M4 21c0-4.4 3.6-8 8-8s8 3.6 8 8"/></svg>`,
  collapse: `<svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/></svg>`,
  chevron: `<svg class="chev" viewBox="0 0 24 24"><path d="M6 9l6 6 6-6"/></svg>`,
  close: `<svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg>`,
  noticeInfo: `<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/></svg>`,
  noticeSuccess: `<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M8 12.5l2.5 2.5L16 9.5"/></svg>`,
  noticeWarning: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4l9 16H3z"/><path d="M12 10v4M12 17h.01"/></svg>`,
  noticeError: `<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7v6M12 16h.01"/></svg>`,
  dashboard: `<svg viewBox="0 0 24 24"><rect x="3" y="3" width="8" height="8" rx="1.5"/><rect x="13" y="3" width="8" height="8" rx="1.5"/><rect x="3" y="13" width="8" height="8" rx="1.5"/><rect x="13" y="13" width="8" height="8" rx="1.5"/></svg>`,
  requests: `<svg viewBox="0 0 24 24"><path d="M5 4h9l5 5v11a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1z"/><path d="M8 12h8M8 16h5"/></svg>`,
  documents: `<svg viewBox="0 0 24 24"><path d="M6 3h9l4 4v14a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z"/><path d="M9 12h6M9 16h6"/></svg>`,
  messages: `<svg viewBox="0 0 24 24"><path d="M4 5h16v11H8l-4 4V5z"/></svg>`,
  activities: `<svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/></svg>`,
  reports: `<svg viewBox="0 0 24 24"><path d="M4 20V10M12 20V4M20 20v-7"/></svg>`,
  plus: `<svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>`,
  chevDown: `<svg viewBox="0 0 24 24"><path d="M6 9l6 6 6-6"/></svg>`,
  settings: `<svg viewBox="0 0 24 24"><path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/></svg>`,
  shield: `<svg viewBox="0 0 24 24"><path d="M12 3l7 3v5c0 5-3.5 8.5-7 10-3.5-1.5-7-5-7-10V6l7-3z"/><path d="M9 12l2 2 4-4"/></svg>`,
  archive: `<svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="5" rx="1"/><path d="M5 9v10a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V9"/><path d="M10 13h4"/></svg>`,
  calendar: `<svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/></svg>`,
  building: `<svg viewBox="0 0 24 24"><path d="M4 21V5a1 1 0 0 1 1-1h9a1 1 0 0 1 1 1v16"/><path d="M15 10h4a1 1 0 0 1 1 1v10"/><path d="M3 21h18M8 8h3M8 12h3M8 16h3"/></svg>`,
  gear: `<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1"/></svg>`,
  users: `<svg viewBox="0 0 24 24"><circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><path d="M16 4.5a3.5 3.5 0 0 1 0 7M18 14.5a6.5 6.5 0 0 1 3.5 5.5"/></svg>`,
  back: `<svg viewBox="0 0 24 24"><path d="M19 12H5M11 6l-6 6 6 6"/></svg>`,
  forward: `<svg viewBox="0 0 24 24"><path d="M5 12h14M13 6l6 6-6 6"/></svg>`,
  chevRight: `<svg viewBox="0 0 24 24"><path d="M9 6l6 6-6 6"/></svg>`,
  check: `<svg viewBox="0 0 24 24"><path d="M4 12l5 5L20 6"/></svg>`,
  reassign: `<svg viewBox="0 0 24 24"><path d="M17 2l4 4-4 4"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><path d="M7 22l-4-4 4-4"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/></svg>`,
  swap: `<svg viewBox="0 0 24 24"><path d="M3.6 8.4H18M14.4 12L18 8.4l-3.6-3.6M20.4 15.6H6M9.6 19.2L6 15.6 9.6 12"/></svg>`,
  clock: `<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8"/><path d="M12 8v4l3 2"/></svg>`,
  download: `<svg viewBox="0 0 24 24"><path d="M12 3v12M12 15l-4-4M12 15l4-4"/><path d="M4 17v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3"/></svg>`,
  trash: `<svg viewBox="0 0 24 24"><path d="M4 7h16M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2M6 7l1 13a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1l1-13"/></svg>`,
  uploadSmall: `<svg viewBox="0 0 24 24"><path d="M12 16V4M12 4l-4 4M12 4l4 4"/><path d="M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3"/></svg>`,
  // Paperclip and file icons (Request Detail / Conversation), drawn on a 20 grid.
  paperclip: `<svg viewBox="0 0 20 20"><path d="M15.48 9.82l-5.3 5.31a3.75 3.75 0 0 1-5.31-5.31l5.84-5.83a2.5 2.5 0 0 1 3.53 3.53l-5.48 5.48a1.25 1.25 0 0 1-1.77-1.77l4.42-4.42"/></svg>`,
  file: `<svg viewBox="0 0 20 20"><path d="M16 7.5L11 2.5H5.5C4.67 2.5 4 3.17 4 4v12c0 .83.67 1.5 1.5 1.5h9c.83 0 1.5-.67 1.5-1.5V7.5zM11 2.5v5h5"/><path d="M7 10.5h6M7 13.5h4"/></svg>`
};

function el(html){
  const t = document.createElement('template');
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
}

/** Send Reminder: emails the requester that the Legal Office is
    still waiting for them. The server allows one every 24 hours per request
    and says so if one was already sent; the reply is shown next to the button.
    Returns true when the reminder was sent. */
async function sendReminderFrom(button, trackingId){
  if(!(await confirmDialog({
    title: 'Send a reminder?',
    message: `Email the requester of ${trackingId} a reminder that the Legal Office is waiting for them.`,
    confirmLabel: 'Send reminder'
  }))) return false;
  button.disabled = true;
  const r = await apiFetch(`/requests/${encodeURIComponent(trackingId)}/remind`, {method: 'POST'});
  button.disabled = false;
  let note = button.parentElement.querySelector('.reminder-note');
  if(!note){
    note = document.createElement('div');
    note.className = 'reminder-note';
    note.setAttribute('role', 'status');
    button.parentElement.appendChild(note);
  }
  note.classList.toggle('error', !r.ok);
  note.textContent = r.ok ? 'Reminder sent.' : (typeof r.error === 'string' ? r.error : 'Could not send the reminder.');
  return r.ok;
}

/** Table cell: the due date, and under it
    "Overdue" or "Due soon" as coloured text. No date: "No due date". */
function dueCell(row){
  const flag = row.deadline === 'overdue' ? '<span class="due-flag overdue">Overdue</span>'
    : row.deadline === 'approaching' ? '<span class="due-flag approaching">Due soon</span>' : '';
  const date = (row.due && row.due !== '\u2014')
    ? escapeHtml(row.due) : '<span class="due-none">No due date</span>';
  return `<div class="cell-stack">${date}${flag}</div>`;
}

/* Status label colours: new, in progress,
   with the attorney, done, stopped. Unknown statuses use "in progress". */
const STATUS_TONES = {
  'Submitted': 'new',
  'Received': 'progress', 'For Validation': 'progress', 'Under Review': 'progress',
  'Clarificatory Meeting': 'progress', 'Processing': 'progress',
  'For Attorney Review': 'attorney', 'Approved': 'attorney',
  'Completed': 'done',
  'Cancelled': 'stopped', 'Rejected': 'stopped'
};
// text: other wording for the same status (For Approval says "Waiting for approval").
function statusLabel(status, text){
  const tone = STATUS_TONES[status] || 'progress';
  return `<span class="status-label tone-${tone}">${escapeHtml(text || status)}</span>`;
}

/** Table cell: the status label, plus "Waiting on client" under it. */
function statusCell(row){
  const note = row.isWaitingForClient ? '<span class="status-note">Waiting on client</span>' : '';
  return `<div class="cell-stack status-stack">${statusLabel(row.status)}${note}</div>`;
}

/* Client column: office/agency (or name), plus a quiet note under it while
   the client still needs the Legal Office's verification. */
const CLIENT_NOTES = {unverified: 'Not verified', pending: 'Letter to check', expired: 'Verification expired',
                      declined: 'Declined', revoked: 'Revoked'};
function clientCell(row){
  const note = CLIENT_NOTES[row.clientStatus];
  return `<div class="cell-stack">${escapeHtml(row.client)}${note ? `<span class="client-note">${escapeHtml(note)}</span>` : ''}</div>`;
}

/**
 * Build a <tbody> of plain data rows for a simple table view.
 * columns: array of {key, render?} — render(row) returns cell HTML; defaults to row[key]
 */
function buildTableBody(rows, columns){
  const tbody = document.createElement('tbody');
  rows.forEach(row=>{
    const tr = document.createElement('tr');
    tr.className = 'clickable-row';
    columns.forEach(col=>{
      const td = document.createElement('td');
      td.dataset.col = col.key;   // lets the CSS size some columns (e.g. service names wrap)
      td.innerHTML = col.render ? col.render(row) : escapeHtml(row[col.key] ?? '');
      tr.appendChild(td);
    });
    // Last column: a plain "open" arrow (the whole row opens the request).
    const tdOpen = document.createElement('td');
    tdOpen.className = 'row-open';
    tdOpen.innerHTML = `<span aria-hidden="true">${ICONS.chevRight}</span>`;
    tr.appendChild(tdOpen);
    tr.tabIndex = 0;   // keyboard: Tab to a row, Enter opens it
    const open = () => { window.location.href = 'request-detail.html?id=' + encodeURIComponent(row.id); };
    tr.addEventListener('click', open);
    tr.addEventListener('keydown', (e)=>{ if(e.key === 'Enter') open(); });
    tbody.appendChild(tr);
  });
  return tbody;
}

/** One due-date group of "Assigned to me": a header
    row with the group's name and count, then its rows. Empty groups are
    left out. tone 'danger' colours the name red. */
function buildGroupedTableBody(rows, columns, label, tone){
  const tbody = buildTableBody(rows, columns);
  const head = document.createElement('tr');
  head.className = 'queue-group' + (tone ? ' ' + tone : '');
  head.innerHTML = `<th scope="colgroup" colspan="${columns.length + 1}"><span class="queue-group-name">${escapeHtml(label)}</span>
    <span class="queue-group-count">${rows.length}</span></th>`;
  tbody.prepend(head);
  return tbody;
}

/** A card on "Awaiting client": what staff asked
    the client for, with a reminder (email) or the number to call. */
function renderAwaitingClientCard(row){
  const url = 'request-detail.html?id=' + encodeURIComponent(row.id);
  const due = row.deadline === 'overdue' ? '<span class="await-due overdue">Overdue</span>'
    : `<span class="await-due">${row.due && row.due !== '—' ? 'Due ' + escapeHtml(row.due) : 'No due date'}</span>`;
  const meta = [row.client, row.officer, 'Filed ' + row.submitted]
    .map(escapeHtml).join('<span class="await-dot" aria-hidden="true">·</span>');
  // No email on file: LEXIS can't send a reminder, so staff are told to call.
  let contact;
  if(row.hasEmail !== false){
    contact = '<button type="button" class="btn btn-secondary btn-compact send-reminder-btn">Send reminder</button>';
  }else if(row.phone){
    contact = `<span class="await-call">No email on file. Call <a href="tel:${escapeHtml(row.phone.replace(/[^\d+]/g, ''))}">${escapeHtml(row.phone)}</a></span>`;
  }else{
    contact = '<span class="await-call">No email or phone number on file.</span>';
  }
  const note = row.staffNoteToClient
    ? escapeHtml(row.staffNoteToClient)
    : '<span class="await-note-empty">No note was written when this request was set to waiting.</span>';
  const node = el(`
    <article class="await-card">
      <div class="await-top"><a class="await-id" href="${url}">${escapeHtml(row.id)}</a>${due}</div>
      <p class="await-service">${escapeHtml(row.service)}</p>
      <p class="await-meta">${meta}</p>
      <div class="await-note"><p class="await-note-label">What we asked the client for</p><p class="await-note-text">${note}</p></div>
      <div class="await-actions">
        ${contact}
        <a class="btn btn-tertiary btn-compact" href="${url}&amp;tab=conversation">Open conversation</a>
      </div>
    </article>
  `);
  const reminderBtn = node.querySelector('.send-reminder-btn');
  if(reminderBtn) reminderBtn.addEventListener('click', (e)=>{
    e.stopPropagation();
    sendReminderFrom(e.currentTarget, row.id);
  });
  // The whole card opens the request; buttons and links do their own thing
  // (a tap on the phone number only dials).
  node.addEventListener('click', (e)=>{
    if(e.target.closest('.btn, a')) return;
    window.location.href = url;
  });
  return node;
}

/* Shows an empty/error row under a table so a failed fetch never looks
   the same as a genuinely empty result. */
function showTableState(table, rows, error, emptyMessage){
  if(rows.length) return;
  const colCount = table.querySelectorAll('thead th').length || 1;
  const body = table.querySelector('tbody')
            || table.appendChild(document.createElement('tbody'));
  const tr = document.createElement('tr');
  const td = document.createElement('td');
  td.colSpan = colCount;
  td.className = error ? 'table-state error' : 'table-state empty';
  td.textContent = error || emptyMessage;
  tr.appendChild(td);
  body.appendChild(tr);
}

/* Anything typed by a user (messages, file names, names) must be escaped
   before it goes into innerHTML, or it could run as code in the browser. */
function escapeHtml(value){
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
