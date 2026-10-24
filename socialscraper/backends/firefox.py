"""Desktop Firefox with an isolated profile and proxy policy present at startup."""
import json
import logging
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit

from .base import SessionHandle
from .local import LocalBackend, driver_dead
from ..net.browserproxy import BrowserProxy
from ..net.firefox_runtime import installed_binary
from ..net.identity import validate_name
from ..runlock import OsLock

log = logging.getLogger(__name__)


def proxy_preferences(server):
    """The owned bridge is the sole browser route, including before protocol attach."""
    endpoint = urlsplit(server)
    if endpoint.scheme != 'http' or endpoint.hostname != '127.0.0.1' or not endpoint.port:
        raise ValueError('Firefox benötigt seine lokale Proxy-Brücke.')
    return {
        'network.proxy.type': 1,
        'network.proxy.http': '127.0.0.1', 'network.proxy.http_port': endpoint.port,
        'network.proxy.ssl': '127.0.0.1', 'network.proxy.ssl_port': endpoint.port,
        'network.proxy.no_proxies_on': '',
        'network.proxy.failover_direct': False,
        'network.proxy.allow_bypass': False,
        'network.proxy.allow_hijacking_localhost': True,
        'network.proxy.socks_remote_dns': True,
        'network.proxy.socks5_remote_dns': True,
        'network.trr.mode': 5,
        'network.dns.disabled': True,
        'network.dns.native_https_query': False,
        'network.dns.disablePrefetch': True,
        'network.dns.disablePrefetchFromHTTPS': True,
        'network.prefetch-next': False,
        'network.predictor.enabled': False,
        'network.http.speculative-parallel-limit': 0,
        'network.http.http3.enable': False,
        'network.captive-portal-service.enabled': False,
        'network.connectivity-service.enabled': False,
        'network.socket.allowed_nonlocal_domains': '',
        'media.peerconnection.enabled': False,
    }


def write_startup_preferences(profile, preferences):
    """Write managed user.js before launch; never put upstream credentials in it."""
    profile.mkdir(parents=True, exist_ok=True)
    contents = '// Managed Socialscraper Firefox startup network policy.\n' + ''.join(
        f'user_pref({json.dumps(key)}, {json.dumps(value)});\n'
        for key, value in sorted(preferences.items()))
    fd, temp = tempfile.mkstemp(prefix='user.js.', suffix='.tmp', dir=profile)
    try:
        with os.fdopen(fd, 'w', encoding='utf8') as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, profile / 'user.js')
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


class FirefoxBackend(LocalBackend):
    name = 'firefox'

    def __init__(self, profiles_dir='data/profiles', *, identity_manager=None):
        super().__init__(profiles_dir=profiles_dir)
        self.engine = 'firefox'
        self.identity_manager = identity_manager

    def open(self, identity, proxy, *, headless=False, platform=None):
        if proxy is None:
            raise ValueError('Firefox benötigt einen Proxy oder Tor; direkte Verbindungen sind gesperrt.')
        if proxy.scheme not in ('http', 'socks5'):
            raise ValueError('Firefox unterstützt hier HTTP- oder SOCKS5-Proxys, einschließlich Tor.')
        if identity.fp.is_mobile and self.identity_manager is not None:
            desktop = self.identity_manager.browser_context(identity, engine='firefox')
            if desktop is not None:
                identity = desktop
        if identity.fp.is_mobile:
            raise ValueError('Firefox unterstützt hier Desktop-Profile. Für mobile Profile Chrome wählen.')
        name = validate_name(identity.name)
        if platform is not None and (not isinstance(platform, str) or
                not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,95}', platform)):
            raise ValueError('Ungültige Plattform für das Firefox-Profil.')
        # MSIX can redirect LocalAppData. The native loader needs the physical
        # directory to find Firefox's adjacent DLLs; keep the pinned bundle.
        binary = installed_binary().resolve()
        from playwright.sync_api import sync_playwright

        # _firefox cannot collide with a valid Chrome identity (no leading underscore).
        lock = OsLock(self.profiles / '_firefox' / '_locks' / name / ((platform or '_general') + '.lock'))
        if not lock.try_acquire({'owner': 'Firefox-Profil'}):
            raise RuntimeError('Dieses Firefox-Profil wird bereits verwendet.')
        handle = SessionHandle(identity, proxy, self.name, platform=platform)
        handle._profile_lock = lock
        try:
            from .profile_store import begin
            handle._profile_selection = begin(self.profiles, name, platform, firefox=True)
            profile = handle._profile_selection.path
            handle._proxy_bridge = BrowserProxy(proxy, allow_no_auth=True)
            route = handle._proxy_bridge.playwright
            prefs = proxy_preferences(route['server'])
            write_startup_preferences(profile, prefs)
            if self._pw is None:
                self._pw = sync_playwright().start()
            pw = self._pw
            self._users += 1
            handle._pw = pw
            fp = identity.fp
            handle._context = pw.firefox.launch_persistent_context(
                user_data_dir=str(profile.resolve()), executable_path=str(binary),
                headless=headless, timeout=60000,
                env={**os.environ, 'MOZ_DISABLE_NONLOCAL_CONNECTIONS': '1',
                     'MOZ_DISABLE_NONLOCAL_CONNECTIONS_ALLOW': ''},
                proxy=route, firefox_user_prefs=prefs,
                viewport=fp.viewport, screen=fp.screen, device_scale_factor=fp.device_scale,
                locale=fp.locale, timezone_id=fp.timezone,
                permissions=[], color_scheme='light', accept_downloads=False,
                service_workers='block')
            self._apply_cookies(handle, platform)
            self.observe_settings(handle)
            from ..net.browser_window import announce
            try:
                announce(handle, headless)
            except Exception:
                log.warning('Firefox bleibt separat geöffnet; Einbettung nicht verfügbar.', exc_info=True)
            from ..window_notification import window_opened
            window_opened(handle, headless=headless)
            return handle
        except BaseException as error:
            if handle._pw is self._pw and self._pw is not None and driver_dead(error):
                self._reset_driver(self._pw)
            self._close_after_failure(handle)
            raise

    def close(self, handle):
        lock = getattr(handle, '_profile_lock', None)
        handle._profile_lock = None
        try:
            super().close(handle)
        finally:
            if lock is not None:
                lock.release()
