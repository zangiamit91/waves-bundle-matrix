# Waves Bundle Matrix

A small standalone web tool for cross-referencing [Waves Audio](https://www.waves.com) plugin bundles against the plugins inside them.

- **By Bundle** — search/filter all bundles, expand one to see its full plugin list (grouped by category).
- **By Plugin** — pick one or more plugins to see which bundles include them, ranked by full vs. partial match and price.

## Data

`data.json`, `sprite.png` and `sprite_map.json` hold a snapshot of all 55 bundles and 244 plugins (names, categories, prices, and box-art icons) scraped from the official [waves.com/bundles](https://www.waves.com/bundles) listing on 2026-09-15. Prices and promotions on the live site change over time — this is a point-in-time snapshot, not a live feed.

## Running it

It's a static site — open `index.html` directly, or serve the folder with any static file server:

```
python3 -m http.server 8000
```

Then visit `http://localhost:8000/index.html`.

## Disclaimer

Independent, unofficial project. Not affiliated with or endorsed by Waves Audio Ltd.
