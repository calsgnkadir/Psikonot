/* records.js — PsikoNot UI Records Module */
import { apiFetch, patientId, formatTs, emptyState, ROLE_LABEL, escapeHtml, getCurrentUser } from './utils.js';
import { stashPayload } from './actions.js';
import { addNotification } from './notifications.js';

export let allRecords = [];
export let recordTypes = [];

/* -- Record type labels (no icons) ------------------- */
export const TYPE_LABELS = {
  client_profile:     'Client Profile',
  session_note:       'Session Note',
  session_transcript: 'Session Transcript',
  treatment_plan:     'Treatment Plan',
  homework:       'Homework',
  consent_form:   'Consent Form',
  document:       'Document',
  other:          'Other',
  correction:     'Correction',
  unknown:        'Unknown',
};

export async function loadRecordTypes() {
  try {
    const d = await apiFetch('/api/record-types');
    recordTypes = d.types;
    
    const sel = document.getElementById('rec-type');
    if (sel) {
      sel.innerHTML = '<option value="">Select...</option>';
      d.types.forEach(t => { 
        const o = document.createElement('option'); 
        o.value = t.value; 
        o.textContent = t.label; 
        sel.appendChild(o); 
      });
    }
    
    const typeFilter = document.getElementById('rec-type-filter');
    if (typeFilter) {
      typeFilter.innerHTML = '<option value="">All Types</option>';
      d.types.forEach(t => { 
        const o = document.createElement('option'); 
        o.value = t.value; 
        o.textContent = t.label; 
        typeFilter.appendChild(o); 
      });
    }
  } catch(e) { 
    console.error(e); 
  }
}

export async function loadRecords() {
  const container = document.getElementById('all-records');
  if (!container) return;
  const pid = patientId();
  if (!pid) {
    // Asking for /records/null would only return a confusing 403.
    allRecords = [];
    container.innerHTML = emptyState('No client selected. Choose one on the Dashboard.');
    return;
  }
  container.innerHTML = '<div class="loading-spinner">Loading...</div>';
  try {
    const d = await apiFetch(`/api/records/${pid}`);
    allRecords = d.records;
    renderAllRecords();
  } catch(e) { 
    container.innerHTML = `<div class="alert alert-error">${escapeHtml(e.message)}</div>`;
  }
}

export function filterRecords() { 
  renderAllRecords(); 
}

export function renderAllRecords() {
  const qEl = document.getElementById('rec-search');
  const q = qEl ? (qEl.value || '').toLowerCase() : '';
  const typeEl = document.getElementById('rec-type-filter');
  const type = typeEl ? typeEl.value : '';
  
  const filtered = allRecords.filter(r => {
    // The list endpoint carries record metadata on the record itself; `r.data`
    // holds only the clinical fields specific to the record type.
    const txt = ((r.title || '') + ' ' + (r.doctor_name || '') + ' ' + (r.institution || '')).toLowerCase();
    const matchQ = !q || txt.includes(q);
    const matchT = !type || (r.record_type === type);
    return matchQ && matchT;
  });
  
  const container = document.getElementById('all-records');
  if (container) {
    container.innerHTML = filtered.length ? filtered.map(renderRecordCard).join('') : emptyState('No records found');
  }
}

