"""Cookie import and identity/platform-scoped persistence."""
import json
import logging
import math
import os
from pathlib import Path
import re
import tempfile
import time
from urllib.parse import urlsplit

log = logging.getLogger(__name__)
BASE = Path("data/cookies")
AUTH = {"tiktok": ("sessionid", "sessionid_ss"), "instagram": ("sessionid",),
        "reddit": ("reddit_session", "token_v2"), "x": ("auth_token", "ct0"),
        "google": ("SAPISID", "__Secure-3PSID", "SID", "__Secure-1PSID"),
        "youtube": ("SAPISID", "__Secure-3PSID", "SID", "__Secure-1PSID"),
        "trustpilot": ("__Host-jwt", "jwt")}
# X gilt erst als angemeldet, wenn auth_token UND ct0 da sind; bei den anderen genügt eines der Cookies.
AUTH_ALL = {"x"}
DOMAINS = {"tiktok": "tiktok.com", "instagram": "instagram.com", "reddit": "reddit.com", "x": "x.com",
           "google": "google.com", "youtube": "youtube.com", "trustpilot": "trustpilot.com"}
# Weitere Adressen derselben Plattform (X heißt auch twitter.com); der Hauptname steht in DOMAINS.
EXTRA_DOMAINS = {"x": ("twitter.com",), "youtube": ("google.com",)}
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def _key(value, kind):
    if (not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value)
            or value.endswith(".") or value.split(".", 1)[0].upper() in _RESERVED):
        raise ValueError(f"Ungültiger {kind}; nur sichere Namen ohne Pfadbestandteile verwenden")
    return value


def _bool(value, default):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.lower() in ("true", "false"):
        return value.lower() == "true"
    raise ValueError("Ungültiger boolescher Cookie-Wert")


def _norm(cookie):
    if not isinstance(cookie, dict):
        raise ValueError("Cookie muss ein Objekt sein")
    name, value = cookie.get("name"), cookie.get("value", "")
    if (not isinstance(name, str) or not name or any(c in name for c in "\r\n\t;=")
            or not isinstance(value, str) or any(c in value for c in "\r\n\0")):
        raise ValueError("Cookie-Name und Cookie-Wert müssen gültige Zeichenketten sein")
    domain = cookie.get("domain") or ""
    path = cookie.get("path") or "/"
    if not isinstance(domain, str) or not isinstance(path, str) or not path.startswith("/"):
        raise ValueError("Ungültige Cookie-Domain oder Cookie-Pfad")
    if not domain and cookie.get("url"):
        domain = urlsplit(cookie["url"]).hostname or ""
    out = {"name": name, "value": value, "domain": domain.lower(), "path": path,
           "secure": _bool(cookie.get("secure"), True),
           "httpOnly": _bool(cookie.get("httpOnly"), False)}
    expiry = cookie.get("expires")
    if expiry is None or expiry == -1:
        expiry = cookie.get("expirationDate")
    if expiry is not None:
        if isinstance(expiry, bool):
            raise ValueError("Ungültiger Cookie-Ablaufzeitpunkt")
        try:
            expiry = float(expiry)
        except (TypeError, ValueError):
            raise ValueError("Ungültiger Cookie-Ablaufzeitpunkt") from None
        if not math.isfinite(expiry):
            raise ValueError("Ungültiger Cookie-Ablaufzeitpunkt")
        if expiry > 0:
            out["expires"] = expiry
        elif expiry not in (0, -1):
            raise ValueError("Ungültiger Cookie-Ablaufzeitpunkt")
    same_site = str(cookie.get("sameSite") or "").lower()
    same_sites = {"strict": "Strict", "lax": "Lax", "none": "None", "no_restriction": "None"}
    if same_site in same_sites:
        out["sameSite"] = same_sites[same_site]
    return out


def _roots(platform):
    return (DOMAINS[platform], *EXTRA_DOMAINS.get(platform, ()))


def _in_roots(host, roots):
    return any(host == root or host.endswith("." + root) for root in roots)


def _raw_host(cookie):
    """Domain eines Rohcookies tolerant lesen (ohne Prüfung); None, wenn sie sich nicht lesen lässt."""
    if not isinstance(cookie, dict):
        return None
    domain = cookie.get("domain")
    if not domain:
        url = cookie.get("url")
        if isinstance(url, str) and url:
            try:
                domain = urlsplit(url).hostname or ""
            except ValueError:
                return None
        else:
            domain = ""
    if not isinstance(domain, str):
        return None
    return domain.lower().lstrip(".")


