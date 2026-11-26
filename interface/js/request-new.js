/* New request, reached from My Requests.

   request-new.html                 -> Request a Legal Service (door "service")
   request-new.html?door=complaint  -> File a Complaint or Referral (door "complaint")

   On load (two calls, once):  GET /portal/me        who is signed in, may they submit, their requests
                               GET /portal/services  the services THEY may use, each with its checklist
   Submit (one call):          POST /portal/requests  the request and every file together

   The server checks everything again (who may submit, the service, every
   required document, file types and sizes, daily limit, duplicates). The
   checks here only help the client fix things before sending. */

const MAX_FILE_BYTES = 20 * 1024 * 1024;    // same as the server (core/storage.py)
const MAX_TOTAL_BYTES = 50 * 1024 * 1024;   // same as the server (routers/portal.py)
const MAX_FILES = 10;                        // checklist + other files; the endorsement letter is separate
const ALLOWED_EXT = ['.pdf', '.doc', '.docx', '.jpg', '.jpeg', '.png'];
const DOOR = new URLSearchParams(window.location.search).get('door') === 'complaint' ? 'complaint' : 'service';
const OFFICE_EMAIL_LINK = '<a href="mailto:legaloffice@essu.edu.ph">legaloffice@essu.edu.ph</a>';
const TEXT = {
  service: {
    kicker: 'New request',
    title: 'Request a legal service',
    lede: 'Choose the service you need, describe your concern, and attach the documents asked for.',
    serviceHeading: 'Choose the service',
    descLabel: 'Describe your request',
    doneKicker: 'Request sent',
    done: 'Your request has been submitted.',
    noneTitle: 'No legal services to request online',
    none: 'There are no legal services you can request online with your current details. If you need help with a complaint or referral, go back to My Requests and choose "File a complaint or referral", or contact the Legal Office.',
    symbol: 'symbol-document-pen',
    doneSymbol: 'symbol-document-check'
  },
  complaint: {
    kicker: 'Complaint or referral',
    title: 'File a complaint or referral',
    lede: 'Describe the complaint or referral and attach the signed complaint or referral letter.',
    serviceHeading: 'Type of filing',
    descLabel: 'Describe your complaint or referral',
    doneKicker: 'Filed',
    done: 'Your complaint or referral has been filed.',
    noneTitle: 'Complaints can\'t be filed online right now',
    none: 'Complaints and referrals cannot be filed online right now. Please contact the Legal Office.',
    symbol: 'symbol-complaint',
    doneSymbol: 'symbol-document-check-sage'
  }
}[DOOR];
// The icon on each service card (same files as the Home page).
const SERVICE_ICON = {
  legal_advice: 'legal-advice', legal_research: 'legal-research', contract_review: 'contract-review',
  investigation: 'investigation', drafting_document: 'drafting', other: 'other-concerns', complaint_referral: 'complaint'
};
// What happens to a complaint first: same words as the stages on the request page (js/request.js).
const COMPLAINT_NEXT = [
  ['Received', 'The Legal Office has received your request.'],
  ['For Validation', 'Your complaint is being validated and routed for referral, as University procedure requires.'],
  ['Clarificatory Meeting', 'The Legal Office will coordinate with you and the parties concerned for a clarificatory meeting.']
];

let me = null;                 // GET /portal/me
let services = [];             // this door's services only
let endorsementNeeded = false;
let endorsementTemplate = '';
let selectedCode = '';
let reqType = 'new';
let slotFiles = {};            // requirement id -> File
let slotProblems = {};         // slot key -> why the chosen file was refused
let endorsementFile = null;
let extraFiles = [];
let submitting = false;
let tried = false;             // Submit was pressed with things missing: now show the errors
let summaryShown = null;       // what the error summary says now, so a screen reader isn't told twice
let checklistShown = null;     // same, for "Before you can submit"

