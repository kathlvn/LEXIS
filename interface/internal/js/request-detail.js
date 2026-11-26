// Page frame. Kept here, not inline in the page: the
// Content-Security-Policy forbids inline scripts.
renderTopbar();
renderSidebar('requests-all');

const TABS = [
  {key:'overview',     label:'Overview'},
  {key:'processing',   label:'Processing'},
  {key:'documents',    label:'Documents'},
  {key:'history',      label:'History'},
  {key:'conversation', label:'Conversation'}
];

/** Turns a server error (a string, or FastAPI's list of field errors) into
    one readable sentence. */
function friendlyError(error){
  if(typeof error === 'string') return error;
  if(Array.isArray(error) && error[0] && error[0].msg) return error[0].msg.replace(/^Value error, /, '');
  return 'Something went wrong. Please try again.';
}

function getRequestIdFromUrl(){
  const params = new URLSearchParams(window.location.search);
  return params.get('id');
}

let currentRecord = null;
const requestedTab = new URLSearchParams(window.location.search).get('tab');
let activeTab = TABS.some(t => t.key === requestedTab) ? requestedTab : 'overview';

/* ---------------- Phone-only clients ----------------
   A client with no email gets no emails from LEXIS (there is no SMS sender).
   After a save that WOULD have emailed them (the same cases the server
   emails in), the page asks staff to call instead and lists what to tell
   them, until dismissed. Frontend only; no staff notifications.
   "Client contacted" records the call in the staff History, as proof
   the client was told, and clears the list. */
let callReminders = [];
let callNote = null;   // {text, error} shown once under the banner buttons

/* "Print slip": what the client takes home instead of a confirmation
   email. Request ID, service, date filed, office phone and hours only, and
   NEVER the description of the legal matter. */
const OFFICE_PHONE = '[OFFICE PHONE: to be provided by the Legal Office]';   // placeholder until the office gives it
const OFFICE_HOURS = 'Monday to Friday, 8:00 AM to 5:00 PM, except holidays';  // same text as home.html; change both

function remindToCall(line){
  if(!currentRecord || currentRecord.requester.hasEmail) return;
  callReminders.push(line);
  renderPhoneOnly();
}

function renderPhoneOnly(){
  const slot = document.getElementById('phoneOnlySlot');
  if(!slot) return;
  const r = currentRecord;
  if(r.requester.hasEmail){ slot.innerHTML = ''; return; }
  const phone = r.requester.phone;
  const how = phone
    ? `Update the client by phone: <a href="tel:${escapeHtml(phone.replace(/[^\d+]/g, ''))}">${escapeHtml(phone)}</a>`
    : 'There is no contact number on file either.';
  const calls = callReminders.length ? `
    <div class="phone-only-call">
      <b>Call the client now${phone ? ' at ' + escapeHtml(phone) : ''}.</b> LEXIS couldn't email them about:
      <ul>${callReminders.map(line => `<li>${escapeHtml(line)}</li>`).join('')}</ul>
    </div>` : '';
  const buttons = `
    <div class="phone-only-actions">
      ${phone ? `<button type="button" class="btn btn-primary btn-compact" id="clientContactedBtn">Client contacted</button>` : ''}
      ${callReminders.length ? `<button type="button" class="btn btn-secondary btn-compact" id="callDismissBtn">Dismiss</button>` : ''}
      <button type="button" class="btn btn-secondary btn-compact" id="printSlipBtn">Print slip</button>
      ${callNote ? `<span class="phone-only-note${callNote.error ? ' error' : ''}">${escapeHtml(callNote.text)}</span>` : ''}
    </div>`;
  slot.innerHTML = `
    <div class="rd-notice phone-only-banner" role="status">
      <span class="rd-notice-icon" aria-hidden="true">${ICONS.noticeInfo}</span>
      <div class="rd-notice-text"><p class="rd-notice-title">No email on file</p><p>${how}</p>${calls}${buttons}</div>
    </div>`;
  callNote = null;
  const dismiss = document.getElementById('callDismissBtn');
  if(dismiss) dismiss.addEventListener('click', () => { callReminders = []; renderPhoneOnly(); });
  const contacted = document.getElementById('clientContactedBtn');
  if(contacted) contacted.addEventListener('click', recordCall);
  document.getElementById('printSlipBtn').addEventListener('click', printSlip);
}

function printSlip(){
  const r = currentRecord;
  let slip = document.getElementById('printSlip');
  if(!slip){
    slip = document.createElement('div');
    slip.id = 'printSlip';
    document.body.appendChild(slip);
  }
  slip.innerHTML = `
    <div class="slip">
      <div class="slip-school">Eastern Samar State University</div>
      <div class="slip-office">Legal Office</div>
      <div class="slip-title">Request slip</div>
      <dl>
        <dt>Request ID</dt><dd class="slip-id">${escapeHtml(r.id)}</dd>
        <dt>Service</dt><dd>${escapeHtml(r.title)}</dd>
        <dt>Date filed</dt><dd>${escapeHtml(r.submitted)}</dd>
        <dt>Office phone</dt><dd>${escapeHtml(OFFICE_PHONE)}</dd>
        <dt>Office hours</dt><dd>${escapeHtml(OFFICE_HOURS)}</dd>
      </dl>
      <p class="slip-note">Keep this slip. Give the Request ID when you call or visit the Legal Office about this request.</p>
    </div>`;
  document.body.classList.add('print-slip');
  window.addEventListener('afterprint', () => document.body.classList.remove('print-slip'), {once: true});
  window.print();
}

async function recordCall(){
  if(!(await confirmDialog({
    title: 'Record the call in History?',
    message: 'Record that you called the client and told them the latest update. This entry can’t be removed.',
    confirmLabel: 'Record call'
  }))) return;
  const btn = document.getElementById('clientContactedBtn');
  btn.disabled = true;
  const {ok, error} = await recordClientContacted(currentRecord.id);
  if(!ok){
    callNote = {text: (error ? friendlyError(error) : 'Could not record the call.'), error: true};
    renderPhoneOnly();
    return;
  }
  callReminders = [];
  callNote = {text: 'Recorded in History.'};
  const {record} = await fetchRequestDetail(currentRecord.id);
  if(record) currentRecord = record;
  renderShell();
}

/* ---------------- Page frame ----------------
   Heading (back button, Request ID + status, service, facts, actions), an
   optional panel under it (reassign, request reassignment, reopen), the
   notices (closed, reassignment, read-only, phone-only), then two columns:
   the request card with its tabs and the Client card. */
const BACK_LINK = {href: 'requests-all.html', label: 'Back to the list'};
let headerPanel = null;   // 'reassign' | 'requestReassign' | 'reopen' | null

function myRole(){ return window.currentStaff ? window.currentStaff.role : ''; }
function isAttorneyRole(){ return ATTORNEY_ROLES.includes(myRole()); }
function isMine(r){ return !!(window.currentStaff && r.assignedToId && r.assignedToId === window.currentStaff.id); }

async function init(){
  const id = getRequestIdFromUrl();
  if(!id){
    renderMessageCard({title: 'No request selected',
      text: 'Choose a request from one of the lists, or search for it in the top bar.'});
    return;
  }
  renderLoading();
  const {record, error, status} = await fetchRequestDetail(id);
  if(!record){
    if(status === 404){
      renderMessageCard({title: 'Request not found',
        text: `No request has the ID ${id}. Check the ID in the link, or search for the request in the top bar.`});
    }else if(!status || status >= 500){
      renderMessageCard({title: 'Couldn’t load this request',
        text: 'LEXIS couldn’t reach the server. Check your connection, then try again.',
        note: 'If it keeps happening, the server may be down. Nothing you saved earlier is lost.', retry: true});
    }else{
      renderMessageCard({title: 'Couldn’t open this request', text: friendlyError(error)});
    }
    return;
  }
  currentRecord = record;
  renderShell();
  loadClientBox(record.id);   // client verification box (client-box.js)
  showCreatedMessage();
}

/** Loading: the frame with grey placeholders. */
function renderLoading(){
  const bar = (w, h) => `<span class="skel" style="width:${w}px;height:${h || 12}px"></span>`;
  document.getElementById('detailShell').innerHTML = `
    <div class="rd-heading" aria-busy="true">
      ${backButtonHtml(BACK_LINK)}
      <div class="rd-title rd-title-loading">
        ${bar(174, 20)}${bar(124, 10)}${bar(286, 10)}
        <p class="rd-loading-text" role="status">Loading request…</p>
      </div>
    </div>
    <div class="rd-columns" aria-hidden="true">
      <div class="rd-card rd-request">
        <div class="rd-tabs">${TABS.map((t, i) => `<span class="rd-tab${i ? '' : ' current'}">${t.label}</span>`).join('')}</div>
        <div class="rd-skel-body">${bar(112, 14)}${bar(448)}${bar(400)}${bar(437)}${bar(325)}<span class="skel skel-block"></span></div>
      </div>
      <div class="rd-card rd-client rd-skel-body">${bar(56, 14)}${bar(106)}${bar(125)}${bar(100)}${bar(137)}<span class="rd-divider"></span>${bar(75)}${bar(112)}${bar(68)}${bar(93)}<span class="skel skel-button"></span></div>
    </div>`;
  wireBackButton(document.querySelector('#detailShell .back-btn'));
}

/** "Not found" and "Couldn't load". */
function renderMessageCard(m){
  document.getElementById('detailShell').innerHTML = `
    <div class="rd-card rd-message">
      <h1 class="rd-message-title">${escapeHtml(m.title)}</h1>
      <p class="rd-message-text">${escapeHtml(m.text)}</p>
      ${m.note ? `<p class="rd-message-note">${escapeHtml(m.note)}</p>` : ''}
      <div class="rd-message-actions">
        ${m.retry ? '<button type="button" class="btn btn-primary" id="rdRetryBtn">Try again</button>' : ''}
        <a class="btn ${m.retry ? 'btn-tertiary' : 'btn-primary'}" href="requests-all.html">Go to All requests</a>
      </div>
    </div>`;
  const retry = document.getElementById('rdRetryBtn');
  if(retry) retry.addEventListener('click', () => window.location.reload());
}

/** After a walk-in is encoded, new-request.js opens this page with
    created=1 (and emailed=1 / docs=failed). No personal data is in the
    address; the flags are removed so a refresh doesn't repeat the message. */
