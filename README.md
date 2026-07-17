# KPI Dashboard statique

Ce dossier contient les 4 pages HTML a publier sur GitHub Pages.

- `newsletter-sent.html`
- `newsletter-subscribers.html`
- `newsletter-open-rate.html`
- `website-visits.html`

Une fois publiees, utiliser les URLs publiques dans les tuiles personnalisees HubSpot.

## Mise a jour automatique

Le workflow GitHub Actions `.github/workflows/update-kpis.yml` met a jour les donnees chaque lundi a 07:00 UTC.

Il fait automatiquement:

1. recuperation des donnees HubSpot newsletter;
2. recuperation des donnees GA4 site web;
3. mise a jour de `kpi.sqlite3`;
4. regeneration des 4 fichiers HTML;
5. commit des fichiers modifies dans le repo.

## Secrets GitHub requis

Dans GitHub, aller dans:

`Settings > Secrets and variables > Actions > New repository secret`

Ajouter ces 3 secrets:

- `HUBSPOT_ACCESS_TOKEN`: token prive HubSpot;
- `GA4_PROPERTY_ID`: ID numerique de propriete GA4;
- `GA4_SERVICE_ACCOUNT_JSON`: contenu complet du fichier JSON du compte de service Google.

Ne jamais commiter le fichier `.env` ni le fichier JSON Google.

## Lancer une mise a jour manuelle

Dans GitHub:

`Actions > Update KPI pages > Run workflow`

Le site GitHub Pages sera mis a jour apres le commit automatique.
