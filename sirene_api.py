#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sirene_api.py — Collecte SIRENE des entreprises sante via l'API publique
recherche-entreprises.api.gouv.fr (aucune cle, aucun telechargement de fichier).

Itere chaque NAF x chaque departement et pagine, pour contourner le plafond de
resultats par requete. Plus lent que le mode 'stock', mais 100 % en code.

    python sirene_api.py                         # tous les NAF de config.yml, France entiere
    python sirene_api.py --departements 69 38 01 42 73 74
    python sirene_api.py --out sirene_sante.csv --sleep 0.2
"""
import argparse
import csv
import sys
import time

from common import (load_config, naf_type_map, all_departements,
                    clean_siret, siren_of, BASE_COLS)

try:
    import requests
except ImportError:
    requests = None

API = "https://recherche-entreprises.api.gouv.fr/search"
USER_AGENT = "prospection-sante/1.0 (SIRENE api)"
PER_PAGE = 25
MAX_PAGES = 400  # plafond de l'API : page * per_page <= ~10000


def fetch(session, naf, dep, page, sleep):
    params = {"activite_principale": naf, "departement": dep,
              "page": page, "per_page": PER_PAGE}
    for attempt in range(4):
        r = session.get(API, params=params, headers={"User-Agent": USER_AGENT}, timeout=30)
        if r.status_code == 429:
            time.sleep(2 * (attempt + 1))
            continue
        r.raise_for_status()
        return r.json()
    return {}


def parse_result(res, naf, typ):
    siege = res.get("siege") or {}
    siret = clean_siret(siege.get("siret"))
    siren = res.get("siren") or siren_of(siret)
    if len(siren) != 9:
        return None
    dep = siege.get("departement") or (siege.get("code_commune") or "")[:2]
    return {"siren": siren, "siret": siret,
            "nom": res.get("nom_raison_sociale") or res.get("nom_complet") or "",
            "type": typ, "naf": res.get("activite_principale") or naf,
            "libelle": res.get("libelle_activite_principale") or "",
            "commune": siege.get("libelle_commune") or "",
            "departement": dep, "telephone": "", "source": "sirene"}


def collect(cfg, nafs, deps, sleep):
    if requests is None:
        sys.exit("Le module 'requests' est requis (pip install requests).")
    tmap = naf_type_map(cfg)
    rows, sess = {}, requests.Session()
    for naf in nafs:
        typ = tmap.get(naf.replace(".", "").upper(), "autre")
        for dep in deps:
            page, pages = 1, 1
            while page <= pages:
                data = fetch(sess, naf, dep, page, sleep)
                if not data:
                    break
                pages = min(int(data.get("total_pages", 1) or 1), MAX_PAGES)
                for res in data.get("results", []):
                    row = parse_result(res, naf, typ)
                    if row:
                        rows[row["siren"]] = row
                page += 1
                time.sleep(sleep)
            print(f"  {naf} dep {dep} -> cumul {len(rows)}", file=sys.stderr)
    return list(rows.values())


def main():
    ap = argparse.ArgumentParser(description="Collecte SIRENE sante via API (sans telechargement).")
    ap.add_argument("--config", default="config.yml")
    ap.add_argument("--departements", nargs="+")
    ap.add_argument("--out", default="sirene_sante.csv")
    ap.add_argument("--sleep", type=float, default=0.2, help="Pause entre requetes (s).")
    a = ap.parse_args()

    cfg = load_config(a.config)
    nafs = [c for codes in cfg["naf"].values() for c in codes]
    deps = a.departements or (cfg.get("departements") or all_departements())
    rows = collect(cfg, nafs, deps, a.sleep)
    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=BASE_COLS)
        w.writeheader()
        w.writerows(rows)
    print(f"SIRENE (api): {len(rows)} entites -> {a.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
