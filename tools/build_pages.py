"""Build a static demo of the UI for GitHub Pages.

    python tools/build_pages.py _site

Seeds the demo EAM and Skyking into a throwaway DATA_DIR, then writes the API
responses the UI reads as JSON files next to a copy of web/, plus one ADS-B
snapshot taken at build time. The page runs in demo mode: no live audio, no
SSE, nothing to connect to. Needs numpy and ffmpeg.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KINDS = ("eam", "skyking", "voice")


def main(out: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp)
        env = {**os.environ, "DATA_DIR": str(data), "PYTHONPATH": str(ROOT)}
        subprocess.run([sys.executable, str(ROOT / "tools" / "seed_demo.py")], env=env, check=True)

        sys.path.insert(0, str(ROOT))
        from app.store import Store
        store = Store(data / "skywatch.sqlite3")

        if out.exists():
            shutil.rmtree(out)
        shutil.copytree(ROOT / "web", out)
        shutil.copytree(data / "audio", out / "audio")

        api = out / "api"
        (api / "messages").mkdir(parents=True)
        for kind in (*KINDS, "all"):
            rows = store.list(None if kind == "all" else [kind], limit=200)
            (api / f"messages-{kind}.json").write_text(json.dumps(rows))
        for row in store.list(None, limit=200):
            (api / "messages" / f"{row['id']}.json").write_text(json.dumps(store.get(row["id"])))

        from app.aircraft import AircraftWatch
        from app.config import config
        watch = AircraftWatch(config.adsb_url, 0, lambda s: None)
        try:
            snap = watch.update(watch.fetch(), time.time())
        except Exception as e:      # no network: the page says there's no snapshot
            snap = {"updated_at": None, "source": config.adsb_url, "error": str(e), "aircraft": []}
        (api / "aircraft.json").write_text(json.dumps(snap))
        print(f"aircraft snapshot: {len(snap['aircraft'])} watched, error={snap['error']}")

    index = out / "index.html"
    html = index.read_text()
    flag = '<script>window.SKYWATCH_DEMO = true;</script>\n<script src="app.js"></script>'
    assert '<script src="app.js"></script>' in html
    index.write_text(html.replace('<script src="app.js"></script>', flag))
    (out / ".nojekyll").touch()
    print(f"built {out}")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "_site").resolve())
