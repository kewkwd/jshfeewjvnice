import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from socialscraper import form_pacing as pacing
from socialscraper.mailanbieter import MailCtx
from socialscraper.net.errors import FinalError
from socialscraper.ui.postfaecher import PostfaecherTab


class FormPacing(unittest.TestCase):
    def test_preferences_are_local_and_strictly_validated(self):
        with tempfile.TemporaryDirectory() as folder:
            db=Path(folder)/'data/test.db'
            self.assertEqual(pacing.load(db),0)
            for value in pacing.OPTIONS:
                pacing.save(db,value);self.assertEqual(pacing.load(db),value)
            self.assertEqual(pacing.load(db.parent/'other/test.db'),0)
            for value in (True,False,750.0,-1,10000,'750',None):
                with self.assertRaises(ValueError):pacing.save(db,value)
            (db.parent/pacing.FILE_NAME).write_text('{"version":1,"pause_ms":true}')
            with self.assertRaises(ValueError):pacing.load(db)

    def context(self):
        return MailCtx('web.de','Fixture','Person','fixture@web.de','FAKE_ONLY','assisted','de',False,10,Mock(),input_pause_ms=750)

    def test_pause_precedes_real_fill_without_changing_value(self):
        ctx=self.context();events=[]
        ctx.warte=lambda ms:events.append(('wait',ms))
        def fill(page,selectors,value,**kwargs):
            events.append(('fill',value));return selectors[0]
        with patch('socialscraper.mailanbieter._fill',side_effect=fill):
            self.assertEqual(ctx.fill(['input[name="firstName"]'],'Local value'),'input[name="firstName"]')
        self.assertEqual(sum(ms for action,ms in events if action=='wait'),750)
        self.assertEqual(events[-1],('fill','Local value'))
        self.assertTrue(all(ms<=100 for action,ms in events if action=='wait'))

    def test_cancellation_during_pause_prevents_fill(self):
        ctx=self.context();stopped=[]
        ctx.stop=lambda:bool(stopped)
        ctx.warte=lambda ms:stopped.append(True)
        with patch('socialscraper.mailanbieter._fill') as fill:
            with self.assertRaisesRegex(FinalError,'Abgebrochen'):ctx.fill(['input'],'PRIVATE')
        fill.assert_not_called()

    def test_invalid_pause_never_touches_field(self):
        ctx=self.context();ctx.input_pause_ms=True
        with patch('socialscraper.mailanbieter._fill') as fill:
            with self.assertRaises(ValueError):ctx.fill(['input'],'PRIVATE')
        fill.assert_not_called()

    def test_browser_context_uses_profile_settings_instead_of_global_preference(self):
        from socialscraper import mailanbieter
        with tempfile.TemporaryDirectory() as folder:
            cfg=SimpleNamespace(db_path=Path(folder)/'data/test.db',profiles_dir=Path(folder)/'data/profiles')
            pacing.save(cfg.db_path,750)
            backend=Mock();im=Mock();im.proxy_for.return_value=None
            with patch.dict(mailanbieter.ANBIETER,{'web.de':SimpleNamespace(DOMAENE='web.de')}), \
                 patch.object(mailanbieter,'MailCtx',wraps=MailCtx) as constructor:
                with self.assertRaisesRegex(FinalError,'Proxy'):
                    mailanbieter._create_validated.__wrapped__(cfg,'web.de',kennung='abcdef',
                        adresse='fixture@web.de',vorname='Fixture',nachname='Person',passwort='FAKE_ONLY',
                        mode='assisted',land='de',mobile=False,timeout=10,backend=backend,im=im,
                        registrierungsdaten={'vorname':'Fixture','nachname':'Person','geschlecht':'keine Angabe'})
                self.assertEqual(constructor.call_args.kwargs['input_pause_ms'],0)
                self.assertIn('typing_cps',constructor.call_args.kwargs['profile_tempo'])
            backend.session.assert_not_called()

    def test_ui_saves_choice_without_starting_job_and_cancel_preserves_it(self):
        tab=SimpleNamespace(app=SimpleNamespace(log=Mock(),run_steps=Mock()))
        with patch('socialscraper.ui.workflow_components.busy',return_value=False), \
             patch('socialscraper.ui.postfaecher.cfg_of',return_value=SimpleNamespace(db_path='fixture')), \
             patch.object(pacing,'load',return_value=0),patch.object(pacing,'save') as save, \
             patch('socialscraper.ui.postfaecher.ask_form',return_value={'tempo':'750'}):
            PostfaecherTab.input_tempo(tab);save.assert_called_once_with('fixture',750)
        with patch('socialscraper.ui.workflow_components.busy',return_value=False), \
             patch('socialscraper.ui.postfaecher.cfg_of',return_value=SimpleNamespace(db_path='fixture')), \
             patch.object(pacing,'load',return_value=750),patch.object(pacing,'save') as save, \
             patch('socialscraper.ui.postfaecher.ask_form',return_value=None):
            PostfaecherTab.input_tempo(tab);save.assert_not_called()
        tab.app.run_steps.assert_not_called()
