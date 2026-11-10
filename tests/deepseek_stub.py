"""A stub DeepSeek: an OpenAI-compatible chat endpoint that enforces DeepSeek's tool-name rule.

DeepSeek answers HTTP 400 when a tool name is not unique or does not match ``[a-zA-Z0-9_-]{1,64}``.
The stub does the same, so a test that offers tools to it fails the way production would.
No real network call is made; the stub listens on a loopback port.
"""
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock, Thread

KEY = "sk-deepseek-stub-never-real-0001"
# The names DeepSeek's model list returns; any other name is refused, as the real service does once an alias is gone.
SERVED_MODELS = ("deepseek-flash", "deepseek-v4-pro")
NAME_RULE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


class DeepSeekStub:
    def __init__(self, reply="The deepseek stub answers.", tool_script=None, usage=(1000, 500)):
        self.reply = reply
        self.tool_script = list(tool_script or [])  # each item: (tool_name, arguments dict), answered in turn
        self.usage = usage
        self.requests = []
        self.keys_seen = []  # every bearer token received, accepted or not
        self.accepted = {KEY}  # keys the stub answers; any other gets 401
        self.fail_stream_after_first_chunk = False
        self._lock = Lock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _json(self, status, body):
                payload = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(payload)
                self.close_connection = True

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length) or b"{}")
                if self.path != "/v1/chat/completions":
                    return self._json(404, {"error": {"message": "unknown route"}})
                token = (self.headers.get("Authorization") or "")[len("Bearer "):]
                with owner._lock:
                    owner.keys_seen.append(token)
                if token not in owner.accepted:
                    return self._json(401, {"error": {"message": "invalid key"}})
                if body.get("model") not in SERVED_MODELS:
                    return self._json(400, {"error": {"message": "Model Not Exist"}})
                names = [t.get("function", {}).get("name", "") for t in body.get("tools") or []]
                bad = [n for n in names if not NAME_RULE.match(n)]
                if bad or len(set(names)) != len(names):
                    return self._json(400, {"error": {"message": "Tool names must be unique and match %s: %s" % (
                        NAME_RULE.pattern, bad[:3])}})
                with owner._lock:
                    owner.requests.append({"model": body.get("model"), "tool_names": names,
                                           "stream": bool(body.get("stream")),
                                           "thinking": (body.get("thinking") or {}).get("type"),
                                           "text": " ".join(str(m.get("content")) for m in body.get("messages", []))})
                    scripted = owner.tool_script.pop(0) if owner.tool_script and names else None
                usage = None if owner.usage is None else {
                    "prompt_tokens": owner.usage[0], "completion_tokens": owner.usage[1],
                    "total_tokens": sum(owner.usage)}
                base = {"id": "chatcmpl-stub", "object": "chat.completion", "created": 0, "model": body.get("model")}
                if scripted:
                    message = {"role": "assistant", "content": None, "tool_calls": [{
                        "id": "call_1", "type": "function",
                        "function": {"name": scripted[0], "arguments": json.dumps(scripted[1])}}]}
                    finish = "tool_calls"
                else:
                    # Thinking is on unless the request turns it off, and its text spends the budget: no answer.
                    thinking = (body.get("thinking") or {}).get("type") != "disabled"
                    message = {"role": "assistant", "content": "" if thinking else owner.reply}
                    finish = "stop"
                if body.get("stream"):
                    delta = dict(message)
                    if "tool_calls" in delta:
                        delta["tool_calls"] = [dict(c, index=0) for c in delta["tool_calls"]]
                    chunks = [
                        {**base, "object": "chat.completion.chunk",
                         "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                        {**base, "object": "chat.completion.chunk",
                         "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                         **({"usage": usage} if usage else {})},
                    ]
                    frames = b"".join(("data: " + json.dumps(c) + "\n\n").encode() for c in chunks) + b"data: [DONE]\n\n"
                    if owner.fail_stream_after_first_chunk:
                        # promise more than is sent, then drop: a stream that breaks part-way
                        self.send_response(200)
                        self.send_header("Content-Type", "text/event-stream")
                        self.send_header("Content-Length", str(len(frames) + 1000))
                        self.send_header("Connection", "close")
                        self.end_headers()
                        self.wfile.write(frames[: frames.index(b"\n\n") + 2])
                        self.wfile.flush()
                        self.close_connection = True
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Content-Length", str(len(frames)))
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.write(frames)
                    self.close_connection = True
                    return
                self._json(200, {**base, "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                                 **({"usage": usage} if usage else {})})

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.port = self._server.server_port
        self.base_url = "http://127.0.0.1:%d/v1" % self.port
        Thread(target=self._server.serve_forever, daemon=True).start()

    def close(self):
        self._server.shutdown()
        self._server.server_close()
