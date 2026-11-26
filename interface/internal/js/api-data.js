/* ==========================================================================
   Bridge to the real backend API
   Replaces the earlier localStorage-based bridge now that a real FastAPI
   backend exists. Depends on API_BASE_URL, serviceCodeToLabel,
   STATUS_SEQUENCE, formatShortDate, and formatLongDate from api-config.js
   (must be loaded before this file).
   ========================================================================== */

/* --------------------------------------------------------------------------
   apiFetch — the single gateway every internal-console API call goes through.
   Handles timeouts, capped retries, 401 session expiry, and JSON parsing.
   Always returns {ok, status, data, error} and never throws, so callers
   don't need their own try/catch.
   -------------------------------------------------------------------------- */

const API_TIMEOUT_MS    = 15000;
// Saves wait longer: a slow save that times out may still have been saved
// (and emailed the client), so it is better not to give up early (E2E-04).
const API_SAVE_TIMEOUT_MS = 30000;
const API_MAX_RETRIES   = 2;
const API_RETRY_BASE_MS = 400;

let sessionExpiredHandled = false;

function apiSleep(ms){
  return new Promise(resolve => setTimeout(resolve, ms));
}

/** True when the staff member has typed something on this page that isn't
    saved yet (any form field changed from how the page loaded it). */
function pageHasUnsavedInput(){
  return Array.from(document.querySelectorAll('form input, form textarea, form select')).some(el => {
    if(el.type === 'hidden' || el.disabled) return false;
    if(el.type === 'checkbox' || el.type === 'radio') return el.checked !== el.defaultChecked;
    if(el.type === 'file') return el.files && el.files.length > 0;
    if(el.tagName === 'SELECT') return Array.from(el.options).some(o => o.selected !== o.defaultSelected);
    return el.value !== el.defaultValue;
  });
}

/** Keeps the page (and what was typed) and asks the staff member to sign
    in again in a new tab, as the client's form does (E2E-02). */
function showSignedOutBanner(){
  if(document.getElementById('signedOutBanner')) return;
  const bar = document.createElement('div');
  bar.id = 'signedOutBanner';
  bar.setAttribute('role', 'alert');
  bar.style.cssText = 'position:sticky;top:0;z-index:1000;padding:12px 16px;background:#fdecea;color:#6b1a14;'
    + 'border-bottom:1px solid #e0b4b0;font-size:14px;line-height:20px';
  const link = document.createElement('a');
  link.href = 'login.html';
  link.target = '_blank';
  link.rel = 'noopener';
  link.textContent = 'sign in again in a new tab';
  bar.append('You were signed out. What you typed is still here: ', link, ', then come back and save again.');
  document.body.prepend(bar);
}

function handleSessionExpired(isSave){
  if(isSave || pageHasUnsavedInput()){
    showSignedOutBanner();
    return;
  }
  if(sessionExpiredHandled) return;
  sessionExpiredHandled = true;
  if(!window.location.pathname.endsWith('login.html')){
    window.location.href = 'login.html?expired=1';
  }
}

async function apiFetch(path, options = {}){
  // retries: override the automatic retries (e.g. 0 for a slow GET that
  // must run only once, like the holiday proclamation check).
  const { timeoutMs, retries, ...fetchOptions } = options;
  const method = (fetchOptions.method || 'GET').toUpperCase();
  const maxAttempts = retries !== undefined ? retries
    : (method === 'GET' || method === 'HEAD') ? API_MAX_RETRIES : 0;
  const isSave = !(method === 'GET' || method === 'HEAD');
  const effectiveTimeoutMs = timeoutMs || (isSave ? API_SAVE_TIMEOUT_MS : API_TIMEOUT_MS);

  for(let attempt = 0; attempt <= maxAttempts; attempt++){
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), effectiveTimeoutMs);
    
    try{
      const res = await fetch(`${API_BASE_URL}${path}`, {
        credentials: 'same-origin',
        ...fetchOptions,
        signal: controller.signal
      });

      if(res.status === 401){
        handleSessionExpired(isSave);
        return {ok:false, status:401, data:null,
                error: isSave ? 'You were signed out, so this wasn’t saved. Sign in again in a new tab, then save again.'
                              : 'Your session has expired. Please sign in again.'};
      }

      if(res.status >= 500 && attempt < maxAttempts){
        await apiSleep(API_RETRY_BASE_MS * Math.pow(2, attempt));
        continue;
      }

      if(res.status === 429){
        // Rate limit (slowapi's body has no "detail"): same words on every page.
        return {ok:false, status:429, data:null,
                error:'Too many requests in a short time. Wait a moment, then try again.'};
      }

      if(!res.ok){
        let detail = `Request failed (${res.status})`;
        try{
          const body = await res.json();
          if(body && body.detail) detail = body.detail;
        }catch(_){ }
        return {ok:false, status:res.status, data:null, error:detail};
      }

      const data = res.status === 204 ? null : await res.json();
      // Lists send their total number of matches for paging (X-Total-Count).
      const totalHeader = res.headers.get('X-Total-Count');
      const total = totalHeader === null ? null : Number(totalHeader);
      return {ok:true, status:res.status, data, error:null, total};

    }catch(err){
      if(attempt === maxAttempts){
        return {ok:false, status:0, data:null, timedOut: err.name === 'AbortError',
                error: err.name === 'AbortError'
                  ? (isSave
                      // It may have been saved (and the client emailed): never invite a blind retry (E2E-04).
                      ? 'The server is slow, so LEXIS couldn’t confirm this was saved. Reload the page to check before trying again.'
                      : 'The server took too long to respond. Please try again.')
                  : 'Could not reach the Legal Office server. Check your connection and that the backend is running.'};
      }
      await apiSleep(API_RETRY_BASE_MS * Math.pow(2, attempt));
    }finally{
      clearTimeout(timer);
    }
  }

  return {ok:false, status:0, data:null, error:'Request failed after multiple attempts.'};
}

