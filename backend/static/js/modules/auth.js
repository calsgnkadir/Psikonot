/* auth.js — PsikoNot UI Authentication Module */
import { apiFetch, setToken, setCurrentUser, getCurrentUser, setDualControlToken, bytesToB64url, b64urlToBytes } from './utils.js';
import { updateNotificationsUI, addNotification } from './notifications.js';

export let mfaRequired = false;

export function resetLoginFormState() {
  mfaRequired = false;
  document.getElementById('login-fields-group').style.display = 'block';
  document.getElementById('inp-username').required = true;
  document.getElementById('inp-password').required = true;
  document.getElementById('inp-password').value = '';
  
  const mfaGroup = document.getElementById('login-mfa-group');
  mfaGroup.style.display = 'none';
  const inpMfa = document.getElementById('inp-mfa');
  inpMfa.required = false;
  inpMfa.value = '';
  
  document.getElementById('btn-login-text').textContent = 'ENTER VAULT';
  document.getElementById('login-mfa-back').style.display = 'none';
  document.getElementById('inp-username').focus();
}

export function resetLoginForm(e) {
  if (e) e.preventDefault();
  resetLoginFormState();
}

export async function handleLoginSubmit(e) {
  if (e) e.preventDefault();
  const btn  = document.getElementById('btn-login');
  const btxt = document.getElementById('btn-login-text');
  const bspin= document.getElementById('btn-login-spin');
  const err  = document.getElementById('login-error');
  if (err) err.style.display = 'none';
  if (btxt) btxt.style.display = 'none';
  if (bspin) bspin.style.display = 'inline-block';
  if (btn) btn.disabled = true;
  try {
    const payload = {
      username: document.getElementById('inp-username').value,
      password: document.getElementById('inp-password').value,
    };
    if (mfaRequired) {
      payload.code = document.getElementById('inp-mfa').value;
    }
    
    const data = await apiFetch('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify(payload)
    });
    
    if (data.mfa_required) {
      mfaRequired = true;
      document.getElementById('login-fields-group').style.display = 'none';
      document.getElementById('inp-username').required = false;
      document.getElementById('inp-password').required = false;
      
      const mfaGroup = document.getElementById('login-mfa-group');
      mfaGroup.style.display = 'block';
      const inpMfa = document.getElementById('inp-mfa');
      inpMfa.required = true;
      inpMfa.value = '';
      inpMfa.focus();
      
      if (btxt) btxt.textContent = 'VERIFY MFA CODE';
      document.getElementById('login-mfa-back').style.display = 'block';
    } else {
      setToken(data.access_token);
      setCurrentUser(data.user);
      resetLoginFormState();
      if (window.enterApp) {
        // Under a mandatory-passkey policy an account without one goes straight
        // to enrolment instead of the dashboard.
        window.enterApp({ passkeyRequired: !!data.passkey_enrollment_required });
      }
    }
  } catch(ex) {
    if (err) {
      err.textContent = ex.message;
      err.style.display = 'block';
    }
  } finally {
    if (btxt) btxt.style.display = 'inline';
    if (bspin) bspin.style.display = 'none';
    if (btn) btn.disabled = false;
  }
  return false;
}

export function fillCreds(u, p) {
  resetLoginFormState();
  const inpU = document.getElementById('inp-username');
  const inpP = document.getElementById('inp-password');
  if (inpU) inpU.value = u;
  if (inpP) inpP.value = p;
  handleLoginSubmit();
}

export function logout() {
  apiFetch('/api/auth/logout', { method: 'POST' }).catch(() => {});
  setToken(null);
  setCurrentUser(null);
  setDualControlToken(null);
  resetLoginFormState();
  document.getElementById('page-login').classList.add('active');
  document.getElementById('page-login').style.display = 'block';
  document.getElementById('page-app').style.display = 'none';
  updateNotificationsUI();
}

export async function setup2FA() {
  const err = document.getElementById('security-error');
  if (err) err.style.display = 'none';
  
  try {
    const res = await apiFetch('/api/auth/2fa/setup', { method: 'POST' });
    document.getElementById('mfa-qr-code-img').src = res.qr_code;
    document.getElementById('mfa-secret-key').textContent = res.secret;
    document.getElementById('mfa-setup-section').style.display = 'block';
    document.getElementById('inp-2fa-verify-code').value = '';
    document.getElementById('inp-2fa-verify-code').focus();
  } catch(e) {
    if (err) {
      err.textContent = e.message;
      err.style.display = 'block';
    }
  }
}

export async function enable2FA() {
  const err = document.getElementById('security-error');
  const succ = document.getElementById('security-success');
  if (err) err.style.display = 'none';
  if (succ) succ.style.display = 'none';
  
  const code = document.getElementById('inp-2fa-verify-code').value.trim();
  if (!code) {
    if (err) {
      err.textContent = 'Please enter verification code.';
      err.style.display = 'block';
    }
    return;
  }
  
  try {
    const res = await apiFetch('/api/auth/2fa/enable', {
      method: 'POST',
      body: JSON.stringify({ code })
    });
    if (succ) {
      succ.textContent = res.message;
      succ.style.display = 'block';
    }
    
    if (window.loadSecuritySettings) {
      await window.loadSecuritySettings();
    }
  } catch(e) {
    if (err) {
      err.textContent = e.message;
      err.style.display = 'block';
    }
  }
}

