from dataclasses import dataclass
from ..util import matches_host

@dataclass
class ScrapeContext:
    cfg: object
    store: object
    im: object
    sessions: object = None
    identity: str = "default"
    country: str = "us"
    mobile: bool = False
    authenticated: bool = False
    require_auth: bool = False
    checkpoint: object = None


def checkpoint(ctx, video, comments):
    """Der Aufrufer entscheidet, ob und wo Zwischenergebnisse gespeichert werden."""
    callback = getattr(ctx, "checkpoint", None)
    if callback is not None:
        callback(video, list(comments))

class Adapter:
    platform = ""
    domains = ()
    needs_browser = False
    needs_auth = False

    def matches(self, url: str) -> bool:
        return matches_host(url, *self.domains)

    def fetch(self, url: str, ctx: ScrapeContext):
        raise NotImplementedError
