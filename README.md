# Prospection Santé — la base la plus complète (B2B)

Construit **la base la plus complète des personnes morales du secteur santé
français** à partir de données publiques ouvertes, la déduplique, la type, et
(en option) l'enrichit en contacts. Sortie finale : `base_sante.csv`
(ou `base_sante_enrichi.csv` avec les emails et téléphones).

Deux sources fusionnées :

- **SIRENE** (INSEE) : toutes les entreprises dont le code NAF est un code santé.
- **FINESS** (ANS) : les établissements sanitaires et médico-sociaux que le NAF
  rate (hôpitaux publics, médico-social associatif dont l'entité juridique porte
  un code "administration" ou "association").

## Règle de périmètre

**Personnes morales uniquement.** Les praticiens en solo (médecins, dentistes,
infirmiers, kinés, via le RPPS) sont **exclus** : ce sont des personnes physiques,
la charte de réutilisation RPPS interdit leur usage en prospection, et leurs
coordonnées ne sont de toute façon pas en accès libre. On cible **la structure et
son contact générique, jamais le praticien nominatif**. La liste des NAF libéraux
exclus est dans `config.yml`. L'enrichissement ne collecte que des emails
**génériques** (`contact@`, `info@`...), jamais d'email nominatif.

## Démarrage rapide

```bash
pip install -r requirements.txt
python run_all.py --enrich            # collecte + fusion + emails, en une commande
```

Résultat : `base_sante_enrichi.csv`.

## Ce que ça produit

`base_sante.csv` puis `base_sante_enrichi.csv`, colonnes :

```
siren, siret, nom, type, naf, libelle, commune, departement, telephone,
source, domain, email, email_source, email_status, linkedin
```

`type` ∈ soins, laboratoire, pharmacie, distribution, industrie, dispositif,
medico_social, fournisseur. `source` ∈ `sirene`, `finess`, `finess+sirene`.
`email_source` ∈ `site-generique`, `site`. `email_status` = résultat de la
vérification MX du domaine.

## Le pipeline en détail (4 étapes)

`run_all.py` enchaîne tout, mais chaque étape est un script autonome.

**1. SIRENE — deux modes au choix :**

```bash
# Mode API : zero telechargement, 100% en code, mais plus lent (pagine NAF x departement)
python sirene_api.py

# Mode STOCK : le plus complet et rapide a requeter, mais necessite le fichier
# stock INSEE (~4 Go, a recuperer UNE fois sur data.gouv :
# "Base Sirene des entreprises et de leurs etablissements")
python sirene_stock.py --etab StockEtablissement_utf8.csv --unite StockUniteLegale_utf8.csv
```

**2. FINESS — tout en code :**

```bash
python finess_ingest.py --download
```

**3. Fusion → base :**

```bash
python build_base.py        # sirene_sante.csv + finess_sante.csv -> base_sante.csv
```

**4. Enrichissement contacts (optionnel) :**

```bash
python enrich.py            # base_sante.csv -> base_sante_enrichi.csv
```

Restreindre à une zone partout : `--departements 69 38 01 42 73 74`, ou renseigne
`departements:` dans `config.yml`.

## Téléchargements : ce qu'il faut savoir

- **FINESS** : automatique (`--download`). ⚠️ Le flux actuel s'arrête le
  **20/07/2026** (bascule vers "Finess+", specs sur GitHub `ansforge/finess`).
  Le parseur gère déjà le nouveau format à en-tête : seule la constante
  `FINESS_CSV_URL` sera à changer.
- **SIRENE mode API** : rien à télécharger.
- **SIRENE mode stock** : un seul gros fichier (~4 Go) à récupérer une fois.
  C'est le prix de l'exhaustivité totale.

## Fraîcheur & automatisation

C'est un **instantané**, pas du live : les registres bougent (créations, fermetures,
déménagements). Relance le pipeline tous les 1 à 3 mois.

`.github/workflows/refresh.yml` fournit un refresh **mensuel** (SIRENE API + FINESS +
fusion) qui **commite `data/base_sante.csv` dans le repo** (fichier versionné, cliquable)
et le publie aussi en artefact. Un lancement manuel permet de sauter SIRENE (base FINESS
seule, rapide) ou de restreindre à quelques départements. L'enrichissement n'est **pas**
lancé en CI (depuis une IP de datacenter, trop de sites bloquent le scraping) : lance
`enrich.py` en local.

Pour que le commit fonctionne, active une fois les droits d'écriture du workflow :
Settings → Actions → General → Workflow permissions → "Read and write permissions".

En local, tu peux aussi planifier avec cron, par ex. le 1er du mois à 3h :

```cron
0 3 1 * * cd /chemin/prospection-sante && python run_all.py --enrich
```

## Prospection : les règles B2B (rappel)

- **Email B2B** : régime opt-out / intérêt légitime. Privilégie les adresses
  génériques (`contact@`), qui ne sont pas des données perso. Garde une LIA, un
  lien de désinscription en 1 clic, un registre des traitements, une purge à 3 ans.
- **Téléphone B2B** : reste autorisé après le 11/08/2026 (la réforme opt-in ne vise
  que le B2C). Pas d'appels automatisés, opt-out immédiat, identification claire.
- **Jamais** de canal rénovation énergétique grand public via cette base (démarchage
  interdit depuis juillet 2025).

## Sources & licence

SIRENE (INSEE) et FINESS (ANS), sous **Licence Ouverte / Etalab**. Ce dépôt ne
redistribue aucune donnée : il la récupère à la demande depuis les sources officielles.