export function renderRecordCard(r) {
  const type = r.record_type || 'unknown';
  const typeAbbr = (TYPE_LABELS[type] || type).substring(0, 3).toUpperCase();
  const date = r.record_date ? new Date(r.record_date).toLocaleDateString('en-GB') : formatTs(r.timestamp);
  const encBadge = r.is_protected ? '<span class="badge badge-encrypted">ENCRYPTED</span>' : '';
  const corrBadge = r.is_correction ? '<span class="badge badge-private">CORRECTION</span>' : '';
  const correctedBadge = r.is_corrected ? '<span class="badge" style="background:rgba(245,158,11,0.12);color:#f59e0b;border:1px solid rgba(245,158,11,0.3)">CORRECTED</span>' : '';
  const typLabel = escapeHtml(recordTypes.find(t => t.value === type)?.label || TYPE_LABELS[type] || type);
  return `
  <div class="record-card ${r.is_protected?'is-encrypted':''} ${r.is_correction?'is-correction':''}"
       data-action="open-record" data-arg="${r.block_index}">
    <div class="record-type-icon record-type-text">${escapeHtml(typeAbbr)}</div>
    <div class="record-main">
      <div class="record-title">${escapeHtml(r.title)}</div>
      <div class="record-meta">${escapeHtml(r.doctor_name||'—')} · ${escapeHtml(r.institution||'—')}</div>
      <div class="record-badges">${encBadge}${corrBadge}${correctedBadge}
        <span class="badge badge-private" style="background:rgba(255,255,255,0.05);color:var(--muted)">${typLabel}</span>
      </div>
    </div>
    <div class="record-right">
      <div class="record-date">${escapeHtml(date)}</div>
      <div class="record-hash">#${r.block_index} · ${escapeHtml(r.hash_preview)}</div>
    </div>
  </div>`;
}

export function renderAttachmentHtml(fileName, fileType, fileData, patientIdVal, blockIndexVal, passwordVal = null) {
  if (!fileData && !fileName) return '';

  // The file name, type and data all come from whoever uploaded the record, so
  // each is escaped before it reaches innerHTML — including inside the img
  // src, where a quote in the type or data would break out of the attribute.
  const name = escapeHtml(fileName || '');
  const type = escapeHtml(fileType || 'Unknown');
  const data = escapeHtml(fileData || '');

  let previewHtml = '';
  // Check if we have off-chain hash instead of inline base64 data
  const hasOffchain = !fileData && fileName;

  if (hasOffchain) {
    previewHtml = `
      <div style="margin-top: 14px; border: 1px solid var(--border); border-radius: 6px; padding: 12px; background: rgba(255,255,255,0.02); display:flex; justify-content:space-between; align-items:center;">
        <div style="text-align:left">
          <div style="font-weight:600; font-size:13px; color:#fff;">📄 ${name} [Off-chain Blockchain Locked]</div>
          <div style="font-size:11px; color:var(--muted); margin-top:2px;">Type: ${type}</div>
        </div>
        <button class="btn btn-gold btn-sm" data-action="download-offchain" data-arg="${stashPayload({ patientId: patientIdVal, blockIndex: blockIndexVal, password: passwordVal || '', fileName })}">Download Secure File</button>
      </div>
    `;
  } else if (fileType && fileType.startsWith('image/')) {
    previewHtml = `
      <div style="margin-top: 14px; border: 1px solid var(--border); border-radius: 6px; padding: 10px; background: rgba(255,255,255,0.02);">
        <div style="font-size:11px; color:var(--muted); margin-bottom:8px;">IMAGE ATTACHMENT: ${name}</div>
        <img src="data:${type};base64,${data}" style="max-width:100%; max-height:240px; border-radius:4px; display:block; margin:0 auto;" />
        <button class="btn btn-gold btn-sm" style="margin-top:10px; width:100%" data-action="download-attachment" data-arg="${stashPayload({ fileName, fileType, fileData })}">Download Image</button>
      </div>
    `;
  } else {
    previewHtml = `
      <div style="margin-top: 14px; border: 1px solid var(--border); border-radius: 6px; padding: 12px; background: rgba(255,255,255,0.02); display:flex; justify-content:space-between; align-items:center;">
        <div style="text-align:left">
          <div style="font-weight:600; font-size:13px; color:#fff;">📄 ${name}</div>
          <div style="font-size:11px; color:var(--muted); margin-top:2px;">Type: ${type}</div>
        </div>
        <button class="btn btn-gold btn-sm" data-action="download-attachment" data-arg="${stashPayload({ fileName, fileType, fileData })}">Download File</button>
      </div>
    `;
  }
  return `<hr style="border-color:var(--border);margin:16px 0"><h4 style="color:var(--muted-hi);font-size:12px;margin-bottom:12px">ATTACHMENT</h4>${previewHtml}`;
}