/* ---------- small helpers ---------- */
function $(id){ return document.getElementById(id); }
function escapeHtml(value){
  return String(value ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
/** messageHtml must already be safe: escaped, or our own fixed text. */
function noticeHtml(kind, title, messageHtml){
  return `<div class="notice notice-${kind}">
    <img src="img/icons/${kind}.svg" alt="" width="24" height="24">
    <div><p class="notice-title">${escapeHtml(title)}</p><p class="notice-message">${messageHtml}</p></div>
  </div>`;
}
/** Shows or clears a field's error, and tells a screen reader which field it belongs to. */
function setInvalid(fieldId, bad){
  const field = $(fieldId);
  field.classList.toggle('invalid', !!bad);
  const input = field.querySelector('[data-hint], input[type="checkbox"]');
  const err = field.querySelector('.err');
  if(input && err){
    input.setAttribute('aria-invalid', bad ? 'true' : 'false');
    const ids = [input.dataset.hint, bad ? err.id : ''].filter(Boolean).join(' ');
    if(ids) input.setAttribute('aria-describedby', ids);
    else input.removeAttribute('aria-describedby');
  }
}
function errorText(body, fallback){
  if(body && typeof body.detail === 'string') return body.detail;
  if(body && Array.isArray(body.detail) && body.detail[0] && body.detail[0].msg){
    return String(body.detail[0].msg).replace(/^Value error, /, '');
  }
  return fallback;
}
async function api(path, options = {}){
  let response;
  try{
    response = await fetch(`${API_BASE_URL}${path}`, options);
  }catch(err){
    return {ok: false, status: 0, body: {}};   // no connection
  }
  const body = await response.json().catch(() => ({}));
  return {ok: response.ok, status: response.status, body};
}
function show(viewId){
  ['loadingView', 'blockedView', 'formView', 'doneView'].forEach(v => { $(v).hidden = v !== viewId; });
  window.scrollTo({top: 0});
}
function formatSize(bytes){
  return bytes >= 1024 * 1024 ? (bytes / (1024 * 1024)).toFixed(1) + ' MB' : Math.max(1, Math.round(bytes / 1024)) + ' KB';
}
/** null when the file is fine, otherwise the reason. */
function fileProblem(file){
  const dot = file.name.lastIndexOf('.');
  const ext = dot >= 0 ? file.name.slice(dot).toLowerCase() : '';
  if(!ALLOWED_EXT.includes(ext)) return `"${file.name}" can't be uploaded. Use PDF, DOC, DOCX, JPG or PNG.`;
  if(file.size > MAX_FILE_BYTES) return `"${file.name}" is larger than 20 MB.`;
  if(file.size === 0) return `"${file.name}" is empty.`;
  return null;
}
function selectedService(){
  if(reqType === 'followup'){
    const tid = $('followupSelect').value;
    const r = me.requests.find(x => x.tracking_id === tid);
    return r ? services.find(s => s.code === r.service_type_code) || {code: r.service_type_code, name: r.service_type_name, requirements: []} : null;
  }
  return services.find(s => s.code === selectedCode) || null;
}
function openFollowups(){
  return me.requests.filter(r => !r.is_closed && r.door === DOOR);
}
/** The complaint door normally has one service: nothing to choose, so no "Choose" step. */
function hasServiceStep(){
  return !(DOOR === 'complaint' && services.length === 1);
}
/** "Attach the signed letter." from the label "Signed letter" (MOA stays MOA). */
function attachText(label){
  const small = /^[A-Z][a-z]/.test(label) ? label.charAt(0).toLowerCase() + label.slice(1) : label;
  return `Attach the ${small}.`;
}

/* ---------- the top of the page ---------- */
function setHero(parts){
  $('rnSymbol').src = `img/icons/${parts.symbol}.svg`;
  $('rnSymbolPhone').src = `img/icons/${parts.symbol}-phone.svg`;
  $('rnKicker').textContent = parts.kicker;
  $('rnTitle').textContent = parts.title;
  $('rnLede').textContent = parts.lede || '';
  $('rnLede').hidden = !parts.lede;
  $('rnWho').hidden = !parts.who;
  if(parts.who) fillWhoLine();
  $('rnHeroLoading').hidden = true;
  $('rnHeroText').hidden = false;
}
function fillWhoLine(){
  // textContent only, so a name or organization is never read as HTML.
  const where = me.office_name || me.organization;
  const name = document.createElement('b');
  name.textContent = me.full_name;
  $('rnWho').textContent = 'Submitting as ';
  $('rnWho').append(name, `${where ? ' · ' + where : ''} · ${me.contact}`);
}

/* ---------- loading ---------- */
async function init(){
  document.body.classList.toggle('door-complaint', DOOR === 'complaint');
  $('rnSymbol').src = `img/icons/${TEXT.symbol}.svg`;
  $('rnSymbolPhone').src = `img/icons/${TEXT.symbol}-phone.svg`;
  document.title = `${TEXT.title} — The Legal Office, ESSU`;
  const [meRes, svcRes] = await Promise.all([api('/portal/me'), api('/portal/services')]);
  if(meRes.status === 401 || svcRes.status === 401){
    window.location.href = 'portal.html';   // not signed in (or the session ended)
    return;
  }
  if(!meRes.ok || !svcRes.ok){
    showLoadError(meRes.ok ? svcRes : meRes);
    return;
  }
  me = meRes.body;
  if(me.needs_profile){ window.location.href = 'portal.html'; return; }
  if(!me.can_submit_new){ showCantSubmit(); return; }
  services = (svcRes.body.services || []).filter(s => s.door === DOOR);
  if(!services.length){ showNoServices(); return; }
  // The letter is for legal service requests only; complaints never ask for it.
  endorsementNeeded = !!svcRes.body.endorsement_required && DOOR === 'service';
  endorsementTemplate = svcRes.body.endorsement_template || '';
  renderForm();
}

/** Shows the page's message instead of the form. */
function showBlocked(noticeMarkup, options = {}){
  setHero({symbol: TEXT.symbol, kicker: TEXT.kicker, title: TEXT.title, who: !!me});
  $('blockedNotice').innerHTML = noticeMarkup;
  $('blockedHelp').hidden = !options.help;
  $('blockedContact').hidden = !options.contact;
  $('blockedRetry').hidden = !options.retry;
  // One main button per view: Try again when it is there.
  $('blockedBack').className = `nav-btn ${options.retry ? 'btn-tertiary' : 'btn-primary'}`;
  show('blockedView');
}
function showLoadError(res){
  if(res.status === 0){
    showBlocked(noticeHtml('error', 'Could not reach the Legal Office server.', 'Please check your connection and try again.'), {retry: true});
  }else{
    showBlocked(noticeHtml('error', 'Could not load the form.', escapeHtml(errorText(res.body, 'Please try again.'))), {retry: true});
  }
}
function showCantSubmit(){
  if(me.verification_status === 'declined' || me.verification_status === 'revoked'){
    showBlocked(noticeHtml('error', 'The Legal Office could not verify your details.',
      `Please contact the Legal Office before submitting a new request. Email ${OFFICE_EMAIL_LINK}.`));
    return;
  }
  showBlocked(noticeHtml('warning', 'You can\'t start a new request yet',
    escapeHtml(me.submit_block_reason || 'You cannot submit a new request right now.')), {help: true});
}
function showNoServices(){
  showBlocked(noticeHtml('info', TEXT.noneTitle, escapeHtml(TEXT.none)), {contact: DOOR === 'complaint'});
}

/* ---------- the form ---------- */
function renderForm(){
  setHero({symbol: TEXT.symbol, kicker: TEXT.kicker, title: TEXT.title, lede: TEXT.lede, who: true});
  $('serviceHeading').textContent = TEXT.serviceHeading;
  $('descLabel').textContent = TEXT.descLabel;

  // One service only: picked already.
  if(services.length === 1) selectedCode = services[0].code;

  // Complaint door: a card says what is being filed, and the steps start at "Tell us about it".
  const filing = !hasServiceStep();
  $('filingCard').hidden = !filing;
  $('stepService').hidden = filing;
  if(filing){
    $('filingName').textContent = services[0].name;
    $('filingDesc').textContent = services[0].description || '';
    $('filingDesc').hidden = !services[0].description;
    $('filingCard').appendChild($('followupBlock'));
  }

  // Follow-up: only when the client has an ongoing request through this door.
  const ongoing = openFollowups();
  $('followupBlock').hidden = !ongoing.length;
  ongoing.forEach(r => {
    const opt = document.createElement('option');
    opt.value = r.tracking_id;
    opt.textContent = `${r.tracking_id} — ${r.service_type_name || ''}`;
    $('followupSelect').appendChild(opt);
  });

  renderServices();

  $('endorsementBlock').hidden = !endorsementNeeded;
  $('templateRow').hidden = !endorsementTemplate;
  if(endorsementNeeded) renderEndorsementSlot();
  renderChecklist();
  renderExtras();
  show('formView');
  refresh();
}

function serviceDays(s){
  return s.default_processing_days
    ? `Usually acted on within ${s.default_processing_days} working days`
    : 'No set period: depends on the request';
}
function serviceSquare(code){
  const icon = SERVICE_ICON[code] || 'other-concerns';
  return `<span class="accent-square accent-teal"><img src="img/icons/${icon}.svg" alt="" width="24" height="24"></span>`;
}
/** "Pick the one that fits": only when there are services to pick from on this page. */
function refreshServiceHint(){
  $('serviceHint').hidden = !(reqType === 'new' && services.length > 1 && DOOR === 'service');
}
function renderServices(){
  const list = $('serviceList');
  refreshServiceHint();
  $('studentNote').hidden = !(services.length === 1 && services[0].code === 'legal_research' && me.client_category === 'essu_student');
  if(services.length === 1){
    list.removeAttribute('role');
    list.classList.add('rn-services-one');
    list.innerHTML = `<div class="rn-chosen">
        ${serviceSquare(services[0].code)}
        <div class="rn-service-body">
          <span class="rn-service-name">${escapeHtml(services[0].name)}</span>
          <span class="rn-service-days">${escapeHtml(serviceDays(services[0]))}</span>
        </div>
      </div>`;
    return;
  }
  list.innerHTML = services.map(s => `
    <label class="rn-service${s.code === selectedCode ? ' selected' : ''}">
      <input class="rn-radio-input" type="radio" name="service" value="${escapeHtml(s.code)}"${s.code === selectedCode ? ' checked' : ''}>
      <span class="rn-service-body">
        <span class="rn-service-name">${escapeHtml(s.name)}</span>
        ${s.description ? `<span class="rn-service-desc">${escapeHtml(s.description)}</span>` : ''}
        <span class="rn-service-days">${escapeHtml(serviceDays(s))}</span>
      </span>
      ${serviceSquare(s.code)}
    </label>`).join('');
  list.querySelectorAll('input[name="service"]').forEach(input => {
    input.addEventListener('change', () => changeService(input));
  });
}

function changeService(input){
  const before = services.find(s => s.code === selectedCode);
  const hadFiles = !!before && before.requirements.some(r => slotFiles[r.id]);
  selectedCode = input.value;
  slotFiles = {};            // the checklist belongs to the service
  slotProblems = {};
  $('serviceList').querySelectorAll('.rn-service').forEach(l => l.classList.toggle('selected', l.contains(input)));
  // Say so, instead of the files quietly disappearing.
  const after = services.find(s => s.code === selectedCode);
  $('filesRemoved').innerHTML = hadFiles ? noticeHtml('warning', 'Files removed',
    `You changed the service, so the files attached for ${escapeHtml(before.name)} were removed. Attach the documents for ${escapeHtml(after.name)}.`) : '';
  renderChecklist();
  refresh();
}

/** One upload slot: its label and tag, then either "Choose file" or the chosen file. */
function slotHtml(key, label, helpText, required, file, error){
  const tag = required ? '<span class="rn-tag rn-tag-required">Required</span>' : '<span class="rn-tag rn-tag-optional">Optional</span>';
  const errId = `slotErr-${key}`;
  const body = file
    ? `<img class="rn-file-icon" src="img/icons/document-green.svg" alt="" width="24" height="24">
       <span class="rn-file-name">${escapeHtml(file.name)}</span>
       <span class="rn-file-size">${formatSize(file.size)}</span>
       <button type="button" class="nav-btn btn-tertiary" data-remove="${key}" aria-label="Remove ${escapeHtml(file.name)}">Remove</button>`
    : `<label class="nav-btn btn-secondary">Choose file
         <input type="file" class="rn-hidden-input" data-slot="${key}" accept=".pdf,.doc,.docx,.jpg,.jpeg,.png"
                aria-label="Choose file: ${escapeHtml(label)}"${error ? ` aria-invalid="true" aria-describedby="${errId}"` : ''}></label>
       <span class="rn-note">No file chosen</span>`;
  return `<div class="rn-slot${file ? ' filled' : ''}${error ? ' invalid' : ''}">
      <div class="rn-slot-head">
        <span class="rn-slot-mark" aria-hidden="true">${file ? '<img src="img/icons/check-white-small.svg" alt="" width="16" height="16">' : ''}</span>
        <span class="rn-slot-label">${escapeHtml(label)}</span> ${tag}
      </div>
      ${helpText ? `<p class="rn-note">${escapeHtml(helpText)}</p>` : ''}
      <div class="rn-slot-row">${body}</div>
      ${error ? `<div class="err" id="${errId}">${escapeHtml(error)}</div>` : ''}
    </div>`;
}

function wireSlots(container, onPick, onRemove){
  container.querySelectorAll('input[data-slot]').forEach(input => {
    input.addEventListener('change', () => {
      const file = input.files[0];
      if(!file) return;
      const key = input.dataset.slot;
      const problem = fileProblem(file);
      if(problem){
        slotProblems[key] = problem;
        input.value = '';
        onPick(key, null);
        focusSlotControl(container, key);
        return;
      }
      delete slotProblems[key];
      onPick(key, file);
      focusSlotControl(container, key);
    });
  });
  container.querySelectorAll('button[data-remove]').forEach(btn => {
    btn.addEventListener('click', () => {
      const key = btn.dataset.remove;
      onRemove(key);
      focusSlotControl(container, key);
    });
  });
}
/** The slot was drawn again, so keyboard users continue from its button. */
function focusSlotControl(container, key){
  const control = container.querySelector(`[data-remove="${key}"], input[data-slot="${key}"]`);
  if(control) control.focus();
}

/** The error a slot shows now: the refused file, or (after Submit) that it is still needed. */
function slotError(key, label, required, file){
  if(slotProblems[key]) return slotProblems[key];
  return tried && required && !file ? attachText(label) : '';
}

function renderChecklist(){
  const svc = selectedService();
  const box = $('checklistSlots');
  if(reqType === 'followup'){
    // The server checks the checklist on new requests only; a follow-up adds files as needed.
    $('checklistBlock').hidden = true;
    box.innerHTML = '';
    return;
  }
  $('checklistBlock').hidden = false;
  const reqs = svc ? svc.requirements : [];
  $('checklistHeading').textContent = svc ? `Documents for ${svc.name}` : 'Documents';
  $('checklistEmpty').hidden = !!(svc && reqs.length);
  $('checklistEmpty').textContent = svc ? 'No specific documents are listed for this service. You may add files below.'
                                        : 'Choose a service first to see the documents it needs.';
  box.innerHTML = reqs.map(r => slotHtml(String(r.id), r.label, r.help_text, r.is_required, slotFiles[r.id],
    slotError(String(r.id), r.label, r.is_required, slotFiles[r.id]))).join('');
  wireSlots(box,
    (key, file) => { if(file){ slotFiles[key] = file; $('filesRemoved').textContent = ''; } renderChecklist(); refresh(); },
    key => { delete slotFiles[key]; renderChecklist(); refresh(); });
}

function renderEndorsementSlot(){
  const box = $('endorsementSlot');
  const label = 'Signed endorsement letter';
  box.innerHTML = slotHtml('endorsement', label, '', true, endorsementFile, slotError('endorsement', label, true, endorsementFile));
  wireSlots(box,
    (_key, file) => { if(file) endorsementFile = file; renderEndorsementSlot(); refresh(); },
    () => { endorsementFile = null; renderEndorsementSlot(); refresh(); });
}

function renderExtras(){
  $('extraList').innerHTML = extraFiles.map((f, i) => `
    <div class="rn-extra-row">
      <img class="rn-file-icon" src="img/icons/document-green.svg" alt="" width="24" height="24">
      <span class="rn-file-name">${escapeHtml(f.name)}</span>
      <span class="rn-file-size">${formatSize(f.size)}</span>
      <button type="button" class="nav-btn btn-tertiary" data-extra="${i}" aria-label="Remove ${escapeHtml(f.name)}">Remove</button>
    </div>`).join('');
  $('extraList').querySelectorAll('button[data-extra]').forEach(btn => {
    btn.addEventListener('click', () => {
      extraFiles.splice(Number(btn.dataset.extra), 1);
      renderExtras();
      refresh();
      $('extraInput').focus();
    });
  });
}

function addExtraFiles(fileList){
  const problems = [];
  Array.from(fileList).forEach(file => {
    const problem = fileProblem(file);
    if(problem){ problems.push(problem); return; }
    if(countFiles() >= MAX_FILES){ problems.push(`You can attach up to ${MAX_FILES} files. "${file.name}" was not added.`); return; }
    extraFiles.push(file);
  });
  $('fileError').innerHTML = problems.map(p => `<div class="err is-shown">${escapeHtml(p)}</div>`).join('');
  renderExtras();
  refresh();
}

/* ---------- what is done and what is still needed ---------- */
function activeSlotFiles(){
  const svc = selectedService();
  if(reqType === 'followup' || !svc) return [];
  return svc.requirements.filter(r => slotFiles[r.id]).map(r => ({file: slotFiles[r.id], requirementId: String(r.id)}));
}
function countFiles(){ return activeSlotFiles().length + extraFiles.length; }
function totalBytes(){
  return activeSlotFiles().reduce((n, x) => n + x.file.size, 0)
    + extraFiles.reduce((n, f) => n + f.size, 0) + (endorsementFile ? endorsementFile.size : 0);
}

/** Everything the client must do, in page order. Each item says where its field is. */
function checklistItems(){
  const items = [];
  const svc = selectedService();
  if(reqType === 'followup'){
    items.push({text: 'Choose the request you are following up', done: !!$('followupSelect').value, target: '#followupSelect'});
  }else if(services.length > 1){
    items.push({text: 'Choose a service', done: !!svc, target: '#serviceList input'});
  }
  items.push({text: 'Describe your concern', done: !!$('desc').value.trim(), target: '#desc'});
  if(endorsementNeeded){
    items.push({text: 'Attach the signed endorsement letter', done: !!endorsementFile, target: 'input[data-slot="endorsement"]', isFile: true});
  }
  if(reqType === 'new' && svc){
    svc.requirements.filter(r => r.is_required).forEach(r => {
      items.push({text: `Attach: ${r.label}`, done: !!slotFiles[r.id], target: `input[data-slot="${r.id}"]`, isFile: true});
    });
  }
  // These two only appear when they are a problem.
  if(countFiles() > MAX_FILES) items.push({text: `Remove some files (up to ${MAX_FILES})`, done: false, target: '#extraInput', isFile: true});
  if(totalBytes() > MAX_TOTAL_BYTES) items.push({text: 'Remove some files (50 MB in all)', done: false, target: '#extraInput', isFile: true});
  items.push({text: 'Tick the statement that your information is true and correct', done: $('attest').checked, target: '#attest'});
  return items;
}

/** "Step 1 of 4", and "· Done" with the green tick once that step is finished. */
function setStep(sectionId, number, total, done){
  const section = $(sectionId);
  section.querySelector('.rn-step').classList.toggle('is-done', done);
  section.querySelector('.rn-step-digit').textContent = number;
  section.querySelector('.rn-step-label').textContent = `Step ${number} of ${total}${done ? ' · Done' : ''}`;
}
function refreshSteps(items){
  const svc = selectedService();
  const filesAttached = countFiles() + (endorsementFile ? 1 : 0) > 0;
  const docsDone = filesAttached && items.filter(i => i.isFile).every(i => i.done);
  const steps = [
    ['stepDetails', !!$('desc').value.trim()],
    ['stepDocs', docsDone],
    ['stepConfirm', $('attest').checked]
  ];
  if(hasServiceStep()) steps.unshift(['stepService', !!svc]);
  steps.forEach(([id, done], i) => setStep(id, i + 1, steps.length, done));
}

/** "Before you can submit": the side box, and the same list above Submit on narrow screens. */
function refreshChecklist(items){
  const done = items.filter(i => i.done).length;
  const shown = JSON.stringify(items.map(i => [i.text, i.done]));
  if(shown === checklistShown) return;   // no change: a keyboard user's place in the list isn't lost
  checklistShown = shown;
  const rows = items.map(i => i.done
    ? `<li class="rn-check-item is-done"><img src="img/icons/check-done.svg" alt="" width="22" height="22"><span><span class="visually-hidden">Done: </span>${escapeHtml(i.text)}</span></li>`
    : `<li class="rn-check-item"><a href="#" data-target='${escapeHtml(i.target)}'><span class="rn-check-mark" aria-hidden="true"></span><span class="rn-check-text">${escapeHtml(i.text)}</span></a></li>`).join('');
  document.querySelectorAll('[data-checklist]').forEach(box => {
    box.innerHTML = `<h2 class="rn-h3">Before you can submit</h2>
      <p class="rn-note">${done} of ${items.length} done</p>
      <ul class="rn-check-list">${rows}</ul>`;
  });
}

/** The red box at the top of the form, after Submit was pressed with things missing. */
function refreshSummary(missing){
  const shown = tried ? missing.map(i => i.text).join('|') : '';
  if(shown === summaryShown) return;
  summaryShown = shown;
  if(!shown){ $('rnSummary').textContent = ''; return; }
  const title = `Fix ${missing.length} thing${missing.length === 1 ? '' : 's'} before you submit`;
  const links = missing.map(i => `<li><a href="#" data-target='${escapeHtml(i.target)}'><img src="img/icons/arrow-small-error.svg" alt="" width="16" height="16">${escapeHtml(i.text)}</a></li>`).join('');
  $('rnSummary').innerHTML = `<div class="notice notice-error">
    <img src="img/icons/error.svg" alt="" width="24" height="24">
    <div><p class="notice-title">${escapeHtml(title)}</p><ul class="pf-summary-list">${links}</ul></div>
  </div>`;
}

/** Keeps the steps, the lists and the errors in step with the form. */
function refresh(){
  const items = checklistItems();
  const missing = items.filter(i => !i.done);
  refreshSteps(items);
  refreshChecklist(items);
  refreshSummary(missing);
  if(tried){
    setInvalid('f-desc', !$('desc').value.trim());
    setInvalid('f-attest', !$('attest').checked);
    setInvalid('f-followup', reqType === 'followup' && !$('followupSelect').value);
    $('f-service').classList.toggle('invalid', reqType === 'new' && services.length > 1 && !selectedService());
  }
  if(!submitting){
    const count = missing.length;
    const status = count ? `${count} thing${count === 1 ? '' : 's'} still needed before you can submit.` : 'All set. You can submit now.';
    if($('rnStatus').textContent !== status) $('rnStatus').textContent = status;
    $('rnStatus').classList.toggle('is-ready', !count);
  }
  const n = countFiles() + (endorsementFile ? 1 : 0);
  $('fileTotal').hidden = !n;
  $('fileTotal').textContent = n ? `${n} file${n === 1 ? '' : 's'} attached · ${formatSize(totalBytes())} of 50 MB` : '';
  $('descCount').textContent = $('desc').value.length;
}

function setReqType(value){
  reqType = value;
  $('f-followup').hidden = value !== 'followup';
  $('f-service').hidden = value === 'followup';
  refreshServiceHint();
  setInvalid('f-followup', false);
  renderChecklist();
  refresh();
}

/** A link in either list: go to that field. */
function goToField(event){
  const link = event.target.closest('a[data-target]');
  if(!link) return;
  event.preventDefault();
  const field = document.querySelector(link.dataset.target);
  if(!field) return;
  field.focus({preventScroll: true});
  field.scrollIntoView({block: 'center'});
}

/* ---------- endorsement template ---------- */
function downloadTemplate(){
  if(!endorsementTemplate) return;
  const blob = new Blob([endorsementTemplate.replace(/\r?\n/g, '\r\n')], {type: 'text/plain;charset=utf-8'});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'ESSU-Legal-Office-endorsement-letter-template.txt';
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/* ---------- submit ---------- */
function setSubmitting(on){
  submitting = on;
  $('submitBtn').disabled = on;
  $('submitLabel').textContent = on ? 'Submitting…' : 'Submit';
  $('submitArrow').hidden = on;
  $('cancelBtn').classList.toggle('disabled', on);
  if(on){
    $('cancelBtn').setAttribute('aria-disabled', 'true');
    $('cancelBtn').setAttribute('tabindex', '-1');
    $('rnStatus').classList.remove('is-ready');
    $('rnStatus').textContent = 'Sending your request and files. Keep this page open.';
  }else{
    $('cancelBtn').removeAttribute('aria-disabled');
    $('cancelBtn').removeAttribute('tabindex');
  }
}

async function submit(){
  if(submitting) return;
  const missing = checklistItems().filter(i => !i.done);
  if(missing.length){
    tried = true;
    summaryShown = null;   // told again, even when the same things are missing
    renderChecklist();
    if(endorsementNeeded) renderEndorsementSlot();
    refresh();
    $('rnSummary').focus({preventScroll: true});
    $('rnSummary').scrollIntoView({block: 'center'});
    return;
  }
  setSubmitting(true);
  $('submitError').textContent = '';

  const svc = selectedService();
  const form = new FormData();
  form.append('service_type_code', svc ? svc.code : '');
  form.append('description', $('desc').value.trim());
  form.append('request_type', reqType);
  if(reqType === 'followup') form.append('followup_tracking_id', $('followupSelect').value);
  form.append('attestation', 'true');
  // Each file goes with the checklist item it satisfies ("" = other file), in the same order.
  activeSlotFiles().forEach(x => { form.append('files', x.file, x.file.name); form.append('requirement_ids', x.requirementId); });
  extraFiles.forEach(f => { form.append('files', f, f.name); form.append('requirement_ids', ''); });
  if(endorsementNeeded && endorsementFile) form.append('endorsement_letter', endorsementFile, endorsementFile.name);

  // One attempt only: the server refuses an identical request sent twice, and
  // the button stays disabled while this one is on its way.
  const res = await api('/portal/requests', {method: 'POST', body: form});
  setSubmitting(false);

  if(res.ok){ showDone(res.body, svc); return; }
  $('submitError').innerHTML = sendErrorHtml(res);
  refresh();
}

function sendErrorHtml(res){
  if(res.status === 401){
    return noticeHtml('error', 'You were signed out', 'You were signed out because the page was left open too long. Your answers are still here: '
      + '<a href="portal.html" target="_blank" rel="noopener">sign in again in a new tab</a>, then come back and press Submit.');
  }
  if(res.status === 413){
    return noticeHtml('error', 'The files are too large', 'The files are too large to send together. Please remove some files and try again.');
  }
  if(res.status === 0){
    return noticeHtml('error', 'Could not reach the Legal Office server.', 'Please check your connection and try again.');
  }
  return noticeHtml('error', 'Could not submit your request', escapeHtml(errorText(res.body, 'Please try again.')));
}

/* ---------- done ---------- */
function nextStepsHtml(svc){
  if(DOOR === 'complaint'){
    return COMPLAINT_NEXT.map(([title, text], i) => `<li><span class="rn-next-num" aria-hidden="true">${i + 1}</span>
      <div><p class="rn-next-title">${escapeHtml(title)}</p><p class="rn-note">${escapeHtml(text)}</p></div></li>`).join('');
  }
  const steps = ['The Legal Office receives your request. Its status changes from Submitted to Received.'];
  // Only when we know this service: a follow-up may belong to one that is no longer offered.
  const known = services.find(s => s.code === (svc && svc.code));
  if(known){
    steps.push(known.default_processing_days
      ? `${known.name} is usually acted on within ${known.default_processing_days} working days, counted from the next working day.`
      : `${known.name} has no set period: it depends on the request.`);
  }
  steps.push('We email you when the status changes or when the Legal Office needs something from you.');
  return steps.map((text, i) => `<li><span class="rn-next-num" aria-hidden="true">${i + 1}</span><span>${escapeHtml(text)}</span></li>`).join('');
}

function showDone(body, svc){
  setHero({
    symbol: TEXT.doneSymbol, kicker: TEXT.doneKicker, title: TEXT.done,
    lede: 'We also sent the Request ID to your email address. You can follow this request any time from My Requests.'
  });
  document.title = `${TEXT.doneKicker} — The Legal Office, ESSU`;
  $('doneId').textContent = body.tracking_id;
  $('doneViewBtn').href = `request.html?id=${encodeURIComponent(body.tracking_id)}`;
  $('doneVerify').hidden = !endorsementNeeded;
  $('nextSteps').innerHTML = nextStepsHtml(svc);
  $('nextNote').hidden = DOOR !== 'complaint';
  $('nextNote').textContent = DOOR === 'complaint' ? 'Complaints have no set period: it depends on the case. We email you whenever the status changes.' : '';
  // Copying needs a secure page (https or localhost); without it the ID can still be selected.
  $('copyBtn').hidden = !navigator.clipboard;
  show('doneView');
  $('rnTitle').focus({preventScroll: true});
}

async function copyRequestId(){
  try{
    await navigator.clipboard.writeText($('doneId').textContent);
    $('copyLabel').textContent = 'Copied';
  }catch(err){
    $('copyLabel').textContent = 'Could not copy. Select the ID instead.';
  }
  setTimeout(() => { $('copyLabel').textContent = 'Copy Request ID'; }, 3000);
}

/* ---------- wiring ---------- */
document.querySelectorAll('input[name="reqType"]').forEach(r => r.addEventListener('change', () => setReqType(r.value)));
$('followupSelect').addEventListener('change', refresh);
$('desc').addEventListener('input', refresh);
$('attest').addEventListener('change', refresh);
$('extraInput').addEventListener('change', () => { addExtraFiles($('extraInput').files); $('extraInput').value = ''; });
$('templateBtn').addEventListener('click', downloadTemplate);
$('submitBtn').addEventListener('click', submit);
$('copyBtn').addEventListener('click', copyRequestId);
$('blockedRetry').addEventListener('click', () => window.location.reload());
$('cancelBtn').addEventListener('click', e => { if(submitting) e.preventDefault(); });
$('rnSummary').addEventListener('click', goToField);
document.querySelectorAll('[data-checklist]').forEach(box => box.addEventListener('click', goToField));
// Leaving with answers typed, or while the files are sending: ask first (the browser shows its own message).
window.addEventListener('beforeunload', e => {
  const typed = !$('formView').hidden && ($('desc').value.trim() || countFiles() || endorsementFile);
  if(typed || submitting){ e.preventDefault(); e.returnValue = ''; }
});

init();
