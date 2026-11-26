// Records Archive page — Head Attorney only. The server
// enforces the same rule (routers/archive.py) and logs every export.
(function () {
  // Anyone else who opens this page by its address goes back to the requests.
  (function guardHeadAttorneyOnly(triesLeft) {
    if (!window.currentStaff) {
      if (triesLeft > 0) setTimeout(() => guardHeadAttorneyOnly(triesLeft - 1), 100);
      return;
    }
    if (window.currentStaff.role !== 'head_attorney') window.location.replace('requests-all.html');
  })(50);

  renderTopbar();
  renderSidebar('records-archive');
  renderBreadcrumb({ title: 'Records archive' });

  const quarterSelect = document.getElementById('arcQuarter');
  const yearSelect = document.getElementById('arcYear');
  const msg = document.getElementById('arcMsg');
  const preview = document.getElementById('arcPreview');
  const alertBox = document.getElementById('arcAlert');
  const downloadBtn = document.getElementById('arcDownloadBtn');
  const downloadMsg = document.getElementById('arcDownloadMsg');
  const EXPORT_TIMEOUT_MS = 5 * 60 * 1000;   // a big quarter takes a while to package

  // Default: the most recent quarter that has already ended (Philippine time).
  const nowPh = new Date(Date.now() + 8 * 3600 * 1000);
  const thisYear = nowPh.getUTCFullYear();
  const thisQuarter = Math.floor(nowPh.getUTCMonth() / 3) + 1;
  let defaultYear = thisYear, defaultQuarter = thisQuarter - 1;
  if (defaultQuarter === 0) { defaultQuarter = 4; defaultYear -= 1; }
  for (let y = thisYear; y >= Math.max(2026, thisYear - 10); y--) {
    yearSelect.add(new Option(String(y), String(y)));
  }
  if (![...yearSelect.options].some(o => o.value === String(defaultYear))) {
    yearSelect.add(new Option(String(defaultYear), String(defaultYear)));
  }
  yearSelect.value = String(defaultYear);
  quarterSelect.value = String(defaultQuarter);

  let current = null;       // the last preview shown
  let requestSeq = 0;       // ignore answers to older choices

  function formatBytes(n) {
    if (!n) return '0 MB';
    if (n < 1024 * 1024) return '< 1 MB';
    if (n < 1024 * 1024 * 1024) return (n / (1024 * 1024)).toFixed(1) + ' MB';
    return (n / (1024 * 1024 * 1024)).toFixed(2) + ' GB';
  }

  function showAlert(text, isError) {
    alertBox.textContent = text || '';
    alertBox.style.display = text ? 'block' : 'none';
    alertBox.classList.toggle('error', !!isError);
  }

  async function loadPreview() {
    const seq = ++requestSeq;
    const year = yearSelect.value, quarter = quarterSelect.value;
    msg.textContent = 'Loading…';
    downloadMsg.textContent = '';
    const res = await apiFetch(`/archive/preview?year=${encodeURIComponent(year)}&quarter=${encodeURIComponent(quarter)}`);
    if (seq !== requestSeq) return;
    if (!res.ok) {
      msg.textContent = typeof res.error === 'string' ? res.error : 'Could not load this quarter.';
      preview.style.display = 'none';
      return;
    }
    msg.textContent = '';
    current = { year: Number(year), quarter: Number(quarter), data: res.data };
    render(res.data);
  }

  function render(d) {
    preview.style.display = 'block';
    document.getElementById('arcLabel').textContent = d.label;
    document.getElementById('arcReqCount').textContent = d.request_count;
    document.getElementById('arcDocCount').textContent = d.document_count;
    document.getElementById('arcSize').textContent = formatBytes(d.total_bytes);
    document.getElementById('arcMonths').textContent =
      'Closed per month: ' + d.months.map(m => `${m.label} — ${m.count}`).join(' · ');

    if (d.in_future) {
      showAlert('This quarter has not started yet.', false);
    } else if (d.too_large) {
      showAlert(`The documents total ${formatBytes(d.total_bytes)}, more than the ${formatBytes(d.max_bytes)} a single download allows. Please contact the system administrator.`, true);
    } else if (d.quarter_not_over) {
      showAlert('This quarter is not over yet. Requests closed after today will not be in this download, so export it again after the quarter ends.', false);
    } else if (!d.request_count) {
      showAlert('No requests were closed in this quarter, so there is nothing to archive.', false);
    } else {
      showAlert('', false);
    }
    downloadBtn.disabled = !d.request_count || d.too_large;

    const tbody = document.querySelector('#arcTable tbody');
    tbody.innerHTML = d.requests.map(r => `
      <tr>
        <td>${escapeHtml(r.tracking_id)}</td>
        <td>${escapeHtml(r.status)}</td>
        <td>${escapeHtml(formatShortDate(r.closed_at))}</td>
      </tr>`).join('') || '<tr><td colspan="3">None</td></tr>';
  }

  async function download() {
    if (!current || !current.data.request_count) return;
    downloadBtn.disabled = true;
    const original = downloadBtn.textContent;
    downloadBtn.textContent = 'Preparing…';
    downloadMsg.textContent = 'Packaging the records. This can take a minute for a busy quarter.';
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), EXPORT_TIMEOUT_MS);
    try {
      // POST, not a plain link: another website can't start an export.
      const res = await fetch(`${API_BASE_URL}/archive/export`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ year: current.year, quarter: current.quarter }),
        signal: controller.signal
      });
      if (res.status === 401) { handleSessionExpired(); return; }
      if (!res.ok) {
        let detail = `Could not prepare the archive (${res.status}).`;
        try { const body = await res.json(); if (typeof body.detail === 'string') detail = body.detail; } catch (_) {}
        if (res.status === 429 && !detail.includes('export')) detail = 'Too many exports in the last hour. Please try again later.';
        downloadMsg.textContent = detail;
        return;
      }
      const blob = await res.blob();
      const match = (res.headers.get('Content-Disposition') || '').match(/filename="?([^";]+)"?/);
      const filename = match ? match[1] : `LEXIS-Archive-${current.year}-Q${current.quarter}.zip`;
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10000);
      downloadMsg.textContent = `Downloaded ${filename}. Follow the steps below.`;
    } catch (err) {
      downloadMsg.textContent = err.name === 'AbortError'
        ? 'The server took too long to prepare the archive. Please try again.'
        : 'Could not reach the Legal Office server. Please try again.';
    } finally {
      clearTimeout(timer);
      downloadBtn.textContent = original;
      downloadBtn.disabled = !current || !current.data.request_count || current.data.too_large;
    }
  }

  quarterSelect.addEventListener('change', loadPreview);
  yearSelect.addEventListener('change', loadPreview);
  downloadBtn.addEventListener('click', download);
  loadPreview();
})();
