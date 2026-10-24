"""Abrufzustände und gespeicherte Zwischenstände; nur lokale Fakes und kontrollierte Prozesse."""
import contextlib
import ctypes
import io
import json
import os
import pathlib
import queue
import subprocess
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from socialscraper import cli, guicore as core, repo, scheduler
from socialscraper.config import Settings
from socialscraper.fetch_result import FetchResult
from socialscraper.models import Comment, Video
from socialscraper.net.session_guard import SessionExpired, State
from socialscraper.store import Store

os.environ.setdefault('SS_PACE', 'off')
YT = 'https://www.youtube.com/watch?v=aaaaaaaaaaa'
TK = 'https://www.tiktok.com/@u/video/123456789'


class FetchRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.cfg = Settings(db_path=str(self.root / 'scrape.db'), profiles_dir=str(self.root / 'p'), notify_file='')
        self.video = Video('youtube', 'aaaaaaaaaaa', YT)
        self.c1 = Comment('youtube', self.video.video_id, 'one', 'Erster Kommentar')
        self.c2 = Comment('youtube', self.video.video_id, 'two', 'Zweiter Kommentar')
        self.adapter = SimpleNamespace(platform='youtube', needs_browser=False, needs_auth=False,
                                       fetch=lambda url, ctx: FetchResult(self.video, [self.c1], 'received'))

    def run_cli(self, urls=(YT,), require_auth=False, resolver=None, auth=None):
        output = io.StringIO()
        runtime = (Mock(), Mock(), Mock())
        with patch.object(cli, 'load', return_value=self.cfg), \
             patch.object(cli, 'resolve', side_effect=resolver or (lambda url: self.adapter)), \
             patch.object(cli, '_ctx_and_backend', return_value=runtime), \
             patch.object(cli, '_auth', side_effect=auth or (lambda *args: False)), \
             patch.object(cli, 'shutdown_runtime') as shutdown, contextlib.redirect_stdout(output):
            code = cli.main(['scrape', *urls] + (['--require-auth'] if require_auth else []))
            shutdown.assert_called_once_with(runtime[1], runtime[2])
        lines = output.getvalue().splitlines()
        events = [json.loads(line[len('[SS_FETCH] '):]) for line in lines if line.startswith('[SS_FETCH] ')]
        return code, lines, events

    def db_values(self):
        with Store(self.cfg.db_path) as store:
            return (store.db.execute('SELECT COUNT(*) FROM videos').fetchone()[0],
                    store.db.execute('SELECT COUNT(*) FROM comments').fetchone()[0],
                    store.db.execute('SELECT COUNT(*) FROM video_snapshots').fetchone()[0])

    def test_unknown_zero_comments_is_partial_with_saved_video(self):
        self.adapter.fetch = lambda url, ctx: FetchResult(self.video, [], 'unavailable', 'Kommentare nicht abrufbar. Erneut versuchen.')
        code, lines, events = self.run_cli()
        self.assertEqual(code, 1)
        self.assertIn('fertig: 1 ok, 0 fehler, 1 teilabrufe', lines)
        self.assertEqual((events[-1]['video_saved'], events[-1]['comments_status']), (True, 'unavailable'))
        self.assertEqual(self.db_values(), (1, 0, 1))

    def test_known_empty_and_not_requested_are_complete(self):
        for status in ('empty', 'not_requested'):
            self.adapter.fetch = lambda url, ctx: FetchResult(self.video, [], status)
            code, _lines, events = self.run_cli()
            self.assertEqual(code, 0)
            self.assertEqual(events[-1]['comments_status'], status)

    def test_checkpoints_are_committed_cumulative_without_extra_snapshots(self):
        def fetch(url, ctx):
            ctx.checkpoint(self.video, [])
            self.assertEqual(self.db_values(), (1, 0, 0))
            ctx.checkpoint(self.video, [self.c1])
            ctx.checkpoint(self.video, [self.c1, self.c2])
            self.assertEqual(self.db_values(), (1, 2, 0))
            return FetchResult(self.video, [self.c1, self.c2], 'received')
        self.adapter.fetch = fetch
        code, _lines, events = self.run_cli()
        self.assertEqual(code, 0)
        self.assertEqual([event['comments_saved'] for event in events], [0, 1, 2, 2])
        self.assertEqual([event['stage'] for event in events], ['checkpoint'] * 3 + ['result'])
        self.assertEqual(self.db_values(), (1, 2, 1))

    def test_error_after_checkpoint_preserves_data_and_retry_keeps_existing_comments(self):
        def fetch(url, ctx):
            ctx.checkpoint(self.video, [self.c1])
            raise RuntimeError('Netz weg token=PRIVATE http://user:pw@proxy/')
        self.adapter.fetch = fetch
        code, lines, events = self.run_cli()
        self.assertEqual(code, 1)
        self.assertEqual((events[-1]['success'], events[-1]['video_saved'], events[-1]['comments_saved']), (False, True, 1))
        self.assertEqual(events[-1]['comments_status'], 'partial')
        self.assertEqual(self.db_values(), (1, 1, 0))
        self.assertIn('fertig: 1 ok, 0 fehler, 1 teilabrufe', lines)
        self.assertNotIn('PRIVATE', events[-1]['message']); self.assertNotIn('user:pw', events[-1]['message'])
        self.adapter.fetch = lambda url, ctx: FetchResult(self.video, [self.c2], 'received')
        self.assertEqual(self.run_cli()[0], 0)
        self.assertEqual(self.db_values(), (1, 2, 1))

    def test_auth_probe_failure_does_not_block_other_platform(self):
        tk = SimpleNamespace(platform='tiktok', needs_browser=True, needs_auth=True, fetch=Mock())
        def auth(adapter, *_args):
            if adapter.platform == 'tiktok': raise SessionExpired('Anmeldung abgelaufen')
            return False
        code, lines, events = self.run_cli((TK, YT), require_auth=True,
                                         resolver=lambda url: tk if 'tiktok' in url else self.adapter, auth=auth)
        self.assertEqual(code, 1)
        self.assertEqual([event['video_saved'] for event in events], [False, True])
        self.assertIn('fertig: 1 ok, 1 fehler', lines)
        tk.fetch.assert_not_called()

    def test_session_lost_after_data_skips_only_affected_platform(self):
        tk_video = Video('tiktok', '123456789', TK)
        tk_calls = []
        def fetch_tiktok(url, ctx):
            tk_calls.append(url)
            ctx.checkpoint(tk_video, [])
            raise SessionExpired('Anmeldung abgelaufen')
        tk = SimpleNamespace(platform='tiktok', needs_browser=True, needs_auth=True, fetch=fetch_tiktok)
        code, lines, events = self.run_cli((TK, TK + '0', YT), require_auth=True,
                                         resolver=lambda url: tk if 'tiktok' in url else self.adapter,
                                         auth=lambda *_args: True)
        self.assertEqual(code, 1)
        self.assertEqual(tk_calls, [TK])
        final = [e for e in events if e['stage'] == 'result']
        self.assertEqual([e['video_saved'] for e in final], [True, False, True])
        self.assertIn('fertig: 2 ok, 1 fehler, 1 teilabrufe', lines)

    def run_target(self, fetch, *, comments=5):
        self.adapter.fetch = fetch
        with Store(self.cfg.db_path) as store:
            targets = repo.list_targets(store.db)
            target = targets[0] if targets else repo.add_target(store.db, 'video', 'youtube', YT, max_comments=comments)
        with patch.object(scheduler, 'resolve', return_value=self.adapter), \
             patch.object(cli, 'make_runtime', return_value=(Mock(), None, None)), \
             patch.object(cli, 'shutdown_runtime') as shutdown:
            result = scheduler.run_target(self.root, target['id'], cfg=self.cfg)
            shutdown.assert_called_once()
        return result

    def test_scheduler_partial_comments_and_counts_all_checkpoints_once(self):
        def fetch(url, ctx):
            ctx.checkpoint(self.video, [])
            ctx.checkpoint(self.video, [self.c1])
            ctx.checkpoint(self.video, [self.c1, self.c2])
            return FetchResult(self.video, [self.c1, self.c2], 'partial', 'Kommentare nur teilweise abrufbar.')
        result = self.run_target(fetch)
        self.assertEqual((result['status'], result['videos_seen'], result['videos_new'], result['comments_new']), ('partial', 1, 1, 2))
        self.assertEqual(self.db_values(), (1, 2, 1))
        repeated = self.run_target(fetch)
        self.assertEqual((repeated['videos_seen'], repeated['videos_new'], repeated['comments_new']), (1, 0, 0))

    def test_scheduler_preserves_checkpoint_when_fetch_fails(self):
        def fetch(url, ctx):
            ctx.checkpoint(self.video, [self.c1])
            raise RuntimeError('Netz weg')
        result = self.run_target(fetch)
        self.assertEqual((result['status'], result['videos_seen'], result['videos_new'], result['comments_new']), ('partial', 1, 1, 1))
        self.assertEqual(self.db_values(), (1, 1, 0))

    def test_event_and_partial_summary_are_readable(self):
        event = dict(version=1, stage='result', platform='youtube', uid=self.video.uid,
                     video_saved=True, comments_saved=0, comments_status='unavailable', message='Erneut versuchen.')
        text = core.display_line('[SS_FETCH] ' + json.dumps(event))
        self.assertIn('Video gespeichert', text); self.assertIn('Kommentare nicht abrufbar', text)
        self.assertNotIn('{', text)
        self.assertIsNone(core.display_line('[SS_FETCH] kaputt'))
        self.assertEqual(core.parse_summary(['fertig: 1 ok, 0 fehler, 1 teilabrufe']), (1, 0))
        self.assertIn('Teilweise fertig', core.display_line('fertig: 1 ok, 0 fehler, 1 teilabrufe'))