/** The backend sends times as UTC without a trailing "Z"; add it so the
    browser doesn't read them as local time (a due date is the
    end of a Philippine working day, 15:59:59 UTC). */
function asUtc(iso){
  if(!iso) return iso;
  return /[zZ]|[+-]\d\d:\d\d$/.test(iso) ? iso : iso + 'Z';
}

/** Fetches the request list. Pass filters to narrow it server-side
    (search, filters, and paging: see routers/requests.py list_requests). */
async function fetchAllRequests(filters = {}){
  const params = new URLSearchParams();
  if(filters.q)            params.set('q', filters.q);
  if(filters.status)       params.set('status_name', filters.status);
  if(filters.serviceTypeCode) params.set('service_type_code', filters.serviceTypeCode);
  if(filters.officerId !== undefined && filters.officerId !== '') params.set('assigned_officer_id', filters.officerId);
  if(filters.from)         params.set('submitted_from', filters.from);
  if(filters.to)           params.set('submitted_to', filters.to);
  if(filters.deadline)     params.set('deadline', filters.deadline);
  if(filters.openOnly)     params.set('open_only', 'true');
  if(filters.sort)         params.set('sort', filters.sort);
  if(filters.assignedToMe) params.set('assigned_to_me', 'true');
  if(filters.waitingForClient) params.set('waiting_for_client', 'true');
  if(filters.limit)        params.set('limit', filters.limit);
  if(filters.offset)       params.set('offset', filters.offset);
  if(filters.reassignmentRequested) params.set('reassignment_requested', 'true');
  if(filters.requesterId)  params.set('requester_id', filters.requesterId);
  if(filters.clientWaiting) params.set('client_waiting', 'true');

  const qs = params.toString();
  const result = await apiFetch(`/requests${qs ? '?' + qs : ''}`);

  if(!result.ok){
    console.error('Could not load requests:', result.error);
    return {rows:[], total:0, error:result.error};
  }

  return {
    total: result.total,
    rows: result.data.map(item => ({
      id: item.tracking_id,
      service: item.service_type_name,
      client: item.requester_company_agency || item.requester_full_name,
      clientName: item.requester_full_name,
      hasEmail: item.requester_has_email !== false,
      phone: item.requester_phone || '',             // only sent when the client has no email
      clientStatus: item.client_verification_status || null,
      submitted: formatShortDate(item.submitted_at),
      submittedRaw: item.submitted_at,
      officer: item.assigned_officer_name || 'Unassigned',
      status: item.current_status || 'Submitted',
      due: item.due_date ? formatShortDate(asUtc(item.due_date)) : '\u2014',
      dueDateRaw: item.due_date ? asUtc(item.due_date) : null,
      deadline: item.deadline_status || null,   // 'overdue' | 'approaching' | 'on_track' | null
      preparedByOfficer: item.prepared_by_officer_name || null,
      isAssignedToYou: !!item.is_assigned_to_you,
      isPreparedByYou: !!item.is_prepared_by_you,
      wasReturnedForRevision: !!item.was_returned_for_revision,
      isWaitingForClient: !!item.is_waiting_for_client,
      reassignmentRequested: !!item.reassignment_requested,
      reassignmentReason: item.reassignment_reason || null,
      staffNoteToClient: item.staff_note_to_client || null
    })),
    error: null
  };
}