function showCreatedMessage(){
  const params = new URLSearchParams(window.location.search);
  if(params.get('created') !== '1') return;
  const docsFailed = params.get('docs') === 'failed';
  showPageMessage(`Request ${currentRecord.id} was created.`
    + (params.get('emailed') === '1' ? ' The Request ID was emailed to the client.' : ' Give the client the Request ID.')
    + (docsFailed ? '\nThe documents were NOT attached. Add them on the Documents tab.' : ''),
    docsFailed ? 'warning' : 'success', {keep: true});
  ['created', 'emailed', 'docs'].forEach(k => params.delete(k));
  history.replaceState(null, '', window.location.pathname + '?' + params.toString());
}

/** The reason line on the closed notice: what the client was told, or,
    when they withdrew it themselves, the reason they gave (if any). */
function closedReasonHtml(r){
  if(!r.closureReason) return '';
  if(r.closedBy === 'Requester'){
    const own = r.closureReason.replace(/^Withdrawn by the requester[:.]\s*/, '');
    return own ? `<p>The client’s reason: “${escapeHtml(own)}”</p>` : '';
  }
  return `<p>Reason sent to the client: “${escapeHtml(r.closureReason)}”</p>`;
}

/* ---------- heading ---------- */
function headingHtml(r){
  const mine = isMine(r);
  const canReassign = myRole() === 'head_attorney' && !r.isClosed;
  const due = r.dueShort
    ? `<b>${escapeHtml(r.dueShort)}</b>${r.deadline === 'overdue' ? '<span class="rd-due-flag overdue">Overdue</span>'
        : r.deadline === 'approaching' ? '<span class="rd-due-flag approaching">Due soon</span>' : ''}`
    : 'No set period';
  const actions = [];
  // Legal assistant on their own open request.
  if(myRole() === 'legal_assistant' && mine && !r.isClosed && !r.reassignmentRequested){
    actions.push(`<button type="button" class="btn btn-secondary" data-panel="requestReassign" aria-expanded="${headerPanel === 'requestReassign'}">${ICONS.swap} Request reassignment</button>`);
  }
  if(isAttorneyRole() && r.isClosed){
    actions.push(`<button type="button" class="btn btn-secondary" data-panel="reopen" aria-expanded="${headerPanel === 'reopen'}">Reopen</button>`);
  }
  // Moving the status ("Return for revision" + "Approve" for the
  // attorney, "Update progress" for the assigned legal assistant).
  const move = statusMoves(r);
  if(move.canReturn){
    actions.push(`<button type="button" class="btn btn-secondary" data-panel="return" aria-expanded="${headerPanel === 'return'}">${ICONS.back} Return for revision</button>`);
  }
  if(move.advance){
    actions.push(`<button type="button" class="btn btn-primary" id="rdAdvanceBtn">${move.advance === 'Update progress' ? '' : ICONS.check + ' '}${move.advance}</button>`);
  }
  return `
    <div class="rd-heading">
      ${backButtonHtml(BACK_LINK)}
      <div class="rd-title">
        <div class="rd-id-row">
          <h1 class="rd-id">${escapeHtml(r.id)}</h1>
          ${statusLabel(r.status)}
          ${r.processing.returned ? '<span class="status-label tone-attorney">Returned for revision</span>' : ''}
          ${r.overview.isWaitingForClient && !r.isClosed ? '<span class="status-label tone-waiting">Waiting on client</span>' : ''}
        </div>
        <p class="rd-service">${escapeHtml(r.title)}</p>
        <div class="rd-facts">
          <span class="rd-fact"><span class="rd-fact-label">Due</span>${due}</span>
          <span class="rd-fact"><span class="rd-fact-label">Filed</span>${escapeHtml(r.submittedShort)}</span>
          <span class="rd-fact"><span class="rd-fact-label">Updated</span>${escapeHtml(r.updatedShort)}</span>
          <span class="rd-fact"><span class="rd-fact-label">Assigned to</span>${escapeHtml(r.assignedTo)}${mine ? ' (you)' : ''}
            ${canReassign ? `<button type="button" class="btn btn-tertiary rd-fact-btn" data-panel="reassign" aria-expanded="${headerPanel === 'reassign'}">${r.assignedToId ? 'Reassign' : 'Assign'}</button>` : ''}
          </span>
        </div>
      </div>
      ${actions.length ? `<div class="rd-actions">${actions.join('')}</div>` : ''}
    </div>`;
}

/* ---------- notices under the heading ---------- */
function noticeHtml(icon, tone, title, lines, extra){
  return `
    <div class="rd-notice${tone ? ' ' + tone : ''}" role="status">
      <span class="rd-notice-icon" aria-hidden="true">${ICONS[icon]}</span>
      <div class="rd-notice-text">
        <p class="rd-notice-title">${title}</p>
        ${lines.join('')}
        ${extra || ''}
      </div>
    </div>`;
}

function noticesHtml(r){
  const out = [];
  const name = escapeHtml(r.assignedTo);
  if(r.isClosed){
    out.push(noticeHtml('noticeInfo', '',
      `${escapeHtml(r.closedLabel)}${r.closedShort ? ' on ' + escapeHtml(r.closedShort) : ''}${r.closedBy && r.closedBy !== 'Requester' ? ' by ' + escapeHtml(r.closedBy) : ''}`,
      [closedReasonHtml(r),
       '<p class="rd-notice-note">It can’t be moved forward, and the client can’t add documents or messages. Only an attorney can reopen it.</p>']));
  }else if(r.reassignmentRequested && myRole() === 'head_attorney' && headerPanel !== 'reassign'){
    out.push(noticeHtml('swap', 'brand', `${name} asked you to reassign this request`,
      [r.reassignmentReason ? `<p>“${escapeHtml(r.reassignmentReason)}”</p>` : '',
       `<p class="rd-notice-note">Dismissing keeps it with ${name} and lets them ask again later.</p>`],
      `<div class="rd-notice-actions">
         <button type="button" class="btn btn-primary btn-compact" data-panel="reassign">${ICONS.swap} Reassign…</button>
         <button type="button" class="btn btn-secondary btn-compact" id="rdDismissBtn">Dismiss request</button>
       </div>`));
  }else if(r.reassignmentRequested && isMine(r)){
    out.push(noticeHtml('clock', '', 'Reassignment requested',
      ['<p>The Head Attorney will either give this request to someone else or keep it with you. Until then it stays assigned to you, so keep working on it.</p>',
       r.reassignmentReason ? `<p class="rd-notice-note">Your reason: “${escapeHtml(r.reassignmentReason)}”</p>` : '']));
  }
  // A legal assistant may only change requests assigned to them (the server
  // refuses the rest), so say so instead of letting the buttons fail.
  if(myRole() === 'legal_assistant' && !isMine(r) && !r.isClosed){
    out.push(r.assignedToId
      ? noticeHtml('noticeInfo', '', `Assigned to ${name}`,
          [`<p>You can read this request, but only ${name} or an attorney can change it.</p>`])
      : noticeHtml('noticeInfo', '', 'Not assigned yet',
          ['<p>You can read this request, but only the legal assistant it is assigned to, or an attorney, can change it.</p>']));
  }
  return out.join('');
}

/* ---------- the page ---------- */
function renderShell(){
  const r = currentRecord;
  const shell = document.getElementById('detailShell');
  shell.innerHTML = `
    ${headingHtml(r)}
    <div id="rdHeaderPanel"></div>
    ${noticesHtml(r)}
    <div id="phoneOnlySlot"></div>
    <div class="rd-columns">
      <section class="rd-card rd-request" aria-label="Request">
        <div class="rd-tabs" role="tablist" aria-label="Request sections">
          ${TABS.map(t => `<button type="button" class="rd-tab${t.key === activeTab ? ' current' : ''}" role="tab" id="rdTab-${t.key}"
              aria-selected="${t.key === activeTab}" aria-controls="tabPanel" tabindex="${t.key === activeTab ? 0 : -1}" data-tab="${t.key}">${t.label}</button>`).join('')}
        </div>
        <div class="detail-tab-panel" id="tabPanel" role="tabpanel" aria-labelledby="rdTab-${activeTab}"></div>
      </section>
      <aside class="rd-card rd-client side-sticky" aria-labelledby="rdClientTitle">
        <h2 class="rd-card-title" id="rdClientTitle">Client</h2>
        <div class="client-box" id="clientBox"></div>
      </aside>
    </div>`;

  wireBackButton(shell.querySelector('.back-btn'));
  renderPhoneOnly();

  // Tabs: click, or the arrow keys between them (WAI-ARIA tabs pattern).
  const tabButtons = [...shell.querySelectorAll('.rd-tab')];
  tabButtons.forEach((btn, i) => {
    btn.addEventListener('click', () => { activeTab = btn.dataset.tab; renderShell(); document.getElementById('rdTab-' + activeTab).focus(); });
    btn.addEventListener('keydown', (e) => {
      const step = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0;
      if(!step) return;
      e.preventDefault();
      tabButtons[(i + step + tabButtons.length) % tabButtons.length].click();
    });
  });

  // Buttons that open a panel under the heading (a second click closes it).
  shell.querySelectorAll('[data-panel]').forEach(btn => {
    btn.addEventListener('click', () => {
      headerPanel = headerPanel === btn.dataset.panel ? null : btn.dataset.panel;
      renderShell();
      const first = document.querySelector('#rdHeaderPanel input, #rdHeaderPanel textarea');
      if(first) first.focus();
    });
  });
  const dismissBtn = document.getElementById('rdDismissBtn');
  if(dismissBtn) dismissBtn.addEventListener('click', () => dismissReassignment(dismissBtn));
  const advanceBtn = document.getElementById('rdAdvanceBtn');
  if(advanceBtn) advanceBtn.addEventListener('click', () => advanceStatus(advanceBtn));

  renderHeaderPanel();
  renderClientBox();   // from the last load; reloaded only after a client action
  renderTabPanel();
}

async function reloadRecord(){
  const {record} = await fetchRequestDetail(currentRecord.id);
  if(record) currentRecord = record;
  headerPanel = null;
  renderShell();
}

async function dismissReassignment(btn){
  btn.disabled = true;
  const {ok, error} = await reassignRequest(currentRecord.id, null);
  if(!ok){ btn.disabled = false; showPageMessage(friendlyError(error), 'error'); return; }
  showPageMessage('Reassignment request dismissed. The request stays with ' + currentRecord.assignedTo + '.', 'success');
  reloadRecord();
}

