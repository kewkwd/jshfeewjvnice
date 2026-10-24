"""Motion, pacing and cancellation behavior, with an optional real local DOM."""
import gc
import os
import random
import threading
import time
import unittest
import weakref
from types import SimpleNamespace
from unittest.mock import patch

from socialscraper import channel_list, repo, scheduler
from socialscraper.net import humanize


class Page:
    def __init__(self, width=320, height=480):
        self.viewport_size = {'width': width, 'height': height}
        self.moves, self.wheels, self.waits, self.urls = [], [], [], []
        self.mouse = SimpleNamespace(move=lambda x,y:self.moves.append((x,y)),
                                     wheel=lambda x,y:self.wheels.append((x,y)))

    def wait_for_timeout(self, milliseconds):
        self.waits.append(milliseconds)

    def goto(self, url, **kwargs):
        self.urls.append(url)


class HumanizeBehavior(unittest.TestCase):
    def setUp(self):
        humanize._last_fetch.clear()
        humanize._pos.clear()
        self.addCleanup(humanize._last_fetch.clear)
        self.addCleanup(humanize._pos.clear)
        env = patch.dict(os.environ, {'SS_PACE':'on','SS_NO_PACING':''})
        env.start();self.addCleanup(env.stop)

    def test_cancel_pause_preserves_previous_fetch_time(self):
        humanize._last_fetch['youtube'] = 1
        stop = threading.Event();sleeps = []
        def sleep(seconds):
            sleeps.append(seconds);stop.set()
        waited = humanize.pace('youtube', rng=random.Random(3), sleep=sleep,
                               clock=lambda:2, stop=stop.is_set)
        self.assertEqual(sleeps, [.1])
        self.assertEqual(waited, .1)
        self.assertEqual(humanize._last_fetch['youtube'], 1)

    def test_pre_cancel_has_no_fetch_timestamp_or_page_activity(self):
        page = Page()
        self.assertEqual(humanize.pace('youtube', stop=lambda:True), 0)
        self.assertFalse(humanize.move(page, 10, 10, stop=lambda:True))
        self.assertFalse(humanize.warmup(page, stop=lambda:True))
        self.assertEqual((page.moves,page.waits,page.urls), ([],[],[]))
        self.assertNotIn('youtube', humanize._last_fetch)

    def test_delay_rejects_nonfinite_negative_and_reversed_values(self):
        for bounds in ({'low':-1},{'low':5,'high':3},{'high':float('inf')},
                       {'break_chance':1.1},{'break_range':(5,1)}):
            with self.subTest(bounds=bounds), self.assertRaises(ValueError):
                humanize.pace_delay(random.Random(1), bounds)
        for seconds in (-1,float('nan'),float('inf'),10**400):
            with self.subTest(seconds=str(seconds)[:20]), self.assertRaises(ValueError):
                humanize.wait(seconds,sleep=lambda _:self.fail('Must not sleep'))

    def test_move_stays_in_mobile_viewport_and_spreads_duration(self):
        page = Page(80, 120)
        self.assertTrue(humanize.move(page, 1000, -40, rng=random.Random(4),duration=350))
        self.assertTrue(all(0<=x<=79 and 0<=y<=119 for x,y in page.moves))
        self.assertEqual(page.moves[-1], (79,0))
        self.assertAlmostEqual(sum(page.waits),350)
        self.assertEqual(len(page.moves),len(page.waits))

    def test_cancelled_move_records_actual_last_position(self):
        page = Page();stop = threading.Event()
        def wait(milliseconds):
            page.waits.append(milliseconds);stop.set()
        page.wait_for_timeout = wait
        self.assertFalse(humanize.move(page,300,400,rng=random.Random(1),stop=stop.is_set))
        self.assertEqual(len(page.moves),1)
        self.assertEqual(humanize._pos[page],page.moves[-1])
        self.assertNotEqual(humanize._pos[page],(300,400))

    def test_position_cache_releases_closed_pages(self):
        page = Page();humanize.move(page,100,100,duration=0)
        reference = weakref.ref(page)
        del page;gc.collect()
        self.assertIsNone(reference())
        self.assertEqual(len(humanize._pos),0)

    def test_scroll_exact_requested_distance_in_both_directions(self):
        for total in (1,575,-575,1200,0):
            page = Page()
            self.assertTrue(humanize.scroll(page,total,rng=random.Random(3)))
            self.assertEqual(sum(y for _,y in page.wheels),total)
            self.assertTrue(all(abs(y)<=420 for _,y in page.wheels))
            self.assertTrue(all(100<=wait<=1500 for wait in page.waits))

    def test_cancel_scroll_stops_after_first_step(self):
        page=Page();stop=threading.Event()
        page.wait_for_timeout=lambda _:stop.set()
        self.assertFalse(humanize.scroll(page,1200,rng=random.Random(3),stop=stop.is_set))
        self.assertEqual(len(page.wheels),1)

    def test_warmup_allows_short_dwell(self):
        page=Page(100,150)
        self.assertTrue(humanize.warmup(page,'about:blank',dwell=25,rng=random.Random(8)))
        self.assertEqual(page.urls,['about:blank'])
        self.assertLessEqual(page.waits[0],25)
        self.assertTrue(all(0<=x<100 and 0<=y<150 for x,y in page.moves))

    def test_channel_page_pause_can_be_cancelled(self):
        stop=threading.Event();sleeps=[]
        def sleep(seconds):sleeps.append(seconds);stop.set()
        options=channel_list.ListOptions(cancelled=stop.is_set,sleep=sleep,rng=random.Random(1))
        options.pause(2,3)
        self.assertEqual(sleeps,[.1])


