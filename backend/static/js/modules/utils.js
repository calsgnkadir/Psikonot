/* utils.js — Mahrem UI Utilities */

export const API = '';

export function getCookie(name) {
  const value = `; ${document.cookie}`;
  const parts = value.split(`; ${name}=`);
  if (parts.length === 2) return parts.pop().split(';').shift();
  return null;
}

export function setToken() {
  // The session token is an httpOnly, SameSite=Strict cookie the server sets and
  // clears — it is never kept in JS-readable storage, so even an XSS bug that
  // slips past the CSP cannot read it. This only clears any token left in
  // localStorage by an older client version.
  localStorage.removeItem('vhv_token');
}

export function getCurrentUser() {
  return JSON.parse(localStorage.getItem('vhv_user') || 'null');
}

export function setCurrentUser(user) {
  if (user) {
    localStorage.setItem('vhv_user', JSON.stringify(user));
  } else {
    localStorage.removeItem('vhv_user');
  }
}

export async function apiFetch(path, opts = {}) {
  let fullPath = path;
  if (fullPath.startsWith('/api/') && !fullPath.startsWith('/api/v1/')) {
    fullPath = '/api/v1' + fullPath.substring(4);
  }
  const headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
  // Authentication is the httpOnly access_token cookie, sent automatically on
  // same-origin requests — no bearer token is read from JS storage.

  // CSRF protection: append token header for unsafe methods
  const csrfToken = getCookie('csrf_token');
  if (csrfToken) {
    headers['X-CSRF-Token'] = csrfToken;
  }

  // Privileged operators (admin / security officer / auditor) may hold a
  // co-signed Dual-Control token authorising raw record access.
  const dualControlToken = getDualControlToken();
  if (dualControlToken) {
    headers['X-Dual-Control-Token'] = dualControlToken.token_id;
  }
  
  const res = await fetch(API + fullPath, { credentials: 'same-origin', ...opts, headers });
  const json = await res.json().catch(() => ({}));

  // A 401 on an authenticated call means the stored token is no longer usable
  // (expired, revoked, or issued for a different device fingerprint). Without
  // this, the UI keeps looking signed in while every request fails.
  if (res.status === 401 && !isAuthEntryPoint(fullPath)) {
    handleSessionExpiry(json.detail);
  }

  if (!res.ok) {
    throw new Error(json.detail || 'An error occurred');
  }
  return json;
}

/* -- Dual-Control token (co-signed privileged access) ----------------- */
// The short-lived Dual-Control token must be readable by JS to be sent as the
// X-Dual-Control-Token header, so it cannot be an httpOnly cookie. It is kept in
// sessionStorage (not localStorage): never written to disk, and cleared when the
// tab closes. A stale copy from an older client version is migrated out.
localStorage.removeItem('vhv_dual_control');

export function getDualControlToken() {
  try {
    return JSON.parse(sessionStorage.getItem('vhv_dual_control') || 'null');
  } catch (e) {
    return null;
  }
}

export function setDualControlToken(token) {
  if (token) {
    sessionStorage.setItem('vhv_dual_control', JSON.stringify(token));
  } else {
    sessionStorage.removeItem('vhv_dual_control');
  }
}

/* -- Session expiry handling ----------------------------------------- */
// Endpoints that legitimately answer 401 while signed out (a rejected login is
// not an expired session).
const AUTH_ENTRY_POINTS = [
  '/api/v1/auth/login',
  '/api/v1/auth/webauthn/login',
  '/api/v1/auth/webauthn/challenge',
];

function isAuthEntryPoint(fullPath) {
  return AUTH_ENTRY_POINTS.some(entry => fullPath.startsWith(entry));
}

let sessionExpiryPending = false;

export function handleSessionExpiry(detail) {
  // Nothing to expire when signed out, and a burst of parallel 401s must only
  // bounce the user back to the login screen once.
  if (sessionExpiryPending || !getCurrentUser()) return;
  sessionExpiryPending = true;

  setToken(null);
  setCurrentUser(null);
  setDualControlToken(null);

  // The server's wording ("Invalid token", "Token has been revoked") is not
  // actionable for the person at the keyboard; keep it for the console only.
  if (detail) console.warn('Session ended by server:', detail);

  window.dispatchEvent(new CustomEvent('vhv:session-expired', {
    detail: 'Your session has expired. Please sign in again.'
  }));

  setTimeout(() => { sessionExpiryPending = false; }, 1000);
}

