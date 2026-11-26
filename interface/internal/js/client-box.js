/* Client box on Request Detail.

   Shows who the client is, whether the Legal Office has verified them, how
   many requests they have, and their endorsement letter. Staff verify,
   decline, revoke, or correct the client from here.

   The server decides who may do what (routers/clients.py) and sends it as
   can_verify / can_decline / can_revoke / can_edit; this file only shows
   the buttons it was told to show. The rules:
   - Verify / Decline: attorney and Head Attorney on any request; a legal
     assistant only on requests assigned to them.
   - Revoke and Edit details: Head Attorney only.
   Everything typed by a client is escaped before it is shown.

   Needs: apiFetch (api-data.js), escapeHtml and ICONS (render-helper.js),
   formatLongDate / formatMediumDate (api-config.js), and currentRecord / fetchRequestDetail /
   renderShell / friendlyError from request-detail.js. */

const CLIENT_STATUS = {
  unverified: {cls: 'neutral', text: 'Not yet verified'},
  pending:    {cls: 'waiting', text: 'Endorsement letter to check'},
  verified:   {cls: 'ok',      text: 'Verified'},
  expired:    {cls: 'waiting', text: 'Verification expired'},
  declined:   {cls: 'bad',     text: 'Declined'},
  revoked:    {cls: 'bad',     text: 'Verification revoked'}
};
// How staff verified the client: the Verify panel's choices
// and the words used in the "Verified" line of the card.
const CLIENT_METHODS = {
  endorsement_letter: {label: 'Endorsement letter', desc: '', phrase: 'with the endorsement letter'},
  in_person:          {label: 'In person', desc: '', phrase: 'in person'},
  referral:           {label: 'Referral', desc: 'From a complaint or referral', phrase: 'through a complaint or referral'}
};
const CLIENT_CATEGORY_OPTIONS = [
  ['essu_office', 'ESSU – Office / Department'],
  ['essu_employee', 'ESSU – Employee'],
  ['essu_student', 'ESSU – Student'],
  ['ext_government', 'Outside ESSU – Government agency'],
  ['ext_private', 'Outside ESSU – Private individual or organization']
];
const ESSU_CLIENT_CATEGORIES = ['essu_office', 'essu_employee', 'essu_student'];

let currentClient = null;      // last answer from GET /requests/{id}/client
let clientBoxError = '';
let clientPanel = null;        // which panel is open: 'verify' | 'decline' | 'revoke' | 'edit' | null
let focusClientPanel = false;  // move focus into the panel once it has been drawn

/* ---------- loading ---------- */
async function loadClientBox(trackingId){
  clientBoxError = '';
  const result = await apiFetch(`/requests/${encodeURIComponent(trackingId)}/client`);
  if(result.ok){
    currentClient = result.data;
  }else{
    currentClient = null;
    clientBoxError = result.error || 'Could not load the client.';
  }
  renderClientBox();
}

/* After an action: reload the client AND the request, so the History tab
   and the name at the top show the change too. */
async function refreshAfterClientAction(){
  clientPanel = null;
  const {record} = await fetchRequestDetail(currentRecord.id);
  if(record) currentRecord = record;
  await loadClientBox(currentRecord.id);
  renderShell();
}

/* ---------- the box ---------- */
function clientStatusText(c){
  const s = CLIENT_STATUS[c.verification_status] || CLIENT_STATUS.unverified;
  if(c.verification_status === 'verified' && c.verification_expires_at){
    return `${s.text} until ${formatLongDate(c.verification_expires_at)}`;
  }
  return s.text;
}

