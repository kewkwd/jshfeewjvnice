"""One owned AVD, isolated ADB server, proxy and DNS relay with bounded cleanup."""
import os
import logging
import socket
import time
from contextlib import ExitStack

from . import avd
from .adb import Adb, AdbError
from ..net.errors import FinalError
from ..net.proxy import Proxy
from ..operation import check_cancelled, positive_timeout, wait
from ..runlock import OsLock

_RETAINED = []  # Hold ownership if native termination could not be confirmed.


def proxy_value(value):
    try:
        proxy = Proxy.parse(value) if isinstance(value, str) else value
        if not isinstance(proxy, Proxy) or proxy.scheme not in ('http', 'socks5'):
            raise ValueError('scheme')
        if not proxy.host or not 0 < proxy.port < 65536:
            raise ValueError('address')
        return proxy
    except (TypeError, ValueError):
        raise FinalError('AVD benötigt einen gültigen eigenen HTTP- oder SOCKS5-Proxy; kein Direktstart.') from None


def emulator_port():
    for port in range(5560, 5680, 2):
        with ExitStack() as stack:
            try:
                for number in (port, port + 1):
                    sock = stack.enter_context(socket.socket())
                    sock.bind(('127.0.0.1', number))
                return port
            except OSError:
                continue
    raise FinalError('Kein freies lokales AVD-Portpaar vorhanden.')


def environment(sdk, home, server_port):
    env = dict(os.environ)
    for key in list(env):
        if key.upper() in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
                           'ANDROID_ADB_SERVER_ADDRESS', 'ANDROID_SERIAL',
                           'ANDROID_EMULATOR_HOME', 'ANDROID_SDK_HOME'):
            env.pop(key)
    env.update(ANDROID_HOME=str(sdk), ANDROID_SDK_ROOT=str(sdk),
               ANDROID_AVD_HOME=str(home), ANDROID_USER_HOME=str(home / '.android'),
               ANDROID_ADB_SERVER_PORT=str(server_port),
               ADB_SERVER_SOCKET=f'tcp:127.0.0.1:{server_port}',
               ANDROID_EMULATOR_USE_SYSTEM_LIBS='0')
    return env


