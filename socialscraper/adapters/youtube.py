import importlib
from importlib import metadata
import logging
import os
import re
import shutil
import subprocess
from .base import Adapter, checkpoint
from ..fetch_result import FetchResult, received_comments
from ..models import Video, Comment, iso
from ..net import errors, proxymode
from ..net.retry import PermanentFetchError, with_retry
from ..util import comment_id, comment_limit

log = logging.getLogger(__name__)

MIN_YT_DLP_VERSION = "2026.8.19"
# yt-dlp messages for videos that stay unavailable; transient errors are retried.
PERMANENT_ERRORS = ("private video", "video unavailable", "this video is unavailable", "this video has been removed",
                    "this video is not available", "account associated with this video has been terminated",
                    "join this channel", "members-only", "sign in to confirm your age",
                    "unsupported url", "is not a valid url", "incomplete youtube id")
# Hinweise auf eine vorübergehende Sperre (Rate-Limit, Bot-Abfrage). Sie gelten NIE als dauerhaft, auch wenn die
# Meldung zusätzlich „Video unavailable“ enthält (yt-dlp: „Video unavailable. This content isn't available, try
# again later. The current session has been rate-limited ...“): sonst greifen Wiederholung und Proxy-Wechsel nicht.
TRANSIENT_HINTS = ("rate-limited", "rate limited", "rate limit", "try again later", "too many requests",
                   "confirm you're not a bot", "confirm you are not a bot", "not a bot")


def is_permanent_error(message):
    """True, wenn die yt-dlp-Meldung ein dauerhaft nicht abrufbares Video beschreibt (kein vorübergehender Fehler)."""
    low = str(message).lower().replace("’", "'")
    if any(hint in low for hint in TRANSIENT_HINTS):
        return False
    return any(marker in low for marker in PERMANENT_ERRORS)


class _QuietLogger:
    """Leitet yt-dlp-Ausgaben nur ins Protokoll (Stufe DEBUG); die deutschen Meldungen kommen von errors.explain."""
    def __init__(self):
        self.comment_warning = False
        self.comments_disabled = False   # „Youtube said: Comments are turned off“ – dauerhaft keine Kommentare

    def debug(self, message): log.debug("yt-dlp: %s", message)
    info = debug

    def warning(self, message):
        self.debug(message)
        low = str(message).lower()
        if "comment" in low:
            if "turned off" in low or "disabled" in low:
                self.comments_disabled = True
            else:
                self.comment_warning = True

    def error(self, message): log.debug("yt-dlp Fehler: %s", message)


class YouTubeSetupError(ValueError):
    """A local setup problem must not trigger network retries."""


def _check_youtube_packages():
    try:
        from packaging.requirements import Requirement
        from packaging.version import Version

        ytdlp_version = Version(metadata.version("yt-dlp"))
        ejs_version = Version(metadata.version("yt-dlp-ejs"))
        importlib.import_module("yt_dlp_ejs")
    except (ImportError, metadata.PackageNotFoundError) as error:
        raise YouTubeSetupError(
            "YouTube-Komponenten fehlen. Bitte Einrichten.ps1 ausführen oder "
            "die requirements.txt in der Python-Umgebung installieren."
        ) from error
    if ytdlp_version < Version(MIN_YT_DLP_VERSION):
        raise YouTubeSetupError(
            f"yt-dlp {ytdlp_version} ist zu alt. Mindestens {MIN_YT_DLP_VERSION} "
            "wird benötigt; bitte die requirements.txt erneut installieren."
        )
    requirements = [Requirement(text) for text in metadata.requires("yt-dlp") or []]
    ejs_requirements = [requirement for requirement in requirements
                        if requirement.name.lower().replace("_", "-") == "yt-dlp-ejs"
                        and (requirement.marker is None
                             or requirement.marker.evaluate({"extra": "default"}))]
    if not ejs_requirements or not all(ejs_version in item.specifier for item in ejs_requirements):
        raise YouTubeSetupError(
            f"yt-dlp-ejs {ejs_version} passt nicht zur installierten yt-dlp-Version. "
            "Bitte beide gemeinsam über die requirements.txt aktualisieren."
        )


