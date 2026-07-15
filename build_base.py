#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_base.py — Fusionne les sorties SIRENE + FINESS en UNE base sante,
dedupliquee et typee. C'est le fichier final : base_sante.csv.

Dedup par SIREN (entite juridique) par defaut, ou par SIRET avec --by-site.
Quand une entite est vue par les deux sources, on fusionne (source = "finess+sirene")
et on complete les champs vides de l'une avec l'autre (ex: le telephone FINESS).

    python build_base.py                                  # lit sirene_sante.csv + finess_sante.csv
    python build_base.py --sirene sirene_sante.csv --finess finess_sante.csv --out base_sante.csv
    python build_base.py --by-site
"""
import argparse
import csv
import os
import sys
from collections import Counter

from common import BASE_COLS


def load(path):
    if not path or not os.path.exists(path):
        print(f"  (ignore, absent : {path})", file=sys.stderr)
        return []
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def merge(*sources, by_site=False):
    base = {}
    for rows in sources:
        for r in rows:
            key = r.get("siret") if (by_site and r.get("siret")) else r.get("siren")
            if not key:
                continue
            if key not in base:
                base[key] = {c: r.get(c, "") for c in BASE_COLS}
            else:
                b = base[key]
                srcs = set(filter(None, [b.get("source", "")] + r.get("source", "").split("+")))
                b["source"] = "+".join(sorted(srcs))
                for c in BASE_COLS:
                    if not b.get(c) and r.get(c):
                        b[c] = r[c]
    return list(base.values())


def main():
    ap = argparse.ArgumentParser(description="Fusionne SIRENE + FINESS -> base sante dedupliquee.")
    ap.add_argument("--sirene", default="sirene_sante.csv")
    ap.add_argument("--finess", default="finess_sante.csv")
    ap.add_argument("--out", default="base_sante.csv")
    ap.add_argument("--by-site", action="store_true", help="Dedup par SIRET au lieu de SIREN.")
    a = ap.parse_args()

    rows = merge(load(a.sirene), load(a.finess), by_site=a.by_site)
    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=BASE_COLS)
        w.writeheader()
        w.writerows(rows)

    by_type = Counter(r["type"] for r in rows)
    by_src = Counter(r["source"] for r in rows)
    print(f"BASE: {len(rows)} entites -> {a.out}", file=sys.stderr)
    print("  par type   : " + ", ".join(f"{k}={v}" for k, v in sorted(by_type.items())), file=sys.stderr)
    print("  par source : " + ", ".join(f"{k}={v}" for k, v in sorted(by_src.items())), file=sys.stderr)


if __name__ == "__main__":
    main()
