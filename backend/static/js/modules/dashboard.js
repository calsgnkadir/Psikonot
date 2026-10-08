/* dashboard.js — PsikoNot UI Dashboard Module */
import { apiFetch, patientId, emptyState, escapeHtml, appState, getCurrentUser, roleText } from './utils.js';
import { addNotification, getNotifications } from './notifications.js';
import { loadUpcomingAppointments } from './appointments.js';
import { tallyLine } from './clients.js';

let activityChartInstance = null;

/* -- Practitioner: client list --------------------------------------- */

function renderClientCard(c, selectedId) {
  const pid = escapeHtml(c.patient_id);
  const selected = c.patient_id === selectedId;
  const button = `<button type="button" class="btn ${selected ? 'btn-gold' : 'btn-ghost'} btn-sm" data-action="open-client" data-arg="${pid}" data-arg2="dashboard">${selected ? 'Selected' : 'Select'}</button>`;
  return `
    <div class="user-card glass" style="${selected ? 'border-color:var(--gold);' : ''}">
      <div class="user-avatar" style="background:linear-gradient(135deg,#C9A84C,#8B6914)">${escapeHtml(c.full_name.charAt(0))}</div>
      <div style="flex:1">
        <div style="font-weight:600">${escapeHtml(c.full_name)} <span style="font-size:12px;color:var(--muted);font-weight:400">${pid}</span></div>
        <div style="font-size:12px;color:var(--muted)">${escapeHtml(tallyLine(c))}</div>
      </div>
      ${button}
    </div>`;
}

// Renders the practitioner's clients and returns them ([] on error).
async function loadPractitionerClients(selectedId) {
  const list = document.getElementById('dashboard-clients');
  if (!list) return [];
  try {
    const d = await apiFetch('/api/clients');
    list.innerHTML = d.clients.length
      ? d.clients.map(c => renderClientCard(c, selectedId)).join('')
      : emptyState('No clients yet. Add one on the Clients page.');
    return d.clients;
  } catch (e) {
    list.innerHTML = `<div class="alert alert-error">${escapeHtml(e.message)}</div>`;
    return [];
  }
}

export function updateChainPill(valid) {
  appState.updateChain(valid);
}

export function renderActivityChart(records) {
  const canvas = document.getElementById('activityChart');
  if (!canvas) return;

  if (typeof Chart === 'undefined') return;

  const days = 7;
  const labels = [];
  const commitCounts = [];

  for (let i = days - 1; i >= 0; i--) {
    const d = new Date();
    d.setDate(d.getDate() - i);
    const dateStr = d.toISOString().split('T')[0];
    labels.push(d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short' }));

    // Only real ledger activity is plotted. A tamper-evidence dashboard that pads
    // quiet days with invented numbers is worse than an empty one.
    const dayRecords = records.filter(r => {
      const recDate = r.timestamp_iso ? r.timestamp_iso.split('T')[0] : new Date(r.timestamp * 1000).toISOString().split('T')[0];
      return recDate === dateStr;
    });
    commitCounts.push(dayRecords.length);
  }

  if (activityChartInstance) {
    activityChartInstance.destroy();
  }

  const ctx = canvas.getContext('2d');
  activityChartInstance = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: labels,
      datasets: [
        {
          label: 'Block Commits',
          data: commitCounts,
          backgroundColor: 'rgba(10, 191, 188, 0.65)',
          borderColor: '#0ABFBC',
          borderWidth: 1,
          borderRadius: 4
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          position: 'top',
          labels: {
            color: '#8892A4',
            font: { family: 'Inter', size: 11 }
          }
        }
      },
      scales: {
        x: {
          stacked: true,
          grid: { color: 'rgba(255, 255, 255, 0.03)' },
          ticks: { color: '#8892A4', font: { family: 'JetBrains Mono', size: 10 } }
        },
        y: {
          stacked: true,
          grid: { color: 'rgba(255, 255, 255, 0.05)' },
          ticks: { color: '#8892A4', font: { family: 'JetBrains Mono', size: 10 }, stepSize: 1 },
          min: 0
        }
      }
    }
  });
}

