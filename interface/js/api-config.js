// Shared API configuration for the public site (My requests and its pages).
// Change API_BASE_URL here once — when deploying, swap this to the real
// backend URL and every page picks it up.

const API_BASE_URL = '/api';

// The services offered. This built-in list is only a FALLBACK: pages call
// loadServiceTypes() to replace it with the live list from the server, so a
// service the Head Attorney adds or renames (Service Types page) shows
// up without editing this file.
const SERVICE_TYPES = [
  {code:'legal_advice',      label:'Legal Advice'},
  {code:'legal_research',    label:'Legal Research'},
  {code:'contract_review',   label:'Review of MOA/MOU, Contracts, and Similar Documents'},
  {code:'investigation',     label:'Conduct of Investigation'},
  {code:'drafting_document', label:'Drafting of Legal Documents'},
  {code:'other',             label:'Other Legal Concerns'}
];

/** Replaces SERVICE_TYPES with the live list from GET /service-types.
    Returns false and keeps the built-in list if the server can't be reached.
    An empty list is a real answer (every service hidden), so it returns true. */
async function loadServiceTypes(){
  try{
    const res = await fetch(`${API_BASE_URL}/service-types`);
    if(!res.ok) return false;
    const rows = await res.json();
    if(!Array.isArray(rows)) return false;
    SERVICE_TYPES.splice(0, SERVICE_TYPES.length,
      ...rows.map(r => ({
        code: r.code,
        label: r.name,
        description: r.description || '',
        days: r.default_processing_days || null,
        requiredDocuments: Array.isArray(r.required_documents) ? r.required_documents : []
      })));
    return true;
  }catch(err){
    return false;
  }
}

function serviceCodeToLabel(code){
  const match = SERVICE_TYPES.find(s=>s.code === code);
  return match ? match.label : code;
}