export function downloadBase64File(fileName, fileType, fileData) {
  const linkSource = `data:${fileType};base64,${fileData}`; // xss-reviewed: set as an <a> href through the DOM, never parsed as HTML
  const downloadLink = document.createElement("a");
  downloadLink.href = linkSource;
  downloadLink.download = fileName;
  downloadLink.click();
}

export async function downloadOffchainFile(patientIdVal, blockIndexVal, passwordVal, fileName) {
  try {
    let url = `/api/records/offchain/download/${patientIdVal}/${blockIndexVal}`;
    if (passwordVal) {
      url += `?password=${encodeURIComponent(passwordVal)}`;
    }
    // Authenticated by the httpOnly access_token cookie (same-origin); a GET
    // needs no CSRF header.
    const res = await fetch(url, { credentials: 'same-origin' });
    if (!res.ok) {
      const json = await res.json().catch(() => ({}));
      throw new Error(json.detail || 'Download failed');
    }
    const blob = await res.blob();
    const blobUrl = URL.createObjectURL(blob);
    const downloadLink = document.createElement("a");
    downloadLink.href = blobUrl;
    downloadLink.download = fileName;
    downloadLink.click();
    URL.revokeObjectURL(blobUrl);
  } catch (ex) {
    alert("File download failed: " + ex.message);
  }
}

// Renders a record's type-specific fields in the add-record form's order and
// wording (minus the form's input hints, e.g. "(YYYY-MM-DD)").
export function renderDataFields(data, recordType) {
  if (!data || typeof data !== 'object') return '';
  const fields = DYNAMIC_FIELDS[recordType] || [];
  const labels = Object.fromEntries(fields.map(f => [f.id, f.label.replace(/\s*\(.*\)$/, '')]));
  const known = fields.map(f => f.id).filter(k => k in data);
  const keys = [...known, ...Object.keys(data).filter(k => !known.includes(k))];
  return keys.map(k => {
    const v = data[k];
    return `<div class="modal-field"><div class="modal-field-label">${escapeHtml(labels[k] || k)}</div><div class="modal-field-value">${escapeHtml(typeof v === 'object' ? JSON.stringify(v) : String(v))}</div></div>`;
  }).join('');
}


