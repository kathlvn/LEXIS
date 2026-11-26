// Request Details (request.html?id=LEO-…) — one request of the client who is
// signed in to My Requests. Depends on API_BASE_URL / service type helpers
// from api-config.js (loaded first).
//
// There is no lookup form here: the client signs in on My Requests (the door),
// and the server opens the request only if it is that client's own. Signed
// out, or not their request? Back to My Requests.

const $ = id => document.getElementById(id);

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

/* The server sends times in UTC without a trailing "Z". Without it the
   browser reads them as local time, so a request sent between midnight and
   8 AM (Philippine time) showed the previous day. */
function parseServerTime(iso){
  if(!iso) return new Date(NaN);
  return new Date(/[zZ]|[+-]\d\d:\d\d$/.test(iso) ? iso : iso + 'Z');
}

function formatDateTime(iso){
  const d = parseServerTime(iso);
  if(isNaN(d)) return '\u2014';
  return d.toLocaleString('en-US', {month:'long', day:'numeric', year:'numeric', hour:'numeric', minute:'2-digit'});
}

function formatDate(iso){
  const d = parseServerTime(iso);
  if(isNaN(d)) return '—';
  return d.toLocaleDateString('en-US', {month:'long', day:'numeric', year:'numeric'});
}

/* A due date is the end of a working day in the Philippines, so it is shown
   in Philippine time wherever the client happens to be. */
function formatDueDate(iso){
  const d = parseServerTime(iso);
  if(isNaN(d)) return '—';
  return d.toLocaleDateString('en-US', {month:'long', day:'numeric', year:'numeric', timeZone:'Asia/Manila'});
}

function formatMessageTime(iso){
  const d = parseServerTime(iso);
  if(isNaN(d)) return '';
  return d.toLocaleString('en-US', {month:'short', day:'numeric', hour:'numeric', minute:'2-digit'});
}

/** Notice. The title is escaped here; the message must already be safe. */
function noticeHtml(kind, title, messageHtml){
  const message = messageHtml ? `<p class="notice-message">${messageHtml}</p>` : '';
  return `<div class="notice notice-${kind}">
    <img src="img/icons/${kind}.svg" alt="" width="24" height="24">
    <div><p class="notice-title">${escapeHtml(title)}</p>${message}</div>
  </div>`;
}

/** The server's own message when it sent one, else the fallback. */
function detailOr(body, fallback){
  return body && typeof body.detail === 'string' ? body.detail : fallback;
}

const NO_CONNECTION = 'Could not reach the Legal Office server. Please check your connection and try again.';

/* ---------------- Steps ----------------
   The backend tracks a request's real current status (via its service type's
   workflow steps), so this list mirrors the DEFAULT_STATUS_SEQUENCE in
   backend/app/seed.py exactly. If that sequence changes, update this too. */
const STAGES = [
  {key:'Submitted',           title:'Submitted',           desc:'Your request has been successfully submitted.'},
  {key:'Received',            title:'Received',            desc:'The Legal Office has received your request.'},
  {key:'For Validation',      title:'For Validation',      desc:'Your complaint is being validated and routed for referral, as University procedure requires.'},
  {key:'Under Review',        title:'Under Review',        desc:'Your request is currently being reviewed.'},
  {key:'Clarificatory Meeting', title:'Clarificatory Meeting', desc:'The Legal Office will coordinate with you and the parties concerned for a clarificatory meeting.'},
  {key:'Processing',          title:'Processing',          desc:'Your request is being processed by the Legal Office.'},
  {key:'For Attorney Review', title:'For Attorney Review', desc:'Your request is awaiting the Attorney\u2019s review.'},
  {key:'Approved',            title:'Approved',            desc:'Your request has been approved.'},
  {key:'Completed',           title:'Completed',           desc:'Your request has been completed.'}
];
const STAGE_ORDER = STAGES.map(s=>s.key);

/* The same colours as the list on My Requests (js/portal.js). */
const STATUS_TONE = {
  'Submitted': 'submitted', 'Received': 'submitted',
  'For Validation': 'review', 'Under Review': 'review', 'Clarificatory Meeting': 'review', 'Processing': 'review',
  'For Attorney Review': 'attorney',
  'Approved': 'completed', 'Completed': 'completed',
  'Cancelled': 'cancelled', 'Rejected': 'cancelled', 'Withdrawn': 'cancelled'
};

function currentStageKey(record){
  if(record.is_rejected) return 'Rejected';
  if(record.is_withdrawn) return 'Withdrawn';
  if(record.is_cancelled) return 'Cancelled';
  if(STAGE_ORDER.includes(record.current_status)) return record.current_status;
  return 'Submitted'; // fallback if the request has no workflow step assigned yet
}

function isClosedRecord(record){
  return !!(record.is_cancelled || record.is_rejected);
}

/** Every step of this service, each marked done / current / upcoming. A closed
    request shows the steps up to where it stopped, then how it ended. */
function buildSteps(record){
  const stageKey = currentStageKey(record);
  const stages = (record.workflow_steps && record.workflow_steps.length)
    ? STAGES.filter(s => record.workflow_steps.includes(s.key))
    : STAGES;
  const closed = isClosedRecord(record);
  const index = Math.max(0, stages.findIndex(s => s.key === (closed ? record.current_status : stageKey)));
  if(closed){
    return {
      closed: true,
      position: index + 1,
      steps: [
        ...stages.slice(0, index + 1).map(s => ({title: s.title, state: 'done'})),
        {title: stageKey, state: record.is_withdrawn ? 'withdrawn' : 'closed'}
      ]
    };
  }
  const finished = stageKey === 'Completed';
  return {
    closed: false,
    position: index + 1,
    steps: stages.map((s, i) => ({
      title: s.title,
      state: finished || i < index ? 'done' : (i === index ? 'current' : 'upcoming')
    }))
  };
}

