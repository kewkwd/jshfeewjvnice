"""Assisted native Google account setup in an owned, network-contained Nox VM.

This does not suppress Google's QR, phone or CAPTCHA checks. The user completes
unrecognised fields and final confirmations in the Android preview.
"""
import json, os, re, tempfile, time, uuid, math
from pathlib import Path
from .nox import request_guard, google_accounts
from .sdk import Profile,Session
from .adb import GRENZE,AdbError
from .preview import screen_size,consume_inputs,InputRejected
from .progress import emit
from ..net.errors import FinalError

PREFIX='[SS_ANDROID] '
STAGES={
    'checking':'Google prüft die Verbindung und Kontodaten …',
    'connection_error':'Google meldet einen Kommunikationsfehler. Registrierung noch nicht bestätigt.',
    'sign_in':'Google-Anmeldung: Für ein neues Testpostfach „Konto erstellen“ wählen.',
    'signup':'Google-Kontoerstellung: Felder ausfüllen und Bestätigungen selbst übernehmen.',
    'language':'Android-Spracheinstellungen geöffnet.',
    'manual':'Aktuellen Google-Schritt im Android-Fenster prüfen.',
    'tree_unavailable':'Die Android-Felder konnten nicht gelesen werden. Im sichtbaren Fenster manuell fortfahren.',
    'form_unavailable':'Diese Google-Seite liefert keine bedienbaren Formularfelder. Angaben im Android-Fenster selbst eintragen.',
    'credentials_required':'Die Zieladresse ist als Google-Konto bestätigt. Jetzt das tatsächlich verwendete Kennwort mit „Verwendetes Kennwort bestätigen“ lokal übernehmen.',
}
GOOGLE_PACKAGES=('com.google.android.gms','com.google.android.gsf.login')
PASSWORD_TITLES={'starkes passwort erstellen','create a strong password','erstellen sie ein sicheres passwort'}

def google_nodes(tree):
    return [n for n in tree.iter('node') if n.get('package') in GOOGLE_PACKAGES]

def password_signup(nodes):
    titles={(n.get('text') or '').strip().casefold() for n in nodes if n.get('class')!='android.widget.EditText' and n.get('password')!='true'}
    if titles & {'anmeldung','anmelden','sign in','willkommen','welcome'}:return False
    return bool(titles & PASSWORD_TITLES) or any((n.get('resource-id') or '').rpartition('/')[2]=='PasswdAgain' for n in nodes)

def supported_fields(tree):
    nodes=google_nodes(tree);password_allowed=password_signup(nodes)
    return [n for n in nodes if n.get('class')=='android.widget.EditText' and
            ((n.get('resource-id') or '').rpartition('/')[2] in ('firstName','lastName','Username') or
             (password_allowed and n.get('password')=='true' and (n.get('resource-id') or '').rpartition('/')[2] in ('Passwd','PasswdAgain')))]

def observe_form(adb):
    try:tree=adb.baum(timeout=6)
    except AdbError:return 'tree_unavailable',False
    stage=screen_stage(tree);available=bool(supported_fields(tree))
    if stage=='manual' and google_nodes(tree) and not any(n.get('class')=='android.widget.EditText' for n in google_nodes(tree)):
        stage='form_unavailable'
    return stage,available

def screen_stage(nodes):
    # Persist only fixed labels, never copied form values, emails or passwords.
    google=[n for n in nodes.iter('node') if n.get('package') in ('com.google.android.gms','com.google.android.gsf.login')]
    titles={(n.get('text') or '').strip().casefold() for n in google
            if n.get('class')!='android.widget.EditText' and n.get('password')!='true'}
    if titles & {'anmeldung nicht möglich','couldn’t sign in',"couldn't sign in"} or any('problem communicating with google servers' in t for t in titles):
        return 'connection_error'
    if titles & {'daten werden überprüft…','checking info…','checking info...'}:return 'checking'
    if titles & {'anmelden','anmeldung','sign in','登录'}:return 'sign_in'
    if titles & {'google-konto erstellen','create a google account','创建 google 账号'}:return 'signup'
    if any(n.get('resource-id')=='com.android.settings:id/add_language' for n in nodes.iter('node')):return 'language'
    return 'manual'

def write_stage(folder,stage,autofill=None):
    if stage not in STAGES:raise ValueError('Unbekannter Android-Status.')
    data={'version':1,'stage':stage}
    if autofill is not None:data['autofill']=autofill is True
    temporary=folder/'status.tmp'
    temporary.write_text(json.dumps(data),encoding='utf8')
    temporary.replace(folder/'status.json')

def event(state,folder):
    print(PREFIX+json.dumps({'version':1,'state':state,'directory':str(folder),
        'pid':os.getpid(),'proxy':True}),flush=True)

def account_present(raw,address):
    return any(name.casefold()==address.casefold() for name in google_accounts(raw))

