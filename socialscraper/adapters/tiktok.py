import logging
import re
from urllib.parse import quote, urlsplit
from .base import Adapter, checkpoint
from ..fetch_result import FetchResult, comments_failure, received_comments
from ..models import Video, Comment, iso
from ..net import humanize
from ..util import parse_short_number, comment_id, comment_limit
from ..net.retry import with_retry

log = logging.getLogger(__name__)

VIDEO_JS = """
() => {
  let s = window.__UNIVERSAL_DATA_FOR_REHYDRATION__;
  // The element ID also creates a window property in Chrome. That property
  // may be the HTMLScriptElement rather than the parsed hydration object.
  if (!s || !s.__DEFAULT_SCOPE__) {
    const script = document.getElementById('__UNIVERSAL_DATA_FOR_REHYDRATION__');
    try { s = script && JSON.parse(script.textContent || 'null'); }
    catch (_) { s = null; }
  }
  const d = s && s.__DEFAULT_SCOPE__;
  const item = d && d["webapp.video-detail"] && d["webapp.video-detail"].itemInfo &&
               d["webapp.video-detail"].itemInfo.itemStruct;
  if (!item) return null;
  return {id:item.id, desc:item.desc, createTime:item.createTime,
          author:item.author&&item.author.uniqueId, authorId:item.author&&item.author.id,
          stats:item.stats, music:item.music&&item.music.title,
          duration:item.video&&item.video.duration,
          challenges:(item.challenges||[]).map(c=>c.title)};
}
"""

COMMENTS_JS = """
() => {
  // TikTok marks the comment text itself with comment-level-1; older layouts also
  // put the attribute on a wrapper. Read the innermost text element and take
  // author/likes from the nearest ancestor that contains the username.
  const TEXT = '[data-e2e="comment-level-1"]', USER = '[data-e2e="comment-username-1"]';
  const out = [], boxes = new Set();
  for (const node of document.querySelectorAll(TEXT)) {
    if (node.querySelector(TEXT)) continue;
    let box = node.parentElement;
    while (box && box !== document.body && !box.querySelector(USER)) box = box.parentElement;
    if (!box || box === document.body) box = node.parentElement || node;
    if (boxes.has(box)) continue;
    boxes.add(box);
    const holder = node.closest('[data-cid]') || box.querySelector('[data-cid]');
    out.push({
      author: (box.querySelector(USER) || {}).innerText,
      text: node.innerText,
      likes: (box.querySelector('[data-e2e="comment-like-count"]') || {}).innerText,
      id: holder ? holder.getAttribute('data-cid') : null
    });
  }
  return out;
}
"""