function stepDotHtml(state){
  if(state === 'done') return '<span class="rd-dot"><img src="img/icons/step-check.svg" alt="" width="14" height="14"></span>';
  if(state === 'closed' || state === 'withdrawn') return '<span class="rd-dot"><img src="img/icons/step-close.svg" alt="" width="14" height="14"></span>';
  return '<span class="rd-dot"></span>';
}

function renderSteps(record){
  const {steps, closed, position} = buildSteps(record);
  const current = state => state === 'current' ? ' aria-current="step"' : '';
  const spoken = state => state === 'done' ? '<span class="visually-hidden"> (done)</span>' : '';

  $('timeline').innerHTML = steps.map(s => `
    <li class="rd-step is-${s.state}"${current(s.state)}>
      <span class="rd-step-track">${stepDotHtml(s.state)}<span class="rd-line"></span></span>
      <span class="rd-step-label">${escapeHtml(s.title)}${spoken(s.state)}</span>
    </li>`).join('');

  // The phone's short bar and its "See all" list show the same steps.
  $('rdBar').innerHTML = steps.map(s => `<span class="is-${s.state}"></span>`).join('');
  $('rdStepsList').innerHTML = steps.map(s => `
    <li class="rd-step is-${s.state}"${current(s.state)}>
      ${stepDotHtml(s.state)}
      <span class="rd-step-label">${escapeHtml(s.title)}${s.state === 'current' ? ' (now)' : ''}${spoken(s.state)}</span>
    </li>`).join('');
  $('rdAllStepsLabel').textContent = `See all ${steps.length} steps`;

  $('rdStepCount').textContent = closed ? '' : `Step ${position} of ${steps.length}`;
}

/** "Legal Advice is usually acted on within 2 working days. Expected by November 4, 2026." */
function expectedLineHtml(record){
  const service = escapeHtml(record.service_type_name || serviceCodeToLabel(record.service_type_code));
  const expected = record.due_date ? `Expected by <b>${escapeHtml(formatDueDate(record.due_date))}</b>.` : '';
  if(record.processing_days){
    const days = record.processing_days === 1 ? '1 working day' : `${record.processing_days} working days`;
    return `${service} is usually acted on within ${days}. ${expected}`.trim();
  }
  if(expected) return expected;
  if(record.service_type_code === 'complaint_referral') return 'Complaints have no set period: it depends on the case.';
  return `${service} has no set period: it depends on the request.`;
}

function closedSentence(record){
  if(record.is_withdrawn) return 'You withdrew this request' + (record.closed_at ? ' on ' + formatDate(record.closed_at) : '') + '.';
  const what = record.is_rejected ? 'rejected' : 'cancelled';
  return 'The Legal Office ' + what + ' this request' + (record.closed_at ? ' on ' + formatDate(record.closed_at) : '') + '.';
}

/* The box under the steps: where the request is now, or how it ended. */
function renderStatusBox(record){
  const box = $('rdStatusBox');
  const stageKey = currentStageKey(record);
  const stage = STAGES.find(s => s.key === stageKey);
  const since = formatDate(record.current_status_since || record.submitted_at);
  if(isClosedRecord(record)){
    const reason = (record.closure_reason || '').trim();
    box.innerHTML = record.is_withdrawn
      ? noticeHtml('info', closedSentence(record), 'If this was a mistake, contact the Legal Office.')
      : noticeHtml('error', closedSentence(record), reason ? 'Reason: ' + escapeHtml(reason) : '');
    return;
  }
  if(stageKey === 'Completed'){
    box.innerHTML = noticeHtml('success', `Completed · ${since}`, escapeHtml(stage.desc));
    return;
  }
  box.innerHTML = `<div class="rd-step-box">
    <p class="rd-step-box-title"><b>${escapeHtml(stage.title)}</b> · ${escapeHtml(since)}</p>
    <p class="rn-body-text">${escapeHtml(stage.desc)}</p>
    <p class="rd-step-box-note">${expectedLineHtml(record)}</p>
  </div>`;
}

/* ---------------- Opening the request ---------------- */
const REQUEST_ID = (() => {
  const id = (new URLSearchParams(window.location.search).get('id') || '').trim().toUpperCase();
  return /^LEO-\d{4}-\d{6}$/.test(id) ? id : null;
})();

function showLookupError(title, message){
  $('rdLoading').hidden = true;
  $('rdBack').hidden = true;
  $('lookupErrorNotice').innerHTML = noticeHtml('error', title, escapeHtml(message));
  $('lookupError').hidden = false;
}

/** Fetches the request and draws the page. "quiet" is for a refresh of a page
    that is already showing: a failure then must not replace what the client sees. */
async function loadVerifiedRequest(quiet){
  let response;
  try{
    response = await fetch(`${API_BASE_URL}/portal/requests/${encodeURIComponent(REQUEST_ID)}`);
  }catch(err){
    if(!quiet) showLookupError('Could not reach the Legal Office server.', 'Please check your connection and try again.');
    return false;
  }
  if(response.status === 401 || response.status === 404){
    // Signed out, or not this client's request: back to My Requests.
    handleSessionEnded();
    return false;
  }
  if(!response.ok){
    if(!quiet) showLookupError('Could not load your request.', 'Please try again.');
    return false;
  }
  renderResults(await response.json());
  return true;
}

