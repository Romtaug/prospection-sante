#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
finess_ingest.py - Etablissements sante & medico-sociaux depuis FINESS
(open data, Licence Ouverte). Sortie alignee sur le schema commun.

Sources, essayees dans l'ordre (repli automatique) :
 1. Finess+ "FINESS - Structures" : flux QUOTIDIEN nouvelle generation (JSON
    gzip, entites juridiques PMEJ avec leurs etablissements EGE imbriques).
    Schema officiel : github.com/ansforge/finess.
 2. Flux historique CSV : FIGE au 04/05/2026 (l'ANS a arrete sa generation
    le 20/07/2026), garde uniquement en secours.

Robuste au format : classement par TYPE sur le texte complet de chaque
etablissement (mots-cles) complete par les codes categorie surs ; numero
FINESS / SIRET / telephone / commune extraits par MOTIF et par cle tolerante,
jamais par position. On GARDE les etablissements sans SIRET. Le dedoublonnage
ne peut pas ecraser des lignes distinctes. Une ligne "FINESS diag" decrit la
source reellement lue.

    python finess_ingest.py --download
    python finess_ingest.py --download --types soins medico-social --departements 69 38 01
    python finess_ingest.py --file finess-structures.json.gz --all
"""
import argparse
import csv
import gzip
import json
import re
import sys

from common import strip_acc, clean_siret, siren_of, norm_phone, BASE_COLS

try:
    import requests
except ImportError:
    requests = None

FINESS_SOURCES = [
    ("https://www.data.gouv.fr/api/1/datasets/r/cd493959-fb03-41e5-9347-0edd14dfbc22",
     "Finess+ structures (JSON quotidien)"),
    ("https://www.data.gouv.fr/api/1/datasets/r/98f3161f-79ff-4f16-8f6a-6d571a80fea2",
     "flux historique CSV (fige au 04/05/2026, secours)"),
]
USER_AGENT = "prospection-sante/1.0 (FINESS ingest)"
MIN_ROWS_OK = 1000   # en dessous, on tente la source suivante

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
        "personnes agees", "ehpad", "dependantes", "handicap", "polyhandicap", "autiste",
        "medico-social", "medico social", "foyer", "maison d'accueil", "accueil medicalise",
        "esat", "itep", "institut medico", "ssiad", "residence autonomie", "aide a domicile",
        "adultes handicapes", "enfants handicapes", "mecs", "csapa", "caarud",
        "aide sociale a l'enfance", "hebergement", "accueil de jour"],
    "pharmacie": ["pharmacie", "officine"],
}
DEFAULT_TYPES = ["soins", "laboratoire", "medico-social"]

# Codes categorie FINESS surs (complement du classement par mots-cles,
# utile quand le nom ne dit rien, ex. "RESIDENCE LES TILLEULS" code 500).
CODE_CATEG = {"101": "soins", "355": "soins", "500": "medico-social",
              "202": "medico-social", "611": "laboratoire"}

RE_FINESS = re.compile(r"^(?:\d{9}|2[AB]\d{7})$")
RE_SIRET = re.compile(r"^\d{14}$")
RE_INSEE = re.compile(r"^[0-9][0-9AB]\d{3}$")
RE_CP = re.compile(r"^\d{5}$")
RE_CPVILLE = re.compile(r"^\d{5}\s+\S.*$")
RE_LETTERS = re.compile(r"[A-Za-zÀ-ÿ]")
RE_HASLETTER = re.compile(r"^.*[A-Za-zÀ-ÿ]")
TAGS = ("structureet", "geolocalisation")


def classify(blob):
    b = strip_acc(blob)
    for typ, kws in TYPE_KEYWORDS.items():
        if any(kw in b for kw in kws):
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


# ----------------------------------------------------------------------------
#  Telechargement + detection de format
# ----------------------------------------------------------------------------
def download(url, label):
    if requests is None:
        sys.exit("Le module 'requests' est requis (pip install requests).")
    print(f"Telechargement FINESS ({label}) : {url}", file=sys.stderr)
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=300)
    r.raise_for_status()
    print(f"  {len(r.content)/1e6:.1f} Mo recus.", file=sys.stderr)
    return r.content


def decode_payload(data):
    """bytes -> ("json", objet) ou ("csv", texte). Gere le gzip."""
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    head = data.lstrip()[:1]
    if head in (b"{", b"["):
        return "json", json.loads(data.decode("utf-8", errors="replace"))
    for enc in ("utf-8-sig", "latin-1"):
        try:
            return "csv", data.decode(enc)
        except UnicodeDecodeError:
            continue
    return "csv", data.decode("latin-1", errors="replace")


# ----------------------------------------------------------------------------
#  Mode JSON (Finess+ : pmej[] -> ege[])
# ----------------------------------------------------------------------------
def _iter_kv(node):
    if isinstance(node, dict):
        for k, v in node.items():
            yield k, v
            yield from _iter_kv(v)
    elif isinstance(node, list):
        for it in node:
            yield from _iter_kv(it)


def _texts(node):
    out = []
    for _, v in _iter_kv(node):
        if isinstance(v, str) and v.strip():
            out.append(v.strip())
        elif isinstance(v, (int, float)):
            out.append(str(v))
    return out


def _find(node, frags, pattern=None):
    for k, v in _iter_kv(node):
        if not isinstance(v, (str, int)):
            continue
        s = str(v).strip()
        if not s:
            continue
        kl = strip_acc(str(k))
        if any(f in kl for f in frags) and (pattern is None or pattern.match(s)):
            return s
    return ""


def parse_json_records(obj):
    pmejs = obj.get("pmej") or []
    diagnosed = False
    for pm in pmejs:
        info_pm = pm.get("informationsGeneralesPMEJ") or {}
        ej_nom = (info_pm.get("denominationLonguePmSmsse") or info_pm.get("denominationPm") or "").strip()
        ej_siren = str(info_pm.get("siren") or "").strip()
        if not (len(ej_siren) == 9 and ej_siren.isdigit()):
            ej_siren = ""
        for ege in (pm.get("ege") or []):
            info = ege.get("informationsGeneralesEGE") or {}
            if not diagnosed:
                print(f"FINESS diag: mode=json (Finess+) pmej={len(pmejs)} | cles 1er EGE: "
                      f"{sorted(info.keys())[:8]} | categ: {str(ege.get('categorieentiteGeographiqueExercice'))[:60]}",
                      file=sys.stderr)
                diagnosed = True
            nofin = _find(info, ("finess",), RE_FINESS) or _find(ege, ("finess",), RE_FINESS)
            siret = _find(ege, ("siret",), RE_SIRET)
            nom = (info.get("nomEgeLong") or info.get("nomEgeCourt")
                   or _find(info, ("nomege", "denomination"), RE_HASLETTER) or ej_nom)
            texts = _texts(ege)
            phone = ""
            for t in texts:
                phone = norm_phone(t)
                if phone:
                    break
            commune = next((t for t in texts if RE_CPVILLE.match(t)), "")
            if not commune:
                cp = _find(ege, ("codepostal",), RE_CP)
                ville = _find(ege, ("libellecommune", "nomcommune", "ville"), RE_HASLETTER)
                commune = f"{cp} {ville}".strip() or _find(ege, ("commune",), RE_INSEE)
            categ = str(ege.get("categorieentiteGeographiqueExercice") or "").strip()
            yield {"nofinesset": nofin, "siret": siret, "telephone": phone,
                   "commune": commune, "nom": nom, "libelle": categ,
                   "siren_ej": ej_siren, "type_hint": CODE_CATEG.get(categ),
                   "blob": " ".join(texts) + " " + ej_nom + " " + categ}


# ----------------------------------------------------------------------------
#  Mode CSV (flux historique fige + tout CSV a en-tete)
# ----------------------------------------------------------------------------
def detect_delim(line):
    return max([";", "\t", ",", "|"], key=lambda d: line.count(d))


def extract(cells):
    nofin = siret = phone = commune = cpville = nom = ""
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
        if not cpville and RE_CPVILLE.match(q):
            cpville = q
        if not phone:
            n = norm_phone(q)
            if n:
                phone = n
        if not nom and not RE_CPVILLE.match(q) and len(RE_LETTERS.findall(q)) >= 4:
            nom = q
    return nofin, siret, phone, (cpville or commune), nom


def parse_records(text):
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines:
        return
    delim = detect_delim(lines[0])
    rows = list(csv.reader(lines, delimiter=delim))

    def tag(r):
        return r[0].strip().strip('"').lower() if r else ""

    legacy = any(tag(r) in TAGS for r in rows[:5])
    if legacy:
        data_rows = (r for r in rows if tag(r) == "structureet"
                     and (len(r) < 2 or r[1].strip().strip('"').lower() != "nofinesset"))
    else:
        data_rows = (r for r in rows[1:] if tag(r) != "geolocalisation")

    diagnosed = False
    for r in data_rows:
        if not diagnosed:
            mode = "legacy" if legacy else "header"
            print(f"FINESS diag: mode={mode} delim={delim!r} champs={len(r)} | row0: {delim.join(r)[:200]}",
                  file=sys.stderr)
            diagnosed = True
        nofin, siret, phone, commune, nom = extract(r)
        yield {"nofinesset": nofin, "siret": siret, "telephone": phone,
               "commune": commune, "nom": nom, "libelle": "",
               "siren_ej": "", "type_hint": None, "blob": " ".join(r)}


# ----------------------------------------------------------------------------
#  Construction commune
# ----------------------------------------------------------------------------
def build(records, wanted_types, departements):
    seen, rows = set(), []
    stats = dict(total=0, sans_siret=0, hors_type=0, hors_dep=0)
    deps = set(departements or [])
    uniq = 0
    for rec in records:
        stats["total"] += 1
        dep = dep_from_finess(rec["nofinesset"], rec["commune"])
        typ = rec.get("type_hint") or classify(rec["blob"])
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
            key = "u:%d" % uniq
        if key in seen:
            continue
        seen.add(key)
        siret = clean_siret(rec["siret"])
        siren = siren_of(siret) or rec.get("siren_ej", "")
        if not siren:
            stats["sans_siret"] += 1
        rows.append({"siren": siren, "siret": siret, "nom": rec["nom"], "type": typ,
                     "naf": "", "libelle": rec.get("libelle", ""), "commune": rec["commune"],
                     "departement": dep, "telephone": rec["telephone"], "source": "finess"})
    return rows, stats


def records_of(kind, payload):
    return parse_json_records(payload) if kind == "json" else parse_records(payload)


def main():
    ap = argparse.ArgumentParser(description="Etablissements sante depuis FINESS (Finess+ puis secours).")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--download", action="store_true")
    src.add_argument("--file")
    ap.add_argument("--types", nargs="+", default=DEFAULT_TYPES,
                    choices=["soins", "laboratoire", "medico-social", "pharmacie", "autre"])
    ap.add_argument("--all", action="store_true", help="Garde tous les types.")
    ap.add_argument("--departements", nargs="+")
    ap.add_argument("--out", default="finess_sante.csv")
    a = ap.parse_args()
    wanted = None if a.all else a.types

    best = ([], dict(total=0, sans_siret=0, hors_type=0, hors_dep=0))
    if a.download:
        for url, label in FINESS_SOURCES:
            try:
                kind, payload = decode_payload(download(url, label))
                rows, st = build(records_of(kind, payload), wanted, a.departements)
                if len(rows) > len(best[0]):
                    best = (rows, st)
                if len(rows) >= MIN_ROWS_OK:
                    break
                print(f"  ! seulement {len(rows)} lignes via {label}, essai de la source suivante",
                      file=sys.stderr)
            except Exception as e:
                print(f"  ! echec source ({label}) : {type(e).__name__}: {e}", file=sys.stderr)
    else:
        with open(a.file, "rb") as f:
            kind, payload = decode_payload(f.read())
        best = build(records_of(kind, payload), wanted, a.departements)
    rows, st = best

    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=BASE_COLS)
        w.writeheader()
        w.writerows(rows)
    print(f"FINESS: lues={st['total']} hors_type={st['hors_type']} hors_dep={st['hors_dep']} "
          f"-> {len(rows)} lignes ({st['sans_siret']} sans SIRET, gardees) dans {a.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
