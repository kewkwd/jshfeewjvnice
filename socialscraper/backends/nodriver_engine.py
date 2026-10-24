# nodriver_engine.py
from pathlib import Path
import asyncio
import inspect
import nodriver as uc
from .base import BASE_URL, BrowserBackend, SessionHandle, cleanup_steps
from .async_runner import AsyncRunner
from .nodriver_shim import TabShim

class NodriverBackend(BrowserBackend):
    name = "nodriver"
    def __init__(self, profiles_dir="data/profiles"):
        self.profiles = Path(profiles_dir); self.profiles.mkdir(parents=True, exist_ok=True)
        self.runner = AsyncRunner()

    def open(self, identity, proxy, *, headless=False, platform=None):
        import os
        if os.environ.get('SS_REQUIRE_PROXY') == '1' and proxy is None:
            raise ValueError('Dieser Test benötigt einen Proxy oder Tor.')
        fp = identity.fp
        if fp.is_mobile:
            raise ValueError("Mobile Identitäten werden nur mit der Engine patchright unterstützt")
        args = ["--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage",
                "--no-first-run", "--no-default-browser-check", f"--lang={fp.locale}",
                f"--window-size={fp.viewport['width']},{fp.viewport['height']}"]
        from ..net.browserproxy import options
        browser_proxy, bridge = options(proxy)
        if browser_proxy:
            from .local import LocalBackend
            args.append(f"--proxy-server={browser_proxy['server']}")
            args += LocalBackend._proxy_args(browser_proxy)
        handle = SessionHandle(identity, proxy, self.name, platform=platform)
        handle._proxy_bridge = bridge
        try:
            from .profile_store import begin
            handle._profile_selection = begin(self.profiles, identity.name, platform)
            profile = handle._profile_selection.path.resolve()
            handle._browser = self.runner.run(uc.start(headless=headless,
                user_data_dir=str(profile), browser_args=args, lang=fp.locale, sandbox=False))
            handle._context = self.runner.run(handle._browser.get("about:blank"))
            handle._shim = TabShim(self.runner, handle._context)
            self.runner.run(self._emulate(handle._context, fp))
            if proxy and proxy.user and bridge is None:
                self.runner.run(self._auth(handle._context, proxy))
            self._apply_cookies(handle, platform)
            return handle
        except BaseException:
            self._close_after_failure(handle)
            raise

    async def _emulate(self, tab, fp):
        # Apply regional formatting, but do not invent a location or grant access to one.
        emulation, browser = uc.cdp.emulation, uc.cdp.browser
        await tab.send(emulation.set_timezone_override(timezone_id=fp.timezone))
        await tab.send(emulation.set_locale_override(locale=fp.locale))
        await tab.send(browser.grant_permissions(permissions=[]))

    async def _auth(self, tab, proxy):
        attempted = set()
        async def on_auth(ev):
            response = "Default"
            if getattr(ev.auth_challenge, "source", None) == "Proxy":
                response = "CancelAuth" if ev.request_id in attempted else "ProvideCredentials"
                attempted.add(ev.request_id)
            await tab.send(uc.cdp.fetch.continue_with_auth(
                request_id=ev.request_id,
                auth_challenge_response=uc.cdp.fetch.AuthChallengeResponse(
                    response=response,
                    username=proxy.user if response == "ProvideCredentials" else None,
                    password=proxy.password if response == "ProvideCredentials" else None)))
        async def on_paused(ev):
            await tab.send(uc.cdp.fetch.continue_request(request_id=ev.request_id))
        tab.add_handler(uc.cdp.fetch.AuthRequired, on_auth)
        tab.add_handler(uc.cdp.fetch.RequestPaused, on_paused)
        await tab.send(uc.cdp.fetch.enable(handle_auth_requests=True))

    def inject_cookies(self, handle, cookies):
        async def apply():
            for cookie in cookies:
                options = {"name": cookie["name"], "value": cookie["value"],
                           "path": cookie.get("path") or "/",
                           "secure": bool(cookie.get("secure", True)),
                           "http_only": bool(cookie.get("httpOnly", False))}
                if cookie.get("domain"):
                    options["domain"] = cookie["domain"]
                else:
                    url = cookie.get("url") or BASE_URL.get(handle.platform)
                    if not url:
                        raise ValueError("Cookie benötigt Domain oder Plattform-URL")
                    options["url"] = url
                if cookie.get("expires") is not None and cookie["expires"] > 0:
                    options["expires"] = uc.cdp.network.TimeSinceEpoch(cookie["expires"])
                if cookie.get("sameSite"):
                    options["same_site"] = uc.cdp.network.CookieSameSite(cookie["sameSite"])
                if await handle._context.send(uc.cdp.network.set_cookie(**options)) is False:
                    raise ValueError("Browser hat Cookie abgelehnt")
        self.runner.run(apply())

    def read_cookies(self, handle):
        cookies = self.runner.run(handle._context.send(uc.cdp.network.get_all_cookies()))
        result = []
        for cookie in cookies:
            item = {"name": cookie.name, "value": cookie.value, "domain": cookie.domain,
                    "path": cookie.path, "secure": cookie.secure, "httpOnly": cookie.http_only}
            if cookie.expires and cookie.expires > 0:
                item["expires"] = float(cookie.expires)
            same_site = getattr(cookie, "same_site", None)
            if same_site is not None:
                item["sameSite"] = getattr(same_site, "value", same_site)
            result.append(item)
        return result

    def page(self, handle):
        from ..net.input_behavior import bind
        return bind(handle._shim, getattr(getattr(handle, 'identity', None), 'settings', None))

    def close(self, handle):
        tab, browser = handle._context, handle._browser
        handle._context = handle._browser = handle._shim = None
        bridge=getattr(handle,'_proxy_bridge',None);handle._proxy_bridge=None
        async def cleanup():
            try:
                if tab is not None:
                    await tab.close()
            finally:
                if browser is not None:
                    process = getattr(browser, "_process", None)
                    result = browser.stop()
                    if inspect.isawaitable(result):
                        await result
                    if process is not None:
                        # Nodriver terminates Chrome but leaves its Windows pipes open.
                        await asyncio.wait_for(process.communicate(), timeout=5)
                    await asyncio.sleep(0)
        steps=[('Browser-Treiber',lambda:self.runner.run(cleanup()))] if tab is not None or browser is not None else []
        if bridge is not None:steps.append(('Proxy-Brücke',bridge.close))
        self.close_steps(handle, steps)
