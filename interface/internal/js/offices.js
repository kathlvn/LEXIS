// Offices page. Head Attorney only: the sidebar
// hides the link from everyone else, and the server refuses them too
// (routers/offices.py). Everything a client typed is escaped before it is
// shown, because the "typed by clients" names come from the public form.
(function () {
  const TYPE_LABELS = {office: 'Office', college: 'College', other: 'Other'};

  renderTopbar();
  renderSidebar('offices');
  renderBreadcrumb({title: 'Offices'});

  const $ = id => document.getElementById(id);
  const officeTable = $('officeTable');
  const typedTable = $('typedTable');
  let offices = [];
  let typed = [];
  let editingId = null;

  function friendlyError(error) {
    if (typeof error === 'string') return error;
    if (Array.isArray(error) && error[0] && error[0].msg) return error[0].msg.replace(/^Value error, /, '');
    return 'Something went wrong. Please try again.';
  }

  function pageMessage(text, isError) {
    const box = $('officePageMsg');
    box.textContent = text || '';
    box.className = 'office-page-msg' + (text ? (isError ? ' error' : ' ok') : '');
  }

  function typeOptions(selected) {
    return Object.entries(TYPE_LABELS)
      .map(([v, l]) => `<option value="${v}"${v === selected ? ' selected' : ''}>${l}</option>`).join('');
  }

  function replaceBody(table, html) {
    table.querySelectorAll('tbody').forEach(tb => tb.remove());
    const tbody = document.createElement('tbody');
    tbody.innerHTML = html;
    table.appendChild(tbody);
    return tbody;
  }

  /* ---------- loading ---------- */
  async function load() {
    const [listRes, typedRes] = await Promise.all([apiFetch('/offices/manage'), apiFetch('/offices/typed')]);
    if (!listRes.ok) {
      pageMessage(listRes.status === 403 ? 'Only the Head Attorney can manage offices.' : friendlyError(listRes.error), true);
      $('typedCard').hidden = true;
      return;
    }
    offices = listRes.data;
    typed = typedRes.ok ? typedRes.data : [];
    renderOffices();
    renderTyped();
  }

  /* ---------- the list ---------- */
  function renderOffices() {
    const active = offices.filter(o => o.is_active).length;
    renderBreadcrumb({title: 'Offices', count: active});
    if (!offices.length) {
      replaceBody(officeTable, '<tr><td colspan="5" class="holiday-empty">No offices yet. Add one above, or add what clients typed.</td></tr>');
      return;
    }
    const tbody = replaceBody(officeTable, offices.map(o => o.id === editingId ? `
      <tr data-id="${o.id}" class="office-editing">
        <td><input type="text" class="office-inline-input" id="editName" maxlength="200" value="${escapeHtml(o.name)}" aria-label="Office name"></td>
        <td><select class="office-inline-select" id="editType" aria-label="Type">${typeOptions(o.office_type)}</select></td>
        <td>${o.client_count}</td>
        <td>${o.is_active ? 'Shown' : 'Switched off'}</td>
        <td class="office-actions">
          <button type="button" class="btn btn-primary btn-compact" data-act="save">Save</button>
          <button type="button" class="btn btn-secondary btn-compact" data-act="cancel">Cancel</button>
        </td>
      </tr>` : `
      <tr data-id="${o.id}"${o.is_active ? '' : ' class="office-off"'}>
        <td>${escapeHtml(o.name)}</td>
        <td>${TYPE_LABELS[o.office_type] || escapeHtml(o.office_type)}</td>
        <td>${o.client_count}</td>
        <td><span class="client-tag ${o.is_active ? 'office-on' : 'waiting'}">${o.is_active ? 'Shown' : 'Switched off'}</span></td>
        <td class="office-actions">
          <button type="button" class="row-link-btn" data-act="edit">Rename</button>
          <button type="button" class="row-link-btn" data-act="toggle">${o.is_active ? 'Switch off' : 'Switch on'}</button>
        </td>
      </tr>`).join(''));

    tbody.querySelectorAll('button[data-act]').forEach(btn => {
      const id = Number(btn.closest('tr').dataset.id);
      btn.addEventListener('click', () => onOfficeAction(btn.dataset.act, id, btn));
    });
    const editName = $('editName');
    if (editName) {
      editName.focus();
      editName.addEventListener('keydown', e => {
        if (e.key === 'Enter') onOfficeAction('save', editingId);
        if (e.key === 'Escape') onOfficeAction('cancel', editingId);
      });
    }
  }

  async function onOfficeAction(act, id, btn) {
    const office = offices.find(o => o.id === id);
    if (!office) return;
    if (act === 'edit') { editingId = id; pageMessage(''); renderOffices(); return; }
    if (act === 'cancel') { editingId = null; renderOffices(); return; }
    let body;
    if (act === 'save') {
      const name = $('editName').value.trim();
      if (!name) { pageMessage('Enter the name of the office.', true); return; }
      body = {name, office_type: $('editType').value};
    } else {
      if (office.is_active && office.client_count &&
          !(await confirmDialog({
            title: `Switch off “${office.name}”?`,
            message: `It will be hidden from the client form. The ${office.client_count} client(s) already linked to it keep it.`,
            confirmLabel: 'Switch off', cancelLabel: 'Keep it on'
          }))) return;
      body = {is_active: !office.is_active};
    }
    if (btn) btn.disabled = true;
    const r = await apiFetch(`/offices/${id}`, {
      method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
    });
    if (btn) btn.disabled = false;
    if (!r.ok) { pageMessage(friendlyError(r.error), true); return; }
    editingId = null;
    await load();
    pageMessage(act === 'save' ? `Saved "${r.data.name}".`
      : `"${r.data.name}" is now ${r.data.is_active ? 'shown on' : 'hidden from'} the client form.`);
  }

  /* ---------- typed by clients ---------- */
  function renderTyped() {
    $('typedCount').textContent = typed.length ? `(${typed.length})` : '';
    if (!typed.length) {
      replaceBody(typedTable, '<tr><td colspan="4" class="holiday-empty">Nothing to add: every ESSU client picked an office from the list.</td></tr>');
      return;
    }
    const tbody = replaceBody(typedTable, typed.map((t, i) => `
      <tr data-index="${i}">
        <td><input type="text" class="office-inline-input" maxlength="200" value="${escapeHtml(t.name)}" aria-label="Office name to add">
          ${t.matches_office_name ? `<div class="office-hint">Already on the list as “${escapeHtml(t.matches_office_name)}”: clients will be linked to it.</div>` : ''}</td>
        <td><select class="office-inline-select" aria-label="Type">${typeOptions('office')}</select></td>
        <td>${t.client_count}</td>
        <td class="office-actions"><button type="button" class="btn btn-primary btn-compact" data-act="add">Add to list</button></td>
      </tr>`).join(''));
    tbody.querySelectorAll('button[data-act="add"]').forEach(btn => {
      btn.addEventListener('click', () => addTyped(btn));
    });
  }

  async function addTyped(btn) {
    const row = btn.closest('tr');
    const item = typed[Number(row.dataset.index)];
    const name = row.querySelector('input').value.trim();
    if (!name) { pageMessage('Enter the name of the office.', true); return; }
    btn.disabled = true;
    const r = await apiFetch('/offices/from-typed', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({typed_name: item.name, name, office_type: row.querySelector('select').value})
    });
    btn.disabled = false;
    if (!r.ok) { pageMessage(friendlyError(r.error), true); return; }
    const n = r.data.linked_clients;
    await load();
    pageMessage(`${r.data.created ? 'Added' : 'Used'} "${r.data.office.name}" and linked ${n} client${n === 1 ? '' : 's'} to it.`);
  }

  /* ---------- add an office ---------- */
  $('officeForm').addEventListener('submit', async e => {
    e.preventDefault();
    const msg = $('officeFormMsg');
    msg.textContent = '';
    const name = $('officeName').value.trim();
    if (!name) { msg.textContent = 'Enter the name of the office or college.'; return; }
    $('officeAddBtn').disabled = true;
    const r = await apiFetch('/offices', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name, office_type: $('officeType').value})
    });
    $('officeAddBtn').disabled = false;
    if (!r.ok) { msg.textContent = friendlyError(r.error); return; }
    $('officeName').value = '';
    await load();
    pageMessage(`Added "${r.data.name}". Clients can now choose it on My Requests.`);
  });
  $('officeName').addEventListener('input', () => { $('officeFormMsg').textContent = ''; });

  load();
})();