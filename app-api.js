(function () {
  const API_ROOT = "";

  async function request(path, options) {
    const opts = options || {};
    const headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
    const res = await fetch(API_ROOT + path, Object.assign({}, opts, { headers }));
    const raw = await res.text();
    let data;
    try {
      data = raw ? JSON.parse(raw) : {};
    } catch (e) {
      throw new Error(raw || "Unexpected server response");
    }

    if (!res.ok) {
      const message = (data && data.error) || "Request failed";
      const err = new Error(message);
      err.status = res.status;
      err.payload = data;
      throw err;
    }

    return data;
  }

  function getSession() {
    try {
      return JSON.parse(localStorage.getItem("tennis_session") || "{}");
    } catch (e) {
      return {};
    }
  }

  function setSession(session) {
    localStorage.setItem("tennis_session", JSON.stringify(session || {}));
  }

  function clearSession() {
    localStorage.removeItem("tennis_session");
  }

  function escapeHtml(value) {
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/\"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  window.TennisApi = {
    request,
    getSession,
    setSession,
    clearSession,
    escapeHtml,
  };
})();
