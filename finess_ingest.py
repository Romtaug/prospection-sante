#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
finess_ingest.py — Etablissements sante & medico-sociaux depuis FINESS
(open data, Licence Ouverte). Sortie alignee sur le schema commun de la base.

    python finess_ingest.py --download
    python finess_ingest.py --download --types soins medico-social --departements 69 38 01
    python finess_ingest.py --file finess_etab.csv --out finess_sante.csv

Tout en code : --download recupere le fichier depuis data.gouv, aucune manip manuelle.
"""
import argparse
import csv
import io
import sys

from common import strip_acc, clean_siret, siren_of, BASE_COLS

try:
    import requests
except ImportError:
    requests = None

# --- SOURCE -----------------------------------------------------------------
# Flux "FINESS - Extraction du fichier des etablissements" (data.gouv, URL stable).
FINESS_CSV_URL = "https://www.data.gouv.fr/api/1/datasets/r/98f3161f-79ff-4f16-8f6a-6d571a80fea2"
# /!\ Ce flux s'arrete le 20/07/2026 -> bascule sur le flux Finess+ (CSV a en-tete) :
#   org  : https://www.data.gouv.fr/organizations/agence-du-numerique-en-sante
#   specs: https://github.com/ansforge/finess   (jeux "finess-structures" / "finess-activites")
# Le parseur ci-dessous gere deja les CSV a en-tete : remplace juste FINESS_CSV_URL.
USER_AGENT = "prospection-sante/1.0 (FINESS ingest)"

STRUCT_TAG = "structureet"
COL = {"nofinesset": 1, "nofinessej": 2, "rs": 3, "commune": 12,
       "departement": 13, "telephone": 16, "categ_lib": 19, "categagr_lib": 21, "siret": 22}

TYPE_KEYWORDS = {
    "soins": [
        "centre hospitalier", "hopital", "hospitali", "clinique", "chirurgic",
        "etablissement de soins", "soins medic", "soins plurid", "pluridisciplinaire",
        "medecine chirurgie", "soins de suite", "readaptation", "dialyse", "hemodialyse",
        "centre de sante", "maison de sante", "imagerie", "radiotherapie", "cancer",
        "psychiatri", "urgences", "hospitalisation a domicile", "perinatal", "dispensaire"],
    "laboratoire": ["laboratoire", "biologie medicale"],
    "medico-social": [
        "personnes agees", "ehpad", "dependantes", "handicap", "polyhandicap", "autiste",
        "medico-social", "medico social", "foyer", "maison d'accueil", "accueil medicalise",
        "esat", "itep", "institut medico", "ssiad", "residence autonomie", "aide a domicile",
        "adultes handicapes", "enfants handicapes"],
    "pharmacie": ["pharmacie", "officine"],
}
DEFAULT_TYPES = ["soins", "laboratoire", "medico-social"]


def classify(*labels):
    blob = " ".join(strip_acc(x) for x in labels if x)
    for typ, kws in TYPE_KEYWORDS.items():
        if any(kw in blob for kw in kws):
            return typ
    return "autre"


def dep_of(commune, departement):
    com = (commune or "").strip()
    if len(com) >= 2:
        return com[:3] if com[:2] in ("97", "98") else com[:2]
    return (departement or "").strip()


def read_text(src):
    data = src if isinstance(src, bytes) else open(src, "rb").read()
    for enc in ("utf-8", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="replace")


def download():
    if requests is None:
        sys.exit("Le module 'requests' est requis (pip install requests).")
    print(f"Telechargement FINESS depuis {FINESS_CSV_URL} ...", file=sys.stderr)
    r = requests.get(FINESS_CSV_URL, headers={"User-Agent": USER_AGENT}, timeout=180)
    r.raise_for_status()
    print(f"  {len(r.content)/1e6:.1f} Mo recus.", file=sys.stderr)
    return r.content


def parse_records(text):
    first = next((l for l in text.splitlines() if l.strip()), "")
    head = first.split(";")
    legacy = bool(head) and head[0].strip().strip('"').lower() in (STRUCT_TAG, "geolocalisation")
    if legacy:
        for line in text.splitlines():
            parts = line.rstrip("\n").split(";")
            if not parts or parts[0].strip().strip('"').lower() != STRUCT_TAG:
                continue

            def g(key):
                i = COL[key]
                return parts[i].strip().strip('"') if i < len(parts) else ""

            yield dict(rs=g("rs"), siret=g("siret"), categ_lib=g("categ_lib"),
                       categagr_lib=g("categagr_lib"), commune=g("commune"),
                       departement=g("departement"), telephone=g("telephone"), siren_only="")
        return
    delim = ";" if first.count(";") >= first.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delim)
    cols = {strip_acc(c): c for c in (reader.fieldnames or [])}

    def find(*cands):
        for cand in cands:
            for norm, orig in cols.items():
                if cand in norm:
                    return orig
        return None

    c = dict(siret=find("siret"), siren=find("siren"),
             rs=find("raisonsociale", "rslongue", "raison", "rs", "nom"),
             cat=find("libcategetab", "categorie", "categ"),
             com=find("commune", "depcom"), dep=find("departement", "dep"),
             tel=find("telephone", "tel"))
    for row in reader:
        yield dict(rs=row.get(c["rs"], "") if c["rs"] else "",
                   siret=row.get(c["siret"], "") if c["siret"] else "",
                   categ_lib=row.get(c["cat"], "") if c["cat"] else "",
                   categagr_lib="", commune=row.get(c["com"], "") if c["com"] else "",
                   departement=row.get(c["dep"], "") if c["dep"] else "",
                   telephone=row.get(c["tel"], "") if c["tel"] else "",
                   siren_only="".join(ch for ch in (row.get(c["siren"], "") if c["siren"] else "") if ch.isdigit()))


def build(records, wanted_types, departements, by_site):
    seen, rows = set(), []
    stats = dict(total=0, sans_siren=0, hors_type=0, hors_dep=0)
    deps = set(departements or [])
    for rec in records:
        stats["total"] += 1
        siret = clean_siret(rec.get("siret"))
        siren = siren_of(siret, rec.get("siren_only"))
        if len(siren) != 9:
            stats["sans_siren"] += 1
            continue
        typ = classify(rec.get("categ_lib"), rec.get("categagr_lib"))
        if wanted_types and typ not in wanted_types:
            stats["hors_type"] += 1
            continue
        dep = dep_of(rec.get("commune"), rec.get("departement"))
        if deps and dep not in deps:
            stats["hors_dep"] += 1
            continue
        key = siret if (by_site and siret) else siren
        if key in seen:
            continue
        seen.add(key)
        rows.append({"siren": siren, "siret": siret, "nom": rec.get("rs", ""),
                     "type": typ, "naf": "", "libelle": rec.get("categ_lib", ""),
                     "commune": rec.get("commune", ""), "departement": dep,
                     "telephone": (rec.get("telephone") or "").strip(), "source": "finess"})
    return rows, stats


def main():
    ap = argparse.ArgumentParser(description="Etablissements sante depuis FINESS.")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--download", action="store_true")
    src.add_argument("--file")
    ap.add_argument("--types", nargs="+", default=DEFAULT_TYPES,
                    choices=["soins", "laboratoire", "medico-social", "pharmacie", "autre"])
    ap.add_argument("--all", action="store_true", help="Garde tous les types.")
    ap.add_argument("--departements", nargs="+")
    ap.add_argument("--by-site", action="store_true", help="Une ligne par SIRET au lieu d'une par SIREN.")
    ap.add_argument("--out", default="finess_sante.csv")
    a = ap.parse_args()

    text = read_text(download() if a.download else a.file)
    wanted = None if a.all else a.types
    rows, st = build(parse_records(text), wanted, a.departements, a.by_site)
    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=BASE_COLS)
        w.writeheader()
        w.writerows(rows)
    print(f"FINESS: lues={st['total']} sans_siren={st['sans_siren']} "
          f"hors_type={st['hors_type']} hors_dep={st['hors_dep']} -> {len(rows)} lignes dans {a.out}",
          file=sys.stderr)


if __name__ == "__main__":
    main()