export async function openRecord(idx) {
  const r = allRecords.find(x => x.block_index === idx);
  if (!r) return;

  if (r.is_protected) {
    document.getElementById('modal-content').innerHTML = `
      <h2 style="font-size:20px;font-weight:700;margin-bottom:20px">Encrypted Record</h2>
      <p style="margin-bottom:16px;color:var(--muted)">This record is encrypted with AES-256. Enter the password to decrypt:</p>
      <div id="modal-decrypt-error" class="alert alert-error" style="display:none;margin-bottom:12px"></div>
      <div class="field-group">
        <label>Record Password</label>
        <div class="input-wrap">
          <input type="password" id="modal-decrypt-password" placeholder="••••••••" style="width:100%;margin-bottom:12px;padding:8px 12px;border-radius:4px;border:1px solid var(--border);background:rgba(255,255,255,0.05);color:white;" />
        </div>
      </div>
      <button class="btn btn-gold btn-full" data-action="decrypt-record" data-arg="${idx}">Decrypt &amp; View</button>
      <hr style="border-color:var(--border);margin:16px 0">
      <div class="modal-field"><div class="modal-field-label">Block #</div><div class="modal-field-value">${r.block_index}</div></div>
      <div class="modal-field"><div class="modal-field-label">Block Hash</div><div class="modal-field-value mono">${escapeHtml(r.hash_preview)}</div></div>
    `;
    document.getElementById('modal-overlay').classList.add('open');
    return;
  }

  // Record metadata lives on the record; `r.data` holds the clinical fields.
  const clinical = r.data || {};
  const typLabel = recordTypes.find(t => t.value === r.record_type)?.label || r.record_type || '—';

  const dataFields = renderDataFields(clinical, r.record_type);

  const attachmentHtml = renderAttachmentHtml(r.file_name, r.file_type, r.file_data, patientId(), r.block_index);

  document.getElementById('modal-content').innerHTML = `
    <h2 style="font-size:20px;font-weight:700;margin-bottom:20px">${escapeHtml(r.title)}</h2>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px">
      <div class="modal-field"><div class="modal-field-label">Block #</div><div class="modal-field-value">${r.block_index}</div></div>
      <div class="modal-field"><div class="modal-field-label">Record Type</div><div class="modal-field-value">${escapeHtml(typLabel)}</div></div>
      <div class="modal-field"><div class="modal-field-label">Practitioner</div><div class="modal-field-value">${escapeHtml(r.doctor_name||'—')}</div></div>
      <div class="modal-field"><div class="modal-field-label">Institution</div><div class="modal-field-value">${escapeHtml(r.institution||'—')}</div></div>
      <div class="modal-field"><div class="modal-field-label">Date</div><div class="modal-field-value">${escapeHtml(r.record_date||'—')}</div></div>
    </div>
    ${dataFields ? `<hr style="border-color:var(--border);margin:16px 0"><h4 style="color:var(--muted-hi);font-size:12px;margin-bottom:12px">DATA FIELDS</h4><div style="display:grid;grid-template-columns:1fr 1fr;gap:14px">${dataFields}</div>` : ''}
    ${r.notes ? `<div class="modal-field" style="margin-top:14px"><div class="modal-field-label">Notes</div><div class="modal-field-value">${escapeHtml(r.notes)}</div></div>` : ''}
    ${attachmentHtml}
    ${r.is_corrected && r.correction ? `
      <div class="alert" style="background:rgba(245,158,11,0.08);border:1px solid rgba(245,158,11,0.3);margin-top:16px;line-height:1.6">
        <strong style="color:#f59e0b">✎ This record was corrected</strong><br>
        <span style="font-size:12px;color:var(--muted-hi)">By <strong>${escapeHtml(r.correction.corrected_by||'—')}</strong>${r.correction.corrected_at ? ' on ' + new Date(r.correction.corrected_at*1000).toLocaleString('en-GB') : ''}</span><br>
        <span style="font-size:12px">Reason: ${escapeHtml(r.correction.reason||'—')}</span>
        <div style="margin-top:10px"><button class="btn btn-ghost btn-sm" data-action="view-original" data-arg="${r.block_index}">View original version</button></div>
        <div id="original-version-view" style="margin-top:12px"></div>
      </div>` : ''}
    <hr style="border-color:var(--border);margin:16px 0">
    <div class="modal-field"><div class="modal-field-label">Block Hash</div><div class="modal-field-value mono">${escapeHtml(r.hash_preview)}</div></div>
    <div class="modal-field"><div class="modal-field-label">Created By</div><div class="modal-field-value">${escapeHtml(r.created_by||'—')}</div></div>
    <div style="margin-top:16px; display:flex; gap:8px; flex-wrap:wrap;">
      <button class="btn btn-ghost btn-sm" data-action="verify-proof" data-arg="${r.block_index}">Verify Merkle Inclusion Proof</button>
      ${(getCurrentUser()||{}).role === 'practitioner' ? `<button class="btn btn-ghost btn-sm" data-action="correct-record" data-arg="${r.block_index}">Correct this record</button>` : ''}
      <div id="merkle-proof-result" style="width:100%;margin-top:12px"></div>
    </div>
  `;
  document.getElementById('modal-overlay').classList.add('open');
}