/* Session over (30 min idle / 2 h max) or signed out elsewhere: the client
   signs in again on My Requests. */
function handleSessionEnded(){
  window.location.href = 'portal.html';
}

/* ---------------- Render ---------------- */
let currentRecord = null;

function renderResults(record){
  currentRecord = record;
  $('rdLoading').hidden = true;
  $('lookupError').hidden = true;
  $('rdBack').hidden = false;
  $('rdHeroText').hidden = false;
  $('rdProgress').hidden = false;
  $('resultsWrap').hidden = false;

  const stageKey = currentStageKey(record);
  const service = record.service_type_name || serviceCodeToLabel(record.service_type_code);

  $('trackReqId').textContent = record.tracking_id;
  $('statusBadge').className = `status-pill status-pill-${STATUS_TONE[stageKey] || 'review'}`;
  $('statusBadge').textContent = stageKey;
  $('rdMetaText').textContent = `${service} · Submitted ${formatDate(record.submitted_at)}`;

  $('sumReqId').textContent = record.tracking_id;
  $('sumService').textContent = service;
  $('sumDate').textContent = formatDate(record.submitted_at);
  $('sumStatus').textContent = stageKey;
  $('sumOfficer').textContent = record.assigned_officer_name || 'Not yet assigned';

  renderSteps(record);
  renderStatusBox(record);
  renderClosedState(record);
  renderDocs(record);
  renderHistory(record);

  loadClientMessages();
  loadCsm();
}

/* Cancelled / rejected / withdrawn: hide what no longer works
   (uploads, messages, withdraw) and say why. "What's next?" also goes when
   the request is Completed: nothing is left to do. */
function renderClosedState(record){
  const closed = isClosedRecord(record);
  $('uploadWrap').hidden = closed;
  $('uploadClosedHint').hidden = !closed;
  $('whatsNext').hidden = closed || currentStageKey(record) === 'Completed';
  $('clientConvInputRow').hidden = closed;
  $('msgClosedHint').hidden = !closed;
  $('withdrawWrap').hidden = !record.can_withdraw;
  closeWithdrawPanel();
}

function openWithdrawPanel(){
  $('withdrawConfirmLine').textContent = `Withdraw request ${currentRecord.tracking_id}? The Legal Office will stop working on it.`;
  $('withdrawRow').hidden = true;
  $('withdrawPanel').hidden = false;
  $('withdrawReason').focus();
}

function closeWithdrawPanel(){
  $('withdrawPanel').hidden = true;
  $('withdrawRow').hidden = false;
  setError('withdrawMsg', '');
}

function setError(id, message){
  $(id).textContent = message;
  $(id).classList.toggle('is-shown', !!message);
}

async function withdrawRequest(){
  if(!currentRecord) return;
  const reason = $('withdrawReason').value.trim().slice(0, 500);
  const btn = $('withdrawConfirmBtn');
  btn.disabled = true;
  setError('withdrawMsg', '');
  try{
    const response = await fetch(`${API_BASE_URL}/portal/requests/${encodeURIComponent(REQUEST_ID)}/withdraw`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({reason: reason || null})
    });
    if(response.status === 401){ handleSessionEnded(); return; }
    const body = await response.json().catch(()=>({}));
    if(response.status === 429){ setError('withdrawMsg', 'Too many attempts. Please wait a minute and try again.'); return; }
    if(!response.ok){
      setError('withdrawMsg', detailOr(body, 'Could not withdraw your request. Please try again.'));
      return;
    }
    $('withdrawReason').value = '';
    $('withdrawCount').textContent = '0 / 500';
    renderResults(body);
    window.scrollTo(0, 0);
    $('rdTitle').focus({preventScroll: true});
  }catch(err){
    setError('withdrawMsg', NO_CONNECTION);
  }finally{
    btn.disabled = false;
  }
}

/* The server only ever sends documents this requester may see: their own
   uploads, plus Legal Office documents that staff have released. A file
   replaced by a revised copy stays listed, but asks for nothing more. */
function replacedVersions(docs){
  const byOldId = new Map();   // replaced file's id -> the version that replaced it
  docs.forEach(d => { if(d.supersedes_document_id) byOldId.set(d.supersedes_document_id, d.version); });
  return byOldId;
}

function renderFileList(container, docs, emptyText, dateWord, canRevise){
  if(!docs.length){
    container.innerHTML = `<p class="rn-body-text rn-muted">${escapeHtml(emptyText)}</p>`;
    return;
  }
  const replaced = replacedVersions(docs);
  container.innerHTML = '<ul class="rd-files">' + docs.map(d => {
    const name = escapeHtml(d.original_filename);
    const match = /\.([a-z0-9]{1,4})$/i.exec(d.original_filename);
    const needsRevision = d.status === 'revision_required' && !replaced.has(d.id);
    const versionText = d.version > 1 ? `Version ${Number(d.version)} \u00b7 ` : '';
    const replacedText = replaced.has(d.id) ? ` \u00b7 Replaced by version ${Number(replaced.get(d.id))}` : '';
    return `<li>
      <div class="rd-file${needsRevision ? ' is-revision' : ''}">
        <div class="rd-file-info">
          <span class="rd-file-type">${match ? escapeHtml(match[1].toUpperCase()) : 'FILE'}</span>
          <div class="rd-file-text">
            <p class="rd-file-name">${name}</p>
            ${needsRevision ? '<p class="rd-file-flag">Revision requested</p>' : ''}
            <p class="rn-note">${versionText}${dateWord} ${escapeHtml(formatDate(d.uploaded_at))}${replacedText}</p>
          </div>
        </div>
        <div class="rd-file-buttons">
          ${needsRevision && canRevise ? `<button type="button" class="nav-btn btn-secondary rd-file-revise" data-doc-id="${Number(d.id)}">Upload revised copy<span class="visually-hidden"> of ${name}</span></button>
          <input type="file" accept=".pdf,.doc,.docx,.jpg,.jpeg,.png" data-revise-input="${Number(d.id)}" hidden>` : ''}
          <button type="button" class="nav-btn btn-tertiary rd-file-download" data-doc-id="${Number(d.id)}" aria-label="Download ${name}">Download</button>
        </div>
      </div>
      <p class="err" role="alert"></p>
    </li>`;
  }).join('') + '</ul>';
  container.querySelectorAll('.rd-file-download').forEach(btn => {
    btn.addEventListener('click', () => downloadDocument(btn));
  });
  container.querySelectorAll('.rd-file-revise').forEach(btn => {
    const input = container.querySelector(`[data-revise-input="${btn.dataset.docId}"]`);
    btn.addEventListener('click', () => input.click());
    input.addEventListener('change', () => {
      const file = input.files[0];
      input.value = '';
      if(file) uploadRevisedCopy(btn, file);
    });
  });
}

