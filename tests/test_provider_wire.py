"""Opt-in native Codex test against an in-process Responses stub; no model service."""

import json
import os
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from mracbench.codex_exec import CodexExecAdapter
from mracbench.models import AgentRequest


@pytest.mark.skipif(
    os.environ.get("MRAC_CODEX_WIRE_SMOKE") != "1", reason="Opt-in native Codex wire smoke"
)
def test_native_codex_uses_custom_responses_provider(tmp_path, monkeypatch):
    if not shutil.which("codex"):
        pytest.skip("Codex CLI is not installed")
    captured = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            request = json.loads(raw)
            captured.append(
                {
                    "path": self.path,
                    "model": request["model"],
                    "authorization": self.headers.get("Authorization"),
                }
            )
            item = {
                "id": "msg_local",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": "WIRE_OK", "annotations": []}],
            }
            response = {
                "id": "resp_local",
                "object": "response",
                "created_at": 1,
                "status": "completed",
                "model": request["model"],
                "output": [item],
                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            }
            events = [
                {
                    "type": "response.created",
                    "response": {**response, "status": "in_progress", "output": []},
                },
                {
                    "type": "response.output_item.added",
                    "output_index": 0,
                    "item": {**item, "status": "in_progress", "content": []},
                },
                {
                    "type": "response.content_part.added",
                    "item_id": item["id"],
                    "output_index": 0,
                    "content_index": 0,
                    "part": {"type": "output_text", "text": "", "annotations": []},
                },
                {
                    "type": "response.output_text.delta",
                    "item_id": item["id"],
                    "output_index": 0,
                    "content_index": 0,
                    "delta": "WIRE_OK",
                },
                {
                    "type": "response.output_text.done",
                    "item_id": item["id"],
                    "output_index": 0,
                    "content_index": 0,
                    "text": "WIRE_OK",
                },
                {
                    "type": "response.content_part.done",
                    "item_id": item["id"],
                    "output_index": 0,
                    "content_index": 0,
                    "part": item["content"][0],
                },
                {"type": "response.output_item.done", "output_index": 0, "item": item},
                {"type": "response.completed", "response": response},
            ]
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            for number, event in enumerate(events):
                event["sequence_number"] = number
                self.wfile.write(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode())
            self.wfile.flush()
            self.close_connection = True

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    catalog = json.loads(
        (Path(__file__).parents[1] / "examples/providers/glm-models.json").read_bytes()
    )
    provider = {
        "id": "localwire",
        "base_url": f"http://127.0.0.1:{server.server_port}/v1",
        "env_key": "MRAC_LOCAL_WIRE_KEY",
        "model_catalog": catalog,
    }
    monkeypatch.setenv("MRAC_LOCAL_WIRE_KEY", "local-wire-fixture-key")
    try:
        result = CodexExecAdapter(provider=provider).run(
            AgentRequest(
                "Reply exactly WIRE_OK. Do not use tools.",
                tmp_path,
                tmp_path / "raw",
                30,
                model="glm-5.3-flash",
                reasoning_effort="high",
                skip_git_repo_check=True,
            )
        )
        assert result.success, result.stderr
        assert result.final_text.strip() == "WIRE_OK", result
        assert captured == [
            {
                "path": "/v1/responses",
                "model": "glm-5.3-flash",
                "authorization": "Bearer local-wire-fixture-key",
            }
        ]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
