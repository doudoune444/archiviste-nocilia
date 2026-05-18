# Rollback canary Cloud Run (INFRA-002)

Procédure de rollback manuel après auto-rollback échoué OU bug détecté
post-promote 100 %. Référencée par `docs/vision.md` § Cible deploy V1 beta
et `docs/adr/0003-terraform-deferred.md` § Activation 2026-05-18.

## Détection

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

## Post-rollback

1. Reproduire le bug en local (`docker compose up -d` + golden_qa dataset).
2. Ouvrir incident note dans `docs/blockers.md` : symptôme, révision fautive,
   resolution path.
3. Hotfix branch `fix/<ID>-<slug>` ciblant la révision rollback (pas la
   cassée). PR → main → re-deploy normal via GHA `deploy.yml`.