/** Builds the Processing-tab timeline from the backend's single current_status. */
function workflowStepsOf(r){
  // This service's own steps; the standard list is a fallback.
  return (r.workflow_steps && r.workflow_steps.length) ? r.workflow_steps : STATUS_SEQUENCE;
}

/** Progress: every step of this service, each with the date and
    person from the History ("Status changed from A to B." entries; the
    latest move into a step wins). A closed request stops at workflow_status.
    Also says whether the work was just returned for revision: the latest
    return comes after the latest status change (both are saved together). */
function buildProcessingSteps(r){
  const steps = workflowStepsOf(r);
  const log = r.audit_log || [];
  const time = (e) => parseServerTime(e.created_at);
  const at = r.workflow_status || r.current_status || 'Submitted';
  const idx = Math.max(0, steps.indexOf(at));
  const closed = !!(r.is_cancelled || r.is_rejected);
  const completed = !closed && at === 'Completed' && idx === steps.length - 1;

  const moves = {};
  let lastChange = null;
  log.filter(e => e.action === 'status_change').forEach(e => {
    const m = /^Status changed from (.+) to (.+)\.$/.exec(e.detail || '');
    if(!m || m[1] === m[2]) return;
    if(!lastChange || time(e) > time(lastChange)) lastChange = e;
    if(moves[m[2]] && time(moves[m[2]]) > time(e)) return;
    moves[m[2]] = Object.assign({}, e, {back: steps.indexOf(m[1]) > steps.indexOf(m[2])});
  });

  const metaOf = (name, i) => {
    if(i === 0) return `${formatMediumDate(r.submitted_at)} \u00b7 ${r.requester_full_name || 'the client'}`;
    const e = moves[name];
    if(!e) return '';
    const by = !e.staff_name ? ''
      : e.back ? ` \u00b7 returned by ${e.staff_name}`
      : name === 'For Attorney Review' ? ` \u00b7 forwarded by ${e.staff_name}`
      : ` \u00b7 ${e.staff_name}`;
    return formatMediumDate(e.created_at) + by;
  };

  let returned = null;
  const ret = log.filter(e => e.action === 'returned_for_revision').reduce((a, e) => (!a || time(e) > time(a)) ? e : a, null);
  if(ret && !closed && at === 'Processing' && (!lastChange || time(ret) >= time(lastChange))){
    const m = /^Returned for revision: "([\s\S]*)"$/.exec(ret.detail || '');
    returned = {by: ret.staff_name || 'An attorney', date: formatMediumDate(ret.created_at), comment: m ? m[1] : ''};
  }

  return {
    index: idx,
    total: steps.length,
    completed,
    returned,
    steps: steps.map((name, i) => {
      const state = i < idx || completed ? 'done' : i === idx ? 'current' : 'todo';
      const meta = state === 'todo' ? '' : metaOf(name, i);
      return {title: name, state, meta: state === 'current' && !closed && meta ? 'Since ' + meta : meta};
    })
  };
}

