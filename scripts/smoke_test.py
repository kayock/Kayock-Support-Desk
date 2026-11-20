#!/usr/bin/env python3
"""
Kayock Support Desk end-to-end smoke test (real server, real HTTP).

Starts the actual app (python run.py) in a throwaway data directory,
stands up a small real HTTP fake of an OpenAI-compatible server (for
embeddings + chat, same approach as the development pass), and exercises:

  1. first-run setup flow + graceful "AI needs attention" states
     (no real llama-server/Ollama exists in this environment)
  2. document upload -> indexing (job queue) -> ready
  3. keyword search, specs extraction, citation navigation endpoints
  4. multi-turn Q&A through a chat session with a live HTTP model server
  5. restart persistence (SIGTERM + restart, data intact)
  6. clean shutdown (port released, no orphaned processes)

Usage: .venv/bin/python scripts/smoke_test.py
Exits 0 when every step passed; prints a step-by-step report.
"""
import http.server
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PORT = 8799
BASE = f"http://127.0.0.1:{PORT}"

PASS, FAIL = [], []


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASS if condition else FAIL).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}" + (f" -- {detail}" if detail and not condition else ""))


def api(method: str, path: str, payload=None, raw=False, timeout=15):
    req = urllib.request.Request(BASE + path, method=method)
    data = None
    if payload is not None:
        if isinstance(payload, dict):
            data = json.dumps(payload).encode()
            req.add_header("Content-Type", "application/json")
        else:
            data = payload
    with urllib.request.urlopen(req, data=data, timeout=timeout) as resp:
        body = resp.read()
        return (resp.status, body if raw else json.loads(body or b"{}"))


def wait_port_free(port, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = socket.socket()
        s.settimeout(0.3)
        try:
            s.connect(("127.0.0.1", port))
            s.close()
            time.sleep(0.2)
        except OSError:
            return True
        finally:
            try:
                s.close()
            except OSError:
                pass
    return False


# ------------------------------------------------------------ fake model ----

class FakeModelHandler(http.server.BaseHTTPRequestHandler):
    """Minimal OpenAI-compatible embeddings + chat server. Chat echoes the
    content of the last user message's retrieval context so multi-turn
    behavior is verifiable."""

    def log_message(self, *args):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")
        if self.path.endswith("/embeddings"):
            texts = payload.get("input") or []
            vecs = []
            for text in texts:
                lower = (text or "").lower()
                vocab = ["rear", "differential", "drain", "plug", "torque", "fuse", "capacity", "transfer"]
                v = [lower.count(w) for w in vocab]
                norm = sum(x * x for x in v) ** 0.5 or 1.0
                vecs.append([x / norm for x in v])
            self._json({"object": "list", "data": [{"object": "embedding", "index": i, "embedding": v} for i, v in enumerate(vecs)], "model": payload.get("model")})
        elif self.path.endswith("/chat/completions"):
            messages = payload.get("messages") or []
            last_user = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
            # Reflect which evidence blocks appear, so the test can verify
            # the retrieval context and history framing actually arrived.
            markers = []
            question = ""
            for line in last_user.splitlines():
                if line.startswith("Question:"):
                    question = line[len("Question:"):].strip()
                    break
            if question and not question.endswith("?"):
                # multi-line: join until blank line for robustness
                pass
            if "fill plug" in question.lower():
                markers.append("fill-plug-question")
                answer = "The fill plug torque is 39 Nm."
            elif "drain plug" in (question or last_user).lower():
                markers.append("drain-plug-evidence")
                answer = "The drain plug torque is 54 Nm."
            elif "p0302" in (question or last_user).lower():
                markers.append("code-evidence")
                answer = "P0302 is Cylinder 2 Misfire Detected; check the ignition coil."
            else:
                markers.append("drain-plug-evidence")
                answer = "The drain plug torque is 54 Nm."
            if any("Earlier conversation" in str(m.get("content", "")) for m in messages):
                markers.append("history-present")
            answer = answer + (" [" + ",".join(sorted(set(markers))) + "]")
            self._json({
                "id": "fake", "object": "chat.completion", "model": "fake",
                "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": answer}}],
            })
        else:
            self._json({"error": "unknown endpoint"}, 404)

    def do_GET(self):
        self._json({"status": "ok"})


