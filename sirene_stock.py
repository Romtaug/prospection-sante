#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sirene_stock.py — Collecte SIRENE EXHAUSTIVE a partir du stock INSEE (DuckDB).
C'est le mode le plus complet : on filtre tout le stock des etablissements
sur les NAF sante, en une requete.

Fichier a fournir (une seule fois) : le stock INSEE Sirene, sur data.gouv
"Base Sirene des entreprises et de leurs etablissements" :
  - StockEtablissement_utf8.csv   (obligatoire, ~4 Go decompresse)
  - StockUniteLegale_utf8.csv     (optionnel, pour la denomination sociale)

    python sirene_stock.py --etab StockEtablissement_utf8.csv \
                           --unite StockUniteLegale_utf8.csv \
                           --out sirene_sante.csv

DuckDB lit le CSV en streaming : pas besoin de tout charger en RAM.
"""
import argparse
import csv
import sys

from common import load_config, naf_type_map, BASE_COLS

try:
    import duckdb
except ImportError:
    duckdb = None


def build(cfg, etab, unite, out, actifs_only=True):
    if duckdb is None:
        sys.exit("Le module 'duckdb' est requis (pip install duckdb).")
    tmap = naf_type_map(cfg)  # NAF sans point -> type
    in_list = ", ".join(f"'{c}'" for c in sorted(tmap.keys()))
    con = duckdb.connect()

    join = nom = ""
    if unite:
        join = (f"LEFT JOIN read_csv_auto('{unite}', all_varchar=true, ignore_errors=true) u "
                f"ON u.siren = e.siren")
        nom = "coalesce(u.denominationUniteLegale, e.enseigne1Etablissement, '')"
    else:
        nom = "coalesce(e.enseigne1Etablissement, '')"

    where_actif = "e.etatAdministratifEtablissement = 'A' AND" if actifs_only else ""
    q = f"""
        SELECT e.siren, e.siret,
               {nom} AS nom,
               e.activitePrincipaleEtablissement AS naf,
               coalesce(e.libelleCommuneEtablissement, '') AS commune,
               left(coalesce(e.codeCommuneEtablissement, ''), 2) AS departement
        FROM read_csv_auto('{etab}', all_varchar=true, ignore_errors=true) e
        {join}
        WHERE {where_actif}
              replace(coalesce(e.activitePrincipaleEtablissement, ''), '.', '') IN ({in_list})
    """
    rows = []
    for siren, siret, nom_, naf, com, dep in con.execute(q).fetchall():
        rows.append({"siren": siren, "siret": siret, "nom": nom_,
                     "type": tmap.get((naf or "").replace(".", "").upper(), "autre"),
                     "naf": naf, "libelle": "", "commune": com,
                     "departement": dep, "telephone": "", "source": "sirene"})
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=BASE_COLS)
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def main():
    ap = argparse.ArgumentParser(description="Collecte SIRENE exhaustive via le stock INSEE (DuckDB).")
    ap.add_argument("--etab", required=True, help="StockEtablissement_utf8.csv (INSEE Sirene).")
    ap.add_argument("--unite", help="StockUniteLegale_utf8.csv (denomination sociale).")
    ap.add_argument("--config", default="config.yml")
    ap.add_argument("--out", default="sirene_sante.csv")
    ap.add_argument("--tous-etats", action="store_true",
                    help="Inclure aussi les etablissements fermes (defaut: actifs seulement).")
    a = ap.parse_args()
    n = build(load_config(a.config), a.etab, a.unite, a.out, actifs_only=not a.tous_etats)
    print(f"SIRENE (stock): {n} etablissements sante -> {a.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