function renderClientBox(){
  const box = document.getElementById('clientBox');
  if(!box) return;
  if(clientBoxError){
    box.innerHTML = `<p class="client-box-msg">${escapeHtml(clientBoxError)}</p>`;
    return;
  }
  const c = currentClient;
  if(!c){
    box.innerHTML = '<p class="client-box-msg">Loading client…</p>';
    return;
  }
  const status = CLIENT_STATUS[c.verification_status] || CLIENT_STATUS.unverified;
  const detailsMissing = !c.client_category;
  // Who filed it: the details saved with this request (snapshot), as shown at the top before.
  const who = currentRecord ? currentRecord.requester : {name: c.full_name, office: c.office_name || '', email: c.email || c.phone || ''};
  const detail = (label, value) => value ? `<div class="cb-detail"><dt>${label}</dt><dd>${value}</dd></div>` : '';

  // e.g. "By Sample Attorney on Nov 2, 2026, with the endorsement letter"
  const method = CLIENT_METHODS[c.verification_method];
  const verifiedLine = (c.verified_by_name && c.verified_at && ['verified', 'expired', 'revoked'].includes(c.verification_status))
    ? `By ${escapeHtml(c.verified_by_name)} on ${escapeHtml(formatMediumDate(c.verified_at))}${method ? ', ' + method.phrase : ''}`
    : '';

  // An open panel takes the place of the action buttons. Edit
  // details also takes the place of the client's details, since it shows
  // them as fields.
  const buttons = [];
  const wide = [];
  if(!clientPanel){
    if(c.can_verify && !detailsMissing) buttons.push(`<button type="button" class="btn btn-secondary" data-client-panel="verify">Verify client</button>`);
    if(c.can_edit) buttons.push(`<button type="button" class="btn btn-secondary" data-client-panel="edit">Edit details</button>`);
    if(c.can_decline) wide.push(`<button type="button" class="btn btn-danger-outline btn-block" data-client-panel="decline">Decline verification</button>`);
    if(c.can_revoke) wide.push(`<button type="button" class="btn btn-danger-outline btn-block" data-client-panel="revoke">Revoke verification</button>`);
  }

  if(clientPanel === 'edit'){
    box.innerHTML = `
      <span class="client-status ${status.cls}">${escapeHtml(clientStatusText(c))}</span>
      <div id="clientPanelSlot"></div>`;
    renderClientPanel();
    return;
  }

  box.innerHTML = `
    <span class="client-status ${status.cls}">${escapeHtml(clientStatusText(c))}</span>
    <div class="cb-identity">
      <p class="cb-name">${escapeHtml(who.name || c.full_name)}</p>
      ${who.office && who.office !== '—' ? `<p>${escapeHtml(who.office)}</p>` : ''}
      ${who.email && who.email !== '—' ? `<p class="cb-muted">${escapeHtml(who.email)}</p>` : ''}
    </div>
    <hr class="cb-divider">
    <dl class="cb-details">
      ${detail('Requesting as', c.client_category_label ? escapeHtml(c.client_category_label) : '')}
      ${detail('Position', c.position ? escapeHtml(c.position) : '')}
      ${detail('Requests', `<a class="cb-link" href="requests-all.html?client=${encodeURIComponent(c.id)}">${c.total_requests} request${c.total_requests === 1 ? '' : 's'} · ${c.open_requests} open</a>`)}
      ${detail('Endorsement letter', c.endorsement_filename ? `<button type="button" class="cb-link" id="clientLetterBtn">${ICONS.download}<span>${escapeHtml(c.endorsement_filename)}</span></button>` : '')}
      ${detail('Verified', verifiedLine)}
      ${detail('Staff note', c.verification_note ? `“${escapeHtml(c.verification_note)}”` : '')}
    </dl>
    ${detailsMissing ? `<p class="cb-note">This client hasn't completed their details (a walk-in or an older record).${c.can_edit ? ' Use Edit details to fill them in before verifying.' : ''}</p>` : ''}
    ${buttons.length || wide.length || clientPanel ? '<hr class="cb-divider">' : ''}
    ${buttons.length || wide.length ? `
      <div class="cb-actions">
        ${buttons.length ? `<div class="cb-actions-row">${buttons.join('')}</div>` : ''}
        ${wide.join('')}
      </div>` : ''}
    <div id="clientPanelSlot"></div>`;

  const letterBtn = document.getElementById('clientLetterBtn');
  if(letterBtn) letterBtn.addEventListener('click', downloadEndorsementLetter);
  box.querySelectorAll('[data-client-panel]').forEach(btn => {
    btn.addEventListener('click', () => {
      clientPanel = btn.dataset.clientPanel;
      focusClientPanel = true;
      renderClientBox();
    });
  });
  renderClientPanel();
}

/* ---------- action panels ---------- */
function renderClientPanel(){
  const slot = document.getElementById('clientPanelSlot');
  if(!slot) return;
  if(!clientPanel || !currentClient){ slot.innerHTML = ''; return; }
  if(clientPanel === 'verify') return renderVerifyPanel(slot);
  if(clientPanel === 'edit') return renderEditPanel(slot);
  return renderDecisionPanel(slot, clientPanel);
}

/* The frame every panel shares: heading with a close button, the fields,
   the confirm and Cancel buttons, and a line for errors. */
function clientPanelShell(title, body, confirmLabel, confirmCls){
  return `
    <section class="cb-form" aria-labelledby="clientPanelTitle">
      <div class="cb-form-head">
        <h3 class="cb-form-title" id="clientPanelTitle">${title}</h3>
        <button type="button" class="icon-btn rd-panel-close" id="clientPanelX" aria-label="Close">${ICONS.close}</button>
      </div>
      ${body}
      <div class="rd-panel-actions">
        <button type="button" class="btn btn-compact ${confirmCls}" id="clientConfirmBtn">${confirmLabel}</button>
        <button type="button" class="btn btn-tertiary btn-compact" id="clientCancelBtn">Cancel</button>
      </div>
      <p class="rd-panel-msg" id="clientPanelMsg" role="alert"></p>
    </section>`;
}

/* Close the panel and put focus back on the button that opened it. */
function closeClientPanel(){
  const was = clientPanel;
  clientPanel = null;
  renderClientBox();
  const opener = document.querySelector(`#clientBox [data-client-panel="${was}"]`);
  if(opener) opener.focus();
}

