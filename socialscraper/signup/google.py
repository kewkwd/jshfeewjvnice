from . import youtube
REGISTER_URL, SEL = youtube.REGISTER_URL, youtube.SEL
def run(page, ctx):
    return youtube.run(page, ctx)
