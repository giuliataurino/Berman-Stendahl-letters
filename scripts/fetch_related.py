#!/usr/bin/env python3
"""
Find pages in the Getty's Stendahl transcriptions on FromThePage that mention
Eugene Berman, and link them to descriptors in data/descriptors.json.

Uses FromThePage's public IIIF Presentation API (v2), which needs no key for
public collections:
  https://fromthepage.com/iiif/collections      list of collections
  https://fromthepage.com/iiif/collection/<id>  one collection's manifests
  https://fromthepage.com/iiif/<work>/manifest  one work: canvases, images,
                                                and plaintext exports (seeAlso)

Writes data/related.json. Standard library only. Run:
  python scripts/fetch_related.py            # full run
  python scripts/fetch_related.py --limit 20 # quick test on 20 works
"""
import argparse, json, re, sys, time, urllib.request, urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CACHE = ROOT / ".cache"
BASE = "https://fromthepage.com"
UA = "berman-stendahl-descriptors/1.0 (research visualization; GitHub Pages)"

# Collections to walk. Leave empty to discover them by label from /iiif/collections.
COLLECTION_URLS = []
COLLECTION_LABEL = re.compile(r"Stendahl", re.I)

# A page counts as a Berman page if its text matches this.
BERMAN = re.compile(r"\bBerman\b|\bEugene\b|\bGenia\b|\bE\.\s?B\.", re.I)


def get(url, as_json=True, pause=0.4, tries=3):
    """GET with a small on-disk cache so reruns are cheap."""
    CACHE.mkdir(exist_ok=True)
    key = CACHE / (re.sub(r"[^A-Za-z0-9]+", "_", url)[-180:] + (".json" if as_json else ".txt"))
    if key.exists():
        txt = key.read_text(encoding="utf-8")
        return json.loads(txt) if as_json else txt
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json, text/plain"})
            with urllib.request.urlopen(req, timeout=60) as r:
                txt = r.read().decode("utf-8", "replace")
            key.write_text(txt, encoding="utf-8")
            time.sleep(pause)
            return json.loads(txt) if as_json else txt
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            print(f"  ! {url}: {e}", file=sys.stderr)
            time.sleep(2 * (i + 1))
    return None


def as_list(x):
    return x if isinstance(x, list) else ([x] if x else [])


def label(x):
    l = x.get("label", "")
    if isinstance(l, dict):  # IIIF v3 style, just in case
        l = next(iter(l.values()), [""])[0]
    return str(l)


def find_collections():
    if COLLECTION_URLS:
        return COLLECTION_URLS
    top = get(f"{BASE}/iiif/collections") or {}
    found = [c["@id"] for c in as_list(top.get("collections")) if COLLECTION_LABEL.search(label(c))]
    print(f"Found {len(found)} Stendahl collection(s): {found}")
    return found


def walk(coll_url, seen=None):
    """Yield manifest URLs from a collection, following nested collections."""
    seen = seen or set()
    if coll_url in seen:
        return
    seen.add(coll_url)
    c = get(coll_url) or {}
    for m in as_list(c.get("manifests")):
        yield m["@id"]
    for sub in as_list(c.get("collections")):
        yield from walk(sub["@id"], seen)


def plaintext_url(obj):
    """Pick the verbatim (else any) plaintext export from a seeAlso list."""
    sees = as_list(obj.get("seeAlso"))
    for pref in ("verbatim", "searchable", "emended"):
        for s in sees:
            sid = s.get("@id", "") if isinstance(s, dict) else str(s)
            if "plaintext" in sid and pref in sid:
                return sid
    return None


def thumb(canvas):
    for img in as_list(canvas.get("images")):
        res = img.get("resource", {})
        svc = res.get("service")
        svc = svc[0] if isinstance(svc, list) and svc else svc
        if isinstance(svc, dict) and svc.get("@id"):
            base = svc["@id"].rstrip("/")
            return base + "/full/!400,400/0/default.jpg", base + "/full/!1600,1600/0/default.jpg"
        if res.get("@id"):
            return res["@id"], res["@id"]
    return None, None


def page_link(manifest, canvas, index):
    for r in as_list(canvas.get("related")):
        if isinstance(r, dict) and r.get("@id"):
            return r["@id"]
    for r in as_list(manifest.get("related")):
        if isinstance(r, dict) and "contents" not in r.get("@id", ""):
            return r["@id"]
    return manifest.get("@id")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="only scan the first N works")
    args = ap.parse_args()

    keywords = json.loads((DATA / "keywords.json").read_text(encoding="utf-8"))
    for k in keywords:
        k["rx"] = re.compile(k["pattern"], re.I)

    pages, scanned = [], 0
    for coll in find_collections():
        coll_doc = get(coll) or {}
        coll_label = label(coll_doc)
        for murl in walk(coll):
            if args.limit and scanned >= args.limit:
                break
            scanned += 1
            m = get(murl)
            if not m:
                continue
            # Cheap filter: work-level text first, canvases only if Berman appears.
            wtxt_url = plaintext_url(m)
            wtxt = get(wtxt_url, as_json=False) if wtxt_url else None
            if wtxt is not None and not BERMAN.search(wtxt):
                continue
            canvases = [c for seq in as_list(m.get("sequences")) for c in as_list(seq.get("canvases"))]
            for i, cv in enumerate(canvases):
                turl = plaintext_url(cv)
                txt = get(turl, as_json=False) if turl else ""
                if not txt or not BERMAN.search(txt):
                    continue
                hits = [k for k in keywords if k["rx"].search(txt)]
                t_small, t_large = thumb(cv)
                mt = BERMAN.search(txt)
                s = max(0, mt.start() - 160)
                pages.append({
                    "collection": coll_label,
                    "work": label(m),
                    "page": label(cv) or f"page {i + 1}",
                    "url": page_link(m, cv, i),
                    "manifest": murl,
                    "canvas": cv.get("@id"),
                    "thumb": t_small,
                    "image": t_large,
                    "snippet": re.sub(r"\s+", " ", txt[s:s + 360]).strip(),
                    "terms": [k["term"] for k in hits],
                    "descriptors": sorted({d for k in hits for d in k["descriptors"]}),
                })
            print(f"[{scanned}] {label(m)}: {sum(1 for p in pages if p['manifest'] == murl)} Berman page(s)")

    out = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": "FromThePage IIIF API, Getty Research Institute, Stendahl Art Galleries records (2017.M.38)",
        "works_scanned": scanned,
        "pages": pages,
    }
    (DATA / "related.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"Done: {scanned} works scanned, {len(pages)} Berman pages, "
          f"{sum(1 for p in pages if p['descriptors'])} linked to descriptors.")


if __name__ == "__main__":
    main()