function wirePanel(slot, onConfirm){
  document.getElementById('clientPanelX').addEventListener('click', closeClientPanel);
  document.getElementById('clientCancelBtn').addEventListener('click', closeClientPanel);
  if(focusClientPanel){
    focusClientPanel = false;
    const first = slot.querySelector('input[type="radio"]:checked') ||
                  slot.querySelector('input:not(:disabled), textarea, select');
    if(first) first.focus();
  }
  const btn = document.getElementById('clientConfirmBtn');
  btn.addEventListener('click', async () => {
    const msg = document.getElementById('clientPanelMsg');
    msg.textContent = '';
    btn.disabled = true;
    const {ok, error} = await onConfirm(msg);
    if(ok){
      await refreshAfterClientAction();
      return;
    }
    btn.disabled = false;
    if(error) msg.textContent = friendlyError(error);
  });
}

function clientHasEmail(){
  return !!(currentRecord && currentRecord.requester && currentRecord.requester.hasEmail);
}

function renderVerifyPanel(slot){
  const c = currentClient;
  const methods = Object.entries(CLIENT_METHODS).filter(([key]) => key !== 'endorsement_letter' || c.endorsement_filename);
  const preferred = c.endorsement_filename ? 'endorsement_letter' : 'in_person';
  slot.innerHTML = clientPanelShell('Verify client', `
    <fieldset class="rd-field">
      <legend class="rd-label">How did you verify them?</legend>
      <div class="rd-options">
        ${methods.map(([key, m]) => `
          <label class="rd-option${m.desc ? ' rd-option-top' : ''}${key === preferred ? ' selected' : ''}">
            <input type="radio" name="clientMethod" value="${key}"${key === preferred ? ' checked' : ''}>
            <span class="rd-option-text"><span>${escapeHtml(m.label)}</span>${m.desc ? `<span class="rd-option-desc">${escapeHtml(m.desc)}</span>` : ''}</span>
          </label>`).join('')}
      </div>
    </fieldset>
    <div class="rd-field">
      <label class="rd-label" for="clientNoteInput">Note for staff <span class="rd-label-note">optional</span></label>
      <textarea id="clientNoteInput" class="rd-textarea rd-textarea-short" maxlength="500" rows="2" placeholder="e.g. Letter signed by the College Dean." aria-describedby="clientNoteHint"></textarea>
      <span class="rd-hint" id="clientNoteHint">Never shown to the client.</span>
    </div>
    <p class="rd-panel-intro rd-panel-note">They stay verified for the period in Settings (365 days by default). ${clientHasEmail()
      ? 'The client is emailed that they’re verified.'
      : 'The client has no email, so tell them by phone.'}</p>`,
    'Verify client', 'btn-primary');

  slot.querySelectorAll('input[name="clientMethod"]').forEach(input => {
    input.addEventListener('change', () => {
      slot.querySelectorAll('.rd-option').forEach(o => o.classList.toggle('selected', o.contains(input)));
    });
  });
  wirePanel(slot, async () => {
    const method = (slot.querySelector('input[name="clientMethod"]:checked') || {}).value;
    if(!method) return {ok: false, error: 'Choose how you verified the client.'};
    const note = document.getElementById('clientNoteInput').value.trim();
    const result = await clientAction('verify', {method, note: note || null});
    if(result.ok) remindToCall('Their details were verified.');   // phone-only clients (request-detail.js)
    return result;
  });
}

