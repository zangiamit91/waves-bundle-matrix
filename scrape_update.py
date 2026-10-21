"""
Waves Bundle Matrix - automatic data refresher.

Scrapes waves.com/bundles + every bundle detail page, rebuilds data.json /
sprite.png / sprite_map.json, and pushes to GitHub only if something actually
changed. Designed to run unattended on a schedule (Windows Task Scheduler).

No Claude/LLM involved - pure scraping + image processing + git.
"""
import json
import re
import html
import os
import sys
import math
import time
import random
import shutil
import subprocess
import urllib.request
from datetime import datetime, timezone

from playwright.sync_api import sync_playwright
from PIL import Image

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
IMG_CACHE_DIR = os.path.join(REPO_DIR, ".img_cache")
PROFILE_DIR = os.path.join(REPO_DIR, ".pw_profile")
LOG_FILE = os.path.join(REPO_DIR, "update_log.txt")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
SPRITE_CELL = 72
SPRITE_COLS = 20

# Waves' bot protection (Incapsula) reliably blocks headless Chromium after a
# handful of requests, but a real (non-headless) browser with human-like
# pacing gets through cleanly. We still don't want a window flashing on the
# user's screen, so we launch it positioned off any real monitor instead of
# truly headless.
BROWSER_ARGS = ["--window-position=-32000,-32000", "--window-size=1366,900"]


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def decode_entities(s):
    return html.unescape(s)


def extract_json_div(content, div_id):
    for marker in (f"id={div_id}", f'id="{div_id}"'):
        idx = content.find(marker)
        if idx != -1:
            break
    else:
        return None
    start = content.find(">", idx) + 1
    end = content.find("</div>", start)
    raw = content[start:end]
    return json.loads(decode_entities(raw))


