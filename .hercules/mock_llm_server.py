"""OpenAI-compatible mock LLM server: the session agent IS the brain.

Each /v1/chat/completions request is written to mock_llm/req/<id>.json and the
server polls mock_llm/resp/<id>.json, where the session agent writes a full
OpenAI-style response. Non-streaming only.
"""

import json
import pathlib
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = pathlib.Path(__file__).resolve().parent / "mock_llm"
REQ_DIR = BASE / "req"
RESP_DIR = BASE / "resp"
WAIT_SECONDS = 600


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep stdout quiet
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        rid = uuid.uuid4().hex[:12]
        (REQ_DIR / f"{rid}.json").write_text(json.dumps({"id": rid, "url": self.path, "body": body}, indent=1))
        resp_path = RESP_DIR / f"{rid}.json"
        deadline = time.time() + WAIT_SECONDS
        while time.time() < deadline:
            if resp_path.exists():
                payload = json.loads(resp_path.read_text())
                out = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)
                return
            time.sleep(0.25)
        err = json.dumps({"error": {"message": "brain did not answer in time", "type": "brain_timeout"}}).encode()
        self.send_response(500)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(err)))
        self.end_headers()
        self.wfile.write(err)


if __name__ == "__main__":
    REQ_DIR.mkdir(parents=True, exist_ok=True)
    RESP_DIR.mkdir(parents=True, exist_ok=True)
    for stale in list(REQ_DIR.glob("*.json")) + list(RESP_DIR.glob("*.json")):
        stale.unlink()
    print("mock LLM (brain relay) on http://127.0.0.1:9999/v1", flush=True)
    ThreadingHTTPServer(("127.0.0.1", 9999), Handler).serve_forever()