/* ---------- panels under the heading (Reassign, Reassignment request,
   Reopen, Return) ---------- */
function panelFrame(title, body){
  return `
    <section class="rd-card rd-panel" aria-labelledby="rdPanelTitle">
      <div class="rd-panel-head">
        <h2 class="rd-card-title" id="rdPanelTitle">${title}</h2>
        <button type="button" class="icon-btn rd-panel-close" id="rdPanelClose" aria-label="Close">${ICONS.close}</button>
      </div>
      <div class="rd-panel-body">${body}
        <p class="rd-panel-msg" id="rdPanelMsg" role="alert"></p>
      </div>
    </section>`;
}

function closeHeaderPanel(){ headerPanel = null; renderShell(); }

async function renderHeaderPanel(){
  const slot = document.getElementById('rdHeaderPanel');
  const r = currentRecord;
  if(!headerPanel){ slot.innerHTML = ''; return; }
  if(headerPanel === 'reassign') return renderReassignPanel(slot, r);
  if(headerPanel === 'requestReassign') return renderRequestReassignPanel(slot);
  if(headerPanel === 'reopen') return renderReopenPanel(slot, r);
  if(headerPanel === 'return') return renderReturnPanel(slot, r);
}

async function renderReassignPanel(slot, r){
  const name = escapeHtml(r.assignedTo);
  slot.innerHTML = panelFrame(r.assignedToId ? 'Reassign request' : 'Assign request',
    '<p class="rd-panel-intro">Loading the legal assistants…</p>');
  document.getElementById('rdPanelClose').addEventListener('click', closeHeaderPanel);
  const {rows, error} = await fetchStaff();
  if(headerPanel !== 'reassign') return;          // closed while loading
  if(error){ document.querySelector('#rdHeaderPanel .rd-panel-intro').textContent = friendlyError(error); return; }
  const assistants = rows.filter(s => s.role === 'legal_assistant' && s.is_active);
  const options = assistants.map(s => {
    const now = s.id === r.assignedToId;
    return `<label class="rd-option${now ? ' disabled' : ''}">
        <input type="radio" name="rdOfficer" value="${s.id}"${now ? ' disabled' : ''}>
        <span>${escapeHtml(s.full_name)}</span>${now ? '<span class="rd-option-note">Assigned now</span>' : ''}
      </label>`;
  }).join('');
  slot.innerHTML = panelFrame(r.assignedToId ? 'Reassign request' : 'Assign request', `
    <p class="rd-panel-intro">Choose who handles this request from now on. ${r.assignedToId ? `They and ${name} both get a notification.` : 'They get a notification.'}</p>
    ${r.reassignmentRequested && r.reassignmentReason ? `
      <div class="rd-quote"><span class="rd-quote-label">${name}’s reason</span><p>“${escapeHtml(r.reassignmentReason)}”</p></div>` : ''}
    <fieldset class="rd-field">
      <legend class="rd-label">Give it to</legend>
      ${assistants.length ? `<div class="rd-options">${options}</div>` : '<p class="rd-hint">There are no active legal assistants.</p>'}
      <p class="rd-hint">Only active legal assistants are listed.</p>
    </fieldset>
    <div class="rd-panel-actions">
      <button type="button" class="btn btn-primary btn-compact" id="rdReassignBtn" disabled>${ICONS.swap} <span>Reassign</span></button>
      ${r.reassignmentRequested ? '<button type="button" class="btn btn-secondary btn-compact" id="rdPanelDismissBtn">Dismiss request</button>' : ''}
      <button type="button" class="btn btn-tertiary btn-compact" id="rdPanelCancel">Cancel</button>
    </div>`);
  const confirmBtn = document.getElementById('rdReassignBtn');
  const msg = document.getElementById('rdPanelMsg');
  document.getElementById('rdPanelClose').addEventListener('click', closeHeaderPanel);
  document.getElementById('rdPanelCancel').addEventListener('click', closeHeaderPanel);
  slot.querySelectorAll('input[name="rdOfficer"]').forEach(input => {
    input.addEventListener('change', () => {
      slot.querySelectorAll('.rd-option').forEach(o => o.classList.toggle('selected', o.contains(input)));
      confirmBtn.disabled = false;
      confirmBtn.querySelector('span').textContent = (r.assignedToId ? 'Reassign to ' : 'Assign to ') + input.closest('.rd-option').querySelector('span').textContent;
    });
  });
  confirmBtn.addEventListener('click', async () => {
    const chosen = slot.querySelector('input[name="rdOfficer"]:checked');
    if(!chosen){ msg.textContent = 'Choose a legal assistant.'; return; }
    confirmBtn.disabled = true;
    const {ok, error: saveError} = await reassignRequest(currentRecord.id, Number(chosen.value));
    if(!ok){ confirmBtn.disabled = false; msg.textContent = friendlyError(saveError); return; }
    showPageMessage(`${currentRecord.id} now goes to ${chosen.closest('.rd-option').querySelector('span').textContent}.`, 'success');
    reloadRecord();
  });
  const panelDismiss = document.getElementById('rdPanelDismissBtn');
  if(panelDismiss) panelDismiss.addEventListener('click', () => dismissReassignment(panelDismiss));
}

function renderRequestReassignPanel(slot){
  slot.innerHTML = panelFrame('Request reassignment', `
    <p class="rd-panel-intro">Tell the Head Attorney why someone else should handle this request. You stay assigned and keep working on it until they decide.</p>
    <div class="rd-field">
      <label class="rd-label" for="rdReasonInput">Reason</label>
      <textarea id="rdReasonInput" class="rd-textarea" maxlength="500" rows="3"></textarea>
      <div class="rd-hint-row"><span class="rd-hint">Only the Head Attorney sees this. The client isn’t told.</span><span class="rd-hint" id="rdReasonCount">0 / 500</span></div>
    </div>
    <div class="rd-panel-actions">
      <button type="button" class="btn btn-primary btn-compact" id="rdSendBtn">Send request</button>
      <button type="button" class="btn btn-secondary btn-compact" id="rdPanelCancel">Cancel</button>
    </div>`);
  const input = document.getElementById('rdReasonInput');
  const msg = document.getElementById('rdPanelMsg');
  input.addEventListener('input', () => { document.getElementById('rdReasonCount').textContent = `${input.value.length} / 500`; });
  document.getElementById('rdPanelClose').addEventListener('click', closeHeaderPanel);
  document.getElementById('rdPanelCancel').addEventListener('click', closeHeaderPanel);
  document.getElementById('rdSendBtn').addEventListener('click', async (e) => {
    const reason = input.value.trim();
    if(!reason){ msg.textContent = 'Write a reason for the Head Attorney.'; input.focus(); return; }
    const btn = e.currentTarget;
    btn.disabled = true;
    const {ok, error} = await requestReassignment(currentRecord.id, reason);
    if(!ok){ btn.disabled = false; msg.textContent = error ? friendlyError(error) : 'Could not send the request.'; return; }
    reloadRecord();
  });
}

function renderReopenPanel(slot, r){
  const name = escapeHtml(r.assignedTo);
  const from = r.workflowStatus ? `It continues from ${escapeHtml(r.workflowStatus)}, where it stopped` : 'It continues from where it stopped';
  const who = r.assignedToId ? `, and stays with ${name}. ${name} is notified` : '';
  const client = r.requester.hasEmail ? ', and the client is emailed that it was reopened.' : '.';
  slot.innerHTML = panelFrame('Reopen request', `
    <p class="rd-panel-intro">${from}${who}${client}</p>
    <div class="rd-field">
      <label class="rd-label" for="rdReopenInput">Why reopen it? <span class="rd-label-note">required</span></label>
      <textarea id="rdReopenInput" class="rd-textarea" maxlength="500" rows="3" placeholder="e.g. Cancelled by mistake."></textarea>
      <span class="rd-hint">Only staff see this. It’s kept in History; the client isn’t told the reason.</span>
    </div>
    <div class="rd-panel-actions">
      <button type="button" class="btn btn-primary btn-compact" id="rdReopenBtn">Reopen request</button>
      <button type="button" class="btn btn-tertiary btn-compact" id="rdPanelCancel">Cancel</button>
    </div>`);
  const input = document.getElementById('rdReopenInput');
  const msg = document.getElementById('rdPanelMsg');
  document.getElementById('rdPanelClose').addEventListener('click', closeHeaderPanel);
  document.getElementById('rdPanelCancel').addEventListener('click', closeHeaderPanel);
  document.getElementById('rdReopenBtn').addEventListener('click', async (e) => {
    const reason = input.value.trim();
    if(!reason){ msg.textContent = 'A reason is required.'; input.focus(); return; }
    const btn = e.currentTarget;
    btn.disabled = true;
    const {ok, error} = await reopenRequest(currentRecord.id, reason);
    if(!ok){ btn.disabled = false; msg.textContent = error ? friendlyError(error) : 'Could not reopen this request.'; return; }
    remindToCall('The request was reopened.');
    reloadRecord();
  });
}

/* Attorney: send forwarded work back to Processing with a comment that
   only staff see (POST /requests/{id}/return). */
function renderReturnPanel(slot, r){
  const name = r.assignedToId ? escapeHtml(r.assignedTo) : '';
  slot.innerHTML = panelFrame('Return for revision', `
    <p class="rd-panel-intro">${name ? `${name} gets it back at Processing, with your comment.` : 'It goes back to Processing with your comment.'} The client only sees that the status changed to Processing.</p>
    <div class="rd-field">
      <label class="rd-label" for="rdReturnInput">What needs to be revised? <span class="rd-label-note">required</span></label>
      <textarea id="rdReturnInput" class="rd-textarea rd-textarea-tall" maxlength="1000" rows="3"></textarea>
      <span class="rd-hint">Only staff see this comment.${name ? ` ${name} sees it on the Processing tab.` : ' It’s shown on the Processing tab.'}</span>
    </div>
    <div class="rd-panel-actions">
      <button type="button" class="btn btn-primary btn-compact" id="rdReturnBtn">${name ? `Return to ${name}` : 'Return to Processing'}</button>
      <button type="button" class="btn btn-tertiary btn-compact" id="rdPanelCancel">Cancel</button>
    </div>`);
  const input = document.getElementById('rdReturnInput');
  const msg = document.getElementById('rdPanelMsg');
  document.getElementById('rdPanelClose').addEventListener('click', closeHeaderPanel);
  document.getElementById('rdPanelCancel').addEventListener('click', closeHeaderPanel);
  document.getElementById('rdReturnBtn').addEventListener('click', async (e) => {
    const comment = input.value.trim();
    if(!comment){ msg.textContent = 'Write what needs to be revised.'; input.focus(); return; }
    const btn = e.currentTarget;
    btn.disabled = true;
    const {ok, error} = await returnForRevision(r.id, comment);
    if(!ok){ btn.disabled = false; msg.textContent = error ? friendlyError(error) : 'Could not return this request.'; return; }
    showPageMessage(`${r.id} was returned to Processing${r.assignedToId ? ' for ' + r.assignedTo : ''}.`, 'success');
    remindToCall('The status is now “Processing”.');   // the comment itself stays internal
    activeTab = 'processing';
    reloadRecord();
  });
}