/* -- base64url helpers (WebAuthn binary payloads) -------------------- */
export function bytesToB64url(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = '';
  for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

export function b64urlToBytes(value) {
  const normalized = String(value).replace(/-/g, '+').replace(/_/g, '/');
  const padded = normalized + '='.repeat((4 - normalized.length % 4) % 4);
  const binary = atob(padded);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

export function formatTs(ts) {
  return new Date(ts * 1000).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
}

export function formatTsFull(ts) {
  return new Date(ts * 1000).toLocaleString('en-GB', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

export function emptyState(msg) {
  return `<div class="empty-state"><div class="empty-icon-svg">
    <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="#6B6882" stroke-width="1" stroke-linecap="round">
      <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/>
      <polyline points="14 2 14 8 20 8"/>
    </svg>
  </div><p>${escapeHtml(msg)}</p></div>`;
}

export const ROLE_LABEL = {
  admin: 'Administrator', practitioner: 'Practitioner', client: 'Client',
  secretary: 'Secretary', security_officer: 'KVKK Officer', auditor: 'Auditor',
};

// Privileged operators (admin / practitioner / auditor / security officer) are not tied
// to one patient — they pick whose chart to view. Clients are always scoped
// to their own record and never touch this.
let _selectedPatient = null;

export function setSelectedPatient(pid) {
  _selectedPatient = (pid || '').trim() || null;
}

export function getSelectedPatient() {
  return _selectedPatient;
}

export function patientId() {
  const user = getCurrentUser();
  if (user && user.role === 'client') return user.patient_id;
  return _selectedPatient;   // null until a privileged operator selects a patient
}

/* -- Role-aware wording --------------------------------------------
 * The same page is seen by the client, their practitioner and operators, and
 * "your records" means something different to each. Elements that need a
 * different sentence per role carry data-role-text="<key>"; the text lives
 * here, with `default` for every role not listed.
 */
export const ROLE_TEXTS = {
  'records-title': {
    client:       'My Records',
    default:      'Client Records',
  },
  'consent-title': {
    client:       'My Consents',
    practitioner: 'Consent from this client',
    default:      'Consent Rules',
  },
  'consent-sub': {
    client:       'Decide which practitioners may see which of your records, and for how long.',
    practitioner: 'What this client has allowed you to see, and until when. Only the client can change it.',
    default:      "This client's consent rules. Only the client can change them.",
  },
  'consent-list-title': {
    client:       'Who can see my records',
    practitioner: 'Your access',
    default:      'Active Access Permissions',
  },
  'appointments-sub': {
    client:       'Your upcoming and past appointments. You can cancel one that has not started yet.',
    practitioner: 'Your appointment book. Your secretary, if you have one, works in the same book.',
    secretary:    "The practitioner's appointment book: names, client IDs and times only — no records.",
    default:      'Appointments.',
  },
  'consent-empty': {
    client:       'You have not given any practitioner access yet.',
    practitioner: 'This client has not given you access to any records.',
    default:      'No active consent rules.',
  },
};

export function roleText(key, role = (getCurrentUser() || {}).role) {
  const texts = ROLE_TEXTS[key];
  if (!texts) return key;
  return texts[role] || texts.default;
}

function applyRoleTexts(role) {
  document.querySelectorAll('[data-role-text]').forEach(el => {
    el.textContent = roleText(el.dataset.roleText, role);
  });
}

// Centralized UI State Manager
export const appState = {
  currentUser: null,
  chainValid: true,
  activePage: 'dashboard',
  notificationsCount: 0,

  updateUser(user) {
    this.currentUser = user;
    this.render();
  },

  updateChain(valid) {
    this.chainValid = valid;
    this.render();
  },

  updatePage(page) {
    this.activePage = page;
    this.render();
  },

  updateNotifications(count) {
    this.notificationsCount = count;
    this.render();
  },

  render() {
    // 1. Render User Info
    if (this.currentUser) {
      const sbName = document.getElementById('sidebar-name');
      if (sbName) sbName.textContent = this.currentUser.full_name;
      const sbRole = document.getElementById('sidebar-role');
      if (sbRole) {
        if (this.currentUser.role === 'client') {
          sbRole.textContent = this.currentUser.patient_id;
        } else {
          sbRole.textContent = ROLE_LABEL[this.currentUser.role] || this.currentUser.role;
        }
      }
      const sbAvatar = document.getElementById('sidebar-avatar');
      if (sbAvatar) sbAvatar.textContent = this.currentUser.full_name.charAt(0).toUpperCase();
      const tbUser = document.getElementById('topbar-user-name');
      if (tbUser) tbUser.textContent = this.currentUser.full_name;

      // Role-based navigation visibility
      const navUsers = document.getElementById('nav-users');
      if (navUsers) navUsers.style.display = (this.currentUser.role === 'admin') ? 'flex' : 'none';
      const navDualControl = document.getElementById('nav-dual-control');
      if (navDualControl) {
        navDualControl.style.display =
          ['admin', 'security_officer', 'auditor'].includes(this.currentUser.role) ? 'flex' : 'none';
      }
      const navAudit = document.getElementById('nav-audit');
      if (navAudit) navAudit.style.display = (this.currentUser.role === 'admin' || this.currentUser.role === 'auditor') ? 'flex' : 'none';
      // The appointment book: practitioners, their secretaries and clients.
      const kvkkNav = { 'nav-mydata': ['client'], 'nav-erasure-requests': ['admin', 'security_officer'],
                        'nav-alerts': ['admin', 'security_officer'] };
      Object.entries(kvkkNav).forEach(([id, roles]) => {
        const item = document.getElementById(id);
        if (item) item.style.display = roles.includes(this.currentUser.role) ? 'flex' : 'none';
      });
      ['nav-appointments'].forEach(id => {
        const item = document.getElementById(id);
        if (item) {
          item.style.display =
            ['practitioner', 'secretary', 'client'].includes(this.currentUser.role) ? 'flex' : 'none';
        }
      });
      // A secretary works only in the appointment book: every page that shows
      // record data is hidden (the server refuses them anyway).
      if (this.currentUser.role === 'secretary') {
        ['dashboard', 'records', 'add-record', 'consent', 'chain-status'].forEach(page => {
          const item = document.querySelector(`.nav-item[data-page="${page}"]`);
          if (item) item.style.display = 'none';
        });
      }
      const chainWidget = document.getElementById('chain-status-indicator');
      if (chainWidget) chainWidget.style.display = this.currentUser.role === 'secretary' ? 'none' : '';
      const navClients = document.getElementById('nav-clients');
      if (navClients) navClients.style.display = (this.currentUser.role === 'practitioner') ? 'flex' : 'none';
      applyRoleTexts(this.currentUser.role);
      const newRecordBtn = document.getElementById('dashboard-new-record');
      if (newRecordBtn) newRecordBtn.style.display = (this.currentUser.role === 'client') ? 'none' : '';
      const navAdd = document.getElementById('nav-add');
      if (navAdd) navAdd.style.display = ['client', 'secretary'].includes(this.currentUser.role) ? 'none' : 'flex';

      // Only the patient who owns the chart may grant or revoke clinical access.
      const consentGrantCard = document.getElementById('consent-grant-card');
      if (consentGrantCard) {
        consentGrantCard.style.display = (this.currentUser.role === 'client') ? 'block' : 'none';
      }

      // The patient can see who read their records; it is their transparency view.
      const navMyAccess = document.getElementById('nav-my-access');
      if (navMyAccess) {
        navMyAccess.style.display = (this.currentUser.role === 'client') ? 'flex' : 'none';
      }
    }

    // 2. Render Chain Pill
    const pill = document.getElementById('chain-pill');
    if (pill) {
      const dot = pill.querySelector('.chain-dot');
      const txt = document.getElementById('chain-pill-text');
      pill.className = 'chain-pill' + (this.chainValid ? '' : ' invalid');
      if (dot) dot.className = 'chain-dot ' + (this.chainValid ? 'valid' : 'invalid');
      if (txt) txt.textContent = this.chainValid ? 'Chain Valid' : 'Chain BROKEN!';
    }

    // 3. Render Notifications count
    const badge = document.getElementById('noti-badge-count');
    if (badge) {
      badge.textContent = this.notificationsCount;
      badge.style.display = this.notificationsCount > 0 ? 'inline-block' : 'none';
    }
  }
};

export function escapeHtml(str) {
  if (typeof str !== 'string') return str;
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

