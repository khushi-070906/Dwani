"""Land (khasra) and ration-card look-ups against a dataset cached on this machine.

Why cached: kiosks in a panchayat bhawan or CSC often have no (or unreliable) internet, and state portals
(Bhulekh, NFSA "know your ration card") differ in format. An operator downloads/exports the state's data once and
imports it:

    python -m dwaniforms.records import land  /path/to/land.csv   --as-of 2026-09-01 --source "UP Bhulekh export"
    python -m dwaniforms.records import ration /path/to/ration.csv --as-of 2026-09-01 --source "NFSA UP"
    python -m dwaniforms.records demo        # writes clearly-marked DEMO data (fictional people and places)

Every answer says which copy it came from ("records as of 1 Sep 2026") and warns when that copy is old, because
the official portal is the only authority. Ration-card answers read out only category, members and shop; the head of
family's name is shown masked.
"""
from __future__ import annotations

import csv
import datetime as dt
import difflib
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from .spoken import _norm

DEFAULT_DIR = Path(__file__).parent / "data" / "records"
STALE_DAYS = 90
LAND_COLUMNS = ["district", "district_hi", "tehsil", "tehsil_hi", "village", "village_hi", "khasra", "owners", "owners_hi",
                "area_hectare", "land_type", "land_type_hi", "khata_no"]
RATION_COLUMNS = ["card_no", "head_name", "head_name_hi", "category", "members", "fps_shop", "fps_shop_hi", "district"]


def records_dir() -> Path:
    return Path(os.environ.get("DWANIFORMS_RECORDS_DIR") or DEFAULT_DIR)


_ROMAN_FOLD = [("ee", "i"), ("ii", "i"), ("oo", "u"), ("uu", "u"), ("aa", "a"), ("w", "v"), ("ph", "f"), ("kh", "k"),
               ("gh", "g"), ("bh", "b"), ("dh", "d"), ("th", "t"), ("sh", "s"), ("z", "j"), ("q", "k"), ("y", "i")]


def _key(name: str) -> str:
    """Comparable form of a place name in any script: lowercase, no spaces/punctuation, nukta/chandrabindu-insensitive;
    romanised spellings folded (Seetapur = Sitapur, Barabanki = Baarabanki)."""
    k = re.sub(r"[\W_]+", "", _norm(name or ""))
    if k.isascii():
        for a, b in _ROMAN_FOLD:
            k = k.replace(a, b)
    return k


def mask_name(name: str) -> str:
    return " ".join(w[0] + "***" for w in name.split() if w)


@dataclass
class Meta:
    as_of: str = ""
    source: str = ""
    demo: bool = False

    def age_days(self, today: dt.date | None = None) -> int | None:
        try:
            return ((today or dt.date.today()) - dt.date.fromisoformat(self.as_of)).days
        except ValueError:
            return None


class RecordStore:
    def __init__(self, directory: Path | None = None):
        self.dir = Path(directory or records_dir())
        self.land: list[dict] = self._read("land.csv")
        self.ration: dict[str, dict] = {r["card_no"].upper(): r for r in self._read("ration.csv") if r.get("card_no")}
        self.meta = {k: Meta(**v) for k, v in self._meta().items()}

    def _read(self, name: str) -> list[dict]:
        p = self.dir / name
        if not p.exists():
            return []
        with open(p, encoding="utf-8-sig", newline="") as f:
            return [dict(r) for r in csv.DictReader(f)]

    def _meta(self) -> dict:
        p = self.dir / "meta.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

    def available(self) -> dict:
        return {"land": bool(self.land), "ration": bool(self.ration),
                "meta": {k: vars(v) for k, v in self.meta.items()}}

    # ---- place names ------------------------------------------------------------------------
    def _match(self, spoken: str, rows: list[dict], cols: tuple[str, str]) -> list[str]:
        """Canonical names (first column) that match what was said, exactly or within a small spelling distance."""
        names = {}
        for r in rows:
            for c in cols:
                if r.get(c):
                    names.setdefault(_key(r[c]), r[cols[0]])
        k = _key(spoken)
        if k in names:
            return [names[k]]
        close = difflib.get_close_matches(k, list(names), n=3, cutoff=0.82)
        return sorted({names[c] for c in close})

    def match_district(self, spoken: str) -> list[str]:
        return self._match(spoken, self.land, ("district", "district_hi"))

    def match_village(self, spoken: str, district: str | None = None) -> list[str]:
        rows = [r for r in self.land if not district or r["district"] == district]
        return self._match(spoken, rows, ("village", "village_hi"))

    def label(self, kind: str, canonical: str, lang: str) -> str:
        col = {"district": ("district", "district_hi"), "village": ("village", "village_hi")}[kind]
        for r in self.land:
            if r[col[0]] == canonical and lang == "hi" and r.get(col[1]):
                return f"{r[col[1]]} ({canonical})"
        return canonical

    # ---- look-ups ---------------------------------------------------------------------------
    def find_land(self, district: str, village: str, khasra: str) -> list[dict]:
        k = khasra.replace(" ", "").upper()
        return [r for r in self.land if r["district"] == district and r["village"] == village
                and r["khasra"].replace(" ", "").upper() == k]

    def find_ration(self, card_no: str) -> dict | None:
        return self.ration.get(card_no.replace(" ", "").upper())