def parse_any(text):
    """Read Cookie-Editor/Playwright JSON, Netscape files, or a Cookie header."""
    if not isinstance(text, str):
        raise ValueError("Cookie-Import muss Text sein")
    text = text.strip()
    if not text:
        return []
    if text.startswith(("[", "{")):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            raise ValueError("Cookie-JSON ist ungültig") from None
        if isinstance(data, dict):
            data = data.get("cookies", [data])
        if not isinstance(data, list):
            raise ValueError("Cookie-JSON muss eine Liste von Cookies enthalten")
        return [_norm(cookie) for cookie in data]
    out = []
    if "\t" in text:
        for line in text.splitlines():
            http_only = line.startswith("#HttpOnly_")
            if http_only:
                line = line[len("#HttpOnly_"):]
            if not line.strip() or line.startswith("#"):
                continue
            fields = line.split("\t", 6)
            if len(fields) != 7:
                raise ValueError("Ungültige Netscape-Cookie-Zeile")
            out.append(_norm({"name": fields[5], "value": fields[6], "domain": fields[0],
                              "path": fields[2], "secure": fields[3].lower(),
                              "expires": fields[4], "httpOnly": http_only}))
        return out
    if text.lower().startswith("cookie:"):
        text = text[len("cookie:"):].strip()
    for pair in re.split(r"[;\n]", text):
        if not pair.strip():
            continue
        name, separator, value = pair.strip().partition("=")
        if not separator:
            raise ValueError("Ungültiger Cookie-Header")
        out.append(_norm({"name": name, "value": value}))
    return out


class CookieJar:
    @staticmethod
    def path(identity, platform, base_dir=None):
        identity = _key(identity, "Identity-Name")
        platform = _key(platform, "Plattform-Name")
        if platform not in DOMAINS:
            raise ValueError("Cookie-Plattform wird nicht unterstützt")
        return Path(BASE if base_dir is None else base_dir) / f"{identity}.{platform}.json"

    @staticmethod
    def for_platform(cookies, platform, strict=False):
        """Cookies der Plattform aus einer Rohliste (z. B. aus dem Browser).

        Erst nach Domain filtern: fremde Cookies werden nie geprüft und nie gemeldet. Ein ungültiger Cookie der
        eigenen Plattform wird übersprungen und gezählt (Warnung ohne Namen und Werte), statt die ganze Liste zu
        verwerfen. Mit strict=True (Import) bleibt jeder ungültige Cookie ein ValueError.
        """
        if platform not in DOMAINS:
            raise ValueError("Cookie-Plattform wird nicht unterstützt")
        if not isinstance(cookies, list):
            raise ValueError("Cookies müssen eine Liste sein")
        root = DOMAINS[platform]
        roots = _roots(platform)
        now = time.time()
        out = []
        skipped = 0
        for raw in cookies:
            if not strict:
                raw_host = _raw_host(raw)
                if raw_host and not _in_roots(raw_host, roots):
                    continue
                try:
                    cookie = _norm(raw)
                except ValueError:
                    skipped += 1
                    continue
            else:
                cookie = _norm(raw)
            host = cookie["domain"].lstrip(".")
            if host and not _in_roots(host, roots):
                continue
            if cookie.get("expires", float("inf")) <= now:
                continue
            if not host:
                cookie["domain"] = "." + root
            out.append(cookie)
        if skipped:
            log.warning("%s ungültige Cookies für %s übersprungen", skipped, platform)
        return out

    @staticmethod
    def save(identity, platform, cookies, base_dir=None):
        path = CookieJar.path(identity, platform, base_dir)
        data = CookieJar.for_platform(cookies, platform)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             prefix=".cookies-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @staticmethod
    def load(identity, platform, base_dir=None):
        path = CookieJar.path(identity, platform, base_dir)
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return CookieJar.for_platform(data, platform)
        except (OSError, UnicodeError, ValueError, TypeError):
            log.warning("Cookie-Datei konnte nicht gelesen werden; erneute Anmeldung erforderlich")
            return []

    @staticmethod
    def clear(identity, platform, base_dir=None):
        CookieJar.path(identity, platform, base_dir).unlink(missing_ok=True)

    @staticmethod
    def is_authenticated(identity, platform, base_dir=None):
        return CookieJar.authenticated(CookieJar.load(identity, platform, base_dir), platform)

    @staticmethod
    def authenticated(cookies, platform):
        names = {cookie["name"] for cookie in CookieJar.for_platform(cookies, platform) if cookie["value"]}
        wanted = AUTH.get(platform, ())
        if platform in AUTH_ALL:
            return bool(wanted) and all(name in names for name in wanted)
        return any(name in names for name in wanted)
