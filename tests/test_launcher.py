"""Tests für das C#-Startprogramm (launcher/Socialscraper.cs) – nur, was ohne Fenster prüfbar ist.

Gebaut wird in einen Wegwerf-Ordner (nicht die echte Socialscraper.exe); gestartet wird nur `--dienst`
mit einem Attrappen-„pythonw“, das seine Aufrufdaten in eine Datei schreibt. Es öffnet sich kein Fenster,
es werden keine echten Daten berührt.
"""
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
CSC = pathlib.Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Microsoft.NET' / 'Framework64' / 'v4.0.30319' / 'csc.exe'
FAKE_SRC = r'''
using System; using System.IO;
static class F { static void Main(string[] a) {
  File.WriteAllText(Path.Combine(Environment.CurrentDirectory, "aufruf.txt"),
    "args=" + string.Join(" ", a) + "\r\ncwd=" + Environment.CurrentDirectory + "\r\n"); } }
'''


@unittest.skipUnless(os.name == 'nt', 'PowerShell-AST-Prüfung benötigt Windows')
class QuickMenuArguments(unittest.TestCase):
    """Load only pure argument builders; never run the menu, app or browser."""

    @classmethod
    def setUpClass(cls):
        powershell = shutil.which('powershell')
        if not powershell:
            raise unittest.SkipTest('Windows PowerShell fehlt')
        source = r'''
param([string]$LauncherPath)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$tokens = $null; $parseErrors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($LauncherPath, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw 'Launcher syntax invalid' }
foreach ($name in @('Get-Identity', 'Get-UrlPlatform', 'Get-LoginArguments', 'Get-CheckArguments', 'Get-ScrapeArguments')) {
    $definitions = @($ast.FindAll({param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name
    }, $true))
    if ($definitions.Count -ne 1) { throw 'Missing or duplicate argument builder' }
    . ([scriptblock]::Create($definitions[0].Extent.Text))
}
# No top-level launcher code, Start-Menu or Invoke-Scraper is evaluated.
$login = @{}; $check = @{}; $scrape = @{}
foreach ($platform in @('tiktok', 'instagram', 'reddit', 'x')) {
    $login[$platform] = @(Get-LoginArguments $platform 'qr')
    $check[$platform] = @(Get-CheckArguments $platform)
}
$links = @{
    tiktok = 'https://www.tiktok.com/@fixture/video/123'
    instagram = 'https://www.instagram.com/p/fixture/'
    reddit = 'https://www.reddit.com/r/fixture/comments/123/'
    x = 'https://x.com/fixture/status/123'
    youtube = 'https://www.youtube.com/watch?v=fixture'
}
foreach ($platform in $links.Keys) {
    $scrape[$platform] = @(Get-ScrapeArguments $links[$platform] 'C:\fixture export')
}
@{ login = $login; check = $check; scrape = $scrape } | ConvertTo-Json -Depth 5 -Compress
'''
        with tempfile.TemporaryDirectory(prefix='menu-arguments-') as folder:
            script = pathlib.Path(folder) / 'verify.ps1'
            script.write_text(source, encoding='utf-8-sig')
            result = subprocess.run([powershell, '-NoLogo', '-NoProfile', '-NonInteractive',
                                     '-ExecutionPolicy', 'Bypass', '-File', str(script), str(ROOT / 'Starten.ps1')],
                                    capture_output=True, text=True, encoding='utf-8', timeout=20,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise AssertionError('Offline-Argumentprüfung fehlgeschlagen:\n' + result.stderr)
        cls.arguments = json.loads(result.stdout)

    def _assert_configured_engine(self, args):
        from socialscraper.cli import parser
        self.assertNotIn('--engine', args)
        parsed = parser().parse_args(args)
        self.assertIsNone(parsed.engine, 'Die gespeicherte Browserwahl darf nicht überschrieben werden')
        self.assertEqual(parsed.backend, 'local')
        self.assertEqual(parsed.country, 'de')
        return parsed

    def test_login_arguments_keep_configured_engine_for_every_platform(self):
        for platform, args in self.arguments['login'].items():
            parsed = self._assert_configured_engine(args)
            self.assertEqual(parsed.cmd, 'login')
            self.assertEqual(parsed.platform, platform)
            self.assertEqual(parsed.identity, platform + '_main')
            self.assertEqual(parsed.mode, 'qr')
            self.assertEqual(parsed.timeout, 600)

    def test_check_arguments_keep_configured_engine_for_every_platform(self):
        for platform, args in self.arguments['check'].items():
            parsed = self._assert_configured_engine(args)
            self.assertEqual(parsed.cmd, 'check')
            self.assertEqual(parsed.platform, platform)
            self.assertEqual(parsed.identity, platform + '_main')

    def test_scrape_arguments_keep_configured_engine_and_platform_requirements(self):
        for platform, args in self.arguments['scrape'].items():
            parsed = self._assert_configured_engine(args)
            self.assertEqual(parsed.cmd, 'scrape')
            self.assertEqual(parsed.identity, platform + '_main')
            self.assertEqual(parsed.max_comments, 20)
            self.assertEqual(parsed.export, r'C:\fixture export')
            self.assertEqual(bool(parsed.require_auth), platform in ('tiktok', 'instagram', 'x'))


def _csc(args):
    return subprocess.run([str(CSC), '/nologo', *args], capture_output=True, text=True, timeout=120)


@unittest.skipUnless(os.name == 'nt' and CSC.is_file(), 'braucht Windows und den .NET-Framework-Compiler')
class LauncherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = pathlib.Path(tempfile.mkdtemp(prefix='ssl'))  # kurzer Pfad: csc scheitert an langen
        cls.exe = cls.tmp / 'Socialscraper.exe'
        result = _csc(['/target:winexe', '/codepage:65001', f'/win32icon:{ROOT / "assets" / "socialscraper.ico"}',
                       f'/win32manifest:{ROOT / "launcher" / "Socialscraper.manifest"}', f'/out:{cls.exe}',
                       '/reference:System.Windows.Forms.dll', '/reference:System.Drawing.dll',
                       str(ROOT / 'launcher' / 'Socialscraper.cs')])
        if result.returncode != 0 or not cls.exe.is_file():
            raise AssertionError('Startprogramm lässt sich nicht bauen:\n' + result.stdout + result.stderr)
        fake_cs = cls.tmp / 'fake.cs'
        fake_cs.write_text(FAKE_SRC, encoding='ascii')
        cls.fake = cls.tmp / 'fakepythonw.exe'
        result = _csc(['/target:winexe', f'/out:{cls.fake}', str(fake_cs)])
        if result.returncode != 0:
            raise AssertionError('Attrappe lässt sich nicht bauen:\n' + result.stdout + result.stderr)
        # Smart App Control blockiert frisch gebaute, unsignierte Dateien manchmal (Fehler 4551).
        # Dann lässt sich die Attrappe nicht starten; die davon abhängigen Tests werden übersprungen.
        cls.fake_blocked = False
        try:
            subprocess.run([str(cls.fake)], cwd=str(cls.tmp), timeout=20,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except OSError:
            cls.fake_blocked = True
        (cls.tmp / 'aufruf.txt').unlink(missing_ok=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _programmordner(self, name):
        if self.fake_blocked:
            self.skipTest('Windows blockiert die frisch gebaute Attrappe (Smart App Control, Fehler 4551)')
        folder = self.tmp / name
        (folder / 'socialscraper').mkdir(parents=True)
        (folder / 'socialscraper' / 'gui.py').write_text('# Attrappe', encoding='utf-8')
        scripts = folder / '.venv' / 'Scripts'
        scripts.mkdir(parents=True)
        shutil.copy(self.fake, scripts / 'pythonw.exe')
        return folder

    def _run(self, exe, *args, localappdata=None):
        env = dict(os.environ)
        env['LOCALAPPDATA'] = str(localappdata or (self.tmp / 'leer'))
        try:
            return subprocess.run([str(exe), *args], env=env, cwd=str(self.tmp), timeout=30,
                                  creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except OSError as error:   # Smart App Control blockiert die frisch gebaute .exe
            self.skipTest(f'Die frisch gebaute .exe lässt sich hier nicht starten: {error}')

    def test_quelle_und_dateien_vorhanden(self):
        source = (ROOT / 'launcher' / 'Socialscraper.cs').read_text(encoding='utf-8')
        self.assertIn('--dienst', source)
        self.assertIn('Einrichten starten', source)
        self.assertTrue((ROOT / 'assets' / 'socialscraper.ico').is_file())

    def test_exe_hat_versionsinformationen_und_manifest(self):
        info = subprocess.run(['powershell', '-NoProfile', '-Command',
                               f"(Get-Item -LiteralPath '{self.exe}').VersionInfo | "
                               "ForEach-Object { $_.ProductName + '|' + $_.FileVersion + '|' + $_.FileDescription }"],
                              capture_output=True, text=True, timeout=60).stdout.strip()
        self.assertEqual(info.split('|')[:2], ['Profilwerk', '1.0.0.0'])
        data = self.exe.read_bytes()
        self.assertEqual(data[:2], b'MZ')
        self.assertIn(b'asInvoker', data)   # kein Administrator
        self.assertIn(b'dpiAware', data)    # DPI-bewusst

    def test_dienst_startet_pythonw_mit_daemon_im_programmordner_mit_leerzeichen(self):
        folder = self._programmordner('Ordner mit Leerzeichen')
        exe = folder / 'Socialscraper.exe'
        shutil.copy(self.exe, exe)
        self.assertEqual(self._run(exe, '--dienst').returncode, 0)
        report = self._wait_for(folder / 'aufruf.txt')
        self.assertIn('args=-m socialscraper daemon', report)
        self.assertIn('cwd=' + str(folder), report)

    def test_exe_woanders_findet_ordner_ueber_ort_txt(self):
        folder = self._programmordner('Programm')
        local = self.tmp / 'local'
        (local / 'Profilwerk').mkdir(parents=True)
        (local / 'Profilwerk' / 'ort.txt').write_text(str(folder) + '\r\n', encoding='utf-8')
        lone = self.tmp / 'Desktop'
        lone.mkdir()
        exe = lone / 'Socialscraper.exe'
        shutil.copy(self.exe, exe)
        self.assertEqual(self._run(exe, '--dienst', localappdata=local).returncode, 0)
        self.assertIn('args=-m socialscraper daemon', self._wait_for(folder / 'aufruf.txt'))

    def test_dienst_ohne_programmordner_oder_umgebung_zeigt_kein_fenster(self):
        lone = self.tmp / 'Allein'
        lone.mkdir()
        exe = lone / 'Socialscraper.exe'
        shutil.copy(self.exe, exe)
        self.assertEqual(self._run(exe, '--dienst').returncode, 3)       # kein Programmordner
        ohne_venv = self.tmp / 'OhneVenv'
        (ohne_venv / 'socialscraper').mkdir(parents=True)
        (ohne_venv / 'socialscraper' / 'gui.py').write_text('#', encoding='utf-8')
        exe2 = ohne_venv / 'Socialscraper.exe'
        shutil.copy(self.exe, exe2)
        self.assertEqual(self._run(exe2, '--dienst').returncode, 2)      # keine .venv

    def test_pruefen_meldet_nur_per_beendigungscode(self):
        folder = self._programmordner('Pruefen')
        exe = folder / 'Socialscraper.exe'
        shutil.copy(self.exe, exe)
        self.assertEqual(self._run(exe, '--pruefen').returncode, 0)
        self.assertFalse((folder / 'aufruf.txt').exists())               # nichts wurde gestartet
        shutil.rmtree(folder / '.venv')
        self.assertEqual(self._run(exe, '--pruefen').returncode, 2)      # Umgebung fehlt, aber kein Fenster

    def _wait_for(self, path, seconds=10):
        import time
        end = time.time() + seconds
        while time.time() < end:
            if path.is_file() and path.stat().st_size:
                return path.read_text(encoding='ascii')
            time.sleep(0.2)
        self.fail(f'{path.name} wurde nicht geschrieben')


if __name__ == '__main__':
    unittest.main()
