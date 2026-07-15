#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_all.py — Pipeline complet en une commande :
  SIRENE -> FINESS -> fusion -> (option) enrichissement contacts.

    python run_all.py                      # SIRENE via API + FINESS + fusion
    python run_all.py --enrich             # + emails/telephones
    python run_all.py --sirene stock --etab StockEtablissement_utf8.csv --unite StockUniteLegale_utf8.csv --enrich
    python run_all.py --departements 69 38 01 42 73 74 --enrich
    python run_all.py --sirene skip        # FINESS seul
"""
import argparse
import subprocess
import sys


def run(cmd):
    print("»", " ".join(cmd), file=sys.stderr)
    if subprocess.run([sys.executable] + cmd).returncode != 0:
        sys.exit(f"Echec: {' '.join(cmd)}")


def main():
    ap = argparse.ArgumentParser(description="Pipeline complet prospection sante.")
    ap.add_argument("--sirene", choices=["api", "stock", "skip"], default="api")
    ap.add_argument("--etab", help="StockEtablissement_utf8.csv (mode stock).")
    ap.add_argument("--unite", help="StockUniteLegale_utf8.csv (mode stock).")
    ap.add_argument("--departements", nargs="+")
    ap.add_argument("--enrich", action="store_true", help="Ajoute l'etape contacts.")
    ap.add_argument("--limit", type=int, help="Limite l'enrichissement (tests).")
    a = ap.parse_args()

    if a.sirene == "api":
        cmd = ["sirene_api.py"]
        if a.departements:
            cmd += ["--departements"] + a.departements
        run(cmd)
    elif a.sirene == "stock":
        if not a.etab:
            sys.exit("--etab requis en mode stock.")
        cmd = ["sirene_stock.py", "--etab", a.etab]
        if a.unite:
            cmd += ["--unite", a.unite]
        run(cmd)

    fcmd = ["finess_ingest.py", "--download"]
    if a.departements:
        fcmd += ["--departements"] + a.departements
    run(fcmd)

    run(["build_base.py"])

    if a.enrich:
        ecmd = ["enrich.py"]
        if a.limit:
            ecmd += ["--limit", str(a.limit)]
        run(ecmd)

    print("Termine.", file=sys.stderr)


if __name__ == "__main__":
    main()