/* The header's primary button: move the request one step forward.
   It always asks first, since the client is told and the label
   ("Update progress") doesn't name the next step. */
async function advanceStatus(btn){
  const r = currentRecord;
  const {next} = statusMoves(r);
  if(!next) return;
  const clientLine = r.requester.hasEmail ? 'The client is emailed the new status.' : 'The client has no email, so tell them by phone.';
  const ask = next === 'Approved'
      ? {title: 'Approve this request?', message: `The status changes to “Approved”. ${clientLine}`, confirmLabel: 'Approve'}
    : next === 'Completed'
      ? {title: 'Mark this request as completed?', message: `The status changes to “Completed”. A completed request can’t be cancelled or rejected. ${clientLine}`, confirmLabel: 'Mark completed'}
    : next === 'For Attorney Review'
      ? {title: 'Forward it to the attorney?', message: `The status changes to “For Attorney Review” and the attorney is notified. ${isAttorneyRole() ? '' : 'After this, only the attorney can change the status. '}${clientLine}`, confirmLabel: 'Forward for review'}
      : {title: `Move it to “${next}”?`, message: clientLine, confirmLabel: `Move to ${next}`};
  if(!(await confirmDialog(ask))) return;
  btn.disabled = true;
  const {ok, error} = await updateRequestStatus(r.id, next);
  if(!ok){ btn.disabled = false; showPageMessage(error ? friendlyError(error) : 'Could not update the status.', 'error'); return; }
  showPageMessage(`${r.id} is now at “${next}”.`, 'success');
  remindToCall(`The status is now “${next}”.`);
  reloadRecord();
}

function renderTabPanel(){
  const panel = document.getElementById('tabPanel');
  if(activeTab === 'overview') return renderOverview(panel);
  if(activeTab === 'processing') return renderProcessing(panel);
  if(activeTab === 'documents') return renderDocuments(panel);
  if(activeTab === 'history') return renderHistory(panel);
  if(activeTab === 'conversation') return renderConversation(panel);
}

/* ---------------- Overview ---------------- */
let closePanelOpen = false;   // "Cancel or reject" panel inside the tab (attorneys)
let editingNote = false;      // "Edit note" while waiting on the client

async function refreshOverview(){
  const {record} = await fetchRequestDetail(currentRecord.id);
  if(record) currentRecord = record;
  renderShell();
}

function overviewRow(label, valueHtml, extraClass){
  return `<div class="rd-row"><dt>${label}</dt><dd${extraClass ? ` class="${extraClass}"` : ''}>${valueHtml}</dd></div>`;
}

function renderOverview(panel){
  const r = currentRecord;
  const o = r.overview;
  const closed = r.isClosed;
  const canClose = isAttorneyRole() && !closed && r.status !== 'Completed';
  // The server's rule (PATCH /requests/{id}/status): attorneys, or the assigned legal assistant, on an open request.
  const canAsk = !closed && (isAttorneyRole() || isMine(r));
  if(!canClose) closePanelOpen = false;
  if(!canAsk || !o.isWaitingForClient) editingNote = false;

  const docs = o.requiredDocuments.length
    ? `<ul class="rd-docs">${o.requiredDocuments.map(d => `
        <li class="rd-doc">${ICONS.check}<span>${escapeHtml(d.name)}</span><span class="rd-doc-status">${escapeHtml(d.status)}</span></li>`).join('')}</ul>`
    : '<span class="rd-row-muted">None uploaded.</span>';

  panel.innerHTML = `
    <div class="rd-tab-head">
      <h2 class="rd-card-title">Request information</h2>
      ${canClose ? `<button type="button" class="btn btn-danger-outline btn-compact" id="closeRequestBtn" aria-expanded="${closePanelOpen}" aria-controls="closePanelSlot">Cancel or reject</button>` : ''}
    </div>
    <div id="closePanelSlot"></div>
    <dl class="rd-rows">
      ${overviewRow('Request type', escapeHtml(o.requestType))}
      ${overviewRow('Purpose', escapeHtml(o.purpose))}
      ${overviewRow('Description', escapeHtml(o.description || '—'), 'rd-row-text')}
      ${overviewRow('Documents from the client', docs)}
      ${overviewRow('Client action needed', clientActionHtml(r, canAsk))}
    </dl>`;

  const closeBtn = document.getElementById('closeRequestBtn');
  if(closeBtn) closeBtn.addEventListener('click', () => {
    closePanelOpen = !closePanelOpen;
    renderOverview(panel);
    if(closePanelOpen) document.querySelector('#closePanelSlot input:checked').focus();
  });
  if(closePanelOpen) renderClosePanel(panel);
  wireClientAction(panel, r);
}

/* "Client action needed": ask the client for something, or show what was asked. */
function clientActionHtml(r, canAsk){
  const o = r.overview;
  const phoneOnly = !r.requester.hasEmail;
  if(!o.isWaitingForClient){
    if(!canAsk) return '<span class="rd-row-muted">Nothing is asked of the client right now.</span>';
    return `
      <div class="rd-ask">
        <label class="visually-hidden" for="clientNoteInput">What do you need from the client?</label>
        <textarea id="clientNoteInput" class="rd-textarea" maxlength="1000" rows="3" placeholder="What do you need from the client?"></textarea>
        <p class="rd-ask-hint">${phoneOnly
          ? 'The client has no email, so tell them by phone. The deadline keeps running.'
          : 'The client is emailed this note and sees it on their request. The deadline keeps running.'}</p>
        <div class="rd-ask-actions">
          <button type="button" class="btn btn-secondary btn-compact" id="askClientBtn">Ask the client</button>
        </div>
        <p class="rd-panel-msg" id="clientAskMsg" role="alert"></p>
      </div>`;
  }

  if(editingNote){
    return `
      <div class="rd-ask">
        <label class="visually-hidden" for="clientNoteInput">Note to the client</label>
        <textarea id="clientNoteInput" class="rd-textarea" maxlength="1000" rows="3">${escapeHtml(o.staffNoteToClient || '')}</textarea>
        <p class="rd-ask-hint">${phoneOnly
          ? 'The client has no email, so tell them the new note by phone.'
          : 'Saving a changed note emails the client again.'}</p>
        <div class="rd-ask-actions">
          <button type="button" class="btn btn-primary btn-compact" id="saveNoteBtn">Save note</button>
          <button type="button" class="btn btn-tertiary btn-compact" id="cancelNoteBtn">Cancel</button>
        </div>
        <p class="rd-panel-msg" id="clientAskMsg" role="alert"></p>
      </div>`;
  }

  const card = `
    <div class="rd-sent-note">
      <p class="rd-sent-note-title">${o.askedShort ? `Asked the client on ${escapeHtml(o.askedShort)}` : 'Waiting on the client'}</p>
      <p class="rd-sent-note-text">${o.staffNoteToClient
        ? escapeHtml(o.staffNoteToClient)
        : 'No note. The client was told the Legal Office is waiting for a response or document.'}</p>
      ${o.reminderShort ? `<p class="rd-sent-note-meta">Reminder sent ${escapeHtml(o.reminderShort)}</p>` : ''}
    </div>`;
  if(!canAsk) return `<div class="rd-ask">${card}</div>`;
  return `
    <div class="rd-ask">
      ${card}
      <p class="rd-ask-hint">${phoneOnly
        ? 'The deadline keeps running while you wait. The client has no email, so remind them by phone.'
        : 'The deadline keeps running while you wait. Editing the note emails the client again.'}</p>
      <div class="rd-ask-actions">
        <button type="button" class="btn btn-primary btn-compact" id="clientRespondedBtn">Client has responded</button>
        ${phoneOnly ? '' : '<button type="button" class="btn btn-secondary btn-compact" id="sendReminderBtn">Send reminder</button>'}
        <button type="button" class="btn btn-secondary btn-compact" id="editNoteBtn">Edit note</button>
      </div>
      <p class="rd-panel-msg" id="clientAskMsg" role="alert"></p>
    </div>`;
}

function wireClientAction(panel, r){
  const o = r.overview;
  const msg = document.getElementById('clientAskMsg');
  const input = document.getElementById('clientNoteInput');

  async function save(btn, changes, callLine){
    btn.disabled = true;
    const {ok, error} = await updateClientWaitStatus(r.id, changes);
    if(!ok){ btn.disabled = false; msg.textContent = error ? friendlyError(error) : 'Could not save.'; return; }
    editingNote = false;
    if(callLine) remindToCall(callLine);
    refreshOverview();
  }
  const waitingLine = note => note ? `The Legal Office is waiting for something from them: “${note}”`
                                   : 'The Legal Office is waiting for a response or document from them.';

  const askBtn = document.getElementById('askClientBtn');
  if(askBtn) askBtn.addEventListener('click', () => {
    const note = input.value.trim();
    save(askBtn, {isWaitingForClient: true, note}, waitingLine(note));
  });

  const respondedBtn = document.getElementById('clientRespondedBtn');
  if(respondedBtn) respondedBtn.addEventListener('click', () => save(respondedBtn, {isWaitingForClient: false}));

  const reminderBtn = document.getElementById('sendReminderBtn');
  if(reminderBtn) reminderBtn.addEventListener('click', async () => {
    if(await sendReminderFrom(reminderBtn, r.id)){
      showPageMessage('Reminder sent to the client.', 'success');
      refreshOverview();
    }
  });

  const editBtn = document.getElementById('editNoteBtn');
  if(editBtn) editBtn.addEventListener('click', () => {
    editingNote = true;
    renderOverview(panel);
    const box = document.getElementById('clientNoteInput');
    box.focus();
    box.setSelectionRange(box.value.length, box.value.length);
  });

  const cancelNote = document.getElementById('cancelNoteBtn');
  if(cancelNote) cancelNote.addEventListener('click', () => { editingNote = false; renderOverview(panel); });

  const saveNote = document.getElementById('saveNoteBtn');
  if(saveNote) saveNote.addEventListener('click', () => {
    const note = input.value.trim();
    if(note === (o.staffNoteToClient || '')){ editingNote = false; renderOverview(panel); return; }
    // The server emails only when the new note isn't empty.
    save(saveNote, {note}, note ? waitingLine(note) : null);
  });
}

