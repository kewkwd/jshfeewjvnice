"""Exercise real installed libraries against a local page, without an account."""
import argparse
import importlib
import importlib.metadata
import json
import platform
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from socialscraper.net.identity import IdentityManager
from socialscraper.net.proxy import ProxyPool
from socialscraper.backends.local import LocalBackend

HTML = b'''<!doctype html><title>Scraper runtime check</title>
<input id="user"><button id="submit" onclick="document.querySelector('#result').textContent=document.querySelector('#user').value">Pruefen</button><div id="result"></div>'''

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(HTML)
    def log_message(self, *args):
        pass

def run(engine):
    report = {'python': platform.python_version(), 'engine': engine, 'live_accounts_used': False, 'checks': {}}
    for module, distribution in [('yt_dlp','yt-dlp'),('yt_dlp_ejs','yt-dlp-ejs'),('curl_cffi','curl_cffi'),('instaloader','instaloader'),('patchright.sync_api','patchright'),('rebrowser_playwright.sync_api','rebrowser-playwright'),('nodriver','nodriver'),('requests','requests')]:
        importlib.import_module(module)
        report['checks'][distribution] = {'version': importlib.metadata.version(distribution), 'import': True}
    server = ThreadingHTTPServer(('127.0.0.1',0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    if engine == 'nodriver':
        from socialscraper.backends.nodriver_engine import NodriverBackend
        backend = NodriverBackend('data/runtime-check/profiles')
    else:
        backend = LocalBackend(profiles_dir='data/runtime-check/profiles')
    im = IdentityManager(ProxyPool([]), profiles_dir='data/runtime-check/profiles')
    identity = im.get('runtime_' + engine, 'de', False)
    handle = None
    try:
        started = time.monotonic()
        handle = backend.open(identity, None, headless=True)
        page = backend.page(handle)
        page.goto(f'http://127.0.0.1:{server.server_port}/', wait_until='domcontentloaded')
        assert page.evaluate('() => 0') == 0
        assert page.evaluate('() => false') is False
        # Country settings of the identity apply in both engines.
        assert page.evaluate('() => Intl.DateTimeFormat().resolvedOptions().timeZone') == 'Europe/Berlin'
        assert page.evaluate('() => navigator.language') == 'de-DE'
        page.fill('#user', 'local-test-only')
        page.click('#submit')
        assert page.evaluate("() => document.querySelector('#result').textContent") == 'local-test-only'
        backend.inject_cookies(handle, [{'name':'runtime_check','value':'synthetic','domain':'127.0.0.1','path':'/','secure':False}])
        assert any(item['name']=='runtime_check' for item in backend.read_cookies(handle))
        page.mouse.move(20, 20)
        page.mouse.wheel(0,100)
        report['browser'] = {'passed':True,'duration_seconds':round(time.monotonic()-started,2),'actions':['navigate','evaluate','fill','click','cookies','mouse']}
    except Exception as error:
        report['browser'] = {'passed':False,'error':type(error).__name__+': '+str(error)}
    finally:
        if handle:
            try: backend.close(handle)
            except Exception as error: report['cleanup_error']=type(error).__name__+': '+str(error)
        if hasattr(backend, 'runner'):
            backend.runner.stop()
        server.shutdown()
        server.server_close()
    report['passed'] = report['browser']['passed'] and 'cleanup_error' not in report
    destination = Path('data/runtime-check') / (engine + '.json')
    destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))
    return 0 if report['passed'] else 1

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--engine',choices=['patchright','nodriver'],default='patchright')
    raise SystemExit(run(parser.parse_args().engine))
