from .youtube import YouTubeAdapter
from .tiktok import TikTokAdapter
from .instagram import InstagramAdapter
from .reddit import RedditAdapter
from .x import XAdapter

ALL = [YouTubeAdapter(), TikTokAdapter(), InstagramAdapter(), RedditAdapter(), XAdapter()]

def resolve(url: str):
    for a in ALL:
        if a.matches(url):
            return a
    return None