export async function decryptRecord(idx) {
  const pwdEl = document.getElementById('modal-decrypt-password');
  const errEl = document.getElementById('modal-decrypt-error');
  if (!pwdEl) return;
  const pwd = pwdEl.value;
  if (!pwd) {
    errEl.textContent = 'Please enter the password.';
    errEl.style.display = 'block';
    return;
  }
  errEl.style.display = 'none';

  try {
    const res = await apiFetch(`/api/records/${patientId()}/${idx}/decrypt`, {
      method: 'POST',
      body: JSON.stringify({ password: pwd })
    });
    const decData = res.data;

    const r = allRecords.find(x => x.block_index === idx);
    const d = decData || {};
    const typLabel = recordTypes.find(t => t.value === d.record_type)?.label || d.record_type || '—';

    const dataFields = renderDataFields(d.data, d.record_type);

    const attachmentHtml = renderAttachmentHtml(d.file_name, d.file_type, d.file_data, patientId(), idx, pwd);

    // Every value is escaped, as in openRecord(). This view used to interpolate
    // the decrypted fields raw — a stored XSS: a payload in a confidential
    // record's title or notes ran in the browser of whoever decrypted it.
    document.getElementById('modal-content').innerHTML = `
      <h2 style="font-size:20px;font-weight:700;margin-bottom:20px">${escapeHtml(d.title || (r ? r.title : ''))}</h2>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px">
        <div class="modal-field"><div class="modal-field-label">Block #</div><div class="modal-field-value">${escapeHtml(String(idx))}</div></div>
        <div class="modal-field"><div class="modal-field-label">Record Type</div><div class="modal-field-value">${escapeHtml(typLabel)}</div></div>
        <div class="modal-field"><div class="modal-field-label">Practitioner</div><div class="modal-field-value">${escapeHtml(d.doctor_name||'—')}</div></div>
        <div class="modal-field"><div class="modal-field-label">Institution</div><div class="modal-field-value">${escapeHtml(d.institution||'—')}</div></div>
        <div class="modal-field"><div class="modal-field-label">Date</div><div class="modal-field-value">${escapeHtml(d.record_date||'—')}</div></div>
      </div>
      ${dataFields ? `<hr style="border-color:var(--border);margin:16px 0"><h4 style="color:var(--muted-hi);font-size:12px;margin-bottom:12px">DATA FIELDS</h4><div style="display:grid;grid-template-columns:1fr 1fr;gap:14px">${dataFields}</div>` : ''}
      ${d.notes ? `<div class="modal-field" style="margin-top:14px"><div class="modal-field-label">Notes</div><div class="modal-field-value">${escapeHtml(d.notes)}</div></div>` : ''}
      ${attachmentHtml}
      <hr style="border-color:var(--border);margin:16px 0">
      <div class="modal-field"><div class="modal-field-label">Block Hash</div><div class="modal-field-value mono">${escapeHtml(r ? r.hash_preview : '—')}</div></div>
      <div class="modal-field"><div class="modal-field-label">Created By</div><div class="modal-field-value">${escapeHtml(d.created_by||'—')}</div></div>
    `;
  } catch (ex) {
    errEl.textContent = ex.message;
    errEl.style.display = 'block';
  }
}

export async function verifyMerkleProof(blockIndex) {
  const box = document.getElementById('merkle-proof-result');
  if (!box) return;
  box.innerHTML = '<div class="loading-spinner">Recomputing Merkle path…</div>';

  try {
    const res = await apiFetch(`/api/records/proof/${patientId()}/${blockIndex}`);
    const steps = (res.proof || []).length;
    box.innerHTML = `
      <div class="alert ${res.is_valid ? 'alert-success' : 'alert-error'}" style="line-height:1.6">
        <strong>${res.is_valid ? 'Proof valid — this block is part of the anchored chain' : 'Proof INVALID — block does not reconstruct the anchored root'}</strong>
        <div style="font-size:11px; font-family:var(--font-mono); margin-top:8px; word-break:break-all;">
          Block hash: ${escapeHtml(res.block_hash || '—')}<br>
          Merkle root: ${escapeHtml(res.merkle_root || '—')}<br>
          Path length: ${steps} sibling hash${steps === 1 ? '' : 'es'}
        </div>
      </div>`;
  } catch (e) {
    box.innerHTML = `<div class="alert alert-error">${escapeHtml(e.message)}</div>`;
  }
}