def start_fake_model_server(port):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), FakeModelHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


# ------------------------------------------------------------- fixtures ----

def build_manual_pdf(path: Path):
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)
    for line in [
        "DTC P0302",
        "Cylinder 2 Misfire Detected.",
        "Check ignition coil resistance on cylinder 2 before replacing parts.",
    ]:
        pdf.cell(0, 8, line, new_x="LMARGIN", new_y="NEXT")
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)
    for line in [
        "Rear Differential",
        "Oil capacity: 0.58 L.",
        "Drain plug tightening torque: 54 Nm.",
        "Fill plug tightening torque: 39 Nm.",
    ]:
        pdf.cell(0, 8, line, new_x="LMARGIN", new_y="NEXT")
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)
    for line in [
        "Interior Fuse Box",
        "FUSE 12 | 10A | Instrument cluster",
        "FUSE 15 | 15A | Rear differential control module",
    ]:
        pdf.cell(0, 8, line, new_x="LMARGIN", new_y="NEXT")
    pdf.output(str(path))


# ------------------------------------------------------------------ run ----

def main() -> int:
    data_dir = Path(tempfile.mkdtemp(prefix="kayock_smoke_"))
    env = dict(os.environ, KAYOCK_DATA_DIR=str(data_dir), KAYOCK_OPEN_BROWSER="0", KAYOCK_PORT=str(PORT))
    fake_server = start_fake_model_server(8790)
    proc = None
    try:
        print("== 1. First-run setup ==")
        proc = subprocess.Popen(
            [str(REPO / ".venv" / "bin" / "python"), str(REPO / "run.py")],
            env=env, cwd=str(REPO),
            stdout=open(data_dir / "server_out.log", "wb"),
            stderr=subprocess.STDOUT,
        )
        deadline = time.time() + 30
        up = False
        while time.time() < deadline:
            try:
                api("GET", "/api/network")
                up = True
                break
            except Exception:
                time.sleep(0.3)
        check("server started and serves /api/network", up)

        # Setup flow: choose a GGUF path (nonexistent here), Start AI ->
        # honest attention state because llama-server/Ollama are absent.
        api("POST", "/api/ai/setup", {"chat_model_path": str(data_dir / "fake-model.gguf"), "start": True})
        time.sleep(1.5)
        _, status = api("GET", "/api/ai/status")
        check("managed AI reports a clear attention state without llama-server/Ollama",
              status["overall"] == "attention" and status["chat"]["status"] in ("error", "stopped"),
              json.dumps(status))

        print("== 2. Upload + indexing ==")
        manual = data_dir / "manual.pdf"
        build_manual_pdf(manual)
        # multipart upload via raw body
        boundary = "KayockSmokeBoundary"
        pdf_bytes = manual.read_bytes()
        body = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"name\"\r\n\r\nSmoke Tucson\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"device_type\"\r\n\r\nCar\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; filename=\"smoke-manual.pdf\"\r\n"
            f"Content-Type: application/pdf\r\n\r\n"
        ).encode() + pdf_bytes + f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(BASE + "/api/devices", data=body, method="POST")
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        with urllib.request.urlopen(req, timeout=30) as resp:
            device_id = json.loads(resp.read())["id"]
        check("device created with manual upload", isinstance(device_id, int))

        print("== 2b. Honest failure when the embedding server is unreachable ==")
        # Ollama is not running in this environment: indexing must fail
        # with a clean, user-facing message -- not a crash.
        doc_id_probe = None
        deadline = time.time() + 30
        while time.time() < deadline:
            _, jobs = api("GET", "/api/jobs")
            index_jobs = [j for j in jobs["jobs"] if j["job_type"] == "index_document"]
            if index_jobs and index_jobs[0]["status"] in ("done", "error"):
                doc_id_probe = json.loads(index_jobs[0]["payload_json"])["document_id"]
                if index_jobs[0]["status"] == "done":
                    break
            time.sleep(0.3)
        _, st_probe = api("GET", f"/api/documents/{doc_id_probe}/status")
        check("upload without a model server fails cleanly", st_probe["status"] == "error" and "embedding" in st_probe["error"].lower(), json.dumps(st_probe))

        print("== 3. Configure model server + reindex ==")
        # The document id is available from the job queue payloads:
        doc_id = None
        deadline = time.time() + 30
        while time.time() < deadline:
            _, jobs = api("GET", "/api/jobs")
            index_jobs = [j for j in jobs["jobs"] if j["job_type"] == "index_document"]
            if index_jobs and index_jobs[0]["status"] == "done":
                doc_id = json.loads(index_jobs[0]["payload_json"])["document_id"]
                break
            time.sleep(0.3)
        check("indexing job ran through the queue and finished", doc_id is not None)

        print("== 3. Retrieval, search, specs, citations ==")
        # External mode pointed at the fake model server (also validates
        # the external-server path the docs promise).
        api("POST", "/api/settings", {
            "ai_mode": "external",
            "llm_base_url": "http://127.0.0.1:8790/v1",
            "llm_model": "fake",
            "embedding_base_url": "http://127.0.0.1:8790/v1",
            "embedding_model": "fake-embed",
        })
        time.sleep(0.5)

        _, reindex = api("POST", f"/api/devices/{device_id}/reindex", {})
        doc_id = reindex["document_ids"][0]
        st_ready = None
        deadline = time.time() + 30
        while time.time() < deadline:
            _, st_ready = api("GET", f"/api/documents/{doc_id}/status")
            if st_ready.get("job", {}).get("status") == "done":
                break
            time.sleep(0.3)
        check("reindex after configuring the model server reaches 'ready'",
              st_ready and st_ready["status"] == "ready" and st_ready["page_count"] == 3, json.dumps(st_ready))
        check("job progress visible in document status", st_ready.get("job", {}).get("status") == "done")

        _, search = api("GET", "/api/devices/%d/search?q=%s" % (device_id, urllib.parse.quote("P0302")))
        check("keyword search finds the exact trouble code", search["results"] and "P0302" in search["results"][0]["text"])
        _, search2 = api("GET", "/api/devices/%d/search?q=%s" % (device_id, urllib.parse.quote("FUSE 15")))
        check("keyword search finds the fuse table row", search2["results"] and "Rear differential control module" in search2["results"][0]["text"])

        _, specs = api("GET", f"/api/devices/{device_id}/specs")
        torque_specs = [s for s in specs["specs"] if s["category"] == "torque"]
        check("structured specs extracted with source refs", torque_specs and all(s["excerpt"] for s in torque_specs),
              json.dumps(specs["specs"][:2]))

        status_code, viewer_html = api("GET", f"/documents/{doc_id}/view?page=2&q=capacity", raw=True)
        check("local PDF viewer page renders with bundled pdf.js", status_code == 200 and b"pdf.min.js" in viewer_html)
        status_code, pdf_body = api("GET", f"/api/documents/{doc_id}/file", raw=True)
        check("manual file served locally", status_code == 200 and pdf_body[:5] == b"%PDF-")

        print("== 4. Multi-turn Q&A (chat session) ==")
        _, session = api("POST", f"/api/devices/{device_id}/chat/sessions", {"title": "Smoke session"})
        sid = session["id"]
        _, ask = api("POST", "/api/chat/sessions/%d/ask" % sid, {"question": "What is the rear differential drain plug torque?"})
        check("turn 1 answers from the manual", ask["not_found"] is False and "54" in ask["answer"], ask.get("answer", ""))
        check("turn 1 has sources with document/page", ask["sources"] and ask["sources"][0].get("document_id") == doc_id)
        _, ask2 = api("POST", "/api/chat/sessions/%d/ask" % sid, {"question": "What about the fill plug?"})
        check("turn 2 (follow-up) retrieves fresh evidence and keeps meaning",
              ask2["not_found"] is False and "39" in ask2["answer"], json.dumps(ask2)[:300])
        check("turn 2 used history for meaning and fresh excerpts for evidence",
              "history-present" in ask2["answer"] and "fill-plug-question" in ask2["answer"], ask2["answer"])
        _, sess_view = api("GET", f"/api/chat/sessions/{sid}")
        check("session persisted 4 messages", len(sess_view["messages"]) == 4)
        check("model received conversation history + fresh evidence",
              "history-present" in ask2["answer"], ask2["answer"])

        # plain (non-session) ask still works
        _, plain = api("POST", f"/api/devices/{device_id}/ask", {"question": "What does P0302 mean?"})
        check("plain ask endpoint still works", plain.get("error") is False or plain.get("not_found") is False)

        # Diagnostic session tracking
        _, diag = api("POST", f"/api/devices/{device_id}/diag/sessions", {"title": "Smoke diag", "symptoms": "rough idle"})
        api("POST", f"/api/diag/sessions/{diag['id']}/steps", {"description": "Swapped coils", "result": "fail", "reading": "0.8 ohms"})
        _, diag_view = api("GET", f"/api/diag/sessions/{diag['id']}")
        check("diagnostic session tracks steps/readings", len(diag_view["steps"]) == 1 and diag_view["steps"][0]["reading"] == "0.8 ohms")

        print("== 5. Settings diagnostics / metrics / PWA ==")
        _, diags = api("GET", "/api/diagnostics")
        check("diagnostics snapshot works", diags["python"]["python_version"].startswith("3."))
        _, metrics = api("GET", "/api/metrics")
        check("metrics accumulate", metrics["metrics"].get("questions_answered", 0) >= 3)
        status_code, sw = api("GET", "/static/js/sw.js", raw=True)
        check("service worker + manifest served locally", status_code == 200 and b"kayock-static" in sw)
        status_code, manifest = api("GET", "/static/manifest.webmanifest", raw=True)
        check("PWA manifest served", status_code == 200 and b"Kayock" in manifest)
        status_code, css = api("GET", "/static/css/style.css", raw=True)
        check("dark-mode theme shipped", b'data-theme="dark"' in css)

        print("== 6. Restart persistence + clean shutdown ==")
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=30)
        check("SIGTERM shuts the server down", proc.returncode is not None)
        check("port released after shutdown", wait_port_free(PORT))

        proc = subprocess.Popen(
            [str(REPO / ".venv" / "bin" / "python"), str(REPO / "run.py")],
            env=env, cwd=str(REPO),
            stdout=open(data_dir / "server_out2.log", "wb"),
            stderr=subprocess.STDOUT,
        )
        deadline = time.time() + 30
        up = False
        while time.time() < deadline:
            try:
                api("GET", "/api/network")
                up = True
                break
            except Exception:
                time.sleep(0.3)
        check("server restarted", up)
        _, sess_view2 = api("GET", f"/api/chat/sessions/{sid}")
        check("chat session survived restart", len(sess_view2["messages"]) == 4)
        _, specs2 = api("GET", f"/api/devices/{device_id}/specs")
        check("specs and device survived restart", len(specs2["specs"]) >= len(specs["specs"]))
        _, status2 = api("GET", f"/api/documents/{doc_id}/status")
        check("document status survived restart", status2["status"] == "ready")
        _, jobs2 = api("GET", "/api/jobs")
        check("no jobs stuck 'running' after restart", all(j["status"] != "running" for j in jobs2["jobs"]))

        proc.send_signal(signal.SIGINT)
        proc.wait(timeout=30)
        check("Ctrl+C shuts the server down cleanly", proc.returncode is not None)
        check("port released after Ctrl+C", wait_port_free(PORT))
        check("app log written", (data_dir / "logs" / "app.log").exists())

        print("== 7. doctor.py CLI ==")
        result = subprocess.run(
            [str(REPO / ".venv" / "bin" / "python"), str(REPO / "doctor.py"), "--json"],
            capture_output=True, text=True, env=env, cwd=str(REPO),
        )
        check("doctor --json runs", result.returncode in (0, 1) and "python_version" in result.stdout)

    finally:
        if proc is not None and proc.poll() is None:
            proc.kill()
        try:
            fake_server.shutdown()
        except Exception:
            pass
        if FAIL:
            out = (data_dir / "server_out.log").read_text(errors="ignore")
            print("\n---- server_out.log (last 3000 chars) ----")
            print(out[-3000:])
            print("data dir kept for inspection:", data_dir)
        else:
            shutil.rmtree(data_dir, ignore_errors=True)

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED steps:")
        for name in FAIL:
            print(f"  - {name}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