function renderDecisionPanel(slot, kind){
  const isRevoke = kind === 'revoke';
  const hasEmail = clientHasEmail();
  const outcome = isRevoke ? 'they’re no longer verified' : 'they couldn’t be verified';
  // No email goes to a phone-only client, so there is no email to put the
  // reason in: staff decide on the phone what to say.
  slot.innerHTML = clientPanelShell(isRevoke ? 'Revoke verification' : 'Decline verification', `
    <div class="rd-field">
      <label class="rd-label" for="clientReasonInput">${isRevoke ? 'Why revoke it?' : 'Why decline?'} <span class="rd-label-note">required</span></label>
      <textarea id="clientReasonInput" class="rd-textarea" maxlength="500" rows="3" placeholder="${isRevoke ? 'e.g. The endorsement letter turned out not to be genuine.' : 'e.g. The letter isn’t signed by an office head.'}"></textarea>
    </div>
    ${hasEmail ? `<label class="cb-check"><input type="checkbox" id="clientTellInput"> <span>Include this reason in the email to the client</span></label>` : ''}
    <p class="rd-panel-intro rd-panel-note">${hasEmail
      ? `The client is emailed that ${outcome}.`
      : `The client has no email, so tell them by phone that ${outcome}.`} They can’t send new requests until they contact the Legal Office. Requests already sent aren’t affected.</p>`,
    isRevoke ? 'Revoke verification' : 'Decline client', 'btn-danger');

  wirePanel(slot, async () => {
    const reasonInput = document.getElementById('clientReasonInput');
    const reason = reasonInput.value.trim();
    if(!reason){ reasonInput.focus(); return {ok: false, error: 'A reason is required.'}; }
    const tellInput = document.getElementById('clientTellInput');
    const tell = !!(tellInput && tellInput.checked);
    const result = await clientAction(kind, {reason, tell_client: tell});
    if(result.ok){   // phone-only clients (request-detail.js)
      remindToCall((isRevoke ? 'Their verification was withdrawn.' : 'Their details could not be verified.') +
                   ' They can’t submit new requests until they contact the Legal Office.');
    }
    return result;
  });
}

