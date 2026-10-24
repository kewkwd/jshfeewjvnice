import json, os, tempfile, unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from socialscraper.android import nox, google_mail
from socialscraper.net.errors import FinalError
from socialscraper.android.render_host import RenderHost
from socialscraper.android import sdk
from test_android_robustness import png as fixture_png

@unittest.skipUnless(os.name=='nt','native HWND helper requires Windows')
class NativeRenderSurface(unittest.TestCase):
    def test_actual_owned_hidden_surface_starts_and_removes_only_its_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            host=RenderHost(folder)
            try:
                self.assertGreater(host.start(),0)
                self.assertIsNone(host.process.poll())
                self.assertTrue((host.directory/'ready.json').is_file())
                dimensions=json.loads((host.directory/'ready.json').read_text(encoding='utf8'))
                self.assertEqual((dimensions['width'],dimensions['height']),(720,1280))
            finally:host.close()
            self.assertIsNotNone(host.process.poll())
            self.assertTrue(Path(folder).is_dir())
            self.assertFalse(host.directory.exists())

    def test_cancelled_surface_does_not_create_child_process(self):
        with tempfile.TemporaryDirectory() as folder:
            host=RenderHost(folder,lambda:True)
            try:
                with patch('socialscraper.android.render_host.subprocess.Popen') as child:
                    with self.assertRaises(FinalError):host.start()
                child.assert_not_called()
            finally:host.close()


