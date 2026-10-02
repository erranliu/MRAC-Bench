"""Forward Responses unchanged and retain OpenRouter's per-request billing metadata."""

import hmac
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from mrac_contracts.execution import atomic


def uses_openrouter(provider):
    return bool(provider and urlsplit(provider["base_url"]).hostname == "openrouter.ai")


class OpenRouterAccounting:
    def __init__(self, provider, secret, raw, timeout=3600):
        self.provider, self.secret = provider, secret
        self.path = raw / "provider-usage.json"
        self.requests = []
        self.timeout = timeout
        self.lock = threading.Lock()

    def save(self):
        atomic(
            self.path, {"schema_version": 1, "provider": "openrouter", "requests": self.requests}
        )

    def capture(self, entry, event):
        if not isinstance(event, dict):
            return
        response = event.get("response") or event
        if not isinstance(response, dict):
            return
        with self.lock:
            changed = False
            if isinstance(response.get("id"), str) and response["id"] != entry["response_id"]:
                entry["response_id"] = response["id"]
                changed = True
            if isinstance(response.get("usage"), dict):
                entry["usage"] = response["usage"]
                changed = True
            if changed:
                self.save()

    def __enter__(self):
        accounting = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass  # No request bodies, headers or credentials in logs.

            def do_POST(self):
                if self.path != "/responses" or not hmac.compare_digest(
                    self.headers.get("Authorization", ""), "Bearer " + accounting.secret
                ):
                    self.send_error(403)
                    return
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                headers = {
                    key: value
                    for key, value in self.headers.items()
                    if key.lower()
                    not in {"host", "connection", "content-length", "accept-encoding"}
                }
                headers["Accept-Encoding"] = "identity"
                request = Request(
                    accounting.provider["base_url"] + "/responses", body, headers, method="POST"
                )
                with accounting.lock:
                    entry = {
                        "request_index": len(accounting.requests) + 1,
                        "response_id": None,
                        "usage": None,
                        "finished": False,
                    }
                    accounting.requests.append(entry)
                    accounting.save()
                try:
                    try:
                        upstream = urlopen(request, timeout=accounting.timeout)
                    except HTTPError as exc:
                        upstream = exc  # Forward HTTP failures without changing retry behavior.
                    with upstream:
                        with accounting.lock:
                            entry["http_status"] = upstream.status
                            accounting.save()
                        self.send_response(upstream.status)
                        for key, value in upstream.headers.items():
                            if key.lower() not in {
                                "connection",
                                "transfer-encoding",
                                "content-length",
                            }:
                                self.send_header(key, value)
                        self.end_headers()
                        streaming = "text/event-stream" in upstream.headers.get("Content-Type", "")
                        pending = b""
                        while chunk := upstream.read1(65536):
                            pending += chunk
                            if streaming:
                                while b"\n" in pending:
                                    line, pending = pending.split(b"\n", 1)
                                    if line.startswith(b"data:"):
                                        try:
                                            accounting.capture(entry, json.loads(line[5:]))
                                        except ValueError:
                                            pass
                            self.wfile.write(chunk)
                            self.wfile.flush()
                        if not streaming:
                            try:
                                accounting.capture(entry, json.loads(pending))
                            except ValueError:
                                pass
                        with accounting.lock:
                            entry["finished"] = True
                            accounting.save()
                except OSError:
                    with accounting.lock:
                        entry["issue"] = "transport_interrupted"
                        accounting.save()
                    self.close_connection = True

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.save()
        return f"http://127.0.0.1:{self.server.server_port}"

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        # Responses may omit cost on interrupted streams. Query recorded generation IDs
        # for billing only; never repeat the model request or infer a price.
        for entry in self.requests:
            if (entry.get("usage") or {}).get("cost") is not None or not entry["response_id"]:
                continue
            try:
                request = Request(
                    self.provider["base_url"]
                    + "/generation?"
                    + urlencode({"id": entry["response_id"]}),
                    headers={"Authorization": "Bearer " + self.secret},
                )
                with urlopen(request, timeout=15) as response:
                    generation = json.load(response)["data"]
                usage = dict(entry.get("usage") or {})
                usage["cost"] = generation.get("total_cost")
                usage["generation"] = {
                    key: generation.get(key)
                    for key in (
                        "id",
                        "total_cost",
                        "native_tokens_prompt",
                        "native_tokens_completion",
                    )
                }
                entry["usage"] = usage
            except (OSError, ValueError, KeyError, TypeError):
                entry["billing_issue"] = "generation_usage_unavailable"
        with self.lock:
            self.save()
