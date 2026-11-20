#!/usr/bin/env python3
"""
A tiny OpenAI-compatible demo model server for local development, testing,
and screenshot capture. NOT part of the running application: it lets
contributors exercise Kayock's full Q&A flow without downloading GGUF
models or Ollama.

Usage:
    .venv/bin/python scripts/fake_model_server.py [port]     # default 8791

Then point Kayock at it:
    Settings -> External / Advanced Server
      Chat model base URL:      http://127.0.0.1:8791/v1
      Chat model name:          fake
      Embedding model base URL: http://127.0.0.1:8791/v1
      Embedding model name:     fake-embed

Behavior: embeddings are deterministic bag-of-words vectors (stable across
runs); chat answers echo the drain/fill plug torques from the bundled demo
manual so demo screenshots are consistent. It implements only the two
endpoints Kayock uses (/v1/embeddings, /v1/chat/completions).
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VOCAB = ["rear", "differential", "drain", "plug", "torque", "fuse", "capacity", "transfer", "wheel", "hub"]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep demo output quiet
        pass

    def _json(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._json({"status": "ok", "server": "kayock-fake-model"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")
        if self.path.endswith("/embeddings"):
            texts = payload.get("input") or []
            vecs = []
            for text in texts:
                lower = (text or "").lower()
                v = [lower.count(w) for w in VOCAB]
                norm = sum(x * x for x in v) ** 0.5 or 1.0
                vecs.append([x / norm for x in v])
            self._json({
                "object": "list", "model": payload.get("model"),
                "data": [{"object": "embedding", "index": i, "embedding": v} for i, v in enumerate(vecs)],
            })
        elif self.path.endswith("/chat/completions"):
            messages = payload.get("messages") or []
            last_user = next((m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), "")
            question = next((line[len("Question:"):].strip() for line in last_user.splitlines()
                             if line.startswith("Question:")), last_user)
            lower = question.lower()
            if "fill plug" in lower and "drain plug" not in lower:
                answer = "The rear differential fill plug torque is 39 Nm."
            elif "hub" in lower:
                answer = "The hub nut torque is 220 Nm."
            elif "capacity" in lower:
                answer = "The rear differential oil capacity is 0.58 L."
            elif "fuse" in lower:
                answer = "FUSE 15 (15A) protects the rear differential control module."
            elif "p0302" in lower:
                answer = "P0302 is Cylinder 2 Misfire Detected. Check the ignition coil on cylinder 2."
            elif "drain plug" in lower:
                answer = "The rear differential drain plug torque is 54 Nm."
            else:
                answer = "The rear differential drain plug torque is 54 Nm."
            self._json({
                "id": "demo", "object": "chat.completion", "model": payload.get("model"),
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": answer}}],
            })
        else:
            self._json({"error": "unknown endpoint"}, 404)


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8791
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Kayock fake model server listening on http://127.0.0.1:{port}/v1 (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