# ---------------------------------------------------------------------------------------------
# session integration: check answers against the records, and the final read-out
# ---------------------------------------------------------------------------------------------

def checker_for(store: RecordStore):
    def check(f, value, session):
        if f.params.get("lookup") == "district":
            hits = store.match_district(value)
            return (True, hits[0], "") if len(hits) == 1 else (False, None, "lookup_no_district" if not hits else "lookup_ambiguous")
        if f.params.get("lookup") == "village":
            district = session.answers.get("district").value if session.answers.get("district") else None
            hits = store.match_village(value, district)
            return (True, hits[0], "") if len(hits) == 1 else (False, None, "lookup_no_village" if not hits else "lookup_ambiguous")
        return True, value, ""
    return check


def _stale_note(meta: Meta | None) -> dict:
    if meta is None:
        return {}
    age = meta.age_days()
    return {"as_of": meta.as_of, "source": meta.source, "demo": meta.demo, "stale": age is not None and age > STALE_DAYS}


_WHAT = {"en": {"what_khasra": "khasra {k}", "what_card": "this ration card"},
         "hi": {"what_khasra": "खसरा {k}", "what_card": "यह राशन कार्ड"}}


def _what(lang: str, key: str, **kw) -> str:
    """The thing that wasn't found, in the citizen's language ("खसरा 145/2")."""
    from . import langpacks
    fmt = (langpacks.packs().get(lang) or {}).get("formats", {}).get(key) or _WHAT.get(lang, _WHAT["en"])[key]
    return fmt.format(**kw)


def _area(ha: float, lang: str) -> str:
    acres = f"{ha * 2.4711:.2f}".rstrip("0").rstrip(".")
    if lang == "hi":
        return f"{ha:g} हेक्टेयर (लगभग {acres} एकड़)"
    from . import langpacks
    fmt = (langpacks.packs().get(lang) or {}).get("formats", {}).get("area")
    return fmt.format(ha=f"{ha:g}", acres=acres) if fmt else f"{ha:g} hectare (about {acres} acres)"


def land_outcome(session, service=None, **_) -> dict:
    store: RecordStore = service.records
    d = session.answers["district"].value
    v = session.answers["village"].value
    k = session.answers["khasra"].value
    rows = store.find_land(d, v, k)
    meta = _stale_note(store.meta.get("land"))
    if not rows:
        return {"type": "lookup", "title": "Land record", "found": False, "say_key": "lookup_not_found",
                "say_kw": {"what": _what(session.lang, "what_khasra", k=k)}, **meta}
    hi = session.lang == "hi"
    results = []
    for r in rows:
        ha = float(r["area_hectare"] or 0)
        results.append({
            "Khasra": r["khasra"], "Village": store.label("village", r["village"], session.lang),
            "Owner(s)": (r.get("owners_hi") if hi and r.get("owners_hi") else r["owners"]),
            "Area": _area(ha, session.lang),
            "Land type": (r.get("land_type_hi") if hi and r.get("land_type_hi") else r["land_type"]),
            "Khata / khatauni no.": r.get("khata_no", ""),
        })
    first = results[0]
    return {"type": "lookup", "title": "Land record", "found": True, "results": results, "say_key": "lookup_land",
            "say_kw": {"khasra": first["Khasra"], "owners": first["Owner(s)"], "area": first["Area"],
                       "kind": first["Land type"]}, **meta}


CATEGORY_NAMES = {"AAY": ("Antyodaya (AAY)", "अंत्योदय (AAY)"), "PHH": ("Priority household (PHH)", "पात्र गृहस्थी (PHH)"),
                  "NPHH": ("Non-priority (NPHH)", "गैर-प्राथमिकता (NPHH)")}