class SchedulerCancellation(unittest.TestCase):
    def test_cancel_during_real_pace_prevents_adapter_fetch(self):
        import test_scheduler as fixtures
        helper=fixtures.SchedulerTests()
        helper.setUp();self.addCleanup(helper.doCleanups)
        target=repo.add_target(helper.db,'video','youtube',fixtures.YT+'aaaaaaaaaaa')
        stopped=threading.Event()
        previous=dict(humanize._last_fetch)
        humanize._last_fetch['youtube']=time.monotonic()
        self.addCleanup(lambda: (humanize._last_fetch.clear(),humanize._last_fetch.update(previous)))
        original=humanize.pace
        def pace(platform,**kwargs):
            return original(platform,sleep=lambda _:stopped.set(),**kwargs)
        with patch.dict(os.environ,{'SS_PACE':'on','SS_NO_PACING':''}),patch.object(humanize,'pace',side_effect=pace):
            result=helper.run_target(target['id'],cancel=stopped)
        self.assertEqual((result['status'],result['videos_seen']),('cancelled',0))
        self.assertEqual(helper.adapter.calls,[])
        self.assertEqual(helper.run_row(result['run_id'])['status'],'cancelled')


@unittest.skipUnless(os.environ.get('SS_RUN_HUMANIZE_DOM')=='1','local browser fixture disabled')
class HumanizeLocalDOM(unittest.TestCase):
    def test_paced_text_replaces_previous_draft_and_preserves_other_field(self):
        from patchright.sync_api import sync_playwright
        from socialscraper.aktionen.browser import _tippen
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch(headless=True,channel='chrome')
            try:
                page=browser.new_page()
                page.route('**/*',lambda route:route.abort())
                page.set_content('<textarea id="review">Alter Entwurf</textarea><input id="other" value="Unveraendert">')
                desired='Neuer Text ä🌱'
                self.assertEqual(_tippen(page,['#missing','#review'],desired),'#review')
                self.assertEqual(page.locator('#review').input_value(),desired)
                self.assertEqual(page.locator('#other').input_value(),'Unveraendert')
            finally:browser.close()

    def test_actual_mouse_and_wheel_in_small_chrome_viewport(self):
        from patchright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch(headless=True,channel='chrome')
            try:
                page=browser.new_page(viewport={'width':320,'height':480})
                page.route('**/*',lambda route:route.abort())
                page.set_content('<style>body{margin:0;height:4000px}</style>')
                page.evaluate('window.ssTestMoves=[];document.addEventListener("mousemove",e=>window.ssTestMoves.push([e.clientX,e.clientY]))')
                self.assertTrue(humanize.move(page,1000,-50,duration=150,rng=random.Random(2)))
                moves=page.evaluate('window.ssTestMoves')
                self.assertGreater(len(moves),5)
                self.assertTrue(all(0<=x<320 and 0<=y<480 for x,y in moves))
                self.assertEqual(moves[-1],[319,0])
                self.assertTrue(humanize.scroll(page,575,rng=random.Random(3)))
                page.wait_for_timeout(200)
                self.assertEqual(page.evaluate('scrollY'),575)
            finally:browser.close()
