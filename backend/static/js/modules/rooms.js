/* rooms.js — the centre's therapy rooms
 *
 * Admin and security officer: add a room, rename it, switch it off or back on.
 * Rooms are never deleted, so past appointments keep theirs. A name is a short
 * label ("Oda 1"); the server refuses anything else.
 *
 * Practitioner and secretary use the rooms from the appointment book
 * (appointments.js): activeRooms() fills its room selectors.
 *
 * A room's day: the server reads the date in its own timezone (midnight to
 * midnight, VHV_TIMEZONE) and the times are shown in that timezone too, as in
 * the front desk's day view. Operators run no book, so the server sends them
 * every appointment as a busy time only: no client, no practitioner.
 */
import { apiFetch, escapeHtml, emptyState, LOCALE } from './utils.js';
import { showMessage } from './clients.js';

let rooms = [];

/* -- A room's day (shared with appointments.js) ----------------------- */

// YYYY-MM-DD of a Date in the browser's calendar.
export function isoDay(date) {
  const pad = n => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

// The date `delta` days from a YYYY-MM-DD (read at noon, so a clock change
// cannot skip a day); 0, or no date yet, is today.
function shiftedDay(value, delta) {
  const step = Number(delta);
  if (!step || !value) return isoDay(new Date());
  const date = new Date(`${value}T12:00:00`);
  date.setDate(date.getDate() + step);
  return isoDay(date);
}

export function fetchRoomDay(roomId, date) {
  const query = new URLSearchParams({ date });
  return apiFetch(`/api/rooms/${encodeURIComponent(roomId)}/schedule?${query}`);
}

function clockIn(iso, timeZone) {
  const opts = { hour: '2-digit', minute: '2-digit' };
  try {
    return new Date(iso).toLocaleTimeString(LOCALE, { ...opts, timeZone });
  } catch (e) {
    return new Date(iso).toLocaleTimeString(LOCALE, opts);
  }
}

// A slot of a book the caller runs shows its client; any other only its times.
function renderSlot(slot, timeZone, busyLabel) {
  const times = `${escapeHtml(clockIn(slot.starts_at, timeZone))}–${escapeHtml(clockIn(slot.ends_at, timeZone))}`;
  const who = slot.mine
    ? `${escapeHtml(slot.client_name)} <span class="appt-muted">${escapeHtml(slot.patient_id)}</span>`
    : `<span class="appt-muted">${escapeHtml(busyLabel)}</span>`;
  return `
    <div class="record-card appt-row" style="cursor:default">
      <div class="record-main">
        <div class="record-title">${times}</div>
        <div class="record-meta">${who}</div>
      </div>
    </div>`;
}

// The slots of a /schedule answer, with times in the server's timezone.
export function roomDayHtml(day, busyLabel = 'Dolu') {
  const note = `<div class="appt-muted" style="margin-bottom:8px">Saatler ${escapeHtml(day.timezone)} saat dilimindedir.</div>`;
  return note + (day.slots.length
    ? day.slots.map(slot => renderSlot(slot, day.timezone, busyLabel)).join('')
    : emptyState('Oda gün boyu boş.'));
}

// The rooms in use, for the booking form and the room day ([] on error).
export async function activeRooms() {
  try {
    const d = await apiFetch('/api/rooms');
    return d.rooms.filter(room => room.active);
  } catch (e) {
    return [];
  }
}

export async function loadRooms() {
  showMessage('rooms-error', '');
  const list = document.getElementById('rooms-list');
  if (!list) return;
  list.innerHTML = '<div class="loading-spinner">Yükleniyor...</div>';
  try {
    const d = await apiFetch('/api/rooms');
    rooms = d.rooms;
    renderRooms();
  } catch (e) {
    list.innerHTML = `<div class="alert alert-error">${escapeHtml(e.message)}</div>`;
  }
}

function renderRooms() {
  const list = document.getElementById('rooms-list');
  if (!list) return;
  list.innerHTML = rooms.length ? rooms.map(renderRoom).join('') : emptyState('Henüz oda yok.');
  fillDayRooms();
}

// The operators' room-day selector: every room, switched off or not.
function fillDayRooms() {
  const select = document.getElementById('rooms-day-room');
  if (!select) return;
  const chosen = select.value;
  select.replaceChildren();
  rooms.forEach(room => {
    const o = document.createElement('option');
    o.value = room.id;
    o.textContent = room.active ? room.name : `${room.name} (kullanım dışı)`;
    o.selected = room.id === chosen;
    select.appendChild(o);
  });
  const date = document.getElementById('rooms-day-date');
  if (date && !date.value) date.value = isoDay(new Date());
}

// The operators' view of a room's day: busy times only.
export async function showOperatorRoomDay(e) {
  if (e) e.preventDefault();
  const list = document.getElementById('rooms-day-list');
  const roomId = (document.getElementById('rooms-day-room') || {}).value;
  const date = document.getElementById('rooms-day-date');
  if (!list || !roomId || !date) return;
  if (!date.value) date.value = isoDay(new Date());
  list.innerHTML = '<div class="loading-spinner">Yükleniyor...</div>';
  try {
    const d = await fetchRoomDay(roomId, date.value);
    list.innerHTML = roomDayHtml(d, 'Dolu');
  } catch (ex) {
    list.innerHTML = `<div class="alert alert-error">${escapeHtml(ex.message)}</div>`;
  }
}

export function shiftOperatorRoomDay(delta) {
  const date = document.getElementById('rooms-day-date');
  if (!date) return;
  date.value = shiftedDay(date.value, delta);
  showOperatorRoomDay();
}

// "Day" on a room's row: pick that room in the room-day card and show it.
export function openOperatorRoomDay(id) {
  const select = document.getElementById('rooms-day-room');
  if (!select) return;
  select.value = id;
  showOperatorRoomDay();
  const card = document.getElementById('rooms-day-card');
  if (card && card.scrollIntoView) card.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function renderRoom(room) {
  const id = escapeHtml(room.id);
  const button = (action, label, arg2 = '') =>
    `<button type="button" class="btn btn-ghost btn-sm" data-action="${action}" data-arg="${id}"${arg2 ? ` data-arg2="${arg2}"` : ''}>${label}</button>`;
  const state = room.active
    ? '<span class="badge badge-shared">Kullanımda</span>'
    : '<span class="badge badge-private">Kullanım dışı</span>';
  const actions = button('room-day-open', 'Günü göster') + button('room-rename', 'Yeniden adlandır')
    + (room.active ? button('room-active', 'Kullanım dışı bırak', 'off') : button('room-active', 'Kullanıma aç', 'on'));
  return `
    <div class="record-card appt-row" style="cursor:default">
      <div class="record-main">
        <div class="record-title">${escapeHtml(room.name)}</div>
        <div class="record-meta">${id}</div>
      </div>
      ${state}
      <div class="appt-actions" id="room-actions-${id}">${actions}</div>
    </div>`;
}

export async function createRoom(e) {
  if (e) e.preventDefault();
  showMessage('rooms-error', '');
  const input = document.getElementById('room-name');
  try {
    await apiFetch('/api/rooms', { method: 'POST', body: JSON.stringify({ name: input.value }) });
    input.value = '';
    await loadRooms();
  } catch (ex) {
    showMessage('rooms-error', ex.message);
  }
}

// Swap the row's buttons for a name field.
export function startRename(id) {
  const room = rooms.find(x => x.id === id);
  const box = document.getElementById(`room-actions-${id}`);
  if (!room || !box) return;
  const safeId = escapeHtml(id);
  box.innerHTML = `
    <input type="text" id="room-rename-input" maxlength="30" value="${escapeHtml(room.name)}" aria-label="Oda adı" />
    <button type="button" class="btn btn-gold btn-sm" data-action="room-rename-save" data-arg="${safeId}">Kaydet</button>
    <button type="button" class="btn btn-ghost btn-sm" data-action="room-rename-cancel">Vazgeç</button>`;
}

export async function saveRename(id) {
  const input = document.getElementById('room-rename-input');
  if (!input) return;
  await patchRoom(id, { name: input.value });
}

export function cancelRename() {
  renderRooms();
}

export async function setRoomActive(id, on) {
  if (!on && !confirm('Bu oda kullanım dışı bırakılsın mı? Odadaki mevcut randevular odalarını korur; oda yeniden kullanıma açılana kadar kimse bu odaya randevu veremez.')) return;
  await patchRoom(id, { active: on });
}

async function patchRoom(id, body) {
  showMessage('rooms-error', '');
  try {
    await apiFetch(`/api/rooms/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify(body) });
    await loadRooms();
  } catch (ex) {
    showMessage('rooms-error', ex.message);
  }
}
