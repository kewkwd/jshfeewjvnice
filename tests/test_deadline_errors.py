"""Zeitlimits gegen Hängen und deutsche Netzfehler (alles mit Attrappen, kein echtes Netz)."""
import contextlib
import io
import logging
import pathlib
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import Mock, patch

from socialscraper import cli, repo, runlock, scheduler
from socialscraper.adapters.base import ScrapeContext
from socialscraper.config import Settings, validate
from socialscraper.models import Video
from socialscraper.net import deadline as dl_module
from socialscraper.net import errors
from socialscraper.net.deadline import FetchTimeout, deadline
from socialscraper.net.proxy import Proxy
from socialscraper.net.retry import with_retry
from socialscraper.store import Store

YT = 'https://www.youtube.com/watch?v=jNQXAC9IVRw'
TT = 'https://www.tiktok.com/@x/video/123'


def hang_until_expired(limit=15):
    """Hängt wie ein blockierter Browser, bis ein Zeitlimit abläuft; dann bricht es wie ein getöteter Browser mit einem Fehler ab."""
    end = time.monotonic() + limit
    while time.monotonic() < end:
        if dl_module.expired():
            raise RuntimeError('BrowserContext.close: Connection closed while reading from the driver')
        time.sleep(0.02)
    raise AssertionError('Zeitlimit hat nicht gegriffen')


class DeadlineTests(unittest.TestCase):
    def test_passes_result_and_errors_when_not_expired(self):
        with deadline(5, 'Test', kill_browser=False) as dl:
            pass
        self.assertFalse(dl.fired)
        with self.assertRaises(ValueError), deadline(5, 'Test', kill_browser=False):
            raise ValueError('x')
        self.assertFalse(dl_module.expired())

    def test_expired_limit_turns_error_into_clear_message(self):
        started = time.monotonic()
        with self.assertRaises(FetchTimeout) as caught, deadline(0.3, 'Der Abruf', kill_browser=False) as dl:
            hang_until_expired()
        self.assertLess(time.monotonic() - started, 3)
        self.assertTrue(dl.fired)
        self.assertIn('Zeitlimit erreicht', str(caught.exception))
        self.assertIn('Browser geschlossen', str(caught.exception))
        self.assertFalse(dl_module.expired())   # Stapel wieder leer

    def test_retry_stops_after_expiry(self):
        calls = []

        @with_retry(times=3, backoff=1.1)
        def fetch():
            calls.append(1)
            hang_until_expired()
        with self.assertRaises(FetchTimeout), deadline(0.2, 'Der Abruf', kill_browser=False):
            fetch()
        self.assertEqual(len(calls), 1)

    def test_kill_children_only_touches_browser_processes_and_never_python(self):
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
        try:
            time.sleep(0.3)
            names = {pid: name for pid, name in dl_module.descendants()}
            self.assertIn(child.pid, names)
            self.assertEqual(dl_module.kill_children(), 0)   # python.exe steht nicht auf der Liste
            self.assertIsNone(child.poll())
        finally:
            child.kill(); child.wait()

    def test_config_fields(self):
        cfg = Settings()
        self.assertEqual((cfg.video_timeout, cfg.target_timeout), (120, 900))
        for name in ('video_timeout', 'target_timeout'):
            with self.assertRaises(ValueError):
                validate(Settings(**{name: 0}))
            with self.assertRaises(ValueError):
                validate(Settings(**{name: 1.5}))


class HangingAdapter:
    platform = 'youtube'
    needs_browser = True
    needs_auth = False

    def __init__(self):
        self.calls = 0

    def fetch(self, url, ctx):
        self.calls += 1
        hang_until_expired()


class SchedulerTimeoutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.cfg = Settings(db_path=str(self.root / 'data' / 'scrape.db'), notify_file='',
                            profiles_dir=str(self.root / 'data' / 'profiles'))
        self.store = Store(self.cfg.db_path); self.addCleanup(self.store.close)
        repo.ensure_default_accounts(self.store.db)
        self.adapter = HangingAdapter()
        patcher = patch.object(scheduler, 'resolve', side_effect=lambda u: self.adapter if 'youtube' in u else None)
        patcher.start(); self.addCleanup(patcher.stop)
        # Kein echter Browser: die Laufumgebung wird ersetzt.
        patcher = patch.object(scheduler.cli, 'make_runtime', return_value=(Mock(), None, None))
        patcher.start(); self.addCleanup(patcher.stop)
        patcher = patch.object(dl_module, 'kill_children', return_value=0)
        patcher.start(); self.addCleanup(patcher.stop)

    def run_one(self):
        target = repo.add_target(self.store.db, 'video', 'youtube', YT)
        started = time.monotonic()
        result = scheduler.run_target(self.root, target['id'], 'manual', cfg=self.cfg)
        return result, time.monotonic() - started

    def test_video_timeout_ends_run_with_clear_message_and_frees_lock(self):
        self.cfg.video_timeout = 1
        result, seconds = self.run_one()
        self.assertEqual(result['status'], 'failed')
        self.assertIn('Zeitlimit erreicht', result['error'])
        self.assertLess(seconds, 8)
        self.assertEqual(self.adapter.calls, 1)
        with runlock.acquire(self.root, 'test'):
            pass

    def test_target_timeout_ends_run(self):
        self.cfg.video_timeout, self.cfg.target_timeout = 100, 1
        result, seconds = self.run_one()
        self.assertEqual(result['status'], 'failed')
        self.assertIn('Zeitlimit erreicht', result['error'])
        self.assertIn('Das Ziel', result['error'])
        self.assertLess(seconds, 8)

    def test_not_found_message(self):
        self.adapter.fetch = lambda url, ctx: (None, [])
        result, _ = self.run_one()
        self.assertEqual(result['status'], 'failed')
        self.assertIn('Video nicht gefunden oder nicht abrufbar', result['error'])

    def test_run_row_has_german_network_message(self):
        def boom(url, ctx):
            raise RuntimeError('Unable to download API page: Failed to perform, curl: (28) Connection timed out after 20000 ms')
        self.adapter.fetch = boom
        result, _ = self.run_one()
        self.assertIn('Zeitüberschreitung', result['error'])
        self.assertNotIn('curl', result['error'])


class CliTimeoutTests(unittest.TestCase):
    def test_video_timeout_in_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Settings(db_path=str(pathlib.Path(tmp) / 'data' / 'scrape.db'), notify_file='', video_timeout=1)
            adapter = HangingAdapter()
            stream = io.StringIO()
            handler = logging.StreamHandler(stream)
            logging.getLogger('cli').addHandler(handler)
            self.addCleanup(logging.getLogger('cli').removeHandler, handler)
            with patch.object(cli, 'load', return_value=cfg), patch.object(cli, 'resolve', return_value=adapter), \
                 patch.object(cli, '_ctx_and_backend', return_value=(Mock(), Mock(), Mock())), \
                 patch.object(dl_module, 'kill_children', return_value=0), \
                 patch.object(cli, 'ensure'), contextlib.redirect_stdout(io.StringIO()):
                started = time.monotonic()
                code = cli.main(['scrape', YT, '--max-comments', '0'])
            self.assertEqual(code, 1)
            self.assertLess(time.monotonic() - started, 8)
            self.assertIn('Zeitlimit erreicht', stream.getvalue())
            with runlock.acquire(pathlib.Path(tmp), 'test'):
                pass