export async function viewOriginalVersion(blockIndex) {
  const box = document.getElementById('original-version-view');
  if (!box) return;
  box.innerHTML = '<div class="loading-spinner">Loading original version…</div>';
  try {
    const res = await apiFetch(`/api/records/${patientId()}/${blockIndex}?version=original`);
    const d = res.data || {};
    const clinical = d.data || {};
    const rows = Object.entries(clinical)
      .filter(([k]) => k !== 'annotations')
      .map(([k, v]) => `<div style="font-size:12px"><span style="color:var(--muted)">${escapeHtml(k)}:</span> ${escapeHtml(String(v))}</div>`)
      .join('');
    box.innerHTML = `
      <div style="border:1px solid var(--border);border-radius:6px;padding:10px 12px;background:rgba(255,255,255,0.02)">
        <div style="font-size:11px;color:var(--muted-hi);text-transform:uppercase;margin-bottom:6px">Original (superseded) — still on the chain</div>
        <div style="font-weight:600;font-size:13px;margin-bottom:6px">${escapeHtml(d.title || '—')}</div>
        ${rows || '<div style="font-size:12px;color:var(--muted)">No clinical fields.</div>'}
        ${d.notes ? `<div style="font-size:12px;margin-top:6px"><span style="color:var(--muted)">notes:</span> ${escapeHtml(d.notes)}</div>` : ''}
      </div>`;
  } catch (e) {
    box.innerHTML = `<div class="alert alert-error">${escapeHtml(e.message)}</div>`;
  }
}

export function renderCorrectionForm(blockIndex) {
  const r = allRecords.find(x => x.block_index === blockIndex);
  if (!r) return;
  const clinical = r.data || {};
  const fieldRows = Object.entries(clinical)
    .filter(([k]) => k !== 'annotations')
    .map(([k, v]) => `
      <div class="field-group">
        <label>${escapeHtml(k)}</label>
        <input type="text" data-corr-field="${escapeHtml(k)}" value="${escapeHtml(String(v))}" />
      </div>`).join('');

  document.getElementById('modal-content').innerHTML = `
    <h2 style="font-size:18px;font-weight:700;margin-bottom:6px">Correct record #${r.block_index}</h2>
    <p style="color:var(--muted);font-size:13px;margin-bottom:16px;line-height:1.5">
      The original record is never changed — your correction is appended as a new block and both stay on the chain.
    </p>
    <div id="correction-error" class="alert alert-error" style="display:none;margin-bottom:12px"></div>
    <div class="field-group">
      <label>Title</label>
      <input type="text" id="corr-title" value="${escapeHtml(r.title || '')}" />
    </div>
    ${fieldRows}
    <div class="field-group">
      <label>Notes</label>
      <input type="text" id="corr-notes" value="${escapeHtml(r.notes || '')}" />
    </div>
    <div class="field-group">
      <label>Reason for correction <span class="req">*</span></label>
      <input type="text" id="corr-reason" placeholder="e.g. dosage was recorded incorrectly" />
    </div>
    <div style="display:flex;gap:8px;margin-top:16px">
      <button class="btn btn-gold" data-action="submit-correction" data-arg="${r.block_index}">Append Correction</button>
      <button class="btn btn-ghost" data-action="open-record" data-arg="${r.block_index}">Cancel</button>
    </div>
  `;
}