def ration_outcome(session, service=None, **_) -> dict:
    store: RecordStore = service.records
    card = session.answers["card_no"].value
    r = store.find_ration(card)
    meta = _stale_note(store.meta.get("ration"))
    if not r:
        return {"type": "lookup", "title": "Ration card", "found": False, "say_key": "lookup_not_found",
                "say_kw": {"what": _what(session.lang, "what_card")}, **meta}
    hi = session.lang == "hi"
    cat = CATEGORY_NAMES.get(r["category"].upper(), (r["category"], r["category"]))[1 if hi else 0]
    from . import langpacks
    cat = (langpacks.packs().get(session.lang) or {}).get("categories", {}).get(r["category"].upper()) or cat
    shop = r.get("fps_shop_hi") if hi and r.get("fps_shop_hi") else r["fps_shop"]
    results = [{"Card no.": "•" * max(len(r["card_no"]) - 4, 0) + r["card_no"][-4:],
                "Head of family": mask_name(r["head_name"]), "Category": cat, "Members": r["members"],
                "Ration shop": shop, "District": r.get("district", "")}]
    return {"type": "lookup", "title": "Ration card", "found": True, "results": results, "say_key": "lookup_ration",
            "say_kw": {"category": cat, "members": r["members"], "shop": shop}, **meta}


# ---------------------------------------------------------------------------------------------
# command line: import / demo data
# ---------------------------------------------------------------------------------------------

def _import(kind: str, src: str, as_of: str, source: str, demo: bool = False) -> None:
    cols = LAND_COLUMNS if kind == "land" else RATION_COLUMNS
    required = {"district", "village", "khasra", "owners", "area_hectare"} if kind == "land" else {"card_no", "category", "members", "fps_shop"}
    with open(src, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    missing = required - set(rows[0] if rows else {})
    if missing:
        raise SystemExit(f"{src}: missing columns {sorted(missing)} (expected {cols})")
    d = records_dir()
    d.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, d / f"{kind}.csv")
    meta_p = d / "meta.json"
    meta = json.loads(meta_p.read_text(encoding="utf-8")) if meta_p.exists() else {}
    meta[kind] = {"as_of": as_of, "source": source, "demo": demo}
    meta_p.write_text(json.dumps(meta, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"Imported {len(rows)} {kind} records into {d} (as of {as_of}).")


def write_demo(directory: Path | None = None, as_of: str | None = None) -> Path:
    """Fictional records for demos and tests. Every name is invented; the meta file marks them as DEMO."""
    d = Path(directory or records_dir())
    d.mkdir(parents=True, exist_ok=True)
    land = [
        ["Sitapur", "सीतापुर", "Biswan", "बिसवां", "Sonpur", "सोनपुर", "145/2", "DEMO Ram Lal", "डेमो राम लाल", "0.405", "Agricultural", "कृषि", "00231"],
        ["Sitapur", "सीतापुर", "Biswan", "बिसवां", "Sonpur", "सोनपुर", "312", "DEMO Sita Devi, DEMO Mohan", "डेमो सीता देवी, डेमो मोहन", "1.2", "Agricultural", "कृषि", "00232"],
        ["Sitapur", "सीतापुर", "Sadar", "सदर", "Rampur", "रामपुर", "88क", "DEMO Gram Sabha", "डेमो ग्राम सभा", "0.12", "Pond", "तालाब", "00007"],
        ["Barabanki", "बाराबंकी", "Ramnagar", "रामनगर", "Rampur", "रामपुर", "145/2", "DEMO Abdul Karim", "डेमो अब्दुल करीम", "0.8", "Agricultural", "कृषि", "00419"],
    ]
    ration = [
        ["123456789012", "DEMO Ram Lal", "डेमो राम लाल", "PHH", "5", "DEMO Shop 12, Sonpur", "डेमो दुकान 12, सोनपुर", "Sitapur"],
        ["UP1234567890", "DEMO Kamla", "डेमो कमला", "AAY", "3", "DEMO Shop 4, Rampur", "डेमो दुकान 4, रामपुर", "Sitapur"],
    ]
    for name, cols, rows in (("land.csv", LAND_COLUMNS, land), ("ration.csv", RATION_COLUMNS, ration)):
        with open(d / name, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(cols)
            w.writerows(rows)
    as_of = as_of or dt.date.today().isoformat()
    meta = {k: {"as_of": as_of, "source": "DEMO DATA: fictional people and places", "demo": True} for k in ("land", "ration")}
    (d / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return d


def main(argv=None) -> None:
    import argparse
    ap = argparse.ArgumentParser(prog="python -m dwaniforms.records")
    sub = ap.add_subparsers(dest="cmd", required=True)
    imp = sub.add_parser("import")
    imp.add_argument("kind", choices=["land", "ration"])
    imp.add_argument("csv")
    imp.add_argument("--as-of", required=True, help="date the export was taken (YYYY-MM-DD)")
    imp.add_argument("--source", required=True)
    sub.add_parser("demo")
    a = ap.parse_args(argv)
    if a.cmd == "import":
        _import(a.kind, a.csv, a.as_of, a.source)
    else:
        print(f"Demo data written to {write_demo()}")


if __name__ == "__main__":
    main(sys.argv[1:])