export async function disable2FA() {
  const err = document.getElementById('security-error');
  const succ = document.getElementById('security-success');
  if (err) err.style.display = 'none';
  if (succ) succ.style.display = 'none';
  
  const code = document.getElementById('inp-2fa-disable-code').value.trim();
  if (!code) {
    if (err) {
      err.textContent = 'Please enter validation code to disable 2FA.';
      err.style.display = 'block';
    }
    return;
  }
  
  try {
    const res = await apiFetch('/api/auth/2fa/disable', {
      method: 'POST',
      body: JSON.stringify({ code })
    });
    if (succ) {
      succ.textContent = res.message;
      succ.style.display = 'block';
    }
    
    if (window.loadSecuritySettings) {
      await window.loadSecuritySettings();
    }
  } catch(e) {
    if (err) {
      err.textContent = e.message;
      err.style.display = 'block';
    }
  }
}

// Auto-bind login form submit
export function initAuthListeners() {
  const loginForm = document.getElementById('login-form');
  if (loginForm) {
    loginForm.addEventListener('submit', handleLoginSubmit);
  }
}

export async function registerPasskey() {
  const errEl = document.getElementById('security-error');
  const succEl = document.getElementById('security-success');
  if (errEl) errEl.style.display = 'none';
  if (succEl) succEl.style.display = 'none';

  const fail = (msg) => {
    if (errEl) {
      errEl.textContent = msg;
      errEl.style.display = 'block';
    } else {
      alert(msg);
    }
  };

  if (!passkeysSupported()) {
    fail('Passkeys / WebAuthn are not supported by this browser.');
    return;
  }

  const currentUser = getCurrentUser();
  if (!currentUser) {
    fail('You must be signed in to enroll a passkey.');
    return;
  }

  try {
    const { challenge } = await apiFetch('/api/v1/auth/webauthn/challenge');

    const credential = await navigator.credentials.create({
      publicKey: {
        challenge: b64urlToBytes(challenge),
        rp: { name: 'PsikoNot' },
        user: {
          id: new TextEncoder().encode(currentUser.username),
          name: currentUser.username,
          displayName: currentUser.full_name || currentUser.username
        },
        // ES256 only — the server verifies secp256r1 signatures.
        pubKeyCredParams: [{ type: 'public-key', alg: -7 }],
        authenticatorSelection: { userVerification: 'preferred', residentKey: 'preferred' },
        attestation: 'none',
        timeout: 60000
      }
    });

    if (!credential) throw new Error('Passkey enrollment was cancelled.');

    const spki = credential.response.getPublicKey ? credential.response.getPublicKey() : null;
    if (!spki) {
      throw new Error('This browser cannot export the passkey public key. Use Chrome 85+, Safari 15+, or Firefox 119+.');
    }

    await apiFetch('/api/v1/auth/webauthn/register', {
      method: 'POST',
      body: JSON.stringify({
        credential_id:    bytesToB64url(credential.rawId),
        public_key:       bytesToB64url(spki),
        client_data_json: bytesToB64url(credential.response.clientDataJSON)
      })
    });

    if (succEl) {
      succEl.textContent = 'Passkey enrolled. You can now sign in with this device from the login screen.';
      succEl.style.display = 'block';
    }
    addNotification('Passkey Registered', 'Your hardware Passkey / TouchID was successfully bound to your PsikoNot account.', 'success');
  } catch (err) {
    fail('Failed to enroll passkey: ' + (err.message || err));
  }
}

export async function loginWithPasskey() {
  const errEl = document.getElementById('login-error');
  if (errEl) errEl.style.display = 'none';

  const fail = (msg) => {
    if (errEl) {
      errEl.textContent = msg;
      errEl.style.display = 'block';
    }
  };

  if (!passkeysSupported()) {
    fail('Passkeys / WebAuthn are not supported by this browser.');
    return;
  }

  try {
    const { challenge } = await apiFetch('/api/v1/auth/webauthn/challenge');

    // The authenticator signs the server challenge; there is no client-side
    // fallback — an assertion is the only way past this point.
    const assertion = await navigator.credentials.get({
      publicKey: {
        challenge: b64urlToBytes(challenge),
        userVerification: 'preferred',
        timeout: 60000
      }
    });

    if (!assertion) throw new Error('No passkey was selected.');

    const loginRes = await apiFetch('/api/v1/auth/webauthn/login', {
      method: 'POST',
      body: JSON.stringify({
        credential_id:      bytesToB64url(assertion.rawId),
        signature:          bytesToB64url(assertion.response.signature),
        client_data_json:   bytesToB64url(assertion.response.clientDataJSON),
        authenticator_data: bytesToB64url(assertion.response.authenticatorData)
      })
    });

    setToken(loginRes.access_token);
    setCurrentUser(loginRes.user);
    addNotification('Passkey Authenticated', `Authenticated via Hardware Passkey for ${loginRes.user.username}`, 'success');
    if (window.enterApp) {
      window.enterApp();
    }
  } catch (err) {
    fail(err.message || 'Passkey authentication failed.');
  }
}


