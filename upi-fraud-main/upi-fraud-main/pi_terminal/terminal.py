#!/usr/bin/env python3
"""
terminal.py - payment terminal screen for the Raspberry Pi (pygame, no browser).

Shows: idle -> payment QR -> processing / waiting -> green "Payment successful" or red "Payment failed".
It only talks to the laptop's backend (polls /api/demo/current), so it can be started and restarted any time.

Run:      python3 terminal.py
Options (environment variables):
    SERVER=http://192.168.137.1:8000     address of the laptop backend
    SIZE=480x320                         draw at this logical size and scale to the screen (optional)
    WINDOWED=1                           run in a window instead of full screen (for testing)
Keys: Esc or Q quits. Touch / click on a result screen returns to idle.
"""
import json
import os
import threading
import time
import urllib.request

try:
    import pygame
except ImportError:                     # lets the network part be tested on a machine without pygame
    pygame = None

SERVER = os.environ.get("SERVER", "http://192.168.137.1:8000").rstrip("/")
POLL_S = 0.6
RESULT_S = 10.0                          # matches RESULT_SECONDS on the server
SHOP = "Fresh Mart"

BG = (15, 23, 42)
GREEN = (21, 128, 61)
RED = (185, 28, 28)
WHITE = (248, 250, 252)
MUTED = (148, 163, 184)
BLUE = (147, 197, 253)
SKY = (56, 189, 248)


# ----------------------------------------------------------------- network (no pygame needed)
class Poller(threading.Thread):
    """Background thread: asks the server what to show. The drawing loop only reads the latest answer."""

    def __init__(self, server):
        super().__init__(daemon=True)
        self.server = server
        self.session = None              # latest session dict, or None for idle
        self.online = False
        self.qr = {}                     # session id -> list of '0101..' rows
        self._stop = False

    def _get(self, path):
        with urllib.request.urlopen(self.server + path, timeout=2.5) as r:
            return json.loads(r.read().decode("utf-8"))

    def run(self):
        while not self._stop:
            try:
                s = self._get("/api/demo/current")["session"]
                if s and s["status"] == "waiting_scan" and s["id"] not in self.qr:
                    self.qr[s["id"]] = self._get(f"/api/demo/qr/{s['id']}.json")["rows"]
                    if len(self.qr) > 20:
                        self.qr.pop(next(iter(self.qr)))
                self.session, self.online = s, True
            except Exception:
                self.online = False
            time.sleep(POLL_S)

    def stop(self):
        self._stop = True


