"""
scripts/capture_walkthrough.py — regenerate the README walkthrough GIF.

Drives a running demo instance with a headless browser, captures nine scenes
(the practitioner, the secretary, and an administrator who cannot read anything
on their own), overlays a self-captioning top bar on each, and
stitches them into a looping GIF at docs/screenshots/walkthrough.gif.

Silent by design: every frame explains itself, so the GIF is legible in a README
without audio.

Setup (one time):
    pip install playwright pillow
    python -m playwright install chromium

Start a clean demo, e.g. with Docker (fresh volumes):
    docker compose -f docker-compose.yml -f docker-compose.demo.yml up --build

Then:
    python scripts/capture_walkthrough.py                       # uses :8000
    python scripts/capture_walkthrough.py http://127.0.0.1:8093

It signs in three times; the demo allows 5 sign-ins per IP per minute. It files an
erasure request for CL-003, so run it on a fresh demo.
"""

import os
import sys
import tempfile

from playwright.sync_api import sync_playwright
from PIL import Image, ImageDraw, ImageFont

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_GIF = os.path.join(ROOT, "docs", "screenshots", "walkthrough.gif")

PRACTITIONER = ("psk.elif", "Practitioner@2026!")
SECRETARY = ("secretary.ayse", "Secretary@2026!")
ADMIN = ("admin", "Admin@2026Secure!")
VW = {"width": 1366, "height": 860}

# (id, caption, duration_ms)
SCENES = [
    ("01", "Sign in — Argon2id hashing, httpOnly-cookie sessions, 5 attempts per minute", 2300),
    ("02", "Practitioner dashboard — clients with their tally, and the next appointments", 3000),
    ("03", "The client's file — profile, notes, a locked transcript; only their practitioner opens it", 3200),
    ("04", "The access ledger — every read of the file, hash-linked and tamper-evident", 2800),
    ("05", "Client cards — contact details and the KVKK date; export data or request erasure", 3000),
    ("06", "The secretary — the same client cards and the appointment book, no file", 3000),
    ("07", "Appointment book — no double booking; came or did not come", 2800),
    ("08", "An admin sees “SELECT CLIENT” — no records on their own authority", 2600),
    ("09", "Erasure requests — carried out only with a second person's co-signature", 3200),
]

TITLE = "MAHREM — CONFIDENTIAL CLIENT RECORDS"
W, BAR = 1100, 92
BG, ACC, MUT, FG = (11, 13, 18), (230, 168, 60), (150, 160, 175), (238, 242, 248)


def _font(sz, bold=False):
    for pth in ([r"C:/Windows/Fonts/arialbd.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
                if bold else [r"C:/Windows/Fonts/arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]):
        try:
            return ImageFont.truetype(pth, sz)
        except OSError:
            continue
    return ImageFont.load_default()


def _sign_in(browser, user, pw, settle=4500):
    page = browser.new_context(viewport=VW).new_page()
    page.goto(BASE, wait_until="networkidle")
    page.fill("#inp-username", user)
    page.fill("#inp-password", pw)
    page.click("#btn-login")
    page.wait_for_timeout(settle)
    return page


def _shot(page, raw_dir, sid, settle):
    page.wait_for_timeout(settle)
    page.screenshot(path=os.path.join(raw_dir, f"{sid}.png"))


def capture(raw_dir):
    """Drive the browser and drop the raw PNG scenes into raw_dir."""
    with sync_playwright() as p:
        b = p.chromium.launch()

        # Act 1 — the practitioner
        pg = b.new_context(viewport=VW).new_page()
        pg.goto(BASE, wait_until="networkidle")
        _shot(pg, raw_dir, "01", 700)
        pg.fill("#inp-username", PRACTITIONER[0])
        pg.fill("#inp-password", PRACTITIONER[1])
        pg.click("#btn-login")
        _shot(pg, raw_dir, "02", 5500)
        pg.click('[data-page="records"]')
        _shot(pg, raw_dir, "03", 3500)
        pg.click('[data-page="my-access"]')
        _shot(pg, raw_dir, "04", 2500)
        pg.click('[data-page="clients"]')
        _shot(pg, raw_dir, "05", 2500)
        pg.on("dialog", lambda dialog: dialog.accept())   # "File an erasure request?"
        pg.click('[data-action="client-erasure"][data-arg="CL-003"]')   # so the operator has one to show
        pg.wait_for_timeout(2000)

        # Act 2 — the secretary
        pg = _sign_in(b, *SECRETARY)
        pg.click('[data-page="clients"]')
        _shot(pg, raw_dir, "06", 2500)
        pg.click('[data-page="appointments"]')
        _shot(pg, raw_dir, "07", 2500)

        # Act 3 — governance
        pg = _sign_in(b, *ADMIN)
        _shot(pg, raw_dir, "08", 800)
        pg.click('[data-page="erasure-requests"]')
        _shot(pg, raw_dir, "09", 2000)

        b.close()



def compose(raw_dir):
    """Caption each raw scene and stitch the looping GIF."""
    f_title, f_step = _font(15, True), _font(19, True)
    frames, durs = [], []
    for sid, cap, dur in SCENES:
        src = Image.open(os.path.join(raw_dir, f"{sid}.png")).convert("RGB")
        h = int(src.height * W / src.width)
        src = src.resize((W, h), Image.LANCZOS)
        canvas = Image.new("RGB", (W, h + BAR), BG)
        canvas.paste(src, (0, BAR))
        d = ImageDraw.Draw(canvas)
        d.rectangle([0, BAR - 2, W, BAR], fill=(30, 36, 46))
        d.rectangle([18, 20, 22, BAR - 20], fill=ACC)
        d.text((34, 18), TITLE, font=f_title, fill=MUT)
        step = f"{SCENES.index((sid, cap, dur)) + 1} / {len(SCENES)}"
        tw = d.textbbox((0, 0), step, font=f_step)[2]
        px = W - tw - 46
        avail = (px - 14) - 34 - 18
        sz, cf = 25, _font(25, True)
        while sz > 16 and d.textlength(cap, font=cf) > avail:
            sz -= 1
            cf = _font(sz, True)
        d.text((34, 44), cap, font=cf, fill=FG)
        d.rounded_rectangle([px - 14, 26, W - 20, 60], 10, fill=(24, 29, 38), outline=(52, 60, 72))
        d.text((px, 32), step, font=f_step, fill=ACC)
        frames.append(canvas)
        durs.append(dur)

    pal = frames[2].convert("P", palette=Image.ADAPTIVE, colors=256)
    q = [fr.quantize(palette=pal, dither=Image.NONE) for fr in frames]
    os.makedirs(os.path.dirname(OUT_GIF), exist_ok=True)
    q[0].save(OUT_GIF, save_all=True, append_images=q[1:], duration=durs,
              loop=0, optimize=True, disposal=2)
    print("wrote", OUT_GIF, "-", round(os.path.getsize(OUT_GIF) / 1024 / 1024, 2), "MB")


def main():
    with tempfile.TemporaryDirectory() as raw:
        capture(raw)
        compose(raw)


if __name__ == "__main__":
    main()
