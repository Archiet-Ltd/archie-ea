"""Explicit test-only OpenAI protocol peer. Never a production response fallback."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Lock, Thread

MODEL = 'ci-protocol-stub-v1'
TOKEN = 'ci-protocol-only-not-a-real-key'
CHAT = {
    'marker': 'ARCHIE_CI_CHAT_PROTOCOL_V1',
    'prompt': 'ARCHIE_CI_CHAT_PROTOCOL_V1: Confirm this transport fixture.',
    'reply': 'CI protocol fixture: streamed chat transport completed. No model advice was generated.',
    'stream': True,
}
GUIDE = {
    'marker': 'ARCHIE_CI_GUIDE_PROTOCOL_V1',
    'prompt': 'ARCHIE_CI_GUIDE_PROTOCOL_V1: Explain this fixture page.',
    'reply': 'CI protocol fixture: page guide transport completed. This is a deterministic test response.',
    'stream': False,
}
# The marker is embedded verbatim inside the free-text 'description' these two
# features send to the LLM (app/modules/solutions_strategic/v2/services/
# programme_setup_service.py::ai_prefill_programme and app/modules/
# business_case/ai_service.py::generate_case_seed both interpolate the caller's
# description straight into their prompt), so a test that types PROMPT as the
# description makes the real outbound request carry this marker with no
# product-code change needed to support the fixture.
PROGRAMME_PREFILL = {
    'marker': 'ARCHIE_CI_PROGRAMME_PREFILL_V1',
    'prompt': 'ARCHIE_CI_PROGRAMME_PREFILL_V1: consolidate four regional CRM instances into one.',
    'reply': json.dumps({
        'name': 'CI Fixture: CRM Consolidation',
        'objective': 'Consolidate four regional CRM instances onto one platform.',
        'workstream_type': 'application_rationalisation',
        'business_units': ['Sales', 'Support'],
        'outcome_statement': 'One CRM platform serving every region.',
        'direction': 'decrease',
        'metric_name': 'Number of CRM instances',
        'unit': 'instances',
        'baseline_value': 4,
        'target_value': 1,
        'target_date': None,
    }),
    'stream': False,
}
CASE_SEED = {
    'marker': 'ARCHIE_CI_CASE_SEED_V1',
    'prompt': 'ARCHIE_CI_CASE_SEED_V1: four duplicate CRM instances, no shared customer view.',
    'reply': json.dumps({
        'title': 'CI Fixture: Consolidate Regional CRM Instances',
        'description': 'Four regional CRM instances duplicate licence costs with no shared customer view.',
    }),
    'stream': False,
}


class AIProtocolStub:
    """Loopback HTTP transport, strict scenarios and a metadata-only request ledger."""

    def __init__(self):
        self._records = []
        self._sent_text = []
        self._lock = Lock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *args):
                # Never log Authorization or prompt content, even on failures.
                pass

            def _record(self, status, scenario=None, model=None, stream=False):
                with owner._lock:
                    owner._records.append({'scenario': scenario, 'method': self.command,
                        'path': self.path.split('?')[0], 'model': model,
                        'stream': stream, 'status': status})

            def _json(self, status, body):
                payload = json.dumps(body).encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(payload)))
                self.send_header('Connection', 'close')
                self.end_headers()
                self.wfile.write(payload)
                self.close_connection = True

            def _reject(self, status, reason, **metadata):
                self._record(status, **metadata)
                self._json(status, {'error': {'message': reason, 'type': 'ci_protocol_error',
                                             'code': 'fixture_contract_rejected'}})

            def do_GET(self):
                self._reject(404, 'No GET route in the CI provider fixture.')

            def do_POST(self):
                self.connection.settimeout(5)
                if self.path != '/v1/chat/completions':
                    self._reject(404, 'Unknown CI provider route.')
                    return
                if self.headers.get('Authorization') != 'Bearer ' + TOKEN:
                    self._reject(401, 'Only the explicit CI dummy token is accepted.')
                    return
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 0 < length <= 2 * 1024 * 1024:
                        raise ValueError('Invalid request length')
                    body = json.loads(self.rfile.read(length))
                    if not isinstance(body, dict):
                        raise ValueError('Expected JSON object')
                except (ValueError, OSError):
                    self._reject(400, 'Invalid CI provider JSON request.')
                    return
                if body.get('model') != MODEL:
                    self._reject(400, 'Unknown CI fixture model.')
                    return
                messages = body.get('messages')
                if not isinstance(messages, list) or not messages or not isinstance(messages[-1], dict):
                    self._reject(400, 'A final user message is required.', model=MODEL)
                    return
                with owner._lock:
                    owner._sent_text.append(' '.join(
                        str(m.get('content')) for m in messages if isinstance(m, dict)))
                last = messages[-1]
                content = last.get('content')
                matches = [(name, scenario) for name, scenario in [
                    ('chat', CHAT), ('guide', GUIDE),
                    ('programme_prefill', PROGRAMME_PREFILL), ('case_seed', CASE_SEED),
                ] if isinstance(content, str) and scenario['marker'] in content]
                stream = body.get('stream', False)
                if last.get('role') != 'user' or len(matches) != 1 or type(stream) is not bool:
                    self._reject(400, 'Unknown CI fixture scenario.', model=MODEL)
                    return
                name, scenario = matches[0]
                if stream != scenario['stream']:
                    self._reject(400, 'Scenario used the wrong transport.',
                                 scenario=name, model=MODEL, stream=stream)
                    return
                self._record(200, scenario=name, model=MODEL, stream=stream)
                base = {'id': 'chatcmpl-ci-' + name, 'created': 0, 'model': MODEL}
                if not stream:
                    self._json(200, {**base, 'object': 'chat.completion', 'choices': [{
                        'index': 0, 'message': {'role': 'assistant', 'content': scenario['reply']},
                        'finish_reason': 'stop'}], 'usage': {
                            'prompt_tokens': 12, 'completion_tokens': 8, 'total_tokens': 20}})
                    return
                chunks = []
                text = scenario['reply']
                for start in range(0, len(text), 24):
                    chunks.append({**base, 'object': 'chat.completion.chunk', 'choices': [{
                        'index': 0, 'delta': {'content': text[start:start + 24]}, 'finish_reason': None}]})
                chunks.append({**base, 'object': 'chat.completion.chunk', 'choices': [{
                    'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})
                frames = [('data: ' + json.dumps(chunk) + '\n\n').encode('utf-8') for chunk in chunks]
                frames.append(b'data: [DONE]\n\n')
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Content-Length', str(sum(map(len, frames))))
                self.send_header('Connection', 'close')
                self.end_headers()
                for frame in frames:
                    self.wfile.write(frame)
                    self.wfile.flush()
                self.close_connection = True

        self._server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self._server.daemon_threads = True
        self._thread = Thread(target=self._server.serve_forever, daemon=True)
        self.port = self._server.server_port
        self.base_url = f'http://127.0.0.1:{self.port}/v1'

    @property
    def sent_text(self):
        """The message text of each request, in arrival order (never the credentials)."""
        with self._lock:
            return list(self._sent_text)

    @property
    def records(self):
        with self._lock:
            return [dict(record) for record in self._records]

    def child_environment(self, original):
        """Return isolated settings; never alter pytest's already-scrubbed env."""
        env = dict(original)
        providers = ('ANTHROPIC', 'AZURE', 'CLAUDE', 'GEMINI',
                     'GOOGLE', 'HUGGINGFACE', 'NVIDIA', 'OPENAI', 'OPENROUTER')
        for provider in providers:
            for suffix in ['', '_SECONDARY'] + ['_' + str(number) for number in range(1, 10)]:
                env[provider + '_API_KEY' + suffix] = ''
        env['LLM_API_KEY'] = ''
        env['AZURE_OPENAI_API_KEY'] = ''
        env.update(OPENAI_API_KEY=TOKEN, OPENAI_BASE_URL=self.base_url,
                   AI_PAGE_GUIDE_ENABLED='true', REQUIRE_AI_APPROVAL='true',
                   PYTHON_DOTENV_DISABLED='1')
        bypass = ','.join(filter(None, [env.get('NO_PROXY'), env.get('no_proxy'),
                                       '127.0.0.1,localhost,::1']))
        env['NO_PROXY'] = env['no_proxy'] = bypass
        return env

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *_):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
        if self._thread.is_alive():
            raise RuntimeError('CI protocol listener did not stop')
