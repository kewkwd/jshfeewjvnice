import os, re, shlex, subprocess, time, uuid, xml.etree.ElementTree as ET
from ..net.errors import FinalError
GRENZE = re.compile(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]')

class AdbError(RuntimeError):
    pass

class Adb:
    def __init__(self, serial=None, adb='adb',stop_requested=None,env=None,runner=None):
        self.serial, self.adb = serial, adb
        self.stop_requested=stop_requested
        self.env = dict(env) if env is not None else None
        if runner is not None and not callable(runner):
            raise ValueError('Ungültiger ADB-Prozessbesitzer.')
        self.runner = runner
        self.input_tempo = None

    def _input_wait(self, seconds):
        from ..operation import wait
        if not wait(seconds, self.stop_requested):raise FinalError('Abgebrochen.')

    def _cmd(self, *args, timeout=30, roh=False, _check_cancel=True):
        if not args:raise ValueError('ADB-Befehl fehlt.')
        if _check_cancel and self.stop_requested and self.stop_requested():raise FinalError('Abgebrochen.')
        befehl = [self.adb] + (['-s', self.serial] if self.serial else []) + [str(a) for a in args]
        try:
            if self.runner is not None:
                ergebnis = self.runner(befehl, timeout=timeout, env=self.env)
            else:
                ergebnis = subprocess.run(befehl, capture_output=True, timeout=timeout,
                                          **({'env': self.env} if self.env is not None else {}),
                                          creationflags=0x08000000 if os.name=='nt' else 0)
        except FileNotFoundError:
            raise FinalError('adb wurde nicht gefunden. Android-SDK installieren und in den PATH legen.') from None
        except subprocess.TimeoutExpired:
            raise AdbError(f'adb {args[0]} hat zu lange gebraucht.') from None
        if _check_cancel and self.stop_requested and self.stop_requested():raise FinalError('Abgebrochen.')
        if ergebnis.returncode != 0:
            # adb diagnostics may echo an input value, including a password.
            # A binary response is also invalid when the command failed.
            raise AdbError(f'ADB {args[0]} fehlgeschlagen (Exitcode {ergebnis.returncode}).')
        return ergebnis.stdout if roh else ergebnis.stdout.decode('utf-8', 'replace')

    def sh(self, *args, **kwargs):
        return self._cmd('shell', *args, **kwargs)

    @staticmethod
    def geraete(adb='adb'):
        try:
            roh = subprocess.run([adb, 'devices'], capture_output=True, timeout=20).stdout.decode('utf-8', 'replace')
        except FileNotFoundError:
            return []
        return [z.split()[0] for z in roh.splitlines()[1:] if z.strip().endswith('device')]

    def warten_auf_geraet(self, timeout=120):
        ende = time.monotonic() + timeout
        while time.monotonic() < ende:
            if self.serial in self.geraete(self.adb):
                self._cmd('wait-for-device', timeout=30); return True
            time.sleep(2)
        raise AdbError(f'Gerät {self.serial} ist nicht erreichbar.')

    def tap(self, x, y):
        if self.input_tempo:
            self._input_wait(self.input_tempo['click_ms'] / 1000)
        self.sh('input', 'tap', int(x), int(y),timeout=5)

    def swipe(self, x1, y1, x2, y2, ms=300):
        self.sh('input', 'swipe', int(x1), int(y1), int(x2), int(y2), int(ms))

    def tippen(self, text):
        text = str(text)
        if any(ord(char) < 32 or ord(char) > 126 for char in text):
            raise AdbError('ADB-Texteingabe unterstützt hier nur druckbare ASCII-Zeichen.')
        # One character at a time prevents Android's %s substitution from
        # corrupting literal percent signs; quoting protects shell metacharacters.
        for char in text:
            self.sh('input', 'text', shlex.quote('%s' if char == ' ' else char),timeout=5)
            if self.input_tempo:self._input_wait(1 / self.input_tempo['typing_cps'])

    def taste(self, code):
        self.sh('input', 'keyevent', code,timeout=5)

    def zurueck(self):
        self.taste('4')

    def groesse(self):
        treffer = re.search(r'(\d+)x(\d+)', self.sh('wm', 'size'))
        if not treffer:
            raise AdbError('Bildschirmgröße nicht lesbar.')
        return int(treffer.group(1)), int(treffer.group(2))

    def baum(self,timeout=8):
        path='/data/local/tmp/socialscraper-ui-'+uuid.uuid4().hex+'.xml'
        deadline=time.monotonic()+timeout
        def remaining():
            value=deadline-time.monotonic()
            if value<=0:raise AdbError('Zeitlimit beim Lesen der Android-Felder erreicht.')
            return value
        try:
            self.sh('uiautomator','dump',path,timeout=remaining())
            raw=self.sh('cat',path,timeout=remaining())
            if not raw.strip() or len(raw)>2_000_000:raise AdbError('Android-Feldbaum ist leer oder zu groß.')
            try:tree=ET.fromstring(raw)
            except ET.ParseError:raise AdbError('Android-Feldbaum ist beschädigt.') from None
            if tree.tag!='hierarchy':raise AdbError('Android-Feldbaum hat ein unbekanntes Format.')
            return tree
        finally:
            # The caller's cancellation must not leave this dump's form values
            # on the guest. Only this bounded cleanup ignores cancellation.
            try:self.sh('rm','-f',path,timeout=2,_check_cancel=False)
            except (AdbError,FinalError):pass

    def finde(self, *, text=None, teil=None, rid=None, beschreibung=None):   # FIX A2
        for knoten in self.baum().iter('node'):
            k_text, k_desc = knoten.get('text') or '', knoten.get('content-desc') or ''
            if text and k_text != text:
                continue
            if teil and teil.lower() not in k_text.lower() and teil.lower() not in k_desc.lower():
                continue
            if rid and rid != knoten.get('resource-id'):
                continue
            if beschreibung and beschreibung.lower() not in k_desc.lower():
                continue
            treffer = GRENZE.match(knoten.get('bounds') or '')
            if not treffer:
                continue
            x1, y1, x2, y2 = map(int, treffer.groups())
            return ((x1 + x2) // 2, (y1 + y2) // 2)
        return None

    def warte_auf(self, *, text=None, teil=None, rid=None, timeout=30, takt=1.5):
        ende = time.monotonic() + timeout
        while time.monotonic() < ende:
            ziel = self.finde(text=text, teil=teil, rid=rid)
            if ziel:
                return ziel
            time.sleep(takt)
        return None

    def tippe_auf(self, *, text=None, teil=None, rid=None, timeout=20):
        ziel = self.warte_auf(text=text, teil=teil, rid=rid, timeout=timeout)
        if not ziel:
            return False
        self.tap(*ziel); return True

    def texte(self):
        return [n.get('text') for n in self.baum().iter('node') if n.get('text')]

    def app_starten(self, paket):
        self.sh('monkey', '-p', paket, '-c', 'android.intent.category.LAUNCHER', '1')

    def app_stoppen(self, paket):
        self.sh('am', 'force-stop', paket)

    def daten_loeschen(self, paket):
        self.sh('pm', 'clear', paket)

    def proxy_setzen(self, url=None):
        self.sh('settings', 'put', 'global', 'http_proxy', url or ':0')

    def modell(self):
        return self.sh('getprop', 'ro.product.model').strip()
