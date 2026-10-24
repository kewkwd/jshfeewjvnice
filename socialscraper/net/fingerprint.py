"""Identitaet pro Konto: Zeitzone, Sprache, Fenstergroesse und mobile Browser-Emulation.

Grundsatz: Auf dem Desktop wird NICHTS erfunden, was der echte Chrome selbst kennt. Grafikkarte, Prozessorkerne,
Geraetespeicher, Plugins und Plattform bleiben die echten Werte des Rechners (sonst widersprechen sie den
Client Hints und dem Worker-Kontext). Die gespeicherten Felder `hardware_concurrency`, `device_memory`,
`webgl_*`, `canvas_seed`, `platform` und `user_agent` bleiben nur aus Gruenden der Abwaertskompatibilitaet
bestehender Identitaetsdateien und beschreiben den Stand bei der Anlage; die Desktop-Starts wenden sie nicht an.
Auch `geolocation` bleibt als Kompatibilitaetsfeld erhalten, wird aber in keinem lokalen Browserstart angewandt.
Ein Browserprofil oder seine Region belegt weder einen physischen Standort noch ein echtes Android-Geraet.
"""
import hashlib, random, re, sys
from dataclasses import dataclass, asdict, fields

GEO = {
 "us": ("America/New_York","en-US",["en-US","en"],(40.71,-74.00)),
 "gb": ("Europe/London","en-GB",["en-GB","en"],(51.51,-0.13)),
 "de": ("Europe/Berlin","de-DE",["de-DE","de","en"],(52.52,13.40)),
 "fr": ("Europe/Paris","fr-FR",["fr-FR","fr","en"],(48.86,2.35)),
 "br": ("America/Sao_Paulo","pt-BR",["pt-BR","pt","en"],(-23.55,-46.63)),
 "in": ("Asia/Kolkata","en-IN",["en-IN","en","hi"],(19.08,72.88)),
 "id": ("Asia/Jakarta","id-ID",["id-ID","id","en"],(-6.21,106.85)),
 "ph": ("Asia/Manila","en-PH",["en-PH","en"],(14.60,120.98)),
 "vn": ("Asia/Ho_Chi_Minh","vi-VN",["vi-VN","vi","en"],(21.03,105.85)),
 "jp": ("Asia/Tokyo","ja-JP",["ja-JP","ja","en"],(35.68,139.69)),
}

# Betriebssystem des Rechners -> (Teil im User-Agent, navigator.platform, Client-Hints-Plattform)
OS_PROFILES = {
 "windows": ("Windows NT 10.0; Win64; x64", "Win32", "Windows"),
 "linux": ("X11; Linux x86_64", "Linux x86_64", "Linux"),
 "macos": ("Macintosh; Intel Mac OS X 10_15_7", "MacIntel", "macOS"),
}
_MOBILE_UA = ("Mozilla/5.0 (Linux; Android 14; SM-S911B) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/{chrome} Mobile Safari/537.36")
_DESKTOP_UA = "Mozilla/5.0 ({os}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{chrome} Safari/537.36"
_CHROME_TOKEN = re.compile(r"Chrome/(?:[\d.]+|\{chrome\})")

@dataclass
class Fingerprint:
    identity: str; user_agent: str; platform: str; viewport: dict; screen: dict
    timezone: str; locale: str; languages: list; geolocation: dict
    hardware_concurrency: int; device_memory: int
    webgl_vendor: str; webgl_renderer: str
    canvas_seed: int; color_depth: int; device_scale: float; is_mobile: bool
    def to_dict(self): return asdict(self)

    @classmethod
    def from_dict(cls, data):
        """Aus einer gespeicherten Identitaetsdatei laden; unbekannte Felder werden ignoriert (Vor-/Rueckwaertskompatibilitaet)."""
        known = {f.name for f in fields(cls)}
        return cls(**{key: value for key, value in dict(data).items() if key in known})

def host_os(platform=None):
    """'windows' | 'linux' | 'macos' - das Betriebssystem, auf dem Chrome wirklich laeuft."""
    name = (platform or sys.platform).lower()
    if name.startswith("win"): return "windows"
    if name.startswith("darwin") or name.startswith("mac"): return "macos"
    return "linux"

def chrome_major(chrome_version):
    major = str(chrome_version).split(".", 1)[0]
    if not major.isdigit():
        raise ValueError("Chrome-Version konnte nicht ermittelt werden")
    return major

def desktop_user_agent(chrome_version, platform=None):
    """User-Agent wie ihn der installierte Chrome ohne Kopf-Zusatz sendet (reduzierte Form `Chrome/<major>.0.0.0`)."""
    return _DESKTOP_UA.format(os=OS_PROFILES[host_os(platform)][0], chrome=f"{chrome_major(chrome_version)}.0.0.0")

def mobile_user_agent(fp: Fingerprint, chrome_version: str) -> str:
    """Keep the stored mobile UA in line with the Chrome that actually runs.

    Chrome reports its real version in the Sec-CH-UA headers; a fixed version in
    the UA string would contradict them after every Chrome update.
    """
    return _CHROME_TOKEN.sub(f"Chrome/{chrome_major(chrome_version)}.0.0.0", fp.user_agent)

def accept_language(fp: Fingerprint) -> str:
    """Wert fuer `--accept-lang`: Chrome macht daraus navigator.languages UND den Accept-Language-Kopf (mit q-Werten)."""
    return ",".join(fp.languages)

def build(identity: str, country: str | None, mobile: bool = False) -> Fingerprint:
    rng = random.Random(hashlib.sha256(identity.encode()).digest())
    tz, locale, langs, (lat, lon) = GEO.get((country or "us").lower(), GEO["us"])
    if mobile:
        vp = rng.choice([{"width":390,"height":844},{"width":393,"height":851},{"width":412,"height":915}])
        # Die Chrome-Version wird erst beim Start aus dem installierten Chrome eingesetzt.
        ua = _MOBILE_UA
        platform, vendor, renderer = "Linux armv8l", "Qualcomm", "Adreno (TM) 740"
    else:
        vp = rng.choice([{"width":1920,"height":1080},{"width":1536,"height":864},{"width":1680,"height":1050}])
        ua = _DESKTOP_UA.format(os=OS_PROFILES[host_os()][0], chrome="{chrome}")
        platform = OS_PROFILES[host_os()][1]
        vendor = renderer = ""   # echte Grafikkarte des Rechners, nichts erfunden
    return Fingerprint(
        identity=identity, user_agent=ua, platform=platform, viewport=vp,
        screen={"width":vp["width"],"height":vp["height"]+rng.choice([0,40,80])},
        timezone=tz, locale=locale, languages=list(langs),
        geolocation={"latitude":lat+rng.uniform(-.4,.4),"longitude":lon+rng.uniform(-.4,.4),"accuracy":50},
        hardware_concurrency=rng.choice([4,8,8,12,16]), device_memory=rng.choice([8,8,16,16,32]),
        webgl_vendor=vendor, webgl_renderer=renderer, canvas_seed=rng.randint(1,2**31),
        color_depth=24, device_scale=rng.choice([1.0,1.0,1.25,1.5]), is_mobile=mobile)