async function renderEditPanel(slot){
  const c = currentClient;
  const officesResult = await apiFetch('/portal/offices');
  const offices = officesResult.ok && Array.isArray(officesResult.data) ? officesResult.data : [];
  if(clientPanel !== 'edit') return;      // closed while the list was loading
  const typedOffice = !c.office_id && ESSU_CLIENT_CATEGORIES.includes(c.client_category) && c.office_name;
  // One field: label (with "optional" when it is), the control, a hint.
  const field = (id, label, control, opts = {}) => `
    <div class="rd-field"${opts.wrapId ? ` id="${opts.wrapId}"` : ''}>
      <label class="rd-label" for="${id}">${label}${opts.optional ? ' <span class="rd-label-note">optional</span>' : ''}</label>
      ${control}
      ${opts.hint ? `<span class="rd-hint">${opts.hint}</span>` : ''}
    </div>`;
  const text = (id, max, value, placeholder = '') =>
    `<input type="text" class="rd-input" id="${id}" maxlength="${max}"${placeholder ? ` placeholder="${placeholder}"` : ''} value="${escapeHtml(value || '')}">`;

  slot.innerHTML = clientPanelShell('Edit details', `
    <p class="rd-panel-intro rd-panel-note">Correct the client’s details, for example after checking an ID. Requests already sent keep the details they were sent with.</p>
    ${field('ceLast', 'Last name', text('ceLast', 80, c.last_name))}
    ${field('ceFirst', 'First name', text('ceFirst', 80, c.first_name))}
    ${field('ceMiddle', 'Middle name', text('ceMiddle', 80, c.middle_name, 'Middle name'), {optional: true})}
    <label class="cb-check"><input type="checkbox" id="ceNoMiddle"${c.last_name && !c.middle_name ? ' checked' : ''}> <span>No middle name</span></label>
    ${field('ceExt', 'Extension', text('ceExt', 10, c.name_extension, 'Jr., Sr., III'), {optional: true})}
    ${field('ceCategory', 'Requesting as', `
      <select class="rd-input" id="ceCategory">
        <option value="">Select one</option>
        ${CLIENT_CATEGORY_OPTIONS.map(([v, l]) => `<option value="${v}"${v === c.client_category ? ' selected' : ''}>${escapeHtml(l)}</option>`).join('')}
      </select>`)}
    ${field('ceOffice', 'Office or college', `
      <select class="rd-input" id="ceOffice">
        <option value="">Select one</option>
        ${offices.map(o => `<option value="${o.id}"${o.id === c.office_id ? ' selected' : ''}>${escapeHtml(o.name)}</option>`).join('')}
        <option value="other"${typedOffice ? ' selected' : ''}>Others (please specify)</option>
      </select>
      <input type="text" class="rd-input" id="ceOfficeOther" maxlength="200" aria-label="Office or college, typed" placeholder="Type the office or college" value="${typedOffice ? escapeHtml(c.office_name) : ''}">`,
      {wrapId: 'ceEssu', hint: 'Not listed? Choose Others and type it.'})}
    ${field('ceOrg', 'Agency or organization', text('ceOrg', 200, c.organization), {optional: true, wrapId: 'ceOrgWrap'})}
    ${field('cePosition', 'Position', text('cePosition', 150, c.position), {optional: true, hint: 'Students: year and program.'})}`,
    'Save details', 'btn-primary');

  const toggle = () => {
    const cat = document.getElementById('ceCategory').value;
    const essu = ESSU_CLIENT_CATEGORIES.includes(cat);
    document.getElementById('ceEssu').style.display = essu ? '' : 'none';
    document.getElementById('ceOrgWrap').style.display = cat && !essu ? '' : 'none';
    document.getElementById('ceOfficeOther').style.display = document.getElementById('ceOffice').value === 'other' ? '' : 'none';
    const noMiddle = document.getElementById('ceNoMiddle').checked;
    const middle = document.getElementById('ceMiddle');
    middle.disabled = noMiddle;
    if(noMiddle) middle.value = '';
  };
  ['ceCategory', 'ceOffice', 'ceNoMiddle'].forEach(id => document.getElementById(id).addEventListener('change', toggle));
  toggle();

  wirePanel(slot, async () => {
    const v = id => document.getElementById(id).value.trim();
    const cat = v('ceCategory');
    const officeValue = v('ceOffice');
    const essu = ESSU_CLIENT_CATEGORIES.includes(cat);
    if(!v('ceLast') || !v('ceFirst')) return {ok: false, error: 'Enter the last name and first name.'};
    if(!cat) return {ok: false, error: 'Choose how the client is requesting.'};
    return clientAction('edit', {
      last_name: v('ceLast'), first_name: v('ceFirst'),
      middle_name: v('ceMiddle') || null, no_middle_name: document.getElementById('ceNoMiddle').checked,
      name_extension: v('ceExt') || null,
      client_category: cat,
      office_id: essu && officeValue && officeValue !== 'other' ? Number(officeValue) : null,
      office_other: essu && officeValue === 'other' ? (v('ceOfficeOther') || null) : null,
      organization: !essu ? (v('ceOrg') || null) : null,
      position: v('cePosition') || null
    });
  });
}

/* ---------- server calls ---------- */
async function clientAction(kind, body){
  const id = encodeURIComponent(currentRecord.id);
  const path = kind === 'edit' ? `/requests/${id}/client` : `/requests/${id}/client/${kind}`;
  const result = await apiFetch(path, {
    method: kind === 'edit' ? 'PUT' : 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body)
  });
  return {ok: result.ok, error: result.error};
}

async function downloadEndorsementLetter(){
  try{
    const response = await fetch(`${API_BASE_URL}/requests/${encodeURIComponent(currentRecord.id)}/client/endorsement-letter`, {
      credentials: 'same-origin'
    });
    if(!response.ok){
      showPageMessage(response.status === 403 ? 'You can only open documents on requests assigned to you.' : 'Could not download the endorsement letter.', 'error');
      return;
    }
    const blob = await response.blob();
    const disposition = response.headers.get('Content-Disposition') || '';
    const utf8 = disposition.match(/filename\*=UTF-8''([^;]+)/);
    const plain = disposition.match(/filename="([^"]+)"/);
    let filename = 'endorsement-letter';
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
    showPageMessage('Could not reach the Legal Office server to download the letter.', 'error');
  }
}