def scrape_all():
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=PROFILE_DIR,
            headless=False,
            user_agent=UA,
            viewport={"width": 1366, "height": 900},
            args=BROWSER_ARGS,
        )
        page = ctx.new_page()

        log("Fetching bundle listing...")
        page.goto("https://www.waves.com/bundles", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(3000)
        bundle_list = extract_json_div(page.content(), "list-data")
        if not bundle_list:
            raise RuntimeError("Could not find list-data on bundles listing page")
        log(f"Found {len(bundle_list)} bundles in listing")

        bundles_full = []
        for i, b in enumerate(bundle_list):
            url = b["documentUrlPath"]
            plugins = None
            for attempt in range(3):
                try:
                    page.goto("https://www.waves.com" + url, wait_until="domcontentloaded", timeout=45000)
                    page.wait_for_timeout(1500)
                    plugins = extract_json_div(page.content(), "product-page-includes")
                    if plugins:
                        break
                except Exception as e:
                    log(f"  retry {attempt+1} for {url}: {e}")
                    time.sleep(2)
            time.sleep(random.uniform(1.5, 3.5))
            if not plugins:
                log(f"  WARN: failed to fetch plugins for {url}, skipping")
                continue
            bundles_full.append({
                "name": b["documentName"],
                "url": url,
                "thumb": b.get("thumb"),
                "msrp": b.get("msrp"),
                "price": b.get("skuPrice"),
                "discount": b.get("discount"),
                "tagline": b.get("tagline"),
                "category": b.get("gsfCategory"),
                "rating": b.get("rating"),
                "reviewsTotal": b.get("reviewsTotal"),
                "plugins": [
                    {
                        "name": pl["documentName"],
                        "url": pl["documentUrlPath"],
                        "category": pl.get("gsfCategory"),
                        "msrp": pl.get("msrp"),
                        "price": pl.get("skuPrice"),
                        "thumb": pl.get("thumb"),
                    }
                    for pl in plugins
                ],
            })
            log(f"  [{i+1}/{len(bundle_list)}] {b['documentName']}: {len(plugins)} plugins")

        ctx.close()
        return bundles_full


def build_data(bundles_full):
    merged = []
    plugin_index = {}
    for b in bundles_full:
        merged.append({
            "name": b["name"],
            "url": b["url"],
            "thumb": b["thumb"],
            "msrp": b["msrp"],
            "price": b["price"],
            "discount": b["discount"],
            "tagline": b["tagline"],
            "category": b["category"],
            "rating": b["rating"],
            "reviewsTotal": b["reviewsTotal"],
            "pluginCount": len(b["plugins"]),
            "plugins": [p["url"] for p in b["plugins"]],
        })
        for p in b["plugins"]:
            if p["url"] not in plugin_index:
                plugin_index[p["url"]] = {
                    "name": p["name"],
                    "category": p["category"],
                    "msrp": p["msrp"],
                    "thumb": p.get("thumb"),
                    "bundles": [],
                }
            plugin_index[p["url"]]["bundles"].append(b["name"])
    return {"bundles": merged, "plugins": plugin_index}


def cache_path_for(key):
    safe = re.sub(r"[^a-zA-Z0-9]+", "_", key)
    return os.path.join(IMG_CACHE_DIR, safe + ".png")


def download_image(url, dest):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as resp:
        content = resp.read()
    with open(dest, "wb") as f:
        f.write(content)


def build_sprite(data):
    os.makedirs(IMG_CACHE_DIR, exist_ok=True)
    keys = []
    for b in data["bundles"]:
        keys.append(("bundle:" + b["url"], b["thumb"]))
    for purl, p in data["plugins"].items():
        keys.append(("plugin:" + purl, p["thumb"]))

    for key, url in keys:
        if not url:
            continue
        dest = cache_path_for(key)
        if os.path.exists(dest):
            continue
        try:
            download_image(url, dest)
            log(f"  downloaded new image for {key}")
        except Exception as e:
            log(f"  WARN: failed to download {key}: {e}")

    n = len(keys)
    cols = SPRITE_COLS
    rows = math.ceil(n / cols)
    sheet = Image.new("RGBA", (cols * SPRITE_CELL, rows * SPRITE_CELL), (0, 0, 0, 0))
    positions = {}
    for i, (key, url) in enumerate(keys):
        dest = cache_path_for(key)
        if not os.path.exists(dest):
            continue
        im = Image.open(dest).convert("RGBA").resize((SPRITE_CELL, SPRITE_CELL), Image.LANCZOS)
        col, row = i % cols, i // cols
        x, y = col * SPRITE_CELL, row * SPRITE_CELL
        sheet.paste(im, (x, y), im)
        positions[key] = {"x": x, "y": y}

    sprite_map = {
        "cell": SPRITE_CELL,
        "cols": cols,
        "rows": rows,
        "width": cols * SPRITE_CELL,
        "height": rows * SPRITE_CELL,
        "positions": positions,
    }
    return sheet, sprite_map


def files_differ(path_a_bytes, path_b):
    if not os.path.exists(path_b):
        return True
    with open(path_b, "rb") as f:
        return f.read() != path_a_bytes


def run_git(args, cwd=REPO_DIR):
    result = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def main():
    log("=== Waves Bundle Matrix update run started ===")
    try:
        bundles_full = scrape_all()
    except Exception as e:
        log(f"FATAL: scrape failed: {e}")
        sys.exit(1)

    if len(bundles_full) < 40:
        log(f"FATAL: only got {len(bundles_full)} bundles (expected ~55) - aborting, site may have blocked us")
        sys.exit(1)

    data = build_data(bundles_full)
    sprite, sprite_map = build_sprite(data)

    new_data_bytes = json.dumps(data, ensure_ascii=False).encode("utf-8")
    new_sprite_map_bytes = json.dumps(sprite_map).encode("utf-8")

    data_path = os.path.join(REPO_DIR, "data.json")
    sprite_path = os.path.join(REPO_DIR, "sprite.png")
    sprite_map_path = os.path.join(REPO_DIR, "sprite_map.json")

    data_changed = files_differ(new_data_bytes, data_path)

    tmp_sprite_path = sprite_path + ".tmp"
    sheet, _ = sprite, sprite_map
    sheet.save(tmp_sprite_path, format="PNG", optimize=True)
    with open(tmp_sprite_path, "rb") as f:
        new_sprite_bytes = f.read()
    sprite_changed = files_differ(new_sprite_bytes, sprite_path)

    if not data_changed and not sprite_changed:
        log("No changes detected - nothing to commit.")
        os.remove(tmp_sprite_path)
        log("=== Run finished (no-op) ===\n")
        return

    with open(data_path, "w", encoding="utf-8") as f:
        f.write(new_data_bytes.decode("utf-8"))
    with open(sprite_map_path, "w", encoding="utf-8") as f:
        f.write(new_sprite_map_bytes.decode("utf-8"))
    shutil.move(tmp_sprite_path, sprite_path)

    with open(os.path.join(REPO_DIR, "last_updated.txt"), "w", encoding="utf-8") as f:
        f.write(datetime.now(timezone.utc).strftime("%Y-%m-%d"))

    log(f"Changes detected (data_changed={data_changed}, sprite_changed={sprite_changed}). Committing...")

    run_git(["add", "data.json", "sprite.png", "sprite_map.json", "last_updated.txt"])
    code, out, err = run_git(["commit", "-m", f"Auto-update bundle data ({datetime.now().strftime('%Y-%m-%d')})"])
    if code != 0:
        log(f"git commit failed: {err or out}")
        sys.exit(1)
    log(f"Committed: {out}")

    code, out, err = run_git(["push", "origin", "master"])
    if code != 0:
        log(f"git push failed: {err or out}")
        sys.exit(1)
    log("Pushed to GitHub successfully.")
    log("=== Run finished (updated) ===\n")


if __name__ == "__main__":
    main()