def youtube_runtime_options():
    """Select an installed, supported runtime; never fetch runtime components."""
    _check_youtube_packages()
    diagnostics = []
    for name, minimum in (("node", (22, 0, 0)), ("deno", (2, 3, 0))):
        path = shutil.which(name)
        if not path:
            diagnostics.append(f"{name} fehlt im PATH")
            continue
        try:
            result = subprocess.run(
                [path, "--version"], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=5, check=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            diagnostics.append(f"{name} konnte nicht gestartet werden ({type(error).__name__})")
            continue
        pattern = r"^v?(\d+)\.(\d+)\.(\d+)(?:\s|$)" if name == "node" else r"^deno\s+(\d+)\.(\d+)\.(\d+)(?:\s|$)"
        match = re.match(pattern, result.stdout.strip())
        if result.returncode != 0 or not match:
            diagnostics.append(f"{name}: Version konnte nicht ermittelt werden")
            continue
        version = tuple(map(int, match.groups()))
        if version < minimum:
            diagnostics.append(f"{name} {'.'.join(map(str, version))} ist zu alt")
            continue
        log.info("YouTube-JavaScript: %s %s", name, '.'.join(map(str, version)))
        return {"js_runtimes": {name: {"path": path}}, "remote_components": []}
    raise YouTubeSetupError(
        "YouTube benötigt Node.js ab 22 oder Deno ab 2.3. "
        "Bitte installieren und ein neues Terminal öffnen. " + "; ".join(diagnostics)
    )


def _extract(ydl, url):
    """Videodaten holen und die Kommentarzahl der Seite retten. Nach dem Kommentarabruf ersetzt yt-dlp
    `comment_count` durch die Zahl der geholten Kommentare (bei erreichter Grenze also die Grenze) oder bei
    einer Störung durch None (yt_dlp extract_comments -> info.update). Deshalb zuerst unverarbeitet lesen."""
    raw = ydl.extract_info(url, download=False, process=False)
    if not isinstance(raw, dict):
        return raw, None
    page_count = raw.get("comment_count")
    return ydl.process_ie_result(raw, download=False), page_count


def _comment_total(page_count, info, limit):
    """Kommentarzahl des Videos: die der Seite; fehlt sie, die geholte Zahl nur, wenn sie unter der Grenze blieb."""
    if page_count is not None:
        return page_count
    fetched = info.get("comment_count")
    return fetched if fetched is not None and fetched < limit else None


def search_url(query):
    """Suchseite „Videos, neueste zuerst“. (`ytsearchdate` gibt es in yt-dlp nicht mehr; der Sortier-Parameter
    `sp=CAISAhAB` = Hochladedatum + nur Videos.)"""
    from urllib.parse import quote_plus
    return f"https://www.youtube.com/results?search_query={quote_plus(query)}&sp=CAISAhAB"


class YouTubeAdapter(Adapter):
    platform = "youtube"
    domains = ("youtube.com", "youtu.be")

    @staticmethod
    def _proxy_for(ctx):
        """Proxy für diesen Abruf (None = direkt). Bei gewähltem Proxy-Modus nie stillschweigend ohne Proxy."""
        if not proxymode.uses_proxy(ctx.cfg):
            return None
        if ctx.im is None:
            raise ValueError("Konfigurierter YouTube-Proxy benötigt einen IdentityManager")
        proxy = ctx.im.proxy_for(ctx.im.get(ctx.identity, ctx.country, ctx.mobile))
        if proxy is None:
            raise ValueError("Kein konfigurierter YouTube-Proxy verfügbar")
        return proxy

    def _opts(self, cfg):
        limit = comment_limit(cfg)
        o = {"quiet": True, "no_warnings": True, "skip_download": True,
             "getcomments": limit > 0, "ignoreerrors": False, "noplaylist": True,
             "extractor_args": {"youtube": {
                 "comment_sort": ["top"],
                 "max_comments": [str(limit)]}},
             "socket_timeout": cfg.request_timeout,
             "logger": _QuietLogger()}   # yt-dlp schreibt keine englischen Fehlerzeilen auf die Konsole
        if cfg.impersonate:
            try:
                import curl_cffi  # noqa
                from yt_dlp.networking.impersonate import ImpersonateTarget
            except ImportError:
                log.warning("YouTube-Impersonation deaktiviert: curl_cffi oder eine aktuelle yt-dlp-Version fehlt")
            else:
                o["impersonate"] = ImpersonateTarget.from_str(cfg.impersonate)
        return o

    @with_retry(times=3, backoff=2.0)
    def fetch(self, url, ctx):
        cfg = ctx.cfg
        limit = comment_limit(cfg)
        runtime_options = youtube_runtime_options()
        import yt_dlp

        options = self._opts(cfg)
        options.update(runtime_options)
        if cfg.impersonate and "impersonate" not in options:
            raise YouTubeSetupError("YouTube-Impersonation benötigt curl_cffi. Bitte requirements.txt installieren.")
        # Kostenlose Proxys sind unzuverlässig: bei einem Fehler Proxy sperren und mit dem nächsten wiederholen.
        tries = 4 if proxymode.is_anonymous(cfg) else 1
        for attempt in range(tries):
            options["logger"].comment_warning = options["logger"].comments_disabled = False
            proxy = self._proxy_for(ctx)
            if proxy is not None:
                errors.precheck_local_proxy(proxy)
                options["proxy"] = proxymode.ytdlp_proxy_url(proxy)
                if proxymode.is_anonymous(cfg):  # lahme Proxys schnell aufgeben, der nächste kommt dran
                    options.update(socket_timeout=min(cfg.request_timeout, 10), extractor_retries=0)
            try:
                with yt_dlp.YoutubeDL(options) as ydl:
                    info, page_count = _extract(ydl, url)
                if proxymode.is_anonymous(cfg):
                    proxymode.note_success(ctx.im, proxy)
                break
            except yt_dlp.utils.DownloadError as error:
                if is_permanent_error(error):
                    raise PermanentFetchError(f"YouTube-Video nicht abrufbar: {errors.explain(error)}") from None
                if proxymode.is_anonymous(cfg):
                    proxymode.note_failure(ctx.im, proxy)
                    if attempt + 1 < tries:
                        log.warning("YouTube über diesen Proxy fehlgeschlagen; nächster Proxy")
                        continue
                raise errors.wrap(error) from error   # deutsche Meldung; Rohtext bei Logstufe DEBUG
        if not info:
            raise ValueError("YouTube hat keine Videodaten zurückgegeben; der Abruf ist fehlgeschlagen")
        if not isinstance(info, dict) or info.get("entries") is not None or not info.get("id"):
            raise ValueError("YouTube-URL muss ein einzelnes Video bezeichnen")

        v = Video.from_dict({
            "platform": "youtube", "video_id": info["id"],
            "url": info.get("webpage_url") or url,
            "author": info.get("uploader"), "author_id": info.get("channel_id"),
            "caption": info.get("description"), "created_at": iso(info.get("timestamp")),
            "duration_s": info.get("duration"), "view_count": info.get("view_count"),
            "like_count": info.get("like_count"), "comment_count": _comment_total(page_count, info, limit),
            "hashtags": info.get("tags") or [], "language": info.get("language"),
            "thumbnail": info.get("thumbnail"),
            "raw": {"categories": info.get("categories"), "upload_date": info.get("upload_date")},
        })
        checkpoint(ctx, v, [])

        comments, seen = [], set()
        for c in info.get("comments") or []:
            if len(comments) >= limit:
                break
            cid = comment_id(info["id"], c.get("author_id") or c.get("author"),
                             c.get("text"), c.get("id"))
            if cid in seen:
                continue
            seen.add(cid)
            parent = c.get("parent")
            comments.append(Comment.from_dict({
                "platform": "youtube", "video_id": info["id"],
                "comment_id": cid, "text": c.get("text") or "",
                "author": c.get("author"), "author_id": c.get("author_id"),
                "created_at": iso(c.get("timestamp")),
                "like_count": c.get("like_count"),
                "parent_id": None if parent in (None, "root") else parent,
                "is_author_reply": bool(c.get("author_is_uploader")),
                "raw": {"is_favorited": c.get("is_favorited")},
            }))
        log.info("YouTube %s: %s Kommentare", info["id"], len(comments))
        if comments:
            checkpoint(ctx, v, comments)
        if not limit:
            return FetchResult(v, [], "not_requested", "Nur Videodaten angefordert.")
        if options["logger"].comments_disabled and not comments:
            # Kein vorübergehendes Problem: Der Kanal hat die Kommentare abgeschaltet. Sonst würde der Planer das Video
            # bei jedem Lauf als unvollständig melden und erneut versuchen.
            return FetchResult(v, [], "empty", "YouTube: Die Kommentare sind für dieses Video ausgeschaltet.")
        if options["logger"].comment_warning:
            return FetchResult(v, comments, "partial" if comments else "unavailable",
                               "YouTube meldet ein Problem beim Kommentarabruf. Später erneut versuchen.")
        if comments:
            return received_comments(v, comments, limit)
        if v.comment_count == 0:
            return FetchResult(v, [], "empty", "YouTube meldet keine Kommentare.")
        return FetchResult(v, [], "unavailable", "YouTube hat keine Kommentare geliefert. "
                           "Das belegt keine leere Kommentarliste. Später erneut versuchen.")

    @with_retry(times=3, backoff=2.0)
    def search_urls(self, query, ctx, limit=20):
        """Neueste Videos zu einem Suchbegriff (`#tag` für Hashtags) als Links; nur Metadaten, kein Download."""
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise ValueError("limit muss eine nichtnegative ganze Zahl sein")
        query = (query or "").strip()
        if not query:
            raise ValueError("Suchbegriff fehlt")
        if not limit:
            return []
        cfg = ctx.cfg
        runtime_options = youtube_runtime_options()
        import yt_dlp

        options = self._opts(cfg)
        options.update(runtime_options)
        options.update({"extract_flat": "in_playlist", "getcomments": False, "noplaylist": False,
                        "playlist_items": f"1-{limit}"})
        options["extractor_args"] = {"youtube": {}}
        if cfg.impersonate and "impersonate" not in options:
            raise YouTubeSetupError("YouTube-Impersonation benötigt curl_cffi. Bitte requirements.txt installieren.")
        tries = 4 if proxymode.is_anonymous(cfg) else 1
        for attempt in range(tries):
            proxy = self._proxy_for(ctx)
            if proxy is not None:
                errors.precheck_local_proxy(proxy)
                options["proxy"] = proxymode.ytdlp_proxy_url(proxy)
                if proxymode.is_anonymous(cfg):  # lahme Proxys schnell aufgeben, der nächste kommt dran
                    options.update(socket_timeout=min(cfg.request_timeout, 10), extractor_retries=0)
            try:
                with yt_dlp.YoutubeDL(options) as ydl:
                    info = ydl.extract_info(search_url(query), download=False)
                break
            except Exception as error:
                if proxymode.is_anonymous(cfg):
                    proxymode.note_failure(ctx.im, proxy)
                    if attempt + 1 < tries:
                        continue
                if isinstance(error, yt_dlp.utils.DownloadError):
                    raise errors.wrap(error) from error
                raise
        urls, seen = [], set()
        for entry in (info or {}).get("entries") or []:
            entry = entry or {}
            if entry.get("ie_key") not in (None, "Youtube"):
                continue  # z. B. Kanäle oder Hashtag-Seiten in den Suchergebnissen
            video_id = entry.get("id")
            if not isinstance(video_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{6,20}", video_id) or video_id in seen:
                continue
            seen.add(video_id)
            urls.append(f"https://www.youtube.com/watch?v={video_id}")
            if len(urls) >= limit:
                break
        return urls

    # -- ganze Kanäle: nur die Liste (flach, ohne Download, ohne Browser) --

    def channel_items(self, value, ctx, opts):
        """Videoliste eines Kanals (`/videos`, auf Wunsch auch `/shorts`), neueste zuerst. yt-dlp liest die Liste seitenweise
        und flach (nur Link, Titel, Länge, ungefähres Datum). Weitermachen geht über `opts.cursor` ('Reiter:Anzahl')."""
        from .. import channel_list as cl
        cfg = ctx.cfg
        runtime_options = youtube_runtime_options()
        import yt_dlp

        options = self._opts(cfg)
        options.update(runtime_options)
        options.update({"extract_flat": "in_playlist", "getcomments": False, "noplaylist": False, "lazy_playlist": True})
        options["extractor_args"] = {"youtube": {}, "youtubetab": {"approximate_date": [""]}}
        if cfg.impersonate and "impersonate" not in options:
            raise YouTubeSetupError("YouTube-Impersonation benötigt curl_cffi. Bitte requirements.txt installieren.")
        proxy = self._proxy_for(ctx)
        if proxy is not None:
            errors.precheck_local_proxy(proxy)
            options["proxy"] = proxymode.ytdlp_proxy_url(proxy)
        tabs = ["videos"] + (["shorts"] if opts.include_shorts else [])
        tab, skip = tabs[0], 0
        if opts.cursor and ":" in str(opts.cursor):
            name, _, number = str(opts.cursor).partition(":")
            if name in tabs and number.isdigit():
                tab, skip = name, int(number)
        base = str(value).rstrip("/")
        collector = cl.Collector(opts)
        result = cl.ListResult()
        position = tab
        for current in tabs[tabs.index(tab):]:
            options["playliststart"] = skip + 1
            counted = skip
            try:
                with yt_dlp.YoutubeDL(options) as ydl:
                    info = ydl.extract_info(f"{base}/{current}", download=False)
                    info = info or {}
                    if current == "videos":
                        result.name = result.name or info.get("channel") or info.get("uploader") or info.get("title")
                        total = info.get("playlist_count") or info.get("n_entries")
                        if isinstance(total, int) and total > 0:
                            result.platform_total = total
                    for entry in info.get("entries") or []:
                        entry = entry or {}
                        counted += 1
                        if opts.stop():
                            collector.done = True
                            break
                        video_id = entry.get("id")
                        if (entry.get("ie_key") not in (None, "Youtube") or not isinstance(video_id, str)
                                or not re.fullmatch(r"[A-Za-z0-9_-]{6,20}", video_id)):
                            continue
                        if entry.get("live_status") in ("is_live", "is_upcoming"):
                            continue   # läuft gerade oder kommt erst: später beim Beobachten
                        stamp = entry.get("timestamp")
                        if stamp is None and entry.get("upload_date"):
                            day = str(entry["upload_date"])
                            stamp = cl.parse_utc(f"{day[:4]}-{day[4:6]}-{day[6:8]}T12:00:00Z")
                        url = f"https://www.youtube.com/watch?v={video_id}"
                        position = f"{current}:{counted}"
                        keep_going = collector.add(cl.item(url, f"youtube:{video_id}", entry.get("title"), cl.to_iso(stamp),
                                                           entry.get("duration"), "youtube"), position)
                        if counted % cl.PAGE == 0:
                            opts.pause()
                        if not keep_going:
                            break
                    else:
                        result.exhausted = True   # die Liste dieses Reiters ist zu Ende
            except yt_dlp.utils.DownloadError as error:
                text = str(error).lower()
                if current != "videos" and ("does not have a" in text or "no such" in text or "not found" in text):
                    result.exhausted = True       # dieser Kanal hat keinen Shorts-Reiter
                else:
                    collector.flush(position)
                    raise errors.wrap(error) from error
            collector.flush(position)
            if collector.done or opts.stop():
                result.exhausted = False
                break
            skip = 0
            position = f"{current}:{counted}"
        collector.flush(position)
        result.found = collector.count
        result.reached_known = collector.reached_known
        result.cursor = None if (result.exhausted and not collector.done) else position
        if collector.hit_since:
            result.exhausted = True
        return result
