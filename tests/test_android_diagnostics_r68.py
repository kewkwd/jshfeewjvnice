"""Android prerequisites are read only through the already owned session."""
import json
from pathlib import Path
import tempfile
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from socialscraper.android import diagnostics as diag
from socialscraper.net.errors import FinalError
from socialscraper.operation import check_cancelled
from socialscraper.profile_inventory import ProfileRow, inventory
from socialscraper.ui.profiles import ProfilesTab


class AndroidDiagnostics(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.home=Path(temp.name)/'avds';self.name='ss_fixture'
        folder=self.home/(self.name+'.avd');folder.mkdir(parents=True)
        (folder/'config.ini').write_text('AvdId='+self.name)
        self.stopped=False
        self.responses={('getprop',prop):value for prop,value in (
            ('sys.boot_completed','1'),('ro.build.version.sdk','35'),
            ('ro.build.version.release','15'),('ro.product.model','Test AVD'),
            ('persist.sys.locale','de-DE'),('persist.sys.timezone','Europe/Berlin'))}
        self.responses[('settings','get','global','http_proxy')]='null'
        for package in diag.PACKAGES.values():self.responses[('pm','list','packages',package)]='package:'+package
        self.adb=SimpleNamespace(sh=Mock(side_effect=lambda *args,**kw:self.responses[args]))
        self.session=SimpleNamespace(home=self.home,name=self.name,adb=self.adb,bridge=object(),
            stop=lambda:self.stopped,check=lambda:check_cancelled(lambda:self.stopped))

    def test_records_actual_properties_and_only_package_presence(self):
        result=diag.capture(self.session)
        self.assertEqual(result['values']['sdk'],'35')
        self.assertTrue(result['packages']['tiktok'])
        self.assertEqual(result['external_connection'],'not_tested')
        self.assertNotIn('PRIVATE_PATH',json.dumps(result))
        self.assertTrue(all(c.args[0] in ('getprop','settings','pm') for c in self.adb.sh.call_args_list))
        self.assertTrue(all(0<c.kwargs['timeout']<=(5 if c.args[0]=='pm' else 2)
                            for c in self.adb.sh.call_args_list))

    def test_emulator_transport_is_distinct_from_android_system_proxy(self):
        result=diag.capture(self.session)
        self.assertTrue(result['transport_proxy_configured'])
        self.assertEqual(result['system_proxy'],'unset')
        self.responses[('settings','get','global','http_proxy')]='http://PRIVATE_PASSWORD@PRIVATE_HOST:123'
        result=diag.capture(self.session)
        self.assertEqual(result['system_proxy'],'configured')
        self.assertNotIn('PRIVATE_',json.dumps(result))

    def test_missing_app_is_not_a_successful_registration(self):
        self.responses[('pm','list','packages',diag.PACKAGES['tiktok'])]=''
        result=diag.capture(self.session)
        self.assertIs(result['packages']['tiktok'],False)
        self.assertNotIn('account_created',result)

    def test_similar_package_is_not_the_requested_tiktok_app(self):
        self.responses[('pm','list','packages',diag.PACKAGES['tiktok'])]='package:'+diag.PACKAGES['tiktok']+'.other'
        self.assertIs(diag.capture(self.session)['packages']['tiktok'],False)

    def test_malformed_package_list_is_unknown_and_raw_text_is_not_kept(self):
        self.responses[('pm','list','packages',diag.PACKAGES['tiktok'])]='PRIVATE_ERROR_TEXT'
        result=diag.capture(self.session)
        self.assertIsNone(result['packages']['tiktok'])
        self.assertNotIn('PRIVATE_ERROR_TEXT',json.dumps(result))

    def test_read_errors_are_unknown_and_do_not_include_library_details(self):
        self.adb.sh.side_effect=RuntimeError('PRIVATE_ERROR')
        result=diag.capture(self.session)
        self.assertIsNone(result['packages']['tiktok'])
        self.assertTrue(result['unavailable'])
        self.assertNotIn('PRIVATE_ERROR',json.dumps(result))

    def test_cancel_before_read_issues_no_command(self):
        self.stopped=True
        with self.assertRaisesRegex(FinalError,'Abgebrochen'):diag.capture(self.session)
        self.adb.sh.assert_not_called()

    def test_cancel_during_read_stops_instead_of_becoming_unknown(self):
        def cancel(*args,**kwargs):self.stopped=True;return '1'
        self.adb.sh.side_effect=cancel
        with self.assertRaisesRegex(FinalError,'Abgebrochen'):diag.capture(self.session)
        self.assertEqual(self.adb.sh.call_count,1)

    def test_budget_prevents_further_reads_after_first_slow_command(self):
        clock=[0.0]
        def slow(*args,**kwargs):clock[0]+=8;return '1'
        self.adb.sh.side_effect=slow
        with patch.object(diag.time,'monotonic',side_effect=lambda:clock[0]):
            result=diag.capture(self.session,budget=8)
        self.assertEqual(self.adb.sh.call_count,1)
        self.assertIsNone(result['packages']['tiktok'])
        self.assertEqual(result['unavailable_reason']['tiktok'],'budget')

    def test_default_budget_covers_ten_slow_but_valid_reads(self):
        clock=[0.0]
        def read(*args,**kwargs):clock[0]+=1.5;return self.responses[args]
        self.adb.sh.side_effect=read
        with patch.object(diag.time,'monotonic',side_effect=lambda:clock[0]):
            result=diag.capture(self.session)
        self.assertEqual(self.adb.sh.call_count,10)
        self.assertTrue(result['packages']['tiktok'])
        self.assertEqual(result['unavailable'],[])

    def test_timeout_reason_is_sanitized_and_distinct_from_missing_app(self):
        self.adb.sh.side_effect=RuntimeError('adb shell hat zu lange gebraucht. PRIVATE_DETAIL')
        result=diag.observe(self.session)
        self.assertEqual(result['unavailable_reason']['tiktok'],'timeout')
        text=diag.summary(self.home,self.name)
        self.assertIn('Abfragezeitlimit erreicht',text)
        self.assertNotIn('PRIVATE_DETAIL',text)

    def test_storage_and_summary_have_date_and_no_live_network_claim(self):
        diag.observe(self.session)
        text=diag.summary(self.home,self.name)
        self.assertIn('Android gemessen:',text)
        self.assertIn('kein Live-Status',text)
        self.assertIn('externe Verbindung und Kontoanmeldung nicht geprüft',text)
        self.assertNotIn('PRIVATE_PATH',text)

    def test_changed_config_cannot_reuse_an_older_report(self):
        diag.observe(self.session)
        (self.home/(self.name+'.avd')/'config.ini').write_text('changed')
        self.assertIn('älteren Profilkonfiguration',diag.summary(self.home,self.name))

    def test_missing_corrupt_and_redirected_data_are_reported_safely(self):
        self.assertIn('Noch keine',diag.summary(self.home,self.name))
        diag.observe(self.session)
        (self.home/'_diagnostics'/(self.name+'.json')).write_text('invalid json')
        self.assertIn('nicht lesbar',diag.summary(self.home,self.name))
        with patch.object(Path,'is_junction',return_value=True):
            with self.assertRaises(FinalError):diag.observe(self.session)

    def test_report_for_orphan_avd_is_visible_in_inventory(self):
        diag.observe(self.session)
        rows,_=inventory(self.home.parent/'profiles',self.home.parent/'missing.db',self.home)
        self.assertEqual(len(rows),1)
        self.assertIn('Android gemessen',rows[0].diagnostics)
        self.assertEqual(rows[0].identity,'Nicht zugeordnet')

    def test_large_diagnosis_uses_scrollable_dialog_instead_of_expanding_table_footer(self):
        root=tk.Tk();root.withdraw();self.addCleanup(root.destroy)
        tab=ProfilesTab(SimpleNamespace(nb=root))
        row=ProfileRow('fixture','Android','','','',self.name,'',diagnostics='Line\n'*50)
        key=tab.table.insert('','end');tab.rows[key]=row;tab.table.selection_set(key)
        tab.selected()
        self.assertNotIn('Line',tab.detail.cget('text'))
        dialog=tab.show_diagnostics()
        self.assertEqual(dialog.report.get('1.0','end-1c'),row.diagnostics)
        self.assertEqual(dialog.report.cget('state'),'disabled')
        dialog.destroy()
