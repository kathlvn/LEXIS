// Staff "New Request": encode a walk-in or paper request.
// The server (POST /requests/staff-encoded) checks everything again; this
// page only catches mistakes early and says what to fix.
// Layout: three cards + the Summary card.
(function () {
  renderTopbar();
  renderSidebar('requests-all');

  const $ = id => document.getElementById(id);
  // The back button: a real link to All requests (components.js).
  $('nrHeading').insertAdjacentHTML('afterbegin', backButtonHtml({href: 'requests-all.html', label: 'Back to All requests'}));
  wireBackButton($('nrHeading').querySelector('.back-btn'));

  const ALLOWED = ['.pdf', '.doc', '.docx', '.jpg', '.jpeg', '.png'];
  const MAX_BYTES = 20 * 1024 * 1024;
  const ESSU = ['essu_office', 'essu_employee', 'essu_student'];
  const CATEGORY_HINTS = {
    essu_office: 'Office or department heads and key officials requesting for their office.',
    essu_employee: 'ESSU personnel.',
    essu_student: 'Students may request Legal Research, or file a complaint or referral.',
    ext_government: 'Persons and agencies outside ESSU may file a complaint or referral only.',
    ext_private: 'Persons outside ESSU may file a complaint or referral only.'
  };
  // The client's verification, as a pill.
  const STATUS = {
    unverified: {cls: 'tone-neutral',  text: 'Not verified'},
    pending:    {cls: 'tone-progress', text: 'Letter to check'},
    verified:   {cls: 'tone-done',     text: 'Verified'},
    expired:    {cls: 'tone-progress', text: 'Verification expired'},
    declined:   {cls: 'tone-stopped',  text: 'Declined'},
    revoked:    {cls: 'tone-stopped',  text: 'Revoked'}
  };
  // Why the endorsement letter is asked for (the reason under the letter).
  const LETTER_REASONS = {
    declined: 'the Legal Office declined this client before',
    revoked: 'this client’s verification was revoked',
    expired: 'this client’s verification has expired'
  };
  const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
  const NAME_FIELDS = ['nrLastName', 'nrFirstName', 'nrMiddleName', 'nrNoMiddleName', 'nrNameExt'];
  const CATEGORY_FIELDS = ['nrCategory', 'nrOffice', 'nrOfficeOther', 'nrOrganization'];
  let services = [];
  let officeCount = 0;
  let found = null;                      // the existing client for the email/number typed, or null
  let lastLookup = '';
  let pendingLookup = Promise.resolve();

  apiFetch('/service-types').then(r => {
    if(!r.ok) return;
    services = r.data;
    fillServices();
  });
  const officesReady = apiFetch('/portal/offices').then(r => {
    if(!r.ok || !Array.isArray(r.data)) return;
    const others = $('nrOffice').querySelector('option[value="other"]');
    officeCount = r.data.length;
    r.data.forEach(o => $('nrOffice').insertBefore(new Option(o.name, String(o.id)), others));   // the list first, "Others" last
  });

  const selectedService = () => services.find(s => s.code === $('nrService').value);
  const letterNeeded = () => !$('nrLetterField').hidden;

  /** Only the services the client's category may use ("who may use it" on Service Types). */
  function fillServices(){
    const cat = $('nrCategory').value;
    const select = $('nrService');
    const kept = select.value;
    select.length = 0;
    select.add(new Option(cat ? 'Select a service' : "Choose the client's category first", ''));
    services.filter(s => cat && (s.categories || []).includes(cat)).forEach(s => select.add(new Option(s.name, s.code)));
    select.value = [...select.options].some(o => o.value === kept) ? kept : '';
    showDocs();
  }

  function showDocs(){
    const svc = selectedService();
    const hint = $('nrDocsHint');
    const docs = (svc && svc.required_documents) || [];
    hint.hidden = !docs.length;
    hint.innerHTML = docs.length
      ? '<p class="nr-docs-title">Documents to ask for</p><ul>' + docs.map(d => `<li>${escapeHtml(d)}</li>`).join('') + '</ul>' : '';
    // The endorsement letter, legal services only (never complaints/referrals): an existing
    // client as the server says (needs_letter); a new ESSU client always (not yet verified).
    const legalService = !!svc && svc.door !== 'complaint';
    const needsLetter = legalService && (found ? found.needs_letter : ESSU.includes($('nrCategory').value));
    $('nrLetterField').hidden = !needsLetter;
    if(needsLetter){
      const reason = (found && LETTER_REASONS[found.verification_status]) || 'this client isn’t verified yet';
      $('nrLetterWhy').textContent = `Needed because ${reason} and ${svc.name} is a legal service (Citizen’s Charter). ` +
        'Meeting the client in person doesn’t replace it.';
    } else if($('nrLetter').value){
      $('nrLetter').value = '';
    }
    renderLetter();
    updateSummary();
  }

  /* ---------- chosen files ---------- */
  function sizeText(bytes){
    return bytes >= 1024 * 1024 ? `${(bytes / (1024 * 1024)).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
  }

  function fileRowHtml(file, index){
    const ext = (file.name.split('.').pop() || '').toUpperCase();
    return `
      <div class="nr-file">
        <span class="nr-file-icon" aria-hidden="true">${ICONS.documents}</span>
        <span class="nr-file-name">
          <span class="nr-file-title">${escapeHtml(file.name)}</span>
          <span class="nr-file-meta">${escapeHtml(ext)} · ${sizeText(file.size)}</span>
        </span>
        <button type="button" class="btn btn-tertiary btn-compact" data-remove="${index}" aria-label="Remove ${escapeHtml(file.name)}">Remove</button>
      </div>`;
  }

  function pickerHtml(id, label, emptyText, labelledBy){
    return `
      <div class="nr-picker">
        <button type="button" class="btn btn-secondary btn-compact" data-choose="${id}" aria-describedby="${labelledBy}">${ICONS.uploadSmall} ${label}</button>
        ${emptyText ? `<span class="nr-picker-text">${emptyText}</span>` : ''}
      </div>`;
  }

  /** Takes one file out of a file box (the browser has no "remove one" of its own). */
  function removeFile(input, index){
    try{
      const keep = new DataTransfer();
      [...input.files].forEach((f, i) => { if(i !== index) keep.items.add(f); });
      input.files = keep.files;
    }catch(_){
      input.value = '';     // an older browser: clear them all, staff choose again
    }
  }

  function renderLetter(){
    const file = $('nrLetter').files[0];
    $('nrLetterFile').innerHTML = file ? fileRowHtml(file, 0) : pickerHtml('nrLetter', 'Choose file', 'No file chosen', 'nrLetterLabel');
  }

  function renderFiles(){
    const files = [...$('nrFiles').files];
    $('nrFilesList').innerHTML = files.map(fileRowHtml).join('') +
      pickerHtml('nrFiles', files.length ? 'Add more files' : 'Choose files', files.length ? '' : 'No files chosen', 'nrFilesLabel');
  }

  /** "Add more files" keeps the ones already chosen. */
  let filesBefore = [];
  $('nrFiles').addEventListener('click', () => { filesBefore = [...$('nrFiles').files]; });
  $('nrFiles').addEventListener('change', () => {
    if(filesBefore.length){
      try{
        const all = new DataTransfer();
        [...filesBefore, ...$('nrFiles').files].forEach(f => all.items.add(f));
        $('nrFiles').files = all.files;
      }catch(_){ /* older browser: the new choice replaces the old one */ }
    }
    filesBefore = [];
    renderFiles();
  });
  $('nrLetter').addEventListener('change', () => { renderLetter(); updateSummary(); });

  [['nrLetterFile', 'nrLetter', renderLetter], ['nrFilesList', 'nrFiles', renderFiles]].forEach(([boxId, inputId, render]) => {
    $(boxId).addEventListener('click', e => {
      const choose = e.target.closest('[data-choose]');
      if(choose){ $(inputId).click(); return; }
      const remove = e.target.closest('[data-remove]');
      if(!remove) return;
      removeFile($(inputId), Number(remove.dataset.remove));
      render();
      updateSummary();
      const next = $(boxId).querySelector('[data-remove], [data-choose]');   // keep focus in the box
      if(next) next.focus();
    });
  });

  /* ---------- looking the client up ---------- */
  /** The lookup key for the contact typed, or '' while it isn't a complete email/number yet. */
  function contactQuery(){
    if($('nrNoEmail').checked){
      const phone = $('nrPhone').value.replace(/[\s-]/g, '');
      return /^\+?\d{7,15}$/.test(phone) ? 'phone=' + encodeURIComponent(phone) : '';
    }
    const email = $('nrEmail').value.trim().toLowerCase();
    return EMAIL_RE.test(email) ? 'email=' + encodeURIComponent(email) : '';
  }

  /** Looks the client up once per contact; the newest contact typed always wins. */
  function lookUp(){
    const query = contactQuery();
    if(query === lastLookup) return pendingLookup;
    lastLookup = query;
    if(!query){ showFound(null); return pendingLookup = Promise.resolve(); }
    // After the office list, so a saved office is selected from it, not added twice.
    pendingLookup = Promise.all([apiFetch('/clients/lookup?' + query), officesReady]).then(([r]) => {
      if(query !== lastLookup) return;        // the contact changed while this was loading
      showFound(r.ok && r.data && r.data.found ? r.data : null);
    });
    return pendingLookup;
  }

  function clearProfile(){
    ['nrLastName', 'nrFirstName', 'nrMiddleName', 'nrNameExt', 'nrOfficeOther', 'nrOrganization', 'nrPosition']
      .forEach(id => { $(id).value = ''; });
    $('nrNoMiddleName').checked = false;
    $('nrCategory').value = '';
    $('nrOffice').value = '';
  }

  /** An existing client: show who it is, fill in the saved details and lock them (the server
   *  never overwrites them anyway). Blanks on older records stay editable, in the same groups
   *  the server fills them: the name parts, the category with office/agency, the position. */
  function showFound(client){
    const wasFound = !!found;
    found = client;
    [...NAME_FIELDS, ...CATEGORY_FIELDS, 'nrPosition'].forEach(id => { $(id).disabled = false; });
    $('nrNameExt').placeholder = 'Jr., Sr., III';
    const box = $('nrFound');
    if(!client){
      box.hidden = true;
      box.innerHTML = '';
      if(wasFound) clearProfile();   // never carry one client's details over to another
      toggleNoMiddleName();
      updateCategoryFields();
      return;
    }
    clearProfile();   // start clean: a field this client has blank must not keep someone else's value
    const names = !!(client.last_name && client.first_name);
    if(names){
      $('nrLastName').value = client.last_name;
      $('nrFirstName').value = client.first_name;
      $('nrMiddleName').value = client.middle_name || '';
      $('nrNoMiddleName').checked = !client.middle_name;
      $('nrNameExt').value = client.name_extension || '';
    }
    if(client.client_category){
      $('nrCategory').value = client.client_category;
      if(ESSU.includes(client.client_category)){
        const id = client.office_id ? String(client.office_id) : '';
        if(id && ![...$('nrOffice').options].some(o => o.value === id)){
          // An office since taken off the list: still show it for this client.
          $('nrOffice').insertBefore(new Option(client.office_name, id), $('nrOffice').querySelector('option[value="other"]'));
        }
        $('nrOffice').value = id || (client.office_name ? 'other' : '');
        $('nrOfficeOther').value = id ? '' : (client.office_name || '');
      } else {
        $('nrOrganization').value = client.organization || '';
      }
    }
    if(client.position) $('nrPosition').value = client.position;
    toggleNoMiddleName();
    updateCategoryFields();
    // Lock the category group only when it is complete, so a required blank can still be filled.
    const cat = client.client_category;
    const categoryDone = !!cat && (ESSU.includes(cat) ? !!(client.office_id || client.office_name)
                                                       : cat !== 'ext_government' || !!client.organization);
    if(names){
      NAME_FIELDS.forEach(id => { $(id).disabled = true; });
      $('nrNameExt').placeholder = 'None';     // a saved blank, not a field to fill
    }
    if(categoryDone) CATEGORY_FIELDS.forEach(id => { $(id).disabled = true; });
    if(client.position) $('nrPosition').disabled = true;

    const contact = $('nrNoEmail').checked ? 'number' : 'email';
    const status = STATUS[client.verification_status] || STATUS.unverified;
    const open = client.open_requests === 1 ? '1 open request' : `${client.open_requests} open requests`;
    const facts = [client.client_category_label, client.office_name || client.organization, open].filter(Boolean);
    box.innerHTML =
      `<p class="nr-found-title">This ${contact} belongs to an existing client: ${escapeHtml(client.formal_name)}</p>` +
      `<div class="nr-found-facts"><span class="status-label ${status.cls}">${escapeHtml(status.text)}</span>` +
      `<span>${escapeHtml(facts.join(' · '))}</span></div>` +
      `<p class="nr-found-note">Their saved details are filled in below and can't be changed here. To correct them, use the client panel on one of their requests.` +
      (client.details_complete ? '' : ' Fill in the details still missing.') + '</p>' +
      (client.cap_message ? `<p class="nr-found-warn">${escapeHtml(client.cap_message)}</p>` : '');
    box.classList.toggle('warn', !!client.cap_message);
    box.hidden = false;
    updateSummary();
  }

  function updateCategoryFields(){
    const cat = $('nrCategory').value;
    const essu = ESSU.includes(cat);
    $('nrOfficeField').hidden = !essu;
    $('nrOrgField').hidden = !cat || essu;
    $('nrOrgOptional').hidden = cat === 'ext_government';
    $('nrPositionText').textContent = cat === 'essu_student' ? 'Year and program' : 'Position';
    $('nrCategoryHint').textContent = CATEGORY_HINTS[cat] || '';
    toggleOfficeOther();
    fillServices();
  }

  /** "Others (please specify)" shows the text box; with no offices listed yet, it is the only choice. */
  function toggleOfficeOther(){
    if(officeCount === 0 && !$('nrOffice').value) $('nrOffice').value = 'other';
    $('nrOfficeOther').hidden = $('nrOffice').value !== 'other';
  }

  function toggleNoMiddleName(){
    const none = $('nrNoMiddleName').checked;
    $('nrMiddleName').disabled = none;
    if(none) $('nrMiddleName').value = '';
  }

  /** Email is the main contact; the number box takes the hint's place only for a client with no email. */
  function toggleNoEmail(){
    const none = $('nrNoEmail').checked;
    $('nrEmail').disabled = none;
    if(none) $('nrEmail').value = ''; else $('nrPhone').value = '';
    $('nrContactHint').hidden = none;
    $('nrPhoneField').hidden = !none;
    if(none) $('nrPhone').focus();
    lookUp();
    updateSummary();
  }

  /* ---------- the Summary card ---------- */
  function updateSummary(){
    const svc = selectedService();
    const noEmail = $('nrNoEmail').checked;
    const none = text => `<span class="nr-sum-none">${text}</span>`;
    const icon = (name, text) => `<span class="nr-sum-icon">${ICONS[name]}${text}</span>`;
    const typedName = [$('nrFirstName').value.trim(), $('nrLastName').value.trim()].filter(Boolean).join(' ');
    const client = found ? `${escapeHtml(found.formal_name)}, existing client`
                 : typedName ? `${escapeHtml(typedName)}, new client` : none('Not entered yet');
    const phone = $('nrPhone').value.trim();
    const updates = noEmail ? (phone ? `By phone, ${escapeHtml(phone)}` : 'By phone') : 'By email';
    const days = svc && svc.default_processing_days;
    const period = !svc ? none('Choose a service') : days ? `${days} working day${days === 1 ? '' : 's'}` : 'No set period';
    const letter = !letterNeeded() ? none(svc ? 'Not needed' : 'Depends on the service')
                 : $('nrLetter').files.length ? icon('check', 'Attached') : icon('noticeWarning', 'Needed, not attached yet');
    const consent = $('nrConsent').checked ? icon('check', 'Confirmed') : icon('noticeWarning', 'Not confirmed yet');
    const row = (label, value) => `<div class="nr-sum-row"><dt>${label}</dt><dd>${value}</dd></div>`;
    $('nrSummary').innerHTML =
      row('Client', client) +
      row('Updates', updates) +
      row('Legal service', svc ? escapeHtml(svc.name) : none('Not chosen yet')) +
      row('Set period', period) +
      row('Endorsement letter', letter) +
      row('Data Privacy consent', consent);
    $('nrSummaryNote').textContent = noEmail
      ? 'LEXIS records that you encoded this request. The next page has the slip to print for the client.'
      : 'LEXIS records that you encoded this request and emails the Request ID to the client.';
  }

  // Look the client up as soon as the contact is complete (after a short pause in typing).
  let typingTimer = null;
  ['nrEmail', 'nrPhone'].forEach(id => {
    $(id).addEventListener('input', () => { clearTimeout(typingTimer); typingTimer = setTimeout(lookUp, 500); });
    $(id).addEventListener('change', lookUp);
  });
  $('nrCategory').addEventListener('change', updateCategoryFields);
  $('nrOffice').addEventListener('change', toggleOfficeOther);
  $('nrNoMiddleName').addEventListener('change', toggleNoMiddleName);
  $('nrNoEmail').addEventListener('change', toggleNoEmail);
  $('nrService').addEventListener('change', showDocs);
  $('nrDesc').addEventListener('input', () => { $('nrDescCount').textContent = $('nrDesc').value.length; });
  ['nrFirstName', 'nrLastName', 'nrPhone'].forEach(id => $(id).addEventListener('input', updateSummary));
  $('nrConsent').addEventListener('change', updateSummary);

  function problems(){
    const list = [];   // [message, id of the field to fix]
    const add = (text, field) => list.push([text, field]);
    const cat = $('nrCategory').value;
    const essu = ESSU.includes(cat);
    const noEmail = $('nrNoEmail').checked;
    const email = $('nrEmail').value.trim();
    const phone = $('nrPhone').value.replace(/[\s-]/g, '');
    if(!noEmail && !email) add('Email address, or tick "The client has no email address"', 'nrEmail');
    if(!noEmail && email && !EMAIL_RE.test(email)) add('A valid email address', 'nrEmail');
    if(noEmail && !phone) add('Contact number', 'nrPhone');
    if(noEmail && phone && !/^\+?\d{7,15}$/.test(phone)) add('A valid contact number', 'nrPhone');
    if(found && found.cap_message) add(found.cap_message, 'nrEmail');
    if(!$('nrLastName').value.trim()) add('Last name', 'nrLastName');
    if(!$('nrFirstName').value.trim()) add('First name', 'nrFirstName');
    if(!$('nrNoMiddleName').checked && !$('nrMiddleName').value.trim()) add('Middle name, or tick "No middle name"', 'nrMiddleName');
    if(!cat) add('Requesting as', 'nrCategory');
    if(essu && !$('nrOffice').value) add('Office or college', 'nrOffice');
    if(essu && $('nrOffice').value === 'other' && !$('nrOfficeOther').value.trim()) add('The office or college, typed under "Others"', 'nrOfficeOther');
    if(cat === 'ext_government' && !$('nrOrganization').value.trim()) add('Agency or organization', 'nrOrganization');
    if(!$('nrService').value) add('Legal service', 'nrService');
    if(letterNeeded() && !$('nrLetter').files.length) add('Endorsement letter from the office head', 'nrLetter');
    if(!$('nrDesc').value.trim()) add('Description', 'nrDesc');
    [...$('nrFiles').files, ...$('nrLetter').files].forEach(f => {
      const ext = '.' + (f.name.split('.').pop() || '').toLowerCase();
      const where = [...$('nrLetter').files].includes(f) ? 'nrLetter' : 'nrFiles';
      if(!ALLOWED.includes(ext)) add(`${f.name}: this file type is not allowed`, where);
      else if(f.size > MAX_BYTES) add(`${f.name}: larger than 20 MB`, where);
      else if(!f.size) add(`${f.name}: the file is empty`, where);
    });
    if(!$('nrConsent').checked) add('Confirmation that the client signed the Data Privacy consent', 'nrConsent');
    return list;
  }

  /** The list of problems at the top of the form. Each item is a message, or
      [message, field id]: those become links to the field, and the fields are
      marked invalid. Focus moves to the list so a screen reader reads it.
  */
  function showErrors(items, heading){
    const box = $('nrErrors');
    document.querySelectorAll('#nrForm [aria-invalid="true"]').forEach(el => el.removeAttribute('aria-invalid'));
    box.hidden = !items.length;
    box.innerHTML = items.length
      ? `<p class="nr-errors-title">${escapeHtml(heading || 'Please complete the following:')}</p><ul>${items.map(i => {
          const [text, field] = Array.isArray(i) ? i : [i, null];
          return field ? `<li><a href="#${field}" data-field="${field}">${escapeHtml(text)}</a></li>` : `<li>${escapeHtml(text)}</li>`;
        }).join('')}</ul>` : '';
    if(!items.length) return;
    items.forEach(i => { if(Array.isArray(i) && $(i[1])) $(i[1]).setAttribute('aria-invalid', 'true'); });
    box.tabIndex = -1;
    box.focus({preventScroll: true});
    box.scrollIntoView({behavior: 'smooth', block: 'center'});
  }

  // A link in the list moves to its field (no inline handlers: the CSP blocks them).
  $('nrErrors').addEventListener('click', e => {
    const link = e.target.closest('a[data-field]');
    if(!link || !$(link.dataset.field)) return;
    e.preventDefault();
    $(link.dataset.field).focus();
  });

  function friendly(error){
    if(typeof error === 'string') return error;
    if(Array.isArray(error) && error[0] && error[0].msg) return error[0].msg.replace(/^Value error, /, '');
    return 'Could not create the request. Please try again.';
  }

  $('nrForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    clearTimeout(typingTimer);
    await lookUp();   // the form must reflect the client for the contact as typed now
    const list = problems();
    if(list.length){ showErrors(list); return; }
    showErrors([]);
    const btn = $('nrSubmit');
    btn.disabled = true;
    btn.textContent = 'Creating…';
    const cat = $('nrCategory').value;
    const essu = ESSU.includes(cat);
    const officeValue = $('nrOffice').value;
    const notListed = officeValue === 'other';
    const noMiddle = $('nrNoMiddleName').checked;
    const noEmail = $('nrNoEmail').checked;
    const email = noEmail ? '' : $('nrEmail').value.trim();
    // A form upload: the details as JSON, plus the endorsement letter when one is attached,
    // so the server saves the request and the letter together or not at all.
    const submission = new FormData();
    submission.append('payload', JSON.stringify({
      requester: {   // the same shape as the client's own profile
        last_name: $('nrLastName').value.trim(),
        first_name: $('nrFirstName').value.trim(),
        middle_name: noMiddle ? null : $('nrMiddleName').value.trim() || null,
        no_middle_name: noMiddle,
        name_extension: $('nrNameExt').value.trim() || null,
        client_category: cat,
        office_id: essu && !notListed && officeValue ? Number(officeValue) : null,
        office_other: essu && notListed ? $('nrOfficeOther').value.trim() || null : null,
        organization: !essu ? $('nrOrganization').value.trim() || null : null,
        position: $('nrPosition').value.trim() || null,
        email: email || null,
        phone: noEmail ? $('nrPhone').value.trim() || null : null
      },
      service_type_code: $('nrService').value,
      description: $('nrDesc').value.trim(),
      consent_confirmed: $('nrConsent').checked
    }));
    const letter = letterNeeded() ? $('nrLetter').files[0] : null;
    if(letter) submission.append('endorsement_letter', letter);
    const r = await apiFetch('/requests/staff-encoded', {method: 'POST', body: submission, timeoutMs: 120000});
    if(!r.ok){
      btn.disabled = false;
      btn.textContent = 'Create request';
      showErrors([friendly(r.error)], 'The request was not created:');
      return;
    }
    const trackingId = r.data.tracking_id;
    const files = [...$('nrFiles').files];
    let uploadNote = '';
    if(files.length){
      const form = new FormData();
      files.forEach(f => form.append('files', f));
      const up = await apiFetch(`/requests/${encodeURIComponent(trackingId)}/documents/client-by-staff`, {
        method: 'POST', body: form, timeoutMs: 120000
      });
      if(!up.ok) uploadNote = 'failed';
    }
    // Request Detail shows "Request ... was created" (showCreatedMessage).
    window.location.href = 'request-detail.html?id=' + encodeURIComponent(trackingId) + '&created=1'
      + (email ? '&emailed=1' : '') + (uploadNote ? '&tab=documents&docs=failed' : '');
  });

  renderLetter();
  renderFiles();
  updateSummary();
})();
