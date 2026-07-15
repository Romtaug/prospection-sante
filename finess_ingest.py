#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
finess_ingest.py — Etablissements sante & medico-sociaux depuis FINESS
(open data, Licence Ouverte). Sortie alignee sur le schema commun.

Robuste au format reel :
 - detection automatique du delimiteur (; , tab |),
 - classement par TYPE sur la ligne entiere (independant de l'ordre des colonnes),
 - SIRET / telephone / commune / numero FINESS extraits par MOTIF (pas par index),
 - dedoublonnage qui ne peut PAS tout ecraser (cle unique si pas d'identifiant),
 - on GARDE les etablissements meme sans SIRET.
Une ligne de diagnostic affiche le mode, le delimiteur et un echantillon.

    python finess_ingest.py --download
    python finess_ingest.py --download --types soins medico-social --departements 69 38 01
    python finess_ingest.py --file finess_etab.csv --all --out finess_sante.csv
"""
import argparse
import csv
import re
import sys

from common import strip_acc, clean_siret, siren_of, norm_phone, BASE_COLS

try:
    import requests
except ImportError:
    requests = None

FINESS_CSV_URL = "https://www.data.gouv.fr/api/1/datasets/r/98f3161f-79ff-4f16-8f6a-6d571a80fea2"
# /!\ Flux arrete le 20/07/2026 -> bascule Finess+ (specs https://github.com/ansforge/finess).
USER_AGENT = "prospection-sante/1.0 (FINESS ingest)"

TYPE_KEYWORDS = {
    "soins": [
        "centre hospitalier", "hopital", "hospitali", "clinique", "chirurgic",
        "etablissement de soins", "soins medic", "soins plurid", "pluridisciplinaire",
        "medecine chirurgie", "activites hospitalieres", "soins de suite", "readaptation",
        "dialyse", "hemodialyse", "centre de sante", "maison de sante", "imagerie",
        "radiotherapie", "cancer", "psychiatri", "urgences", "hospitalisation a domicile",
        "perinatal", "dispensaire", "sante mentale", "medecins"],
    "laboratoire": ["laboratoire", "biologie medicale", "analyses medicales"],
    "medico-social": [
        "personnes agees", "personnes agees", "ehpad", "dependantes", "handicap",
        "polyhandicap", "autiste", "medico-social", "medico social", "foyer",
        "maison d'accueil", "accueil medicalise", "esat", "itep", "institut medico",
        "ssiad", "residence autonomie", "aide a domicile", "adultes handicapes",
        "enfants handicapes", "mecs", "csapa", "caarud", "aide sociale a l'enfance",
        "hebergement", "accueil de jour"],
    "pharmacie": ["pharmacie", "officine"],
}
DEFAULT_TYPES = ["soins", "laboratoire", "medico-social"]

RE_FINESS = re.compile(r"^(?:\d{9}|2[AB]\d{7})$")   # numero FINESS (9 car., Corse 2A/2B)
RE_SIRET = re.compile(r"^\d{14}$")
RE_INSEE = re.compile(r"^[0-9][0-9AB]\d{3}$")        # code commune INSEE (5 car.)
RE_LETTERS = re.compile(r"[A-Za-zÀ-ÿ]")
TAGS = ("structureet", "geolocalisation")


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


def detect_delim(line):
    return max([";", "\t", ",", "|"], key=lambda d: line.count(d))


def extract(cells):
    nofin = siret = phone = commune = nom = ""
    for c in cells:
        q = c.strip().strip('"')
        if q.lower() in TAGS or not q:
            continue
        if not nofin and RE_FINESS.match(q):
            nofin = q
        if not siret and RE_SIRET.match(q):
            siret = q
        if not commune and RE_INSEE.match(q):
            commune = q
        if not phone:
            n = norm_phone(q)
            if n:
                phone = n
        if not nom and len(RE_LETTERS.findall(q)) >= 4:
            nom = q
    return nofin, siret, phone, commune, nom


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
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines:
        return
    delim = detect_delim(lines[0])
    rows = list(csv.reader(lines, delimiter=delim))
    first_cell = rows[0][0].strip().strip('"').lower() if rows[0] else ""
    legacy = first_cell in TAGS
    data_rows = (r for r in rows if r and r[0].strip().strip('"').lower() == "structureet") if legacy \
        else iter(rows[1:])  # header CSV : on saute la ligne d'en-tete

    diagnosed = False
    for r in data_rows:
        if not diagnosed:
            mode = "legacy" if legacy else "header"
            print(f"FINESS diag: mode={mode} delim={delim!r} champs={len(r)} | row0: {delim.join(r)[:200]}",
                  file=sys.stderr)
            diagnosed = True
        nofin, siret, phone, commune, nom = extract(r)
        yield {"nofinesset": nofin, "siret": siret, "telephone": phone,
               "commune": commune, "nom": nom, "blob": " ".join(r)}


def build(records, wanted_types, departements):
    seen, rows = set(), []
    stats = dict(total=0, sans_siret=0, hors_type=0, hors_dep=0)
    deps = set(departements or [])
    uniq = 0
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
        if rec["nofinesset"]:
            key = "f:" + rec["nofinesset"]
        elif rec["siret"]:
            key = "e:" + rec["siret"]
        else:
            uniq += 1
            key = "u:%d" % uniq          # aucune collision possible
        if key in seen:
            continue
        seen.add(key)
        siret = clean_siret(rec["siret"])
        siren = siren_of(siret)
        if not siren:
            stats["sans_siret"] += 1
        rows.append({"siren": siren, "siret": siret, "nom": rec["nom"], "type": typ,
                     "naf": "", "libelle": "", "commune": rec["commune"],
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
