// Articles page: staff write, edit,
// publish and hide the articles shown on the public Home page
// (routers/articles.py). Two views on one page: the list, and Write +
// Preview (or the preview alone for articles this staff member may not
// edit). Every staff member sees every article. Which buttons appear comes
// from the server's can_edit / can_publish / can_hide, and the server checks
// again on every action. Everything staff typed is escaped before it is shown.
(function () {
  const STATUS_LABELS = {draft: 'Draft', published: 'Published', hidden: 'Hidden'};
  const STATUS_TONES = {draft: 'tone-neutral', published: 'tone-done', hidden: 'tone-progress'};
  const BODY_MAX = 20000;
  const NOTICE_ICONS = {ok: ICONS.check, error: ICONS.noticeError, warn: ICONS.noticeWarning, info: ICONS.noticeInfo};

  renderTopbar();
  renderSidebar('articles');

  const $ = id => document.getElementById(id);
  const table = $('articleTable');
  let articles = [];
  let filter = '';
  let mode = 'list';         // 'list', 'edit' or 'read'
  let editing = null;        // the article in the editor (null = a new one)
  let saved = {title: '', body: ''};
  let historyPushed = false; // the editor added a step to the browser history

  $('artHowtoIcon').innerHTML = ICONS.noticeInfo;
  $('artHowtoChev').innerHTML = ICONS.chevron;
  $('artBackBtn').innerHTML = ICONS.back;

  function friendlyError(error) {
    if (typeof error === 'string') return error;
    if (Array.isArray(error) && error[0] && error[0].msg) return error[0].msg.replace(/^Value error, /, '');
    return 'Something went wrong. Please try again.';
  }

  // "6 Nov 2026", Philippine time. Built from parts: en-GB would
  // write "Sept".
  function shortDate(iso) {
    const d = parseServerTime(iso);
    if (isNaN(d)) return '—';
    const part = {};
    new Intl.DateTimeFormat('en-US', {day: 'numeric', month: 'short', year: 'numeric', timeZone: 'Asia/Manila'})
      .formatToParts(d).forEach(p => { part[p.type] = p.value; });
    return `${part.day} ${part.month} ${part.year}`;
  }

  function plural(n, one, many) { return `${n} ${n === 1 ? one : (many || one + 's')}`; }

  // A coloured banner with an icon. text is plain text (never read as HTML).
  function noticeHtml(text, tone) {
    return `<span class="page-msg-icon">${NOTICE_ICONS[tone]}</span><span>${escapeHtml(text)}</span>`;
  }

  function pageMessage(text, isError) {
    $('articlePageMsg').innerHTML = text
      ? `<p class="page-msg ${isError ? 'error' : 'ok'}">${noticeHtml(text, isError ? 'error' : 'ok')}</p>` : '';
  }

  function statusLabel(status) {
    return `<span class="status-label ${STATUS_TONES[status] || 'tone-neutral'}">${escapeHtml(STATUS_LABELS[status] || status)}</span>`;
  }

  /* ---------- the list ---------- */
  async function load() {
    const r = await apiFetch('/articles');
    if (!r.ok) { pageMessage(friendlyError(r.error), true); return; }
    articles = r.data;
    renderList();
  }

  function renderList() {
    const counts = {'': articles.length, draft: 0, published: 0, hidden: 0};
    articles.forEach(a => { if (a.status in counts) counts[a.status] += 1; });
    document.querySelectorAll('.art-chip-n').forEach(el => { el.textContent = counts[el.dataset.count]; });
    document.querySelectorAll('.art-filter .rd-chip').forEach(chip => {
      const on = chip.dataset.filter === filter;
      chip.classList.toggle('current', on);
      chip.setAttribute('aria-pressed', String(on));
    });

    const rows = filter ? articles.filter(a => a.status === filter) : articles;
    table.querySelectorAll('tbody').forEach(tb => tb.remove());
    const tbody = document.createElement('tbody');
    if (!rows.length) {
      tbody.innerHTML = `<tr><td colspan="6" class="art-empty">${articles.length
        ? 'No articles with this status.' : 'No articles yet. Use “Write an article” to start one.'}</td></tr>`;
    } else {
      tbody.innerHTML = rows.map(a => `
        <tr data-id="${a.id}">
          <td><button type="button" class="art-open" data-act="open">${escapeHtml(a.title)}</button></td>
          <td>${statusLabel(a.status)}</td>
          <td>${escapeHtml(a.author_name)}</td>
          <td>${escapeHtml(shortDate(a.updated_at))}${a.updated_by_name
            ? `<span class="art-sub">by ${escapeHtml(a.updated_by_name)}</span>` : ''}</td>
          <td>${a.published_at ? escapeHtml(shortDate(a.published_at)) : '<span class="art-none">—</span>'}</td>
          <td><div class="art-acts">
            <button type="button" class="art-act" data-act="open">${a.can_edit ? 'Edit' : 'Read'}</button>
            ${a.can_publish ? `<button type="button" class="art-act" data-act="publish">${a.status === 'hidden' ? 'Publish again' : 'Publish'}</button>` : ''}
            ${a.can_hide ? '<button type="button" class="art-act" data-act="hide">Hide</button>' : ''}
          </div></td>
        </tr>`).join('');
    }
    table.appendChild(tbody);
    tbody.querySelectorAll('button[data-act]').forEach(btn => {
      const id = Number(btn.closest('tr').dataset.id);
      btn.addEventListener('click', () => onAction(btn.dataset.act, id, btn));
    });

    const foot = $('articleFoot');
    foot.hidden = !articles.length;
    foot.textContent = `${plural(articles.length, 'article')} · ${plural(counts.draft, 'draft')}, `
      + `${counts.published} published, ${counts.hidden} hidden. Articles are never deleted.`;
  }

  async function onAction(act, id, btn) {
    const item = articles.find(a => a.id === id);
    if (!item) return;
    if (act === 'open') { openArticle(id); return; }
    const ok = await confirmDialog(act === 'publish'
      ? {title: `Publish “${item.title}”?`, message: 'It will be shown to the public on the Home page.',
         confirmLabel: 'Publish'}
      : {title: `Hide “${item.title}”?`, message: 'It will be taken off the public site. You can publish it again later.',
         confirmLabel: 'Hide article'});
    if (!ok) return;
    btn.disabled = true;
    const r = await apiFetch(`/articles/${id}/${act}`, {method: 'POST'});
    btn.disabled = false;
    if (!r.ok) { pageMessage(friendlyError(r.error), true); return; }
    await load();
    pageMessage(act === 'publish' ? `“${r.data.title}” is now published.`
                                  : `“${r.data.title}” is now hidden from the public site.`);
  }

  document.querySelectorAll('.art-filter .rd-chip').forEach(chip =>
    chip.addEventListener('click', () => { filter = chip.dataset.filter; renderList(); }));

  /* ---------- switching views ---------- */
  function showView(next) {
    mode = next;
    $('artListView').hidden = next !== 'list';
    $('artEditView').hidden = next === 'list';
    window.scrollTo(0, 0);
  }

  // The browser's Back button returns from the editor to the list, instead
  // of leaving the page. Nothing about the article goes into the address.
  function pushEditorStep() {
    if (historyPushed) return;
    history.pushState({articlesEditor: true}, '');
    historyPushed = true;
  }
  function dropEditorStep() {
    if (!historyPushed) return;
    historyPushed = false;
    history.back();          // its popstate is ignored (historyPushed is already false)
  }
  window.addEventListener('popstate', async () => {
    if (!historyPushed) return;
    historyPushed = false;
    if (mode === 'list') return;
    if (!(await closeEditor(false))) pushEditorStep();   // "Keep editing": stay, and keep the step
  });

  /* ---------- editor ---------- */
  function isDirty() {
    return mode === 'edit' &&
      ($('articleTitle').value !== saved.title || $('articleBody').value !== saved.body);
  }

  // Back to the list. Returns a Promise (true when closed). With force there
  // is no question, so it closes at once.
  async function closeEditor(force) {
    if (!force && isDirty() && !(await confirmDialog({
      title: 'Close without saving?', message: 'Your changes to this article will be lost.',
      confirmLabel: 'Close without saving', cancelLabel: 'Keep editing', danger: true
    }))) return false;
    editing = null;
    showView('list');
    dropEditorStep();
    return true;
  }

  function updateCount() {
    const n = $('articleBody').value.length;
    $('articleCount').textContent = `${n.toLocaleString('en-US')} / ${BODY_MAX.toLocaleString('en-US')}`;
  }

  // Same formatter as the public article page; it escapes everything first.
  function renderPreview(title, body, publishedAt) {
    $('previewTitle').textContent = title.trim() || '(No title yet)';
    $('previewDate').textContent = articleDate(publishedAt || new Date().toISOString());
    $('previewBody').innerHTML = body.trim()
      ? formatArticle(body) : '<p class="art-none">The text will show here as you write.</p>';
  }

  function livePreview() {
    renderPreview($('articleTitle').value, $('articleBody').value, editing && editing.published_at);
  }

  function setNotice(text, tone) {
    const box = $('editorNotice');
    box.hidden = !text;
    box.className = 'page-msg ' + (tone || '');
    box.innerHTML = text ? noticeHtml(text, tone) : '';
  }

  // The parts the editor and the read-only view share: heading, status, meta.
  function fillHeading(a, heading) {
    $('editorTitle').textContent = heading;
    const status = $('editorStatus');
    status.hidden = !a;
    if (a) {
      status.className = `status-label ${STATUS_TONES[a.status] || 'tone-neutral'}`;
      status.textContent = STATUS_LABELS[a.status] || a.status;
    }
    $('editorMeta').textContent = a
      ? `Written by ${a.author_name} on ${shortDate(a.created_at)}. Last edited ${shortDate(a.updated_at)}`
        + (a.updated_by_name ? ` by ${a.updated_by_name}.` : '.')
      : 'New articles start as drafts. Only the Head Attorney can publish them.';
  }

  function setReadOnly(readOnly) {
    $('artWrite').hidden = readOnly;
    $('articleSaveBtn').hidden = readOnly;
    $('artColumns').classList.toggle('read', readOnly);
    $('artPreview').classList.toggle('side-sticky', !readOnly);   // alone, it simply follows the page
    $('artPreviewLabel').textContent = readOnly ? 'How the public sees it' : 'Preview · how the public will see it';
    $('artPreviewLive').hidden = readOnly;
  }

  function fillEditor(a) {
    editing = a;
    saved = {title: a ? a.title : '', body: a ? a.body : ''};
    $('articleTitle').value = saved.title;
    $('articleBody').value = saved.body;
    $('articleFormMsg').textContent = '';
    fillHeading(a, a ? 'Edit article' : 'Write an article');
    $('articleSaveBtn').textContent = !a || a.status === 'draft' ? 'Save draft' : 'Save changes';
    if (a && a.status === 'published') {
      setNotice('This article is on the public site. Your changes are shown to the public as soon as you save.', 'warn');
    } else if (a && a.status === 'hidden') {
      setNotice('This article is hidden. Your changes are shown to the public only if it is published again.', 'info');
    } else {
      setNotice('');
    }
    setReadOnly(false);
    updateCount();
    livePreview();
    showView('edit');
    pushEditorStep();
    $('articleTitle').focus({preventScroll: true});
  }

  function showReader(a) {
    editing = null;
    fillHeading(a, 'Read article');
    setNotice(a.status === 'draft'
      ? 'Only its writer and the Head Attorney can edit a draft.'
      : 'Only the Head Attorney can edit a published or hidden article.', 'info');
    setReadOnly(true);
    renderPreview(a.title, a.body, a.published_at);
    showView('read');
    pushEditorStep();
    $('artBackBtn').focus({preventScroll: true});
  }

  async function openArticle(id) {
    pageMessage('');
    const r = await apiFetch(`/articles/${id}`);
    if (!r.ok) { pageMessage(friendlyError(r.error), true); return; }
    if (r.data.can_edit) fillEditor(r.data);
    else showReader(r.data);
  }

  $('articleNewBtn').addEventListener('click', () => { pageMessage(''); fillEditor(null); });
  $('articleCloseBtn').addEventListener('click', () => closeEditor(false));
  $('artBackBtn').addEventListener('click', () => closeEditor(false));
  $('articleBody').addEventListener('input', updateCount);
  ['articleTitle', 'articleBody'].forEach(id =>
    $(id).addEventListener('input', () => { $('articleFormMsg').textContent = ''; livePreview(); }));

  $('articleForm').addEventListener('submit', async e => {
    e.preventDefault();
    if (mode !== 'edit') return;
    const msg = $('articleFormMsg');
    const title = $('articleTitle').value.trim();
    const body = $('articleBody').value;
    if (!title) { msg.textContent = 'Enter a title.'; $('articleTitle').focus(); return; }
    if (!body.trim()) { msg.textContent = 'Write the article text.'; $('articleBody').focus(); return; }
    $('articleSaveBtn').disabled = true;
    const isNew = !editing;
    const r = await apiFetch(isNew ? '/articles' : `/articles/${editing.id}`, {
      method: isNew ? 'POST' : 'PATCH', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({title, body})
    });
    $('articleSaveBtn').disabled = false;
    if (!r.ok) { msg.textContent = friendlyError(r.error); return; }
    closeEditor(true);             // saved: back to the list, where the article now is
    await load();
    pageMessage(`“${r.data.title}” is saved${r.data.status === 'draft' ? ' as a draft' : ''}.`);
  });

  window.addEventListener('beforeunload', e => {
    if (isDirty()) { e.preventDefault(); e.returnValue = ''; }
  });

  load();
})();
