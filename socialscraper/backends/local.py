import json
import logging
from pathlib import Path
from .base import BrowserBackend, SessionHandle, cleanup_steps
from ..net.fingerprint import accept_language, chrome_major, desktop_user_agent, mobile_user_agent

log = logging.getLogger(__name__)

def _spoof_script(fp):
    """Setzt je Identity Hardware-Kerne, Speicher, WebGL-Kennung, Canvas- und Audio-Rauschen (Init-Skript,
    laeuft vor jedem Seiten-Skript). Additiv und stabil: gleiche Identity -> gleiches Ergebnis."""
    cfg = {'hc': fp.hardware_concurrency, 'dm': fp.device_memory,
           'glv': fp.webgl_vendor, 'glr': fp.webgl_renderer,
           'cn': int(round(fp.canvas_noise * 100000)), 'an': fp.audio_noise}
    return """
(() => {
  const c = %s;
  const def = (o,p,v) => { try { Object.defineProperty(o,p,{get:()=>v,configurable:true}); } catch(e){} };
  if (c.hc) def(navigator, 'hardwareConcurrency', c.hc);
  if (c.dm) def(navigator, 'deviceMemory', c.dm);
  const gl = (P) => { if (!P) return; const g = P.getParameter;
    P.getParameter = function(p){ if(p===37445) return c.glv; if(p===37446) return c.glr; return g.apply(this,arguments); }; };
  gl(window.WebGLRenderingContext && WebGLRenderingContext.prototype);
  gl(window.WebGL2RenderingContext && WebGL2RenderingContext.prototype);
  if (c.cn && window.HTMLCanvasElement) { const td = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = function(){ try { const x=this.getContext('2d');
      if (x && !this.__n){ this.__n=1; const d=x.getImageData(0,0,this.width,this.height);
        for(let i=0;i<d.data.length;i+=4){ d.data[i]=(d.data[i]+(c.cn%%3))&255; } x.putImageData(d,0,0); } } catch(e){}
      return td.apply(this,arguments); }; }
  if (c.an && window.AnalyserNode) { const fd = AnalyserNode.prototype.getFloatFrequencyData;
    AnalyserNode.prototype.getFloatFrequencyData = function(a){ fd.call(this,a);
      for(let i=0;i<a.length;i++){ a[i]+= (c.an*(i%%7)); } }; }
})();
""" % json.dumps(cfg)

