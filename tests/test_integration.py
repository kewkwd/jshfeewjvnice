import asyncio
import io
import json
import pathlib
import tempfile
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from socialscraper import cli
from socialscraper.config import Settings
from socialscraper.backends.nodriver_shim import TabShim

class ImmediateRunner:
    def run(self, coroutine): return asyncio.run(coroutine)

class IntegrationTests(unittest.TestCase):
    def test_nodriver_login_click_selects_and_awaits_submit(self):
        element=SimpleNamespace(click=AsyncMock(return_value=True))
        tab=SimpleNamespace(select=AsyncMock(return_value=element))
        self.assertTrue(TabShim(ImmediateRunner(),tab).click('button[type="submit"]',timeout=500))
        tab.select.assert_awaited_once_with('button[type="submit"]',timeout=0.5)
        element.click.assert_awaited_once()

    def test_nodriver_missing_submit_is_a_timeout(self):
        tab=SimpleNamespace(select=AsyncMock(return_value=None))
        with self.assertRaises(TimeoutError):TabShim(ImmediateRunner(),tab).click('missing')

    def test_foreign_cookie_import_is_rejected_instead_of_claiming_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=pathlib.Path(tmp)/'import.json'
            source.write_text(json.dumps([{'name':'sessionid','value':'fake','domain':'.instagram.com'}]),encoding='utf-8')
            with patch.object(cli,'load',return_value=Settings(notify_file='')), \
                 patch('socialscraper.net.cookies.BASE',pathlib.Path(tmp)/'cookies'),redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(['import-cookies','--platform','tiktok','--file',str(source)]),1)
            self.assertFalse((pathlib.Path(tmp)/'cookies').exists())

    def test_sqlite_open_failure_returns_controlled_cli_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Settings(db_path=tmp,notify_file='')
            with patch.object(cli,'load',return_value=cfg),patch.object(cli,'resolve',return_value=None), \
                 patch.object(cli,'_ctx_and_backend',return_value=(None,None,None)):
                self.assertEqual(cli.main(['scrape','https://invalid.example/video']),1)

    def test_schema_failure_closes_connection(self):
        from socialscraper.store import Store
        connection=SimpleNamespace(execute=Mock(),executescript=Mock(side_effect=RuntimeError('schema')),close=Mock())
        with patch('socialscraper.store.sqlite3.connect',return_value=connection):
            with self.assertRaisesRegex(RuntimeError,'schema'):Store(':memory:')
        connection.close.assert_called_once()

if __name__=='__main__':unittest.main()