/* Attorney: cancel or reject, with a reason written for the client. */
function renderClosePanel(panel){
  const slot = document.getElementById('closePanelSlot');
  const phoneOnly = !currentRecord.requester.hasEmail;
  slot.innerHTML = `
    <section class="rd-inset" aria-labelledby="closePanelTitle">
      <div class="rd-panel-head">
        <h3 class="rd-inset-title" id="closePanelTitle">Cancel or reject this request</h3>
        <button type="button" class="icon-btn rd-panel-close" id="closePanelX" aria-label="Close">${ICONS.close}</button>
      </div>
      <div class="rd-panel-body">
        <fieldset class="rd-field">
          <legend class="rd-label">What should happen to it?</legend>
          <div class="rd-options">
            <label class="rd-option rd-option-top selected">
              <input type="radio" name="closeAction" value="reject" checked>
              <span class="rd-option-text"><span>Reject</span><span class="rd-option-desc">The Legal Office won’t act on it, for example it’s outside the office’s mandate.</span></span>
            </label>
            <label class="rd-option rd-option-top">
              <input type="radio" name="closeAction" value="cancel">
              <span class="rd-option-text"><span>Cancel</span><span class="rd-option-desc">It’s no longer needed, for example it’s a duplicate or the client asked to withdraw it.</span></span>
            </label>
          </div>
        </fieldset>
        <div class="rd-field">
          <label class="rd-label" for="closeReasonInput">Reason for the client <span class="rd-label-note">required</span></label>
          <textarea id="closeReasonInput" class="rd-textarea" maxlength="1000" rows="3" placeholder="e.g. This concern is handled by another office, not the Legal Office."></textarea>
          <span class="rd-hint">${phoneOnly
            ? 'The client has no email, so tell them by phone. It’s kept on their request.'
            : 'Emailed to the client and shown on their request, so write it for them.'}</span>
        </div>
        <p class="rd-panel-intro rd-panel-note">The request stops at its current step. An attorney can reopen it later if it was closed by mistake.</p>
        <div class="rd-panel-actions">
          <button type="button" class="btn btn-danger btn-compact" id="closeConfirmBtn">Reject request</button>
          <button type="button" class="btn btn-tertiary btn-compact" id="closeCancelBtn">Keep it open</button>
        </div>
        <p class="rd-panel-msg" id="closeMsg" role="alert"></p>
      </div>
    </section>`;

  const confirmBtn = document.getElementById('closeConfirmBtn');
  const close = () => { closePanelOpen = false; renderOverview(panel); document.getElementById('closeRequestBtn').focus(); };
  document.getElementById('closePanelX').addEventListener('click', close);
  document.getElementById('closeCancelBtn').addEventListener('click', close);
  slot.querySelectorAll('input[name="closeAction"]').forEach(input => {
    input.addEventListener('change', () => {
      slot.querySelectorAll('.rd-option').forEach(o => o.classList.toggle('selected', o.contains(input)));
      confirmBtn.textContent = input.value === 'reject' ? 'Reject request' : 'Cancel request';
    });
  });
  confirmBtn.addEventListener('click', async () => {
    const action = slot.querySelector('input[name="closeAction"]:checked').value;
    const reasonInput = document.getElementById('closeReasonInput');
    const reason = reasonInput.value.trim();
    const msg = document.getElementById('closeMsg');
    if(!reason){ msg.textContent = 'Write a reason for the client.'; reasonInput.focus(); return; }
    confirmBtn.disabled = true;
    const {ok, error} = await closeRequest(currentRecord.id, action, reason);
    if(!ok){ msg.textContent = error ? friendlyError(error) : 'Could not close this request.'; confirmBtn.disabled = false; return; }
    closePanelOpen = false;
    showPageMessage(`${currentRecord.id} was ${action === 'reject' ? 'rejected' : 'cancelled'}.`, 'success');
    remindToCall(`The request was ${action === 'reject' ? 'rejected' : 'cancelled'}. Reason: “${reason}”`);
    refreshOverview();
  });
}

/* ---------------- Processing ---------------- */
// Mirrors the rules in routers/requests.py. The server enforces them; this
// only decides which buttons to show at the top of the page.
const ATTORNEY_ROLES = ['attorney', 'head_attorney'];
const ATTORNEY_LOCKED_STATUSES = ['For Attorney Review', 'Approved', 'Completed'];
const ATTORNEY_ONLY_STATUSES = ['Approved', 'Completed'];
const RETURNABLE_STATUSES = ['For Attorney Review', 'Approved'];

/** What this person may do with the status: attorneys move any open
    request forward and return forwarded work; the assigned legal assistant
    moves it forward until it reaches the attorney. */
function statusMoves(r){
  const none = {next: null, advance: null, canReturn: false};
  if(r.isClosed) return none;
  const steps = r.workflowSteps;
  const next = steps[steps.indexOf(r.status) + 1] || null;
  if(isAttorneyRole()){
    return {
      next,
      advance: !next ? null : next === 'Approved' ? 'Approve' : next === 'Completed' ? 'Mark completed' : 'Update progress',
      canReturn: RETURNABLE_STATUSES.includes(r.status) && steps.includes('Processing')
    };
  }
  if(myRole() === 'legal_assistant' && isMine(r) && next
     && !ATTORNEY_LOCKED_STATUSES.includes(r.status) && !ATTORNEY_ONLY_STATUSES.includes(next)){
    return {next, advance: 'Update progress', canReturn: false};
  }
  return none;
}

/** The note in the current step's box: what happens next, for this person. */
function currentStepNote(r){
  const move = statusMoves(r);
  const name = escapeHtml(r.assignedTo);
  const mine = isMine(r);
  if(isAttorneyRole()){
    if(move.canReturn && move.advance) return `Ready for your decision: ${move.advance === 'Approve' ? 'approve it' : 'mark it completed'}, or return it for revision. Both buttons are at the top of the page.`;
    if(move.advance) return r.assignedToId ? `${name} handles this step. Update progress at the top of the page moves it on.` : 'Not assigned to a legal assistant yet.';
    return '';
  }
  if(r.status === 'For Attorney Review') return `Waiting for the attorney to approve it or return it for revision.${mine ? ' You can’t change the status until then.' : ''}`;
  if(r.status === 'Approved') return 'Approved by the attorney. Only the attorney can mark it completed.';
  if(move.advance){
    return move.next === 'For Attorney Review'
      ? 'Your turn. When the work is ready, use Update progress at the top of the page to forward it to the attorney for review.'
      : `Your turn. When this step is done, use Update progress at the top of the page to move it to ${escapeHtml(move.next)}.`;
  }
  if(!mine) return r.assignedToId ? `${name} handles this step.` : 'Not assigned to a legal assistant yet.';
  return '';
}

function currentStepBox(r){
  const p = r.processing;
  if(r.isClosed){
    return `<div class="rd-step-box stopped"><p>${escapeHtml(r.closedLabel)} while at this step. Only an attorney can reopen it.</p></div>`;
  }
  const flag = r.deadline === 'overdue' ? ' <span class="rd-due-flag overdue">Overdue</span>'
    : r.deadline === 'approaching' ? ' <span class="rd-due-flag approaching">Due soon</span>' : '';
  const due = r.dueShort ? `<p class="rd-step-due">Due <b>${escapeHtml(r.dueShort)}</b>${flag}</p>` : '<p class="rd-step-due">No set period</p>';
  if(p.returned){
    const canFix = myRole() === 'legal_assistant' && statusMoves(r).advance === 'Update progress';
    return `
      <div class="rd-step-box returned">
        ${due}
        <p class="rd-step-strong">Returned for revision by ${escapeHtml(p.returned.by)} on ${escapeHtml(p.returned.date)}</p>
        ${p.returned.comment ? `<blockquote class="rd-step-comment">“${escapeHtml(p.returned.comment)}”</blockquote>` : ''}
        <p>${canFix ? 'Make the changes, then use Update progress at the top of the page to forward it to the attorney again. ' : ''}Only staff see this comment.</p>
      </div>`;
  }
  const note = currentStepNote(r);
  return `<div class="rd-step-box">${due}${note ? `<p>${note}</p>` : ''}</div>`;
}

function renderProcessing(panel){
  const r = currentRecord;
  const p = r.processing;
  const count = p.completed ? `All ${p.total} steps done`
    : `${r.isClosed ? 'Stopped at step' : 'Step'} ${p.index + 1} of ${p.total}`;
  const stateText = {done: 'done', current: r.isClosed ? 'stopped here' : 'current step', todo: 'not reached yet'};
  panel.innerHTML = `
    <div class="rd-tab-head">
      <h2 class="rd-card-title">Progress</h2>
      <span class="rd-tab-count">${count}</span>
    </div>
    <ol class="rd-steps">
      ${p.steps.map(s => `
        <li class="rd-step ${s.state}"${s.state === 'current' ? ' aria-current="step"' : ''}>
          <span class="rd-step-rail" aria-hidden="true"><span class="rd-step-marker">${s.state === 'done' ? ICONS.check : ''}</span><span class="rd-step-line"></span></span>
          <div class="rd-step-body">
            <p class="rd-step-title">${escapeHtml(s.title)}<span class="visually-hidden">, ${stateText[s.state]}</span></p>
            ${s.meta ? `<p class="rd-step-meta">${escapeHtml(s.meta)}</p>` : ''}
            ${s.state === 'current' ? currentStepBox(r) : ''}
          </div>
        </li>`).join('')}
    </ol>`;
}

/* ---------------- Documents ---------------- */
function plural(n, one, many){ return `${n} ${n === 1 ? one : many}`; }

