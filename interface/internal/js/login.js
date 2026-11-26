// Staff login. One card with three steps:
// the login form; the email-code step a new account sees until its ESSU
// email is verified (POST /auth/verify-email); and the "choose a new
// password" step after logging in with a password the Head Attorney set
// (POST /auth/new-password). The server does every check; this page only
// shows its answers in plain words.
(function () {
  const $ = id => document.getElementById(id);
  const loginForm = $('loginForm');
  const verifyForm = $('verifyForm');
  const newPwForm = $('newPasswordForm');
  const message = $('loginMessage');
  const title = $('loginTitle');
  const lede = $('loginLede');
  const emailInput = $('loginEmail');
  const passwordInput = $('loginPassword');
  const codeInput = $('verifyCode');
  const resendBtn = $('resendCodeLink');
  const backBtn = $('backToLoginLink');
  const newPwInput = $('newPassword');
  const newPwConfirm = $('newPasswordConfirm');

  const NEW_PW_MIN = 12;
  let pendingVerifyEmail = null;
  // The step "choose a new password" sends the given password again, so the
  // server can check it (no session exists yet). Kept only in this page's
  // memory, never stored; cleared on "Back to login" and on success.
  let pendingNewPw = null; // {email, password}

  // tone: notice (grey), ok (green) or error (red, read out at once)
  function showMessage(text, tone = 'error') {
    message.textContent = text;
    message.className = `login-msg ${tone}`;
    message.setAttribute('role', tone === 'error' ? 'alert' : 'status');
    message.hidden = false;
  }
  function clearMessage() {
    message.textContent = '';
    message.hidden = true;
  }

  // A string from the server, or nothing (FastAPI sends a list for bad input).
  const detailOf = data => (data && typeof data.detail === 'string' ? data.detail : '');

  async function post(path, body) {
    const res = await fetch(`${API_BASE_URL}${path}`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      credentials: 'include',
      body: JSON.stringify(body)
    });
    const data = await res.json().catch(() => ({}));
    return {res, data};
  }

  function busy(btn, label) {
    btn.dataset.label = btn.dataset.label || btn.textContent;
    btn.disabled = true;
    btn.textContent = label;
  }
  function idle(btn) {
    btn.disabled = false;
    if (btn.dataset.label) btn.textContent = btn.dataset.label;
  }

  // Sent here by the console after a 401 (see apiFetch / auth-guard.js).
  if (new URLSearchParams(window.location.search).get('expired') === '1') {
    showMessage('You were logged out after 30 minutes without activity, or at the 12-hour limit. Log in again to continue.', 'notice');
  }

  /* ---------------- Show / hide passwords ---------------- */
  // One button shows or hides every input it controls (on the new-password
  // step, both the new password and its confirmation). Returns a function
  // that hides them again.
  function passwordToggle(btn, inputs, noun) {
    btn.addEventListener('click', () => {
      const show = inputs[0].type === 'password';
      inputs.forEach(input => { input.type = show ? 'text' : 'password'; });
      btn.textContent = show ? 'Hide' : 'Show';
      btn.setAttribute('aria-pressed', String(show));
      btn.setAttribute('aria-label', `${show ? 'Hide' : 'Show'} ${noun}`);
      inputs[0].focus();
    });
    return () => { if (inputs[0].type === 'text') btn.click(); };
  }
  const hidePassword = passwordToggle($('passwordToggle'), [passwordInput], 'password');
  const hideNewPasswords = passwordToggle($('newPasswordToggle'), [newPwInput, newPwConfirm], 'passwords');

  /* ---------------- Switching between the steps ---------------- */
  function showOnly(form) {
    [loginForm, verifyForm, newPwForm].forEach(f => { f.hidden = f !== form; });
  }

  function showVerifyStep(email) {
    pendingVerifyEmail = email;
    title.textContent = 'Verify your email';
    lede.textContent = `Enter the 6-digit code sent to ${email} to finish setting up your account.`;
    showOnly(verifyForm);
    codeInput.value = '';
    clearMessage();
    codeInput.focus();
  }

  function showNewPasswordStep(email, password) {
    pendingNewPw = {email, password};
    title.textContent = 'Choose a new password';
    lede.textContent = 'The Head Attorney set your current password. Choose your own to finish logging in.';
    showOnly(newPwForm);
    $('newPwUsername').value = email;
    newPwInput.value = '';
    newPwConfirm.value = '';
    hideNewPasswords();
    clearMessage();
    newPwInput.focus();
  }

  function showLoginStep(prefillEmail) {
    pendingNewPw = null;
    title.textContent = 'Staff login';
    lede.textContent = 'Log in with your ESSU staff account.';
    showOnly(loginForm);
    passwordInput.value = '';
    newPwInput.value = '';
    newPwConfirm.value = '';
    hidePassword();
    hideNewPasswords();
    if (prefillEmail) emailInput.value = prefillEmail;
    (emailInput.value ? passwordInput : emailInput).focus();
  }

  /* ---------------- Step 1: email and password ---------------- */
  loginForm.addEventListener('submit', async e => {
    e.preventDefault();
    clearMessage();

    const email = emailInput.value.trim();
    const password = passwordInput.value;
    if (!email || !password) {
      showMessage(!email ? 'Enter your email address.' : 'Enter your password.');
      (!email ? emailInput : passwordInput).focus();
      return;
    }

    const submitBtn = $('loginSubmitBtn');
    busy(submitBtn, 'Logging in…');
    try {
      const {res, data} = await post('/auth/login', {email, password});
      if (res.ok) {
        window.location.href = 'dashboard.html';
        return;
      }
      const detail = detailOf(data);
      if (res.status === 403 && detail.toLowerCase().includes('verify your email')) {
        showVerifyStep(email);
        return;
      }
      if (res.status === 403 && detail.toLowerCase().includes('choose a new password')) {
        showNewPasswordStep(email, password);
        return;
      }
      if (res.status === 403) showMessage(detail || 'This account cannot log in right now.');
      else if (res.status === 423) showMessage(detail || 'Too many failed attempts. Try again later.');
      else if (res.status === 429) showMessage(detail || 'Too many tries. Wait a minute, then try again.');
      else if (res.status === 401) showMessage('Incorrect email or password.');
      else showMessage('Something went wrong. Please try again.');
      // The email stays; the password is cleared so it can be typed again.
      passwordInput.value = '';
      passwordInput.focus();
    } catch (err) {
      showMessage('Could not reach the server. Check your connection and try again.');
    } finally {
      idle(submitBtn);
    }
  });

  /* ---------------- Step 2: the code from the email ---------------- */
  // Digits only, so a pasted "123 456" still works.
  codeInput.addEventListener('input', () => {
    const digits = codeInput.value.replace(/\D/g, '').slice(0, 6);
    if (digits !== codeInput.value) codeInput.value = digits;
  });

  verifyForm.addEventListener('submit', async e => {
    e.preventDefault();
    clearMessage();

    const code = codeInput.value.trim();
    if (!/^\d{6}$/.test(code)) {
      showMessage('Enter the 6-digit code from the email.');
      codeInput.focus();
      return;
    }

    const submitBtn = $('verifySubmitBtn');
    busy(submitBtn, 'Verifying…');
    try {
      const {res, data} = await post('/auth/verify-email', {email: pendingVerifyEmail, code});
      if (res.ok) {
        showLoginStep(pendingVerifyEmail);
        showMessage('Your account is verified. Log in to continue.', 'ok');
      } else {
        showMessage(detailOf(data) || 'Could not verify that code.');
        codeInput.focus();
      }
    } catch (err) {
      showMessage('Could not reach the server. Check your connection and try again.');
    } finally {
      idle(submitBtn);
    }
  });

  resendBtn.addEventListener('click', async () => {
    if (!pendingVerifyEmail || resendBtn.disabled) return;
    busy(resendBtn, 'Sending…');
    try {
      const {res, data} = await post('/auth/resend-verification', {email: pendingVerifyEmail});
      if (res.ok) showMessage(detailOf(data) || 'If an account needs verification, a new code has been sent.', 'notice');
      else showMessage(detailOf(data) || 'Could not send a new code. Please try again.');
    } catch (err) {
      showMessage('Could not reach the server. Please try again.');
    } finally {
      // A short pause so the button can't be pressed again at once.
      setTimeout(() => idle(resendBtn), 3000);
    }
  });

  backBtn.addEventListener('click', () => {
    clearMessage();
    showLoginStep();
  });

  /* ---------------- Step 3: choose a new password ---------------- */
  newPwForm.addEventListener('submit', async e => {
    e.preventDefault();
    clearMessage();
    if (!pendingNewPw) { showLoginStep(); return; }

    const newPassword = newPwInput.value;
    const confirm = newPwConfirm.value;
    // Same rules as the server (schemas.StaffNewPassword, routers/auth.py),
    // checked here first so a slip costs no request.
    let problem = '', field = newPwInput;
    if (!newPassword) problem = 'Enter a new password.';
    else if (newPassword.length < NEW_PW_MIN) problem = `Use at least ${NEW_PW_MIN} characters.`;
    else if (newPassword === pendingNewPw.password) problem = 'Choose a password different from the one you were given.';
    else if (newPassword !== confirm) { problem = 'The two passwords don’t match.'; field = newPwConfirm; }
    if (problem) {
      showMessage(problem);
      field.focus();
      return;
    }

    const submitBtn = $('newPasswordSubmitBtn');
    busy(submitBtn, 'Saving…');
    try {
      const {res, data} = await post('/auth/new-password', {
        email: pendingNewPw.email, password: pendingNewPw.password, new_password: newPassword
      });
      if (res.ok) {
        pendingNewPw = null;
        window.location.href = 'dashboard.html';
        return;
      }
      const detail = detailOf(data);
      if (res.status === 401 || res.status === 403 || res.status === 423 ||
          (res.status === 400 && detail.toLowerCase().includes('log in as usual'))) {
        // The given password no longer works, the account is locked or
        // switched off, or it no longer needs a new password: start over.
        const email = pendingNewPw.email;
        showLoginStep(email);
        showMessage(res.status === 401
          ? 'Your password was changed meanwhile. Log in again.'
          : (detail || 'This account cannot log in right now.'));
        return;
      }
      if (res.status === 429) showMessage(detail || 'Too many tries. Wait a minute, then try again.');
      else if (res.status === 400) showMessage(detail || 'Choose a different password.');
      else if (res.status === 422) {
        // The server's password rule: common words, the person's
        // name or email, too few different characters.
        const rule = data && Array.isArray(data.detail) && data.detail[0] && data.detail[0].msg;
        showMessage(rule ? String(rule).replace(/^Value error, /, '') : `Use ${NEW_PW_MIN} to 256 characters.`);
      }
      else showMessage('Something went wrong. Please try again.');
      newPwInput.focus();
    } catch (err) {
      showMessage('Could not reach the server. Check your connection and try again.');
    } finally {
      idle(submitBtn);
    }
  });

  $('newPwBackLink').addEventListener('click', () => {
    clearMessage();
    showLoginStep(pendingNewPw && pendingNewPw.email);
  });
})();