/* A revised copy of one document the Legal Office asked to revise: saved as
   the next version of that file; the earlier one stays listed. The server
   checks everything again. */
async function uploadRevisedCopy(btn, file){
  if(!currentRecord || uploading) return;
  const errorEl = btn.closest('li').querySelector('.err');
  const showError = message => {
    errorEl.textContent = message;
    errorEl.classList.toggle('is-shown', !!message);
  };
  showError('');
  if(!MSG_FILE_TYPES[fileExtension(file.name)]){ showError(`\u201c${file.name}\u201d isn\u2019t a PDF, Word, JPG or PNG file.`); return; }
  if(!file.size){ showError(`\u201c${file.name}\u201d is empty.`); return; }
  if(file.size > MSG_MAX_FILE_BYTES){ showError(`\u201c${file.name}\u201d is larger than 20 MB.`); return; }
  const form = new FormData();
  form.append('file', file);
  uploading = true;
  btn.disabled = true;
  $('uploadStatus').textContent = 'Uploading\u2026';
  try{
    const response = await fetch(
      `${API_BASE_URL}/requests/${encodeURIComponent(currentRecord.tracking_id)}/documents/${btn.dataset.docId}/revision`,
      {method: 'POST', body: form}
    );
    if(response.status === 401){ handleSessionEnded(); return; }
    const body = await response.json().catch(() => ({}));
    if(!response.ok){
      $('uploadStatus').textContent = '';
      showError(response.status === 429 ? 'Too many uploads. Please wait a minute and try again.'
        : detailOr(body, 'Could not upload your document. Please try again.'));
      return;
    }
    const shown = await loadVerifiedRequest(true);
    $('uploadStatus').textContent = `Your revised copy was uploaded as version ${Number(body.version)}.`
      + (shown ? '' : ' Reload this page to see the list.');
  }catch(err){
    $('uploadStatus').textContent = '';
    showError(NO_CONNECTION);
  }finally{
    uploading = false;
    btn.disabled = false;
  }
}

function renderDocs(record){
  const allDocs = record.documents || [];
  const clientDocs = allDocs.filter(d=>d.category === 'client_submitted');
  const officeDocs = allDocs.filter(d=>d.category === 'legal_office');
  renderFileList($('docChipGrid'), clientDocs, 'No documents uploaded yet.', 'Uploaded', !isClosedRecord(record));
  renderFileList($('officeDocGrid'), officeDocs, 'Nothing has been released to you yet.', 'Added');
  renderActionAlert(record, clientDocs);
}

/* "What the Legal Office needs from you": the note staff wrote for the
   requester (also sent by email) and/or a document to revise. The note is the
   staff member's own text, so it is escaped. Nothing to act on once closed. */
function renderActionAlert(record, clientDocs){
  const closed = isClosedRecord(record);
  const replaced = replacedVersions(clientDocs);
  const needsRevision = !closed && clientDocs.some(d => d.status === 'revision_required' && !replaced.has(d.id));
  const waiting = !closed && !!record.is_waiting_for_client;
  const note = waiting ? (record.staff_note_to_client || '').trim() : '';
  const parts = [];
  if(note) parts.push(noticeHtml('warning', 'The Legal Office needs something from you', 'Message from the Legal Office: <span class="notice-note">' + escapeHtml(note) + '</span>'));
  else if(waiting) parts.push(noticeHtml('warning', 'The Legal Office is waiting for you', 'The Legal Office is waiting for a response or document from you.'));
  if(needsRevision) parts.push(noticeHtml('warning', 'A document needs revision', 'One or more of your documents needs revision. Click Upload revised copy beside it below.'));
  $('docsAlertNotices').innerHTML = parts.join('');
  $('rdJumpLinks').hidden = !waiting;
  $('docsAlert').hidden = !parts.length;
}

/* "What happened to this request, and when?" The server
   already removed internal entries and staff names; newest first. */
function renderHistory(record){
  const entries = record.history || [];
  $('historyList').innerHTML = entries.length
    ? entries.map(h => `
        <li>
          <span class="rd-history-when">${escapeHtml(formatDateTime(h.created_at))}</span>
          <span class="rd-history-text">${escapeHtml(h.description)}</span>
        </li>`).join('')
    : '<li><span class="rd-history-text rn-muted">No activity yet.</span></li>';
}

