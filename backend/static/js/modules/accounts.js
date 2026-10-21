/* accounts.js — passwords and account switches
 *
 * Everyone: change their own password (Security page). The other sessions of
 * the account are signed out; this one continues.
 *
 * Practitioner (staff list) and admin (user list): issue a password reset code,
 * disable an account, enable it again. An operator's reset, and re-enabling
 * anyone but a secretary, needs a co-signed Dual-Control token for that
 * username: apiFetch sends the held token, and the server spends it.
 *
 * Login screen: the reset code arrives as a link (#reset=CODE) and opens the
 * same form as an invitation (clients.js).
 */
import { apiFetch, escapeHtml, getDualControlToken, setDualControlToken } from './utils.js';
import { showMessage } from './clients.js';
import { translateApiMessage } from './messages.js';

// In the URL fragment, which browsers never send to the server.
export function resetLink(code) {
  return `${location.origin}/#reset=${encodeURIComponent(code)}`;
}

export async function changePassword(e) {
  if (e) e.preventDefault();
  showMessage('password-error', '');
  showMessage('password-success', '');
  const current = document.getElementById('cp-current').value;
  const next = document.getElementById('cp-new').value;
  if (next !== document.getElementById('cp-new2').value) {
    showMessage('password-error', 'Yeni şifreler birbiriyle eşleşmiyor.');
    return;
  }
  try {
    const d = await apiFetch('/api/accounts/password', {
      method: 'POST',
      body: JSON.stringify({ current_password: current, new_password: next }),
    });
    document.getElementById('password-form').reset();
    showMessage('password-success', translateApiMessage(d.message));
  } catch (ex) {
    showMessage('password-error', ex.message);
  }
}

// A co-signed token for this account is spent by the server once used.
function dropSpentToken(username) {
  const held = getDualControlToken();
  if (held && held.target_patient_id === username) setDualControlToken(null);
}

/* Buttons for one account, by its status: 'active', 'pending' or 'disabled'.
 * Each carries the username in data-arg; the handlers below do the rest. */
export function accountButtons(username, status) {
  const user = escapeHtml(username);
  const button = (action, label) =>
    `<button type="button" class="btn btn-ghost btn-sm" data-action="${action}" data-arg="${user}">${label}</button>`;
  if (status === 'disabled') return button('account-enable', 'Etkinleştir');
  if (status === 'pending') return button('account-disable', 'Daveti geri çek');
  return button('account-reset', 'Şifre sıfırla') + button('account-disable', 'Devre dışı bırak');
}

/* `where` is the id prefix of the panel's message and result elements
 * ('staff' or 'users'); `reload` redraws the list. */
export async function setAccountEnabled(username, enable, where, reload) {
  showMessage(`${where}-error`, '');
  if (!enable && !confirm(`${username} devre dışı bırakılsın mı? Açık oturumları hemen kapanır.`)) return;
  try {
    await apiFetch(`/api/accounts/${encodeURIComponent(username)}/${enable ? 'enable' : 'disable'}`, { method: 'POST' });
    if (enable) dropSpentToken(username);
    await reload();
  } catch (ex) {
    showMessage(`${where}-error`, ex.message);
  }
}

export async function issueResetCode(username, where) {
  showMessage(`${where}-error`, '');
  if (!confirm(`${username} için şifre sıfırlama kodu oluşturulsun mu? Kodu kişiye yüz yüze verin.`)) return;
  try {
    const d = await apiFetch(`/api/accounts/${encodeURIComponent(username)}/reset-code`, { method: 'POST' });
    dropSpentToken(username);
    document.getElementById(`${where}-reset-result`).hidden = false;
    document.getElementById(`${where}-reset-username`).textContent = d.username;
    document.getElementById(`${where}-reset-link`).value = resetLink(d.reset_code);
  } catch (ex) {
    showMessage(`${where}-error`, ex.message);
  }
}
