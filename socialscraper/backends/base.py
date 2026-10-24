from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Optional
import logging

from ..net.cookies import CookieJar

log = logging.getLogger(__name__)

def cleanup_steps(steps):
    """Attempt independent cleanup operations and preserve the primary failure."""
    problems=[]
    for label,function in steps:
        try:function()
        except BaseException as error:problems.append((label,error))
    if problems:
        from ..problemlogic import scrub
        primary=next((error for _,error in problems if isinstance(error,(KeyboardInterrupt,SystemExit))),problems[0][1])
        for label,error in problems:
            if error is not primary:primary.add_note(label+': '+scrub(str(error)))
        raise primary

BASE_URL = {"tiktok": "https://www.tiktok.com/", "instagram": "https://www.instagram.com/",
            "reddit": "https://www.reddit.com/", "x": "https://x.com/"}

@dataclass
class SessionHandle:
    identity: Any
    proxy: Any
    backend: str
    _pw: Any = None
    _browser: Any = None
    _context: Any = None
    _shim: Any = None
    uuid: Optional[str] = None
    platform: Optional[str] = None

class BrowserBackend(ABC):
    name = "base"

    @abstractmethod
    def open(self, identity, proxy, *, headless=False, platform=None) -> SessionHandle: ...

    @abstractmethod
    def close(self, handle: SessionHandle) -> None: ...

    def prepare(self, platform: str) -> None: return None

    def observe_settings(self, handle):
        from ..browser_diagnostics import observe
        try:
            observe(self, handle)
        except Exception:
            log.warning('Browserdiagnose konnte nicht gespeichert werden; die Sitzung bleibt verwendbar.')

    def page(self, handle):
        page = handle._context.pages[0] if handle._context.pages else handle._context.new_page()
        from ..net.input_behavior import bind
        return bind(page, getattr(getattr(handle, 'identity', None), 'settings', None))

    def inject_cookies(self, handle, cookies):
        if cookies:
            handle._context.add_cookies(cookies)

    def read_cookies(self, handle):
        return handle._context.cookies()

    def _apply_cookies(self, handle, platform):
        if not platform or platform == 'mail':
            return 0
        selection = getattr(handle, '_profile_selection', None)
        if selection is not None and selection.isolated:
            return 0  # Do not re-import cookies from the failed profile/account session.
        # Kostenlose Proxys/Tor und die anonyme Identität: nie Cookies laden (kein Konto, siehe net/proxymode.py).
        if getattr(handle.proxy, 'anonymous', False) is True or getattr(handle.identity, 'name', None) == 'anon_public':
            return 0
        cookies = CookieJar.load(handle.identity.name, platform)
        if not cookies:
            return 0
        self.inject_cookies(handle, cookies)
        return len(cookies)

    def _close_after_failure(self, handle):
        self.failed_profile(handle)
        try:
            self.close(handle)
        except Exception:
            log.warning("Browser konnte nach Fehler nicht vollständig schließen", exc_info=True)

    def failed_profile(self, handle):
        selection = getattr(handle, '_profile_selection', None)
        if selection is not None:
            selection.failed = True

    def finish_profile(self, handle, *, success):
        selection = getattr(handle, '_profile_selection', None)
        if selection is not None:
            selection.finish(success=success)

    def close_steps(self, handle, steps):
        try:
            cleanup_steps(steps)
        except BaseException as error:
            try:self.finish_profile(handle, success=False)
            except Exception:
                error.add_note('Profilstatus nicht gespeichert; der nächste Start behandelt das Profil als unterbrochen.')
            raise
        else:
            self.finish_profile(handle, success=True)

    @contextmanager
    def session(self, identity, proxy, *, headless=False, platform=None):
        self.prepare(platform)
        cfg=getattr(self,'profile_proxy_cfg',None)
        if cfg is not None:
            from ..net.sticky_sessions import prepare_start
            proxy=prepare_start(cfg,identity.name,proxy)
        h = self.open(identity, proxy, headless=headless, platform=platform)
        try:
            yield h
        except BaseException:
            self._close_after_failure(h)
            raise
        else:
            self.close(h)
