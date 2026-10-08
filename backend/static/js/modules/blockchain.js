/* blockchain.js — PsikoNot UI Blockchain Module */
import { apiFetch, patientId, formatTs, escapeHtml, emptyState } from './utils.js';
import { updateChainPill } from './dashboard.js';

export async function loadChainStatus() {
  const box = document.getElementById('chain-status-content');
  const vis = document.getElementById('chain-visual');
  if (!box || !vis) return;

  const pid = patientId();
  vis.innerHTML = '';
  if (!pid) {
    box.innerHTML = emptyState('No client selected. Choose one on the Dashboard.');
    return;
  }
  box.innerHTML = '<div class="loading-spinner">Verifying chain...</div>';
  try {
    const [status, records] = await Promise.all([
      apiFetch(`/api/blockchain/${pid}/status`),
      apiFetch(`/api/records/${pid}`)
    ]);
    const valid = status.is_valid;
    updateChainPill(valid);
    const anchorVerified = status.anchor_verified;
    let onChainColor = 'var(--danger)';
    let onChainText = 'NOT ANCHORED';
    if (anchorVerified) {
      onChainColor = 'var(--success)';
      onChainText = 'SECURED';
    }
    const sig = status.anchor_signature;
    const txLabel = sig
      ? `<a href="#" data-action="copy-tx" data-arg="${escapeHtml(sig)}" style="color:var(--accent-ledger); text-decoration:underline; font-size:10px;" title="Anchor signature (HMAC-SHA256 of the Merkle root)">Copy anchor sig: ${sig.substring(0,10)}...</a>`
      : 'No anchor signature';

    box.innerHTML = `
      <div class="chain-stat"><div class="chain-stat-val">${status.chain_length}</div><div class="chain-stat-lbl">Total Blocks</div></div>
      <div class="chain-stat">
        <div class="chain-stat-val" style="color:${valid?'var(--success)':'var(--danger)'}; font-size:18px; font-weight:700">
          ${valid ? 'VALID' : 'BROKEN'}
        </div>
        <div class="chain-stat-lbl">Integrity</div>
      </div>
      <div class="chain-stat">
        <div class="chain-stat-val" style="color:${valid?'var(--success)':'var(--danger);font-size:20px'}">
          ${valid ? '100%' : 'ERROR #' + status.broken_at}
        </div>
        <div class="chain-stat-lbl">Status</div>
      </div>
      <div class="chain-stat">
        <div class="chain-stat-val" style="color:${onChainColor}; font-size:18px; font-weight:700">
          ${onChainText}
        </div>
        <div class="chain-stat-lbl">${txLabel}</div>
      </div>`;

    // Visual chain (show last 8 blocks)
    const recs = records.records.slice(0, 8);
    recs.forEach((r, i) => {
      const broken = status.broken_at !== null && r.block_index >= status.broken_at;
      vis.innerHTML += `
        <div class="card-flip-container ${broken?'broken-block':'valid-block'}">
          <div class="card-flip-inner">
            <div class="card-front">
              <div class="blk-header">BLOCK #${r.block_index} · ${formatTs(r.timestamp)}</div>
              <div class="blk-hash">${escapeHtml(r.hash_preview)}</div>
              <div style="margin-top:8px;font-size:12px;color:var(--muted);font-weight:600">${escapeHtml(r.title)}</div>
            </div>
            <div class="card-back">
              <div class="blk-header" style="color:var(--accent-ledger)">PROOF #${r.block_index}</div>
              <div class="blk-field" style="margin-top:4px;font-size:11px"><span style="color:var(--muted)">Hash:</span> <span style="font-family:var(--font-mono);word-break:break-all">${escapeHtml(r.hash_preview)}</span></div>
              <div class="blk-field" style="margin-top:4px;font-size:11px"><span style="color:var(--muted)">Merkle:</span> <span style="font-family:var(--font-mono);word-break:break-all">${escapeHtml(r.merkle_root_preview)}</span></div>
              <div class="blk-field" style="margin-top:4px;font-size:11px"><span style="color:var(--muted)">Sig:</span> <span style="font-family:var(--font-mono);word-break:break-all">${escapeHtml(r.signature_preview)}</span></div>
            </div>
          </div>
        </div>
        ${i < recs.length-1 ? '<div class="chain-connector"></div>' : ''}`;
    });
  } catch(e) { 
    box.innerHTML = `<div class="alert alert-error">${escapeHtml(e.message)}</div>`;
  }
}