@unittest.skipUnless(os.name == 'nt', 'Windows-Prozessbaum')
class CancellationTests(unittest.TestCase):
    def launch(self, source):
        lines, done = [], []
        ready, complete = threading.Event(), threading.Event()
        def line(text): lines.append(text); ready.set()
        def finished(code): done.append(code); complete.set()
        job = core.Job([], line, finished)
        job.proc = subprocess.Popen([core.python_exe(), '-u', '-c', source], stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    creationflags=core.NO_WINDOW | core.NEW_GROUP)
        self.addCleanup(lambda: job.cancel() if job.running() else None)
        threading.Thread(target=job._pump, daemon=True).start()
        self.assertTrue(ready.wait(8))
        return job, lines, done, complete

    def alive(self, pid):
        k32 = ctypes.windll.kernel32
        k32.OpenProcess.restype = ctypes.c_void_p
        k32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = k32.OpenProcess(0x1000, False, pid)
        if not handle: return False
        try:
            code = ctypes.c_ulong()
            return bool(k32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            k32.CloseHandle(handle)

    def test_real_parent_and_child_end_before_completion_is_reported(self):
        source = ('import subprocess,sys,time;'
                  'p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(120)"],'
                  'creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0));'
                  'print("child="+str(p.pid),flush=True);time.sleep(120)')
        job, lines, done, complete = self.launch(source)
        child = int(next(line.split('=', 1)[1] for line in lines if line.startswith('child=')))
        self.assertTrue(self.alive(child))
        self.assertTrue(job.cancel())
        self.assertTrue(complete.wait(15))
        self.assertEqual(done, [-1])
        self.assertFalse(job.running()); self.assertFalse(self.alive(child))
        self.assertFalse(job.cancel())

    def test_completed_process_is_not_changed_to_cancelled(self):
        job, _lines, done, complete = self.launch('print("fertig",flush=True)')
        self.assertTrue(complete.wait(8))
        self.assertFalse(job.cancel())
        self.assertFalse(job.cancelled)
        self.assertEqual(done, [0])

    def test_app_keeps_job_and_controls_locked_until_done(self):
        from socialscraper.ui.app import App
        fake_job = Mock(cancelled=True, lines=['saved'])
        fake_job.cancel.return_value = True
        a = SimpleNamespace(job=fake_job, cancelled=False, steps=[('next', [])], cancel_btn=Mock(),
                            set_status=Mock(), log=Mock())
        App.cancel(a)
        self.assertIs(a.job, fake_job)
        self.assertTrue(a.cancelled); self.assertEqual(a.steps, [('next', [])])   # Restschritte bleiben stehen, _next_step lässt sie bei cancelled aus
        a.cancel_btn.configure.assert_called_once_with(state='disabled')

    def test_app_cancel_after_natural_exit_preserves_success(self):
        from socialscraper.ui.app import App
        fake_job = Mock(cancelled=False)
        fake_job.cancel.return_value = False
        a = SimpleNamespace(job=fake_job, cancelled=False, steps=[('next', [])], cancel_btn=Mock(),
                            set_status=Mock(), log=Mock())
        App.cancel(a)
        self.assertFalse(a.cancelled); self.assertEqual(a.steps, [('next', [])])
        a.cancel_btn.configure.assert_not_called()


if __name__ == '__main__': unittest.main()
