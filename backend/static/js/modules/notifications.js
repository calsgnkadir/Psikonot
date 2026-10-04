/* notifications.js — the bell in the top bar: messages this browser shows the
 * signed-in user (sign-in, chain alerts). They live in this browser only. */
import { getCurrentUser, appState, escapeHtml } from './utils.js';

export function getLocalNotifications() {
  const currentUser = getCurrentUser();
  const key = `vhv_notifications_${currentUser ? currentUser.username : 'guest'}`;
  try {
    return JSON.parse(localStorage.getItem(key) || '[]');
  } catch (e) {
    return [];
  }
}

export function saveLocalNotifications(list) {
  const currentUser = getCurrentUser();
  const key = `vhv_notifications_${currentUser ? currentUser.username : 'guest'}`;
  localStorage.setItem(key, JSON.stringify(list));
}

export function addNotification(title, text, type = 'info') {
  const list = getLocalNotifications();
  const noti = {
    id: 'local_' + Date.now() + Math.random().toString(36).substr(2, 5),
    title,
    text,
    type,
    time: new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' }),
    date: new Date().toLocaleDateString('en-GB'),
    read: false,
  };
  list.unshift(noti);
  saveLocalNotifications(list);
  updateNotificationsUI();
}

// Global cached notifications list
export function getNotifications() {
  return getLocalNotifications();
}

export function updateNotificationsUI() {
  const badge = document.getElementById('noti-badge-count');
  const listEl = document.getElementById('noti-list');
  const currentUser = getCurrentUser();
  if (!currentUser) {
    if (badge) badge.style.display = 'none';
    if (listEl) listEl.innerHTML = '<div style="padding: 16px; text-align: center; color: var(--muted); font-size: 12px;">No alerts</div>';
    return;
  }

  const combined = getLocalNotifications();   // newest first

  const unreadCount = combined.filter(n => !n.read).length;

  appState.updateNotifications(unreadCount);

  if (listEl) {
    if (combined.length === 0) {
      listEl.innerHTML = '<div style="padding: 16px; text-align: center; color: var(--muted); font-size: 12px;">No new alerts</div>';
    } else {
      listEl.innerHTML = combined.map(n => {
        let typeDotClass = 'noti-dot-info';
        if (n.type === 'warning' || n.type === 'danger') typeDotClass = 'noti-dot-warning';
        if (n.type === 'success') typeDotClass = 'noti-dot-success';

        return `
          <div class="noti-item" data-action="notification-read" data-arg="${escapeHtml(n.id)}">
            <div class="noti-title-row">
              <span class="noti-title">
                <span class="noti-icon-dot ${typeDotClass}"></span>
                ${escapeHtml(n.title)}
              </span>
              <span class="noti-time">${escapeHtml(n.time)}</span>
            </div>
            <div class="noti-text" style="${n.read ? 'color: var(--muted);' : 'font-weight: 500;'}">${escapeHtml(n.text)}</div>
            <div style="font-size: 9px; color: var(--muted); text-align: right; margin-top: 4px;">${escapeHtml(n.date)}</div>
          </div>
        `;
      }).join('');
    }
  }
}

export function toggleNotifications(event) {
  if (event) event.stopPropagation();
  const dropdown = document.getElementById('noti-dropdown');
  if (!dropdown) return;
  const isVisible = dropdown.style.display === 'block';
  
  closeAllDropdowns();

  if (!isVisible) {
    dropdown.style.display = 'block';
    markAllAsRead();
  }
}

export function closeAllDropdowns() {
  const dropdown = document.getElementById('noti-dropdown');
  if (dropdown) dropdown.style.display = 'none';
}

export function markAsRead(id) {
  const list = getLocalNotifications();
  const item = list.find(n => n.id === id);
  if (item) {
    item.read = true;
    saveLocalNotifications(list);
    updateNotificationsUI();
  }
}

export function markAllAsRead() {
  const list = getLocalNotifications();
  list.forEach(n => n.read = true);
  saveLocalNotifications(list);
  updateNotificationsUI();
}

export function clearAllNotifications(event) {
  if (event) {
    event.preventDefault();
    event.stopPropagation();
  }
  saveLocalNotifications([]);
  updateNotificationsUI();
}
