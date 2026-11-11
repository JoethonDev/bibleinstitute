/* Browser WebAuthn passkey enrollment, login and revocation. */
(function () {
  "use strict";
  const csrf = (document.querySelector('meta[name="csrf-token"]') || document.querySelector('input[name="csrfmiddlewaretoken"]') || {}).value
    || (document.querySelector('meta[name="csrf-token"]') || {}).content || "";

  const isArabic = (document.documentElement.lang || "ar").startsWith("ar");
  const T = isArabic ? {
    unsupported: "هذا المتصفح لا يدعم مفاتيح المرور.",
    label: "أدخل اسماً لمفتاح المرور.",
    cancelled: "أُلغي إنشاء مفتاح المرور.",
    added: "أُضيف مفتاح المرور. جارٍ إعادة التحميل...",
    addFailed: "تعذر إضافة مفتاح المرور.",
    removeConfirm: "إزالة مفتاح المرور هذا؟",
    removed: "أُزيل مفتاح المرور. جارٍ إعادة التحميل...",
    removeFailed: "تعذر إزالة مفتاح المرور.",
    loginCancelled: "أُلغي تسجيل الدخول بمفتاح المرور.",
    loginFailed: "تعذر تسجيل الدخول بمفتاح المرور."
  } : {
    unsupported: "This browser does not support passkeys.",
    label: "Enter a name for this passkey.",
    cancelled: "Passkey creation was cancelled.",
    added: "Passkey added. Reloading...",
    addFailed: "Could not add the passkey.",
    removeConfirm: "Remove this passkey?",
    removed: "Passkey removed. Reloading...",
    removeFailed: "Could not remove the passkey.",
    loginCancelled: "Passkey sign-in was cancelled.",
    loginFailed: "Could not sign in with a passkey."
  };
  function b64(buffer) {
    const bytes = new Uint8Array(buffer);
    let binary = "";
    for (let index = 0; index < bytes.length; index += 1) binary += String.fromCharCode(bytes[index]);
    return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
  }

  function fromB64(value) {
    const padded = value.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - value.length % 4) % 4);
    const binary = atob(padded);
    const bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
    return bytes;
  }

  function toCredentialJson(credential) {
    const response = credential.response;
    const result = {
      id: credential.id,
      rawId: b64(credential.rawId),
      type: credential.type,
      clientExtensionResults: credential.getClientExtensionResults ? credential.getClientExtensionResults() : {},
      response: {},
    };
    if (response.clientDataJSON) result.response.clientDataJSON = b64(response.clientDataJSON);
    if (response.attestationObject) result.response.attestationObject = b64(response.attestationObject);
    if (response.authenticatorData) result.response.authenticatorData = b64(response.authenticatorData);
    if (response.signature) result.response.signature = b64(response.signature);
    if (response.userHandle) result.response.userHandle = b64(response.userHandle);
    if (typeof response.getTransports === "function") result.response.transports = response.getTransports();
    return result;
  }

  function prepareCreationOptions(options) {
    options.challenge = fromB64(options.challenge);
    if (options.user && options.user.id) options.user.id = fromB64(options.user.id);
    (options.excludeCredentials || []).forEach((credential) => {
      credential.id = fromB64(credential.id);
    });
    return options;
  }

  function prepareRequestOptions(options) {
    options.challenge = fromB64(options.challenge);
    (options.allowCredentials || []).forEach((credential) => {
      credential.id = fromB64(credential.id);
    });
    return options;
  }

  async function postJson(url, payload) {
    const response = await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
      body: JSON.stringify(payload || {}),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error((data.error && data.error.message) || "Request failed.");
    return data;
  }

  const profileRoot = document.querySelector("[data-passkey-root]");
  if (profileRoot) {
    const status = profileRoot.querySelector("[data-passkey-status]");
    const add = profileRoot.querySelector("[data-passkey-add]");
    const labelInput = profileRoot.querySelector("[data-passkey-label]");
    const setStatus = (message, error) => {
      if (!status) return;
      status.textContent = message || "";
      status.style.color = error ? "#9a2f2f" : "";
    };
    if (add) {
      add.addEventListener("click", async () => {
        add.disabled = true;
        try {
          if (!window.PublicKeyCredential || !navigator.credentials || !window.isSecureContext) {
            throw new Error(T.unsupported);
          }
          const label = (labelInput && labelInput.value || "").trim();
          if (!label) throw new Error(T.label);
          const data = await postJson(profileRoot.dataset.optionsUrl, {});
          const publicKey = prepareCreationOptions(data.publicKey);
          const credential = await navigator.credentials.create({ publicKey });
          if (!credential) throw new Error(T.cancelled);
          await postJson(profileRoot.dataset.verifyUrl, {
            challenge_id: data.challenge_id,
            label,
            credential: toCredentialJson(credential),
          });
          setStatus(T.added, false);
          window.location.reload();
        } catch (error) {
          setStatus(error.message || T.addFailed, true);
          add.disabled = false;
        }
      });
    }
    profileRoot.querySelectorAll("[data-passkey-revoke]").forEach((button) => {
      button.addEventListener("click", async () => {
        if (!window.confirm(T.removeConfirm)) return;
        button.disabled = true;
        try {
          await postJson(profileRoot.dataset.revokeUrl, { credential_id: button.dataset.passkeyRevoke });
          setStatus(T.removed, false);
          window.location.reload();
        } catch (error) {
          setStatus(error.message || T.removeFailed, true);
          button.disabled = false;
        }
      });
    });
  }

  const loginRoot = document.querySelector("[data-passkey-login]");
  if (loginRoot) {
    const button = loginRoot.querySelector("[data-passkey-login-button]");
    const status = loginRoot.querySelector("[data-passkey-login-status]");
    const setStatus = (message, error) => {
      if (!status) return;
      status.textContent = message || "";
      status.style.color = error ? "#9a2f2f" : "";
    };
    if (button) {
      button.hidden = false;
      button.addEventListener("click", async () => {
        button.disabled = true;
        try {
          if (!window.PublicKeyCredential || !navigator.credentials || !window.isSecureContext) {
            throw new Error(T.unsupported);
          }
          const data = await postJson(loginRoot.dataset.optionsUrl, {});
          const publicKey = prepareRequestOptions(data.publicKey);
          const credential = await navigator.credentials.get({ publicKey });
          if (!credential) throw new Error(T.loginCancelled);
          const result = await postJson(loginRoot.dataset.verifyUrl, {
            challenge_id: data.challenge_id,
            credential: toCredentialJson(credential),
            next: loginRoot.dataset.next || "",
          });
          window.location.assign(result.redirect || "/");
        } catch (error) {
          setStatus(error.message || T.loginFailed, true);
          button.disabled = false;
        }
      });
    }
  }
})();
