"""Demo server (stdlib only).

    python -m drip.server            # http://127.0.0.1:8765

One asyncio loop runs every job in a background thread; HTTP handlers talk to it through
thread-safe queues. Events reach the browser over Server-Sent Events.
"""

import argparse
import asyncio
import base64
import json
import mimetypes
import queue
import random
import threading
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .ads.engine import FrequencyCapper
from .app import build
from .config import ROOT, load_policy
from .corpus import SAMPLES
from .latency import LLM_LATENCIES, SECURITY_LATENCIES, Clock, LatencyProfile, LatencySimulator
from .models import SessionContext
from .predictor import llm_seconds, predict
from .security.pdf import extract_text

WEB = ROOT / "web"
TERMINAL = {"completed", "blocked", "error", "cancelled"}
MAX_UPLOAD = 20 * 1024 * 1024


class Runtime:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        self.clock = Clock()
        policy = load_policy()
        f = policy["ads"]["frequency"]
        self.capper = FrequencyCapper(f["max_ads_per_session"], f["min_interval_s"])
        self.orchestrators = {}
        self.simulator = LatencySimulator(seed=7)
        self.rng = random.Random(11)
        self.jobs: dict[str, dict] = {}
        self.lock = threading.Lock()

    def orchestrator(self, mode: str, strategy: str, surface: str):
        key = (mode, strategy, surface)
        if key not in self.orchestrators:
            self.orchestrators[key] = build(self.clock, mode, strategy, capper=self.capper, surface=surface)
        return self.orchestrators[key]

    def start(self, data: bytes | str, opts: dict) -> str:
        job_id = uuid.uuid4().hex[:12]
        kind = "prompt" if isinstance(data, str) else "pdf"
        upload = 0.6 if kind == "pdf" else 0.0

        # "auto": the wait follows the size of the request. The prediction sees only the request;
        # the "true" answer length deviates from it, like a real model's would.
        if kind == "prompt":
            pred = predict(data)
        else:
            ext = extract_text(data)
            pred = predict(opts.get("instruction", "") or "summarize", pages=ext.pages, input_chars=len(ext.text))
        true_tokens = max(20, int(pred.output_tokens * self.rng.lognormvariate(0, 0.35)))
        auto_sec, auto_llm = opts.get("security_s") in (None, "auto"), opts.get("llm_s") in (None, "auto")
        security_s = pred.security_s if auto_sec else float(opts["security_s"])
        llm_s = llm_seconds(true_tokens, pred.input_tokens) if auto_llm else float(opts["llm_s"])
        profile = LatencyProfile(
            upload_s=upload, security_s=security_s, llm_s=round(llm_s, 2),
            ad_failure=opts.get("ad_failure") or None,
            llm_failure=opts.get("llm_failure") or None,
            security_hang=bool(opts.get("security_hang")),
            output_tokens=true_tokens if auto_llm else None,
        )
        if auto_sec and auto_llm:
            estimate = round(upload + pred.total_s, 1)
        else:
            estimate = self.simulator.estimate(profile)
        session = SessionContext(
            session_id=str(opts.get("session_id") or "anon")[:64],
            tier="premium" if opts.get("tier") == "premium" else "free",
            contextual_ads_consent=opts.get("consent", True) is not False,
        )
        surface = "card" if opts.get("surface") == "card" else "status_line"
        orch = self.orchestrator(opts.get("mode", "mock"), opts.get("strategy", "adaptive"), surface)
        events: queue.Queue = queue.Queue()
        job = {"events": events, "review": None, "task": None, "clicks": []}

        async def review(report):
            fut = self.loop.create_future()
            job["review"] = fut
            try:
                return await asyncio.wait_for(fut, 120)
            except asyncio.TimeoutError:
                return False

        def on_event(ev):
            events.put(ev)

        async def run():
            on_event({"t": 0, "type": "job", "job_id": job_id, "kind": kind, "estimated_wait_s": estimate,
                      "prediction": pred.public(), "actual_output_tokens": true_tokens,
                      "profile": {"security_s": profile.security_s, "llm_s": profile.llm_s}})
            result = await orch.run(data, opts.get("instruction", ""), session, profile, estimate,
                                    on_event=on_event, review=review, job_id=job_id, kind=kind)
            on_event({"t": result.metrics.get("total_s"), "type": "result", "state": result.state.value,
                      "reason": result.reason, "security": result.security, "summary": result.summary,
                      "ad_log": result.ad_log, "exposures": result.exposures, "metrics": result.metrics,
                      "strategy": opts.get("strategy", "adaptive"), "mode": orch.security.name,
                      "surface": surface, "kind": kind, "prediction": pred.public(),
                      "actual_output_tokens": true_tokens if auto_llm else None})
            events.put(None)

        with self.lock:
            self.jobs[job_id] = job
        job["task"] = asyncio.run_coroutine_threadsafe(run(), self.loop)
        return job_id


