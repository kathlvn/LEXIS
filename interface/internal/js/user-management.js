// User Management page (the list, and editing one account).
// Head Attorney only. The server
// enforces the same rules (routers/staff.py): accounts are never deleted,
// the sign-in email can't be changed, and the last active Head Attorney
// can't be deactivated or given another role.
//
// Two views: the list of staff accounts, and one account with its cards
// (Details on the left; Account and Temporary password on the right). Each
// card saves on its own: PATCH /staff/{id} and POST /staff/{id}/password.
// Saving the Details or the password goes back to the list, unless the other
// card still has changes that aren't saved.
(function () {
  const ROLES = [
    ['legal_assistant', 'Legal Assistant'],
    ['attorney', 'Attorney'],
    ['head_attorney', 'Head Attorney'],
  ];
  const ROLE_LABELS = Object.fromEntries(ROLES);
  // "What each role can do", in the same order as the Role list
  const ROLE_NOTES = {
    legal_assistant: 'Works on requests assigned to them, encodes walk-ins, and acts on client verification only for their own requests.',
    attorney: 'Approves requests, returns work for revision, cancels, rejects or reopens requests, and verifies any client. Reserved for a future hire.',
    head_attorney: 'Everything, plus User Management, Service Types, Offices, Settings, Records Archive, Security Log and reassignment.',
  };
  const NOTICE_ICONS = {ok: ICONS.check, error: ICONS.noticeError, warn: ICONS.noticeWarning, info: ICONS.noticeInfo};
  const ESSU_EMAIL = /^[^\s@]+@essu\.edu\.ph$/i;

  // Anyone else who opens this page by its address goes back to the requests.
  (function guardHeadAttorneyOnly(triesLeft) {
    if (!window.currentStaff) {
      if (triesLeft > 0) setTimeout(() => guardHeadAttorneyOnly(triesLeft - 1), 100);
      return;
    }
    if (window.currentStaff.role !== 'head_attorney') window.location.replace('requests-all.html');
  })(50);

  renderTopbar();
  renderSidebar('user-management');

  const $ = id => document.getElementById(id);
  const table = $('umTable');
  const addCard = $('umAddCard');
  const form = $('umForm');
  const formMessage = $('umFormMessage');
  const detailsCard = $('umDetailsCard');
  const accountCard = $('umAccountCard');
  const passwordCard = $('umPasswordCard');
  $('umBackBtn').innerHTML = ICONS.back;
  // Typing in a card clears its "Saved" line
  [detailsCard, accountCard, passwordCard].forEach(card => card.addEventListener('input', () => {
    card.querySelectorAll('.um-status.ok, .um-status.note').forEach(el => { el.innerHTML = ''; });
  }));

  let staffList = [];       // the list, as the server last sent it
  let current = null;       // the account open in the edit view
  let openVerifyId = null;  // the list row whose code form is open
  let devCodes = {};        // development only: the code the server sent back
  let historyPushed = false;

  /* ---------------- Small helpers ---------------- */
  function friendlyError(error) {
    if (typeof error === 'string') return error;
    if (Array.isArray(error) && error[0] && error[0].msg) return error[0].msg.replace(/^Value error, /, '');
    return 'Please check the form and try again.';
  }
  function plural(n, word) {
    return `${n} ${word}${n === 1 ? '' : 's'}`;
  }
  function isSelf(staff) {
    return !!window.currentStaff && window.currentStaff.id === staff.id;
  }
  // The only active Head Attorney: can't be deactivated or given another role
  function isLastHead(staff) {
    if (staff.role !== 'head_attorney' || !staff.is_active) return false;
    return !staffList.some(s => s.id !== staff.id && s.role === 'head_attorney' && s.is_active);
  }
  function dot(tone, text) {
    return `<span class="um-state"><span class="um-dot ${tone}" aria-hidden="true"></span>${escapeHtml(text)}</span>`;
  }
  function activeDot(staff) {
    return staff.is_active ? dot('on', 'Active') : dot('off', 'Inactive');
  }
  function emailDot(staff) {
    return staff.is_email_verified ? dot('on', 'Verified') : dot('warn', 'Not verified');
  }

  /* ---------------- Messages ---------------- */
  function msgHtml(text, tone) {
    return `<p class="page-msg ${tone}"><span class="page-msg-icon">${NOTICE_ICONS[tone]}</span><span>${escapeHtml(text)}</span></p>`;
  }
  function setMsg(el, text, tone) {
    el.innerHTML = text ? msgHtml(text, tone || 'ok') : '';
  }
  // The line beside a card's button: done (ok) or what went wrong (error)
  function lineStatus(el, text, tone) {
    el.className = `um-status ${tone || 'ok'}`;
    el.setAttribute('role', tone === 'error' ? 'alert' : 'status');
    el.innerHTML = text ? `${tone === 'ok' ? ICONS.check : ''}<span>${escapeHtml(text)}</span>` : '';
  }

  /* ---------------- The list ---------------- */
  function verifyHtml(staff) {
    if (staff.is_email_verified) return emailDot(staff);
    if (openVerifyId !== staff.id) {
      return `${emailDot(staff)}
        <button type="button" class="um-link brand" data-action="open-verify">Enter the code</button>`;
    }
    return `${emailDot(staff)}
      <div class="um-verify" data-id="${staff.id}">
        <div class="um-verify-row">
          <input type="text" class="rd-input um-code" inputmode="numeric" autocomplete="one-time-code" maxlength="6" placeholder="6-digit code" aria-label="6-digit code for ${escapeHtml(staff.full_name)}" value="${escapeHtml(devCodes[staff.id] || '')}">
          <button type="button" class="btn btn-primary btn-compact" data-action="verify">Verify</button>
          <button type="button" class="um-link" data-action="close-verify">Cancel</button>
        </div>
        <p class="um-status" role="status"></p>
        <button type="button" class="um-link brand" data-action="resend">Resend code</button>
      </div>`;
  }

  function rowHtml(staff) {
    const cls = ['um-row'];
    if (!staff.is_active) cls.push('inactive');
    if (openVerifyId === staff.id) cls.push('open');
    return `<tr class="${cls.join(' ')}" data-id="${staff.id}">
      <td class="um-who" data-label="Name and email"><span class="um-name">${escapeHtml(staff.full_name)}</span><span class="um-email">${escapeHtml(staff.email)}</span></td>
      <td class="um-fact" data-label="Role">${escapeHtml(ROLE_LABELS[staff.role] || staff.role)}</td>
      <td class="um-fact" data-label="Status">${activeDot(staff)}</td>
      <td class="um-fact um-verify-cell" data-label="Sign-in email">${verifyHtml(staff)}</td>
      <td class="um-act"><button type="button" class="um-edit-link" data-action="edit" aria-label="Edit ${escapeHtml(staff.full_name)}">Edit</button></td>
    </tr>`;
  }

  function renderList() {
    table.querySelectorAll('tbody').forEach(tb => tb.remove());
    const active = staffList.filter(s => s.is_active).length;
    $('umCount').textContent = staffList.length ? `${plural(staffList.length, 'account')} · ${active} active` : '';
    if (!staffList.length) {
      table.insertAdjacentHTML('beforeend', '<tbody><tr><td class="um-empty" colspan="5">No staff accounts yet.</td></tr></tbody>');
      return;
    }
    table.insertAdjacentHTML('beforeend', `<tbody>${staffList.map(rowHtml).join('')}</tbody>`);
  }

  // Re-draws one row only, so a code being typed elsewhere isn't lost
  function renderRow(staff) {
    const old = table.querySelector(`tr.um-row[data-id="${staff.id}"]`);
    if (!old) return renderList();
    old.insertAdjacentHTML('afterend', rowHtml(staff));
    old.remove();
  }

  function openVerify(staff) {
    const before = staffList.find(s => s.id === openVerifyId);
    openVerifyId = staff.id;
    if (before && before.id !== staff.id) renderRow(before);
    renderRow(staff);
    const input = table.querySelector(`.um-verify[data-id="${staff.id}"] .um-code`);
    if (input) input.focus();
  }

  table.addEventListener('click', async e => {
    const row = e.target.closest('tr.um-row');
    if (!row) return;
    const staff = staffList.find(s => s.id === Number(row.dataset.id));
    if (!staff) return;
    const btn = e.target.closest('button');
    const action = btn && btn.dataset.action;
    if (action === 'open-verify') return openVerify(staff);
    if (action === 'close-verify') {
      openVerifyId = null;
      renderRow(staff);
      const again = table.querySelector(`tr.um-row[data-id="${staff.id}"] [data-action="open-verify"]`);
      if (again) again.focus();
      return;
    }
    if (action === 'verify') return verifyFromList(staff, row);
    if (action === 'resend') return resendFromList(staff, row, btn);
    // Anywhere else on the row (not inside the code form) opens the account
    if (e.target.closest('.um-verify')) return;
    openEdit(staff);
  });
  table.addEventListener('keydown', e => {
    if (e.key === 'Enter' && e.target.classList.contains('um-code')) {
      e.preventDefault();
      const row = e.target.closest('tr.um-row');
      const staff = staffList.find(s => s.id === Number(row.dataset.id));
      if (staff) verifyFromList(staff, row);
    }
  });

  async function verifyFromList(staff, row) {
    const box = row.querySelector('.um-verify');
    const input = box.querySelector('.um-code');
    const status = box.querySelector('.um-status');
    const code = input.value.trim();
    if (!/^\d{6}$/.test(code)) { lineStatus(status, 'Enter the 6 digits from the email.', 'error'); input.focus(); return; }
    const btn = box.querySelector('[data-action="verify"]');
    btn.disabled = true;
    const { error } = await verifyStaffEmail(staff.email, code);
    btn.disabled = false;
    if (error) { lineStatus(status, friendlyError(error), 'error'); input.focus(); return; }
    openVerifyId = null;
    delete devCodes[staff.id];
    await load();
    setMsg($('umPageMsg'), `${staff.full_name}’s sign-in email is verified. They can sign in now.`, 'ok');
  }

  async function resendFromList(staff, row, btn) {
    const status = row.querySelector('.um-verify .um-status');
    btn.disabled = true;
    const { devCode, error } = await resendVerification(staff.email);
    btn.disabled = false;
    if (error) { lineStatus(status, friendlyError(error), 'error'); return; }
    if (devCode) {
      devCodes[staff.id] = devCode;
      row.querySelector('.um-code').value = devCode;
    }
    lineStatus(status, devCode ? 'A new code was sent (filled in here in development only).' : 'A new code was sent.');
  }

  async function load() {
    table.querySelectorAll('tbody').forEach(tb => tb.remove());
    table.insertAdjacentHTML('beforeend', '<tbody><tr><td class="um-empty" colspan="5">Loading the staff accounts…</td></tr></tbody>');
    const { rows, error } = await fetchStaff();
    if (error) {
      table.querySelectorAll('tbody').forEach(tb => tb.remove());
      table.insertAdjacentHTML('beforeend', `<tbody><tr><td class="um-empty" colspan="5">${escapeHtml(friendlyError(error))}</td></tr></tbody>`);
      return false;
    }
    staffList = rows;
    renderList();
    return true;
  }

  /* ---------------- Add a staff account ---------------- */
  function closeAdd() {
    form.reset();
    formMessage.innerHTML = '';
    addCard.hidden = true;
    $('umAddBtn').hidden = false;
  }
  $('umAddBtn').addEventListener('click', () => {
    addCard.hidden = false;
    $('umAddBtn').hidden = true;
    $('umName').focus();
  });
  $('umFormCancelBtn').addEventListener('click', () => {
    closeAdd();
    $('umAddBtn').focus();
  });
  form.addEventListener('input', () => { formMessage.innerHTML = ''; });

  form.addEventListener('submit', async e => {
    e.preventDefault();
    const fullName = $('umName').value.trim();
    const email = $('umEmail').value.trim();
    const password = $('umPassword').value;
    const fail = (text, field) => { lineStatus(formMessage, text, 'error'); field.focus(); };
    if (!fullName) return fail('Enter the person’s full name.', $('umName'));
    if (!ESSU_EMAIL.test(email)) return fail('Enter an @essu.edu.ph email address.', $('umEmail'));
    if (password.length < 12) return fail('Use at least 12 characters for the temporary password.', $('umPassword'));

    const btn = $('umFormSubmitBtn');
    btn.disabled = true;
    formMessage.innerHTML = '';
    const { staff, error } = await createStaff({ fullName, email, password, role: $('umRole').value });
    btn.disabled = false;
    if (error) { lineStatus(formMessage, friendlyError(error), 'error'); return; }

    closeAdd();
    if (staff.dev_verification_code) devCodes[staff.id] = staff.dev_verification_code;
    openVerifyId = staff.id;
    await load();
    setMsg($('umPageMsg'), `Account created for ${staff.full_name}. Enter the 6-digit code sent to their email to finish setting it up.`, 'ok');
    const input = table.querySelector(`.um-verify[data-id="${staff.id}"] .um-code`);
    if (input) input.focus();
  });

  /* ---------------- Views and the browser's Back button ---------------- */
  function showView(next) {
    $('umListView').hidden = next !== 'list';
    $('umEditView').hidden = next !== 'edit';
    window.scrollTo(0, 0);
  }
  function pushStep() {
    if (historyPushed) return;
    history.pushState({ userManagementEdit: true }, '');
    historyPushed = true;
  }
  function dropStep() {
    if (!historyPushed) return;
    historyPushed = false;
    history.back();          // its popstate is ignored (historyPushed is already false)
  }
  window.addEventListener('popstate', async () => {
    if (!historyPushed) return;
    historyPushed = false;
    if (!current) return;
    if (!(await leaveEdit(true))) pushStep();   // "Keep editing": stay, and keep the step
  });

  function openEdit(staff, note) {
    current = staff;
    setMsg($('umPageMsg'), '');
    renderEditHeading();
    renderDetails();
    renderAccount();
    renderPassword();
    setMsg($('umEditMsg'), note || '');
    showView('edit');
    pushStep();
  }

  function isDirty() {
    return !!current && (detailsDirty() || passwordDirty());
  }

  // Back to the list. Returns a Promise (true when it left). fromPopstate:
  // the browser has already gone back one step.
  async function leaveEdit(fromPopstate) {
    if (isDirty() && !(await confirmDialog({
      title: 'Leave without saving?',
      message: 'Changes you haven’t saved on this account will be lost.',
      confirmLabel: 'Leave without saving', cancelLabel: 'Keep editing', danger: true
    }))) return false;
    current = null;
    showView('list');
    if (!fromPopstate) dropStep();
    renderList();
    return true;
  }
  $('umBackBtn').addEventListener('click', () => leaveEdit(false));

  window.addEventListener('beforeunload', e => {
    if (isDirty()) { e.preventDefault(); e.returnValue = ''; }
  });

  // After a card saves: the server's copy replaces ours in the list too.
  function replaceCurrent(data) {
    current = data;
    const i = staffList.findIndex(s => s.id === data.id);
    if (i >= 0) staffList[i] = data; else staffList.push(data);
  }

  // After a card saves: back to the list. Stays only when another card has
  // changes that aren't saved, because going back would lose them.
  async function afterSave(status, text) {
    if (isDirty()) {
      lineStatus(status, `${text} Other changes on this page are not saved yet.`);
      return;
    }
    const name = current.full_name;
    await leaveEdit(false);
    setMsg($('umPageMsg'), `${name}: ${text}`, 'ok');
  }

  function renderEditHeading() {
    $('umEditTitle').textContent = current.full_name;
    $('umEditSub').textContent = [
      ROLE_LABELS[current.role] || current.role,
      current.email,
      'Each card saves on its own.'
    ].join(' · ');
  }

  /* ---------------- Card: Details ---------------- */
  function readDetails() {
    return { full_name: $('umInName').value.trim(), role: $('umInRole').value };
  }
  function detailsDirty() {
    const now = readDetails();
    return now.full_name !== current.full_name || now.role !== current.role;
  }

  function renderDetails() {
    const staff = current;
    const locked = isLastHead(staff);
    const roleNotes = ROLES.map(([code, label]) =>
      `<div class="um-role-line"><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(ROLE_NOTES[code])}</dd></div>`).join('');
    detailsCard.innerHTML = `
      <h2 class="rd-card-title" id="umDetailsTitle">Details</h2>
      <div class="um-fields um-fields-wide">
        <div class="rd-field">
          <label class="rd-label" for="umInName">Full name</label>
          <input type="text" class="rd-input" id="umInName" maxlength="150" autocomplete="off" value="${escapeHtml(staff.full_name)}">
        </div>
        <div class="rd-field">
          <label class="rd-label" for="umInRole">Role</label>
          <select class="rd-input" id="umInRole"${locked ? ' disabled aria-describedby="umRoleLocked"' : ''}>
            ${ROLES.map(([code, label]) => `<option value="${code}"${code === staff.role ? ' selected' : ''}>${escapeHtml(label)}</option>`).join('')}
          </select>
          ${locked ? '<p class="rd-hint" id="umRoleLocked">The only active Head Attorney keeps this role.</p>' : ''}
        </div>
        <div class="rd-field um-span">
          <label class="rd-label" for="umInEmail">Sign-in email</label>
          <input type="email" class="rd-input um-readonly" id="umInEmail" value="${escapeHtml(staff.email)}" readonly aria-describedby="umInEmailHint">
          <p class="rd-hint" id="umInEmailHint">The sign-in email can’t be changed. For a new email, create a new account.</p>
        </div>
      </div>
      <div class="um-roles">
        <p class="um-roles-title">What each role can do</p>
        <dl>${roleNotes}</dl>
      </div>
      <div class="um-actions">
        <p class="um-status" role="status"></p>
        <button type="button" class="btn btn-primary" id="umSaveDetails">Save details</button>
      </div>`;
    $('umSaveDetails').addEventListener('click', saveDetails);
  }

  async function saveDetails() {
    const btn = $('umSaveDetails');
    const status = detailsCard.querySelector('.um-actions .um-status');
    const now = readDetails();
    if (!now.full_name) { lineStatus(status, 'Enter the person’s full name.', 'error'); $('umInName').focus(); return; }
    const payload = {};
    if (now.full_name !== current.full_name) payload.full_name = now.full_name;
    if (now.role !== current.role) payload.role = now.role;
    if (!Object.keys(payload).length) { lineStatus(status, 'No changes to save.', 'note'); return; }

    const self = isSelf(current);
    if (payload.role) {
      const message = self && current.role === 'head_attorney'
        ? 'You will lose User Management and the other Head Attorney pages as soon as this is saved.'
        : `${ROLE_LABELS[payload.role]}: ${ROLE_NOTES[payload.role]}`;
      if (!(await confirmDialog({
        title: self ? 'Change your own role?' : `Make ${current.full_name} ${/^[AEIOU]/.test(ROLE_LABELS[payload.role]) ? 'an' : 'a'} ${ROLE_LABELS[payload.role]}?`,
        message,
        confirmLabel: 'Change role',
        danger: self
      }))) return;
    }

    btn.disabled = true;
    lineStatus(status, '');
    const { staff, error } = await updateStaff(current.id, payload);
    btn.disabled = false;
    if (error) { lineStatus(status, friendlyError(error), 'error'); return; }
    if (self && payload.role) { window.location.replace('requests-all.html'); return; }
    replaceCurrent(staff);
    renderEditHeading();
    renderDetails();
    renderAccount();
    afterSave(detailsCard.querySelector('.um-actions .um-status'), 'Details saved.');
  }

  /* ---------------- Card: Account ---------------- */
  function renderAccount() {
    const staff = current;
    const self = isSelf(staff);
    const last = isLastHead(staff);
    let action;
    if (self) {
      action = '<p class="um-side-note">This is your own account. Another Head Attorney can deactivate it.</p>';
    } else if (staff.is_active) {
      action = `<button type="button" class="btn btn-danger um-wide" id="umToggleActive"${last ? ' disabled' : ''}>Deactivate account</button>`;
    } else {
      action = '<button type="button" class="btn btn-primary um-wide" id="umToggleActive">Activate account</button>';
    }
    const verify = staff.is_email_verified ? '' : `
      <div class="um-verify um-verify-side">
        <label class="rd-label" for="umSideCode">Code from their email</label>
        <div class="um-verify-row">
          <input type="text" class="rd-input um-code" id="umSideCode" inputmode="numeric" autocomplete="one-time-code" maxlength="6" placeholder="6-digit code" value="${escapeHtml(devCodes[staff.id] || '')}">
          <button type="button" class="btn btn-primary btn-compact" id="umSideVerify">Verify</button>
        </div>
        <p class="um-status" role="status"></p>
        <button type="button" class="um-link brand" id="umSideResend">Resend code</button>
      </div>`;
    accountCard.innerHTML = `
      <h2 class="rd-card-title" id="umAccountTitle">Account</h2>
      <dl class="um-lines">
        <div class="um-line"><dt>Status</dt><dd>${activeDot(staff)}</dd></div>
        <div class="um-line"><dt>Sign-in email</dt><dd>${emailDot(staff)}</dd></div>
      </dl>
      ${verify}
      <hr class="um-rule">
      <p class="um-note">${staff.is_active
        ? 'Deactivating signs this person out at once and stops them signing in again. Their name stays on past requests and history. You can activate the account again later.'
        : 'This account can’t sign in. Activating it lets them sign in again with their current password.'}</p>
      ${action}
      <p class="um-status" role="status" id="umAccountStatus"></p>
      <p class="um-box-note">The last active Head Attorney can’t be deactivated or given another role.</p>`;

    const toggle = $('umToggleActive');
    if (toggle) toggle.addEventListener('click', toggleActive);
    if (!staff.is_email_verified) {
      $('umSideVerify').addEventListener('click', verifyFromSide);
      $('umSideCode').addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); verifyFromSide(); } });
      $('umSideResend').addEventListener('click', resendFromSide);
    }
  }

  async function toggleActive() {
    const staff = current;
    const turnOn = !staff.is_active;
    if (!turnOn && !(await confirmDialog({
      title: `Deactivate ${staff.full_name}’s account?`,
      message: 'They are signed out at once and can’t sign in until the account is activated again. Their name stays on past requests and history.',
      confirmLabel: 'Deactivate account', cancelLabel: 'Keep it active', danger: true
    }))) return;
    const btn = $('umToggleActive');
    btn.disabled = true;
    const { staff: data, error } = await updateStaff(staff.id, { is_active: turnOn });
    if (error) {
      btn.disabled = false;
      const status = $('umAccountStatus');
      lineStatus(status, friendlyError(error), 'error');
      // Still has open requests: link to them, for reassigning.
      if (typeof error === 'string' && error.includes('open request')) {
        const link = document.createElement('a');
        link.href = `requests-all.html?officer=${encodeURIComponent(staff.id)}`;
        link.className = 'um-link brand';
        link.textContent = 'See their requests';
        (status.querySelector('span') || status).append(' ', link);
      }
      return;
    }
    replaceCurrent(data);
    renderAccount();
    lineStatus($('umAccountStatus'), turnOn ? 'Account activated. They can sign in again.' : 'Account deactivated.');
  }

  async function verifyFromSide() {
    const box = accountCard.querySelector('.um-verify');
    const input = $('umSideCode');
    const status = box.querySelector('.um-status');
    const code = input.value.trim();
    if (!/^\d{6}$/.test(code)) { lineStatus(status, 'Enter the 6 digits from the email.', 'error'); input.focus(); return; }
    const btn = $('umSideVerify');
    btn.disabled = true;
    const { error } = await verifyStaffEmail(current.email, code);
    btn.disabled = false;
    if (error) { lineStatus(status, friendlyError(error), 'error'); input.focus(); return; }
    delete devCodes[current.id];
    if (openVerifyId === current.id) openVerifyId = null;
    replaceCurrent(Object.assign({}, current, { is_email_verified: true }));
    renderAccount();
    lineStatus($('umAccountStatus'), 'Sign-in email verified. They can sign in now.');
  }

  async function resendFromSide() {
    const status = accountCard.querySelector('.um-verify .um-status');
    const btn = $('umSideResend');
    btn.disabled = true;
    const { devCode, error } = await resendVerification(current.email);
    btn.disabled = false;
    if (error) { lineStatus(status, friendlyError(error), 'error'); return; }
    if (devCode) { devCodes[current.id] = devCode; $('umSideCode').value = devCode; }
    lineStatus(status, devCode ? 'A new code was sent (filled in here in development only).' : 'A new code was sent.');
  }

  /* ---------------- Card: Temporary password ----------------
     For a staff member who forgot their password. The Head Attorney tells
     them the new one in person; nothing is emailed. Not for your own account
     (the server refuses it: you would be signed out). */
  function passwordDirty() {
    const input = $('umInPassword');
    return !!input && input.value !== '';
  }

  function renderPassword() {
    if (isSelf(current)) {
      passwordCard.hidden = true;
      passwordCard.innerHTML = '';
      return;
    }
    passwordCard.hidden = false;
    passwordCard.innerHTML = `
      <h2 class="rd-card-title" id="umPasswordTitle">Temporary password</h2>
      <p class="um-note">For someone who forgot their password. Tell them the new one in person; nothing is emailed. Setting it signs them out of any open session, and they choose their own password when they next log in.</p>
      <form class="um-form" id="umPasswordForm" novalidate>
        <div class="rd-field">
          <label class="rd-label" for="umInPassword">New temporary password</label>
          <input type="password" class="rd-input" id="umInPassword" minlength="12" maxlength="256" autocomplete="new-password" aria-describedby="umInPasswordHint">
          <p class="rd-hint" id="umInPasswordHint">At least 12 characters, not a common word or pattern, and not their name or email. It works for 72 hours.</p>
        </div>
        <button type="submit" class="btn btn-secondary um-wide" id="umSavePassword">Set temporary password</button>
        <p class="um-status" role="status"></p>
      </form>`;
    $('umPasswordForm').addEventListener('submit', savePassword);
  }

  async function savePassword(e) {
    e.preventDefault();
    const input = $('umInPassword');
    const status = passwordCard.querySelector('.um-status');
    if (input.value.length < 12) { lineStatus(status, 'Use at least 12 characters.', 'error'); input.focus(); return; }
    const btn = $('umSavePassword');
    btn.disabled = true;
    const { ok, error } = await setStaffPassword(current.id, input.value);
    btn.disabled = false;
    if (!ok) { lineStatus(status, friendlyError(error), 'error'); return; }
    input.value = '';
    afterSave(status, 'Temporary password set. Tell them in person; they’ll choose their own when they log in.');
  }

  load();
})();