async function downloadDocument(btn){
  if(!currentRecord) return;
  const errorEl = btn.closest('li').querySelector('.err');
  const showError = message => {
    errorEl.textContent = message;
    errorEl.classList.toggle('is-shown', !!message);
  };
  showError('');
  try{
    const response = await fetch(
      `${API_BASE_URL}/requests/${encodeURIComponent(currentRecord.tracking_id)}/documents/${btn.dataset.docId}/download`,
      {method: 'POST'}
    );
    if(response.status === 401){ handleSessionEnded(); return; }
    if(!response.ok){
      showError('Could not download this document. Please try again.');
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
    showError('Could not reach the Legal Office server to download this document.');
  }
}

let uploading = false;

async function addDocumentToActiveRequest(fileListRaw){
  if(!currentRecord || uploading || !fileListRaw.length) return;
  const count = fileListRaw.length;
  const formData = new FormData();
  Array.from(fileListRaw).forEach(f => formData.append('files', f));

  uploading = true;
  $('uploadBtnTrack').disabled = true;
  setError('uploadError', '');
  $('uploadStatus').textContent = 'Uploading\u2026';
  try{
    const response = await fetch(
      `${API_BASE_URL}/requests/${encodeURIComponent(currentRecord.tracking_id)}/documents/client`,
      {method: 'POST', body: formData}
    );
    if(response.status === 401){ handleSessionEnded(); return; }
    if(!response.ok){
      const body = await response.json().catch(()=>({}));
      $('uploadStatus').textContent = '';
      setError('uploadError', detailOr(body, 'Could not upload your document. Please try again.'));
      return;
    }
    // Re-fetch so the new document (and its real status) comes from the
    // server rather than a client-side guess.
    const shown = await loadVerifiedRequest(true);
    const done = count === 1 ? 'Your file was uploaded.' : `Your ${count} files were uploaded.`;
    $('uploadStatus').textContent = shown ? done : done + ' Reload this page to see the list.';
  }catch(err){
    $('uploadStatus').textContent = '';
    setError('uploadError', NO_CONNECTION);
  }finally{
    uploading = false;
    $('uploadBtnTrack').disabled = false;
  }
}

/* ---------------- Client Satisfaction Measurement ----------------
   Offered once the request is Completed; optional, answered once. The
   questions come from the server (the office's editable survey file,
   backend/app/csm_form.json), so none are written here. Everything from the
   file is escaped before it goes into HTML. Staff see totals only. */
let csmForm = null;

async function loadCsm(){
  const wrap = $('csmWrap');
  // The page re-renders after an upload or message; keep a half-filled survey.
  const formWrap = $('csmFormWrap');
  if(csmForm && formWrap && !formWrap.hidden) return;
  wrap.hidden = true;
  if(!currentRecord || currentStageKey(currentRecord) !== 'Completed') return;
  try{
    const response = await fetch(`${API_BASE_URL}/portal/requests/${encodeURIComponent(REQUEST_ID)}/csm`);
    if(response.status === 401){ handleSessionEnded(); return; }
    if(!response.ok) return;   // the survey is optional: no error box on the page
    renderCsm(await response.json());
  }catch(err){ /* optional: leave it hidden */ }
}

function renderCsm(state){
  const wrap = $('csmWrap');
  const body = $('csmBody');
  csmForm = null;
  if(state.status === 'answered'){
    const when = state.answered_at ? ' on ' + formatDate(state.answered_at) : '';
    body.innerHTML = noticeHtml('success', `Thank you for answering our satisfaction survey${when}.`, 'Your answers help the Legal Office improve its services.');
  }else if(state.status === 'unavailable'){
    body.innerHTML = '<p class="rn-body-text rn-muted">Our satisfaction survey is not available right now. Please check back later.</p>';
  }else if(state.status === 'open' && state.form){
    csmForm = state.form;
    body.innerHTML = `
      <p class="rn-body-text rn-muted">${escapeHtml(csmForm.intro || '')}</p>
      <div id="csmStart"><button type="button" class="nav-btn btn-secondary" id="csmOpenBtn">Answer the survey</button></div>
      <div class="csm-form" id="csmFormWrap" tabindex="-1" hidden></div>`;
    $('csmOpenBtn').addEventListener('click', openCsmForm);
  }else{
    wrap.hidden = true;
    return;
  }
  wrap.hidden = false;
}

function openCsmForm(){
  const f = csmForm;
  let html = f.placeholder
    ? noticeHtml('draft', 'Draft survey', 'The questions are still being confirmed with the Legal Office.')
    : '';
  f.sections.forEach(sec => {
    html += `<fieldset class="csm-section"><legend>${escapeHtml(sec.heading)}</legend>`;
    if(sec.instructions) html += `<p class="rn-note">${escapeHtml(sec.instructions)}</p>`;
    sec.questions.forEach(q => {
      const qid = escapeHtml(q.id);
      html += `
        <div class="csm-question" role="radiogroup" tabindex="-1" aria-labelledby="csmQ_${qid}" data-qid="${qid}">
          <p class="csm-question-text" id="csmQ_${qid}"><b>${qid}.</b> ${escapeHtml(q.text)}</p>
          <div class="csm-options">
            ${q.options.map(o => `<label class="csm-option"><input type="radio" name="csm_${qid}" value="${escapeHtml(o.value)}"><span>${escapeHtml(o.label)}</span></label>`).join('')}
          </div>
          <p class="err">Choose an answer.</p>
        </div>`;
    });
    html += '</fieldset>';
  });
  if(f.comment){
    html += `<div class="field csm-comment">
      <label for="csmComment">${escapeHtml(f.comment.label)}</label>
      <textarea id="csmComment" rows="3" maxlength="${Number(f.comment.max_length) || 1000}"></textarea>
    </div>`;
  }
  html += `
    <div id="csmMsg" role="alert"></div>
    <div><button type="button" class="nav-btn btn-primary" id="csmSubmitBtn">Send my answers</button></div>`;
  const formWrap = $('csmFormWrap');
  formWrap.innerHTML = html;
  formWrap.hidden = false;
  $('csmStart').hidden = true;
  formWrap.focus();
  formWrap.addEventListener('change', e => {
    const question = e.target.closest('.csm-question');
    if(question){
      question.classList.remove('invalid');
      question.removeAttribute('aria-invalid');
    }
  });
  $('csmSubmitBtn').addEventListener('click', submitCsm);
}

function showCsmError(messageHtml){
  $('csmMsg').innerHTML = noticeHtml('error', 'Your answers were not sent', messageHtml);
}

async function submitCsm(){
  if(!csmForm) return;
  const answers = {};
  const missing = [];
  document.querySelectorAll('#csmFormWrap .csm-question').forEach(el => {
    const picked = el.querySelector('input[type="radio"]:checked');
    if(picked){ answers[el.dataset.qid] = picked.value; }
    else{
      missing.push(el);
      el.classList.add('invalid');
      el.setAttribute('aria-invalid', 'true');
    }
  });
  if(missing.length){
    showCsmError(escapeHtml('Please answer every question (missing: ' + missing.map(el => el.dataset.qid).join(', ') + '). Choose "Not applicable" where it does not apply.'));
    missing[0].focus();
    return;
  }
  const commentEl = $('csmComment');
  const btn = $('csmSubmitBtn');
  btn.disabled = true;
  $('csmMsg').textContent = '';
  try{
    const response = await fetch(`${API_BASE_URL}/portal/requests/${encodeURIComponent(REQUEST_ID)}/csm`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        form_version: csmForm.version,
        answers,
        comment: commentEl ? (commentEl.value.trim() || null) : null
      })
    });
    if(response.status === 401){ handleSessionEnded(); return; }
    const body = await response.json().catch(()=>({}));
    if(response.status === 429){ showCsmError('Too many attempts. Please wait a minute and try again.'); return; }
    if(!response.ok){
      showCsmError(escapeHtml(detailOr(body, 'Could not send your answers. Please try again.')));
      return;
    }
    renderCsm(body);
    $('csmWrap').scrollIntoView({block: 'nearest'});
    $('csmTitle').tabIndex = -1;
    $('csmTitle').focus({preventScroll: true});
  }catch(err){
    showCsmError(escapeHtml(NO_CONNECTION));
  }finally{
    btn.disabled = false;
  }
}

