"""Local details, missing-input boundaries and actual provider readback."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from socialscraper import registration_data as data
from socialscraper.mailanbieter import unitedinternet as provider
from socialscraper.ui.postfaecher import PostfaecherTab
import test_webde_flow as flow

FIXTURE = {'vorname':'Offline', 'nachname':'Fixture', 'geburtsdatum':'29.02.1996',
           'geschlecht':'keine Angabe',
           'plz':'00000', 'ort':'Fixturestadt', 'strasse':'Fixtureweg', 'hausnummer':'12',
           'anrede':'Keine Angabe', 'land':'DE'}


class RegistrationDataTests(unittest.TestCase):
    def test_valid_date_is_normalized_without_generating_missing_details(self):
        result=data.normalize(FIXTURE, required=True)
        self.assertEqual(result['geburtsdatum'],'1996-02-29')
        self.assertTrue(all(value=='' for value in data.normalize({}).values()))

    def test_missing_and_invalid_fields_are_rejected_without_echoing_values(self):
        cases=[{}, {'geburtsdatum':'29.02.1995'}, {'geburtsdatum':'2999-01-01'},
               {'vorname':'PRIVATE\nVALUE'}, {'land':'PRIVATE'}, {'anrede':'PRIVATE'},
               {'unknown':'PRIVATE'}, [], 'PRIVATE', {'vorname':23}]
        for value in cases:
            with self.subTest(kind=type(value).__name__), self.assertRaises(ValueError) as error:
                data.normalize(value, required=True)
            self.assertNotIn('PRIVATE', str(error.exception))

    def test_provider_storage_uses_protected_writer_and_preserves_other_provider(self):
        with tempfile.TemporaryDirectory() as folder:
            db=Path(folder)/'test.sqlite'
            def write(path, payload):path.write_text(json.dumps(payload), encoding='utf8')
            with patch.object(data.accountvault, '_write', side_effect=write) as writer:
                data.save(db,'web.de',FIXTURE)
                data.save(db,'gmx.net',{**FIXTURE,'vorname':'OtherFixture'})
            self.assertEqual(writer.call_count,2)
            self.assertEqual(data.load(db,'web.de')['vorname'],'Offline')
            returned=data.load(db,'gmx.net');returned['vorname']='Changed'
            self.assertEqual(data.load(db,'gmx.net')['vorname'],'OtherFixture')
            self.assertFalse(data.accountvault.path_for(db).exists())

    def test_corrupt_storage_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as folder:
            db=Path(folder)/'test.sqlite';path=Path(folder)/'registration_details.json'
            path.write_text('{broken',encoding='utf8')
            with self.assertRaises(ValueError), patch.object(data.accountvault,'_write') as writer:
                data.save(db,'web.de',FIXTURE)
            writer.assert_not_called()
            self.assertEqual(path.read_text(),'{broken')

    def test_ui_does_not_put_personal_details_in_job_arguments(self):
        app=SimpleNamespace(run_steps=MagicMock())
        tab=SimpleNamespace(app=app,provider=SimpleNamespace(get=lambda:'web.de'),
            route=SimpleNamespace(get=lambda:'Browser'),refresh=lambda:None,registration_details=MagicMock())
        with patch('socialscraper.ui.workflow_components.busy',return_value=False), \
                patch('socialscraper.ui.postfaecher.cfg_of',return_value=SimpleNamespace(db_path='fixture')), \
                patch('socialscraper.net.proxymode.require_no_anon'),patch.object(data,'load',return_value=FIXTURE):
            PostfaecherTab.create(tab)
        args=app.run_steps.call_args.args[0][0][1]
        self.assertEqual(args,['mailkonto','neu','web.de','--modus','assisted','--weg','browser','--fehlerfenster','300'])
        tab.registration_details.assert_not_called()

    def test_ui_cancelled_details_prevent_job_start(self):
        app=SimpleNamespace(run_steps=MagicMock())
        tab=SimpleNamespace(app=app,provider=SimpleNamespace(get=lambda:'web.de'),
            route=SimpleNamespace(get=lambda:'Browser'),refresh=lambda:None,registration_details=MagicMock(return_value=False))
        with patch('socialscraper.ui.workflow_components.busy',return_value=False), \
                patch('socialscraper.ui.postfaecher.cfg_of',return_value=SimpleNamespace(db_path='fixture')), \
                patch('socialscraper.net.proxymode.require_no_anon'),patch.object(data,'load',return_value={}):
            PostfaecherTab.create(tab)
        app.run_steps.assert_not_called()

    def form(self):return flow.WebdeFlow.form(self)

    def test_missing_birthday_never_generates_or_submits_one(self):
        page,ctx,visible,values=self.form();ctx.registrierungsdaten={}
        with patch.object(provider,'_warte_bis_fertig'):
            provider.run(page,ctx)
        self.assertNotIn('input[name="birthDay"]',values)
        ctx.click.assert_not_called()

    def test_personal_fields_are_filled_once_and_manual_edits_preserved(self):
        page,ctx,visible,values=self.form();ctx.registrierungsdaten=FIXTURE
        visible.clear();visible.update({'input[name="postalCode"]','input[name="city"]','input[name="street"]','input[name="houseNumber"]'})
        def wait(_page,_ctx,*,tick):
            self.assertEqual(values['input[name="postalCode"]'],'00000')
            self.assertEqual(values['input[name="street"]'],'Fixtureweg')
            self.assertEqual(values['input[name="houseNumber"]'],'12')
            ctx.click.assert_called_once()
            values['input[name="city"]']='Manual fixture correction'
            tick();tick()
            self.assertEqual(values['input[name="city"]'],'Manual fixture correction')
            ctx.click.assert_called_once()
        with patch.object(provider,'_warte_bis_fertig',side_effect=wait):provider.run(page,ctx)

    def test_combined_street_and_missing_postcode(self):
        page,ctx,visible,values=self.form();ctx.registrierungsdaten={**FIXTURE,'plz':''}
        visible.clear();visible.update({'input[name="postalCode"]','input[name="street"]'})
        with patch.object(provider,'_warte_bis_fertig'):provider.run(page,ctx)
        self.assertEqual(values['input[name="street"]'],'Fixtureweg 12')
        self.assertNotIn('input[name="postalCode"]',values)
        ctx.click.assert_not_called()

    def test_control_rejecting_entered_value_prevents_submit(self):
        page,ctx,visible,values=self.form()
        page.input_value.side_effect=lambda selector,**_kwargs:'rejected'
        with patch.object(provider,'_warte_bis_fertig'):provider.run(page,ctx)
        ctx.click.assert_not_called()

    def test_visible_captcha_keeps_submit_manual_after_fields_are_filled(self):
        page,ctx,visible,values=self.form();visible.add('text="Ich bin ein Mensch"')
        def wait(_page,_ctx,*,tick):
            self.assertEqual(values['input[name="birthYear"]'],'1995')
            visible.remove('text="Ich bin ein Mensch"')
            tick();ctx.click.assert_not_called()
        with patch.object(provider,'_warte_bis_fertig',side_effect=wait):provider.run(page,ctx)

    def test_alias_second_click_waits_when_captcha_appears_after_availability_check(self):
        page,ctx,visible,values=self.form();visible.clear();visible.add('input[name="alias"]')
        def wait(_page,_ctx,*,tick):
            ctx.click.assert_called_once()
            visible.add('text="Ich bin ein Mensch"')
            tick();ctx.click.assert_called_once()
        with patch.object(provider,'_warte_bis_fertig',side_effect=wait), patch.object(provider,'_alias_available',return_value=True):
            provider.run(page,ctx)


if __name__=='__main__':unittest.main()