export async function submitCorrection(blockIndex) {
  const r = allRecords.find(x => x.block_index === blockIndex);
  if (!r) return;
  const errEl = document.getElementById('correction-error');
  const reason = (document.getElementById('corr-reason').value || '').trim();
  if (!reason) {
    errEl.textContent = 'A correction reason is required.';
    errEl.style.display = 'block';
    return;
  }

  const data = {};
  document.querySelectorAll('[data-corr-field]').forEach(inp => {
    data[inp.getAttribute('data-corr-field')] = inp.value;
  });

  const corrected_data = {
    title:        document.getElementById('corr-title').value,
    record_type:  r.record_type,
    doctor_name:  r.doctor_name || '',
    institution:  r.institution || '',
    record_date:  r.record_date || '',
    data,
    notes:        document.getElementById('corr-notes').value,
  };

  try {
    const res = await apiFetch(`/api/records/${patientId()}/${blockIndex}/correct`, {
      method: 'POST',
      body: JSON.stringify({ corrected_data, reason }),
    });
    addNotification('Record Corrected', res.message || 'Correction appended.', 'success');
    closeModal();
    if (window.loadRecords) await window.loadRecords();
  } catch (e) {
    errEl.textContent = e.message;
    errEl.style.display = 'block';
  }
}

export function closeModal() {
  document.getElementById('modal-overlay').classList.remove('open');
}

/* -- Add Record Dynamic Fields --------------------------------- */
// Must match the field names of the schemas in backend/schemas/requests.py.
export const DYNAMIC_FIELDS = {
  session_note:   [{id:'session_number',label:'Session Number'},{id:'duration_min',label:'Duration (min)'},{id:'session_format',label:'Format (In-person/Online)'},{id:'summary',label:'Summary'}],
  client_profile: [{id:'presenting_problem',label:'Presenting problem',multiline:true},{id:'characteristics',label:'Characteristics',multiline:true},{id:'background',label:'Background',multiline:true}],
  session_transcript: [{id:'session_number',label:'Session Number'},{id:'transcript',label:'Transcript',multiline:true}],
  treatment_plan: [{id:'goals',label:'Goals'},{id:'approach',label:'Approach (e.g. CBT)'},{id:'planned_sessions',label:'Planned Sessions'}],
  homework:       [{id:'task',label:'Task'},{id:'due_date',label:'Due Date (YYYY-MM-DD)'}],
  consent_form:   [{id:'form_type',label:'Form Type'},{id:'signed_date',label:'Signed Date (YYYY-MM-DD)'}],
};

export function renderDynamicFields() {
  const type = document.getElementById('rec-type').value;
  const container = document.getElementById('dynamic-fields');
  if (!container) return;
  const fields = DYNAMIC_FIELDS[type] || [];
  let html = fields.map(f =>
    f.multiline
      ? `<div class="field-group span-2">
          <label>${f.label}</label>
          <textarea id="dyn-${f.id}" rows="${f.id === 'transcript' ? 8 : 3}" placeholder="${f.label}..."></textarea>
        </div>`
      : `<div class="field-group">
          <label>${f.label}</label>
          <input type="text" id="dyn-${f.id}" placeholder="${f.label}...">
        </div>`
  ).join('');

  container.innerHTML = html;
  applyTranscriptRule(type);
}

// A transcript is what was said, word for word: the form suggests locking it
// with a password on top of the at-rest encryption.
function applyTranscriptRule(type) {
  const hint = document.getElementById('transcript-hint');
  if (hint) hint.style.display = type === 'session_transcript' ? 'block' : 'none';
}

