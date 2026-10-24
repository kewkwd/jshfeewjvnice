import contextlib, io, json, os, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from socialscraper.net import browser_window

class BrowserEvents(unittest.TestCase):
    def test_only_complete_typed_events_are_accepted(self):
        event={'version':1,'state':'open','hwnd':123,'pid':456,'proxy':True}
        self.assertEqual(browser_window.parse(browser_window.PREFIX+json.dumps(event)),event)
        for bad in ('{}','[]','{"version":1,"state":"open","hwnd":true}', 'not JSON'):
            self.assertIsNone(browser_window.parse(browser_window.PREFIX+bad))

    def test_other_apps_and_wrong_owner_are_rejected(self):
        event={'state':'open','hwnd':123,'pid':456}
        native=MagicMock();native.owner.return_value=456;native.browser.return_value=True
        with patch('socialscraper.net.deadline.descendants',return_value=[(456,'chrome.exe')]):
            self.assertTrue(browser_window.validate(event,10,native))
            native.owner.return_value=999
            self.assertFalse(browser_window.validate(event,10,native))
        with patch('socialscraper.net.deadline.descendants',return_value=[(456,'notepad.exe')]):
            native.owner.return_value=456
            self.assertFalse(browser_window.validate(event,10,native))

    def test_headless_sessions_are_never_announced(self):
        with patch.dict(os.environ,{'SS_EMBED_BROWSER':'1'}),patch.object(browser_window,'Windows') as native:
            browser_window.announce(MagicMock(),True)
            native.assert_not_called()

@unittest.skipUnless(os.name=='nt' and os.environ.get('SS_RUN_EMBED_TESTS')=='1','opt-in real Windows embedding')
class RealBrowserEmbedding(unittest.TestCase):
    def test_owned_chrome_is_embedded_resized_and_restored_without_new_session(self):
        import tkinter as tk
        from socialscraper.backends.local import LocalBackend
        from socialscraper.net.fingerprint import build
        from socialscraper.net.proxy import Proxy
        from socialscraper.ui.browser_dialog import BrowserDialog
        with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{'SS_EMBED_BROWSER':'1','SS_REQUIRE_PROXY':'1'}):
            root=tk.Tk();root.withdraw()
            root.job=SimpleNamespace(proc=SimpleNamespace(pid=os.getpid()))
            root.cancel=MagicMock()
            backend=LocalBackend(profiles_dir=directory)
            handle=None;dialog=None
            try:
                output=io.StringIO()
                with contextlib.redirect_stdout(output):
                    handle=backend.open(SimpleNamespace(name='local_embed',fp=build('local_embed','de')),
                        Proxy.parse('http://127.0.0.1:9'),headless=False)
                event=next(browser_window.parse(line) for line in output.getvalue().splitlines() if browser_window.parse(line))
                native=browser_window.Windows()
                page=backend.page(handle);page.route('**/*',lambda route:route.abort())
                page.set_content('<h1>Lokale Bestätigung</h1><label>Testcode <input id="code"></label>')
                dialog=BrowserDialog(root,event);root.update()
                self.assertEqual(native.api.GetParent(event['hwnd']),dialog.host.winfo_id())
                dialog.embedded.resize(900,500);root.update()
                self.assertTrue(page.locator('#code').is_visible())
                page.locator('#code').fill('TEST')
                self.assertEqual(page.locator('#code').input_value(),'TEST')
                from socialscraper.signup import _select
                page.set_content('''<div role="combobox" onclick="document.querySelector('#options').hidden=false">Monat</div>
                    <div id="options" hidden><div role="option" data-value="2" onclick="document.querySelector('[role=combobox]').textContent='Monat Februar';this.parentNode.hidden=true">Februar</div></div>''')
                self.assertTrue(_select(page,['[role="combobox"]:has-text("Monat")'],2))
                self.assertEqual(page.locator('[role=combobox]').inner_text(),'Monat Februar')
                dialog.external();dialog=None;root.update()
                self.assertFalse(native.api.GetParent(event['hwnd']))
                self.assertEqual(backend.page(handle),page)
            finally:
                if dialog:dialog.destroy()
                if handle:backend.close(handle)
                root.destroy()
