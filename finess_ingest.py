#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
finess_ingest.py — Etablissements sante & medico-sociaux depuis FINESS
(open data, Licence Ouverte). Sortie alignee sur le schema commun.

Robuste au format : le classement par TYPE scanne la LIGNE ENTIERE (mots-cles
sante), donc il ne depend pas de la position des colonnes. Le SIRET, le telephone,
la commune et le departement sont eux aussi extraits par motif, pas par index fixe.
On GARDE les etablissements meme sans SIRET (nom + adresse + tel = prospect valable).

    python finess_ingest.py --download
    python finess_ingest.py --download --types soins medico-social --departements 69 38 01
    python finess_ingest.py --file finess_etab.csv --all --out finess_sante.csv

Tout en code : --download recupere le fichier depuis data.gouv, aucune manip manuelle.
"""
import argparse
import csv
import io
import re
import sys

from common import strip_acc, clean_siret, siren_of, norm_phone, BASE_COLS

try:
    import requests
except ImportError:
    requests = None

FINESS_CSV_URL = "https://www.data.gouv.fr/api/1/datasets/r/98f3161f-79ff-4f16-8f6a-6d571a80fea2"
# /!\ Flux arrete le 20/07/2026 -> bascule Finess+ (CSV a en-tete) :
#   org https://www.data.gouv.fr/organizations/agence-du-numerique-en-sante
#   specs https://github.com/ansforge/finess
USER_AGENT = "prospection-sante/1.0 (FINESS ingest)"
STRUCT_TAG = "structureet"

TYPE_KEYWORDS = {
    "soins": [
        "centre hospitalier", "hopital", "hospitali", "clinique", "chirurgic",
        "etablissement de soins", "soins medic", "soins plurid", "pluridisciplinaire",
        "medecine chirurgie", "activites hospitalieres", "soins de suite", "readaptation",
        "dialyse", "hemodialyse", "centre de sante", "maison de sante", "imagerie",
        "radiotherapie", "cancer", "psychiatri", "urgences", "hospitalisation a domicile",
        "perinatal", "dispensaire", "sante mentale"],
    "laboratoire": ["laboratoire", "biologie medicale", "analyses medicales"],
    "medico-social": [
        "personnes agees", "ehpad", "dependantes", "handicap", "polyhandicap", "autiste",
        "medico-social", "medico social", "foyer", "maison d'accueil", "accueil medicalise",
        "esat", "itep", "institut medico", "ssiad", "residence autonomie", "aide a domicile",
        "adultes handicapes", "enfants handicapes", "mecs", "csapa", "caarud", "mas ", "fam "],
    "pharmacie": ["pharmacie", "officine"],
}
DEFAULT_TYPES = ["soins", "laboratoire", "medico-social"]

RE_SIRET = re.compile(r"\d{14}")
RE_INSEE = re.compile(r"^[0-9][0-9AB]\d{3}$")   # code commune INSEE (5 car., Corse 2A/2B)
RE_LETTERS = re.compile(r"[A-Za-zÀ-ÿ]")


def classify(line):
    blob = strip_acc(line)
    for typ, kws in TYPE_KEYWORDS.items():
        if any(kw in blob for kw in kws):
            return typ
    return "autre"


def dep_from_finess(nofinesset, commune=""):
    n = (nofinesset or "").strip()
    if len(n) >= 2:
        if n[:2] in ("2A", "2B"):
            return n[:2]
        if n[:2] in ("97", "98"):
            return n[:3]
        if n[:2].isdigit():
            return n[:2]
    c = (commune or "").strip()
    return c[:2] if len(c) >= 2 else ""


def first_siret(parts):
    for p in parts:
        q = p.strip().strip('"')
        if re.fullmatch(r"\d{14}", q):
            return q
    return ""


def first_phone(parts):
    for p in parts:
        n = norm_phone(p.strip().strip('"'))
        if n:
            return n
    return ""


def first_insee(parts):
    for p in parts:
        q = p.strip().strip('"')
        if RE_INSEE.match(q):
            return q
    return ""


def first_text(parts):
    """Premier champ 'textuel' apres le tag : c'est la raison sociale."""
    for p in parts[1:]:
        q = p.strip().strip('"')
        if len(RE_LETTERS.findall(q)) >= 4:
            return q
    return ""


