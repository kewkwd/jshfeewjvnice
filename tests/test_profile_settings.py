"""Local profile data, input isolation, and preflight boundaries."""
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from socialscraper import profile_settings as profiles
from socialscraper.net import input_behavior as actions
from socialscraper.net.errors import FinalError

PERSON = {'vorname':'Test', 'nachname':'Person', 'wohnort':'Teststadt',
          'geschlecht':'keine Angabe', 'geburtsdatum':'1996-02-29'}


class ProfileSettings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'data' / 'profiles'

    def test_stable_independent_defaults_and_persistence(self):
        first = profiles.update(self.root, 'first')
        second = profiles.update(self.root, 'second')
        self.assertEqual(first, profiles.load(self.root, 'first'))
        profiles.update(self.root,'first',person=PERSON,tempo={'mouse_px_s':800,'click_ms':150,'typing_cps':4})
        self.assertEqual(profiles.load(self.root,'second'),second)
        self.assertNotEqual(profiles.load(self.root,'first')['tempo'],second['tempo'])
        self.assertEqual(profiles.load(self.root,'first')['person'],PERSON)

    def test_import_contract_refuses_conflicting_identity_data(self):
        saved = profiles.import_person(self.root,'first',PERSON,source='local_fixture')
        with self.assertRaises(ValueError):
            profiles.import_person(self.root,'first',{**PERSON,'vorname':'Other'},source='another_fixture')
        self.assertEqual(profiles.load(self.root,'first'),saved)
        self.assertEqual(saved['source'],'local_fixture')

    def test_missing_gender_is_not_inferred_from_name_or_salutation(self):
        person = profiles.from_registration({'vorname':'Test','nachname':'Person','anrede':'Herr'})
        self.assertEqual(person['geschlecht'],'')
        with self.assertRaises(ValueError):profiles.normalize_person(person,required=True)

    def test_corrupt_file_and_invalid_tempo_are_preserved(self):
        path = profiles._path(self.root,'first');path.parent.mkdir(parents=True)
        path.write_text('{broken',encoding='utf8')
        with self.assertRaises(ValueError):profiles.update(self.root,'first',person=PERSON)
        self.assertEqual(path.read_text(),'{broken')
        for value in (True,float('nan'),float('inf'),0,'fast'):
            data = profiles.defaults('second'); data['tempo']['typing_cps']=value
            with self.assertRaises(ValueError):profiles.validate(data,'second')

    def test_identity_reload_and_browser_handoff_share_settings(self):
        from socialscraper.net.identity import IdentityManager
        manager = IdentityManager(Mock(),self.root)
        profiles.update(self.root,'first',person=PERSON)
        mobile = manager.get('first','de',True)
        before = mobile.fp.to_dict()
        desktop = manager.browser_context(mobile,engine='firefox',create=True)
        self.assertEqual(desktop.settings,mobile.settings)
        self.assertEqual(manager.get('first','de',True).fp.to_dict(),before)
        self.assertTrue(desktop.settings['person']['geschlecht'])

    def test_social_signup_rejects_incomplete_profile_before_runtime(self):
        from socialscraper import signup
        cfg = SimpleNamespace(profiles_dir=self.root,db_path=self.root.parent/'fixture.db')
        with patch.object(signup,'_lade_module'),patch.dict(signup.MODULE,{'reddit':object()}), \
                patch.object(signup.proxymode,'require_no_anon'),patch.object(signup,'_identity_manager') as runtime:
            with self.assertRaisesRegex(ValueError,'Name und Geschlecht'):
                signup.create.__wrapped__.__wrapped__(cfg,'reddit',name='first')
            runtime.assert_not_called()


class Page:
    def __init__(self):
        self.waits=[]; self.mouse=SimpleNamespace(move=Mock()); self.viewport_size={'width':1000,'height':800}
    def wait_for_timeout(self,ms):self.waits.append(ms)


