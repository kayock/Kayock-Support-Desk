// Compact AI status/control widget shown in the top nav on every page,
// so a user never has to visit a separate page just to unload the model.
(function () {
  const widget = document.getElementById("globalAiWidget");
  if (!widget) return;

  const dot = document.getElementById("globalAiDot");
  const label = document.getElementById("globalAiLabel");
  const btn = document.getElementById("globalAiBtn");

  function render(data) {
    const chat = data.chat || {};
    const embedding = data.embedding || {};
    dot.className = "dot dot-" + (data.overall === "ready" ? "running" : data.overall === "starting" ? "starting" : data.overall === "attention" ? "error" : "stopped");

    if (data.overall === "ready") {
      label.textContent = "AI Running";
      btn.textContent = "Stop AI";
      btn.disabled = false;
      btn.dataset.action = "stop";
    } else if (data.overall === "starting") {
      label.textContent = "Starting AI\u2026";
      btn.textContent = "Starting\u2026";
      btn.disabled = true;
      btn.dataset.action = "";
    } else if (data.overall === "attention") {
      const chatUp = chat.status === "running";
      const embUp = embedding.status === "running";
      if (chatUp && !embUp) {
        label.textContent = "Manual Search Stopped";
      } else if (!chatUp && embUp) {
        label.textContent = "Answering Model Stopped";
      } else {
        label.textContent = "AI needs attention";
      }
      btn.textContent = "Start AI";
      btn.disabled = false;
      btn.dataset.action = "start";
    } else {
      label.textContent = "AI Stopped";
      btn.textContent = "Start AI";
      btn.disabled = false;
      btn.dataset.action = "start";
    }
  }

  async function refresh() {
    try {
      const res = await fetch("/api/ai/status");
      const data = await res.json();
      render(data);
    } catch (e) {
      /* ignore -- keep showing the last known state */
    }
  }

  btn.addEventListener("click", async () => {
    const action = btn.dataset.action;
    if (!action) return;
    btn.disabled = true;
    try {
      await fetch(`/api/ai/${action}`, { method: "POST" });
    } catch (e) {
      /* ignore */
    }
    refresh();
  });

  refresh();
  setInterval(refresh, 4000);
})();
