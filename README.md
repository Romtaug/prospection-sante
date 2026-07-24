# Prospection Sante - la base la plus complete (B2B)

Construit la base la plus complete des personnes morales du secteur sante
francais (SIRENE + FINESS fusionnes, dedupliques au niveau etablissement),
puis l'enrichit au maximum : fiche officielle, finances, dirigeants, labels,
TVA, signaux BODACC, site web, email generique, telephone, LinkedIn, score.

Sorties : `data/base_sante.csv` (via GitHub Actions) puis
`base_sante_enrichi.csv` (42 colonnes, en local ou entierement via GitHub Actions).

## Regle de perimetre

Personnes morales uniquement. Les praticiens en solo (RPPS) sont exclus :
donnees personnelles, charte RPPS, coordonnees non publiques. On cible la
structure et son contact generique, jamais le praticien nominatif. Aucun
email nominatif (prenom.nom@) n'est devine.

## Demarrage rapide

```bash
pip install -r requirements.txt
python run_all.py                 # construit base_sante.csv (SIRENE + FINESS)
python enrich.py --limit 500      # enrichit 500 lignes ; relancer pour continuer
python enrich.py --stats          # ou en suis-je ?
```

Le workflow GitHub Actions (`.github/workflows/refresh.yml`) reconstruit et
commite `data/base_sante.csv` chaque mois. `enrich.py` detecte ce fichier
automatiquement.

## Etape 1 - la base (collecte + fusion)

- `sirene_api.py` : entreprises sante via l'API Recherche d'entreprises
  (requetes combinees par departement, retries, jamais fatale).
- `sirene_stock.py` : alternative exhaustive via le stock INSEE (DuckDB,
  fichier ~4 Go a telecharger une fois).
- `finess_ingest.py` : etablissements sanitaires et medico-sociaux. Lit en
  priorite le NOUVEAU flux Finess+ "FINESS - Structures" (JSON quotidien,
  depuis le 20/07/2026), avec repli automatique sur l'ancien flux CSV (fige
  au 04/05/2026). Robuste au format (extraction par motifs et cles, jamais
  par position) ; ignore les etablissements FERMES, garde ceux sans SIRET et herite le
  SIREN de l'entite juridique quand l'etablissement n'a pas le sien.
- `build_base.py` : fusion + dedoublonnage par ETABLISSEMENT (chaque site
  d'un groupe reste une ligne ; `--par-entreprise` pour une ligne par SIREN).

## Etape 2 - l'enrichissement maximal (`enrich.py`, en LOCAL)

Pour chaque ligne, ajoute 32 colonnes :

- Fiche officielle (API Recherche d'entreprises, 1 appel par SIREN, cache) :
  effectif, categorie PME/ETI/GE, nature juridique, date de creation, nb
  d'etablissements, dirigeant principal + 4 autres (avec fonction), CA,
  CA n-1, resultat net, exercice, labels (RGE, Qualiopi, ESS, Bio, societe
  a mission...), convention collective (IDCC), adresse complete, latitude/
  longitude. Complete aussi nom, NAF, libelle et commune des lignes FINESS.
- TVA intracommunautaire (formule officielle, calcul local).
- Signaux BODACC (`--bodacc`) : annonces legales + tier "exclu" si procedure
  collective ou radiation.
- Contacts : site officiel (devine depuis le nom puis verifie, repli
  DuckDuckGo), scraping des pages contact / mentions legales : email
  generique (contact@, accueil@...), autres emails, telephone normalise,
  page LinkedIn ; verification MX du domaine.
- Score 0-100 pondere (contactabilite, finances, taille, anciennete,
  dirigeant, labels) et tier A / B / C, avec le detail des raisons.

### Reprise automatique (execution en plusieurs fois)

La sortie est completee a chaque run, les lignes deja faites sont sautees,
et chaque ligne est ecrite immediatement (un crash ne perd rien) :

```bash
python enrich.py --limit 500                                   # run apres run
python enrich.py --limit 2000 --types laboratoire soins        # par segment
python enrich.py --departements 69 38 01 42 73 74              # par zone
python enrich.py --tiers-min-tel --limit 1000                  # d'abord les lignes avec telephone
python enrich.py --sans-web --limit 5000                       # fiche officielle seule (rapide)
python enrich.py --stats
```

Strategie conseillee sur ~276 000 lignes : enrichir par segment prioritaire
(ex. laboratoires + cliniques de ta region), et etendre ensuite. Compter
environ 1 a 3 s par ligne avec le web (30 min pour 500 lignes a 8 workers),
beaucoup moins avec `--sans-web`.

### Tout via GitHub Actions (`.github/workflows/enrich.yml`)

Le workflow "Enrichissement base sante" fait tout sur GitHub, chaque nuit :
phase 1 (fiche officielle des nouvelles lignes) puis phase 2 (contacts web des
lignes fichees). L'avancement est sauvegarde sur la branche `enrichi` du repo
(`data/base_sante_enrichi.csv.gz`, compresse pour la limite GitHub de 100 Mo),
et le CSV lisible est publie en artefact a chaque run. Lancement manuel
possible (Run workflow) avec mode, volumes et filtres.

A savoir : depuis les IP GitHub le taux d'emails trouves est plus faible qu'en
local ; et sur un repo prive gratuit, le quota Actions est de 2000 min/mois.
La phase fiche complete coute ~600 min ; le web complet 1500 a 5000 min selon
les sites. Options si besoin : filtrer le web sur les segments prioritaires,
etaler sur 2 mois, ou completer en local (`python enrich.py --completer-web`)
sur le meme fichier.

## Prospection : les regles B2B (rappel)

- Email B2B : opt-out / interet legitime. Privilegie les emails generiques
  (`email_source = site-generique`). LIA documentee, lien de desinscription
  1 clic, registre, purge a 3 ans. Pas de Brevo/Mailchimp sur une liste
  froide : outil de cold email dedie, domaine dedie, chauffe progressive.
- Telephone B2B : autorise (la reforme du 11/08/2026 ne vise que le B2C).
  Appels humains, opt-out immediat.
- SMS a froid : non conforme, a eviter.
- Jamais de canal renovation energetique grand public via cette base.

## Sources et licence

SIRENE (INSEE), FINESS (ANS), BODACC (DILA), API Recherche d'entreprises
(Etat) - Licence Ouverte / Etalab. Ce depot ne redistribue aucune donnee
personnelle : emails generiques de structures uniquement.