# ---------------------------------------------------------------------------- drawing
class Ui:
    def __init__(self, screen):
        self.screen = screen
        self.w, self.h = screen.get_size()
        self.unit = min(self.w, self.h) / 100.0          # 1 "vmin"
        self.fonts = {}
        self.font_path = pygame.font.match_font("dejavusans,freesans,liberationsans,arial")
        bold = pygame.font.match_font("dejavusans,freesans,liberationsans,arial", bold=True)
        self.bold_path = bold or self.font_path
        probe = self.font(20)
        m = probe.metrics("\u20B9")
        self.rupee = "\u20B9" if m and m[0] is not None else "Rs "

    def font(self, px, bold=False):
        key = (int(px), bold)
        if key not in self.fonts:
            self.fonts[key] = pygame.font.Font(self.bold_path if bold else self.font_path, int(px))
        return self.fonts[key]

    def money(self, n):
        return f"{self.rupee}{float(n):,.0f}"

    def text(self, s, vmin, color, center=None, left=None, bold=False, max_w=None):
        px = vmin * self.unit
        f = self.font(px, bold)
        while max_w and f.size(s)[0] > max_w and px > 8:      # shrink to fit
            px -= 2
            f = self.font(px, bold)
        surf = f.render(s, True, color)
        r = surf.get_rect()
        if center:
            r.center = center
        elif left:
            r.midleft = left
        self.screen.blit(surf, r)
        return r

    # ---- screens
    def idle(self, online):
        self.screen.fill(BG)
        cx, cy = self.w // 2, self.h // 2
        self.text(SHOP, 11, WHITE, center=(cx, cy - 9 * self.unit), bold=True, max_w=self.w * 0.9)
        pulse = 0.5 + 0.5 * abs(((time.time() * 0.6) % 2) - 1)
        c = tuple(int(v * (0.35 + 0.65 * pulse)) for v in (34, 197, 94))
        y = cy + 9 * self.unit
        pygame.draw.circle(self.screen, c, (int(cx - 24 * self.unit), int(y)), int(1.6 * self.unit))
        self.text("Ready for the next payment", 5.2, MUTED, center=(cx + 3 * self.unit, y), max_w=self.w * 0.8)

    def qr(self, s, rows):
        self.screen.fill(BG)
        landscape = self.w >= self.h
        side = min(self.h * 0.88, self.w * 0.48) if landscape else min(self.w * 0.80, self.h * 0.52)
        side = int(side)
        if landscape:
            box = pygame.Rect(int(self.w * 0.04), (self.h - side) // 2, side, side)
            tx, ty, tw = box.right + int(self.w * 0.05), self.h // 2, self.w - box.right - int(self.w * 0.09)
        else:
            box = pygame.Rect((self.w - side) // 2, int(self.h * 0.04), side, side)
            tx, ty, tw = self.w // 2, box.bottom + int(self.h * 0.05), self.w * 0.9
        pygame.draw.rect(self.screen, (255, 255, 255), box, border_radius=int(2 * self.unit))
        if rows:
            n, quiet = len(rows), 2
            mod = max(1, side // (n + 2 * quiet))
            ox = box.x + (side - mod * n) // 2
            oy = box.y + (side - mod * n) // 2
            for y, row in enumerate(rows):
                for x, ch in enumerate(row):
                    if ch == "1":
                        pygame.draw.rect(self.screen, (0, 0, 0), (ox + x * mod, oy + y * mod, mod, mod))
        if landscape:
            self.text(s["merchant"], 6, (203, 213, 225), left=(tx, ty - 15 * self.unit), max_w=tw)
            self.text(self.money(s["amount"]), 13, WHITE, left=(tx, ty), bold=True, max_w=tw)
            self.text("Scan with your phone", 4.6, BLUE, left=(tx, ty + 14 * self.unit), max_w=tw)
            self.text("to pay", 4.6, BLUE, left=(tx, ty + 20 * self.unit), max_w=tw)
        else:
            self.text(s["merchant"], 5, (203, 213, 225), center=(tx, ty + 3 * self.unit), max_w=tw)
            self.text(self.money(s["amount"]), 11, WHITE, center=(tx, ty + 13 * self.unit), bold=True, max_w=tw)
            self.text("Scan with your phone to pay", 4.2, BLUE, center=(tx, ty + 23 * self.unit), max_w=tw)

    def wait(self, msg, sub):
        self.screen.fill(BG)
        cx, cy = self.w // 2, self.h // 2
        r = int(9 * self.unit)
        c = (cx, int(cy - 10 * self.unit))
        pygame.draw.circle(self.screen, (51, 65, 85), c, r, int(1.4 * self.unit))
        a = -(time.time() * 6.0) % 6.2832
        rect = pygame.Rect(c[0] - r, c[1] - r, 2 * r, 2 * r)
        pygame.draw.arc(self.screen, SKY, rect, a, a + 1.6, int(1.6 * self.unit))
        self.text(msg, 7.5, WHITE, center=(cx, cy + 9 * self.unit), bold=True, max_w=self.w * 0.9)
        if sub:
            self.text(sub, 4.4, MUTED, center=(cx, cy + 19 * self.unit), max_w=self.w * 0.9)

    def result(self, ok, amount):
        self.screen.fill(GREEN if ok else RED)
        cx, cy = self.w // 2, self.h // 2
        u, c = self.unit, (self.w // 2, int(self.h // 2 - 12 * self.unit))
        t, r = int(4.5 * u), int(14 * u)
        pygame.draw.circle(self.screen, WHITE, c, r, int(1.2 * u))
        if ok:                                          # tick
            pts = [(c[0] - 7 * u, c[1] + 0.5 * u), (c[0] - 2 * u, c[1] + 6 * u), (c[0] + 8 * u, c[1] - 6 * u)]
            pygame.draw.lines(self.screen, WHITE, False, pts, t)
        else:                                           # cross
            d = 6.5 * u
            pygame.draw.line(self.screen, WHITE, (c[0] - d, c[1] - d), (c[0] + d, c[1] + d), t)
            pygame.draw.line(self.screen, WHITE, (c[0] - d, c[1] + d), (c[0] + d, c[1] - d), t)
        self.text("Payment successful" if ok else "Payment failed", 9.5, WHITE,
                  center=(cx, cy + 14 * u), bold=True, max_w=self.w * 0.92)
        self.text(self.money(amount), 6.5, (255, 255, 255), center=(cx, cy + 25 * u), max_w=self.w * 0.9)

    def offline_badge(self):
        self.text("reconnecting...", 3.4, (251, 191, 36), left=(self.w - 24 * self.unit, 3 * self.unit))


# -------------------------------------------------------------------------------- main
def main():
    if pygame is None:
        raise SystemExit("pygame is not installed:  sudo apt install python3-pygame")
    pygame.init()
    pygame.mouse.set_visible(False)
    flags = 0 if os.environ.get("WINDOWED") == "1" else pygame.FULLSCREEN
    size = os.environ.get("SIZE")
    if size:
        w, h = (int(v) for v in size.lower().split("x"))
        screen = pygame.display.set_mode((w, h), flags | pygame.SCALED)
    elif flags:
        screen = pygame.display.set_mode((0, 0), flags)
    else:
        screen = pygame.display.set_mode((480, 320))
    pygame.display.set_caption("Payment terminal")
    ui = Ui(screen)
    clock = pygame.time.Clock()

    poller = Poller(SERVER)
    poller.start()
    dismissed = None                    # session id the customer tapped away
    result_key, result_at = None, 0.0

    running = True
    while running:
        for e in pygame.event.get():
            if e.type == pygame.QUIT or (e.type == pygame.KEYDOWN and e.key in (pygame.K_ESCAPE, pygame.K_q)):
                running = False
            elif e.type in (pygame.MOUSEBUTTONDOWN, pygame.FINGERDOWN):
                s = poller.session
                if s and s["status"] in ("success", "failed"):
                    dismissed = s["id"]

        s = poller.session
        if s and s["id"] == dismissed:
            s = None
        if s and s["status"] in ("success", "failed"):             # show the result for RESULT_S seconds
            key = (s["id"], s["status"])
            if key != result_key:
                result_key, result_at = key, time.time()
            elif time.time() - result_at > RESULT_S:
                dismissed, s = s["id"], None

        if s is None or s["status"] == "expired":
            ui.idle(poller.online)
        elif s["status"] == "waiting_scan":
            ui.qr(s, poller.qr.get(s["id"]))
        elif s["status"] == "processing":
            ui.wait("Processing...", "")
        elif s["status"] == "awaiting_customer":                   # the terminal does not know why
            ui.wait("Waiting for customer", "Please confirm on your phone")
        elif s["status"] == "success":
            ui.result(True, s["amount"])
        elif s["status"] == "failed":
            ui.result(False, s["amount"])
        else:
            ui.idle(poller.online)

        if not poller.online:
            ui.offline_badge()
        pygame.display.flip()
        clock.tick(30)

    poller.stop()
    pygame.quit()


if __name__ == "__main__":
    main()