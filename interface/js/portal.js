/* My Requests — the door, registration, and the list of
   the client's own requests.

   1. Email address             -> POST /access/contact/send-code
           (after the "I'm not a robot" check: GET /access/bot-check/challenge)
      (email only: no SMS sender; clients without
      email are served by the Legal Office in person, as walk-ins)
   2. 6-digit code              -> POST /portal/verify-code
        "signed_in"  -> My Requests (or "complete your details" for older records)
        "new_client" -> registration -> POST /portal/register
   3. My Requests               -> GET  /portal/me
   4. Edit details              -> PATCH /portal/me (only while not yet verified)
   5. Sign out                  -> POST /portal/logout

   The browser never sees a password or token: the server keeps the session
   in an HttpOnly cookie (30 min idle / 2 h max). */

const RESEND_SECONDS = 60;
const OFFICE_EMAIL_LINK = '<a href="mailto:legaloffice@essu.edu.ph">legaloffice@essu.edu.ph</a>';
/* One message at a time on My Requests. New wording, for Legal Office review:
   the Verified line and the email address on the last two. */
const VERIFY_NOTICE = {
  unverified_essu: {kind: 'info', title: 'Not yet verified.', message: 'To request a legal service, attach the signed endorsement letter from your office head (or your dean, for students) under Documents on your first request. You only send it once. Complaints and referrals don\'t need the letter.'},
  unverified_ext:  {kind: 'info', title: 'Not yet verified.', message: 'The Legal Office confirms your details while it handles your complaint or referral.'},
  pending:  {kind: 'draft', title: 'Being verified.', message: 'The Legal Office is checking your endorsement letter.'},
  verified: {kind: 'success', title: 'Verified.', message: 'You don’t need an endorsement letter again, and there is no limit on open requests.'},
  declined: {kind: 'error', title: 'The Legal Office could not verify your details.', message: `Please contact the Legal Office at ${OFFICE_EMAIL_LINK}.`},
  revoked:  {kind: 'error', title: 'Your verification was withdrawn.', message: `Please contact the Legal Office at ${OFFICE_EMAIL_LINK}.`}
};
/* The colour of each status: always written out too. */
const STATUS_TONE = {
  'Submitted': 'submitted', 'Received': 'submitted',
  'For Validation': 'review', 'Under Review': 'review', 'Clarificatory Meeting': 'review', 'Processing': 'review',
  'Waiting for Client': 'waiting', 'Returned for Revision': 'waiting',
  'For Attorney Review': 'attorney',
  'Approved': 'completed', 'Completed': 'completed',
  'Cancelled': 'cancelled', 'Rejected': 'cancelled'
};
const ESSU = ['essu_office', 'essu_employee', 'essu_student'];

let doorContact = '';
let resendLeft = 0;
let resendTimer = null;
let doorBusy = false;       // a code is being sent or checked: ignore another press
let me = null;              // last GET /portal/me
let profileMode = 'register';   // 'register' | 'complete' | 'edit'
let profileBusy = false;    // the details are being saved: ignore another press
let pageOpening = true;     // the first check for a signed-in client: focus stays where the browser put it
let officesLoaded = false;
let officeCount = 0;