class TikTokAdapter(Adapter):
    platform = "tiktok"
    domains = ("tiktok.com",)
    needs_browser = True
    needs_auth = True

    PROFILE_JS = """
    () => Array.from(document.querySelectorAll('a[href*="/video/"]'))
              .map(a => a.href.split('?')[0])
    """

    @staticmethod
    def _check_session(page, ctx):
        require_auth = ctx.require_auth or getattr(ctx.cfg, "require_auth", False)
        if require_auth and not ctx.authenticated:
            from ..net.session_guard import SessionExpired
            raise SessionExpired("TikTok benötigt eine bestätigte Anmeldung")
        if not ctx.authenticated:
            return
        logged_out = page.evaluate(
            "() => !!document.querySelector('[data-e2e=\"top-login-button\"]')")
        if not logged_out:
            return
        from ..net.session_guard import invalidate, SessionExpired
        from ..net.notify import notify
        ctx.authenticated = False
        invalidate(ctx.identity, "tiktok")
        notify("session_lost_runtime", "TikTok-Session während der Abfrage verloren",
               identity=ctx.identity, platform="tiktok")
        if require_auth:
            raise SessionExpired("TikTok-Session während der Abfrage verloren; erneut anmelden")
        log.warning("TikTok-Session verloren; Abfrage läuft ohne Anmeldung weiter")
        return True

    # Nur ganze Sätze der TikTok-Prüfseite, nie einzelne Wörter wie „captcha“ oder „puzzle“: Der Seitentext enthält
    # auch Beschreibungen und Kommentare, und ein Rätsel-Kanal darf nicht als Sicherheitsabfrage gelten.
    CAPTCHA_MARKERS = ("schieberegler", "drag the slider", "slide to verify", "fit the puzzle", "verify to continue",
                       "select 2 objects")

    @staticmethod
    def _blocked_message(page):
        try:
            text = str(page.evaluate("() => (document.body && document.body.innerText) || ''")).lower()
        except Exception:
            return ""
        if any(marker in text for marker in TikTokAdapter.CAPTCHA_MARKERS):
            return ("TikTok zeigt eine Sicherheitsabfrage (CAPTCHA). Im Anmeldefenster prüfen "
                    "und danach erneut versuchen.")
        if any(marker in text for marker in ("log in to see comments", "login to see comments", "anmelden, um kommentare")):
            return "TikTok verlangt eine Anmeldung für Kommentare. Erneut anmelden und den Abruf wiederholen."
        return ""

    @with_retry(times=3, backoff=2.0)
    def fetch(self, url, ctx):
        cfg = ctx.cfg
        limit = comment_limit(cfg)
        from ..net import proxymode
        proxymode.assert_fetch_allowed(cfg, "tiktok", limit)  # kostenlose Proxys/Tor: keine Kommentare (brauchen ein Konto)
        if ctx.sessions is None:
            raise ValueError("TikTok benötigt eine Browser-Session")
        with ctx.sessions.acquire(ctx.identity, "tiktok", ctx.country, ctx.mobile) as page:
            humanize.warmup(page, "https://www.tiktok.com/")
            page.goto(url, wait_until="domcontentloaded")

            raw = humanize.wait_for(page, lambda: page.evaluate(VIDEO_JS), timeout=4.0)
            # A transient login button may disappear as the page hydrates.
            session_lost = self._check_session(page, ctx)
            if not raw:
                log.warning("kein Hydration-Blob für %s", url)
                return FetchResult(None, [], "unavailable", self._blocked_message(page) or
                                   "TikTok hat keine Videodaten geliefert. Link prüfen und erneut versuchen.")

            if not isinstance(raw, dict) or not raw.get("id"):
                raise ValueError("TikTok liefert keine gültige Video-ID")
            vid = str(raw["id"])
            st = raw.get("stats") or {}
            # Short links (vm./vt.tiktok.com) resolve in the browser; tracking parameters are dropped.
            final = getattr(page, "url", None)
            canonical = final if isinstance(final, str) and self.matches(final) else url
            canonical = urlsplit(canonical)._replace(query="", fragment="").geturl()
            v = Video.from_dict({
                "platform": "tiktok", "video_id": vid, "url": canonical,
                "author": raw.get("author"), "author_id": raw.get("authorId"),
                "caption": raw.get("desc"), "created_at": iso(raw.get("createTime")),
                "duration_s": raw.get("duration"),
                "view_count": parse_short_number(st.get("playCount")),
                "like_count": parse_short_number(st.get("diggCount")),
                "comment_count": parse_short_number(st.get("commentCount")),
                "share_count": parse_short_number(st.get("shareCount")),
                "save_count": parse_short_number(st.get("collectCount")),
                "hashtags": raw.get("challenges") or [], "music": raw.get("music"),
            })
            checkpoint(ctx, v, [])
            if not limit:
                return FetchResult(v, [], "not_requested", "Nur Videodaten angefordert.")
            if session_lost:
                return FetchResult(v, [], "unavailable", "TikTok-Anmeldung ist abgelaufen. "
                                   "Erneut anmelden und den Abruf wiederholen.")
            if v.comment_count == 0:
                return FetchResult(v, [], "empty", "TikTok meldet keine Kommentare.")

            try:
                page.wait_for_selector('[data-e2e="comment-level-1"]', timeout=15000)
            except Exception as error:
                log.warning("Kommentarliste nicht gefunden für %s", vid)
                message = self._blocked_message(page)
                if message:
                    return FetchResult(v, [], "unavailable", message)
                result = comments_failure(v, [], error)
                result.message = "TikTok-Kommentarliste konnte nicht geladen werden. " + result.message
                return result

            seen, comments, stalled = set(), [], 0
            for _ in range(20):
                before = len(comments)
                try:
                    if self._check_session(page, ctx):
                        from ..net.session_guard import SessionExpired
                        raise SessionExpired("TikTok-Anmeldung ist abgelaufen. Erneut anmelden und den Abruf wiederholen.")
                    batch = page.evaluate(COMMENTS_JS) or []
                    for c in batch:
                        txt = (c.get("text") or "").strip()
                        if not txt: continue
                        author = (c.get("author") or "").strip() or None
                        cid = comment_id(vid, author, txt, c.get("id"))
                        if cid in seen: continue
                        seen.add(cid)
                        comments.append(Comment.from_dict({
                            "platform": "tiktok", "video_id": vid, "comment_id": str(cid),
                            "text": txt, "author": author,
                            "like_count": parse_short_number(c.get("likes")),
                        }))
                        if len(comments) >= limit:
                            break
                except Exception as error:
                    # Callback errors never enter this handler: failed persistence must surface.
                    return comments_failure(v, comments, error)
                if len(comments) > before:
                    checkpoint(ctx, v, comments)
                if len(comments) >= limit:
                    break
                stalled = stalled + 1 if len(comments) == before else 0
                if stalled >= 3:
                    break
                try:
                    humanize.scroll(page, 900)
                    page.wait_for_timeout(700)
                except Exception as error:
                    return comments_failure(v, comments, error)

            log.info("TikTok %s: %s Kommentare", vid, len(comments))
            message = self._blocked_message(page) if len(comments) < limit else ""
            if message:
                return FetchResult(v, comments, "partial" if comments else "unavailable", message)
            if not comments:
                return FetchResult(v, [], "unavailable", "TikTok hat keine Kommentare geliefert. "
                                   "Das belegt keine leere Kommentarliste. Anmeldung prüfen und erneut versuchen.")
            return received_comments(v, comments, limit, end_unconfirmed=True)

    @with_retry(times=3, backoff=2.0)
    def profile_urls(self, username, ctx, limit=20):
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise ValueError("limit muss eine nichtnegative ganze Zahl sein")
        username = str(username).lstrip("@")
        if not re.fullmatch(r"[A-Za-z0-9_.]+", username):
            raise ValueError("Ungültiger TikTok-Benutzername")
        if not limit:
            return []
        if ctx.sessions is None:
            raise ValueError("TikTok benötigt eine Browser-Session")
        with ctx.sessions.acquire(ctx.identity, "tiktok", ctx.country, ctx.mobile) as page:
            humanize.warmup(page, "https://www.tiktok.com/")
            page.goto(f"https://www.tiktok.com/@{username}", wait_until="domcontentloaded")
            page.wait_for_timeout(4000)
            self._check_session(page, ctx)
            urls, seen, stalled = [], set(), 0
            for _ in range(15):
                self._check_session(page, ctx)
                before = len(urls)
                for link in page.evaluate(self.PROFILE_JS) or []:
                    if not self.matches(link):
                        continue
                    parsed = urlsplit(link)
                    match = re.fullmatch(r"/@([^/]+)/video/(\d+)/?", parsed.path)
                    if not match or match.group(1).casefold() != username.casefold():
                        continue
                    canonical = parsed._replace(query="", fragment="").geturl()
                    if match.group(2) not in seen:
                        seen.add(match.group(2))
                        urls.append(canonical)
                    if len(urls) >= limit:
                        break
                if len(urls) >= limit:
                    break
                stalled = stalled + 1 if len(urls) == before else 0
                if stalled >= 3:
                    break
                humanize.scroll(page, 1200)
                page.wait_for_timeout(900)
            return urls

    STATS_JS = """
    () => {
      let s = window.__UNIVERSAL_DATA_FOR_REHYDRATION__;
      if (!s || !s.__DEFAULT_SCOPE__) {
        const script = document.getElementById('__UNIVERSAL_DATA_FOR_REHYDRATION__');
        try { s = script && JSON.parse(script.textContent || 'null'); } catch (_) { s = null; }
      }
      const d = s && s.__DEFAULT_SCOPE__ && s.__DEFAULT_SCOPE__['webapp.user-detail'];
      const info = d && d.userInfo;
      if (!info) return null;
      return {videoCount: info.stats && info.stats.videoCount, name: info.user && info.user.nickname};
    }
    """
    MAX_SCROLL_ROUNDS = 600

    def channel_items(self, value, ctx, opts):
        """Videoliste eines Profils: das Profil im Browser (mit Konto) bis zum Ende scrollen. Pausen zwischen den Schritten;
        bei einer Sicherheitsabfrage (CAPTCHA) wird abgebrochen – was bis dahin gefunden wurde, ist schon gespeichert.
        Das Datum steckt in der Video-Nummer (die oberen Bits sind Unix-Sekunden)."""
        from .. import channel_list as cl
        username = str(value).lstrip("@")
        if not re.fullmatch(r"[A-Za-z0-9_.]+", username):
            raise ValueError("Ungültiger TikTok-Benutzername")
        if ctx.sessions is None:
            raise ValueError("TikTok benötigt eine Browser-Session")
        collector, result = cl.Collector(opts), cl.ListResult()
        stop = humanize._remember_stop(opts.stop)
        if stop():
            return result
        with ctx.sessions.acquire(ctx.identity, "tiktok", ctx.country, ctx.mobile) as page:
            if stop():
                return result
            humanize.warmup(page, "https://www.tiktok.com/", stop=stop)
            if stop():
                return result
            page.goto(f"https://www.tiktok.com/@{username}", wait_until="domcontentloaded")
            humanize.wait(4, stop=stop, sleep=lambda seconds: page.wait_for_timeout(seconds * 1000))
            if stop():
                return result
            self._check_session(page, ctx)
            if stop():
                return result
            try:
                stats = page.evaluate(self.STATS_JS) or {}
            except Exception:
                stats = {}
            if stop():
                return result
            total = parse_short_number(stats.get("videoCount")) if isinstance(stats, dict) else None
            if total:
                result.platform_total = total
            if isinstance(stats, dict) and stats.get("name"):
                result.name = str(stats["name"])
            stalled = 0
            for _ in range(self.MAX_SCROLL_ROUNDS):
                if stop():
                    break
                self._check_session(page, ctx)
                if stop():
                    break
                before = collector.count
                for link in page.evaluate(self.PROFILE_JS) or []:
                    if stop():
                        break
                    if not isinstance(link, str) or not self.matches(link):
                        continue
                    parsed = urlsplit(link)
                    match = re.fullmatch(r"/@([^/]+)/video/(\d+)/?", parsed.path)
                    if not match or match.group(1).casefold() != username.casefold():
                        continue
                    canonical = parsed._replace(query="", fragment="").geturl()
                    if not collector.add(cl.item(canonical, f"tiktok:{match.group(2)}", None, cl.tiktok_time(match.group(2)), None, "tiktok")):
                        break
                if stop() or collector.done:
                    break
                if collector.count == before:
                    stalled += 1
                    message = self._blocked_message(page) if stalled >= 2 else ""
                    if stop():
                        break
                    if message:
                        collector.flush()
                        from ..net.retry import PermanentFetchError
                        raise PermanentFetchError(message)
                    if stalled >= 4:
                        result.exhausted = True
                        break
                else:
                    stalled = 0
                    collector.flush()
                if not humanize.scroll(page, 1200, stop=stop):
                    break
                humanize.wait((900 + int(opts.rng.uniform(0, 1400))) / 1000,
                              stop=stop, sleep=lambda seconds: page.wait_for_timeout(seconds * 1000))
        collector.flush()
        result.found = collector.count
        result.reached_known = collector.reached_known
        if collector.hit_since:
            result.exhausted = True
        return result

    def _collect_links(self, ctx, page_url, limit, warm=True):
        """Öffnet eine TikTok-Seite (Hashtag, Suche) im Browser und sammelt Links `/@nutzer/video/<id>` beim Scrollen."""
        if ctx.sessions is None:
            raise ValueError("TikTok benötigt eine Browser-Session")
        if not limit:
            return []
        with ctx.sessions.acquire(ctx.identity, "tiktok", ctx.country, ctx.mobile) as page:
            if warm:
                humanize.warmup(page, "https://www.tiktok.com/")
            page.goto(page_url, wait_until="domcontentloaded")
            page.wait_for_timeout(4000)
            self._check_session(page, ctx)
            urls, seen, stalled = [], set(), 0
            for _ in range(15):
                self._check_session(page, ctx)
                before = len(urls)
                for link in page.evaluate(self.PROFILE_JS) or []:
                    if not isinstance(link, str) or not self.matches(link):
                        continue
                    parsed = urlsplit(link)
                    match = re.fullmatch(r"/@([^/]+)/video/(\d+)/?", parsed.path)
                    if not match or match.group(2) in seen:
                        continue
                    seen.add(match.group(2))
                    urls.append(parsed._replace(query="", fragment="").geturl())
                    if len(urls) >= limit:
                        break
                if len(urls) >= limit:
                    break
                stalled = stalled + 1 if len(urls) == before else 0
                if stalled >= 3:
                    break
                humanize.scroll(page, 1200)
                page.wait_for_timeout(900)
            if not urls:
                try:
                    text = str(page.evaluate("() => (document.body && document.body.innerText) || ''")).lower()
                except Exception:
                    text = ""
                if any(m in text for m in self.CAPTCHA_MARKERS):
                    from ..net.retry import PermanentFetchError
                    raise PermanentFetchError("TikTok zeigt ein Sicherheitsrätsel (CAPTCHA); "
                                              "mit angemeldetem Konto erneut versuchen")
            return urls

    @with_retry(times=3, backoff=2.0)
    def hashtag_urls(self, tag, ctx, limit=20):
        """Videos der Hashtag-Seite https://www.tiktok.com/tag/<tag>."""
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise ValueError("limit muss eine nichtnegative ganze Zahl sein")
        tag = str(tag).lstrip("#").strip()
        if not re.fullmatch(r"\w{1,100}", tag):
            raise ValueError("Ungültiger TikTok-Hashtag")
        return self._collect_links(ctx, f"https://www.tiktok.com/tag/{quote(tag)}", limit)

    @with_retry(times=3, backoff=2.0)
    def search_urls(self, query, ctx, limit=20):
        """Videos der TikTok-Suche (Reiter „Videos“)."""
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise ValueError("limit muss eine nichtnegative ganze Zahl sein")
        query = (query or "").strip()
        if not query:
            raise ValueError("Suchbegriff fehlt")
        return self._collect_links(ctx, f"https://www.tiktok.com/search/video?q={quote(query)}", limit)
