"""Selbstgebauter Fake der Anthropic Messages API (Standardbibliothek) für Tests – es gibt keine echten API-Aufrufe.

    fake = FakeAnthropic(); fake.url -> 'http://127.0.0.1:PORT'  (an sentiment.run(base_url_=...) geben)
    fake.faults: Liste, wird von vorn abgearbeitet. Einträge:
        int                    -> antwortet mit diesem HTTP-Status (z. B. 500)
        ('retry', status, s)   -> Status mit Retry-After: s
        ('raw', text)          -> 200 mit diesem Text als Antwort des Modells (z. B. kaputtes JSON)
        ('drop_last', n)       -> 200, aber die letzten n Einträge fehlen
        'drop'                 -> Verbindung kappen
"""
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

KEY = 'sk-ant-FAKE-KEY-do-not-leak-0123456789'


def default_classify(text):
    low = text.lower()
    if any(w in low for w in ('super', 'toll', 'liebe', 'great', 'love')):
        return 'positiv', 0.8
    if any(w in low for w in ('mist', 'schlecht', 'hass', 'hate', 'awful')):
        return 'negativ', -0.7
    return 'neutral', 0.0


class FakeAnthropic:
    def __init__(self, key=KEY, classify=default_classify):
        self.key, self.classify = key, classify
        self.faults = []
        self.requests = []      # {'headers', 'body', 'path'}
        self.sent_texts = []    # alle Kommentartexte, die angekommen sind (in Reihenfolge)
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *a):
                pass

            def _reply(self, status, body, headers=None):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                length = int(self.headers.get('Content-Length') or 0)
                raw = self.rfile.read(length)
                body = json.loads(raw)
                outer.requests.append({'headers': dict(self.headers), 'body': body, 'path': self.path})
                if self.path != '/v1/messages':
                    return self._reply(404, {'type': 'error', 'error': {'type': 'not_found_error', 'message': 'nope'}})
                if self.headers.get('x-api-key') != outer.key:
                    return self._reply(401, {'type': 'error', 'error': {'type': 'authentication_error', 'message': 'invalid x-api-key'}})
                if self.headers.get('anthropic-version') != '2023-06-01':
                    return self._reply(400, {'type': 'error', 'error': {'type': 'invalid_request_error', 'message': 'version'}})
                fault = outer.faults.pop(0) if outer.faults else None
                if fault == 'drop':
                    self.close_connection = True
                    self.connection.close()
                    return
                if isinstance(fault, int):
                    return self._reply(fault, {'type': 'error', 'error': {'type': 'api_error', 'message': 'Fehler vom Fake'}})
                if isinstance(fault, tuple) and fault[0] == 'retry':
                    return self._reply(fault[1], {'type': 'error', 'error': {'type': 'rate_limit_error', 'message': 'slow down'}},
                                       {'Retry-After': str(fault[2])})
                if isinstance(fault, tuple) and fault[0] == 'raw':
                    return self._reply(200, outer._message(fault[1]))
                content = body['messages'][0]['content']
                try:
                    items = json.loads(content)['kommentare']
                except (ValueError, KeyError, TypeError):   # z. B. die winzige Testanfrage der Einstellungen
                    return self._reply(200, outer._message('ok'))
                outer.sent_texts += [i['text'] for i in items]
                answer = []
                for i in items:
                    label, score = outer.classify(i['text'])
                    answer.append({'n': i['n'], 'label': label, 'score': score})
                if isinstance(fault, tuple) and fault[0] == 'drop_last':
                    answer = answer[:-fault[1]]
                return self._reply(200, outer._message('```json\n' + json.dumps(answer) + '\n```'))

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.url = f'http://127.0.0.1:{self.server.server_address[1]}'
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
        self.thread.start()

    @staticmethod
    def _message(text):
        return {'id': 'msg_fake', 'type': 'message', 'role': 'assistant', 'model': 'fake', 'content': [{'type': 'text', 'text': text}],
                'stop_reason': 'end_turn', 'usage': {'input_tokens': 100, 'output_tokens': 20}}

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
