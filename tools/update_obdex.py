"""Build obd_doctor/data/obdex_en.json.gz from the obdex trouble-code database.

obdex (https://github.com/foerbsnavi/obdex) is a public-domain (CC0-1.0) database of the
9,500+ generic OBD-II trouble codes, with sourced descriptions, likely causes, symptoms and
repair estimates. This script downloads it and keeps a compact English-only copy for the app.

    python tools/update_obdex.py            # download the latest data
    python tools/update_obdex.py FILE.json  # use a local generic.json / generic.min.json
"""

import gzip
import json
import os
import sys
import urllib.request

URL = "https://foerbsnavi.github.io/OBDex/generic.min.json"
META = "https://foerbsnavi.github.io/OBDex/meta.json"
OUT = os.path.join(os.path.dirname(__file__), "..", "obd_doctor", "data", "obdex_en.json.gz")
ORDER = {"high": 0, "medium": 1, "low": 2}


def en(x):
    return (x or {}).get("en") if isinstance(x, dict) else x


def main():
    if len(sys.argv) > 1:
        data = json.load(open(sys.argv[1], encoding="utf-8"))
        generated = "local file"
    else:
        data = json.load(urllib.request.urlopen(URL))
        generated = json.load(urllib.request.urlopen(META)).get("generated_at", "")
    codes = {}
    for e in data:
        causes = sorted(e.get("common_causes") or [], key=lambda c: ORDER.get(c.get("likelihood"), 3))
        r = e.get("repair") or {}
        f = e.get("flags") or {}
        codes[e["code"]] = {
            "t": en(e.get("title")),
            "d": en(e.get("description")),
            "c": [[en(c.get("label")), c.get("likelihood")] for c in causes if en(c.get("label"))],
            "s": [en(s) for s in e.get("symptoms") or [] if en(s)],
            "r": [r.get("difficulty"), r.get("diy_possible"), r.get("estimated_cost_eur"), r.get("estimated_hours")],
            "l": bool(f.get("limp_mode_possible")),
        }
    payload = {"source": "obdex (https://github.com/foerbsnavi/obdex), CC0-1.0", "generated_at": generated,
               "codes": codes}
    with gzip.open(OUT, "wt", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
    print(f"Wrote {len(codes)} codes to {os.path.abspath(OUT)} ({os.path.getsize(OUT) / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