/* ---------- small helpers ---------- */
function escapeHtml(value){
  return String(value ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
function $(id){ return document.getElementById(id); }
function show(viewId){
  ['doorView', 'profileView', 'homeLoadingView', 'homeView'].forEach(v => { $(v).style.display = v === viewId ? 'block' : 'none'; });
  window.scrollTo({top: 0});
}
/** A new view: the keyboard and screen readers continue from its title. */
function enterView(viewId, titleId){
  show(viewId);
  if(!pageOpening) $(titleId).focus({preventScroll: true});
}
function setInvalid(fieldId, bad){
  const field = $(fieldId);
  field.classList.toggle('invalid', !!bad);
  // A screen reader also reads the error when the field gets focus.
  const input = field.querySelector('[data-hint]');
  const err = field.querySelector('.err');
  if(input && err){
    input.setAttribute('aria-invalid', bad ? 'true' : 'false');
    const ids = [input.dataset.hint, bad ? err.id : ''].filter(Boolean).join(' ');
    if(ids) input.setAttribute('aria-describedby', ids);
    else input.removeAttribute('aria-describedby');
  }
}
/** The server's error text: a string, or the first message of a 422 list. */
function errorText(body, fallback){
  if(body && typeof body.detail === 'string') return body.detail;
  if(body && Array.isArray(body.detail) && body.detail[0] && body.detail[0].msg){
    return String(body.detail[0].msg).replace(/^Value error, /, '');
  }
  return fallback;
}
async function api(path, options = {}){
  const opts = {...options};
  if(opts.json !== undefined){
    opts.headers = {'Content-Type': 'application/json', ...(opts.headers || {})};
    opts.body = JSON.stringify(opts.json);
    delete opts.json;
  }
  let response;
  try{
    response = await fetch(`${API_BASE_URL}${path}`, opts);
  }catch(err){
    return {ok: false, status: 0, body: {detail: 'Could not reach the Legal Office server. Please check your connection and try again.'}};
  }
  const body = await response.json().catch(() => ({}));
  return {ok: response.ok, status: response.status, body};
}
function formatDate(iso){
  if(!iso) return '—';
  const d = new Date(/Z|[+-]\d\d:\d\d$/.test(iso) ? iso : iso + 'Z');
  return d.toLocaleDateString('en-PH', {year: 'numeric', month: 'short', day: 'numeric'});
}
function validContact(v){
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v);   // email only (no SMS)
}

  /* ---------- bot check ----------
     Before a code is emailed, the browser solves a small "I'm not a robot"
     puzzle made by LEXIS (ALTCHA, our own copy in js/vendor/altcha; about a
     second). One person never notices; a script trying to make LEXIS send
     thousands of emails pays for every one. Each answer works once, so a
     new one is solved for every code (and every Resend). */
  const BOT_CHECK_TIMEOUT_MS = 30000;
  let botSetup = null;

  function withTimeout(promise, ms){
    return Promise.race([promise, new Promise(resolve => setTimeout(() => resolve(null), ms))]);
  }

  function setupBotCheck(){
    if(!botSetup){
      botSetup = customElements.whenDefined('altcha-widget').then(async () => {
        // Our own copy of the puzzle worker, never a CDN.
        globalThis.$altcha.algorithms.set('PBKDF2/SHA-256', () => new Worker('js/vendor/altcha/pbkdf2.js'));
        await $('botCheck').configure({
          challenge: `${API_BASE_URL}/access/bot-check/challenge`,
          humanInteractionSignature: false,   // record nothing about how the visitor types or moves
          timeout: BOT_CHECK_TIMEOUT_MS,
        });
        return true;
      });
    }
    return botSetup;
  }

  /* The solved puzzle for ONE code request, or null if it couldn't finish
     (script didn't load, no connection, too slow). Never waits forever. */
  async function getBotPayload(){
    try{
      if(!await withTimeout(setupBotCheck(), 10000)) return null;
      const widget = $('botCheck');
      widget.reset();
      const result = await withTimeout(widget.verify(), BOT_CHECK_TIMEOUT_MS + 2000);
      return result && result.payload ? result.payload : null;
    }catch(err){
      return null;
    }
  }

/* ---------- 1. the door ---------- */
const CHECKING_TEXT = 'Checking you\'re not a robot…';
const ROBOT_LINE = 'The robot check runs by itself when you press Send code.';
const NO_CONNECTION_TITLE = 'No connection';

/** A message box in the sign-in card. kind: 'error' or 'warning'.
    doorTopNotice is for someone sent back to the door; doorNotice sits under
    the button they just pressed. */
function showDoorNotice(slotId, kind, title, message){
  $(slotId).innerHTML = noticeHtml(kind, title, escapeHtml(message));
}
/** messageHtml must already be safe: escaped, or our own fixed text. */
function noticeHtml(kind, title, messageHtml){
  return `<div class="notice notice-${kind}">
    <img src="img/icons/${kind}.svg" alt="" width="24" height="24">
    <div><p class="notice-title">${escapeHtml(title)}</p><p class="notice-message">${messageHtml}</p></div>
  </div>`;
}
function clearDoorNotices(){
  $('doorTopNotice').textContent = '';
  $('doorNotice').textContent = '';
}
function setDoorFieldError(fieldId, errId, inputId, message){
  $(errId).textContent = message;
  setInvalid(fieldId, true);
  $(inputId).focus();
}

function showDoorStep(step){
  const isCode = step === 'code';
  $('doorSkeleton').hidden = true;
  $('doorEmailStep').hidden = isCode;
  $('doorCodeStep').hidden = !isCode;
  $('doorLinks').hidden = !isCode;
  $('doorStepText').textContent = isCode ? 'Step 2 of 2 · The code from your email' : 'Step 1 of 2 · Your email address';
  $('doorProgress2').classList.toggle('is-done', isCode);
}

/** While the robot check runs and the code is sent. */
function setSending(on, isResend){
  doorBusy = on;
  if(isResend){
    $('doorResendBtn').textContent = on ? 'Sending code…' : 'Resend code';
    $('doorResendBtn').disabled = on;
    return;
  }
  const hadFocus = document.activeElement === $('doorSendBtn');
  $('f-doorContact').classList.toggle('is-busy', on);
  $('doorContact').readOnly = on;
  $('doorSendBtn').disabled = on;
  $('doorSendLabel').textContent = on ? 'Sending code…' : 'Send code';
  $('doorRobot').classList.toggle('is-checking', on);
  $('doorRobotText').textContent = on ? CHECKING_TEXT : ROBOT_LINE;
  $('doorStatus').textContent = on ? CHECKING_TEXT : '';
  if(!on && hadFocus) $('doorSendBtn').focus();
}

async function sendCode(isResend){
  if(doorBusy) return;
  const value = isResend ? doorContact : $('doorContact').value.trim();
  clearDoorNotices();
  if(!validContact(value)){
    setDoorFieldError('f-doorContact', 'doorContactErr', 'doorContact', 'Enter a valid email address, like name@example.com.');
    return;
  }
  setInvalid('f-doorContact', false);
  setSending(true, isResend);
  const altcha = await getBotPayload();
  if(!altcha){
    setSending(false, isResend);
    if(isResend) updateResendLabel();
    showDoorNotice('doorNotice', 'error', 'Code not sent', 'The "I\'m not a robot" check could not finish. Please try again, or reload the page.');
    return;
  }
  const res = await api('/access/contact/send-code', {method: 'POST', json: {contact: value, altcha}});
  setSending(false, isResend);
  if(!res.ok){
    // A resend that came too soon waits out the same minute before the next try.
    if(res.status === 429 && isResend) startResendCountdown();
    else if(isResend) updateResendLabel();
    if(res.status === 429){
      const text = errorText(res.body, 'Please wait a minute before requesting another code.');
      showDoorNotice('doorNotice', 'warning', /a minute/.test(text) ? 'Please wait a minute' : 'Code not sent', text);
    }else if(res.status === 0){
      showDoorNotice('doorNotice', 'error', NO_CONNECTION_TITLE, errorText(res.body, ''));
    }else{
      showDoorNotice('doorNotice', 'error', 'Code not sent', errorText(res.body, 'Could not send the code. Please try again.'));
    }
    return;
  }
  doorContact = value;
  showDoorStep('code');
  $('doorCodeHint').textContent = `We sent a code to ${value}. It expires in 10 minutes.`;
  $('doorCode').value = '';
  setInvalid('f-doorCode', false);
  $('doorCode').focus();
  $('doorStatus').textContent = isResend ? 'We sent a new code.' : '';
  startResendCountdown();
}

function startResendCountdown(){
  resendLeft = RESEND_SECONDS;
  clearInterval(resendTimer);
  updateResendLabel();
  resendTimer = setInterval(() => {
    resendLeft -= 1;
    updateResendLabel();
    if(resendLeft <= 0) clearInterval(resendTimer);
  }, 1000);
}
function updateResendLabel(){
  const btn = $('doorResendBtn');
  btn.disabled = resendLeft > 0;
  btn.textContent = resendLeft > 0 ? `Resend code (${resendLeft}s)` : 'Resend code';
}

/** Back to step 1, with nothing typed from before. */
function resetDoor(){
  clearInterval(resendTimer);
  doorContact = '';
  clearDoorNotices();
  setInvalid('f-doorContact', false);
  setInvalid('f-doorCode', false);
  showDoorStep('email');
}

function changeContact(){
  if(doorBusy) return;
  resetDoor();
  $('doorContact').focus();
}

async function verifyCode(){
  if(doorBusy) return;
  const code = $('doorCode').value.trim();
  clearDoorNotices();
  if(!/^\d{6}$/.test(code)){
    setDoorFieldError('f-doorCode', 'doorCodeErr', 'doorCode', 'Enter the 6-digit code.');
    return;
  }
  setInvalid('f-doorCode', false);
  doorBusy = true;
  $('doorVerifyBtn').disabled = true;
  const res = await api('/portal/verify-code', {method: 'POST', json: {contact: doorContact, code}});
  doorBusy = false;
  $('doorVerifyBtn').disabled = false;
  if(res.status === 429){
    showDoorNotice('doorNotice', 'error', 'Please wait a minute', 'Too many attempts. Please wait a minute and try again.');
    return;
  }
  if(res.status === 0){
    showDoorNotice('doorNotice', 'error', NO_CONNECTION_TITLE, errorText(res.body, ''));
    return;
  }
  if(res.status === 400 || res.status === 422){
    // The server gives one answer for a wrong, expired or locked code.
    setDoorFieldError('f-doorCode', 'doorCodeErr', 'doorCode', 'That code doesn’t match. Check the 6 digits and try again. Codes expire after 10 minutes. If it still doesn’t work, press Resend code.');
    return;
  }
  if(!res.ok){
    showDoorNotice('doorNotice', 'error', 'Something went wrong', errorText(res.body, 'Could not check the code. Please try again.'));
    return;
  }
  clearInterval(resendTimer);
  if(res.body.status === 'new_client'){
    await openProfile('register');
  }else{
    show('homeLoadingView');
    await loadMe();
  }
}

/* ---------- 2. registration / details ---------- */
async function loadOffices(){
  if(officesLoaded) return;
  const res = await api('/portal/offices');
  if(!res.ok || !Array.isArray(res.body)) return;
  const select = $('pfOffice');
  const othersOption = select.querySelector('option[value="other"]');
  officeCount = res.body.length;
  res.body.forEach(o => {
    const opt = document.createElement('option');
    opt.value = String(o.id);
    opt.textContent = o.name;
    select.insertBefore(opt, othersOption);   // the list first, "Others" last
  });
  officesLoaded = true;
}

function updateCategoryFields(){
  const cat = $('pfCategory').value;
  const essu = ESSU.includes(cat);
  $('f-office').hidden = !essu;
  $('f-organization').hidden = !cat || essu;
  $('pfMoreNote').hidden = !!cat;
  $('orgOptional').hidden = cat === 'ext_government';
  $('pfPositionLabel').textContent = cat === 'essu_student' ? 'Year and program' : 'Position';
  const hints = {
    essu_office: 'For office or department heads and key officials requesting for their office.',
    essu_employee: 'For ESSU personnel. Your first request needs a signed endorsement from your office head.',
    essu_student: 'Students may request Legal Research. Your first request needs a signed endorsement from your dean.',
    ext_government: 'Persons and agencies outside ESSU may file a complaint or referral.',
    ext_private: 'Persons outside ESSU may file a complaint or referral.'
  };
  $('pfCategoryHint').textContent = hints[cat] || '';
  // An error on a field that just went away, or that became optional, no longer applies.
  if(!essu){ setInvalid('f-office', false); }
  if(cat !== 'ext_government'){ setInvalid('f-organization', false); }
  toggleOfficeOther();
  refreshProfileSummary();
}

/** "Others (please specify)" shows the text box. While the Head Attorney's
    list is still empty, "Others" is the only choice and is picked already. */
function toggleOfficeOther(){
  const essu = ESSU.includes($('pfCategory').value);
  if(essu && officeCount === 0 && !$('pfOffice').value) $('pfOffice').value = 'other';
  const typing = essu && $('pfOffice').value === 'other';
  $('f-officeOther').hidden = !typing;
  if(!typing) setInvalid('f-officeOther', false);
}

function toggleNoMiddleName(){
  const none = $('pfNoMiddleName').checked;
  $('pfMiddleName').disabled = none;
  if(none) $('pfMiddleName').value = '';
}

/* The errors at the top of the form: one link per field, in the form's order.
   Each link's words match the error under its field. */
const PROFILE_ERRORS = [
  ['f-lastName', 'pfLastName', 'Enter your last name'],
  ['f-firstName', 'pfFirstName', 'Enter your first name'],
  ['f-middleName', 'pfMiddleName', 'Enter your middle name, or tick “No middle name”'],
  ['f-category', 'pfCategory', 'Choose how you are requesting'],
  ['f-office', 'pfOffice', 'Choose your office, or choose “Others” and type it'],
  ['f-officeOther', 'pfOfficeOther', 'Type your office or college'],
  ['f-organization', 'pfOrganization', 'Enter the name of your agency'],
  ['f-consent', 'pfConsent', 'Please give your consent to continue']
];
let summaryOpen = false;     // only after Continue was pressed
let summaryCount = 0;

function refreshProfileSummary(){
  if(!summaryOpen) return;
  const errors = PROFILE_ERRORS.filter(([field]) => $(field).classList.contains('invalid'));
  if(errors.length === summaryCount) return;   // no change: a screen reader isn't told again
  summaryCount = errors.length;
  if(!errors.length){
    summaryOpen = false;
    $('profileSummary').textContent = '';
    return;
  }
  const title = `Fix ${errors.length} thing${errors.length === 1 ? '' : 's'} before you continue`;
  const links = errors.map(([, input, text]) =>
    `<li><a href="#${input}" data-focus="${input}"><img src="img/icons/arrow-small-error.svg" alt="" width="16" height="16">${escapeHtml(text)}</a></li>`).join('');
  $('profileSummary').innerHTML = `<div class="notice notice-error">
    <img src="img/icons/error.svg" alt="" width="24" height="24">
    <div><p class="notice-title">${escapeHtml(title)}</p><ul class="pf-summary-list">${links}</ul></div>
  </div>`;
}

function setProfileNote(){
  $('pfActionNote').textContent = profileMode === 'register'
    ? 'You can change these details later, until the Legal Office starts verifying them.'
    : '';
}

async function openProfile(mode){
  profileMode = mode;
  await loadOffices();
  const texts = {
    register: ['One-time setup', 'Tell us who you are', 'We need a few details once. The Legal Office verifies them with your first request.'],
    complete: ['Your details', 'Complete your details', 'We need a few more details before you can submit a new request.'],
    edit: ['Your details', 'Edit my details', 'You can change your details until the Legal Office starts verifying them.']
  };
  const isEdit = mode === 'edit';
  $('profileKicker').textContent = texts[mode][0];
  $('profileTitle').textContent = texts[mode][1];
  $('profileLede').textContent = texts[mode][2];
  // Say whose details these are, so someone who sees this form after
  // coming back to the page knows which email is signed in.
  $('profileEmail').textContent = mode === 'register' ? doorContact : ((me && me.contact) || '');
  $('profileWho').hidden = isEdit;
  $('consentBlock').hidden = mode !== 'register';
  $('pfCancelBtn').hidden = !isEdit;
  $('pfSaveLabel').textContent = isEdit ? 'Save' : 'Continue';
  $('pfSaveArrow').hidden = isEdit;
  setProfileNote();
  // "You only do this once" would be wrong while editing, so only the note and the email stay.
  $('pfAsideTitle').textContent = isEdit ? 'Good to know' : 'What happens next';
  $('pfSteps').hidden = isEdit;

  const src = mode === 'register' ? null : me;
  // Older records have only a full name: the client fills in the parts once.
  $('pfLastName').value = src && src.last_name ? src.last_name : '';
  $('pfFirstName').value = src && src.first_name ? src.first_name : '';
  $('pfMiddleName').value = src && src.middle_name ? src.middle_name : '';
  $('pfNoMiddleName').checked = !!(src && src.last_name && !src.middle_name);
  $('pfNameExt').value = src && src.name_extension ? src.name_extension : '';
  $('pfCategory').value = src && src.client_category ? src.client_category : '';
  const typedOffice = !!(src && !src.office_id && src.office_name && ESSU.includes(src.client_category));
  $('pfOffice').value = src && src.office_id ? String(src.office_id) : (typedOffice ? 'other' : '');
  $('pfOfficeOther').value = typedOffice ? src.office_name : '';
  $('pfOrganization').value = src && src.organization ? src.organization : '';
  $('pfPosition').value = src && src.position ? src.position : '';
  $('pfConsent').checked = false;
  PROFILE_ERRORS.forEach(([field]) => setInvalid(field, false));
  summaryOpen = false;
  summaryCount = 0;
  $('profileSummary').textContent = '';
  $('profileError').textContent = '';
  updateCategoryFields();
  toggleNoMiddleName();
  enterView('profileView', 'profileTitle');
}

/** While the details are sent: the button says so, and everything typed stays. */
function setSaving(on){
  profileBusy = on;
  $('pfSaveBtn').disabled = on;
  $('pfCancelBtn').disabled = on;
  $('pfSaveLabel').textContent = on ? 'Saving…' : (profileMode === 'edit' ? 'Save' : 'Continue');
  $('pfSaveArrow').hidden = on || profileMode === 'edit';
  if(on) $('pfActionNote').textContent = 'Saving your details…';
  else setProfileNote();
}

async function saveProfile(){
  if(profileBusy) return;
  const cat = $('pfCategory').value;
  const essu = ESSU.includes(cat);
  const officeValue = $('pfOffice').value;
  const notListed = officeValue === 'other';
  const noMiddle = $('pfNoMiddleName').checked;
  const data = {
    last_name: $('pfLastName').value.trim(),
    first_name: $('pfFirstName').value.trim(),
    middle_name: noMiddle ? null : $('pfMiddleName').value.trim() || null,
    no_middle_name: noMiddle,
    name_extension: $('pfNameExt').value.trim() || null,
    client_category: cat,
    office_id: essu && !notListed && officeValue ? Number(officeValue) : null,
    office_other: essu && notListed ? $('pfOfficeOther').value.trim() || null : null,
    organization: !essu ? $('pfOrganization').value.trim() || null : null,
    position: $('pfPosition').value.trim() || null
  };
  const bad = {
    'f-lastName': !data.last_name,
    'f-firstName': !data.first_name,
    'f-middleName': !noMiddle && !data.middle_name,
    'f-category': !cat,
    'f-office': essu && !officeValue,
    'f-officeOther': essu && notListed && !data.office_other,
    'f-organization': cat === 'ext_government' && !data.organization,
    'f-consent': profileMode === 'register' && !$('pfConsent').checked
  };
  Object.entries(bad).forEach(([f, isBad]) => setInvalid(f, isBad));
  $('profileError').textContent = '';
  summaryOpen = true;
  summaryCount = 0;
  refreshProfileSummary();
  if(Object.values(bad).some(Boolean)){
    $('profileSummary').focus();
    return;
  }

  setSaving(true);
  const res = profileMode === 'register'
    ? await api('/portal/register', {method: 'POST', json: {...data, privacy_consent: true}})
    : await api('/portal/me', {method: 'PATCH', json: data});
  setSaving(false);
  if(res.status === 401){
    // The 30 minutes to register (or the session) ran out.
    backToDoor('warning', 'You were signed out', 'Your verification has expired. Please enter your email address to receive a new code.');
    return;
  }
  if(res.status === 0){
    showDoorNotice('profileError', 'error', NO_CONNECTION_TITLE, errorText(res.body, ''));
    return;
  }
  if(!res.ok){
    showDoorNotice('profileError', 'error', 'Your details were not saved', errorText(res.body, 'Could not save your details. Please try again.'));
    return;
  }
  renderHome(res.body);
}

/* ---------- 3. My Requests ---------- */
async function loadMe(){
  const res = await api('/portal/me');
  if(res.status === 401){ backToDoor(); return; }
  if(!res.ok){ backToDoor('error', 'Something went wrong', 'Could not load your requests. Please try again.'); return; }
  if(res.body.needs_profile){
    me = res.body;
    await openProfile('complete');
    return;
  }
  renderHome(res.body);
}

function verifyNotice(m){
  if(m.verification_status === 'unverified') return ESSU.includes(m.client_category) ? VERIFY_NOTICE.unverified_essu : VERIFY_NOTICE.unverified_ext;
  return VERIFY_NOTICE[m.verification_status] || null;
}

function statusPill(status){
  const name = status || 'Submitted';
  return `<span class="status-pill status-pill-${STATUS_TONE[name] || 'review'}">${escapeHtml(name)}</span>`;
}

/** One table on a computer, one card per request on a phone (same rows, css/base.css). */
function requestTable(requests, label){
  const rows = requests.map(r => `
    <div class="my-row${r.is_closed ? ' is-closed' : ''}" role="row">
      <div class="my-cell-id" role="cell"><a class="my-row-link" href="request.html?id=${encodeURIComponent(r.tracking_id)}">${escapeHtml(r.tracking_id)}</a></div>
      <div class="my-cell-service" role="cell">${escapeHtml(r.service_type_name || '—')}</div>
      <div class="my-cell-status" role="cell">${statusPill(r.current_status)}</div>
      <div class="my-cell-date" role="cell"><span class="my-cell-label">Submitted on</span>${escapeHtml(formatDate(r.submitted_at))}</div>
      <div class="my-cell-open" role="cell"><img src="img/icons/arrow-right-muted.svg" alt="" width="20" height="20"></div>
    </div>`).join('');
  return `<div class="my-table" role="table" aria-label="${escapeHtml(label)}">
    <div class="my-row my-row-head" role="row">
      <div role="columnheader">Request ID</div><div role="columnheader">Service</div>
      <div role="columnheader">Status</div><div role="columnheader">Submitted</div>
      <div role="columnheader"><span class="visually-hidden">Open</span></div>
    </div>${rows}
  </div>`;
}

function renderRequests(requests){
  const list = $('requestList');
  const total = requests.length;
  $('requestCount').textContent = total ? `${total} request${total === 1 ? '' : 's'}, newest first` : '';
  if(!total){
    list.innerHTML = `<div class="my-card my-empty">
      <span class="my-empty-icon"><img src="img/icons/document-large.svg" alt="" width="32" height="32"></span>
      <p>You have no requests yet. Use the buttons above to submit your first one.</p>
    </div>`;
    return;
  }
  const open = requests.filter(r => !r.is_closed);
  const closed = requests.filter(r => r.is_closed);
  // The two groups only have names when there are both.
  if(!open.length || !closed.length){
    list.innerHTML = requestTable(requests, 'Your requests');
    return;
  }
  list.innerHTML = `
    <h3 class="my-group-title">Open · ${open.length}</h3>${requestTable(open, 'Open requests')}
    <h3 class="my-group-title">Closed · ${closed.length}</h3>${requestTable(closed, 'Closed requests')}`;
}

function renderHome(m){
  me = m;
  $('meName').textContent = m.full_name;
  $('meMeta').textContent = [m.client_category_label, m.office_name || m.organization, m.position].filter(Boolean).join(' · ');
  $('meEmail').textContent = m.contact;
  $('editProfileBtn').hidden = !m.can_edit_profile;
  $('meLocked').hidden = m.verification_status !== 'pending';
  const notice = verifyNotice(m);
  $('verifyBox').innerHTML = notice ? noticeHtml(notice.kind, notice.title, notice.message) : '';

  // Outside ESSU, only complaints and referrals are offered (OOM), so that button becomes the main one.
  const essu = ESSU.includes(m.client_category);
  $('newRequestBtn').hidden = !essu;
  $('newComplaintBtn').classList.toggle('btn-primary', !essu);
  $('newComplaintBtn').classList.toggle('btn-secondary', essu);
  $('startIntro').textContent = (essu ? 'Choose what you need.' : 'Persons outside ESSU may file a complaint or referral.') +
    ' You can follow it on this page after you send it.';

  const blocked = !m.can_submit_new;
  ['newRequestBtn', 'newComplaintBtn'].forEach(id => {
    $(id).classList.toggle('disabled', blocked);
    if(blocked) $(id).setAttribute('aria-disabled', 'true');
    else $(id).removeAttribute('aria-disabled');
  });
  if(blocked) showDoorNotice('blockedMsg', 'warning', 'You can’t start a new request yet', m.submit_block_reason || '');
  else $('blockedMsg').textContent = '';

  renderRequests(m.requests);
  enterView('homeView', 'meName');

  const wanted = new URLSearchParams(window.location.search).get('door');
  if(wanted === 'complaint' && m.can_submit_new){
    history.replaceState(null, '', 'portal.html');
    window.location.href = 'request-new.html?door=complaint';
  }
}

function blockIfDisabled(e){
  if(e.currentTarget.classList.contains('disabled')){
    e.preventDefault();
    $('blockedMsg').focus();
  }
}

async function signOut(e){
  const btn = e.currentTarget;
  const label = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Signing out…';
  await api('/portal/logout', {method: 'POST'});
  btn.disabled = false;
  btn.textContent = label;
  me = null;
  // A fresh page, so none of the client's list or details stays in the
  // page's memory for the next person at this computer.
  window.location.replace('portal.html');
}

/** The door, empty, with a message at the top of the card if there is one. */
function backToDoor(kind, title, message){
  resetDoor();
  $('doorContact').value = '';
  show('doorView');
  if(title) showDoorNotice('doorTopNotice', kind, title, message);
}

/* ---------- wiring ---------- */
// One form: Enter or the button sends the code on step 1 and checks it on step 2.
$('doorForm').addEventListener('submit', e => {
  e.preventDefault();
  if($('doorCodeStep').hidden) sendCode(false);
  else verifyCode();
});
$('doorResendBtn').addEventListener('click', () => sendCode(true));
$('doorChangeBtn').addEventListener('click', changeContact);
$('pfCategory').addEventListener('change', updateCategoryFields);
// Clear a field's error as soon as the person fixes it.
[['pfLastName', 'f-lastName'], ['pfFirstName', 'f-firstName'], ['pfMiddleName', 'f-middleName'], ['pfNoMiddleName', 'f-middleName'],
 ['pfCategory', 'f-category'], ['pfOffice', 'f-office'], ['pfOfficeOther', 'f-officeOther'],
 ['pfOrganization', 'f-organization'], ['pfConsent', 'f-consent'], ['doorContact', 'f-doorContact'], ['doorCode', 'f-doorCode']]
  .forEach(([input, field]) => ['input', 'change'].forEach(ev => $(input).addEventListener(ev, () => {
    setInvalid(field, false);
    refreshProfileSummary();
  })));
$('pfOffice').addEventListener('change', () => { toggleOfficeOther(); refreshProfileSummary(); });
$('pfNoMiddleName').addEventListener('change', toggleNoMiddleName);
$('profileForm').addEventListener('submit', e => {
  e.preventDefault();
  saveProfile();
});
// A link in the error summary goes to its field.
$('profileSummary').addEventListener('click', e => {
  const link = e.target.closest('a[data-focus]');
  if(!link) return;
  e.preventDefault();
  $(link.dataset.focus).focus();
});
$('pfCancelBtn').addEventListener('click', () => renderHome(me));
$('pfSignOutBtn').addEventListener('click', signOut);
$('editProfileBtn').addEventListener('click', () => openProfile('edit'));
$('signOutBtn').addEventListener('click', signOut);
$('newRequestBtn').addEventListener('click', blockIfDisabled);
$('newComplaintBtn').addEventListener('click', blockIfDisabled);

// Already signed in (page refreshed, or back from a request)? Go straight in.
loadMe().finally(() => { pageOpening = false; });