/** Fetches a single request's full detail record for the Request Detail page. */
async function fetchRequestDetail(trackingId){
  const result = await apiFetch(`/requests/${encodeURIComponent(trackingId)}`);
  // status: 404 = no such request; 0 or 5xx = the server couldn't be reached.
  if(!result.ok) return {record:null, error:result.error, status:result.status};

  const r = result.data;
  const clientDocs = (r.documents || []).filter(d=>d.category === 'client_submitted');
  const replacedBy = {};   // a file a revised copy replaced -> the version that replaced it
  clientDocs.forEach(d => { if(d.supersedes_document_id) replacedBy[d.supersedes_document_id] = d.version; });
  const officeDocs  = (r.documents || []).filter(d=>d.category === 'legal_office');

  return {
    record: {
      id: r.tracking_id,
      title: r.service_type_name || serviceCodeToLabel(r.service_type_code),
      workflowSteps: workflowStepsOf(r),
      status: r.current_status || 'Submitted',
      lastUpdated: formatLongDate(r.last_updated_at),
      submitted: formatLongDate(r.submitted_at),
      due: r.due_date ? formatLongDate(asUtc(r.due_date)) : '\u2014',
      deadline: r.deadline_status || null,
      assignedTo: r.assigned_officer_name || 'Unassigned',
      assignedToId: r.assigned_officer_id || null,
      workflowStatus: r.workflow_status || null,   // where a closed request stopped (Reopen continues from here)
      dueShort: r.due_date ? formatMediumDate(asUtc(r.due_date)) : null,
      submittedShort: formatMediumDate(r.submitted_at),
      updatedShort: formatMediumDate(r.last_updated_at),
      closedShort: r.closed_at ? formatMediumDate(asUtc(r.closed_at)) : null,
      reassignmentRequested: !!r.reassignment_requested,
      reassignmentReason: r.reassignment_reason || null,
      // Cancelled / rejected / withdrawn
      isClosed: !!(r.is_cancelled || r.is_rejected),
      closedLabel: r.is_rejected ? 'Rejected'
        : (r.is_cancelled ? (r.closed_by_name === 'Requester' ? 'Withdrawn by the requester' : 'Cancelled') : null),
      closedBy: r.closed_by_name || null,
      closedAt: r.closed_at ? formatLongDate(asUtc(r.closed_at)) : null,
      closureReason: r.closure_reason || null,
      requester: {
        office: r.requester_company_agency || '\u2014',
        name: r.requester_full_name,
        email: r.requester_email || r.requester_phone || '\u2014',
        hasEmail: !!r.requester_email,         // false = phone-only: LEXIS can't email them
        phone: r.requester_phone || ''
      },
      overview: {
        requestType: r.request_type === 'followup' ? 'Follow-up Request' : 'New Request',
        purpose: r.purpose || '\u2014',
        description: r.description,
        requiredDocuments: clientDocs.map(d=>({name:d.original_filename, status: replacedBy[d.id] ? `Replaced by version ${replacedBy[d.id]}` : 'Submitted', submitted:true})),
        isWaitingForClient: !!r.is_waiting_for_client,
        staffNoteToClient: r.staff_note_to_client || '',
        ...waitingDates(r.audit_log || [])
      },
      processing: buildProcessingSteps(r),
      history: (r.audit_log || []).map(entry => ({
        action: entry.action,
        actorType: entry.actor_type || 'staff',   // 'staff' | 'requester' | 'system'
        actor: entry.staff_name,
        at: entry.created_at,
        detail: entry.detail
      })),
      documents: {
        client: clientDocs.map(d=>({id:d.id, name:d.original_filename, uploaded: formatMediumDate(d.uploaded_at), version: d.version || 1, replacedBy: replacedBy[d.id] || 0, status:d.status, addedByStaff: !!d.uploaded_by_staff_id})),
        office: officeDocs.map(d=>({id:d.id, name:d.original_filename, uploaded: formatMediumDate(d.uploaded_at), version: d.version || 1, status:d.status, released: !!d.is_released_to_requester}))
      }
    },
    error: null
  };
}

/** "Client action needed" dates, read from the History (no extra column):
    when the client was last asked (waiting switched on, or the note
    changed, since both email them) and the last reminder after that. */
function waitingDates(log){
  const latest = (test) => log.filter(test).reduce((a, e) =>
    (!a || parseServerTime(e.created_at) > parseServerTime(a.created_at)) ? e : a, null);
  const asked = latest(e => (e.action === 'waiting_for_client_change' && /^Marked/.test(e.detail))
                         || (e.action === 'client_note_change' && !/^Cleared/.test(e.detail)));
  const reminder = latest(e => e.action === 'reminder_sent');
  const reminderCounts = reminder && (!asked || parseServerTime(reminder.created_at) >= parseServerTime(asked.created_at));
  return {
    askedShort: asked ? formatMediumDate(asked.created_at) : null,
    reminderShort: reminderCounts ? formatMediumDate(reminder.created_at) : null
  };
}

/** Attorney only: sends forwarded work back to Processing with a required
    comment (saved to the internal History only). Returns {ok, error}. */
async function returnForRevision(trackingId, comment){
  const result = await apiFetch(`/requests/${encodeURIComponent(trackingId)}/return`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({comment})
  });
  return {ok: result.ok, error: result.error};
}

/** Attorney only: cancel or reject a request. The reason is
    emailed to the requester and shown on their request in My Requests. Returns {ok, error}. */
async function closeRequest(trackingId, action, reason){
  const result = await apiFetch(`/requests/${encodeURIComponent(trackingId)}/close`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({action, reason})
  });
  return {ok: result.ok, error: result.error};
}

/** Phone-only clients: records in the staff History that staff called
    the client. Returns {ok, error}. */
async function recordClientContacted(trackingId){
  const result = await apiFetch(`/requests/${encodeURIComponent(trackingId)}/client-contacted`, {method: 'POST'});
  return {ok: result.ok, error: result.error};
}