/* ---------------- Export / print ---------------- */
function downloadRequestSummary(){
  const record = currentRecord;
  if(!record) return;
  const lines = [
    `THE LEGAL OFFICE — Eastern Samar State University`,
    `Request Summary`,
    ``,
    `Request ID: ${record.tracking_id}`,
    `Full Name: ${record.requester_full_name}`,
    `Office or Agency: ${record.requester_company_agency || '\u2014'}`,
    `Request Type: ${record.request_type === 'followup' ? 'Follow-up Request' : 'New Request'}`,
    `Legal Service Requested: ${record.service_type_name || serviceCodeToLabel(record.service_type_code)}`,
    `Description: ${record.description}`,
    `Documents: ${(record.documents || []).map(d=>d.original_filename).join(', ') || 'None'}`,
    `Status: ${currentStageKey(record)}`,
    `Submitted: ${formatDate(record.submitted_at)}`
  ];
  const blob = new Blob([lines.join('\n')], {type:'text/plain'});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `${record.tracking_id}-summary.txt`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

/* ---------------- Messages ---------------- */
let shownMessages = 0;      // how many bubbles are in the box
let sendingMessage = false;

/** A short line in the message box: loading, empty, or an error. */
function setConversationText(text, withRetry){
  shownMessages = 0;
  const box = $('clientConvMessages');
  box.classList.remove('has-messages');
  box.removeAttribute('tabindex');
  box.innerHTML = `<p class="rn-body-text rn-muted">${escapeHtml(text)}</p>`
    + (withRetry ? '<button type="button" class="nav-btn btn-secondary" id="msgRetryBtn">Try again</button>' : '');
  if(withRetry) $('msgRetryBtn').addEventListener('click', loadClientMessages);
}

async function loadClientMessages(){
  if(!currentRecord) return;
  if(!shownMessages) setConversationText('Loading messages\u2026');
  try{
    const response = await fetch(`${API_BASE_URL}/requests/${encodeURIComponent(currentRecord.tracking_id)}/messages/lookup`, {
      method: 'POST'
    });
    if(response.status === 401){ handleSessionEnded(); return; }
    if(!response.ok){
      if(!shownMessages) setConversationText('Could not load messages.', true);
      return;
    }
    renderClientMessages(await response.json());
  }catch(err){
    if(!shownMessages) setConversationText('Could not reach the Legal Office server.', true);
  }
}

/* Files in messages. A message can carry up to 5 files;
   each is also one of the request's documents (Your Documents, or From the
   Legal Office). The server's limits, checked here first so the client hears
   about a bad file before it uploads; the server checks everything again. */
const MSG_MAX_FILES = 5;
const MSG_MAX_FILE_BYTES = 20 * 1024 * 1024;
const MSG_MAX_TOTAL_BYTES = 50 * 1024 * 1024;
const MSG_FILE_TYPES = {pdf: 'PDF', doc: 'Word', docx: 'Word', jpg: 'JPG', jpeg: 'JPG', png: 'PNG'};
let pendingFiles = [];   // picked for the next message, not sent yet

function fileExtension(name){
  const dot = name.lastIndexOf('.');
  return dot < 0 ? '' : name.slice(dot + 1).toLowerCase();
}

/** "PDF · 1.2 MB". */
function fileFacts(name, bytes){
  const type = MSG_FILE_TYPES[fileExtension(name)] || 'File';
  if(bytes == null) return type;
  const size = bytes < 1024 * 1024
    ? `${Math.max(1, Math.round(bytes / 1024))} KB`
    : `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${type} · ${size}`;
}

/** One file in a bubble: a JPG or PNG shows as a picture (js/image-preview.js),
    then the card with its Download button, like the Documents list. */
function messageFileHtml(a){
  const name = escapeHtml(a.original_filename);
  const picture = ChatImagePreview.isImage(a.mime_type);
  return `<li>
    ${picture ? ChatImagePreview.frameHtml('rd-bubble-preview', a.id, name) : ''}
    <div class="rd-bubble-file">
      <img src="img/icons/document-green-small.svg" alt="" width="20" height="20">
      <div class="rd-bubble-file-text">
        <p class="rd-bubble-file-name">${name}</p>
        <p class="rd-bubble-file-meta">${escapeHtml(fileFacts(a.original_filename, a.file_size_bytes))}</p>
      </div>
      <button type="button" class="nav-btn btn-tertiary rd-file-download" data-doc-id="${Number(a.id)}" aria-label="Download ${name}">Download</button>
    </div>
    <p class="err" role="alert"></p>
  </li>`;
}

function messageHtml(m){
  const fromOffice = m.sender_type === 'staff';
  const when = formatMessageTime(m.sent_at);
  const who = (fromOffice ? 'Legal Office' : 'You') + (when ? ' · ' + when : '');
  const files = m.attachments || [];
  return `<div class="rd-bubble ${fromOffice ? 'is-office' : 'is-client'}">
    <p class="rd-bubble-who">${escapeHtml(who)}</p>
    ${m.body ? `<p class="rd-bubble-text">${escapeHtml(m.body)}</p>` : ''}
    ${files.length ? `<ul class="rd-bubble-files" aria-label="${files.length === 1 ? '1 file' : files.length + ' files'}">${files.map(messageFileHtml).join('')}</ul>` : ''}
  </div>`;
}

/** A document's bytes for a picture preview, through the same download
    endpoint as the Download button. Null when it can't be read (the preview
    then just isn't shown; a signed-out client finds out from Download). */
async function fetchDocumentBlob(documentId){
  const response = await fetch(
    `${API_BASE_URL}/requests/${encodeURIComponent(currentRecord.tracking_id)}/documents/${encodeURIComponent(documentId)}/download`,
    {method: 'POST'}
  );
  return response.ok ? response.blob() : null;
}

/** The files picked for the next message, each with a Remove button. */
function renderPendingFiles(){
  $('msgFiles').innerHTML = pendingFiles.map((f, i) => {
    const name = escapeHtml(f.name);
    return `<li class="rd-bubble-file">
      <img src="img/icons/document-green-small.svg" alt="" width="20" height="20">
      <div class="rd-bubble-file-text">
        <p class="rd-bubble-file-name">${name}</p>
        <p class="rd-bubble-file-meta">${escapeHtml(fileFacts(f.name, f.size))}</p>
      </div>
      <button type="button" class="nav-btn btn-tertiary" data-remove-file="${i}" aria-label="Remove ${name}">Remove</button>
    </li>`;
  }).join('');
}

/** Adds picked files that pass the server's checks; returns what was left
    out and why. */
function addPendingFiles(list){
  const problems = [];
  let total = pendingFiles.reduce((sum, f) => sum + f.size, 0);
  for(const f of list){
    if(pendingFiles.some(p => p.name === f.name && p.size === f.size && p.lastModified === f.lastModified)) continue;
    if(pendingFiles.length >= MSG_MAX_FILES){
      problems.push(`You can send up to ${MSG_MAX_FILES} files with one message.`);
      break;
    }
    if(!MSG_FILE_TYPES[fileExtension(f.name)]) problems.push(`“${f.name}” isn’t a PDF, Word, JPG or PNG file.`);
    else if(!f.size) problems.push(`“${f.name}” is empty.`);
    else if(f.size > MSG_MAX_FILE_BYTES) problems.push(`“${f.name}” is larger than 20 MB.`);
    else if(total + f.size > MSG_MAX_TOTAL_BYTES) problems.push(`“${f.name}” would bring the files over 50 MB together.`);
    else{ pendingFiles.push(f); total += f.size; }
  }
  return problems;
}

/* Only new messages are added, so a screen reader announces just those
   (the box is aria-live) and not the whole conversation again. */
function renderClientMessages(messages){
  const box = $('clientConvMessages');
  if(!messages.length){
    setConversationText('No messages yet.');
    return;
  }
  if(!shownMessages) box.innerHTML = '';
  box.classList.add('has-messages');
  box.insertAdjacentHTML('beforeend', messages.slice(shownMessages).map(messageHtml).join(''));
  shownMessages = messages.length;
  box.scrollTop = box.scrollHeight;
  ChatImagePreview.watch(box, fetchDocumentBlob);
  // A long conversation scrolls inside the box: let the keyboard scroll it too.
  if(box.scrollHeight > box.clientHeight) box.tabIndex = 0;
}

function setMessageError(message){
  setError('msgError', message);
  $('f-message').classList.toggle('invalid', !!message);
  $('clientConvInput').setAttribute('aria-invalid', message ? 'true' : 'false');
}

/* A message is text, files, or both. If sending fails, the text and the
   files stay so the client can fix the problem and send again. */
async function sendClientMessage(){
  const input = $('clientConvInput');
  const text = input.value.trim();
  const files = [...pendingFiles];
  if(sendingMessage || !currentRecord) return;
  if(!text && !files.length){ input.focus(); return; }
  const sendBtn = $('clientConvSendBtn');
  const attachBtn = $('msgAttachBtn');
  // A problem with the files goes under the files, not on the message box.
  const showError = message => files.length ? setError('msgFileError', message) : setMessageError(message);
  sendingMessage = true;
  sendBtn.disabled = true;
  attachBtn.disabled = true;
  setMessageError('');
  setError('msgFileError', '');
  $('msgStatus').textContent = files.length ? 'Sending…' : '';
  try{
    const form = new FormData();
    form.append('body', text);
    files.forEach(f => form.append('files', f));
    const response = await fetch(`${API_BASE_URL}/requests/${encodeURIComponent(currentRecord.tracking_id)}/messages/client`, {
      method: 'POST',
      body: form
    });
    if(response.status === 401){ handleSessionEnded(); return; }
    if(response.status === 429){
      showError('Too many messages in a short time. Please wait a minute and try again.');
      return;
    }
    if(!response.ok){
      const body = await response.json().catch(() => ({}));
      showError(detailOr(body, 'Could not send your message. Please try again.'));
      return;
    }
    input.value = '';
    pendingFiles = [];
    renderPendingFiles();
    if(files.length){
      // The files are now in Your Documents and History too: read the whole
      // request again (this also adds the new message).
      await loadVerifiedRequest(true);
    }else{
      await loadClientMessages();
    }
  }catch(err){
    showError(NO_CONNECTION);
  }finally{
    sendingMessage = false;
    sendBtn.disabled = false;
    attachBtn.disabled = false;
    $('msgStatus').textContent = '';
  }
}

/* ---------------- Wire up events ---------------- */

$('retryBtn').addEventListener('click', () => window.location.reload());

$('uploadBtnTrack').addEventListener('click', ()=>{ $('fileInputTrack').click(); });
$('fileInputTrack').addEventListener('change', (e)=>{
  addDocumentToActiveRequest(e.target.files);
  e.target.value = '';
});
const uploadBoxTrack = $('uploadBoxTrack');
uploadBoxTrack.addEventListener('dragover', (e)=>{ e.preventDefault(); uploadBoxTrack.classList.add('is-dragging'); });
uploadBoxTrack.addEventListener('dragleave', ()=>{ uploadBoxTrack.classList.remove('is-dragging'); });
uploadBoxTrack.addEventListener('drop', (e)=>{
  e.preventDefault();
  uploadBoxTrack.classList.remove('is-dragging');
  addDocumentToActiveRequest(e.dataTransfer.files);
});

/* The two links under "The Legal Office needs something from you". */
function jumpTo(link, scrollTarget, focusTarget){
  link.addEventListener('click', (e)=>{
    e.preventDefault();
    $(scrollTarget).scrollIntoView({block: 'center'});
    $(focusTarget).focus({preventScroll: true});
  });
}
jumpTo($('jumpUpload'), 'uploadBoxTrack', 'uploadBtnTrack');
jumpTo($('jumpMessages'), 'clientConvInput', 'clientConvInput');

$('downloadBtn').addEventListener('click', downloadRequestSummary);
$('printBtn').addEventListener('click', ()=>window.print());

$('clientConvSendBtn').addEventListener('click', sendClientMessage);
$('clientConvMessages').addEventListener('click', (e)=>{
  const btn = e.target.closest('.rd-file-download');
  if(btn) downloadDocument(btn);
});
$('msgAttachBtn').addEventListener('click', ()=>{ $('msgFileInput').click(); });
$('msgFileInput').addEventListener('change', (e)=>{
  const problems = addPendingFiles([...e.target.files]);
  e.target.value = '';   // the same file can be picked again after removing it
  setError('msgFileError', problems.join(' '));
  renderPendingFiles();
});
$('msgFiles').addEventListener('click', (e)=>{
  const remove = e.target.closest('[data-remove-file]');
  if(!remove) return;
  const index = Number(remove.dataset.removeFile);
  pendingFiles.splice(index, 1);
  setError('msgFileError', '');
  renderPendingFiles();
  // Keep the keyboard nearby: the next file's Remove, else Attach files.
  const next = $('msgFiles').querySelectorAll('[data-remove-file]')[Math.min(index, pendingFiles.length - 1)];
  (next || $('msgAttachBtn')).focus();
});
$('clientConvInput').addEventListener('keydown', (e)=>{
  if(e.key === 'Enter') sendClientMessage();
});

$('withdrawOpenBtn').addEventListener('click', openWithdrawPanel);
$('withdrawCancelBtn').addEventListener('click', ()=>{
  closeWithdrawPanel();
  $('withdrawOpenBtn').focus();
});
$('withdrawConfirmBtn').addEventListener('click', withdrawRequest);
$('withdrawReason').addEventListener('input', (e)=>{
  $('withdrawCount').textContent = `${e.target.value.length} / 500`;
});


// Opened from My Requests, or from the link in an update email
// (request.html?id=LEO-…). A link alone never opens anything: without the
// client's sign-in, the server answers 401 and the page goes to My Requests.
if(REQUEST_ID){
  loadVerifiedRequest();
}else{
  window.location.href = 'portal.html';
}
