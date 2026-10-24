"""Regressionen für Persona-, FYP- und Watch-Time-Kommandos."""
import asyncio
import contextlib
import io
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from checks import watchtime_check
from socialscraper import cli, personas, repo
from socialscraper.backends.nodriver_shim import TabShim
from socialscraper.commands import fyp as fyp_command
from socialscraper.commands import personas as personas_command
from socialscraper.store import Store


class AstraCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cfg = SimpleNamespace(db_path=str(Path(self.temp.name) / 'scrape.db'))

    def test_fyp_requires_one_seed_account_and_positive_seconds(self):
        parser = cli.parser()
        for command in (['fyp', 'seed'], ['fyp', 'seed', '--konto', '0'],
                        ['fyp', 'konsum', '--sekunden', '-1']):
            with self.subTest(command=command), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    parser.parse_args(command)

    def test_fyp_rejects_unknown_and_non_tiktok_accounts(self):
        with Store(self.cfg.db_path) as store:
            instagram = repo.add_account(store.db, 'instagram', 'Test')
        with patch.object(fyp_command.seed, 'sitzung') as session:
            for account_id in (99999, instagram['id']):
                with self.subTest(account_id=account_id), self.assertRaises(ValueError):
                    fyp_command.run(SimpleNamespace(was='seed', konto=account_id), self.cfg)
            session.assert_not_called()

    def test_persona_proposals_take_unused_names(self):
        with Store(self.cfg.db_path) as store:
            personas.anlegen(store.db, 'persona_02', influencer='x', kanaele=['a'])
        args = SimpleNamespace(was='vorschlag', influencer='x', kanaele='a,b,c', anzahl=2, je=2)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(personas_command.run(args, self.cfg), 0)
        with Store(self.cfg.db_path) as store:
            names = [row[0] for row in store.db.execute('SELECT name FROM personas ORDER BY id')]
        self.assertEqual(names, ['persona_02', 'persona_01', 'persona_03'])

    def test_persona_proposal_batch_rolls_back_on_later_error(self):
        with Store(self.cfg.db_path) as store:
            store.db.executescript("""CREATE TRIGGER reject_second BEFORE INSERT ON personas
                WHEN NEW.name='persona_02' BEGIN SELECT RAISE(ABORT, 'test failure'); END;""")
        args = SimpleNamespace(was='vorschlag', influencer='x', kanaele='a,b,c', anzahl=2, je=2)
        with self.assertRaises(sqlite3.IntegrityError):
            personas_command.run(args, self.cfg)
        with Store(self.cfg.db_path) as store:
            self.assertEqual(store.db.execute('SELECT COUNT(*) FROM personas').fetchone()[0], 0)

    def test_watchtime_rejects_unknown_account_before_browser_start(self):
        with patch.object(watchtime_check, 'load', return_value=self.cfg), \
             patch.object(watchtime_check, '_lauf') as run, \
             patch.object(sys, 'argv', ['watchtime_check', '--konto', '99999']), \
             contextlib.redirect_stderr(io.StringIO()) as stderr:
            self.assertEqual(watchtime_check.main(), 1)
        self.assertIn('nicht gefunden', stderr.getvalue())
        run.assert_not_called()

    def test_watchtime_uses_saved_device_and_ignores_page_load_requests(self):
        class Page:
            def __init__(self):
                self.callback = None
                self.reads = 0

            def on(self, event, callback):
                self.callback = callback

            def goto(self, url, **kwargs):
                self.callback(SimpleNamespace(url='https://mon.tiktokv.com/loading'))

            def wait_for_timeout(self, milliseconds):
                pass

            def evaluate(self, expression):
                if 'return v ? v.currentTime' in expression:
                    self.reads += 1
                    return 0 if self.reads == 1 else 3

        page = Page()
        ident = object()
        im = SimpleNamespace(stored=Mock(return_value=('fr', True)),
                             get=Mock(return_value=ident), proxy_for=Mock(return_value=None))
        handle = object()
        backend = SimpleNamespace(session=Mock(return_value=contextlib.nullcontext(handle)),
                                  page=Mock(return_value=page))
        konto = {'identity': 'konto', 'country': 'de'}
        with patch.object(watchtime_check.cli, 'make_runtime', return_value=(im, backend, None)), \
             patch.object(watchtime_check.cli, 'shutdown_runtime') as shutdown:
            fortschritt, treffer = watchtime_check._lauf(self.cfg, konto, 'https://example.test', 2, True)
        self.assertEqual((fortschritt, treffer), (3, []))
        self.assertEqual(watchtime_check._urteil(fortschritt, treffer)[0], 'TEILWEISE')
        im.get.assert_called_once_with('konto', 'fr', True)
        shutdown.assert_called_once_with(backend, None)

    def test_watchtime_shuts_down_after_identity_error(self):
        im = SimpleNamespace(stored=Mock(side_effect=ValueError('broken identity')))
        backend, sessions = object(), object()
        with patch.object(watchtime_check.cli, 'make_runtime', return_value=(im, backend, sessions)), \
             patch.object(watchtime_check.cli, 'shutdown_runtime') as shutdown:
            with self.assertRaises(ValueError):
                watchtime_check._lauf(self.cfg, None, 'https://example.test', 2, True)
        shutdown.assert_called_once_with(backend, sessions)

    def test_nodriver_request_event_adapts_to_page_callback(self):
        event_type = type('RequestWillBeSent', (), {})
        network = SimpleNamespace(RequestWillBeSent=event_type, enable=lambda: 'enable')
        nodriver = types.ModuleType('nodriver')
        nodriver.cdp = SimpleNamespace(network=network)
        tab = SimpleNamespace(add_handler=Mock(), send=AsyncMock())
        runner = SimpleNamespace(run=lambda coroutine: asyncio.run(coroutine))
        urls = []
        with patch.dict(sys.modules, {'nodriver': nodriver}):
            TabShim(runner, tab).on('request', lambda request: urls.append(request.url))
        registered_type, callback = tab.add_handler.call_args.args
        self.assertIs(registered_type, event_type)
        callback(SimpleNamespace(request=SimpleNamespace(url='https://example.test/report')))
        self.assertEqual(urls, ['https://example.test/report'])
        tab.send.assert_awaited_once_with('enable')


if __name__ == '__main__':
    unittest.main()
