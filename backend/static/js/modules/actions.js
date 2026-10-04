/* actions.js — delegated event handling
 *
 * Interactive elements declare what they do with a data attribute instead of an
 * inline handler:
 *
 *   <button data-action="navigate" data-arg="records">
 *   <form data-submit-action="save-client">
 *   <select data-change-action="switch-theme">
 *
 * A handful of listeners on `document` resolve those declarations against the
 * registry below. Nothing in the application is written as `onclick="..."`, so
 * the Content-Security-Policy can forbid inline script outright — which is what
 * stops an injected string from executing even if it reaches the DOM.
 *
 * Handlers are called as `handler(element, event)`; arguments come from
 * `data-arg` / `data-arg2`, or from stashPayload() for values too large or too
 * sensitive to sit in an attribute.
 */

const ATTRIBUTE = {
  click:  'data-action',
  change: 'data-change-action',
  input:  'data-input-action',
  submit: 'data-submit-action',
};

const registries = {
  click:  new Map(),
  change: new Map(),
  input:  new Map(),
  submit: new Map(),
};

export function registerActions(kind, handlers) {
  const registry = registries[kind];
  if (!registry) throw new Error(`Unknown action kind: ${kind}`);
  for (const [name, handler] of Object.entries(handlers)) {
    registry.set(name, handler);
  }
}

/* -- Payload stash ---------------------------------------------------
 * Attachment bytes and record passwords used to be interpolated into onclick
 * attributes. They are held here and referenced by an opaque key instead.
 */
const payloads = new Map();
let payloadSequence = 0;

export function stashPayload(value) {
  const key = `pl-${++payloadSequence}`;
  payloads.set(key, value);
  return key;
}

export function takePayload(key) {
  return payloads.get(key);
}

function dispatch(kind, event) {
  const attribute = ATTRIBUTE[kind];
  const target = event.target;
  if (!target || typeof target.closest !== 'function') return;

  const element = target.closest(`[${attribute}]`);
  if (!element) return;

  const handler = registries[kind].get(element.getAttribute(attribute));
  if (!handler) return;

  handler(element, event);
}

export function initActionDispatch() {
  for (const kind of Object.keys(ATTRIBUTE)) {
    document.addEventListener(kind, (event) => dispatch(kind, event));
  }
}
