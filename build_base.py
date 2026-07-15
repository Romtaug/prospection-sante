#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_base.py — Fusionne SIRENE + FINESS en UNE base sante, typee et dedupliquee.
Sortie : base_sante.csv

Dedup sur SIREN (ou SIRET) quand disponible : une entite vue par les deux sources
devient une ligne (source = "finess+sirene") et on complete les champs vides.
Les etablissements FINESS SANS SIREN/SIRET sont CONSERVES (ils restent des prospects
valides : nom + adresse + telephone), jamais fusionnes entre eux par erreur.

    python build_base.py
    python build_base.py --sirene sirene_sante.csv --finess finess_sante.csv --out base_sante.csv
"""
import argparse
import csv
import os
import sys
from collections import Counter

from common import BASE_COLS, clean_siret


def load(path):
    if not path or not os.path.exists(path):
        print(f"  (ignore, absent : {path})", file=sys.stderr)
        return []
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def key_of(r):
    siren = (r.get("siren") or "").strip()
    if len(siren) == 9 and siren.isdigit():
        return "s:" + siren
    siret = clean_siret(r.get("siret"))
    if siret:
        return "e:" + siret
    return None  # pas d'identifiant -> ligne conservee telle quelle


def merge(*sources):
    base, keyless, n = {}, [], 0
    for rows in sources:
        for r in rows:
            row = {c: r.get(c, "") for c in BASE_COLS}
            k = key_of(r)
            if k is None:
                keyless.append(row)
                n += 1
                continue
            if k not in base:
                base[k] = row
            else:
                b = base[k]
                srcs = set(filter(None, [b.get("source", "")] + row.get("source", "").split("+")))
                b["source"] = "+".join(sorted(srcs))
                for c in BASE_COLS:
                    if not b.get(c) and row.get(c):
                        b[c] = row[c]
    return list(base.values()) + keyless


def main():
    ap = argparse.ArgumentParser(description="Fusionne SIRENE + FINESS -> base sante.")
    ap.add_argument("--sirene", default="sirene_sante.csv")
    ap.add_argument("--finess", default="finess_sante.csv")
    ap.add_argument("--out", default="base_sante.csv")
    a = ap.parse_args()

    rows = merge(load(a.sirene), load(a.finess))
    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=BASE_COLS)
        w.writeheader()
        w.writerows(rows)

    by_type = Counter(r["type"] for r in rows)
    by_src = Counter(r["source"] for r in rows)
    with_id = sum(1 for r in rows if (r.get("siren") or r.get("siret")))
    print(f"BASE: {len(rows)} entites -> {a.out} ({with_id} avec SIREN/SIRET)", file=sys.stderr)
    print("  par type   : " + ", ".join(f"{k}={v}" for k, v in sorted(by_type.items())), file=sys.stderr)
    print("  par source : " + ", ".join(f"{k}={v}" for k, v in sorted(by_src.items())), file=sys.stderr)


if __name__ == "__main__":
    main()