def cell(parts, i):
    return parts[i].strip().strip('"') if i < len(parts) else ""


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
        diagnosed = False
        for line in text.splitlines():
            parts = line.rstrip("\n").split(";")
            if not parts or parts[0].strip().strip('"').lower() != STRUCT_TAG:
                continue
            if not diagnosed:
                print(f"FINESS diag: {len(parts)} champs | debut: {line[:170]}", file=sys.stderr)
                diagnosed = True
            yield {"nofinesset": cell(parts, 1), "nom": first_text(parts) or cell(parts, 3),
                   "siret": first_siret(parts), "telephone": first_phone(parts),
                   "commune": first_insee(parts), "libelle": cell(parts, 19), "blob": line}
        return
    # Mode a en-tete (Finess+ / t_finess).
    delim = ";" if first.count(";") >= first.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delim)
    cols = {strip_acc(c): c for c in (reader.fieldnames or [])}

    def find(*cands):
        for cand in cands:
            for norm, orig in cols.items():
                if cand in norm:
                    return orig
        return None

    c = dict(fet=find("nofinesset", "finesset"), rs=find("raisonsociale", "rslongue", "raison", "rs", "nom"),
             siret=find("siret"), cat=find("libcategetab", "categorie", "categ"),
             com=find("commune", "depcom"), tel=find("telephone", "tel"))
    for row in reader:
        vals = " ".join(str(v) for v in row.values())
        yield {"nofinesset": row.get(c["fet"], "") if c["fet"] else "",
               "nom": row.get(c["rs"], "") if c["rs"] else "",
               "siret": clean_siret(row.get(c["siret"], "") if c["siret"] else ""),
               "telephone": norm_phone(row.get(c["tel"], "") if c["tel"] else ""),
               "commune": row.get(c["com"], "") if c["com"] else "",
               "libelle": row.get(c["cat"], "") if c["cat"] else "", "blob": vals}


def build(records, wanted_types, departements):
    seen, rows = set(), []
    stats = dict(total=0, sans_siret=0, hors_type=0, hors_dep=0)
    deps = set(departements or [])
    for rec in records:
        stats["total"] += 1
        dep = dep_from_finess(rec["nofinesset"], rec["commune"])
        typ = classify(rec["blob"])
        if wanted_types and typ not in wanted_types:
            stats["hors_type"] += 1
            continue
        if deps and dep not in deps:
            stats["hors_dep"] += 1
            continue
        key = rec["nofinesset"] or rec["siret"] or f"{rec['nom']}|{dep}"
        if key in seen:
            continue
        seen.add(key)
        siret = clean_siret(rec["siret"])
        siren = siren_of(siret)
        if not siren:
            stats["sans_siret"] += 1
        rows.append({"siren": siren, "siret": siret, "nom": rec["nom"], "type": typ,
                     "naf": "", "libelle": rec["libelle"], "commune": rec["commune"],
                     "departement": dep, "telephone": rec["telephone"], "source": "finess"})
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
    ap.add_argument("--out", default="finess_sante.csv")
    a = ap.parse_args()

    text = read_text(download() if a.download else a.file)
    wanted = None if a.all else a.types
    rows, st = build(parse_records(text), wanted, a.departements)
    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=BASE_COLS)
        w.writeheader()
        w.writerows(rows)
    print(f"FINESS: lues={st['total']} hors_type={st['hors_type']} hors_dep={st['hors_dep']} "
          f"-> {len(rows)} lignes ({st['sans_siret']} sans SIRET, gardees) dans {a.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