export function initRecordsListeners() {
  // Confidential Checkbox Toggle
  const confCheckbox = document.getElementById('rec-confidential');
  if (confCheckbox) {
    confCheckbox.addEventListener('change', function() {
      const group = document.getElementById('confidential-password-group');
      if (group) {
        group.style.display = this.checked ? 'block' : 'none';
      }
      const pwInput = document.getElementById('rec-confidential-password');
      if (pwInput && !this.checked) pwInput.value = '';
    });
  }

  // Add Record Form Submit
  const addRecordForm = document.getElementById('add-record-form');
  if (addRecordForm) {
    addRecordForm.addEventListener('submit', async e => {
      e.preventDefault();
      const errEl = document.getElementById('add-error');
      const okEl  = document.getElementById('add-success');
      errEl.style.display = 'none'; okEl.style.display = 'none';

      const type = document.getElementById('rec-type').value;
      const isConfidential = document.getElementById('rec-confidential').checked;
      const confPassword = document.getElementById('rec-confidential-password').value.trim();

      if (isConfidential && !confPassword) {
        errEl.textContent = 'A password is required for confidential (encrypted) records.';
        errEl.style.display = 'block';
        return;
      }

      const dynData = {};
      (DYNAMIC_FIELDS[type]||[]).forEach(f => {
        const el = document.getElementById('dyn-'+f.id);
        if (el && el.value.trim()) dynData[f.id] = el.value.trim();
      });

      const fileInput = document.getElementById('rec-file');
      let file_name = null;
      let file_type = null;
      let file_data = null;

      if (fileInput && fileInput.files && fileInput.files.length > 0) {
        const file = fileInput.files[0];
        if (file.size > 2 * 1024 * 1024) {
          errEl.textContent = 'Attachment size exceeds the 2MB limit.';
          errEl.style.display = 'block';
          return;
        }
        file_name = file.name;
        file_type = file.type;

        const getBase64 = (f) => new Promise((resolve, reject) => {
          const reader = new FileReader();
          reader.readAsDataURL(f);
          reader.onload = () => resolve(reader.result);
          reader.onerror = error => reject(error);
        });

        try {
          const dataUrl = await getBase64(file);
          file_data = dataUrl.split(',')[1];
        } catch(err) {
          errEl.textContent = 'Failed to read file attachment.';
          errEl.style.display = 'block';
          return;
        }
      }

      const payload = {
        patient_id:             document.getElementById('rec-patient-id').value.trim(),
        record_type:            type,
        title:                  document.getElementById('rec-title').value.trim(),
        doctor_name:            document.getElementById('rec-doctor').value.trim(),
        institution:            document.getElementById('rec-institution').value.trim(),
        record_date:            document.getElementById('rec-date').value,
        is_confidential:        isConfidential,
        confidential_password:  isConfidential ? confPassword : null,
        data:                   dynData,
        notes:                  document.getElementById('rec-notes').value.trim(),
        file_name:              file_name,
        file_type:              file_type,
        file_data:              file_data,
      };

      const btn = document.getElementById('btn-add-rec');
      btn.disabled = true; btn.textContent = 'Saving...';
      try {
        const res = await apiFetch('/api/records', { method:'POST', body: JSON.stringify(payload) });
        okEl.textContent = `${res.message} (Block #${res.block_index})`;
        okEl.style.display = 'block';
        
        addNotification('Record Saved', `New ${payload.record_type} record successfully added to block #${res.block_index}.`, 'success');

        document.getElementById('add-record-form').reset();
        const fileNameLabel = document.getElementById('file-name-label');
        if (fileNameLabel) fileNameLabel.textContent = 'No file chosen';
        document.getElementById('dynamic-fields').innerHTML = '';
        applyTranscriptRule('');
        if (fileInput) fileInput.value = '';
        document.getElementById('rec-confidential-password').value = '';
        document.getElementById('confidential-password-group').style.display = 'none';
                document.getElementById('rec-patient-id').value = patientId() || '';
        document.getElementById('rec-date').value = new Date().toISOString().split('T')[0];
      } catch(ex) {
        errEl.textContent = ex.message; errEl.style.display = 'block';
      } finally {
        btn.disabled = false; btn.textContent = 'Save to Blockchain';
      }
    });
  }
}
