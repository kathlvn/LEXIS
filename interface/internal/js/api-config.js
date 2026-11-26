// Shared API configuration for the internal console.
// Mirrors interface/js/api-config.js — kept as a separate copy since the
// internal console is a self-contained "side" with its own css/js, same as
// the public site. Update both copies together if this ever changes.

const API_BASE_URL = '/api';

// Mirrors the service types seeded in backend/app/seed.py.
const SERVICE_TYPES = [
  {code:'legal_advice',      label:'Legal Advice'},
  {code:'legal_research',    label:'Legal Research'},
  {code:'contract_review',   label:'Review of MOA/MOU, Contracts, and Similar Documents'},
  {code:'investigation',     label:'Conduct of Investigation'},
  {code:'drafting_document', label:'Drafting of Legal Documents'},
  {code:'other',             label:'Other Legal Concerns'}
];

function serviceCodeToLabel(code){
  const match = SERVICE_TYPES.find(s=>s.code === code);
  return match ? match.label : code;
}

// Mirrors backend/app/seed.py's DEFAULT_STATUS_SEQUENCE — the real
// workflow every request moves through, regardless of service type (for
// now; services can get distinct sequences later without touching this).
const STATUS_SEQUENCE = [
  'Submitted', 'Received', 'Under Review', 'Processing',
  'For Attorney Review', 'Approved', 'Completed'
];

/* The server sends times in UTC without a trailing "Z". Without it the
   browser reads them as local time, so a request sent between midnight and
   8 AM (Philippine time) showed the previous day. */
function parseServerTime(iso){
  if(!iso) return new Date(NaN);
  return new Date(/[zZ]|[+-]\d\d:\d\d$/.test(iso) ? iso : iso + 'Z');
}

function formatShortDate(iso){
  const d = parseServerTime(iso);
  if(isNaN(d)) return '\u2014';
  return d.toLocaleDateString('en-US', {month:'short', day:'numeric'});
}

// "Dec 3, 2026" (Request Detail heading).
function formatMediumDate(iso){
  const d = parseServerTime(iso);
  if(isNaN(d)) return '—';
  return d.toLocaleDateString('en-US', {month:'short', day:'numeric', year:'numeric'});
}

function formatLongDate(iso){
  const d = parseServerTime(iso);
  if(isNaN(d)) return '\u2014';
  return d.toLocaleDateString('en-US', {month:'long', day:'numeric', year:'numeric'});
}

// "3:15 PM" (Request Detail History).
function formatTime(iso){
  const d = parseServerTime(iso);
  if(isNaN(d)) return '';
  return d.toLocaleTimeString('en-US', {hour:'numeric', minute:'2-digit'});
}
