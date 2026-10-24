"""Offline failure scenarios: no emulator, browser or desktop window is opened."""
import json,os,struct,tempfile,unittest,zlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock,patch
import xml.etree.ElementTree as ET
from socialscraper.android.adb import Adb,AdbError
from socialscraper.android import google_mail,nox,sdk
from socialscraper.net.errors import FinalError

def png(width=100,height=100):
    def chunk(kind,data):
        return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    return (b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,2,0,0,0))+
            chunk(b'IDAT',zlib.compress((b'\0'+b'\0'*(width*3))*height))+chunk(b'IEND',b''))

class AdbContracts(unittest.TestCase):
    def test_cancelled_tree_probe_still_removes_its_own_dump(self):
        checks=[False,True];adb=Adb(stop_requested=lambda:checks.pop(0))
        with patch('socialscraper.android.adb.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout=b'',stderr=b'')) as run:
            with self.assertRaisesRegex(FinalError,'Abgebrochen'):adb.baum()
        self.assertEqual(run.call_count,2)
        dumped=run.call_args_list[0].args[0][-1]
        self.assertEqual(run.call_args_list[1].args[0][-3:],['rm','-f',dumped])
        self.assertEqual(run.call_args_list[1].kwargs['timeout'],2)

    def test_cancel_during_typing_stops_before_the_next_character(self):
        checks=[False,True];adb=Adb(stop_requested=lambda:checks.pop(0))
        result=SimpleNamespace(returncode=0,stdout=b'',stderr=b'')
        with patch('socialscraper.android.adb.subprocess.run',return_value=result) as run:
            with self.assertRaisesRegex(FinalError,'Abgebrochen'):adb.tippen('secret-value')
        self.assertEqual(run.call_count,1)

    def test_binary_failure_is_not_a_successful_screenshot(self):
        result=SimpleNamespace(returncode=137,stdout=b'partial',stderr=b'private-password')
        with patch('socialscraper.android.adb.subprocess.run',return_value=result):
            with self.assertRaises(AdbError) as caught:Adb()._cmd('exec-out','screencap','-p',roh=True)
        self.assertIn('137',str(caught.exception));self.assertNotIn('private-password',str(caught.exception))

    def test_command_error_does_not_echo_form_or_password_values(self):
        result=SimpleNamespace(returncode=1,stdout=b'',stderr=b'private-password private@example.com')
        with patch('socialscraper.android.adb.subprocess.run',return_value=result):
            with self.assertRaises(AdbError) as caught:Adb().sh('input','text','private-password')
        self.assertNotIn('private-password',str(caught.exception));self.assertNotIn('private@example.com',str(caught.exception))

    def test_invalid_tree_is_an_error_instead_of_a_valid_empty_page(self):
        for raw in ('','<hierarchy>','<unrelated/>'):
            with self.subTest(raw=raw):
                adb=Adb()
                with patch.object(adb,'sh',side_effect=lambda *a,**k:raw if a[0]=='cat' else ''):
                    with self.assertRaises(AdbError):adb.baum()

    def test_tree_reads_are_bounded_and_cannot_reuse_another_dump(self):
        adb=Adb();calls=[]
        def shell(*args,**kwargs):
            calls.append((args,kwargs))
            return '<hierarchy><node/></hierarchy>' if args[0]=='cat' else ''
        with patch.object(adb,'sh',side_effect=shell):
            adb.baum();adb.baum()
        dumps=[(a,k) for a,k in calls if a[0]=='uiautomator']
        self.assertNotEqual(dumps[0][0][-1],dumps[1][0][-1])
        self.assertTrue(all(k.get('timeout',90)<=8 for _,k in dumps))

class PreviewContracts(unittest.TestCase):
    def test_failed_manual_edit_or_deletion_invalidates_credential_proof(self):
        for command in ({'action':'text','value':'FAKE'},{'action':'key','key':'67'}):
            with self.subTest(command=command):
                adb=MagicMock();adb.tippen.side_effect=AdbError('partial input');adb.taste.side_effect=AdbError('partial input')
                ctx=SimpleNamespace(password_filled=True)
                with self.assertRaises(AdbError):google_mail.apply_input(adb,command,100,100,ctx)
                self.assertFalse(ctx.password_filled)

    def test_incomplete_initial_picture_never_opens_an_interactive_window(self):
        with tempfile.TemporaryDirectory() as directory:
            adb=MagicMock();adb.sh.return_value='package:google.apk';adb._cmd.return_value=png()[:24]
            session=MagicMock();session.__enter__.return_value.adb=adb
            ctx=SimpleNamespace(stop=None,melden=MagicMock(),vorname='Test',nachname='Person',adresse='test@gmail.com')
            cfg=SimpleNamespace(profiles_dir=str(Path(directory)/'profiles'))
            with patch.dict(os.environ,{},clear=True),patch.object(google_mail,'Profile'),patch.object(google_mail,'Session',return_value=session),patch.object(google_mail,'request_guard'),patch.object(google_mail,'event') as event,patch('socialscraper.android.language.ensure_german'):
                with self.assertRaises(FinalError):google_mail.run(cfg,ctx,MagicMock())
            event.assert_not_called();session.__exit__.assert_called_once()

    def test_failed_password_confirmation_never_proves_the_saved_credentials(self):
        tree=ET.fromstring('<hierarchy><node package="com.google.android.gms" text="Starkes Passwort erstellen"/>'+''.join(
            '<node package="com.google.android.gms" class="android.widget.EditText" password="true" resource-id="com.google/id/'+name+'" bounds="[0,0][100,100]"/>'
            for name in ('Passwd','PasswdAgain'))+'</hierarchy>')
        adb=MagicMock();adb.baum.return_value=tree;adb.tippen.side_effect=[None,AdbError('transport failed')]
        ctx=SimpleNamespace(vorname='Test',nachname='Person',adresse='test@gmail.com',passwort='FAKE',password_filled=False)
        with self.assertRaises(AdbError):google_mail.fill_known(adb,ctx)
        self.assertFalse(ctx.password_filled)

    def test_bad_input_is_discarded_and_following_valid_input_still_runs(self):
        from socialscraper.android.preview import consume_inputs
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            samples=['[1,2]','not JSON',json.dumps({'action':'text','value':'private-é'}),json.dumps({'action':'key','key':'66'})]
            for index,raw in enumerate(samples):(folder/f'{index}.json').write_text(raw,encoding='utf8')
            adb=MagicMock();notify=MagicMock();ctx=SimpleNamespace(password_filled=False)
            self.assertEqual(consume_inputs(folder,lambda c:google_mail.apply_input(adb,c,100,100,ctx),notify),1)
            adb.taste.assert_called_once_with('66');adb.tippen.assert_not_called()
            self.assertEqual(notify.call_count,3);self.assertFalse(list(folder.glob('*.json')))
            self.assertNotIn('private',str(notify.call_args_list))

    def test_stale_or_rotated_preview_cannot_send_a_tap(self):
        adb=MagicMock()
        with patch.object(google_mail.time,'monotonic',return_value=100):
            for extra in ({'size':[200,100]},{'seen_at':80},{'seen_at':float('nan')},{'seen_at':True},{'seen_at':10**1000}):
                with self.assertRaises(ValueError):google_mail.apply_input(adb,{'action':'tap','x':1,'y':1,**extra},100,100,None)
            adb.tap.assert_not_called()
            google_mail.apply_input(adb,{'action':'tap','x':1,'y':1,'size':[100,100],'seen_at':99},100,100,None)
        adb.tap.assert_called_once_with(1,1)

    def test_next_field_geometry_is_reobserved_after_focus_changes(self):
        adb=MagicMock()
        prefix='<hierarchy><node package="com.google.android.gms" resource-id="com.google/id/firstName" class="android.widget.EditText" bounds="[0,0][100,100]"/>'
        def tree(bounds):return ET.fromstring(prefix+'<node package="com.google.android.gms" resource-id="com.google/id/lastName" class="android.widget.EditText" bounds="'+bounds+'"/></hierarchy>')
        adb.baum.side_effect=[tree('[0,200][100,300]'),tree('[0,110][100,160]')]
        ctx=SimpleNamespace(vorname='Test',nachname='Person',adresse='test@gmail.com',passwort='FAKE',password_filled=False)
        self.assertEqual(google_mail.fill_known(adb,ctx),2)
        self.assertEqual([c.args for c in adb.tap.call_args_list],[(50,50),(50,135)])

    def test_unreadable_tree_and_opaque_form_have_distinct_statuses(self):
        adb=MagicMock();adb.baum.side_effect=AdbError('killed')
        self.assertEqual(google_mail.observe_form(adb),('tree_unavailable',False))
        adb.baum.side_effect=None;adb.baum.return_value=ET.fromstring('<hierarchy><node package="com.google.android.gms" class="android.webkit.WebView"/></hierarchy>')
        self.assertEqual(google_mail.observe_form(adb),('form_unavailable',False))
        adb.baum.side_effect=RuntimeError('programming error')
        with self.assertRaises(RuntimeError):google_mail.observe_form(adb)

    def test_truncated_or_corrupt_png_does_not_leave_a_usable_preview(self):
        valid=png();self.assertEqual(google_mail.screen_size(valid),(100,100))
        corrupt=bytearray(valid);corrupt[29]^=1
        for raw in (valid[:24],valid[:-5],bytes(corrupt)):
            with self.subTest(length=len(raw)),self.assertRaises(FinalError):google_mail.screen_size(raw)

    def test_login_password_is_not_mistaken_for_signup_password(self):
        adb=MagicMock();adb.baum.return_value=ET.fromstring('<hierarchy><node package="com.google.android.gms" text="Anmeldung"/><node package="com.google.android.gms" resource-id="com.google/id/Passwd" class="android.widget.EditText" password="true" bounds="[0,0][100,100]"/></hierarchy>')
        ctx=SimpleNamespace(vorname='Test',nachname='Person',adresse='test@gmail.com',passwort='FAKE',password_filled=False)
        google_mail.fill_known(adb,ctx)
        adb.tap.assert_not_called();adb.tippen.assert_not_called();self.assertFalse(ctx.password_filled)

class ShutdownContracts(unittest.TestCase):
    def test_temporary_adb_connection_failure_is_retried_before_opening_proxy(self):
        with tempfile.TemporaryDirectory() as directory:
            state=Path(directory)/'guard.json'
            state.write_text(json.dumps({'worker_pid':os.getpid(),'vm_name':'Nox_1','retain_file':str(Path(directory)/'hold'),'release_file':str(Path(directory)/'release')}))
            profile=MagicMock();profile.name='Nox_1';controller=MagicMock();device=MagicMock()
            controller._cmd.side_effect=[AdbError('temporary transport failure'),'connected']
            def shell(*args,**kwargs):
                if args==('getprop','sys.boot_completed'):return '1'
                if args==('am','get-config'):return 'config: de-rDE'
                if args==('settings','get','global','http_proxy'):return '172.16.4.2:8765'
                return ''
            device.sh.side_effect=shell
            bridge=MagicMock();bridge.server.server_address=('127.0.0.1',8765)
            with patch.dict(os.environ,{'SS_ANDROID_GUARD':str(state)}),patch.object(nox,'verify_guard'),patch.object(nox,'info',return_value={'running_vms':''}),patch.object(nox,'command',return_value='VMState="running"'),patch.object(sdk,'network',return_value=('127.0.0.1:62025','172.16.4.2')),patch.object(sdk,'Adb',side_effect=lambda *a,**k:device if a else controller),patch.object(sdk.time,'sleep'),patch('socialscraper.net.browserproxy.BrowserProxy',return_value=bridge),patch.object(nox,'stop'),patch.object(sdk.subprocess,'run',return_value=SimpleNamespace(returncode=0)):
                with sdk.Session(profile,MagicMock()) as session:self.assertIs(session.adb,device)
            self.assertEqual(controller._cmd.call_count,2);bridge.close.assert_called_once()

    def test_failed_frontend_quit_still_attempts_owned_vm_shutdown(self):
        with tempfile.TemporaryDirectory() as directory:
            hold=Path(directory)/'hold';hold.write_text('pending');release=Path(directory)/'release'
            profile=MagicMock();profile.name='Nox_1';profile.action.side_effect=FinalError('Frontend quit failed')
            profile.row.return_value=['1','Nox_1','Socialscraper_ProxyConsole','0','0','100','101']
            session=sdk.Session(profile,MagicMock());session.launched=True;session.observed=True
            session.lease={'retain_file':str(hold),'release_file':str(release)};session.bridge=MagicMock()
            with patch.object(nox,'command',return_value='VMState="running"'),patch.object(nox,'stop') as stop,patch.object(nox,'info',return_value={'running_vms':''}),patch.object(sdk.subprocess,'run',return_value=SimpleNamespace(returncode=0)):
                with self.assertRaises(FinalError):session.__exit__(None,None,None)
            stop.assert_called_once();self.assertEqual(stop.call_args.kwargs['name'],'Nox_1')
            self.assertGreater(stop.call_args.kwargs['timeout'],0);self.assertLessEqual(stop.call_args.kwargs['timeout'],20)
            self.assertTrue(hold.exists());self.assertTrue(release.exists())

    def test_bridge_failure_does_not_skip_the_guard_release_signal(self):
        with tempfile.TemporaryDirectory() as directory:
            release=Path(directory)/'release';session=sdk.Session(MagicMock(),MagicMock())
            session.lease={'release_file':str(release)};session.bridge=MagicMock()
            session.bridge.close.side_effect=RuntimeError('bridge close failed')
            with self.assertRaises(Exception):session.__exit__(None,None,None)
            self.assertTrue(release.exists())

class CancellationContracts(unittest.TestCase):
    def test_unsupported_automatic_android_registration_cannot_start_any_device(self):
        from socialscraper import mailanbieter
        with patch.object(mailanbieter.proxymode,'require_no_anon'),patch.object(google_mail,'run') as run,patch.object(mailanbieter,'_identity_manager') as identities:
            with self.assertRaisesRegex(ValueError,'assisted'):mailanbieter.create(MagicMock(),'gmail.com',route='android',mode='auto')
        run.assert_not_called();identities.assert_not_called()

    def test_mail_command_passes_the_live_cancel_file_to_registration(self):
        from socialscraper.commands import mailkonto
        with tempfile.TemporaryDirectory() as directory:
            file=Path(directory)/'cancel'
            args=SimpleNamespace(was='neu',anbieter='gmail.com',adresse=None,vorname=None,nachname=None,passwort=None,modus='assisted',handy=False,weg='android')
            def create(*a,**kw):
                self.assertFalse(kw['stop']());file.write_text('stop');self.assertTrue(kw['stop']());return {}
            with patch.dict(os.environ,{'SS_CANCEL_FILE':str(file)}),patch('socialscraper.mailanbieter.create',side_effect=create),patch('builtins.print'):
                self.assertEqual(mailkonto.run(args,MagicMock()),0)

    def test_mail_job_allows_orderly_cleanup_instead_of_killing_after_ten_seconds(self):
        from socialscraper.guicore import Job
        job=Job(['mailkonto','neu','gmail.com'],lambda _:None,lambda _:None)
        self.assertIsNotNone(job.cancel_file);clock=[0]
        job.proc=SimpleNamespace(poll=lambda:0 if clock[0]>=20 else None)
        try:
            with patch('socialscraper.guicore.time.monotonic',side_effect=lambda:clock[0]),patch('socialscraper.guicore.time.sleep',side_effect=lambda _:clock.__setitem__(0,clock[0]+5)):
                self.assertTrue(job._soft_cancel())
            self.assertEqual(clock[0],20)
        finally:job.cancel_file.unlink(missing_ok=True)

    def test_cancelled_mail_context_does_not_touch_browser_fields(self):
        from socialscraper.mailanbieter import MailCtx
        backend=MagicMock();ctx=MailCtx('gmail.com','Test','Person','test@gmail.com','FAKE','assisted','de',False,900,backend,stop=lambda:True)
        for action in (lambda:ctx.fill(['input'],'value'),lambda:ctx.click(['button']),lambda:ctx.warte(100),ctx.dismiss):
            with self.assertRaisesRegex(FinalError,'Abgebrochen'):action()
        backend.page.assert_not_called()

class PhaseContracts(unittest.TestCase):
    def test_bad_or_unknown_phase_messages_do_not_break_status_reporting(self):
        from socialscraper.android.progress import parse,PREFIX
        for value in ([],{'version':1,'phase':[],'pid':123},{'version':True,'phase':'starting','pid':123},
                      {'version':1,'phase':'password-value','pid':123},{'version':1,'phase':'starting','pid':True}):
            with self.subTest(value=value):self.assertIsNone(parse(PREFIX+json.dumps(value)))

    def test_phase_changes_require_the_current_worker_or_a_descendant(self):
        from socialscraper.ui.app import App
        from socialscraper.android.progress import PREFIX
        app=SimpleNamespace(job=SimpleNamespace(proc=SimpleNamespace(pid=123)),current_label='Postfach',current_phase=None,set_status=MagicMock())
        def line(pid):return PREFIX+json.dumps({'version':1,'phase':'booting','pid':pid})
        with patch('socialscraper.net.deadline.descendants',return_value=[(456,'worker')]):
            App.log(app,line(999));app.set_status.assert_not_called()
            App.log(app,line(456));self.assertEqual(app.current_phase,'booting');app.set_status.assert_called_once()
            app.job.proc=None;App.log(app,line(456));self.assertEqual(app.set_status.call_count,1)

if __name__=='__main__':unittest.main()