class ErrorTextTests(unittest.TestCase):
    CASES = [
        ('Unable to download API page: Failed to perform, curl: (7) Failed to connect to www.youtube.com:443 over proxy '
         '1.2.3.4 after 2017 ms: Could not connect to server', 'Proxy ist nicht erreichbar'),
        ('Failed to perform, curl: (7) Failed to connect to 127.0.0.1 port 9: Connection refused', 'Proxy ist nicht erreichbar'),
        ('Failed to perform, curl: (28) Connection timed out after 20001 milliseconds', 'Zeitüberschreitung'),
        ('Page.goto: Timeout 30000ms exceeded.', 'Zeitüberschreitung'),
        ('[Errno 11001] getaddrinfo failed', 'DNS'),
        ('net::ERR_NAME_NOT_RESOLVED at https://www.tiktok.com/', 'DNS'),
        ('curl: (60) SSL certificate problem: unable to get local issuer certificate', 'Zertifikatsfehler'),
        ("Sign in to confirm you’re not a bot. Use --cookies-from-browser", 'YouTube verlangt'),
        ("Sign in to confirm you're not a bot", 'YouTube verlangt'),
        ('HTTP Error 429: Too Many Requests', 'HTTP 429'),
        ('HTTP Error 403: Forbidden', 'HTTP 403'),
        ('Please solve the CAPTCHA to continue', 'CAPTCHA'),
        ('Private video. Sign in if you have been granted access', 'privat'),
        ('Video unavailable. This video has been removed by the uploader', 'gelöscht'),
        ('Failed to perform, curl: (56) Recv failure: Connection reset by peer', 'Keine Verbindung'),
        ('net::ERR_INTERNET_DISCONNECTED', 'Keine Verbindung'),
    ]

    def test_translation_table(self):
        for raw, expected in self.CASES:
            with self.subTest(raw=raw[:50]):
                text = errors.explain(RuntimeError(raw))
                self.assertIn(expected, text)
                self.assertNotIn('curl', text.lower())
                self.assertTrue(text.endswith('.'))   # vollständig, nicht mitten im Wort abgeschnitten

    def test_own_german_text_is_unchanged_and_unknown_english_gets_generic_text(self):
        own = 'Mit kostenlosen Proxys/Tor werden keine Konten benutzt – aus Sicherheitsgründen. Sehr lange ' + 'Erklärung ' * 40
        self.assertEqual(errors.explain(ValueError(own)), ' '.join(own.split()))
        text = errors.explain(RuntimeError('curl: (99) something weird'))
        self.assertIn('RuntimeError', text)
        self.assertNotIn('weird', text)

    def test_raw_text_only_at_debug_and_without_secrets(self):
        with self.assertLogs('socialscraper.net.errors', level='DEBUG') as logs:
            errors.explain(RuntimeError('curl: (6) Could not resolve host via http://user:pw@proxy:1 token=abc123'))
        self.assertIn('Could not resolve host', logs.output[0])
        self.assertNotIn('pw@', logs.output[0])
        self.assertNotIn('abc123', logs.output[0])

    def test_local_proxy_refused_is_hopeless_remote_is_not(self):
        local = RuntimeError('curl: (7) Failed to connect to 127.0.0.1 port 9 after 1 ms: Connection refused')
        remote = RuntimeError('curl: (7) Failed to connect to www.youtube.com:443 over proxy 8.8.8.8 after 2 ms')
        self.assertTrue(errors.is_hopeless(local))
        self.assertFalse(errors.is_hopeless(remote))

    def test_retry_does_not_repeat_local_proxy_refusal(self):
        calls = []

        @with_retry(times=3, backoff=1.1)
        def fetch():
            calls.append(1)
            raise RuntimeError('curl: (7) Failed to connect to 127.0.0.1 port 9: Connection refused')
        with self.assertRaises(RuntimeError):
            fetch()
        self.assertEqual(len(calls), 1)

    def test_precheck_local_proxy(self):
        with self.assertRaises(errors.NetworkError) as caught:
            errors.precheck_local_proxy(Proxy('http', '127.0.0.1', 9))
        self.assertFalse(caught.exception.retryable)
        self.assertIn('127.0.0.1:9', str(caught.exception))
        errors.precheck_local_proxy(Proxy('http', '8.8.8.8', 9))   # fremder Host: keine Vorprüfung
        server = threading.Thread  # nur Import-Nutzung vermeiden
        import socket
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0)); listener.listen(1)
            errors.precheck_local_proxy(Proxy('http', '127.0.0.1', listener.getsockname()[1]))

    def test_youtube_adapter_ends_immediately_for_dead_local_proxy(self):
        from socialscraper.adapters import youtube
        calls = []
        module = types.ModuleType('yt_dlp')
        module.YoutubeDL = lambda options: calls.append(options)
        module.utils = types.SimpleNamespace(DownloadError=type('DownloadError', (Exception,), {}))
        cfg = Settings(proxies=['http://127.0.0.1:9'], max_comments=0, impersonate='')
        im = Mock(); im.proxy_for.return_value = Proxy('http', '127.0.0.1', 9)
        ctx = ScrapeContext(cfg=cfg, store=None, im=im, identity='youtube_main', country='de')
        with patch.dict('sys.modules', {'yt_dlp': module}), patch.object(youtube, 'youtube_runtime_options', return_value={}):
            started = time.monotonic()
            with self.assertRaises(errors.NetworkError) as caught:
                youtube.YouTubeAdapter().fetch(YT, ctx)
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(calls, [])
        self.assertIn('nicht erreichbar', str(caught.exception))

    def test_youtube_download_error_becomes_german_without_losing_cause(self):
        from socialscraper.adapters import youtube
        module = types.ModuleType('yt_dlp')
        module.utils = types.SimpleNamespace(DownloadError=type('DownloadError', (Exception,), {}))

        class YDL:
            def __init__(self, options): pass
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def extract_info(self, url, download=False, process=True):   # yt-dlp: erst roh lesen, dann verarbeiten
                raise module.utils.DownloadError("Unable to download API page: Failed to perform, curl: (28) timed out")
            def process_ie_result(self, result, download=False): return result
        module.YoutubeDL = YDL
        cfg = Settings(max_comments=0, impersonate='')
        ctx = ScrapeContext(cfg=cfg, store=None, im=None, identity='youtube_main', country='de')
        with patch.dict('sys.modules', {'yt_dlp': module}), patch.object(youtube, 'youtube_runtime_options', return_value={}), \
             patch('socialscraper.net.retry.time.sleep'):
            with self.assertRaises(errors.NetworkError) as caught:
                youtube.YouTubeAdapter().fetch(YT, ctx)
        self.assertIn('Zeitüberschreitung', str(caught.exception))
        self.assertIsInstance(caught.exception.__cause__, module.utils.DownloadError)


if __name__ == '__main__':
    unittest.main()
