// Service Types page (the list, and editing one service).
// Head Attorney only. The server enforces
// the same rules (routers/service_types.py): services can be added and edited
// but not deleted; only the optional steps can be switched off, and not while
// a request is at that step; a new processing period, checklist or "who may
// use it" applies to new requests only.
//
// Two views: the list of services, and one service with three cards
// (Details, Documents clients upload, Who may use it online). Each card saves
// on its own (PATCH /service-types/{code}, PUT .../requirements and
// PUT .../categories) and then goes back to the list, unless another card
// still has changes that aren't saved.
(function () {
  const REQUIRED = ['Submitted', 'Processing', 'Completed'];
  const DEFAULT_STEPS = ['Submitted', 'Received', 'Under Review', 'Processing',
                         'For Attorney Review', 'Approved', 'Completed'];

  // Who may use a service, in the order shown.
  const CATEGORY_GROUPS = [
    { label: 'ESSU', items: [['essu_office', 'Office / Department'], ['essu_employee', 'Employee'], ['essu_student', 'Student']] },
    { label: 'Outside ESSU', items: [['ext_government', 'Government agency'], ['ext_private', 'Private individual or organization']] },
  ];
  const ALL_CATEGORIES = CATEGORY_GROUPS.flatMap(g => g.items.map(i => i[0]));

  // The two "doors" a service belongs to (seed.SERVICE_DOORS), in list order.
  const DOORS = [
    { door: 'service', group: 'Legal services', one: 'Legal service' },
    { door: 'complaint', group: 'Complaints and referrals', one: 'Complaint or referral' },
  ];
  const MAX_DOCS = 20;
  const NOTICE_ICONS = {ok: ICONS.check, error: ICONS.noticeError, warn: ICONS.noticeWarning, info: ICONS.noticeInfo};

  // Anyone else who opens this page by its address goes back to the requests.
  (function guardHeadAttorneyOnly(triesLeft) {
    if (!window.currentStaff) {
      if (triesLeft > 0) setTimeout(() => guardHeadAttorneyOnly(triesLeft - 1), 100);
      return;
    }
    if (window.currentStaff.role !== 'head_attorney') window.location.replace('requests-all.html');
  })(50);

  renderTopbar();
  renderSidebar('service-types');

  const $ = id => document.getElementById(id);
  const table = $('svcTable');
  const addCard = $('svcAddCard');
  const form = $('svcForm');
  const formMessage = $('svcFormMessage');
  const detailsCard = $('svcDetailsCard');
  const docsCard = $('svcDocsCard');
  const accessCard = $('svcAccessCard');
  $('svcBackBtn').innerHTML = ICONS.back;

  let services = [];        // the list, as the server last sent it
  let current = null;       // the service open in the edit view
  let docRows = [];         // the Documents card's rows while editing
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

  function daysText(d) {
    return d ? `${plural(d, 'working day')}` : 'No set period';
  }

  // "a", "a and b", "a, b and c"
  function listJoin(parts) {
    return parts.length < 2 ? parts.join('') : `${parts.slice(0, -1).join(', ')} and ${parts[parts.length - 1]}`;
  }

  function doorInfo(door) {
    return DOORS.find(d => d.door === door) || { door, group: 'Other services', one: 'Service' };
  }

  function activeDocs(svc) {
    return (svc.requirements || []).filter(r => r.is_active);
  }

  function stepsOf(svc) {
    return svc.all_steps || DEFAULT_STEPS;
  }

  // "1 required, 1 optional" for the list
  function docsSummary(svc) {
    const docs = activeDocs(svc);
    const req = docs.filter(d => d.is_required).length;
    const opt = docs.length - req;
    const parts = [];
    if (req) parts.push(`${req} required`);
    if (opt) parts.push(`${opt} optional`);
    return parts.length ? { text: parts.join(', ') } : { text: 'None', tone: 'muted' };
  }

  // "ESSU offices and employees" for the list
  function whoSummary(categories) {
    const on = new Set(categories || []);
    if (!on.size) return { text: 'Nobody (walk-ins only)', tone: 'warn' };
    if (ALL_CATEGORIES.every(c => on.has(c))) return { text: 'Everyone, including people outside ESSU' };
    const essu = [['essu_office', 'offices'], ['essu_employee', 'employees'], ['essu_student', 'students']]
      .filter(([c]) => on.has(c)).map(([, l]) => l);
    const outside = [['ext_government', 'government agencies'], ['ext_private', 'private individuals and organizations']]
      .filter(([c]) => on.has(c)).map(([, l]) => l);
    const parts = [];
    if (essu.length) parts.push(`ESSU ${listJoin(essu)}`);
    if (outside.length) parts.push(`${listJoin(outside)} outside ESSU`);
    const text = parts.join('; ');
    return { text: text.charAt(0).toUpperCase() + text.slice(1) };
  }

  /* ---------------- Messages ---------------- */
  function msgHtml(text, tone) {
    return `<p class="page-msg ${tone}"><span class="page-msg-icon">${NOTICE_ICONS[tone]}</span><span>${escapeHtml(text)}</span></p>`;
  }
  function setMsg(el, text, tone) {
    el.innerHTML = text ? msgHtml(text, tone || 'ok') : '';
  }
  // The line beside a card's Save button: saved (ok) or what went wrong (error)
  function cardStatus(card, text, tone) {
    const el = card.querySelector('.svc-status');
    el.className = `svc-status ${tone || 'ok'}`;
    el.setAttribute('role', tone === 'error' ? 'alert' : 'status');
    el.innerHTML = text ? `${tone === 'error' ? '' : ICONS.check}<span>${escapeHtml(text)}</span>` : '';
  }

  /* ---------------- The list ---------------- */
  function rowHtml(svc) {
    const docs = docsSummary(svc);
    const who = whoSummary(svc.categories);
    return `<tr class="svc-row" data-code="${escapeHtml(svc.code)}">
      <td class="svc-name-cell"><span class="svc-name">${escapeHtml(svc.name)}</span>${svc.description ? `<span class="svc-desc">${escapeHtml(svc.description)}</span>` : ''}</td>
      <td class="svc-fact${svc.default_processing_days ? '' : ' svc-muted'}" data-label="Processing period">${escapeHtml(daysText(svc.default_processing_days))}</td>
      <td class="svc-fact${docs.tone ? ' svc-' + docs.tone : ''}" data-label="Documents to upload">${escapeHtml(docs.text)}</td>
      <td class="svc-fact${who.tone ? ' svc-' + who.tone : ''}" data-label="Who may use it online">${escapeHtml(who.text)}</td>
      <td class="svc-fact svc-num" data-label="Requests">${svc.request_count || 0}</td>
      <td class="svc-act"><button type="button" class="svc-edit-link" data-code="${escapeHtml(svc.code)}" aria-label="Edit ${escapeHtml(svc.name)}">Edit</button></td>
    </tr>`;
  }

  function renderList() {
    table.querySelectorAll('tbody').forEach(tb => tb.remove());
    $('svcCount').textContent = plural(services.length, 'service');
    if (!services.length) {
      table.insertAdjacentHTML('beforeend', '<tbody><tr><td class="svc-empty" colspan="6">No services yet.</td></tr></tbody>');
      return;
    }
    // Grouped by door: legal services first, then complaints and referrals
    const doors = [...DOORS.map(d => d.door), ...new Set(services.map(s => s.door).filter(d => !DOORS.some(x => x.door === d)))];
    const html = doors.map(door => {
      const list = services.filter(s => s.door === door);
      if (!list.length) return '';
      return `<tbody>
        <tr class="svc-group"><th colspan="6" scope="colgroup">${escapeHtml(doorInfo(door).group)} · ${list.length}</th></tr>
        ${list.map(rowHtml).join('')}
      </tbody>`;
    }).join('');
    table.insertAdjacentHTML('beforeend', html);
  }

  table.addEventListener('click', e => {
    const row = e.target.closest('tr.svc-row');
    if (!row) return;
    const svc = services.find(s => s.code === row.dataset.code);
    if (svc) openEdit(svc);
  });

  async function load() {
    table.querySelectorAll('tbody').forEach(tb => tb.remove());
    table.insertAdjacentHTML('beforeend', '<tbody><tr><td class="svc-empty" colspan="6">Loading the services…</td></tr></tbody>');
    const { ok, data, error } = await apiFetch('/service-types/manage');
    if (!ok) {
      table.querySelectorAll('tbody').forEach(tb => tb.remove());
      setMsg($('svcPageMsg'), friendlyError(error) || 'Could not load the services.', 'error');
      return;
    }
    services = data;
    renderList();
  }

  /* ---------------- Add a service ---------------- */
  function closeAddForm() {
    form.reset();
    cardStatus(addCard, '', 'error');
    addCard.hidden = true;
  }

  $('svcAddBtn').addEventListener('click', () => {
    addCard.hidden = false;
    $('svcName').focus();
  });
  $('svcFormCancelBtn').addEventListener('click', closeAddForm);

  // Whole working days from 1 to 365, or blank. Returns an error text or ''.
  function daysProblem(raw) {
    if (raw === '') return '';
    const n = Number(raw);
    return Number.isInteger(n) && n >= 1 && n <= 365 ? '' : 'Enter whole working days from 1 to 365, or leave it blank.';
  }

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const name = $('svcName').value.trim();
    const daysRaw = $('svcDays').value.trim();
    const desc = $('svcDesc').value.trim();
    if (!name) { cardStatus(addCard, 'Enter the name of the service.', 'error'); $('svcName').focus(); return; }
    const problem = daysProblem(daysRaw);
    if (problem) { cardStatus(addCard, problem, 'error'); $('svcDays').focus(); return; }
    const payload = { name, description: desc || null };
    if (daysRaw !== '') payload.default_processing_days = Number(daysRaw);

    const submitBtn = $('svcFormSubmitBtn');
    submitBtn.disabled = true;
    const { ok, data, error } = await apiFetch('/service-types', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    submitBtn.disabled = false;
    if (!ok) { cardStatus(addCard, friendlyError(error), 'error'); return; }
    closeAddForm();
    setMsg($('svcPageMsg'), '');
    services.push(data);
    renderList();
    // Straight to the new service, so its documents and who may use it can be set
    openEdit(data, `“${data.name}” is added. It starts open to ESSU offices and employees, with no documents.`);
  });

  /* ---------------- Views and the browser's Back button ---------------- */
  function showView(next) {
    $('svcListView').hidden = next !== 'list';
    $('svcEditView').hidden = next !== 'edit';
    window.scrollTo(0, 0);
  }
  function pushStep() {
    if (historyPushed) return;
    history.pushState({ serviceTypesEdit: true }, '');
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

  function openEdit(svc, note) {
    current = svc;
    renderEditHeading();
    renderDetails();
    renderDocs(svc);
    renderAccess();
    setMsg($('svcPageMsg'), '');
    setMsg($('svcEditMsg'), note || '');
    showView('edit');
    pushStep();
  }

  function isDirty() {
    return !!current && (detailsDirty() || docsDirty() || accessDirty());
  }

  // Back to the list. Returns a Promise (true when it left). fromPopstate:
  // the browser has already gone back one step.
  async function leaveEdit(fromPopstate) {
    if (isDirty() && !(await confirmDialog({
      title: 'Leave without saving?',
      message: 'Changes you haven’t saved on this service will be lost.',
      confirmLabel: 'Leave without saving', cancelLabel: 'Keep editing', danger: true
    }))) return false;
    current = null;
    showView('list');
    if (!fromPopstate) dropStep();
    renderList();
    return true;
  }
  $('svcBackBtn').addEventListener('click', () => leaveEdit(false));

  window.addEventListener('beforeunload', e => {
    if (isDirty()) { e.preventDefault(); e.returnValue = ''; }
  });

  // After a card saves: the server's copy replaces ours in the list too.
  function replaceCurrent(data) {
    current = data;
    const i = services.findIndex(s => s.code === data.code);
    if (i >= 0) services[i] = data; else services.push(data);
  }

  // After a card saves: back to the list. Stays only when another card has
  // changes that aren't saved, because going back would lose them.
  async function afterSave(card, text) {
    if (isDirty()) {
      cardStatus(card, `${text} Other changes on this page are not saved yet.`);
      return;
    }
    const name = current.name;
    await leaveEdit(false);
    setMsg($('svcPageMsg'), `“${name}”: ${text}`);
  }

  function renderEditHeading() {
    const n = current.request_count || 0;
    $('svcEditTitle').textContent = current.name;
    $('svcEditSub').textContent = [
      doorInfo(current.door).one,
      n ? `${plural(n, 'request')} so far` : 'No requests yet',
      'Each card saves on its own. Changes apply only to requests sent after them.'
    ].join(' · ');
  }

  /* ---------------- Card: Details ---------------- */
  function savedDetails(svc) {
    return {
      name: svc.name,
      days: String(svc.default_processing_days || ''),
      desc: svc.description || '',
      steps: stepsOf(svc).filter(s => !REQUIRED.includes(s) && svc.steps.includes(s)),
    };
  }
  function readDetails() {
    return {
      name: $('svcInName').value,
      days: $('svcInDays').value.trim(),
      desc: $('svcInDesc').value,
      steps: [...detailsCard.querySelectorAll('.svc-step input:not(:disabled)')].filter(i => i.checked).map(i => i.value),
    };
  }
  function detailsDirty() {
    const now = readDetails();
    now.name = now.name.trim();
    now.desc = now.desc.trim();
    return JSON.stringify(now) !== JSON.stringify(savedDetails(current));
  }

  function renderDetails() {
    const svc = current;
    const steps = stepsOf(svc).map(step => {
      const locked = REQUIRED.includes(step);
      const on = locked || svc.steps.includes(step);
      return `<label class="svc-step${locked ? ' locked' : ''}"${locked ? ' title="Always part of the workflow"' : ''}>
          <input type="checkbox" class="rd-check" value="${escapeHtml(step)}"${on ? ' checked' : ''}${locked ? ' disabled' : ''}>
          <span>${escapeHtml(step)}</span>
        </label>`;
    }).join('<span class="svc-arrow" aria-hidden="true">›</span>');

    detailsCard.innerHTML = `
      <div class="svc-card-head"><h2 class="rd-card-title" id="svcDetailsTitle">Details</h2></div>
      <div class="svc-fields">
        <div class="rd-field">
          <label class="rd-label" for="svcInName">Name</label>
          <input type="text" class="rd-input" id="svcInName" maxlength="150" value="${escapeHtml(svc.name)}">
        </div>
        <div class="rd-field">
          <label class="rd-label" for="svcInDays">Processing period (working days)</label>
          <input type="number" class="rd-input" id="svcInDays" min="1" max="365" step="1" inputmode="numeric" value="${svc.default_processing_days || ''}" aria-describedby="svcInDaysHint">
          <p class="rd-hint" id="svcInDaysHint">Leave blank for no set period.</p>
        </div>
        <div class="rd-field svc-span">
          <label class="rd-label" for="svcInDesc">Description (optional)</label>
          <input type="text" class="rd-input" id="svcInDesc" maxlength="1000" value="${escapeHtml(svc.description || '')}" placeholder="A short line clients see under the service name">
        </div>
      </div>
      <div class="svc-steps-head">
        <p class="rd-label" id="svcStepsLabel">Workflow steps</p>
        <span class="svc-legend" aria-hidden="true"><i class="rd-check-mark locked"></i>Always on</span>
        <span class="svc-legend" aria-hidden="true"><i class="rd-check-mark on"></i>Can be switched off</span>
      </div>
      <div class="svc-steps" role="group" aria-labelledby="svcStepsLabel">${steps}</div>
      <p class="svc-note">A step can’t be switched off while a request is at it. Submitted, Processing and Completed are always part of the workflow.</p>
      <div class="svc-actions">
        <p class="svc-status" role="status"></p>
        <button type="button" class="btn btn-primary" id="svcSaveDetails">Save details</button>
      </div>`;
    $('svcSaveDetails').addEventListener('click', saveDetails);
  }

  async function saveDetails() {
    const btn = $('svcSaveDetails');
    const now = readDetails();
    const name = now.name.trim();
    if (!name) { cardStatus(detailsCard, 'Enter the name of the service.', 'error'); $('svcInName').focus(); return; }
    const problem = daysProblem(now.days);
    if (problem) { cardStatus(detailsCard, problem, 'error'); $('svcInDays').focus(); return; }

    const payload = { name, description: now.desc.trim(), optional_steps: now.steps };
    if (now.days === '') payload.clear_processing_days = true;
    else payload.default_processing_days = Number(now.days);

    if (now.days !== String(current.default_processing_days || '') &&
        !(await confirmDialog({
          title: 'Change the processing period?',
          message: 'Only new requests will use it. Requests already submitted keep their due date.',
          confirmLabel: 'Change period'
        }))) return;

    btn.disabled = true;
    cardStatus(detailsCard, '');
    const { ok, data, error } = await apiFetch(`/service-types/${encodeURIComponent(current.code)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    btn.disabled = false;
    if (!ok) { cardStatus(detailsCard, friendlyError(error), 'error'); return; }
    replaceCurrent(data);
    renderEditHeading();
    renderDetails();
    afterSave(detailsCard, 'Details saved.');
  }

  /* ---------------- Card: Documents clients upload ----------------
     The whole list is saved at once, in order (PUT .../requirements).
     Removing an item switches it off on the server, so documents already
     uploaded for it keep their link; removed items can be put back. */
  function docRowsFrom(svc) {
    return activeDocs(svc).map(r => ({ id: r.id, label: r.label, help_text: r.help_text || '', is_required: r.is_required }));
  }
  function readDocInputs() {
    docsCard.querySelectorAll('.svc-doc-row').forEach((rowEl, i) => {
      docRows[i].label = rowEl.querySelector('.svc-doc-label').value;
      docRows[i].help_text = rowEl.querySelector('.svc-doc-help').value;
      docRows[i].is_required = rowEl.querySelector('.svc-doc-req input').checked;
    });
  }
  function docsDirty() {
    readDocInputs();
    const clean = rows => rows.map(r => [r.id, r.label.trim(), r.help_text.trim(), r.is_required]);
    return JSON.stringify(clean(docRows)) !== JSON.stringify(clean(docRowsFrom(current)));
  }

  function renderDocs(svc) {
    docRows = docRowsFrom(svc);
    drawDocs();
  }

  function drawDocs() {
    const svc = current;
    // Items not in the list now: switched off earlier, or removed just now (not saved yet)
    const removed = (svc.requirements || []).filter(r => !docRows.some(x => x.id === r.id));
    const removedNow = removed.some(r => r.is_active);
    const rows = docRows.map((r, i) => `
      <div class="svc-doc-row" data-i="${i}">
        <span class="svc-doc-num" aria-hidden="true">${i + 1}</span>
        <div class="svc-doc-fields">
          <input type="text" class="rd-input svc-doc-label" maxlength="200" placeholder="Document name" value="${escapeHtml(r.label)}" aria-label="Document ${i + 1}: name">
          <input type="text" class="rd-input svc-doc-help" maxlength="500" placeholder="Short hint for clients (optional)" value="${escapeHtml(r.help_text)}" aria-label="Document ${i + 1}: hint for clients">
        </div>
        <label class="svc-doc-req"><input type="checkbox" class="rd-check"${r.is_required ? ' checked' : ''}>Required</label>
        <div class="svc-doc-move">
          <button type="button" class="svc-move-btn up" data-act="up"${i === 0 ? ' disabled' : ''} aria-label="Move document ${i + 1} up" title="Move up">${ICONS.chevDown}</button>
          <button type="button" class="svc-move-btn" data-act="down"${i === docRows.length - 1 ? ' disabled' : ''} aria-label="Move document ${i + 1} down" title="Move down">${ICONS.chevDown}</button>
        </div>
        <button type="button" class="svc-link-btn" data-act="remove" aria-label="Remove document ${i + 1}">Remove</button>
      </div>`).join('');

    docsCard.innerHTML = `
      <div class="svc-card-head">
        <h2 class="rd-card-title" id="svcDocsTitle">Documents clients upload</h2>
        <p class="svc-card-count">${docRows.length} of ${MAX_DOCS}</p>
      </div>
      <p class="svc-note">Clients must attach every required document before they can submit; optional ones are shown but not enforced. Keep this in line with the Citizen’s Charter.</p>
      ${docRows.length ? `<div class="svc-doc-rows">${rows}</div>`
        : '<p class="svc-docs-none">No documents. Clients can still attach other files.</p>'}
      <div class="svc-add-row">
        <button type="button" class="btn btn-secondary btn-compact" id="svcDocAdd"${docRows.length >= MAX_DOCS ? ' disabled' : ''}>+ Add a document</button>
      </div>
      ${removed.length ? `<div class="svc-removed">
          <p class="svc-removed-title">${removedNow ? 'Removed' : 'Removed earlier'}</p>
          <ul>${removed.map(r => `<li><span>${escapeHtml(r.label)}</span>
            <button type="button" class="svc-link-btn brand" data-restore="${r.id}"${docRows.length >= MAX_DOCS ? ' disabled' : ''}>Put back</button></li>`).join('')}</ul>
          <p class="svc-removed-note">Files clients already uploaded for a removed document stay on their requests.</p>
        </div>` : ''}
      <div class="svc-actions">
        <p class="svc-status" role="status"></p>
        <button type="button" class="btn btn-primary" id="svcSaveDocs">Save checklist</button>
      </div>`;

    docsCard.querySelectorAll('.svc-doc-row button[data-act]').forEach(btn => btn.addEventListener('click', () => {
      readDocInputs();
      const i = Number(btn.closest('.svc-doc-row').dataset.i);
      const act = btn.dataset.act;
      if (act === 'remove') docRows.splice(i, 1);
      if (act === 'up' && i > 0) [docRows[i - 1], docRows[i]] = [docRows[i], docRows[i - 1]];
      if (act === 'down' && i < docRows.length - 1) [docRows[i + 1], docRows[i]] = [docRows[i], docRows[i + 1]];
      drawDocs();
      // Keep the keyboard on the moved row's button (or the list, after a removal)
      const to = act === 'up' ? i - 1 : act === 'down' ? i + 1 : -1;
      const rowEl = to >= 0 ? docsCard.querySelector(`.svc-doc-row[data-i="${to}"]`) : null;
      const target = rowEl && (rowEl.querySelector(`button[data-act="${act}"]:not(:disabled)`) || rowEl.querySelector('.svc-doc-label'));
      (target || $('svcDocAdd')).focus();
    }));
    docsCard.querySelectorAll('button[data-restore]').forEach(btn => btn.addEventListener('click', () => {
      readDocInputs();
      const r = (current.requirements || []).find(x => x.id === Number(btn.dataset.restore));
      if (r && docRows.length < MAX_DOCS) docRows.push({ id: r.id, label: r.label, help_text: r.help_text || '', is_required: r.is_required });
      drawDocs();
      $('svcDocAdd').focus();
    }));
    $('svcDocAdd').addEventListener('click', () => {
      readDocInputs();
      docRows.push({ id: null, label: '', help_text: '', is_required: true });
      drawDocs();
      const inputs = docsCard.querySelectorAll('.svc-doc-label');
      inputs[inputs.length - 1].focus();
    });
    $('svcSaveDocs').addEventListener('click', saveDocs);
  }

  async function saveDocs() {
    readDocInputs();
    const items = docRows.map(r => ({ id: r.id, label: r.label.trim(), help_text: r.help_text.trim() || null, is_required: r.is_required }));
    const empty = items.findIndex(x => !x.label);
    if (empty >= 0) {
      cardStatus(docsCard, 'Every document needs a name (or remove the empty row).', 'error');
      docsCard.querySelectorAll('.svc-doc-label')[empty].focus();
      return;
    }
    const btn = $('svcSaveDocs');
    btn.disabled = true;
    cardStatus(docsCard, '');
    const { ok, data, error } = await apiFetch(`/service-types/${encodeURIComponent(current.code)}/requirements`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ items })
    });
    btn.disabled = false;
    if (!ok) { cardStatus(docsCard, friendlyError(error), 'error'); return; }
    replaceCurrent(data);
    renderDocs(data);
    afterSave(docsCard, 'Checklist saved.');
  }

  /* ---------------- Card: Who may use it online ---------------- */
  function readAccess() {
    return [...accessCard.querySelectorAll('.svc-cat input:checked')].map(i => i.value).sort();
  }
  function accessDirty() {
    return JSON.stringify(readAccess()) !== JSON.stringify([...(current.categories || [])].sort());
  }

  function renderAccess() {
    const svc = current;
    const on = new Set(svc.categories || []);
    const groups = CATEGORY_GROUPS.map(g => `
      <p class="svc-cat-group">${escapeHtml(g.label)}</p>
      ${g.items.map(([v, l]) => `<label class="svc-cat"><input type="checkbox" class="rd-check" value="${v}"${on.has(v) ? ' checked' : ''}>${escapeHtml(l)}</label>`).join('')}`).join('');
    accessCard.innerHTML = `
      <div class="svc-card-head"><h2 class="rd-card-title" id="svcAccessTitle">Who may use it online</h2></div>
      <p class="svc-note">Clients in the ticked groups see this service in My Requests. Staff can still encode walk-ins for anyone.</p>
      <div class="svc-cats" role="group" aria-labelledby="svcAccessTitle">${groups}</div>
      <p class="svc-cat-note">${svc.door === 'complaint'
        ? 'Complaints and referrals are usually open to everyone, including people outside ESSU.'
        : 'Citizen’s Charter default: ESSU offices and employees. Students may request Legal Research only.'}</p>
      <div class="svc-actions">
        <p class="svc-status" role="status"></p>
        <button type="button" class="btn btn-primary" id="svcSaveAccess">Save</button>
      </div>`;
    $('svcSaveAccess').addEventListener('click', saveAccess);
  }

  async function saveAccess() {
    const categories = readAccess();
    if (!categories.length &&
        !(await confirmDialog({
          title: 'Close this service to online requests?',
          message: 'Nobody will be able to request it online. Staff can still encode walk-ins.',
          confirmLabel: 'Save', danger: true
        }))) return;
    const btn = $('svcSaveAccess');
    btn.disabled = true;
    cardStatus(accessCard, '');
    const { ok, data, error } = await apiFetch(`/service-types/${encodeURIComponent(current.code)}/categories`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ categories })
    });
    btn.disabled = false;
    if (!ok) { cardStatus(accessCard, friendlyError(error), 'error'); return; }
    replaceCurrent(data);
    renderAccess();
    afterSave(accessCard, 'Who may use it online saved.');
  }

  // Typing again clears a card's "saved" line
  [detailsCard, docsCard, accessCard].forEach(card => card.addEventListener('input', () => {
    const el = card.querySelector('.svc-status.ok');
    if (el && el.textContent) cardStatus(card, '');
  }));

  load();
})();