class ProfileInput(unittest.TestCase):
    def setUp(self):
        self.page=Page();self.settings=profiles.defaults('fixture')
        self.settings['tempo']={'mouse_px_s':1000,'click_ms':130,'typing_cps':5}
        actions.bind(self.page,self.settings)

    def test_typing_uses_exact_value_and_own_speed(self):
        field=Mock();field.evaluate.return_value=True
        actions.fill(self.page,field,'A% é!')
        self.assertEqual([call.args[0] for call in field.press_sequentially.call_args_list],list('A% é!'))
        self.assertAlmostEqual(sum(self.page.waits),1000)
        self.assertTrue(all(ms<=100 for ms in self.page.waits))

    def test_cancellation_during_typing_stops_next_character(self):
        field=Mock();field.evaluate.return_value=True
        def check():
            if self.page.waits:raise FinalError('Abgebrochen.')
        with self.assertRaises(FinalError):actions.fill(self.page,field,'abc',check=check)
        self.assertEqual(field.press_sequentially.call_count,1)

    def test_click_uses_profile_move_and_pause_without_long_press(self):
        control=Mock();control.bounding_box.return_value={'x':400,'y':300,'width':40,'height':20}
        actions.click(self.page,control)
        self.assertGreater(self.page.mouse.move.call_count,0)
        self.assertNotIn('delay',control.click.call_args.kwargs)
        # Straight-line movement from the documented initial half-position.
        self.assertAlmostEqual(sum(self.page.waits),(210**2+155**2)**.5+130,places=4)

    def test_page_settings_do_not_leak_and_native_click_is_not_retried(self):
        other=Page();self.assertIsNone(actions.tempo(other))
        control=Mock();control.bounding_box.return_value={'x':5,'y':5,'width':10,'height':10}
        control.click.side_effect=TimeoutError('fixture')
        with self.assertRaises(FinalError):actions.click(self.page,control)
        self.assertEqual(control.click.call_count,1)

    def test_native_android_uses_the_same_typing_and_touch_values(self):
        from socialscraper.android.adb import Adb
        device=Adb();device.input_tempo=self.settings['tempo'];device.sh=Mock();device._input_wait=Mock()
        device.tap(20,30);self.assertEqual(device.sh.call_args.args,('input','tap',20,30))
        device._input_wait.assert_called_with(.13);device._input_wait.reset_mock()
        device.tippen('ab');self.assertEqual(device._input_wait.call_count,2)
        device._input_wait.assert_called_with(.2)


@unittest.skipUnless(os.environ.get('SS_RUN_PROFILE_DOM')=='1','local profile DOM fixtures disabled')
class NativeProfileInput(unittest.TestCase):
    def test_native_browser_keeps_value_and_click_semantics(self):
        from patchright.sync_api import sync_playwright
        with sync_playwright() as driver:
            browser=driver.chromium.launch(channel='chrome',headless=True)
            try:
                page=browser.new_page()
                page.set_content('<input id="text"><button id="go" onclick="document.body.dataset.clicked=1">Go</button>')
                settings=profiles.defaults('fixture');settings['tempo']={'mouse_px_s':1200,'click_ms':30,'typing_cps':20}
                actions.bind(page,settings)
                actions.fill(page,page.locator('#text'),'Ab %é!')
                actions.click(page,page.locator('#go'))
                self.assertEqual(page.locator('#text').input_value(),'Ab %é!')
                self.assertEqual(page.locator('body').get_attribute('data-clicked'),'1')
            finally:browser.close()


@unittest.skipUnless(os.environ.get('SS_RUN_PROFILE_UI')=='1','local profile UI fixtures disabled')
class ProfileEditor(unittest.TestCase):
    def test_prepare_profile_saves_person_and_independent_defaults(self):
        from ui_fake import FakeApp
        from socialscraper.ui.profiles import ProfilesTab
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);cfg=SimpleNamespace(profiles_dir=root/'data/profiles',db_path=root/'data/fixture.db')
            app=FakeApp(root)
            try:
                with patch('socialscraper.ui.profiles.inventory',return_value=([],[])), \
                     patch('socialscraper.ui.profiles.cfg_of',return_value=cfg), \
                     patch('socialscraper.ui.workflow_components.busy',return_value=False):
                    tab=app.add('Profile',ProfilesTab)
                    values={**PERSON,'identity':'prepared','mouse_px_s':'','click_ms':'','typing_cps':''}
                    with patch('socialscraper.ui.widgets.ask_form',return_value=values):tab.edit_profile(new=True)
                    saved=profiles.load(cfg.profiles_dir,'prepared')
                    self.assertEqual(saved['person'],PERSON)
                    self.assertEqual(saved['tempo'],profiles.defaults('prepared')['tempo'])
            finally:app.destroy()
