from .base import BrowserBackend, SessionHandle

class SphereBackend(BrowserBackend):
    name = "sphere"

    def __init__(self, ls, presets=None):
        self.ls, self.presets = ls, presets or {}
        self._pw = None
        self._users = 0

    def prepare(self, platform):
        if pid := self.presets.get(platform): self.ls.use_preset(pid)

    def open(self, identity, proxy, *, headless=False, platform=None):
        import os
        if os.environ.get('SS_REQUIRE_PROXY') == '1':
            raise ValueError('Sphere-Tests sind gesperrt: Der externe Browserdienst besitzt keine prüfbare Proxy-Netzgrenze. Patchright verwenden.')
        from rebrowser_playwright.sync_api import sync_playwright
        uuid = self.ls.create(name=identity.name)
        handle = SessionHandle(identity, proxy, self.name, uuid=uuid, platform=platform)
        try:
            if proxy:
                self.ls.set_proxy(uuid, proxy.url)
            port = self.ls.start(uuid, headless=headless)
            if self._pw is None:
                self._pw = sync_playwright().start()
            handle._pw = self._pw
            self._users += 1
            handle._browser = handle._pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
            handle._context = handle._browser.contexts[0]
            self._apply_cookies(handle, platform)
            return handle
        except BaseException:
            self._close_after_failure(handle)
            raise

    def close(self, handle):
        browser, pw, uuid = handle._browser, handle._pw, handle.uuid
        handle._browser = handle._pw = handle._context = handle.uuid = None
        try:
            if browser is not None:
                browser.close()
        finally:
            try:
                if pw is not None:
                    self._users -= 1
                    if self._users <= 0:
                        self._pw = None
                        pw.stop()
            finally:
                if uuid:
                    try:
                        self.ls.stop(uuid)
                    finally:
                        self.ls.remove(uuid)