function docMeta(d, fromClient){
  if(fromClient && d.addedByStaff) return `Added by staff · ${escapeHtml(d.uploaded)}`;
  return d.version > 1 ? `Version ${d.version} · uploaded ${escapeHtml(d.uploaded)}` : `Uploaded ${escapeHtml(d.uploaded)}`;
}

// "Request revision": the document whose "what to correct" box is open, and the text typed so far.
let revisingDocId = null;
let revisionDraft = '';

function revisionBox(name){
  const phoneOnly = !currentRecord.requester.hasEmail;
  return `
      <div class="rd-file-ask">
        <label class="rd-label" for="docRevisionInput">What needs to be corrected in ${name}? <span class="rd-label-note">required</span></label>
        <textarea id="docRevisionInput" class="rd-textarea rd-textarea-short" maxlength="1000" rows="2">${escapeHtml(revisionDraft)}</textarea>
        <p class="rd-ask-hint">${phoneOnly
          ? 'The client has no email, so tell them by phone. The request is marked as waiting on the client. The deadline keeps running.'
          : 'The client is emailed this and sees it on their request, which is marked as waiting on the client. The deadline keeps running.'}</p>
        <div class="rd-ask-actions">
          <button type="button" class="btn btn-primary btn-compact" id="docRevisionSend">Request revision</button>
          <button type="button" class="btn btn-tertiary btn-compact" id="docRevisionCancel">Cancel</button>
        </div>
        <p class="rd-panel-msg" id="docRevisionMsg" role="alert"></p>
      </div>`;
}

function docRow(d, fromClient){
  const name = escapeHtml(d.name);
  let pill, action, box = '';
  if(fromClient && d.replacedBy){
    // A revised copy took over (a new version); this one is kept for the record.
    pill = `<span class="status-label tone-neutral">Replaced by version ${d.replacedBy}</span>`;
    action = '';
  }else if(fromClient){
    const revising = d.status === 'revision_required';
    pill = revising ? '<span class="status-label tone-waiting">Revision requested</span>' : '';
    const asking = !revising && d.id === revisingDocId;
    // A closed request can't take a corrected copy, so nothing is asked there.
    action = !revising && currentRecord.isClosed ? ''
      : `<button type="button" class="btn btn-secondary btn-compact" data-doc-revision="${d.id}" data-current-status="${revising ? 'revision_required' : 'submitted'}"${revising ? '' : ` aria-expanded="${asking}"`}>${revising ? 'Clear revision' : 'Request revision'}<span class="visually-hidden">: ${name}</span></button>`;
    if(asking && !currentRecord.isClosed) box = revisionBox(name);
  }else{
    pill = d.released ? '<span class="status-label tone-done">Released to client</span>'
      : '<span class="status-label tone-neutral">Office only</span>';
    action = `<button type="button" class="btn btn-secondary btn-compact" data-doc-release="${d.id}" data-released="${d.released ? '1' : '0'}">${d.released ? 'Withdraw' : 'Release to client'}<span class="visually-hidden">: ${name}</span></button>`;
  }
  return `
    <li class="rd-file${box ? ' is-asking' : ''}">
      <span class="rd-file-icon" aria-hidden="true">${ICONS.documents}</span>
      <div class="rd-file-text">
        <p class="rd-file-name" title="${name}">${name}</p>
        <p class="rd-file-meta"><span>${docMeta(d, fromClient)}</span>${pill}</p>
      </div>
      <div class="rd-file-actions">
        ${action}
        <button type="button" class="btn btn-tertiary btn-compact" data-doc-download="${d.id}">${ICONS.download} Download<span class="visually-hidden">: ${name}</span></button>
      </div>${box}
    </li>`;
}

function docSection(s){
  return `
    <section class="rd-doc-section" aria-labelledby="${s.id}">
      <div class="rd-doc-head">
        <div class="rd-doc-title">
          <h3 id="${s.id}">${s.title}</h3>
          <p>${s.hint}</p>
        </div>
        ${s.button}
      </div>
      ${s.docs.length
        ? `<ul class="rd-files">${s.docs.map(d => docRow(d, s.fromClient)).join('')}</ul>`
        : `<div class="rd-files rd-empty-box"><p class="rd-empty-title">${s.emptyTitle}</p>${s.emptyHint ? `<p>${s.emptyHint}</p>` : ''}</div>`}
    </section>`;
}

/** Reads the request again for its documents and History, after a change
    that adds to them (a document action, or files sent in the chat). */
async function reloadDocumentsAndHistory(){
  const {record} = await fetchRequestDetail(currentRecord.id);
  if(record){
    currentRecord.documents = record.documents;
    currentRecord.history = record.history;
  }
}

/** After a change, read the request again (History gets the new entry too),
    then put keyboard focus back on the button that was used. */
async function refreshDocuments(panel, focusSelector){
  await reloadDocumentsAndHistory();
  if(activeTab !== 'documents') return;
  renderDocuments(panel);
  const again = focusSelector && panel.querySelector(focusSelector);
  if(again) again.focus();
}

function renderDocuments(panel){
  const docs = currentRecord.documents;
  const total = docs.client.length + docs.office.length;
  // Walk-in / paper requests: papers the client handed over are filed as
  // theirs (visible on My Requests) and marked "Added by staff".
  const canAddClientDocs = !currentRecord.isClosed;
  panel.innerHTML = `
    <div class="rd-tab-head">
      <h2 class="rd-card-title">Documents</h2>
      <span class="rd-tab-count">${total ? plural(total, 'file', 'files') : 'No files yet'}</span>
    </div>
    <div class="rd-doc-sections">
      ${docSection({
        id: 'rdDocsClient', title: 'From the client', fromClient: true, docs: docs.client,
        hint: 'Files the client sent online, and papers a walk-in client handed over.',
        button: canAddClientDocs ? `<button type="button" class="btn btn-secondary btn-compact" id="uploadClientDocBtn">${ICONS.uploadSmall} Add client’s documents</button>
          <input type="file" id="uploadClientDocInput" multiple accept=".pdf,.doc,.docx,.jpg,.jpeg,.png" hidden>` : '',
        emptyTitle: 'No files from the client yet.',
        emptyHint: canAddClientDocs ? 'A walk-in client’s papers can be added here with Add client’s documents.' : ''
      })}
      ${docSection({
        id: 'rdDocsOffice', title: 'From the Legal Office', fromClient: false, docs: docs.office,
        hint: 'The client sees a file here only after you release it.',
        button: `<button type="button" class="btn btn-secondary btn-compact" id="uploadDocBtn">${ICONS.uploadSmall} Upload document</button>
          <input type="file" id="uploadDocInput" multiple hidden>`,
        emptyTitle: 'No documents from the Legal Office yet.',
        emptyHint: 'Upload a document when it’s ready. It stays hidden from the client until you release it.'
      })}
    </div>`;

  panel.querySelectorAll('[data-doc-download]').forEach(btn => {
    btn.addEventListener('click', () => downloadDocumentInternal(btn.dataset.docDownload));
  });

  const wireUpload = (btnId, inputId, path, opts) => {
    const btn = document.getElementById(btnId);
    if(!btn) return;
    const input = document.getElementById(inputId);
    btn.addEventListener('click', () => input.click());
    input.addEventListener('change', async () => {
      if(!input.files.length) return;
      const form = new FormData();
      [...input.files].forEach(f => form.append('files', f));
      input.value = '';
      btn.disabled = true;
      const result = await apiFetch(`/requests/${encodeURIComponent(currentRecord.id)}/documents/${path}`, {
        method: 'POST', body: form, ...opts
      });
      if(!result.ok){
        btn.disabled = false;
        showPageMessage(result.error ? friendlyError(result.error) : 'Could not upload. Please try again.', 'error');
        return;
      }
      await refreshDocuments(panel, '#' + btnId);
    });
  };
  wireUpload('uploadClientDocBtn', 'uploadClientDocInput', 'client-by-staff', {timeoutMs: 120000});
  wireUpload('uploadDocBtn', 'uploadDocInput', 'staff', {});

  const patchDocument = (id, changes) =>
    apiFetch(`/requests/${encodeURIComponent(currentRecord.id)}/documents/${id}`, {
      method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(changes)
    });
  const openRevision = id => {
    revisingDocId = id;
    revisionDraft = '';
    renderDocuments(panel);
  };
  panel.querySelectorAll('[data-doc-revision]').forEach(btn => {
    const id = Number(btn.dataset.docRevision);
    btn.addEventListener('click', async () => {
      if(btn.dataset.currentStatus !== 'revision_required'){   // open or close the "what to correct" box
        const opening = revisingDocId !== id;
        openRevision(opening ? id : null);
        panel.querySelector(opening ? '#docRevisionInput' : `[data-doc-revision="${id}"]`).focus();
        return;
      }
      btn.disabled = true;
      const result = await patchDocument(id, {status: 'submitted'});
      if(!result.ok){
        btn.disabled = false;
        showPageMessage(result.error ? friendlyError(result.error) : 'Could not update this document.', 'error');
        return;
      }
      await refreshDocuments(panel, `[data-doc-revision="${id}"]`);
    });
  });

  const revisionInput = document.getElementById('docRevisionInput');
  if(revisionInput){
    const id = revisingDocId;
    const msg = document.getElementById('docRevisionMsg');
    revisionInput.addEventListener('input', () => { revisionDraft = revisionInput.value; msg.textContent = ''; });
    document.getElementById('docRevisionCancel').addEventListener('click', () => {
      openRevision(null);
      panel.querySelector(`[data-doc-revision="${id}"]`).focus();
    });
    document.getElementById('docRevisionSend').addEventListener('click', async (e) => {
      const btn = e.currentTarget;
      const note = revisionInput.value.trim();
      if(!note){ msg.textContent = 'State what needs to be corrected.'; revisionInput.focus(); return; }
      btn.disabled = true;
      const result = await patchDocument(id, {status: 'revision_required', note});
      if(!result.ok){
        btn.disabled = false;
        msg.textContent = result.error ? friendlyError(result.error) : 'Could not update this document.';
        return;
      }
      revisingDocId = null;
      revisionDraft = '';
      const name = result.data.original_filename;
      showPageMessage(`Revision requested for “${name}”. The request is waiting on the client.`, 'success');
      remindToCall(`Their document “${name}” needs to be revised: “${note}”`);
      await reloadRecord();   // the heading and the Overview show the waiting mark and the note
      const again = document.querySelector(`[data-doc-revision="${id}"]`);
      if(again) again.focus();
    });
  }

  // Release / withdraw a Legal Office document. Nothing the
  // office uploads is visible to the client until it is released.
  panel.querySelectorAll('[data-doc-release]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const releasing = btn.dataset.released !== '1';
      const ok = await confirmDialog(releasing
        ? {title: 'Release this document?', message: 'The client will be able to view and download it on their request page.',
           confirmLabel: 'Release document'}
        : {title: 'Withdraw this document?',
           message: 'The client will no longer be able to download it. A copy they already downloaded can’t be taken back.',
           confirmLabel: 'Withdraw document', cancelLabel: 'Keep it released', danger: true});
      if(!ok) return;
      btn.disabled = true;
      const result = await apiFetch(`/requests/${encodeURIComponent(currentRecord.id)}/documents/${btn.dataset.docRelease}/release`, {
        method: 'PATCH',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({released: releasing})
      });
      if(!result.ok){
        btn.disabled = false;
        showPageMessage(result.error ? friendlyError(result.error) : 'Could not update this document.', 'error');
        return;
      }
      if(releasing) remindToCall(`The document “${result.data.original_filename}” was released to them.`);
      await refreshDocuments(panel, `[data-doc-release="${btn.dataset.docRelease}"]`);
    });
  });
}

