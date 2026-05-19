# Rollback canary Cloud Run (INFRA-002)

Procédure de rollback manuel après auto-rollback échoué OU bug détecté
post-promote 100 %. Référencée par `docs/vision.md` § Cible deploy V1 beta
et `docs/adr/0003-terraform-deferred.md` § Activation 2026-05-18.

## Workflow auto-rollback

`.github/workflows/deploy.yml` déclenche un rollback automatique si le smoke test
`curl -sf <canary-url>/healthz | jq -e '.status == "ok"'` échoue après
`gcloud run deploy --no-traffic --tag canary`.
Dans ce cas le workflow :
1. Résout la révision précédente via :
   ```bash
   gcloud run revisions list --service=<svc> --region=europe-west9 \
     --sort-by=~metadata.creationTimestamp --limit=2 \
     --format='value(metadata.name)' | tail -1
   ```
   (Note : il n'existe pas de sentinel `PREVIOUS` côté gcloud — la résolution dynamique
   est obligatoire. Voir spec AC-12 step 6.)
2. Exécute `gcloud run services update-traffic --to-revisions=<previous>=100` sur gateway ET workers.
3. Sort avec `exit 1` (run GHA marqué failed — notification email GitHub par défaut).
La révision défaillante reste taggée `canary` mais ne reçoit aucun trafic.

## Détection (post-promote)

- Cloudflare analytics : spike 5xx > 1 % sur 5 min.
- Langfuse error rate > 5 % sur 10 min.
- Rapport manuel utilisateur via `https://archiviste.nocilia.fr/healthz`.

## 3 commandes rollback

```bash
# 1. Lister révisions par service (gateway puis workers, region europe-west9)
gcloud run revisions list \
  --service=archiviste-gateway \
  --region=europe-west9 \
  --limit=5

# 2. Re-router 100 % du trafic vers la révision précédente (nom exact)
gcloud run services update-traffic archiviste-gateway \
  --to-revisions=<PREV_REVISION_NAME>=100 \
  --region=europe-west9

# 3. Vérifier healthz
curl -sf https://archiviste.nocilia.fr/healthz \
  && echo "rollback OK" \
  || echo "rollback FAIL — escalate"
```

Répéter étapes 1-3 pour `archiviste-workers` si la régression touche aussi
le service interne. Cloud Run conserve par défaut les N dernières révisions
non actives, accessibles par nom.

## DB rollback

V1 beta = pas de scripts down migration. Si une migration casse le schéma :

```bash
# Lister backups PITR Cloud SQL (rétention 7j auto sur db-f1-micro)
gcloud sql backups list --instance=archiviste-db

# Restore point-in-time vers une nouvelle instance (preserve l'originale)
gcloud sql backups restore <BACKUP_ID> \
  --restore-instance=archiviste-db-restored-<DATE> \
  --backup-instance=archiviste-db
```

Puis mettre à jour le secret `DATABASE_URL` dans Secret Manager pour pointer
vers l'instance restaurée et redéployer (Cloud Run pick le nouveau secret au
prochain cold start).

## Secrets GitHub Actions requis

Le workflow `deploy.yml` nécessite les 3 secrets suivants configurés dans
**Settings → Secrets and variables → Actions** du dépôt :

| Secret | Source | Description |
|---|---|---|
| `GCP_WIF_PROVIDER` | `terraform output wif_provider` | Provider WIF complet : `projects/<number>/locations/global/workloadIdentityPools/<pool>/providers/<provider>` |
| `GCP_SA_EMAIL` | `terraform output gha_deploy_sa_email` | Email du SA `gha-deploy@<project>.iam.gserviceaccount.com` |
| `GCP_PROJECT_ID` | ID du projet GCP | Ex. `archiviste-prod-123456` |

Ces valeurs sont produites par `terraform apply` (PR INFRA-002a). Sans elles,
l'étape `authenticate to GCP via WIF` échoue avec
`failed to generate Google Cloud access token`.

## Post-rollback

1. Reproduire le bug en local (`docker compose up -d` + golden_qa dataset).
2. Ouvrir incident note dans `docs/blockers.md` : symptôme, révision fautive,
   resolution path.
3. Hotfix branch `fix/<ID>-<slug>` ciblant la révision rollback (pas la
   cassée). PR → main → re-deploy normal via GHA `deploy.yml`.
