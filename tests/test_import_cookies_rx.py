"""`import-cookies` kennt Reddit und X (Server-Weg: Anmeldung am PC, Cookie-Datei hochladen). Nur erfundene Werte, Wegwerf-Ordner."""
import contextlib
import io
import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch

from socialscraper import cli
from socialscraper.config import Settings
from socialscraper.net import cookies
from socialscraper.net.cookies import CookieJar


class ImportCookiesRedditX(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.cfg = Settings(db_path=str(self.root / 'scrape.db'), notify_file='', profiles_dir=str(self.root / 'profiles'))

    def run_import(self, platform, identity, rows):
        source = self.root / f'{identity}.json'
        source.write_text(json.dumps(rows), encoding='utf-8')
        out = io.StringIO()
        with patch.object(cookies, 'BASE', self.root / 'cookiespeicher'), patch.object(cli, 'load', return_value=self.cfg), \
                contextlib.redirect_stdout(out):
            code = cli.main(['import-cookies', '--platform', platform, '--identity', identity, '--file', str(source)])
        return code, out.getvalue()

    def test_reddit_cookies_are_imported(self):
        rows = [{'name': 'reddit_session', 'value': 'erfunden', 'domain': '.reddit.com', 'path': '/'},
                {'name': 'fremd', 'value': 'x', 'domain': '.example.org', 'path': '/'}]
        code, out = self.run_import('reddit', 'reddit_main', rows)
        self.assertEqual(code, 0)
        self.assertIn('reddit_main/reddit', out)
        with patch.object(cookies, 'BASE', self.root / 'cookiespeicher'):
            saved = CookieJar.load('reddit_main', 'reddit')
            self.assertEqual([c['name'] for c in saved], ['reddit_session'])
            self.assertTrue(CookieJar.is_authenticated('reddit_main', 'reddit'))

    def test_x_cookies_are_imported_for_x_and_twitter_domains(self):
        rows = [{'name': 'auth_token', 'value': 'erfunden', 'domain': '.x.com', 'path': '/'},
                {'name': 'ct0', 'value': 'erfunden', 'domain': '.twitter.com', 'path': '/'}]
        code, out = self.run_import('x', 'x_main', rows)
        self.assertEqual(code, 0)
        with patch.object(cookies, 'BASE', self.root / 'cookiespeicher'):
            self.assertTrue(CookieJar.is_authenticated('x_main', 'x'))
        self.assertNotIn('erfunden', out)

    def test_cookie_of_another_platform_is_rejected(self):
        code, _ = self.run_import('x', 'x_zweit', [{'name': 'sessionid', 'value': 'e', 'domain': '.tiktok.com', 'path': '/'}])
        self.assertNotEqual(code, 0)

    def test_parser_knows_platforms(self):
        parser = cli.parser()
        for platform in ('reddit', 'x'):
            ns = parser.parse_args(['import-cookies', '--platform', platform, '--file', 'a.json'])
            self.assertEqual(ns.platform, platform)


if __name__ == '__main__':
    unittest.main()
