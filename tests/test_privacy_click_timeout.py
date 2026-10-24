"""Uncertain consent clicks remain observable without automatic resubmission."""
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from socialscraper import privacy_consent as privacy
from socialscraper.net.errors import FinalError


class ConsentTimeout(unittest.TestCase):
    def fixture(self, error):
        node = Mock()
        node.is_visible.return_value = node.is_enabled.return_value = True
        node.evaluate.return_value = True
        node.inner_text.return_value = 'Akzeptieren und weiter'
        node.click.side_effect = error
        nodes = Mock(); nodes.count.return_value = 1; nodes.nth.return_value = node
        frame = Mock(url='https://web.de/consent-management/')
        frame.get_by_role.return_value = nodes
        page = Mock(url=frame.url)
        ctx = SimpleNamespace(accept_privacy=True,check_cancelled=Mock(),hinweis=Mock())
        return page,ctx,frame,node

    def test_known_browser_timeouts_remain_manual_and_are_never_reclicked(self):
        from patchright.sync_api import TimeoutError as ChromeTimeout
        from playwright.sync_api import TimeoutError as FirefoxTimeout
        for kind in (ChromeTimeout,FirefoxTimeout):
            with self.subTest(kind=kind):
                page,ctx,frame,node=self.fixture(kind('fixture timeout'))
                with patch('socialscraper.entry_rules.visible_frames',return_value=(frame,)):
                    self.assertFalse(privacy.accept_known(page,ctx))
                    self.assertFalse(privacy.accept_known(page,ctx))
                node.click.assert_called_once()
                self.assertEqual(ctx.hinweis.call_count,1)
                self.assertNotIn('akzeptiert',ctx.hinweis.call_args.args[0])

    def test_cancel_after_uncertain_click_still_propagates(self):
        from patchright.sync_api import TimeoutError
        page,ctx,frame,node=self.fixture(TimeoutError('fixture timeout'))
        def cancelled():
            if node.click.called:raise FinalError('Abgebrochen.')
        ctx.check_cancelled.side_effect=cancelled
        with patch('socialscraper.entry_rules.visible_frames',return_value=(frame,)):
            with self.assertRaisesRegex(FinalError,'Abgebrochen'):privacy.accept_known(page,ctx)

    def test_unrelated_errors_are_not_hidden(self):
        for error in (RuntimeError('closed browser'),FinalError('Abgebrochen.'),TimeoutError('unrelated')):
            page,ctx,frame,node=self.fixture(error)
            with patch('socialscraper.entry_rules.visible_frames',return_value=(frame,)):
                with self.assertRaises(type(error)):privacy.accept_known(page,ctx)


@unittest.skipUnless(os.environ.get('SS_RUN_WEBDE_DOM')=='1','offline browser fixtures disabled')
class ConsentTimeoutBrowser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from patchright.sync_api import sync_playwright
        from socialscraper.backends.local import LocalBackend
        cls.pw=sync_playwright().start()
        proxy={'server':'http://127.0.0.1:9'}
        cls.browser=cls.pw.chromium.launch(channel='chrome',headless=True,proxy=proxy,
            args=LocalBackend._proxy_args(proxy)+['--disable-background-networking'])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close();cls.pw.stop()

    def fixture(self):
        context=self.browser.new_context(service_workers='block');self.addCleanup(context.close)
        held=[]
        def route(route):
            if route.request.url=='https://web.de/':held.append(route);return
            if route.request.url=='https://web.de/consent-management/':
                route.fulfill(status=200,content_type='text/html',body='''<div>Cookies und Tracking
                    <a role="button" href="https://web.de/" onclick="sessionStorage.setItem('clicks',String(Number(sessionStorage.getItem('clicks')||0)+1))">Akzeptieren und weiter</a></div>''')
            else:route.abort()
        context.route('**/*',route)
        page=context.new_page();page.goto('https://web.de/consent-management/')
        ctx=SimpleNamespace(accept_privacy=True,check_cancelled=Mock(),hinweis=Mock())
        return page,ctx,held

    def test_blocked_click_keeps_dialog_available_without_reclick(self):
        page,ctx,_=self.fixture()
        page.evaluate('''()=>{const cover=document.createElement('div');cover.style='position:fixed;inset:0;z-index:999';document.body.append(cover)}''')
        self.assertFalse(privacy.accept_known(page,ctx,timeout=150))
        self.assertFalse(privacy.accept_known(page,ctx,timeout=150))
        self.assertEqual(page.url,'https://web.de/consent-management/')
        self.assertIsNone(page.evaluate("sessionStorage.getItem('clicks')"))

    def test_dispatched_click_with_slow_navigation_is_observed_once(self):
        page,ctx,held=self.fixture()
        self.assertFalse(privacy.accept_known(page,ctx,timeout=400))
        self.assertEqual(len(held),1)
        held[0].fulfill(status=200,content_type='text/html',body='<h1>WEB.DE Homepage</h1>')
        page.wait_for_url('https://web.de/')
        self.assertEqual(page.evaluate("sessionStorage.getItem('clicks')"),'1')
        self.assertFalse(privacy.accept_known(page,ctx))
