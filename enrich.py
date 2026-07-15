#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
enrich.py — Ajoute les contacts (site web, email GENERIQUE, telephone) a la base.
Entree : base_sante.csv   Sortie : base_sante_enrichi.csv

Conformite : on ne recupere que des emails generiques (contact@, info@, accueil@...)
trouves sur le site officiel de la structure. Aucun email nominatif (prenom.nom@)
n'est devine, ce serait une donnee personnelle.

    python enrich.py
    python enrich.py --in base_sante.csv --out base_sante_enrichi.csv --limit 500 --workers 12

Note : lance-le en LOCAL. Depuis une IP de datacenter (CI, cloud), beaucoup de sites
bloquent le scraping. En local, ton IP residentielle passe bien mieux.
"""
import argparse
import csv
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from common import load_config, strip_acc, slugify, norm_phone, USER_AGENT, BASE_COLS, ENRICH_COLS

try:
    import requests
except ImportError:
    requests = None
try:
    import dns.resolver
except ImportError:
    dns = None

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
CF_HEX = re.compile(r'data-cfemail="([0-9a-fA-F]{8,})"')
PHONE_RE = re.compile(r"(?<!\d)(?:\+33\s?|0033\s?|0)[1-9](?:[\s.\-]?\d{2}){4}(?!\d)")
_AT = r'(?:\s*\[at\]\s*|\s*\(at\)\s*|\s+arobase\s+|@)'
_DOT = r'(?:\s*\[dot\]\s*|\s*\(dot\)\s*|\s+point\s+|\.)'
OBF_RE = re.compile(r'([a-z0-9._%+\-]+)' + _AT + r'([a-z0-9.\-]+)' + _DOT + r'([a-z]{2,})', re.I)
PLACEHOLDER = ("example", "domain", "votre", "your", "yourname", "sentry", "wix",
               "@2x", ".png", ".jpg", ".jpeg", ".gif", ".webp", "email@")


def cf_decode(hexstr):
    try:
        b = bytes.fromhex(hexstr)
        return "".join(chr(c ^ b[0]) for c in b[1:])
    except Exception:
        return ""


def emails_from_html(html):
    found = set()
    for m in EMAIL_RE.findall(html or ""):
        found.add(m.lower())
    for a, b, c in OBF_RE.findall(html or ""):
        found.add(f"{a}@{b}.{c}".lower())
    for hx in CF_HEX.findall(html or ""):
        d = cf_decode(hx)
        if "@" in d:
            found.add(d.lower())
    return {e for e in found if not any(p in e for p in PLACEHOLDER) and 5 <= len(e) <= 60}


def phones_from_html(html):
    out = []
    for m in PHONE_RE.findall(html or ""):
        n = norm_phone(m)
        if n and n not in out:
            out.append(n)
    return out


def guess_domains(nom, tlds):
    stem = slugify(nom)
    return [f"{stem}.{t}" for t in tlds] if len(stem) >= 3 else []


def http_get(url, timeout):
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout, allow_redirects=True)
        if r.status_code == 200 and "html" in r.headers.get("content-type", "").lower():
            return r.text
    except Exception:
        pass
    return ""


def plausible(html, nom):
    h = strip_acc(html)
    toks = [t for t in re.split(r"[^a-z0-9]+", strip_acc(nom)) if len(t) >= 4]
    return any(t in h for t in toks) if toks else True


def resolve_domain(nom, cfg):
    for dom in guess_domains(nom, cfg["tlds"]):
        for scheme in ("https://", "http://"):
            html = http_get(scheme + dom, cfg["timeout_http"])
            if html and plausible(html, nom):
                return dom, scheme + dom, html
    return "", "", ""


def pick_email(emails, domain, role_prefixes):
    same = [e for e in emails if e.split("@")[-1].endswith(domain)] or list(emails)
    for e in same:
        local = e.split("@")[0]
        if any(local == p or local.startswith(p) for p in role_prefixes):
            return e, "site-generique"
    return (same[0], "site") if same else ("", "")


def verify_mx(domain):
    if dns is None or not domain:
        return "unknown"
    try:
        return "mx_ok" if dns.resolver.resolve(domain, "MX", lifetime=4) else "no_mx"
    except Exception:
        return "no_mx"


def enrich_one(row, cfg):
    out = {c: "" for c in ENRICH_COLS}
    out["telephone"] = ""
    try:
        domain, base, home = resolve_domain(row.get("nom", ""), cfg)
        if not domain:
            return out
        out["domain"] = domain
        emails, phones = set(), []
        for path in cfg["paths_a_scanner"]:
            html = home if path == "/" else http_get(base + path, cfg["timeout_http"])
            if not html:
                continue
            emails |= emails_from_html(html)
            phones += [p for p in phones_from_html(html) if p not in phones]
        emails = {e for e in emails if not any(d in e.split("@")[-1] for d in cfg["domaines_ignores"])}
        email, src = pick_email(emails, domain, cfg["role_prefixes"])
        out["email"] = email
        out["email_source"] = src
        out["email_status"] = verify_mx(email.split("@")[-1]) if email else ""
        if not row.get("telephone") and phones:
            out["telephone"] = phones[0]
    except Exception:
        pass
    return out


def main():
    ap = argparse.ArgumentParser(description="Enrichit la base sante (site, email generique, tel).")
    ap.add_argument("--in", dest="inp", default="base_sante.csv")
    ap.add_argument("--out", default="base_sante_enrichi.csv")
    ap.add_argument("--config", default="config.yml")
    ap.add_argument("--limit", type=int, help="Limiter le nombre de lignes (tests).")
    ap.add_argument("--workers", type=int)
    a = ap.parse_args()
    if requests is None:
        sys.exit("Le module 'requests' est requis (pip install -r requirements.txt).")

    cfg = load_config(a.config)["enrichissement"]
    if a.workers:
        cfg["workers"] = a.workers
    with open(a.inp, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if a.limit:
        rows = rows[: a.limit]

    results, done = [], 0
    with ThreadPoolExecutor(max_workers=cfg["workers"]) as ex:
        futs = {ex.submit(enrich_one, r, cfg): r for r in rows}
        for fut in as_completed(futs):
            r = futs[fut]
            upd = fut.result()
            merged = {c: r.get(c, "") for c in BASE_COLS}
            if upd.get("telephone") and not merged.get("telephone"):
                merged["telephone"] = upd["telephone"]
            for c in ENRICH_COLS:
                merged[c] = upd.get(c, "")
            results.append(merged)
            done += 1
            if done % 50 == 0:
                print(f"  {done}/{len(rows)}", file=sys.stderr)

    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=BASE_COLS + ENRICH_COLS)
        w.writeheader()
        w.writerows(results)
    n_mail = sum(1 for r in results if r["email"])
    n_tel = sum(1 for r in results if r["telephone"])
    print(f"ENRICH: {len(results)} lignes -> {a.out} | emails trouves={n_mail}, tel={n_tel}", file=sys.stderr)


if __name__ == "__main__":
    main()