async function downloadDocumentInternal(documentId){
  try{
    const response = await fetch(`${API_BASE_URL}/requests/${encodeURIComponent(currentRecord.id)}/documents/${documentId}/download`, {
      method: 'GET',
      credentials: 'same-origin'
    });
    if(!response.ok){
      showPageMessage(response.status === 429
        ? 'Too many downloads in a short time. Wait a moment, then try again.'
        : 'Could not download this document.', 'error');
      return;
    }
    const blob = await response.blob();
    const disposition = response.headers.get('Content-Disposition') || '';
    // Exact UTF-8 name first (ñ, quotes), else the plain fallback.
    const utf8 = disposition.match(/filename\*=UTF-8''([^;]+)/);
    const plain = disposition.match(/filename="([^"]+)"/);
    let filename = 'document';
    try{ filename = utf8 ? decodeURIComponent(utf8[1]) : (plain ? plain[1] : filename); }catch(_){ /* keep the default */ }
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }catch(err){
    showPageMessage('Could not reach the Legal Office server to download this document.', 'error');
  }
}

/** A document's bytes for a picture preview in the Conversation, through
    the same staff download endpoint (no public URL). Null when it can't be
    read; the preview then just isn't shown. */
async function fetchDocumentBlob(documentId){
  const response = await fetch(`${API_BASE_URL}/requests/${encodeURIComponent(currentRecord.id)}/documents/${encodeURIComponent(documentId)}/download`, {
    method: 'GET',
    credentials: 'same-origin'
  });
  return response.ok ? response.blob() : null;
}

/* ---------------- History ---------------- */
const HISTORY_FILTERS = [
  {key: 'all', label: 'All'},
  {key: 'status', label: 'Status'},
  {key: 'documents', label: 'Documents'},
  {key: 'contact', label: 'Messages and calls'},
  {key: 'assignment', label: 'Assignment'}
];
// Which filter each History entry belongs to, and its marker. Actions not
// listed here (client verification, details edited) show under "All" only.
const HISTORY_LOOK = {
  status_change:             {kind: 'status', icon: 'forward', solid: true},
  request_reopened:          {kind: 'status', icon: 'forward', solid: true},
  returned_for_revision:     {kind: 'status', icon: 'back'},
  request_cancelled:         {kind: 'status', icon: 'close'},
  request_rejected:          {kind: 'status', icon: 'close'},
  request_withdrawn:         {kind: 'status', icon: 'close'},
  request_submitted:         {kind: 'status', icon: 'requests'},
  request_encoded:           {kind: 'status', icon: 'requests'},
  waiting_for_client_change: {kind: 'status', icon: 'clock'},
  client_note_change:        {kind: 'status', icon: 'clock'},
  client_document_uploaded:  {kind: 'documents', icon: 'documents'},
  office_document_uploaded:  {kind: 'documents', icon: 'documents'},
  document_status_changed:   {kind: 'documents', icon: 'documents'},
  document_released:         {kind: 'documents', icon: 'documents'},
  document_withdrawn:        {kind: 'documents', icon: 'documents'},
  endorsement_uploaded:      {kind: 'documents', icon: 'documents'},
  document_opened:           {kind: 'documents', icon: 'documents'},
  message_sent:              {kind: 'contact', icon: 'messages'},
  client_contacted:          {kind: 'contact', icon: 'messages'},
  reminder_sent:             {kind: 'contact', icon: 'bell'},
  auto_assigned:             {kind: 'assignment', icon: 'users'},
  reassigned:                {kind: 'assignment', icon: 'users'},
  reassignment_requested:    {kind: 'assignment', icon: 'users'},
  reassignment_dismissed:    {kind: 'assignment', icon: 'users'}
};
let historyFilter = 'all';

function historyWho(e){
  if(e.actorType === 'system') return 'LEXIS, automatically';
  if(e.actorType === 'requester') return 'Client';
  return e.actor;
}

function historyEntryHtml(e){
  const look = HISTORY_LOOK[e.action] || {icon: 'user'};
  return `
    <li class="rd-event">
      <span class="rd-event-rail" aria-hidden="true"><span class="rd-event-marker${look.solid ? ' solid' : ''}">${ICONS[look.icon]}</span><span class="rd-event-line"></span></span>
      <div class="rd-event-text">
        <p class="rd-event-detail">${escapeHtml(e.detail)}</p>
        <p class="rd-event-meta">${escapeHtml(historyWho(e))} · ${formatTime(e.at)}</p>
      </div>
    </li>`;
}

function renderHistory(panel){
  const all = [...currentRecord.history].sort((a, b) => parseServerTime(b.at) - parseServerTime(a.at));
  const shown = historyFilter === 'all' ? all : all.filter(e => (HISTORY_LOOK[e.action] || {}).kind === historyFilter);
  const days = [];
  shown.forEach(e => {
    const day = formatMediumDate(e.at);
    const last = days[days.length - 1];
    if(last && last.day === day) last.entries.push(e);
    else days.push({day, entries: [e]});
  });
  const filterLabel = HISTORY_FILTERS.find(f => f.key === historyFilter).label;

  panel.innerHTML = `
    <div class="rd-tab-head">
      <h2 class="rd-card-title">History</h2>
      <span class="rd-tab-count">${plural(all.length, 'entry', 'entries')}${all.length > 1 ? ' · newest first' : ''}</span>
    </div>
    ${all.length ? `<div class="rd-chips" role="group" aria-label="Show">
      ${HISTORY_FILTERS.map(f => `<button type="button" class="rd-chip${f.key === historyFilter ? ' current' : ''}" aria-pressed="${f.key === historyFilter}" data-filter="${f.key}">${f.label}</button>`).join('')}
    </div>` : ''}
    ${days.length ? `<div class="rd-days">${days.map(g => `
      <section class="rd-day">
        <h3 class="rd-day-label">${g.day}</h3>
        <ol class="rd-events">${g.entries.map(historyEntryHtml).join('')}</ol>
      </section>`).join('')}</div>`
    : `<div class="rd-empty-box"><p class="rd-empty-title">${all.length ? `Nothing under “${filterLabel}” yet.` : 'No activity recorded yet.'}</p></div>`}`;

  panel.querySelectorAll('.rd-chip').forEach(btn => {
    btn.addEventListener('click', () => {
      historyFilter = btn.dataset.filter;
      renderHistory(panel);
      panel.querySelector(`.rd-chip[data-filter="${historyFilter}"]`).focus();
    });
  });
}

/* ---------------- Conversation ---------------- */
/* The thread by day,
   client messages on the left, the office's on the right, then the box to
   write to the client. A message can carry up to 5 files:
   each is a Legal Office document, released to the client as it is sent,
   and also listed in the Documents tab. */
const MESSAGE_MAX = 5000;
// The server's limits (messages.MAX_CHAT_FILES, storage.py, documents.py),
// checked here first so staff hear about a bad file before it uploads.
// The server checks everything again.
const CHAT_MAX_FILES = 5;
const CHAT_MAX_FILE_BYTES = 20 * 1024 * 1024;
const CHAT_MAX_TOTAL_BYTES = 50 * 1024 * 1024;
const CHAT_FILE_TYPES = {pdf: 'PDF', doc: 'Word', docx: 'Word', jpg: 'JPG', jpeg: 'JPG', png: 'PNG'};
let messageDraft = '';   // kept while staff look at another tab
let pendingFiles = [];   // picked but not sent yet, kept the same way

function fileExtension(name){
  const dot = name.lastIndexOf('.');
  return dot < 0 ? '' : name.slice(dot + 1).toLowerCase();
}

/** "PDF · 1.2 MB". */
function fileFacts(name, bytes){
  const type = CHAT_FILE_TYPES[fileExtension(name)] || 'File';
  if(bytes == null) return type;
  const size = bytes < 1024 * 1024
    ? `${Math.max(1, Math.round(bytes / 1024))} KB`
    : `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${type} · ${size}`;
}

/** A file in a bubble: the whole card downloads it. A file staff withdrew
    in the Documents tab stays in the staff thread, marked, since the client
    no longer sees it. A JPG or PNG also shows as a picture above its card
    (js/image-preview.js). */
function attachmentHtml(a){
  const name = escapeHtml(a.original_filename);
  const picture = ChatImagePreview.isImage(a.mime_type);
  return `
    <li${picture ? ' class="has-preview"' : ''}>
      ${picture ? ChatImagePreview.frameHtml('rd-attach-preview', a.id, name) : ''}
      <button type="button" class="rd-attach rd-attach-download" data-msg-download="${a.id}" title="Download ${name}">
        <span class="rd-attach-icon" aria-hidden="true">${ICONS.file}</span>
        <span class="rd-attach-text">
          <span class="rd-attach-name">${name}</span>
          <span class="rd-attach-meta">${escapeHtml(fileFacts(a.original_filename, a.file_size_bytes))}${a.hidden_from_client ? ' · <span class="rd-attach-flag">Withdrawn, the client can’t see it</span>' : ''}</span>
        </span>
        <span class="rd-attach-end" aria-hidden="true">${ICONS.download}</span>
        <span class="visually-hidden">, download</span>
      </button>
    </li>`;
}

