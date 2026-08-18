"""docs/REFACTOR_PLAN.md Step 7: what main.py's old start()/stop()/
shutdown()/tray_* methods shrink down to - the process lifecycle (tray icon,
worker thread, clean teardown) - ported verbatim from main.py. Owns the
pystray.Icon itself now (not host.icon): nothing outside these same methods
ever read or wrote it before this extraction either, so there's no reason
for the Vortex instance to hold a reference to it any more.

This is deliberately NOT where voice/session.py's Session lives or where
execute()'s intent-routing/dispatch happens - those are Step 3's and Step
6's territory respectively, both already correctly separated and
live-debugged; Orchestrator only owns the outer process lifecycle around
them, the same role start()/stop() always played."""
import contextlib
import threading

import pygame
import pystray
from PIL import Image, ImageDraw


class Orchestrator:
    def __init__(self, host):
        self.host = host
        self.icon = None

    def request_stop_speaking(self, icon=None, item=None):
        self.host.stop_speaking.set()

    def listen_now(self, icon=None, item=None):
        self.host.stop_speaking.set()
        self.host.events.put('barge_in')

    def stop(self, icon=None, item=None):
        """The single shutdown path - used by both the "shutdown vortex" voice
        command and the tray's Exit item. Must stop the tray icon itself, or
        icon.run() (blocking the main thread in run()) never returns and the
        process lingers as a zombie: no longer listening or responding, but
        never actually exiting."""
        self.host.running = False
        self.host.stop_speaking.set()
        if self.icon is not None:
            self.icon.stop()

    def tray_exit(self, icon, item):
        self.stop()

    def tray_icon(self):
        img = Image.new('RGB', (64, 64), 'black')
        d = ImageDraw.Draw(img)
        d.ellipse((16, 16, 48, 48), fill='white')
        return img

    def shutdown(self):
        self.host.running = False
        self.host.stop_speaking.set()
        with contextlib.suppress(Exception):
            pygame.mixer.music.stop()
        self.host.wake.close()
        with contextlib.suppress(Exception):
            self.host.browser.close()
        with contextlib.suppress(Exception):
            self.host.memory.close()
        if self.host.rag is not None:
            with contextlib.suppress(Exception):
                self.host.rag.close()

    def run(self):
        self.host.wake.start()
        threading.Thread(target=self.host.session.worker, daemon=True).start()
        self.icon = pystray.Icon('VORTEX', self.tray_icon(), 'VORTEX Assistant', menu=pystray.Menu(
            pystray.MenuItem('Stop talking', self.request_stop_speaking),
            pystray.MenuItem('Listen now', self.listen_now),
            pystray.MenuItem('Exit', self.tray_exit)))
        try:
            self.icon.run()
        finally:
            self.shutdown()