/** Attorney only: reopen a cancelled, rejected, or withdrawn request. The
    reason stays in the staff History. Returns {ok, error}. */
async function reopenRequest(trackingId, reason){
  const result = await apiFetch(`/requests/${encodeURIComponent(trackingId)}/reopen`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({reason})
  });
  return {ok: result.ok, error: result.error};
}

/** Advances a request to a new named status. Returns {ok, error}. */
async function updateRequestStatus(trackingId, newStatus){
  const result = await apiFetch(`/requests/${encodeURIComponent(trackingId)}/status`, {
    method: 'PATCH',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({new_status: newStatus})
  });
  return {ok: result.ok, error: result.error};
}

/** Updates the waiting-for-client flag and/or the note left for the client.
    Pass only the fields you want to change. */
async function updateClientWaitStatus(trackingId, {isWaitingForClient, note} = {}){
  const payload = {};
  if(isWaitingForClient !== undefined) payload.is_waiting_for_client = isWaitingForClient;
  if(note !== undefined) payload.note = note;
  const result = await apiFetch(`/requests/${encodeURIComponent(trackingId)}/status`, {
    method: 'PATCH',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload)
  });
  return {ok: result.ok, error: result.error};
}

async function createStaff({ fullName, email, password, role }) {
  const { ok, data, error } = await apiFetch('/staff', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ full_name: fullName, email, password, role }),
  });
  if (!ok) return { staff: null, error: error || 'Could not create account.' };
  return { staff: data, error: null };
}

async function fetchStaff() {
  const { ok, data, error } = await apiFetch('/staff');
  if (!ok) return { rows: [], error: error || 'Could not load staff accounts.' };
  return { rows: data, error: null };
}

async function updateStaff(staffId, changes) {
  const { ok, data, error } = await apiFetch(`/staff/${staffId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(changes),
  });
  if (!ok) return { staff: null, error: error || 'Could not update account.' };
  return { staff: data, error: null };
}

// Head Attorney: a temporary password for a staff member who forgot theirs.
// The server ends that person's open sessions and logs it (Security Log).
async function setStaffPassword(staffId, password) {
  const { ok, error } = await apiFetch(`/staff/${staffId}/password`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ password }),
  });
  return { ok, error: ok ? null : (error || 'Could not set the password.') };
}

async function verifyStaffEmail(email, code) {
  const { ok, data, error } = await apiFetch('/auth/verify-email', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, code }),
  });
  if (!ok) return { staff: null, error: error || 'Could not verify this account.' };
  return { staff: data, error: null };
}

async function resendVerification(email) {
  const { ok, data, error } = await apiFetch('/auth/resend-verification', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email }),
  });
  if (!ok) return { devCode: null, error: error || 'Could not resend the code.' };
  return { devCode: (data && data.dev_code) || null, error: null };
}

/** Head-Attorney only: reassigns to a new officer, or pass no id to dismiss a pending reassignment request without reassigning. */
async function reassignRequest(trackingId, newOfficerId){
  const result = await apiFetch(`/requests/${encodeURIComponent(trackingId)}/reassign`, {
    method: 'PATCH',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({new_officer_id: newOfficerId ?? null})
  });
  return {ok: result.ok, error: result.error};
}

/** Legal Assistant/Attorney: submits a reassignment request with a reason on their own assigned case. */
async function requestReassignment(trackingId, reason){
  const result = await apiFetch(`/requests/${encodeURIComponent(trackingId)}/status`, {
    method: 'PATCH',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({reassignment_reason: reason})
  });
  return {ok: result.ok, error: result.error};
}

/** Fetches a request's message thread (internal/staff side). */
async function fetchMessages(trackingId){
  const result = await apiFetch(`/requests/${encodeURIComponent(trackingId)}/messages`);
  if(!result.ok) return {rows: [], error: result.error};
  return {rows: result.data, error: null};
}

/** Sends a staff message on a request's thread: text, files (up to 5), or
    both. Sent as a form; the browser sets the Content-Type itself. */
async function sendMessage(trackingId, body, files = []){
  const form = new FormData();
  form.append('body', body);
  for(const file of files) form.append('files', file);
  const result = await apiFetch(`/requests/${encodeURIComponent(trackingId)}/messages`, {
    method: 'POST',
    body: form,
    timeoutMs: files.length ? 120000 : undefined   // uploads can be slow
  });
  return {message: result.ok ? result.data : null, error: result.error};
}
