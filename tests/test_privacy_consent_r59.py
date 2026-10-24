import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from socialscraper import privacy_consent as privacy, entry_rules
from socialscraper.net.errors import FinalError


class PrivacyPreferences(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / 'data/test.db'

    def test_explicit_preference_roundtrip_and_isolated_data_roots(self):
        self.assertFalse(privacy.enabled(self.db))
        privacy.save(self.db, True); self.assertTrue(privacy.enabled(self.db))
        self.assertFalse(privacy.enabled(self.db.parent / 'other/test.db'))
        privacy.save(self.db, False); self.assertFalse(privacy.enabled(self.db))

    def test_invalid_preference_is_not_treated_as_permission(self):
        for value in ('true', 1, None):
            with self.assertRaises(ValueError):privacy.save(self.db, value)
        self.db.parent.mkdir(exist_ok=True)
        (self.db.parent / privacy.FILE_NAME).write_text('{"version":1,"accept":"false"}')
        with self.assertRaises(ValueError):privacy.enabled(self.db)

    def test_ui_preference_save_never_starts_a_job(self):
        from socialscraper.ui.working_rules import WorkingRulesTab
        subject = SimpleNamespace(app=SimpleNamespace(log=Mock(),run_steps=Mock()),
                                  accept_privacy=SimpleNamespace(get=lambda:True))
        with patch('socialscraper.ui.hintergrund.cfg_of',return_value=SimpleNamespace(db_path=self.db)):
            WorkingRulesTab.save_privacy(subject)
        self.assertTrue(privacy.enabled(self.db)); subject.app.run_steps.assert_not_called()


@unittest.skipUnless(os.environ.get('SS_RUN_WEBDE_DOM') == '1', 'offline browser fixtures disabled')
class PrivacyBrowser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from patchright.sync_api import sync_playwright
        from socialscraper.backends.local import LocalBackend
        cls.pw = sync_playwright().start()
        blocked = {'server':'http://127.0.0.1:9'}
        cls.browser = cls.pw.chromium.launch(channel='chrome',headless=True,proxy=blocked,
            args=LocalBackend._proxy_args(blocked)+['--disable-background-networking'])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close();cls.pw.stop()

    def fixture(self, html, *, url='https://www.google.de/', accept=True):
        context=self.browser.new_context(service_workers='block'); self.addCleanup(context.close)
        context.route('**/*',lambda route:route.fulfill(status=200,content_type='text/html',body=html))
        page=context.new_page();page.goto(url)
        ctx=SimpleNamespace(accept_privacy=accept,check_cancelled=Mock(),hinweis=Mock(),warte=page.wait_for_timeout)
        return page,ctx

    def banner(self, label='Alle akzeptieren'):
        return '<div role="dialog"><h2>Cookies und Datenschutz</h2><form onsubmit="event.preventDefault();this.parentElement.remove()"><input type="hidden" name="token" value="fixture"><button>'+label+'</button></form></div>'

    def test_google_nested_form_and_below_fold_button_accept_once(self):
        page,ctx=self.fixture(self.banner().replace('<form','<div style="height:1000px"></div><form'))
        self.assertTrue(privacy.accept_known(page,ctx))
        self.assertEqual(page.locator('[role="dialog"]').count(),0)
        self.assertFalse(privacy.accept_known(page,ctx));ctx.hinweis.assert_called_once()

    def test_off_and_unknown_hosts_do_not_click(self):
        for url,accept in [('https://www.google.de/',False),('https://web.de.evil.invalid/',True),('http://web.de/',True)]:
            page,ctx=self.fixture(self.banner(),url=url,accept=accept)
            self.assertFalse(privacy.accept_known(page,ctx));self.assertEqual(page.locator('button').count(),1)

    def test_account_form_with_cookie_link_and_password_is_not_consent(self):
        page,ctx=self.fixture('<form><input type="password"><a>Cookie-Richtlinie</a><button>Alle akzeptieren</button></form>')
        self.assertFalse(privacy.accept_known(page,ctx))

    def test_captcha_is_never_solved_or_submitted(self):
        page,ctx=self.fixture('<div>Cookies<input name="captcha"><button>Alle akzeptieren</button></div>')
        self.assertFalse(privacy.accept_known(page,ctx));self.assertEqual(page.locator('input').input_value(),'')

    def test_unresponsive_banner_does_not_cause_click_loop(self):
        page,ctx=self.fixture('<div>Cookies<button onclick="this.dataset.clicks=Number(this.dataset.clicks||0)+1">Alle akzeptieren</button></div>')
        self.assertTrue(privacy.accept_known(page,ctx))
        for _ in range(3):self.assertFalse(privacy.accept_known(page,ctx))
        self.assertEqual(page.locator('button').get_attribute('data-clicks'),'1')

    def test_hidden_duplicate_is_ignored_and_visible_cookie_choice_clicked(self):
        page,ctx=self.fixture('<div style="display:none">Cookies<button>Alle akzeptieren</button></div>'+self.banner('Akzeptieren und weiter'),url='https://web.de/')
        self.assertTrue(privacy.accept_known(page,ctx));self.assertEqual(page.locator('[role="dialog"]').count(),0)

    def test_cancel_prevents_any_cookie_click(self):
        page,ctx=self.fixture(self.banner());ctx.check_cancelled.side_effect=FinalError('Abgebrochen')
        with self.assertRaises(FinalError):privacy.accept_known(page,ctx)
        self.assertEqual(page.locator('button').count(),1)

    def test_official_visible_iframe_is_handled_but_unknown_iframe_is_not(self):
        for frame_url,expected in [('https://consent.google.com/',True),('https://unknown.invalid/',False)]:
            page,ctx=self.fixture(self.banner())
            page.set_content('<iframe></iframe>')
            page.locator('iframe').evaluate('(node,url)=>node.src=url',frame_url)
            page.frame_locator('iframe').locator('button').wait_for()
            self.assertEqual(privacy.accept_known(page,ctx),expected)

    def test_google_search_continues_after_saved_consent_choice(self):
        context=self.browser.new_context(service_workers='block');self.addCleanup(context.close)
        visited=[]
        def route_handler(route):
            url=route.request.url;visited.append(url)
            html=(self.banner()+'<form action="/search"><input name="q"></form>' if url==entry_rules.SEARCH_HOME
                  else '<a href="https://web.de/"><h3>Homepage</h3></a>' if '/search?' in url else '<h1>WEB.DE</h1>')
            route.fulfill(status=200,content_type='text/html',body=html)
        context.route('**/*',route_handler)
        page=context.new_page();ctx=SimpleNamespace(timeout=4,accept_privacy=True,check_cancelled=Mock(),hinweis=Mock(),warte=page.wait_for_timeout)
        entry_rules.visit_home(page,'web.de',ctx)
        self.assertEqual(visited,[entry_rules.SEARCH_HOME,'https://www.google.de/search?q=web.de','https://web.de/'])

    def test_unknown_privacy_button_keeps_waiting_without_using_decline_fallback(self):
        from socialscraper.signup import Ctx
        html='<div>Cookies<button onclick="this.dataset.declined=\'true\'">Only allow essential cookies</button></div><button onclick="this.remove()">Not now</button>'
        page,_=self.fixture(html)
        ctx=Ctx('instagram','fixture@example.test','FAKE','fixture','assisted','de',False,5,
                SimpleNamespace(page=lambda handle:page),accept_privacy=True)
        ctx.dismiss()
        self.assertEqual(page.get_by_role('button',name='Not now').count(),0)
        self.assertIsNone(page.get_by_role('button',name='Only allow essential cookies').get_attribute('data-declined'))

    def test_webde_cookie_banner_accepted_before_tariff_link(self):
        from socialscraper.mailanbieter import webde
        context=self.browser.new_context(service_workers='block');self.addCleanup(context.close)
        visited=[]
        def route_handler(route):
            url=route.request.url;visited.append(url)
            html=(self.banner('Akzeptieren und weiter')+'<a href="/email/tarifvergleich/">Registrieren</a>'
                  if url=='https://web.de/' else '<a href="https://register.web.de/">FreeMail</a>'
                  if '/email/tarifvergleich/' in url else '<input name="firstName">')
            route.fulfill(status=200,content_type='text/html',body=html)
        context.route('**/*',route_handler)
        page=context.new_page();ctx=SimpleNamespace(timeout=4,accept_privacy=True,check_cancelled=Mock(),hinweis=Mock(),warte=page.wait_for_timeout)
        with patch.object(entry_rules,'visit_home',side_effect=lambda p,d,c:p.goto('https://web.de/')):
            webde.open_signup(page,ctx)
        self.assertEqual(visited,['https://web.de/','https://web.de/email/tarifvergleich/','https://register.web.de/'])
