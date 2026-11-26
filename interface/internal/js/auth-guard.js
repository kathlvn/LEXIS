// Auth guard for the internal console. Loaded right after api-config.js
// on every protected page (everything except login.html). Checks for a
// valid staff session via GET /auth/me; if there isn't one, redirects to
// login.html before any protected content is shown. On success, stashes
// the current staff record on window.currentStaff so other scripts (the
// topbar, role-based UI later) can use it without a second network call.
const SERVER_DOWN = 'Could not reach the LEXIS server. Your sign-in is not the problem: check your connection, '
  + 'or ask whoever runs LEXIS to check that it is running, then try again.';

/** Replaces the hidden page with a short message and a Try again button
    (no inline handlers: the Content-Security-Policy blocks them). */
function showGuardMessage(text) {
  const box = document.createElement('div');
  box.setAttribute('role', 'alert');
  box.style.cssText = 'max-width:560px;margin:48px auto;padding:24px;font:16px/1.5 sans-serif';
  const p = document.createElement('p');
  p.textContent = text;
  const again = document.createElement('button');
  again.type = 'button';
  again.textContent = 'Try again';
  again.addEventListener('click', () => window.location.reload());
  box.append(p, again);
  document.body.replaceChildren(box);
  document.documentElement.style.visibility = 'visible';
  again.focus();
}

(async function () {
  try {
    const res = await fetch(`${API_BASE_URL}/auth/me`, { credentials: 'include' });
    if (res.status === 429) {
      // Rate limit: the session is still fine, so don't send staff to login.
      showGuardMessage('Too many requests in a short time. Wait a moment, then refresh the page.');
      return;
    }
    if (res.status >= 500) {
      // The server (or the backend behind Caddy) is down: not a sign-in
      // problem, so say so instead of sending staff to login (E2E-03).
      showGuardMessage(SERVER_DOWN);
      return;
    }
    if (!res.ok) {
      // Tell the login page why, if the session ended from inactivity.
      let idle = false;
      try { idle = /inactivity/i.test((await res.json()).detail || ''); } catch (_) { }
      window.location.replace(idle ? 'login.html?expired=1' : 'login.html');
      return;
    }
    window.currentStaff = await res.json();
  } catch (err) {
    showGuardMessage(SERVER_DOWN);   // no answer at all (E2E-03)
    return;
  }
  // components.js has already finished loading by the time this resolves
  // (network round-trip takes longer than parsing the rest of the page's
  // <script> tags), so it's safe to re-render the topbar now with the
  // real user's info before finally revealing the page.
  if (typeof renderTopbar === 'function') renderTopbar();
  if (typeof renderSidebar === 'function') renderSidebar();
  document.documentElement.style.visibility = 'visible';
})();