class LocalBackend(BrowserBackend):
    name = "local"

    def __init__(self, engine="patchright", profiles_dir="data/profiles"):
        if engine != "patchright":
            raise ValueError(f"Unbekannte lokale Browser-Engine: {engine}")
        self.engine, self.profiles = engine, Path(profiles_dir)
        self.profiles.mkdir(parents=True, exist_ok=True)
        self._pw = None
        self._users = 0
        self._chrome_version = None

    def open(self, identity, proxy, *, headless=False, platform=None):
        import os
        if os.environ.get('SS_REQUIRE_PROXY') == '1' and proxy is None:
            raise ValueError('Dieser Test benötigt einen Proxy oder Tor; eine direkte Verbindung ist gesperrt.')
        from patchright.sync_api import sync_playwright
        pw = None
        handle = SessionHandle(identity, proxy, self.name, platform=platform)
        try:
            from .profile_store import begin
            handle._profile_selection = begin(self.profiles, identity.name, platform)
            profile = handle._profile_selection.path
            if self._pw is None:
                self._pw = sync_playwright().start()
            pw = self._pw
            self._users += 1
            handle._pw = pw
            from ..net.browserproxy import options
            browser_proxy, handle._proxy_bridge = options(proxy)
            fp = identity.fp
            extra = {}
            embedded = os.environ.get('SS_EMBED_BROWSER') == '1' and not headless
            viewport = fp.viewport
            screen = fp.screen
            if embedded:
                if fp.is_mobile:
                    viewport = {**fp.viewport, 'height':min(fp.viewport['height'],420)}
                else:
                    # Chrome follows the size of its native UI host for desktop forms.
                    viewport, screen = None, None
            version = None
            ua_args = []
            if fp.is_mobile:
                version = self._installed_version(pw, browser_proxy)
                ua_args = [f"--user-agent={mobile_user_agent(fp, version)}"]
            elif headless:
                try:
                    ua = desktop_user_agent(self._installed_version(pw, browser_proxy))
                except ValueError:
                    log.warning("Chrome-Version unbekannt; der Headless-Zusatz im User-Agent bleibt")
                else:
                    extra["user_agent"] = ua
                    ua_args = [f"--user-agent={ua}"]
            handle._context = pw.chromium.launch_persistent_context(
                user_data_dir=str(profile),
                timeout=60000,
                headless=headless, channel="chrome",
                proxy=browser_proxy,
                viewport=viewport, screen=screen, **extra,
                device_scale_factor=fp.device_scale if viewport else None,
                timezone_id=fp.timezone,
                permissions=[], color_scheme="light",
                is_mobile=fp.is_mobile, has_touch=fp.is_mobile,
                args=["--disable-dev-shm-usage", "--no-first-run",
                      "--no-default-browser-check", f"--lang={fp.locale}",
                      f"--accept-lang={accept_language(fp)}", *ua_args,
                      *self._proxy_args(browser_proxy)])
            # Geraetekennung je Identity VOR dem ersten goto setzen (Kerne, Speicher, GPU, Canvas, Audio).
            handle._context.add_init_script(_spoof_script(fp))
            if fp.is_mobile:
                self._emulate_mobile(handle._context, fp, version)
            self._apply_cookies(handle, platform)
            self.observe_settings(handle)
            from ..net.browser_window import announce
            try:
                announce(handle,headless)
            except Exception:
                log.warning('Browserfenster bleibt separat geöffnet; Einbettung konnte nicht vorbereitet werden.',exc_info=True)
            from ..window_notification import window_opened
            window_opened(handle,headless=headless)
            return handle
        except BaseException as error:
            if pw is not None and pw is self._pw and driver_dead(error):
                self._reset_driver(pw)
            self._close_after_failure(handle)
            raise

    def _emulate_mobile(self, context, fp, version):
        """Android-Handy: User-Agent, Plattform UND Client Hints zusammen ueber die Browser-Emulation setzen,
        damit sie sich nicht widersprechen (ein reiner User-Agent-Tausch liesse die Hints auf 'Windows')."""
        major = chrome_major(version)
        override = {
            "userAgent": mobile_user_agent(fp, version), "platform": fp.platform,
            "acceptLanguage": accept_language(fp),
            "userAgentMetadata": {
                "brands": [{"brand": "Chromium", "version": major}, {"brand": "Google Chrome", "version": major},
                           {"brand": "Not A(Brand", "version": "99"}],
                "fullVersionList": [{"brand": "Chromium", "version": str(version)},
                                    {"brand": "Google Chrome", "version": str(version)},
                                    {"brand": "Not A(Brand", "version": "99.0.0.0"}],
                "platform": "Android", "platformVersion": fp.platform_version or "14.0.0",
                "architecture": "", "model": fp.model or "SM-S911B", "mobile": True}}
        def apply(page):
            try:
                context.new_cdp_session(page).send("Emulation.setUserAgentOverride", override)
            except Exception:
                log.warning("Handy-Emulation konnte für eine Seite nicht gesetzt werden", exc_info=True)
        for page in context.pages:
            apply(page)
        context.on("page", apply)

    @staticmethod
    def _proxy_args(proxy):
        if not proxy:
            return []
        from urllib.parse import urlsplit
        host = urlsplit(proxy['server']).hostname
        return ['--disable-quic', '--proxy-bypass-list=<-loopback>',
                '--force-webrtc-ip-handling-policy=disable_non_proxied_udp',
                f'--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE {host}, EXCLUDE localhost']

    def _installed_version(self, pw, proxy=None):
        if self._chrome_version is None:
            browser = pw.chromium.launch(channel="chrome", headless=True, proxy=proxy,
                                         args=self._proxy_args(proxy), timeout=30000)
            try:
                self._chrome_version = browser.version
            finally:
                browser.close()
        return self._chrome_version

    def close(self, handle):
        from ..net.browser_window import closed
        ctx,pw,bridge=handle._context,handle._pw,getattr(handle,'_proxy_bridge',None)
        handle._context = handle._pw = None
        handle._proxy_bridge=None
        steps=[('Schließmeldung',lambda:closed(handle))]
        def close_context():
            try:ctx.close()
            except Exception as error:
                if pw is not None and pw is self._pw and driver_dead(error):self._reset_driver(pw)
                raise
        if ctx is not None:steps.append(('Browserkontext',close_context))
        if bridge is not None:steps.append(('Proxy-Brücke',bridge.close))
        if pw is not None:steps.append(('Browser-Treiber',lambda:self._release(pw)))
        self.close_steps(handle, steps)

    def _release(self, pw):
        if pw is not self._pw:
            return   # Treiber wurde schon als tot verworfen; der Zähler gehört dem neuen Treiber
        self._users -= 1
        if self._users <= 0:
            self._pw = None
            self._chrome_version = None
            pw.stop()

    def _reset_driver(self, pw):
        """Der Playwright-Treiber (node.exe) ist tot, z. B. nach dem Zeitlimit beendet. Er bedient ALLE Sitzungen:
        Ohne diesen Schritt bliebe er für die übrigen Sitzungen stehen, und jeder weitere Abruf scheiterte, bis die
        letzte alte Sitzung geschlossen wird. Der nächste open() startet einen frischen Treiber."""
        log.warning("Browser-Treiber antwortet nicht mehr (vermutlich nach einem Zeitlimit beendet); "
                    "beim nächsten Abruf wird er neu gestartet")
        self._pw, self._users = None, 0
        self._chrome_version = None
        try:
            pw.stop()
        except Exception:
            log.debug("Alter Browser-Treiber konnte nicht angehalten werden", exc_info=True)


def driver_dead(error):
    """True, wenn der Fehler sagt, dass die Verbindung zum Playwright-Treiber weg ist (nicht nur eine Seite zu)."""
    return "closed while reading from the driver" in str(error).lower()