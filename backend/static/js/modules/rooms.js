/* rooms.js — the centre's therapy rooms
 *
 * Admin and security officer: add a room, rename it, switch it off or back on.
 * Rooms are never deleted, so past appointments keep theirs. A name is a short
 * label ("Oda 1"); the server refuses anything else.
 *
 * Practitioner and secretary use the rooms from the appointment book
 * (appointments.js): activeRooms() fills its room selectors.
 */
import { apiFetch, escapeHtml, emptyState } from './utils.js';
import { showMessage } from './clients.js';

let rooms = [];

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
  list.innerHTML = '<div class="loading-spinner">Loading...</div>';
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
  list.innerHTML = rooms.length ? rooms.map(renderRoom).join('') : emptyState('No rooms yet.');
}

function renderRoom(room) {
  const id = escapeHtml(room.id);
  const button = (action, label, arg2 = '') =>
    `<button type="button" class="btn btn-ghost btn-sm" data-action="${action}" data-arg="${id}"${arg2 ? ` data-arg2="${arg2}"` : ''}>${label}</button>`;
  const state = room.active
    ? '<span class="badge badge-shared">In use</span>'
    : '<span class="badge badge-private">Switched off</span>';
  const actions = button('room-rename', 'Rename')
    + (room.active ? button('room-active', 'Switch off', 'off') : button('room-active', 'Switch on', 'on'));
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
    <input type="text" id="room-rename-input" maxlength="30" value="${escapeHtml(room.name)}" aria-label="Room name" />
    <button type="button" class="btn btn-gold btn-sm" data-action="room-rename-save" data-arg="${safeId}">Save</button>
    <button type="button" class="btn btn-ghost btn-sm" data-action="room-rename-cancel">Back</button>`;
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
  if (!on && !confirm('Switch this room off? Appointments already in it keep it; nobody can book it again until it is switched back on.')) return;
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