class Handler(BaseHTTPRequestHandler):
    runtime: Runtime
    server_version = "drip-demo"

    def log_message(self, fmt, *args):
        pass

    # ------------------------------------------------------------------ helpers

    def send_json(self, obj, status=HTTPStatus.OK):
        body = json.dumps(obj, ensure_ascii=False, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_UPLOAD * 1.4:
            raise ValueError("payload too large")
        return json.loads(self.rfile.read(n) or b"{}")

    # ------------------------------------------------------------------ routes

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            return self.static("index.html")
        if path.startswith("/static/"):
            return self.static(path[len("/static/"):])
        if path == "/api/samples":
            return self.send_json([{"name": d.name, "title": d.title or d.name, "label": d.label, "kind": d.kind}
                                   for d in SAMPLES])
        if path == "/api/options":
            policy = load_policy()
            return self.send_json({"security_latencies": SECURITY_LATENCIES, "llm_latencies": LLM_LATENCIES,
                                   "strategies": ["immediate", "delayed", "adaptive"], "modes": ["mock", "sieve"],
                                   "policy": policy})
        if path.startswith("/api/jobs/") and path.endswith("/events"):
            return self.stream(path.split("/")[3])
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self):
        path = self.path.split("?")[0]
        try:
            body = self.read_json()
        except (ValueError, json.JSONDecodeError):
            return self.send_json({"error": "bad request"}, HTTPStatus.BAD_REQUEST)

        if path == "/api/jobs":
            if isinstance(body.get("prompt"), str):
                prompt = body["prompt"][:4000]
                if not prompt.strip():
                    return self.send_json({"error": "empty prompt"}, HTTPStatus.BAD_REQUEST)
                return self.send_json({"job_id": self.runtime.start(prompt, body)})
            if body.get("sample"):
                doc = next((d for d in SAMPLES if d.name == body["sample"]), None)
                if doc is None:
                    return self.send_json({"error": "unknown sample"}, HTTPStatus.BAD_REQUEST)
                data = doc.pdf()
            else:
                try:
                    data = base64.b64decode(body.get("file_b64", ""), validate=True)
                except ValueError:
                    return self.send_json({"error": "bad file"}, HTTPStatus.BAD_REQUEST)
                if len(data) > MAX_UPLOAD:
                    return self.send_json({"error": "file too large"}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return self.send_json({"job_id": self.runtime.start(data, body)})

        parts = path.strip("/").split("/")
        if len(parts) == 4 and parts[:2] == ["api", "jobs"]:
            job = self.runtime.jobs.get(parts[2])
            if not job:
                return self.send_json({"error": "unknown job"}, HTTPStatus.NOT_FOUND)
            if parts[3] == "cancel":
                job["task"].cancel()   # thread-safe; the orchestrator reports "cancelled"
                return self.send_json({"ok": True})
            if parts[3] == "review":
                fut = job["review"]
                if fut is not None and not fut.done():
                    self.runtime.loop.call_soon_threadsafe(fut.set_result, bool(body.get("proceed")))
                return self.send_json({"ok": True})
            if parts[3] == "click":
                job["clicks"].append(body.get("ad_id"))
                return self.send_json({"ok": True})
        if path == "/api/session/reset":
            self.runtime.capper.reset(str(body.get("session_id", "")))
            return self.send_json({"ok": True})
        self.send_error(HTTPStatus.NOT_FOUND)

    def static(self, rel: str):
        target = (WEB / rel).resolve()
        if WEB.resolve() not in target.parents or not target.is_file():
            return self.send_error(HTTPStatus.NOT_FOUND)
        body = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", (mimetypes.guess_type(target.name)[0] or "application/octet-stream")
                         + ("; charset=utf-8" if target.suffix in {".html", ".js", ".css"} else ""))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def stream(self, job_id: str):
        job = self.runtime.jobs.get(job_id)
        if not job:
            return self.send_error(HTTPStatus.NOT_FOUND)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            while True:
                try:
                    ev = job["events"].get(timeout=15)
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    continue
                if ev is None:
                    self.wfile.write(b"event: end\ndata: {}\n\n")
                    self.wfile.flush()
                    return
                self.wfile.write(b"data: " + json.dumps(ev, ensure_ascii=False, default=str).encode() + b"\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return


def main():
    ap = argparse.ArgumentParser(description="Latency monetization demo")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    Handler.runtime = Runtime()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Demo running at http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