class Session:
    def __init__(self, name, proxy, *, headless=True, stop_requested=None, timeout=180,
                 sdk=None, home=None, profile_identity=None, profile_cfg=None):
        self.name = avd.safe_name(name)
        self.proxy = proxy_value(proxy)
        self.headless = bool(headless)
        self.stop = stop_requested
        self.timeout = positive_timeout(timeout)
        self.sdk = sdk
        self.home = avd._plain(home or avd.HOME)
        self.job = self.bridge = self.dns = self.lock = self.adb = None
        self.server = self.process = None
        self.active = False
        self.profile_identity,self.profile_cfg=profile_identity,profile_cfg

    def check(self):
        check_cancelled(self.stop)
        if not self.active:
            raise FinalError('Die eigene AVD-Sitzung ist nicht aktiv.')

    def _run_adb(self, command, *, timeout, env):
        if self.job is None or self.server is None or self.server.poll() is not None:
            raise AdbError('Der eigene ADB-Server läuft nicht mehr.')
        # ADB may auto-start a daemon if its server disappears during a call.
        # The command's nested job contains and removes such descendants.
        return self.job.run(command, env=env, cwd=self.sdk, timeout=timeout)

    def __enter__(self):
        check_cancelled(self.stop)
        self.sdk, _, _ = avd.sdk_info(self.sdk)
        avd.verify_profile(self.name, sdk=self.sdk, home=self.home)
        self.lock = OsLock(self.home / '.session.lock')
        if not self.lock.try_acquire({}, tries=1):
            raise FinalError('Ein eigener AVD-Auftrag läuft bereits.')
        try:
            avd.verify_guard(self.sdk)
            check_cancelled(self.stop)
            from ..net.browserproxy import BrowserProxy
            from .avd_dns import ProxyDns
            from .avd_process import WindowsOwnedJob
            self.bridge = BrowserProxy(self.proxy, allow_no_auth=True)
            self.dns = ProxyDns(self.bridge.playwright['server']).start()
            self.job = WindowsOwnedJob()
            with socket.socket() as reservation:
                reservation.bind(('127.0.0.1', 0))
                server_port = reservation.getsockname()[1]
            port = emulator_port()
            env = environment(self.sdk, self.home, server_port)
            (self.home / '.android').mkdir(exist_ok=True)
            acceleration = self.job.run([str(self.sdk / 'emulator/emulator.exe'), '-accel-check'],
                                        env=env, cwd=self.sdk, timeout=30)
            if acceleration.returncode != 0:
                raise FinalError('AVD benötigt eine einsatzbereite Windows-Hypervisor-Plattform. '
                                 'Die Windows-Einrichtung und gegebenenfalls ein Neustart stehen noch aus.')
            check_cancelled(self.stop)
            cfg=self.profile_cfg
            identity=self.profile_identity
            if cfg is None and self.home==avd._plain(avd.HOME):
                from ..guicore import read_settings
                cfg=read_settings()
            if cfg is not None and identity is None:
                from pathlib import Path
                profiles=Path(cfg.profiles_dir).resolve().parent/'profile_settings'
                matches=[]
                for path in profiles.glob('*.json'):
                    try:
                        if avd.name_for(path.stem)==self.name:matches.append(path.stem)
                    except ValueError:continue
                if len(matches)==1:identity=matches[0]
            if cfg is not None and identity is not None:
                from ..net.sticky_sessions import prepare_start
                self.proxy=prepare_start(cfg,identity,self.proxy)
            adb_path = self.sdk / 'platform-tools/adb.exe'
            # Windows ADB rejects hostnames in listener specs. tcp:PORT binds
            # loopback by default; never add -a (all interfaces). Clients retain
            # their explicit 127.0.0.1 endpoint in the isolated environment.
            server = self.job.start([str(adb_path), '-L', f'tcp:{server_port}',
                                     'nodaemon', 'server'], env=env, cwd=self.sdk)
            self.server = server
            deadline = time.monotonic() + self.timeout
            while True:
                check_cancelled(self.stop)
                if server.poll() is not None or time.monotonic() >= deadline:
                    raise FinalError('Der eigene ADB-Server wurde nicht bereit.')
                try:
                    with socket.create_connection(('127.0.0.1', server_port), timeout=.2):
                        break
                except OSError:
                    wait(.1, self.stop)
            check_cancelled(self.stop)
            command = [str(self.sdk / 'emulator/emulator.exe'), '-avd', self.name,
                       '-port', str(port), '-no-boot-anim', '-no-snapshot', '-no-audio',
                       '-no-metrics', '-http-proxy', self.bridge.playwright['server'],
                       '-dns-server', '127.0.0.1']
            if self.headless:
                command.append('-no-window')
            process = self.job.start(command, env=env, cwd=self.sdk)
            self.process = process
            self.adb = Adb(f'emulator-{port}', adb=str(adb_path), stop_requested=self.stop,
                           env=env, runner=self._run_adb)
            while time.monotonic() < deadline:
                check_cancelled(self.stop)
                if process.poll() is not None or server.poll() is not None:
                    raise FinalError('Das eigene AVD wurde während des Starts beendet.')
                try:
                    if self.adb.sh('getprop', 'sys.boot_completed', timeout=min(3, max(.1, deadline-time.monotonic()))).strip() == '1':
                        break
                except AdbError:
                    pass
                wait(.5, self.stop)
            else:
                raise FinalError('Das eigene AVD ist nicht rechtzeitig hochgefahren.')
            check_cancelled(self.stop)
            avd.verify_guard(self.sdk)
            check_cancelled(self.stop)
            self.active = True
            self.adb._socialscraper_avd_session = self
            from .language import ensure_avd_german
            ensure_avd_german(self.adb)
            check_cancelled(self.stop)
            from .apk_cache import ensure_all
            self.installed_apps = ensure_all(self.adb, self.home.parent / 'android-apks')
            from .diagnostics import observe
            try:
                observe(self)
            except Exception:
                check_cancelled(self.stop)
                logging.getLogger(__name__).warning('Android-Diagnose nicht gespeichert; laufende Sitzung bleibt verwendbar.')
            check_cancelled(self.stop)
            return self.adb
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *unused):
        self.active = False
        errors = []
        if self.adb is not None and self.server is not None and self.process is not None:
            try:
                if self.server.poll() is None and self.process.poll() is None:
                    self.adb._cmd('emu', 'kill', timeout=5, _check_cancel=False)
                    self.process.wait(timeout=10)
            except Exception:
                pass  # The owned job below is the bounded shutdown fallback.
        native_stopped = True
        for resource in (self.job, self.dns, self.bridge):
            if resource is not None:
                try:
                    resource.close()
                except Exception:
                    errors.append(type(resource).__name__)
                    if resource is self.job:
                        native_stopped = False
        if self.lock and native_stopped:
            self.lock.release()
        if not native_stopped and not any(item is self for item in _RETAINED):
            _RETAINED.append(self)
        if errors:
            raise FinalError('AVD-Aufräumen nicht vollständig bestätigt; Windows-Netzwerksperre bleibt bestehen.')
