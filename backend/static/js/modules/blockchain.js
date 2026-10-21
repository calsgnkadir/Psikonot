/* blockchain.js — PsikoNot UI Blockchain Module */
import { apiFetch, patientId, formatTs, escapeHtml, emptyState, recordTitle } from './utils.js';
import { updateChainPill } from './dashboard.js';

export async function loadChainStatus() {
  const box = document.getElementById('chain-status-content');
  const vis = document.getElementById('chain-visual');
  if (!box || !vis) return;

  const pid = patientId();
  vis.innerHTML = '';
  if (!pid) {
    box.innerHTML = emptyState('Danışan seçilmedi. Genel bakış sayfasından bir danışan seçin.');
    return;
  }
  box.innerHTML = '<div class="loading-spinner">Zincir doğrulanıyor...</div>';
  try {
    const [status, records] = await Promise.all([
      apiFetch(`/api/blockchain/${pid}/status`),
      apiFetch(`/api/records/${pid}`)
    ]);
    const valid = status.is_valid;
    updateChainPill(valid);
    const anchorVerified = status.anchor_verified;
    let onChainColor = 'var(--danger)';
    let onChainText = 'ÇAPA YOK';
    if (anchorVerified) {
      onChainColor = 'var(--success)';
      onChainText = 'İMZALI';
    }
    const sig = status.anchor_signature;
    const txLabel = sig
      ? `<a href="#" data-action="copy-tx" data-arg="${escapeHtml(sig)}" style="color:var(--accent-ledger); text-decoration:underline; font-size:10px;" title="Çapa imzası (Merkle kökünün HMAC-SHA256 imzası)">Çapa imzasını kopyala: ${escapeHtml(sig.substring(0,10))}...</a>`
      : 'Çapa imzası yok';

    box.innerHTML = `
      <div class="chain-stat"><div class="chain-stat-val">${status.chain_length}</div><div class="chain-stat-lbl">Toplam blok</div></div>
      <div class="chain-stat">
        <div class="chain-stat-val" style="color:${valid?'var(--success)':'var(--danger)'}; font-size:18px; font-weight:700">
          ${valid ? 'GEÇERLİ' : 'BOZULMUŞ'}
        </div>
        <div class="chain-stat-lbl">Bütünlük</div>
      </div>
      <div class="chain-stat">
        <div class="chain-stat-val" style="color:${valid?'var(--success)':'var(--danger);font-size:20px'}">
          ${valid ? '%100' : 'HATA #' + status.broken_at}
        </div>
        <div class="chain-stat-lbl">Durum</div>
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
              <div class="blk-header">BLOK #${r.block_index} · ${formatTs(r.timestamp)}</div>
              <div class="blk-hash">${escapeHtml(r.hash_preview)}</div>
              <div style="margin-top:8px;font-size:12px;color:var(--muted);font-weight:600">${escapeHtml(recordTitle(r.title))}</div>
            </div>
            <div class="card-back">
              <div class="blk-header" style="color:var(--accent-ledger)">KANIT #${r.block_index}</div>
              <div class="blk-field" style="margin-top:4px;font-size:11px"><span style="color:var(--muted)">Hash:</span> <span style="font-family:var(--font-mono);word-break:break-all">${escapeHtml(r.hash_preview)}</span></div>
              <div class="blk-field" style="margin-top:4px;font-size:11px"><span style="color:var(--muted)">Merkle:</span> <span style="font-family:var(--font-mono);word-break:break-all">${escapeHtml(r.merkle_root_preview)}</span></div>
              <div class="blk-field" style="margin-top:4px;font-size:11px"><span style="color:var(--muted)">İmza:</span> <span style="font-family:var(--font-mono);word-break:break-all">${escapeHtml(r.signature_preview)}</span></div>
            </div>
          </div>
        </div>
        ${i < recs.length-1 ? '<div class="chain-connector"></div>' : ''}`;
    });
  } catch(e) { 
    box.innerHTML = `<div class="alert alert-error">${escapeHtml(e.message)}</div>`;
  }
}