function messageHtml(m){
  const office = m.sender_type === 'staff';
  const who = office ? (m.sender_name || 'Legal Office') : currentRecord.requester.name;
  const files = m.attachments || [];
  return `
    <li class="rd-msg ${office ? 'office' : 'client'}">
      <p class="rd-msg-meta">${escapeHtml(who)} · ${formatTime(m.sent_at)}</p>
      <div class="rd-msg-bubble">
        ${m.body ? `<p class="rd-msg-text">${escapeHtml(m.body)}</p>` : ''}
        ${files.length ? `<ul class="rd-attach-list" aria-label="${plural(files.length, 'file', 'files')}">${files.map(attachmentHtml).join('')}</ul>` : ''}
      </div>
    </li>`;
}

/** The chips under the hint: files picked for the next message. */
function pendingFilesHtml(){
  return pendingFiles.map((f, i) => `
    <li class="rd-attach">
      <span class="rd-attach-icon" aria-hidden="true">${ICONS.file}</span>
      <span class="rd-attach-text">
        <span class="rd-attach-name" title="${escapeHtml(f.name)}">${escapeHtml(f.name)}</span>
        <span class="rd-attach-meta">${escapeHtml(fileFacts(f.name, f.size))}</span>
      </span>
      <button type="button" class="rd-attach-remove" data-remove-file="${i}" aria-label="Remove ${escapeHtml(f.name)}">${ICONS.close}</button>
    </li>`).join('');
}

/** Adds picked files that pass the server's checks; returns what was left
    out and why, so staff can fix it before sending. */
function addPendingFiles(list){
  const problems = [];
  let total = pendingFiles.reduce((sum, f) => sum + f.size, 0);
  for(const f of list){
    if(pendingFiles.some(p => p.name === f.name && p.size === f.size && p.lastModified === f.lastModified)) continue;
    if(pendingFiles.length >= CHAT_MAX_FILES){
      problems.push(`You can send up to ${CHAT_MAX_FILES} files with one message.`);
      break;
    }
    if(!CHAT_FILE_TYPES[fileExtension(f.name)]) problems.push(`“${f.name}” isn’t a PDF, Word, JPG or PNG file.`);
    else if(!f.size) problems.push(`“${f.name}” is empty.`);
    else if(f.size > CHAT_MAX_FILE_BYTES) problems.push(`“${f.name}” is larger than 20 MB.`);
    else if(total + f.size > CHAT_MAX_TOTAL_BYTES) problems.push(`“${f.name}” would bring the files over 50 MB together.`);
    else{ pendingFiles.push(f); total += f.size; }
  }
  return problems;
}

function threadHtml(rows){
  const days = [];
  rows.forEach(m => {
    const day = formatMediumDate(m.sent_at);
    const last = days[days.length - 1];
    if(last && last.day === day) last.rows.push(m);
    else days.push({day, rows: [m]});
  });
  return days.map(g => `
    <section class="rd-msg-day">
      <h3 class="rd-msg-day-label">${g.day}</h3>
      <ol class="rd-msgs">${g.rows.map(messageHtml).join('')}</ol>
    </section>`).join('');
}

/* The box grows with the text, up to its max-height (then it scrolls). */
function fitComposer(input){
  input.style.height = 'auto';
  input.style.height = input.scrollHeight + 2 + 'px';
}

async function renderConversation(panel, focusComposer){
  panel.innerHTML = `
    <div class="rd-tab-head">
      <h2 class="rd-card-title">Conversation</h2>
      <span class="rd-tab-count">Loading…</span>
    </div>
    <div class="rd-thread rd-thread-empty"><p>Loading messages…</p></div>`;

  const {rows, error} = await fetchMessages(currentRecord.id);
  if(activeTab !== 'conversation') return; // navigated away before this resolved

  if(error){
    panel.innerHTML = `
      <div class="rd-tab-head"><h2 class="rd-card-title">Conversation</h2></div>
      <div class="rd-thread rd-thread-empty">
        <p class="rd-empty-title">Couldn’t load the conversation</p>
        <p>${escapeHtml(friendlyError(error))}</p>
      </div>`;
    return;
  }

  const count = rows.length;
  // A phone-only client can't sign in to My Requests, so they never see
  // these messages: say so, and remind staff to call.
  const online = currentRecord.requester.hasEmail;
  panel.innerHTML = `
    <div class="rd-tab-head">
      <h2 class="rd-card-title">Conversation</h2>
      <span class="rd-tab-count">${plural(count, 'message', 'messages')}${count && online ? ' · the client reads them on their request page' : ''}</span>
    </div>
    ${count
      ? `<div class="rd-thread" id="convThread" tabindex="0" role="region" aria-label="Messages">${threadHtml(rows)}</div>`
      : `<div class="rd-thread rd-thread-empty">
          <p class="rd-empty-title">No messages yet</p>
          <p>${online ? 'Write to the client below. They can reply from their request page.' : 'This client has no email, so they can’t sign in to read messages.'}</p>
        </div>`}
    <form class="rd-composer" id="convForm" novalidate>
      <label class="rd-label rd-composer-label" for="convInput">Message to the client</label>
      <p class="rd-hint" id="convHint">${online ? 'The client reads this on their request page and can reply there. They get an email saying there’s a message (at most one a day for this request), without the text.' : 'This client has no email and can’t read messages. LEXIS will remind you to call them.'}</p>
      <p class="rd-hint" id="convFilesHint">Attach up to ${CHAT_MAX_FILES} files (PDF, Word, JPG or PNG; 20 MB each).${online ? ' They are released to the client when you send, and' : ' They are'} also listed under Documents.</p>
      <ul class="rd-attach-list rd-attach-pending" id="convFiles" aria-label="Files to send"></ul>
      <p class="rd-composer-error" id="convFileError" role="alert"></p>
      <div class="rd-composer-row">
        <button type="button" class="rd-attach-btn" id="convAttachBtn" aria-label="Attach files" aria-describedby="convFilesHint">${ICONS.paperclip}</button>
        <input type="file" id="convFileInput" multiple accept=".pdf,.doc,.docx,.jpg,.jpeg,.png" hidden>
        <textarea class="rd-textarea rd-composer-input" id="convInput" rows="1" maxlength="${MESSAGE_MAX}"
          placeholder="Write a message to the client" aria-describedby="convHint convKeys"></textarea>
        <button type="submit" class="rd-send-btn" id="convSendBtn" aria-label="Send message">${ICONS.forward}</button>
      </div>
      <p class="rd-hint" id="convKeys">Enter sends · Shift + Enter for a new line · the box grows as you type · <span id="convCount"></span></p>
    </form>`;

  const thread = document.getElementById('convThread');
  if(thread){
    thread.scrollTop = thread.scrollHeight;
    thread.addEventListener('click', e => {
      const card = e.target.closest('[data-msg-download]');
      if(card) downloadDocumentInternal(card.dataset.msgDownload);
    });
    ChatImagePreview.watch(thread, fetchDocumentBlob);
  }

  const form = document.getElementById('convForm');
  const input = document.getElementById('convInput');
  const sendBtn = document.getElementById('convSendBtn');
  const counter = document.getElementById('convCount');
  const attachBtn = document.getElementById('convAttachBtn');
  const fileInput = document.getElementById('convFileInput');
  const fileList = document.getElementById('convFiles');
  const fileError = document.getElementById('convFileError');
  const showPending = () => { fileList.innerHTML = pendingFilesHtml(); };
  showPending();

  attachBtn.addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', () => {
    const problems = addPendingFiles([...fileInput.files]);
    fileInput.value = '';   // the same file can be picked again after removing it
    fileError.textContent = problems.join(' ');
    showPending();
  });
  fileList.addEventListener('click', e => {
    const remove = e.target.closest('[data-remove-file]');
    if(!remove) return;
    const index = Number(remove.dataset.removeFile);
    pendingFiles.splice(index, 1);
    fileError.textContent = '';
    showPending();
    // Keep the keyboard nearby: the next chip's ×, else the paperclip.
    const next = fileList.querySelectorAll('[data-remove-file]')[Math.min(index, pendingFiles.length - 1)];
    (next || attachBtn).focus();
  });
  const update = () => {
    messageDraft = input.value;
    counter.textContent = `${input.value.length.toLocaleString('en-US')} / ${MESSAGE_MAX.toLocaleString('en-US')}`;
    fitComposer(input);
  };
  input.value = messageDraft;
  update();
  if(focusComposer) input.focus();

  input.addEventListener('input', update);
  input.addEventListener('keydown', e => {
    // Enter sends; Shift + Enter is a new line; Enter while choosing an
    // accented letter or other input-method text does not send.
    if(e.key === 'Enter' && !e.shiftKey && !e.isComposing){
      e.preventDefault();
      form.requestSubmit();
    }
  });
  form.addEventListener('submit', async e => {
    e.preventDefault();
    if(sendBtn.disabled) return;
    const text = input.value.trim();
    const files = [...pendingFiles];
    if(!text && !files.length){ input.focus(); return; }   // the server needs text, a file, or both
    sendBtn.disabled = true;
    attachBtn.disabled = true;
    sendBtn.setAttribute('aria-busy', 'true');
    const {message, error: sendError} = await sendMessage(currentRecord.id, text, files);
    if(!message){
      // The text and the files stay, so staff can fix the problem and resend.
      sendBtn.disabled = false;
      attachBtn.disabled = false;
      sendBtn.removeAttribute('aria-busy');
      showPageMessage(sendError ? friendlyError(sendError) : 'Could not send this message.', 'error');
      input.focus();
      return;
    }
    messageDraft = '';
    pendingFiles = [];
    const names = files.map(f => f.name).join(', ');
    const quote = text && `Your message: “${text.length > 200 ? text.slice(0, 199).trimEnd() + '…' : text}”`;
    remindToCall(quote ? quote + (files.length ? ` (with ${plural(files.length, 'file', 'files')}: ${names})` : '')
                       : `The ${files.length === 1 ? 'file' : 'files'} you sent: ${names}`);
    // The message is in History, and its files in Documents: read them again
    // so those tabs are current without reloading the page.
    reloadDocumentsAndHistory();
    if(activeTab === 'conversation') renderConversation(panel, true);
  });
}

init();