export async function loadDashboard() {
  let pid = patientId();
  const role = (getCurrentUser() || {}).role;
  const isPractitioner = role === 'practitioner';

  // Clear first: on a failed or blocked load the panel must not keep showing the
  // previous session's figures as if they belonged to the current user.
  ['stat-total-blocks', 'stat-total-records', 'stat-encrypted'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.textContent = '—';
  });
  const heading = document.getElementById('dashboard-client-heading');
  if (heading) heading.hidden = true;

  // A practitioner works from their client list, not from a typed client ID.
  // The ledger activity chart is for operators, not for a practitioner.
  const clientsPanel = document.getElementById('practitioner-clients-panel');
  if (clientsPanel) clientsPanel.hidden = !isPractitioner;
  const activityPanel = document.getElementById('activity-chart-panel');
  if (activityPanel) activityPanel.hidden = isPractitioner;
  if (isPractitioner) {
    loadUpcomingAppointments();
    const clients = await loadPractitionerClients(pid);
    const current = clients.find(c => c.patient_id === pid);
    if (!current) {
      // Nothing selected yet: open the first client, if there is one.
      if (clients.length) {
        window.openClientInPlace(clients[0].patient_id);
        return;   // openClientInPlace reloads the dashboard for that client
      }
      pid = null;
    } else if (heading) {
      heading.textContent = `${current.full_name} · ${current.patient_id}`;
      heading.hidden = false;
    }
  }

  // Privileged operators pick a client before any chart loads (no hardcoded
  // default), so prompt for a selection instead of firing a forbidden request.
  if (!pid) {
    const integ = document.getElementById('stat-integrity');
    if (integ) integ.textContent = 'SELECT CLIENT';
    const recent = document.getElementById('recent-records');
    if (recent) recent.innerHTML = emptyState(isPractitioner
      ? 'Select a client above to see their file.'
      : 'Choose a client to see their file.');
    return;
  }

  try {
    const [recData, statusData] = await Promise.all([
      apiFetch(`/api/records/${pid}`),
      apiFetch(`/api/blockchain/${pid}/status`)
    ]);
    
    document.getElementById('stat-total-blocks').textContent  = statusData.chain_length;
    document.getElementById('stat-total-records').textContent = recData.records.length;
    
    const encrypted = recData.records.filter(r => r.is_protected).length;
    document.getElementById('stat-encrypted').textContent = encrypted;
    
    const valid = statusData.is_valid;
    document.getElementById('stat-integrity').textContent = valid ? 'VALID' : 'BROKEN!';

    // Update sidebar widget
    const widgetBlock = document.getElementById('widget-block-count');
    if (widgetBlock) widgetBlock.textContent = statusData.chain_length;
    const widgetIntegrity = document.getElementById('widget-integrity-status');
    if (widgetIntegrity) {
      widgetIntegrity.textContent = valid ? 'SECURED' : 'COMPROMISED';
      widgetIntegrity.style.color = valid ? 'var(--success)' : 'var(--danger)';
    }
    const widgetCheck = document.getElementById('widget-last-check');
    if (widgetCheck) {
      widgetCheck.textContent = new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    }

    // Update integrity icon color
    const iconEl = document.getElementById('stat-integrity-icon');
    if (iconEl) {
      iconEl.querySelector('svg').setAttribute('stroke', valid ? '#2CC985' : '#E57373');
    }

    updateChainPill(valid);
    
    const recent = recData.records.slice(0, 5);
    document.getElementById('recent-records').innerHTML =
      recent.length ? recent.map(r => window.renderRecordCard(r)).join('') : emptyState('No records yet');

    renderActivityChart(recData.records);

    // Trigger chain failure notification if broken
    if (!valid) {
      const notis = getNotifications();
      const hasAlert = notis.some(n => n.title === 'Chain Integrity Compromised' && n.text.includes(`#${statusData.broken_at}`));
      if (!hasAlert) {
        addNotification('Chain Integrity Compromised', `Integrity check failed. Block #${statusData.broken_at} is broken!`, 'warning');
      }
    }
  } catch(e) {
    console.error(e);
    // Never leave the dashboard silently showing em-dashes: say why it is empty.
    const recent = document.getElementById('recent-records');
    if (recent) {
      const isPolicyBlock = /Dual-Control/i.test(e.message || '');
      recent.innerHTML = `
        <div class="alert alert-error" style="line-height:1.5">
          <strong>${isPolicyBlock ? 'Client records are locked by policy' : 'Could not load dashboard data'}</strong><br>
          ${escapeHtml(e.message || 'Unknown error')}
          ${isPolicyBlock ? "<br><br><button class='btn btn-gold btn-sm' data-action=\"navigate\" data-arg=\"dual-control\">Open Dual-Control Access</button>" : ''}
        </div>`;
    }
  }
}

export function navigate(page) {
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  const view = document.getElementById('view-' + page);
  if (view) view.classList.add('active');
  const navEl = document.querySelector(`[data-page="${page}"]`);
  if (navEl) navEl.classList.add('active');
  
  const titles = {
    dashboard:      'Dashboard Overview',
    records:        roleText('records-title'),
    clients:        'Clients',
    appointments:   'Appointments',
    'erasure-requests': 'Erasure Requests',
    alerts:         'Security Alerts',
    'add-record':   'Add Record',
    'chain-status': 'Chain Status Verification',
    users:          'User Management',
    audit:          'Access & Audit History',
    security:       'Security & 2FA Settings',
    'dual-control': 'Dual-Control Access',
    'my-access':    'Access Ledger',
  };
  
  document.getElementById('topbar-title').textContent = titles[page] || page;
  
  if (page === 'dashboard')     loadDashboard();
  if (page === 'records')       if (window.loadRecords) window.loadRecords();
  if (page === 'clients')       if (window.loadClients) window.loadClients();
  if (page === 'appointments')  if (window.loadAppointments) window.loadAppointments();
  if (page === 'erasure-requests') if (window.loadErasureRequests) window.loadErasureRequests();
  if (page === 'alerts')        if (window.loadSecurityAlerts) window.loadSecurityAlerts();
  if (page === 'chain-status')  if (window.loadChainStatus) window.loadChainStatus();
  if (page === 'users')         if (window.loadUsers) window.loadUsers();
  if (page === 'audit')         if (window.switchLogTab) window.switchLogTab(window.currentLogTab || 'audit');
  if (page === 'security')      if (window.loadSecuritySettings) window.loadSecuritySettings();
  if (page === 'dual-control')  if (window.loadDualControl) window.loadDualControl();
  if (page === 'my-access')     if (window.loadMyAccessLog) window.loadMyAccessLog();
}