def fill_known(adb,ctx):
    fields={'firstName':ctx.vorname,'lastName':ctx.nachname,'Username':ctx.adresse.partition('@')[0],
            'Passwd':ctx.passwort,'PasswdAgain':ctx.passwort}
    tree=adb.baum();targets=supported_fields(tree);count=0
    password_was_filled=ctx.password_filled;password_done=set()
    password_expected={(n.get('resource-id') or '').rpartition('/')[2] for n in targets if (n.get('resource-id') or '').rpartition('/')[2] in ('Passwd','PasswdAgain')}
    for index,original in enumerate(targets):
        identifier=(original.get('resource-id') or '').rpartition('/')[2]
        # Focus/keyboard changes can move the next field. Do not reuse its
        # geometry from before the preceding input.
        if index:
            matches=[n for n in supported_fields(adb.baum()) if (n.get('resource-id') or '').rpartition('/')[2]==identifier]
            if len(matches)!=1:continue
            node=matches[0]
        else:node=original
        identifier=(node.get('resource-id') or '').rpartition('/')[2]
        if identifier in ('Passwd','PasswdAgain'):
            if password_was_filled:continue
            if node.get('text'):raise InputRejected('Kennwortfeld ist bereits ausgefüllt; keine automatische Ergänzung.')
        value=fields[identifier]
        if not isinstance(value,str) or len(value)>256 or any(ord(c)<32 or ord(c)>126 for c in value):
            raise InputRejected('Dieses Feld kann nicht sicher über ADB ausgefüllt werden.')
        bounds=GRENZE.fullmatch(node.get('bounds') or '')
        if not bounds:continue
        x1,y1,x2,y2=map(int,bounds.groups())
        if x2<=x1 or y2<=y1:continue
        adb.tap((x1+x2)//2,(y1+y2)//2)
        adb.taste('123')  # move cursor to end; replace the entire current field
        for _ in range(len(node.get('text') or '')):adb.taste('67')
        adb.tippen(value)
        count+=1
        if identifier in ('Passwd','PasswdAgain'):password_done.add(identifier)
    if password_expected and password_expected<=password_done:ctx.password_filled=True
    return count

def apply_input(adb,command,width,height,ctx):
    if not isinstance(command,dict):raise InputRejected('Ungültiges Android-Eingabeformat.')
    if 'size' in command and command['size']!=[width,height]:
        raise InputRejected('Android-Bildschirmgröße hat sich geändert; Vorschau erneuern.')
    if 'seen_at' in command:
        seen=command['seen_at']
        now=time.monotonic()
        if (type(seen) not in (int,float) or (type(seen) is float and not math.isfinite(seen)) or
            not now-10<=seen<=now):
            raise InputRejected('Android-Vorschau ist veraltet; auf ein aktuelles Bild warten.')
    kind=command.get('action')
    if kind=='tap':
        x,y=command.get('x'),command.get('y')
        if type(x) is not int or type(y) is not int or not (0<=x<width and 0<=y<height):
            raise InputRejected('Ungültige Android-Koordinaten')
        adb.tap(x,y)
    elif kind=='key' and command.get('key') in ('4','61','66','67'):
        if command['key']=='67' and ctx is not None:ctx.password_filled=False;ctx.password_confirmed_by_user=False
        adb.taste(command['key'])
    elif kind=='text':
        value=command.get('value')
        if not isinstance(value,str) or len(value)>256 or any(ord(c)<32 or ord(c)>126 for c in value):
            raise InputRejected('ADB-Texteingabe unterstützt nur bis zu 256 druckbare ASCII-Zeichen.')
        # Even a failed ADB call can have changed part of the field. Invalidate
        # credential proof before sending manual text, rather than after success.
        if ctx is not None:ctx.password_filled=False;ctx.password_confirmed_by_user=False
        adb.tippen(value)
    elif kind=='confirm_password':
        value=command.get('value')
        if not isinstance(value,str) or not 1<=len(value)<=256 or any(ord(c)<32 or ord(c)==127 for c in value):
            raise InputRejected('Kennwort muss 1 bis 256 Zeichen ohne Steuerzeichen enthalten.')
        if (ctx is None or getattr(ctx,'account_confirmed',False) is not True or
            not account_present(adb.sh('dumpsys','account',timeout=5),ctx.adresse)):
            raise InputRejected('Die Zieladresse ist noch nicht als Google-Konto bestätigt.')
        ctx.passwort=value;ctx.password_confirmed_by_user=True
    elif kind=='fill':
        try:
            if not fill_known(adb,ctx):
                ctx.hinweis('Keine unterstützten Registrierungsfelder erkannt. Angaben selbst im Android-Fenster eintragen; kein Feld wurde geändert.')
        except AdbError:
            ctx.hinweis('Nox konnte die Google-Eingabefelder nicht auslesen oder die Eingabe nicht vollständig übertragen. Angaben im Fenster prüfen. Ein selbst gewähltes Kennwort wird nach bestätigter Zieladresse lokal übernommen.')
    elif kind=='scroll' and command.get('direction') in ('up','down'):
        top,bottom=int(height*.3),int(height*.7)
        adb.swipe(width//2,bottom if command['direction']=='down' else top,
                  width//2,top if command['direction']=='down' else bottom)
    else:raise InputRejected('Unbekannte Android-Eingabe')

def parse(line):
    if not line.startswith(PREFIX) or len(line)>4096:return None
    try:
        data=json.loads(line[len(PREFIX):])
        if (data.get('version')!=1 or data.get('state') not in ('open','closed') or
            type(data.get('pid')) is not int or data['pid']<=0 or data.get('proxy') is not True or
            not isinstance(data.get('directory'),str)):return None
        return data
    except (ValueError,TypeError,AttributeError):return None

def run(cfg,ctx,proxy):
    if proxy is None:raise FinalError('Android benötigt einen ausdrücklich konfigurierten Proxy oder Tor; kein Testgerät vorbereitet und keine Windows-Freigabe angefordert.')
    base=Path(cfg.profiles_dir).resolve().parent/'android-sessions'
    base.mkdir(parents=True,exist_ok=True)
    profile=Profile(base.parent/'nox-console-device')
    request_guard(base,ctx.melden,ctx.stop,vm_name=profile.name,ownership_file=profile.marker)
    try:
        with Session(profile,proxy,ctx.stop,ctx.melden) as session:
            adb=session.adb
            adb.input_tempo = getattr(ctx, 'profile_tempo', None)
            if not adb.sh('pm','path','com.google.android.gms').strip().startswith('package:'):
                raise FinalError('Dem Testemulator fehlen Google-Dienste; kein Registrierungsversuch gestartet.')
            from .language import ensure_german
            ctx.melden('Android-Sprache für den sichtbaren Kontodialog auf Deutsch einstellen …')
            ensure_german(adb)
            # Android's native Google account provider, not a mobile browser URL.
            started=adb.sh('am','start','-a','android.settings.ADD_ACCOUNT_SETTINGS','--esa','account_types','com.google')
            if 'Error:' in started:raise FinalError('Der native Google-Kontendialog ist auf diesem Android-Gerät nicht verfügbar.')
            with tempfile.TemporaryDirectory(prefix='session-',dir=base) as directory:
                folder=Path(directory);inputs=folder/'inputs';inputs.mkdir()
                (folder/'instructions.json').write_text(json.dumps({'version':1,'address':ctx.adresse,
                    'first_name':ctx.vorname,'last_name':ctx.nachname}),encoding='utf8')
                initial=adb._cmd('exec-out','screencap','-p',roh=True,timeout=15)
                screen_size(initial)
                (folder/'frame.png').write_bytes(initial)
                event('open',folder)
                emit('interactive',ctx.melden)
                ctx.hinweis('Google-Konto im Android-Fenster erstellen. „Kontodaten eintragen“ füllt erkannte Registrierungsfelder. Bestätigungen selbst übernehmen; Google kann weiterhin Telefon/CAPTCHA/QR verlangen.')
                deadline=time.monotonic()+ctx.timeout;stage=None;next_stage=0
                try:
                    while time.monotonic()<deadline:
                        if ctx.stop and ctx.stop():raise FinalError('Abgebrochen.')
                        frame=adb._cmd('exec-out','screencap','-p',roh=True,timeout=15)
                        width,height=screen_size(frame)
                        temporary=folder/'frame.tmp';temporary.write_bytes(frame);temporary.replace(folder/'frame.png')
                        accepted=consume_inputs(inputs,lambda command:apply_input(adb,command,width,height,ctx),ctx.hinweis)
                        if accepted:
                            deadline=time.monotonic()+ctx.timeout;next_stage=0
                        confirmed=account_present(adb.sh('dumpsys','account',timeout=5),ctx.adresse)
                        if getattr(ctx,'account_confirmed',False) and not confirmed:
                            ctx.password_filled=False;ctx.password_confirmed_by_user=False
                        ctx.account_confirmed=confirmed
                        if confirmed and (ctx.password_filled or getattr(ctx,'password_confirmed_by_user',False)):
                            return {'adresse':ctx.adresse,'imap':{'host':'imap.gmail.com','port':993},
                                    'hinweis':'Native Android-Registrierung; IMAP-App-Passwort gegebenenfalls separat erforderlich.'}
                        if confirmed:
                            current,autofill='credentials_required',False
                            if stage!=current:
                                write_stage(folder,current,autofill);ctx.melden(STAGES[current]);stage=current
                        elif time.monotonic()>=next_stage:
                            current,autofill=observe_form(adb)
                            write_stage(folder,current,autofill)
                            if current!=stage:ctx.melden(STAGES[current]);stage=current
                            next_stage=time.monotonic()+(5 if autofill else 15)
                        time.sleep(.5)
                    raise FinalError('Android-Registrierung nicht bestätigt; kein Postfach im Pool gespeichert.')
                finally:event('closed',folder)
    finally:
        # Release the lease even if the VM could not be prepared.
        path=os.environ.pop('SS_ANDROID_GUARD',None)
        if path:
            state=json.loads(Path(path).read_text(encoding='utf-8-sig'))
            Path(state['release_file']).write_text('release',encoding='ascii')
