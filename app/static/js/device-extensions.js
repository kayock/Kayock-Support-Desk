// Device-page extensions: chat sessions, manual search, structured specs,
// and the diagnostic log. Vanilla JS, no build step -- same style as the
// rest of Kayock's frontend. Every panel degrades to a friendly empty
// state; none of them block the existing Q&A flow.
(function () {
  "use strict";

  // Device id from the page URL (/devices/{id}) -- keeps this script
  // independent of script-load order relative to the inline page script.
  const deviceId = parseInt((location.pathname.match(/\/devices\/(\d+)/) || [])[1], 10);
  if (!deviceId) return;

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function viewerLinkFor(s) {
    if (!s.document_id) return "";
    const q = encodeURIComponent((s.excerpt || "").slice(0, 70).trim());
    const pagePart = s.page ? "page=" + s.page : "";
    const qPart = q ? (pagePart ? "&" : "") + "q=" + q : "";
    return `<a href="/documents/${s.document_id}/view?${pagePart}${qPart}" target="_blank" rel="noopener">${s.page ? "Open page " + s.page + " \u2197" : "Open manual \u2197"}</a>`;
  }

  // ---------------------------------------------------- search manuals ----
  const searchInput = document.getElementById("deviceSearchInput");
  const searchBtn = document.getElementById("deviceSearchBtn");
  const searchResults = document.getElementById("deviceSearchResults");

  async function runSearch() {
    const q = (searchInput.value || "").trim();
    if (!q) return;
    searchResults.style.display = "block";
    searchResults.innerHTML = "<span class='hint'>Searching…</span>";
    try {
      const res = await fetch(`/api/devices/${deviceId}/search?q=${encodeURIComponent(q)}`);
      const data = await res.json();
      const results = data.results || [];
      if (!results.length) {
        searchResults.innerHTML = "<span class='hint'>No matches in the indexed manuals.</span>";
        return;
      }
      searchResults.innerHTML = results.map((r) => `
        <div class="search-result">
          <div class="sr-head">${esc(r.meta.filename)} · page ${r.meta.page}${r.meta.section ? ` · ${esc(r.meta.section)}` : ""} ${viewerLinkFor(r)}</div>
          <div class="sr-text">${esc(r.text.slice(0, 400))}${r.text.length > 400 ? "…" : ""}</div>
        </div>`).join("");
    } catch (e) {
      searchResults.innerHTML = "<span class='hint'>Search failed — is the server reachable?</span>";
    }
  }
  if (searchBtn) searchBtn.addEventListener("click", runSearch);
  if (searchInput) searchInput.addEventListener("keydown", (e) => { if (e.key === "Enter") runSearch(); });

  // ------------------------------------------------------ chat sessions ----
  const chatList = document.getElementById("chatSessionList");
  const chatHistory = document.getElementById("chatHistory");
  const chatInput = document.getElementById("chatInput");
  const chatSend = document.getElementById("chatSendBtn");
  const chatHint = document.getElementById("chatActiveHint");
  let activeSession = null;

  async function loadSessions() {
    if (!chatList) return;
    try {
      const res = await fetch(`/api/devices/${deviceId}/chat/sessions`);
      const data = await res.json();
      const sessions = data.sessions || [];
      if (!sessions.length) {
        chatList.innerHTML = "<div class='session-row' style='cursor:default;'><span class='hint'>No sessions yet.</span></div>";
        return;
      }
      chatList.innerHTML = sessions.map((s) => `
        <div class="session-row ${activeSession === s.id ? "active" : ""}" data-session="${s.id}">
          <span class="session-title">${esc(s.title)}</span>
          <span class="session-meta">${s.message_count} msg</span>
          <button class="icon-btn" data-del-session="${s.id}" title="Delete session">&#10005;</button>
        </div>`).join("");
    } catch (e) {
      chatList.innerHTML = "<div class='session-row'><span class='hint'>Could not load sessions.</span></div>";
    }
  }

  async function openSession(id) {
    activeSession = id;
    const session = await (await fetch(`/api/chat/sessions/${id}`)).json();
    const msgs = session.messages || [];
    chatHistory.innerHTML = msgs.length
      ? msgs.map((m) => `
        <div class="chat-msg ${esc(m.role)}">
          <div class="chat-meta">${m.role === "user" ? "You" : "Kayock"} · ${esc((m.created_at || "").slice(0, 16).replace("T", " "))}</div>
          <div class="answer-text">${esc(m.content)}</div>
          ${m.sources && m.sources.length ? `<div class="sources" style="margin-top:8px;">${m.sources.map((s) => `
            <div class="source-item"><div class="src-head">${esc(s.filename)}${s.page ? " · page " + s.page : ""} ${viewerLinkFor(s)}</div></div>`).join("")}</div>` : ""}
        </div>`).join("")
      : "<span class='hint'>Empty session — ask something below.</span>";
    chatHistory.scrollTop = chatHistory.scrollHeight;
    chatHint.textContent = `Session: ${session.title}`;
    loadSessions();
  }

  async function newSession() {
    const res = await fetch(`/api/devices/${deviceId}/chat/sessions`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
    });
    const data = await res.json();
    await loadSessions();
    openSession(data.id);
    chatInput.focus();
  }

  async function askInSession() {
    const question = (chatInput.value || "").trim();
    if (!question || !activeSession) {
      if (!activeSession) showToast("Start or pick a chat session first.");
      return;
    }
    chatInput.value = "";
    chatSend.disabled = true;
    chatHistory.insertAdjacentHTML("beforeend", `<div class="chat-msg user"><div class="chat-meta">You</div><div class="answer-text">${esc(question)}</div></div><div id="chatThinking" class="chat-msg"><span class="spinner"></span> Searching the manuals…</div>`);
    chatHistory.scrollTop = chatHistory.scrollHeight;
    try {
      const res = await fetch(`/api/chat/sessions/${activeSession}/ask`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question }),
      });
      const data = await res.json();
      await openSession(activeSession);
    } catch (e) {
      showToast("Could not reach the server.");
      document.getElementById("chatThinking") && document.getElementById("chatThinking").remove();
    } finally {
      chatSend.disabled = false;
    }
  }

  if (chatList) {
    chatList.addEventListener("click", (e) => {
      const del = e.target.closest("[data-del-session]");
      if (del) {
        e.stopPropagation();
        fetch(`/api/chat/sessions/${del.dataset.delSession}`, { method: "DELETE" }).then(() => {
          if (String(activeSession) === del.dataset.delSession) {
            activeSession = null;
            chatHistory.innerHTML = "<span class='hint'>Pick a session on the left, or start a new one.</span>";
            chatHint.textContent = "No session selected.";
          }
          loadSessions();
        });
        return;
      }
      const row = e.target.closest("[data-session]");
      if (row) openSession(parseInt(row.dataset.session, 10));
    });
  }
  if (document.getElementById("newChatBtn")) document.getElementById("newChatBtn").addEventListener("click", newSession);
  if (document.getElementById("refreshChatBtn")) document.getElementById("refreshChatBtn").addEventListener("click", loadSessions);
  if (chatSend) chatSend.addEventListener("click", askInSession);
  if (chatInput) chatInput.addEventListener("keydown", (e) => { if (e.key === "Enter") askInSession(); });
  if (chatList) loadSessions();

  // ---------------------------------------------------------- specs panel ----
  const specsPanel = document.getElementById("specsPanel");
  const specsFilter = document.getElementById("specsFilter");

  async function loadSpecs() {
    if (!specsPanel) return;
    const q = (specsFilter.value || "").trim();
    try {
      const res = await fetch(`/api/devices/${deviceId}/specs${q ? "?q=" + encodeURIComponent(q) : ""}`);
      const data = await res.json();
      const specs = data.specs || [];
      if (!specs.length) {
        specsPanel.innerHTML = "<span class='hint'>No extracted specifications yet. They are extracted automatically when a manual is indexed; \"Re-extract\" rescan is available above.</span>";
        return;
      }
      specsPanel.innerHTML = `<table class="spec-table">
        <thead><tr><th>Value</th><th>Category</th><th>Component / property</th><th>Source</th><th>Status</th></tr></thead>
        <tbody>${specs.map((s) => `
          <tr>
            <td class="spec-value">${esc(s.value_text)}</td>
            <td>${esc(s.category)}</td>
            <td>${esc(s.component || "—")}${s.property ? ` <span class="hint">(${esc(s.property)})</span>` : ""}</td>
            <td>${esc(s.original_filename)} · page ${s.page}${s.section ? ` · ${esc(s.section)}` : ""} <a href="/documents/${s.document_id}/view?page=${s.page}" target="_blank" rel="noopener">view</a></td>
            <td><span class="spec-badge ${esc(s.status)}">${esc(s.status)}</span></td>
          </tr>`).join("")}</tbody></table>
        <p class="hint" style="margin-bottom:0;">Candidate rows are pattern matches not yet confirmed for the requested component — never treat them as factory values without checking the source page.</p>`;
    } catch (e) {
      specsPanel.innerHTML = "<span class='hint'>Could not load specifications.</span>";
    }
  }
  if (specsPanel) {
    loadSpecs();
    document.getElementById("specsRefreshBtn").addEventListener("click", loadSpecs);
    specsFilter.addEventListener("keydown", (e) => { if (e.key === "Enter") loadSpecs(); });
    document.getElementById("specsExtractBtn").addEventListener("click", async () => {
      const btn = document.getElementById("specsExtractBtn");
      btn.disabled = true;
      try {
        const res = await fetch(`/api/devices/${deviceId}/specs/extract`, { method: "POST" });
        const data = await res.json();
        showToast(`Extraction added ${data.added} new value(s).`);
        loadSpecs();
      } finally {
        btn.disabled = false;
      }
    });
  }

  // ------------------------------------------------------- diagnostic log ----
  const diagSessions = document.getElementById("diagSessions");

  async function loadDiag() {
    if (!diagSessions) return;
    try {
      const res = await fetch(`/api/devices/${deviceId}/diag/sessions`);
      const data = await res.json();
      const sessions = data.sessions || [];
      if (!sessions.length) {
        diagSessions.innerHTML = "<span class='hint'>No diagnostic logs yet.</span>";
        return;
      }
      const details = await Promise.all(sessions.map((s) => fetch(`/api/diag/sessions/${s.id}`).then((r) => r.json())));
      diagSessions.innerHTML = details.map((s) => `
        <div class="diag-session" data-diag="${s.id}">
          <div style="display:flex; justify-content:space-between; gap:8px; align-items:center; flex-wrap:wrap;">
            <h4 style="margin:0;">${esc(s.title || "Untitled log")} <span class="hint">(${esc(s.status)})</span></h4>
            <div style="display:flex; gap:6px;">
              ${s.status !== "resolved" ? `<button class="btn secondary btn-sm" data-diag-resolve="${s.id}">Mark resolved</button>` : ""}
              <button class="icon-btn" data-diag-del="${s.id}" title="Delete log">&#10005;</button>
            </div>
          </div>
          ${s.symptoms ? `<div class="diag-symptom"><strong>Symptoms:</strong> ${esc(s.symptoms)}</div>` : ""}
          ${s.outcome ? `<div class="diag-symptom"><strong>Outcome:</strong> ${esc(s.outcome)}</div>` : ""}
          ${(s.steps || []).map((st) => `
            <div class="diag-step">
              <span class="step-no">${st.step_index}.</span>
              <span class="step-desc">${esc(st.description)}${st.notes ? `<div class="hint">${esc(st.notes)}</div>` : ""}</span>
              ${st.reading ? `<span class="step-reading">${esc(st.reading)}</span>` : ""}
              ${st.result ? `<span class="step-badge ${esc(st.result)}">${esc(st.result.replace("_", " "))}</span>` : ""}
              <button class="icon-btn" data-diag-del-step="${st.id}" title="Remove step">&#10005;</button>
            </div>`).join("")}
          <div class="diag-new-step">
            <div class="row-inline">
              <input type="text" placeholder="Next step tested…" data-diag-desc="${s.id}">
              <input type="text" placeholder="Measured reading (optional)" data-diag-reading="${s.id}">
              <select data-diag-result="${s.id}">
                <option value="">result…</option>
                <option value="pass">pass</option>
                <option value="fail">fail</option>
                <option value="ruled_out">ruled out</option>
              </select>
            </div>
            <button class="btn secondary btn-sm" data-diag-add-step="${s.id}">+ Add step</button>
          </div>
        </div>`).join("");
    } catch (e) {
      diagSessions.innerHTML = "<span class='hint'>Could not load diagnostic logs.</span>";
    }
  }

  if (diagSessions) {
    document.getElementById("diagNewBtn").addEventListener("click", async () => {
      const title = document.getElementById("diagNewTitle").value.trim();
      const symptoms = document.getElementById("diagNewSymptoms").value.trim();
      if (!title && !symptoms) { showToast("Give the log a title or symptoms first."); return; }
      await fetch(`/api/devices/${deviceId}/diag/sessions`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title, symptoms }),
      });
      document.getElementById("diagNewTitle").value = "";
      document.getElementById("diagNewSymptoms").value = "";
      loadDiag();
    });

    diagSessions.addEventListener("click", async (e) => {
      const add = e.target.closest("[data-diag-add-step]");
      if (add) {
        const sid = add.dataset.diagAddStep;
        const desc = diagSessions.querySelector(`[data-diag-desc="${sid}"]`).value.trim();
        if (!desc) { showToast("Describe the step first."); return; }
        await fetch(`/api/diag/sessions/${sid}/steps`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            description: desc,
            reading: diagSessions.querySelector(`[data-diag-reading="${sid}"]`).value.trim(),
            result: diagSessions.querySelector(`[data-diag-result="${sid}"]`).value,
          }),
        });
        loadDiag();
        return;
      }
      const delStep = e.target.closest("[data-diag-del-step]");
      if (delStep) {
        await fetch(`/api/diag/steps/${delStep.dataset.diagDelStep}`, { method: "DELETE" });
        loadDiag();
        return;
      }
      const resolve = e.target.closest("[data-diag-resolve]");
      if (resolve) {
        await fetch(`/api/diag/sessions/${resolve.dataset.diagResolve}`, {
          method: "PATCH", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ status: "resolved" }),
        });
        loadDiag();
        return;
      }
      const del = e.target.closest("[data-diag-del]");
      if (del) {
        await fetch(`/api/diag/sessions/${del.dataset.diagDel}`, { method: "DELETE" });
        loadDiag();
      }
    });
    loadDiag();
  }
})();
