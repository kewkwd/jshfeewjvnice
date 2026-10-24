"""Compatibility observation has no navigation, spoofing or account side effects."""
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from socialscraper import browser_diagnostics as diag
from socialscraper.backends.base import BrowserBackend
from socialscraper.backends.profile_store import begin
from socialscraper.net.fingerprint import build


class Diagnostics(unittest.TestCase):
    def setUp(self):
        self.fp = build('fixture', 'de', False)
        self.actual = {'locale':self.fp.locale, 'languages':self.fp.languages,
            'timezone':self.fp.timezone, 'user_agent':'Chrome fixture',
            'viewport':dict(self.fp.viewport), 'device_scale':1}
        self.page = SimpleNamespace(evaluate=Mock(return_value=self.actual), goto=Mock())
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / 'profiles'

    def report(self, **kwargs):return diag.capture(self.page, self.fp, **kwargs)

    def handle(self):
        selection = begin(self.root, 'fixture', 'mail')
        return SimpleNamespace(backend='local', identity=SimpleNamespace(name='fixture', fp=self.fp),
                               platform='mail', _profile_selection=selection)

    def observe(self, handle):
        backend = SimpleNamespace(profiles=self.root, page=lambda h:self.page)
        with patch.dict(os.environ, {'SS_EMBED_BROWSER':'0'}):diag.observe(backend, handle)
        return diag.report_path(self.root,'chromium','fixture','mail')

    def test_read_only_measurement_and_no_navigation(self):
        report = self.report()
        self.assertEqual(report['status'], 'consistent')
        self.assertEqual(report['observed']['locale'], 'de-DE')
        self.page.goto.assert_not_called()
        self.page.evaluate.assert_called_once_with(diag.SCRIPT)

    def test_actual_mismatches_are_reported_without_changing_configuration(self):
        self.actual.update(locale='en-US', timezone='UTC', viewport={'width':1,'height':2})
        result = self.report()
        self.assertEqual(result['issues'], ['locale','timezone','viewport'])
        self.assertEqual(result['status'], 'different')
        self.assertEqual(self.fp.locale, 'de-DE')

    def test_embedded_resizing_is_not_misreported_as_configuration_failure(self):
        self.actual['viewport'] = {'width':10,'height':20}
        self.assertEqual(self.report(resizable=True)['status'], 'consistent')

    def test_extra_sensitive_fields_are_not_persisted(self):
        self.actual.update(cookie='PRIVATE_COOKIE', url='PRIVATE_URL', password='PRIVATE_PASSWORD')
        handle = self.handle()
        raw = self.observe(handle).read_text(encoding='utf8')
        self.assertNotIn('PRIVATE', raw)
        self.assertIn('captured_at', raw)

    def test_unavailable_capture_replaces_previous_success_without_raw_error(self):
        handle = self.handle(); path = self.observe(handle)
        self.page.evaluate.side_effect = RuntimeError('PRIVATE_ERROR')
        self.observe(handle)
        self.assertEqual(json.loads(path.read_text())['status'], 'unavailable')
        self.assertNotIn('PRIVATE_ERROR', path.read_text())

    def test_invalid_browser_values_cannot_become_success(self):
        for bad in (None, {}, dict(self.actual, device_scale=float('nan')),
                    dict(self.actual, viewport={'width':True,'height':2}),
                    dict(self.actual, languages='de-DE')):
            with self.subTest(value=bad):
                self.page.evaluate.return_value=bad
                self.assertEqual(self.report()['status'], 'unavailable')

    def test_new_retry_does_not_reuse_old_report_as_current(self):
        handle = self.handle(); self.observe(handle)
        handle._profile_selection.finish(success=False)
        retry = begin(self.root,'fixture','mail')
        result = diag.summary(self.root,'chromium','fixture','mail',retry.path)
        self.assertIn('älterer Bericht', result)

    def test_summary_recomputes_differences_and_ignores_untrusted_issue_text(self):
        self.actual['locale']='en-US'
        handle=self.handle(); path=self.observe(handle)
        data=json.loads(path.read_text()); data['issues']=['PRIVATE_UNTRUSTED_TEXT']
        path.write_text(json.dumps(data),encoding='utf8')
        result=diag.summary(self.root,'chromium','fixture','mail',handle._profile_selection.path)
        self.assertIn('Abweichungen: Sprache',result)
        self.assertNotIn('PRIVATE_UNTRUSTED_TEXT',result)

    def test_missing_corrupt_and_redirected_reports_do_not_change_profiles(self):
        handle=self.handle()
        self.assertIn('Noch keine',diag.summary(self.root,'chromium','fixture','mail',handle._profile_selection.path))
        path=self.observe(handle);path.write_text('bad-json')
        self.assertIn('nicht lesbar',diag.summary(self.root,'chromium','fixture','mail',handle._profile_selection.path))
        with patch.object(Path,'is_junction',return_value=True):
            with self.assertRaises(ValueError):self.observe(handle)
            self.assertIn('umgeleitet',diag.summary(self.root,'chromium','fixture','mail',handle._profile_selection.path))

    def test_invalid_storage_identifier_cannot_escape_diagnostic_root(self):
        for name in ('../other','bad/name','_private'):
            with self.assertRaises(ValueError):diag.report_path(self.root,'chromium',name,'mail')

    def test_diagnostic_failure_does_not_close_backend_session(self):
        backend=SimpleNamespace(close=Mock())
        with patch.object(diag,'observe',side_effect=OSError('PRIVATE_ERROR')):
            BrowserBackend.observe_settings(backend,object())
        backend.close.assert_not_called()


@unittest.skipUnless(os.environ.get('SS_RUN_WEBDE_DOM') == '1','offline browser fixtures disabled')
class RealBrowser(unittest.TestCase):
    def test_normal_backend_open_records_the_selected_profile_automatically(self):
        from socialscraper.backends.local import LocalBackend
        from socialscraper.net.proxy import Proxy
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'profiles'
            backend=LocalBackend(profiles_dir=root)
            fp=build('autocapture','de',False)
            identity=SimpleNamespace(name='autocapture',fp=fp)
            with backend.session(identity,Proxy.parse('http://127.0.0.1:9'),headless=True,platform='mail') as handle:
                path=diag.report_path(root,'chromium','autocapture','mail')
                report=json.loads(path.read_text(encoding='utf8'))
                self.assertEqual(report['status'],'consistent')
                self.assertEqual(report['profile_path'],str(handle._profile_selection.path.absolute()))
                self.assertEqual(backend.page(handle).url,'about:blank')

    def test_reports_real_chrome_locale_timezone_viewport_without_external_page(self):
        from patchright.sync_api import sync_playwright
        from socialscraper.backends.local import LocalBackend
        proxy={'server':'http://127.0.0.1:9'}
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='chrome',headless=True,proxy=proxy,
                args=LocalBackend._proxy_args(proxy)+['--disable-background-networking'])
            try:
                ctx=browser.new_context(locale='de-DE',timezone_id='Europe/Berlin',viewport={'width':900,'height':700},service_workers='block')
                ctx.route('**/*',lambda route:route.abort())
                page=ctx.new_page()
                fp=SimpleNamespace(locale='de-DE',timezone='Europe/Berlin',viewport={'width':900,'height':700})
                report=diag.capture(page,fp)
                self.assertEqual(report['status'],'consistent')
                self.assertEqual(page.url,'about:blank')
                self.assertIn('Chrome/',report['observed']['user_agent'])
            finally:browser.close()