class AndroidContainment(unittest.TestCase):
    def test_existing_guard_reuse_does_not_launch_an_elevation_prompt(self):
        from socialscraper.android import retained_guard
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)/'android-sessions';owner=Path(directory)/'nox-console-device/owned.json'
            owner.parent.mkdir();owner.write_text(json.dumps({'name':'Nox_1','sdk_title':'Socialscraper_ProxyConsole','fresh_sdk_base':True}))
            state={'worker_pid':os.getpid()};file=folder/'reused.json'
            with patch.dict(os.environ,{},clear=True),patch.object(nox,'info',return_value={'running_vms':''}),patch.object(retained_guard,'reuse',return_value=(file,state)),patch.object(nox.subprocess,'Popen') as elevate:
                self.assertEqual(nox.request_guard(folder,lambda _:None,vm_name='Nox_1',ownership_file=owner),state)
                self.assertEqual(os.environ['SS_ANDROID_GUARD'],str(file));elevate.assert_not_called()

    def test_retained_guard_transfer_requires_effective_rules_and_ended_processes(self):
        from socialscraper.android import retained_guard
        for mode in ('valid','live','missing_rules','foreign_path','wrong_profile'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as directory:
                folder=Path(directory)/'android-sessions';folder.mkdir()
                owner=Path(directory)/'nox-console-device/owned.json'
                file=folder/('guard-'+'a'*32+'.json');hold=Path(str(file)+'.hold')
                state={'group':'SocialscraperProxy_'+'b'*32,'vm_name':'Nox_1','proxy_only':True,'worker_pid':555,
                       'retain_file':str(hold),'release_file':str(file)+'.release'}
                if mode=='foreign_path':state['retain_file']=str(Path(directory)/'foreign.hold')
                original=json.dumps(state);file.write_text(original);hold.write_text('pending')
                Path(str(file)+'.release').write_text('release')
                Path(str(file)+'.error').write_text(json.dumps({'group':state['group'],'rules_retained':True}))
                Path(str(file)+'.launch.json').write_text(json.dumps({'started':True,'pid':556}))
                fake=SimpleNamespace(name='Nox_3' if mode=='wrong_profile' else 'Nox_1')
                with patch.object(retained_guard,'inactive',return_value=mode!='live'),patch.object(sdk,'Profile',return_value=fake),patch.object(nox,'info',return_value={'running_vms':''}),patch.object(nox,'verify_guard',side_effect=FinalError('missing') if mode=='missing_rules' else None) as verify:
                    if mode in ('missing_rules','wrong_profile'):
                        with self.assertRaises(FinalError):retained_guard.reuse(folder,'Nox_1',owner)
                        result=None
                    else:result=retained_guard.reuse(folder,'Nox_1',owner)
                self.assertTrue(hold.exists())
                if mode=='valid':
                    self.assertEqual(result[0],file);verify.assert_called_once()
                    actual=json.loads(file.read_text());self.assertEqual(actual['worker_pid'],os.getpid())
                    self.assertEqual(actual['previous_worker_pid'],555);self.assertTrue(actual['reused_retained_guard'])
                else:self.assertIsNone(result);self.assertEqual(file.read_text(),original)

    def test_sdk_start_error_is_preserved_when_cleanup_retains_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            state=Path(directory)/'guard.json'
            state.write_text(json.dumps({'worker_pid':os.getpid(),'vm_name':'Nox_1','retain_file':str(Path(directory)/'hold')}))
            profile=MagicMock();profile.name='Nox_1'
            profile.action.side_effect=FinalError('Ursprünglicher Starterfehler')
            with patch.dict(os.environ,{'SS_ANDROID_GUARD':str(state)}),patch.object(nox,'verify_guard'),patch.object(nox,'info',return_value={'running_vms':''}),patch.object(sdk,'network'),patch.object(sdk.Session,'__exit__',side_effect=FinalError('Netzwerksperre bleibt erhalten')):
                with self.assertRaisesRegex(FinalError,'Ursprünglicher Starterfehler.*Netzwerksperre bleibt erhalten'):
                    sdk.Session(profile,MagicMock()).__enter__()
            self.assertTrue((Path(directory)/'hold').exists())

    def test_sdk_session_missing_proxy_or_guard_does_not_launch(self):
        profile=MagicMock();profile.name='Nox_1'
        with patch.dict(os.environ,{},clear=True):
            for proxy in (None,MagicMock()):
                with self.assertRaises(FinalError):
                    with sdk.Session(profile,proxy):pass
        profile.action.assert_not_called()

    def test_sdk_owner_record_rejects_a_previous_user_vm(self):
        with tempfile.TemporaryDirectory() as folder:
            marker=Path(folder)/'owned.json'
            marker.write_text(json.dumps({'name':'Nox_3','sdk_title':'Socialscraper_ProxyConsole','fresh_sdk_base':True,'phase':'prepared',
                'uuid':'previous-user-vm','prior_vm_uuids':['previous-user-vm'],'config':str(Path(folder)/'Nox_3.vbox')}))
            with patch.object(sdk,'console') as console,patch.object(nox,'command') as manager:
                with self.assertRaisesRegex(FinalError,'anderes Gerät'):sdk.Profile(folder)
                console.assert_not_called();manager.assert_not_called()

    def test_sdk_shutdown_keeps_hold_if_no_real_start_was_observed(self):
        for observed,reused in ((False,False),(True,False),(True,True)):
            with tempfile.TemporaryDirectory() as folder:
                hold=Path(folder)/'hold';hold.write_text('pending');release=Path(folder)/'release'
                profile=MagicMock();profile.name='Nox_1'
                session=sdk.Session(profile,MagicMock());session.launched=True;session.observed=observed
                session.lease={'retain_file':str(hold),'release_file':str(release),'reused_retained_guard':reused};session.bridge=MagicMock()
                def stop(**kwargs):session.bridge.close.assert_not_called()
                with patch.object(nox,'command',return_value='VMState="poweroff"'),patch.object(nox,'stop',side_effect=stop),patch.object(nox,'info',return_value={'running_vms':''}),patch.object(sdk.subprocess,'run',return_value=SimpleNamespace(returncode=0)):
                    if observed:session.__exit__(None,None,None)
                    else:
                        with self.assertRaisesRegex(FinalError,'sperre bleibt'):session.__exit__(None,None,None)
                self.assertEqual(hold.exists(),not observed or reused)
                self.assertTrue(release.exists());session.bridge.close.assert_called_once()

    def test_official_local_control_ports_and_abbreviated_nat_are_validated(self):
        from socialscraper.android.sdk_network import read
        xml='<VirtualBox xmlns="urn:test"><Adapter slot="0" enabled="true"><NAT network="172.16.3/24"><DNS pass-domain="false"/><Forwarding name="port1" proto="1" hostip="127.0.0.1" hostport="61024" guestport="18011"/><Forwarding name="port2" proto="1" hostip="127.0.0.1" hostport="62025" guestport="5555"/></NAT></Adapter><Adapter slot="1" enabled="true"><NAT network="172.16.4/24"><DNS pass-domain="false"/></NAT></Adapter></VirtualBox>'
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'sdk.vbox';path.write_text(xml)
            self.assertEqual(read(path),('127.0.0.1:62025','172.16.4.2'))
            for bad in (xml.replace('127.0.0.1','0.0.0.0'),xml.replace('pass-domain="false"','use-host-resolver="true"'),xml.replace('172.16.4/24','192.168.1/24'),xml.replace('<NAT network="172.16.3/24">','<BridgedInterface/><NAT network="172.16.3/24">'),xml.replace('proto="1" hostip="127.0.0.1" hostport="62025"','proto="0" hostip="127.0.0.1" hostport="62025"')):
                path.write_text(bad)
                with self.assertRaises(RuntimeError):read(path)
    def test_user_profile_name_cannot_request_a_guard(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(nox.subprocess,'Popen') as launch:
            with self.assertRaisesRegex(FinalError,'Unbekanntes Android-Testgerät'):
                nox.request_guard(folder,lambda _:None,vm_name='nox')
            launch.assert_not_called()

    def test_numbered_sdk_profile_without_owned_record_cannot_request_guard(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(nox.subprocess,'Popen') as launch:
            for ownership in (None,Path(folder)/'foreign.json'):
                with self.assertRaisesRegex(FinalError,'Unbekanntes Android-Testgerät'):
                    nox.request_guard(Path(folder)/'android-sessions',lambda _:None,vm_name='Nox_3',ownership_file=ownership)
            launch.assert_not_called()
    def test_language_is_saved_only_for_owned_named_device_and_rejects_failed_readback(self):
        properties={}
        def manager(*args,**kwargs):
            self.assertEqual(args[2],nox.NAME)
            if args[1]=='set':properties[args[3]]=args[4];return ''
            return 'Value: '+properties.get(args[3],'')
        template=ET.ElementTree(ET.fromstring('<VirtualBox xmlns="urn:test"><GuestProperty name="nox_host_language" value="zh"/><GuestProperty name="nox_host_country" value="CN"/></VirtualBox>'))
        with patch.object(nox.ET,'parse',return_value=template),patch.object(nox,'command',side_effect=manager):nox.configure_language()
        self.assertEqual(properties,{'nox_host_language':'zh','nox_host_country':'CN'})
        with patch.object(nox.ET,'parse',return_value=template),patch.object(nox,'command',return_value='Value: unknown'):
            with self.assertRaisesRegex(FinalError,'Sprach'):nox.configure_language()

    def test_unowned_vm_cannot_have_language_changed(self):
        with tempfile.TemporaryDirectory() as folder:
            state=Path(folder)/'guard.json';state.write_text(json.dumps({'worker_pid':os.getpid(),'release_file':str(Path(folder)/'release')}))
            with patch.dict(os.environ,{'SS_ANDROID_GUARD':str(state)}),patch.object(nox,'info',return_value={'running_vms':''}),patch.object(nox,'verify_guard'),patch.object(nox,'prepare',side_effect=FinalError('unowned')),patch.object(nox,'configure_language') as language:
                with self.assertRaisesRegex(FinalError,'unowned'):
                    with nox.Session(folder,MagicMock()):pass
                language.assert_not_called()

    def test_cancelled_guard_does_not_open_elevation_prompt(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(nox.subprocess,'Popen') as launch:
            with self.assertRaisesRegex(FinalError,'Abgebrochen'):
                nox.request_guard(folder,lambda _:None,lambda:True)
            launch.assert_not_called()

    def test_uac_launch_failure_is_reported_without_waiting_for_timeout(self):
        for code,expected in ((1223,'geschlossen oder abgelehnt'),(5,'konnte die Administratorabfrage nicht starten')):
            with tempfile.TemporaryDirectory() as folder,patch.object(nox,'info',return_value={'running_vms':''}):
                def launch(*args,**kwargs):
                    state=Path(folder)/'guard-fixed.json'
                    Path(str(state)+'.launch.json').write_text(json.dumps({'started':False,'native_code':code,'error':'Zugriff verweigert'}),encoding='utf8')
                    return SimpleNamespace(poll=lambda:1)
                with patch.object(nox.uuid,'uuid4',return_value=SimpleNamespace(hex='fixed')),patch.object(nox.subprocess,'Popen',side_effect=launch),patch.object(nox.time,'sleep') as sleep:
                    with self.assertRaisesRegex(FinalError,expected):nox.request_guard(folder,lambda _:None)
                    sleep.assert_not_called()
                    self.assertTrue((Path(folder)/'guard-fixed.json.release').is_file())

    def test_google_authenticator_is_not_an_existing_account(self):
        raw='Accounts: 0\nServiceInfo: AuthenticatorDescription {type=com.google}\n'
        self.assertEqual(nox.google_accounts(raw),[])
        self.assertEqual(nox.google_accounts('Accounts: 1\n  Account {name=test@gmail.com, type=com.google}\n'),['test@gmail.com'])
        self.assertEqual(nox.google_accounts('Account {name=test@gmail.com, type=com.google.other}'),[])
        self.assertFalse(google_mail.account_present(raw,'test@gmail.com'))

    def test_failed_native_frontend_stops_before_render_surface_and_lease_release(self):
        with tempfile.TemporaryDirectory() as folder:
            session=nox.Session(folder,MagicMock());session.started=True
            session.renderer=MagicMock();session.frontend=MagicMock();session.frontend_log=MagicMock()
            release=Path(folder)/'release';session.lease={'release_file':str(release)}
            def stop():
                self.assertFalse(release.exists());session.renderer.close.assert_not_called()
                session.frontend.terminate.assert_not_called()
            with patch.object(nox,'stop',side_effect=stop):session.__exit__(None,None,None)
            session.renderer.close.assert_called_once();self.assertTrue(release.exists())

    def test_repeated_configuration_replaces_only_its_own_adb_rule(self):
        rules={'ss_adb':'ss_adb,tcp,127.0.0.1,62997,,5555'}
        def manager(*args,**kwargs):
            if args[0]=='showvminfo':return 'Forwarding(0)="'+rules['ss_adb']+'"' if rules else ''
            if '--natpf1' in args:
                rest=args[args.index('--natpf1')+1:]
                if rest[0]=='delete':rules.pop(rest[1])
                else:
                    if 'ss_adb' in rules:raise FinalError('Duplicate forwarding rule')
                    rules['ss_adb']=rest[0]
            return ''
        with patch.object(nox,'command',side_effect=manager):
            for port in (62998,62999):
                nox.configure_network(port)
                self.assertEqual(rules['ss_adb'],f'ss_adb,tcp,127.0.0.1,{port},,5555')

    def test_missing_proxy_or_guard_never_prepares_or_starts_vm(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{},clear=True),patch.object(nox,'info',return_value={'running_vms':''}),patch.object(nox,'prepare') as prepare:
            for proxy in (None,MagicMock()):
                with self.assertRaises(FinalError):
                    with nox.Session(folder,proxy):pass
            prepare.assert_not_called()

    def test_other_running_vm_is_never_taken_over(self):
        with patch.object(nox,'info',return_value={'running_vms':'"personal" {uuid}'}),patch.object(nox,'prepare') as prepare:
            with self.assertRaisesRegex(FinalError,'andere Nox'):
                with nox.Session('unused',MagicMock()):pass
            prepare.assert_not_called()

    def test_unverified_os_rule_stops_before_native_launch(self):
        with tempfile.TemporaryDirectory() as folder:
            state=Path(folder)/'guard.json';state.write_text(json.dumps({'worker_pid':os.getpid()}))
            with patch.dict(os.environ,{'SS_ANDROID_GUARD':str(state)}),patch.object(nox,'info',return_value={'running_vms':''}),patch.object(nox,'verify_guard',side_effect=FinalError('not verified')),patch.object(nox,'command') as command:
                with self.assertRaises(FinalError):
                    with nox.Session(folder,MagicMock()):pass
                command.assert_not_called()

    def test_device_stops_before_bridge_and_guard_release(self):
        with tempfile.TemporaryDirectory() as folder:
            session=nox.Session(folder,MagicMock());session.started=True;session.bridge=MagicMock()
            release=Path(folder)/'release';session.lease={'release_file':str(release)}
            def stop():self.assertFalse(release.exists());session.bridge.close.assert_not_called()
            with patch.object(nox,'stop',side_effect=stop):session.__exit__(None,None,None)
            session.bridge.close.assert_called_once();self.assertTrue(release.exists())

    def test_bridging_host_dns_or_extra_forwarding_rejects_vm(self):
        good='<VirtualBox xmlns="urn:test"><Machine><Hardware><Network><Adapter slot="0" enabled="true"><NAT network="172.17.99.0/24"><DNS use-host-resolver="false" use-proxy="false"/><Forwarding name="ss_adb" hostip="127.0.0.1" guestport="5555"/></NAT></Adapter><Adapter slot="1" enabled="true"><NAT network="172.17.100.0/24"><DNS pass-domain="false"/></NAT></Adapter></Network></Hardware></Machine></VirtualBox>'
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/(nox.NAME+'.vbox')
            target.write_text(good);nox.verify_network(folder)
            target.write_text(good.replace(' use-host-resolver="false" use-proxy="false"',' pass-domain="false"'))
            nox.verify_network(folder)
            for bad in (good.replace('use-host-resolver="false"','use-host-resolver="true"'),
                        good.replace('<NAT network="172.17.99.0/24">','<BridgedInterface name="LAN"/><NAT network="172.17.99.0/24">'),
                        good.replace('hostip="127.0.0.1"','hostip="0.0.0.0"'),
                        good.replace('</Network>','<Adapter slot="2" enabled="true"/></Network>'),
                        good.replace('172.17.100.0/24','192.168.1.0/24'),
                        good.replace('<DNS pass-domain="false"/>','<DNS use-proxy="true"/>'),
                        good.replace('<DNS pass-domain="false"/>','<DNS/><Forwarding name="other" hostip="127.0.0.1"/>'),
                        good.replace('slot="1" enabled="true"','slot="1" enabled="false"')):
                target.write_text(bad)
                with self.assertRaises(FinalError):nox.verify_network(folder)

class AndroidRegistration(unittest.TestCase):
    def test_active_manual_input_extends_registration_until_account_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            now=[0.0];polls=[0]
            adb=MagicMock();adb.baum.return_value=ET.fromstring('<hierarchy/>')
            frame=fixture_png()
            def capture(*args,**kwargs):now[0]+=.3;return frame
            adb._cmd.side_effect=capture
            def shell(*args,**kwargs):
                if args[:2]==('pm','path'):return 'package:google.apk'
                if args==('dumpsys','account'):
                    polls[0]+=1
                    return '' if polls[0]==1 else 'Account {name=test@gmail.com, type=com.google}'
                return ''
            adb.sh.side_effect=shell
            session=MagicMock();session.__enter__.return_value.adb=adb
            ctx=SimpleNamespace(timeout=1,stop=None,melden=MagicMock(),hinweis=MagicMock(),vorname='Test',nachname='Person',adresse='test@gmail.com',password_filled=True)
            def event(state,folder):
                if state=='open':
                    self.assertEqual(google_mail.screen_size((folder/'frame.png').read_bytes()),(100,100))
                    (folder/'inputs/command.json').write_text(json.dumps({'action':'key','key':'66'}))
            def sleep(_):now[0]=1.4  # Beyond initial deadline, within renewed one.
            cfg=SimpleNamespace(profiles_dir=str(Path(directory)/'profiles'))
            with patch.dict(os.environ,{},clear=True),patch.object(google_mail,'Profile'),patch.object(google_mail,'Session',return_value=session),patch.object(google_mail,'request_guard'),patch.object(google_mail,'event',side_effect=event),patch('socialscraper.android.language.ensure_german'),patch.object(google_mail.time,'monotonic',side_effect=lambda:now[0]),patch.object(google_mail.time,'sleep',side_effect=sleep):
                result=google_mail.run(cfg,ctx,MagicMock())
            self.assertEqual(result['adresse'],'test@gmail.com');self.assertEqual(polls[0],2)
            adb.taste.assert_called_once_with('66');session.__exit__.assert_called_once()

    def test_unsupported_form_reports_no_change_instead_of_silent_fill(self):
        adb=MagicMock();adb.baum.return_value=ET.fromstring('<hierarchy><node resource-id="unknown"/></hierarchy>')
        ctx=SimpleNamespace(hinweis=MagicMock(),password_filled=False,vorname='Test',nachname='Person',adresse='test@gmail.com',passwort='FAKE')
        google_mail.apply_input(adb,{'action':'fill'},100,100,ctx)
        ctx.hinweis.assert_called_once();adb.tap.assert_not_called();adb.tippen.assert_not_called()
        self.assertFalse(ctx.password_filled)

    def test_unavailable_google_field_tree_does_not_abort_manual_form(self):
        from socialscraper.android.adb import AdbError
        adb=MagicMock();adb.baum.side_effect=AdbError('No hierarchy')
        ctx=SimpleNamespace(hinweis=MagicMock(),password_filled=False,vorname='Test',nachname='Person',adresse='test@gmail.com',passwort='FAKE')
        google_mail.apply_input(adb,{'action':'fill'},1600,900,ctx)
        ctx.hinweis.assert_called_once();self.assertFalse(ctx.password_filled);adb.tippen.assert_not_called()
    def test_german_must_be_primary_active_locale_not_a_recent_configuration(self):
        from socialscraper.android.language import german_config
        self.assertTrue(german_config('config: mcc262-mnc2-de-rDE-ldltr-v25'))
        self.assertTrue(german_config('config: de-rDE,zh-rCN-ldltr-v25'))
        self.assertFalse(german_config('config: zh-rCN,de-rDE-ldltr-v25'))
        self.assertFalse(german_config('config: zh-rCN-ldltr-v25\nrecentConfigs:\nconfig: de-rDE-v25'))

    def test_already_german_guest_does_not_change_settings_or_click(self):
        from socialscraper.android.language import ensure_german
        adb=MagicMock();adb.sh.return_value='config: mcc262-mnc2-de-rDE-ldltr-v25'
        ensure_german(adb)
        adb.sh.assert_called_once_with('am','get-config');adb.tap.assert_not_called();adb.baum.assert_not_called()

    def test_language_setup_never_clicks_other_application(self):
        from socialscraper.android.language import ensure_german
        adb=MagicMock();adb.sh.return_value='config: zh-rCN-ldltr-v25'
        adb.baum.return_value=ET.fromstring('<hierarchy><node package="com.google.android.gms" text="Deutsch (Deutschland)"/></hierarchy>')
        with self.assertRaisesRegex(FinalError,'nicht erreichbar'):ensure_german(adb)
        adb.tap.assert_not_called()

    def test_language_setup_preserves_unexpected_additional_language(self):
        from socialscraper.android.language import ensure_german
        adb=MagicMock();adb.sh.return_value='config: zh-rCN-ldltr-v25'
        tree=ET.fromstring('<hierarchy><node package="com.android.settings" resource-id="com.android.settings:id/label" text="Deutsch (Deutschland)"/><node package="com.android.settings" resource-id="com.android.settings:id/label" text="English (United States)"/></hierarchy>')
        adb.baum.return_value=tree
        with self.assertRaisesRegex(FinalError,'keine Sprache entfernt'):ensure_german(adb)
        adb.tap.assert_not_called()

    def test_native_stage_reports_connection_error_without_copying_private_form_values(self):
        tree=ET.fromstring('<hierarchy><node package="com.google.android.gms" text="Anmeldung nicht möglich"/><node package="com.google.android.gms" class="android.widget.EditText" text="private@example.com"/></hierarchy>')
        self.assertEqual(google_mail.screen_stage(tree),'connection_error')
        with tempfile.TemporaryDirectory() as directory:
            google_mail.write_stage(Path(directory),google_mail.screen_stage(tree))
            raw=(Path(directory)/'status.json').read_text()
            self.assertNotIn('private',raw);self.assertNotIn('example.com',raw)
            self.assertEqual(json.loads(raw),{'version':1,'stage':'connection_error'})

    def test_stage_distinguishes_checking_signin_and_unknown_screen(self):
        for text,stage in (('Daten werden überprüft…','checking'),('登录','sign_in'),('Anmeldung','sign_in'),('Google-Konto erstellen','signup'),('Unbekannter Schritt','manual')):
            tree=ET.fromstring('<hierarchy><node package="com.google.android.gms"/></hierarchy>')
            tree[0].set('text',text);self.assertEqual(google_mail.screen_stage(tree),stage)
            tree[0].set('package','unrelated');self.assertEqual(google_mail.screen_stage(tree),'manual')
        form=ET.fromstring('<hierarchy><node package="com.google.android.gms" class="android.widget.EditText" text="Anmelden"/></hierarchy>')
        self.assertEqual(google_mail.screen_stage(form),'manual')
    def test_tiny_or_incomplete_screen_is_never_treated_as_interactive(self):
        png=fixture_png
        for frame in (b'',b'\x89PNG\r\n\x1a\n',png(1,1),png(720,1)):
            with self.assertRaises(FinalError):google_mail.screen_size(frame)
        self.assertEqual(google_mail.screen_size(png(720,1280)),(720,1280))

    def test_account_confirmation_rejects_other_or_similar_addresses(self):
        raw='Account {name=test@gmail.com, type=com.google}'
        self.assertTrue(google_mail.account_present(raw,'test@gmail.com'))
        self.assertFalse(google_mail.account_present(raw,'xtest@gmail.com'))
        self.assertFalse(google_mail.account_present('Account {name=test@gmail.com, type=other}','test@gmail.com'))

    def test_generic_login_field_is_not_autofilled(self):
        adb=MagicMock();adb.baum.return_value=ET.fromstring('<hierarchy><node resource-id="com.google/id/identifierId" class="android.widget.EditText" bounds="[0,0][100,100]"/></hierarchy>')
        ctx=SimpleNamespace(vorname='Test',nachname='Person',adresse='test@gmail.com',passwort='FAKE',password_filled=False)
        google_mail.fill_known(adb,ctx);adb.tippen.assert_not_called();self.assertFalse(ctx.password_filled)

    def test_signup_password_records_only_actual_program_input(self):
        adb=MagicMock();adb.baum.return_value=ET.fromstring('<hierarchy><node package="com.google.android.gms" text="Starkes Passwort erstellen"/><node package="com.google.android.gms" resource-id="com.google/id/Passwd" class="android.widget.EditText" password="true" bounds="[0,0][100,100]"/></hierarchy>')
        ctx=SimpleNamespace(vorname='Test',nachname='Person',adresse='test@gmail.com',passwort='FAKE',password_filled=False)
        google_mail.fill_known(adb,ctx);adb.tippen.assert_called_once_with('FAKE');self.assertTrue(ctx.password_filled)

    def test_input_commands_cannot_execute_shell_or_escape_screen(self):
        adb=MagicMock()
        for command in ({'action':'shell','value':'any'},{'action':'tap','x':-1,'y':0},{'action':'tap','x':True,'y':0},{'action':'key','key':'arbitrary'},{'action':'text','value':'X'*257}):
            with self.assertRaises(ValueError):google_mail.apply_input(adb,command,100,100,None)
        adb.assert_not_called();adb.sh.assert_not_called();adb.tap.assert_not_called()
        google_mail.apply_input(adb,{'action':'tap','x':99,'y':99},100,100,None);adb.tap.assert_called_once_with(99,99)

    def test_only_proxy_bound_typed_screen_events_accepted(self):
        good={'version':1,'state':'open','directory':'session-local','pid':1,'proxy':True}
        self.assertEqual(google_mail.parse(google_mail.PREFIX+json.dumps(good)),good)
        for value in ([],{},dict(good,pid=True),dict(good,proxy=False),dict(good,directory=1)):
            self.assertIsNone(google_mail.parse(google_mail.PREFIX+json.dumps(value)))

    def test_android_route_does_not_fall_back_to_browser_or_save_failed_mailbox(self):
        from socialscraper import mailanbieter
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        cfg=SimpleNamespace(db_path=str(Path(folder.name)/'data/fixture.db'),profiles_dir=str(Path(folder.name)/'data/profiles'),headless=True)
        im=MagicMock()
        with patch('socialscraper.net.proxymode.require_no_anon'),patch('socialscraper.net.proxymode.is_anonymous',return_value=False),patch('socialscraper.net.factory.build') as browser,patch.object(google_mail,'run',side_effect=FinalError('pending human')),patch('socialscraper.accountvault.add_postfach') as save:
            with self.assertRaisesRegex(FinalError,'pending human'):
                from profile_fixture import REGISTRATION
                mailanbieter.create(cfg,'gmail.com',route='android',im=im,melden=lambda _:None,registrierungsdaten=REGISTRATION)
            browser.assert_not_called();save.assert_not_called()

    def test_screen_folder_and_worker_are_validated(self):
        from socialscraper.ui.android_dialog import validate
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder)/'android-sessions';base.mkdir();screen=base/'session-test';screen.mkdir()
            app=SimpleNamespace(cfg=SimpleNamespace(profiles_dir=str(Path(folder)/'profiles')),job=SimpleNamespace(proc=SimpleNamespace(pid=123)))
            event={'pid':123,'directory':str(screen),'proxy':True}
            with patch('socialscraper.net.deadline.descendants',return_value=[]):
                self.assertTrue(validate(app,event))
                self.assertFalse(validate(app,dict(event,pid=456)))
                self.assertFalse(validate(app,dict(event,directory=folder)))

    @unittest.skipUnless(os.name=='nt' and os.environ.get('SS_RUN_EMBED_TESTS')=='1','opt-in actual Tk preview')
    def test_actual_preview_routes_taps_only_to_owned_session(self):
        import tkinter as tk
        from socialscraper.ui.android_dialog import AndroidDialog
        with tempfile.TemporaryDirectory() as folder:
            root=tk.Tk();root.withdraw();dialog=None
            try:
                root.cfg=SimpleNamespace(profiles_dir=str(Path(folder)/'profiles'))
                root.job=SimpleNamespace(proc=SimpleNamespace(pid=os.getpid()));root.cancel=MagicMock()
                session=Path(folder)/'android-sessions/session-fixture';session.mkdir(parents=True);(session/'inputs').mkdir()
                picture=tk.PhotoImage(width=200,height=300);picture.put('white',to=(0,0,200,300));picture.write(str(session/'frame.png'),format='png')
                with patch('socialscraper.net.deadline.descendants',return_value=[]):
                    dialog=AndroidDialog(root,{'directory':str(session),'pid':os.getpid(),'proxy':True});root.update()
                self.assertEqual(dialog.photo.width(),200)
                self.assertIn('disabled',dialog.confirm_button.state())
                google_mail.write_stage(session,'connection_error');dialog.refresh()
                self.assertEqual(dialog.stage.get(),google_mail.STAGES['connection_error'])
                (session/'status.json').write_text(json.dumps({'version':1,'stage':'private arbitrary text'}),encoding='utf8');dialog.refresh()
                self.assertEqual(dialog.stage.get(),google_mail.STAGES['connection_error'])
                dialog.tap(SimpleNamespace(x=50,y=60))
                files=list((session/'inputs').glob('*.json'));self.assertEqual(len(files),1)
                command=json.loads(files[0].read_text());self.assertEqual(command['size'],[200,300])
                self.assertEqual((command['action'],command['x'],command['y']),('tap',50,60))
                dialog.tap(SimpleNamespace(x=250,y=60));self.assertEqual(len(list((session/'inputs').glob('*.json'))),1)
                landscape=tk.PhotoImage(width=1600,height=900)
                landscape.put('white',to=(0,0,1600,900));landscape.write(str(session/'frame.png'),format='png')
                with patch.object(dialog,'winfo_screenwidth',return_value=1920),patch.object(dialog,'winfo_screenheight',return_value=1080):
                    dialog.refresh()
                self.assertEqual((dialog.photo.width(),dialog.photo.height()),(800,450))
                dialog.tap(SimpleNamespace(x=300,y=200))
                latest=sorted((session/'inputs').glob('*.json'))[-1]
                command=json.loads(latest.read_text());self.assertEqual(command['size'],[1600,900])
                self.assertEqual((command['action'],command['x'],command['y']),('tap',600,400))
                google_mail.write_stage(session,'credentials_required',False);dialog.refresh()
                self.assertNotIn('disabled',dialog.confirm_button.state())
                with patch('socialscraper.ui.android_dialog.simpledialog.askstring',return_value='LOCAL_FAKE_SECRET') as ask:
                    dialog.confirm_button.invoke()
                self.assertEqual(ask.call_args.kwargs['show'],'*')
                latest=sorted((session/'inputs').glob('*.json'))[-1];command=json.loads(latest.read_text())
                self.assertEqual(command['action'],'confirm_password');self.assertEqual(command['value'],'LOCAL_FAKE_SECRET')
                self.assertNotIn('LOCAL_FAKE_SECRET',(session/'status.json').read_text())
            finally:
                if dialog:dialog.destroy()
                root.destroy()
