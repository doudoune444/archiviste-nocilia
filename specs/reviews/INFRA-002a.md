# Review INFRA-002a — Round 3 (re-review after fix `b480439`)

## Round 3

### Round 2 findings status

| Round 2 | Severity | Status | Evidence in `b480439` |
|---|---|---|---|
| HIGH-5 `DATABASE_URL` `@` non-encoded in userinfo | HIGH | RESOLVED | `cloud_run.tf:62` + `cloud_run.tf:133` — `replace(google_service_account.archiviste_runtime.email, "@", "%40")` appliqué aux deux occurrences. URL finale = `postgresql+asyncpg://archiviste-runtime%40<proj>.iam.gserviceaccount.com@/archiviste?host=/cloudsql/<conn>`. Un seul `@` non-encodé (séparateur userinfo/host) → asyncpg `URL.parse()` extrait user=`archiviste-runtime%40<proj>.iam.gserviceaccount.com` (décodé puis), host vide, `host=/cloudsql/...` en query param → Unix socket pris en compte. Conforme RFC 3986. |
| HIGH-6 `roles/cloudsql.instanceUser` SA runtime | HIGH | RESOLVED | `iam.tf:42` — ajouté à `local.runtime_project_roles`. Boucle `for_each` ligne 47 le bind project-wide au SA `archiviste-runtime`. Commentaire ligne 35-38 cite la doc GCP. |
| MED-6 ordre runbook §4/§5 inversé | MED | RESOLVED | `bootstrap-gcp.md:47-85` réordonné en 4a (AR-only apply) / 4b (placeholder push) / 4c (full apply) — séquence linéaire correcte, plus de `-target` caché en fin de section. Sections suivantes renumérotées (§5 secret, §6 pgvector, §7 IAM verify, §8 verify, §9 GHA secrets). |
| MED-7 IAM auth verify step | MED | RESOLVED | `bootstrap-gcp.md:122-137` — §7 ajouté avec `gcloud sql connect --user=archiviste-runtime@<proj>.iam` + instruction blocker explicite si `pg_authentication_failed`. Référence `docs/blockers.md` cohérente avec `.claude/rules/no-workaround.md`. |
| LOW-7 §6 `postgres` password set | LOW | RESOLVED | `bootstrap-gcp.md:101-108` — `gcloud sql users set-password postgres` inséré avant le `gcloud sql connect` pgvector. |

### Round 3 — nouveaux findings

#### LOW

| File:line | Pattern | Evidence | Suggested fix |
|---|---|---|---|
| `infra/terraform/cloud_run.tf:62` (gateway) | scheme `postgresql+asyncpg://` injecté au gateway alors que gateway = Rust/sqlx | Gateway `Config.database_url` (`gateway/src/config.rs:14,41`) ne fait que lire la var ; aucun `PgPool::connect(...)` actif dans PR-a (`grep PgPool` = 0 résultat dans `gateway/src/`). Donc dormant pour cette PR. Mais le moment où un ticket gateway ouvrira sqlx, `sqlx::PgPool::connect("postgresql+asyncpg://...")` échouera (sqlx accepte `postgres://` / `postgresql://` uniquement, pas le suffixe `+asyncpg`). Workers normalise via `db.py:12-16` ; gateway n'a pas l'équivalent. | Décision à acter (futur ticket gateway-DB) : soit deux env vars distinctes (`DATABASE_URL_GATEWAY` sans `+asyncpg`, `DATABASE_URL_WORKERS` avec), soit gateway strip le suffixe au boot façon `normalize_database_url`. Non-bloquant PR-a — flagger pour follow-up. |
| `docs/runbook/bootstrap-gcp.md:130-132` | `gcloud sql connect` IAM SA en §7 — `gcloud sql connect` ouvre une session via le proxy interactif sans `--auto-iam-authn`, ne teste PAS le chemin Cloud Run | `gcloud sql connect` utilise le Cloud SQL Auth Proxy en mode CLI (compte utilisateur gcloud). Pour tester réellement le path runtime, il faudrait `gcloud auth print-access-token` du SA puis psql via proxy avec ce token. La §7 actuelle teste seulement que `cloudsql.instanceUser` est bien bind ; ne prouve PAS que le proxy intégré Cloud Run v2 supporte `--auto-iam-authn`. | Étape complémentaire post-deploy `deploy.yml` : grep logs Cloud Run gateway/workers pour confirmer "Connected to database" / absence de `pg_authentication_failed`. Ou ADR sur sidecar Auth Proxy explicite si Cloud Run v2 intégré insuffisant (déjà mentionné §7 comme fallback blocker). |
| `docs/runbook/bootstrap-gcp.md:30` (numérotation sections) | Section anciennement "5/6/7" remap en "5/6/7/8/9" — anchors externes éventuels cassés | Aucun lien `bootstrap-gcp.md#section-N` détecté dans le repo (grep `bootstrap-gcp.md#` = 0), donc cosmétique. | Optionnel : si docs externe pointe ces ancres, ajouter redirects ou ne pas casser. RAS pour cette PR. |

### Verdict final
APPROVE

### Rationale Round 3

Round 1 (5 HIGH + 5 MED + 5 LOW) → fix `58fbbdd` a résolu les 5 HIGH et 4 MED, mais a introduit 2 HIGH (DATABASE_URL `@`, instanceUser manquant) + 1 MED (runbook order) au passage.

Round 2 → fix `b480439` corrige ces 5 trous (HIGH-5, HIGH-6, MED-6, MED-7, LOW-7) proprement :
- `replace(..., "@", "%40")` est la bonne primitive HCL (pure string, idempotente, lisible dans le plan diff Terraform). URL finale parseable par asyncpg après `normalize_database_url`.
- `cloudsql.instanceUser` bind via la même boucle `for_each` que les autres rôles runtime — pas de duplication, pas de drift.
- Runbook §4a/4b/4c est exécutable linéairement, plus de `-target` enterré en fin de §5.
- §7 IAM verify ajoute un fail-fast pré-`deploy.yml` + référence blocker (cohérent rules).
- LOW-7 `set-password postgres` documenté avant connexion psql.

3 nouveaux findings LOW non-bloquants : (a) gateway scheme `postgresql+asyncpg://` dormant — non utilisé en PR-a, à régler au futur ticket gateway-DB ; (b) §7 verify utilise compte gcloud user, ne teste pas exactement le proxy Cloud Run intégré — mitigation post-deploy via logs ; (c) numérotation sections runbook décalée — pas d'ancres cassées détectées.

AC-3 (Cloud SQL accessible) désormais fonctionnellement satisfait sous réserve de la vérification §7 manuelle opérateur. Aucun nouveau HIGH, aucun MED bloquant. Diff total 653 LOC dont 164 runbook + 89 budget/secrets/AR/wif/outputs hors compteur "code" — backbone Terraform infra acceptable au regard du contexte one-shot pre-conditions.

`terraform validate` / `terraform fmt -check` toujours non exécutables dans l'env review (binary absent du PATH agent) — gate opérateur avant merge inchangé. Pas un blocker review.

Aucun secret en clair, aucune SA key, CEL WIF inchangée. `.claude/rules/security.md` et `.claude/rules/secret-hygiene.md` respectés.

---

# Review INFRA-002a — Round 2 (re-review after fix `58fbbdd`)

## Round 2

### Round 1 findings status

| Round 1 | Severity | Status | Evidence in `58fbbdd` |
|---|---|---|---|
| HIGH-1 `WORKERS_URL` manquante | HIGH | RESOLVED | `cloud_run.tf:64-67` env `WORKERS_URL = google_cloud_run_v2_service.workers.uri` |
| HIGH-2 workers `run.invoker` SA runtime | HIGH | RESOLVED | `cloud_run.tf:161-167` `google_cloud_run_v2_service_iam_member.workers_runtime_invoker` member=`serviceAccount:${archiviste_runtime.email}` |
| HIGH-3 gateway `run.invoker allUsers` | HIGH | RESOLVED | `cloud_run.tf:151-157` member=`allUsers` |
| HIGH-4 `google_sql_user "archiviste"` manquant | HIGH | PARTIAL | `cloud_sql.tf:43-47` crée user IAM SA + DATABASE_URL aligné, MAIS deux nouveaux trous bloquants (voir Round 2 HIGH-5 et HIGH-6 ci-dessous) |
| MED-1 budget email non câblé | MED | RESOLVED | `budget.tf:3-11` `google_monitoring_notification_channel` + `budget.tf:35` wire vers `monitoring_notification_channels` |
| MED-2 `cloudbilling.googleapis.com` manquant | MED | RESOLVED | `bootstrap-gcp.md:24` ajouté à `gcloud services enable` |
| MED-3 image placeholder pré-apply | MED | PARTIAL | `bootstrap-gcp.md:58-80` section ajoutée, MAIS section §4 `terraform apply` placée AVANT section §5 image placeholder — ordre d'exécution inversé dans le runbook (voir MED-6) |
| MED-4 `ssl_mode=ENCRYPTED_ONLY` | MED | RESOLVED | `cloud_sql.tf:27` |
| MED-5 image `:latest` drift | MED | RESOLVED | `cloud_run.tf:15-17,83-85` `lifecycle.ignore_changes = [template[0].containers[0].image]` sur les 2 services |
| LOW-1 commentaires obsolètes `budget.tf` | LOW | RESOLVED | `budget.tf` lignes 3-4 supprimées (`git diff 58fbbdd^ -- infra/terraform/budget.tf`) |
| LOW-2 `versioning` GCS | LOW | UNCHANGED | Resté hors-scope (acceptable) |
| LOW-3 `max_instance_count` | LOW | UNCHANGED | Resté hors-scope (acceptable) |
| LOW-4 WIF binding scope `subject` | LOW | UNCHANGED | Resté hors-scope (CEL au niveau provider suffisant) |
| LOW-5 Secret Manager regional replication | LOW | UNCHANGED | Resté hors-scope (acceptable) |
| LOW-6 `terraform validate`/`fmt` operator-run | LOW | UNCHANGED | `terraform` binary toujours absent du PATH agent — gate humain |

### Round 2 — nouveaux findings introduits par le fix

#### HIGH

| File:line | Pattern | Evidence | Suggested fix |
|---|---|---|---|
| `infra/terraform/iam.tf:35-48` | manque `roles/cloudsql.instanceUser` sur SA runtime — IAM DB auth bloquée | Le fix HIGH-4 crée `google_sql_user.archiviste_runtime` avec `type = "CLOUD_IAM_SERVICE_ACCOUNT"` (`cloud_sql.tf:43-47`). Google Cloud SQL IAM database authentication **exige** que la SA possède `roles/cloudsql.instanceUser` AU NIVEAU PROJET (en plus de `roles/cloudsql.client` qui gère seulement la connexion réseau via le proxy). Le SA `archiviste-runtime` n'a que `cloudsql.client` + `secretmanager.secretAccessor` (locals `runtime_project_roles`, `iam.tf:36-40`). Première connexion DB = `pg_authentication_failed` / token refusé par le proxy IAM | Ajouter `"roles/cloudsql.instanceUser"` à `local.runtime_project_roles` (`iam.tf:36-40`). Réf : `https://cloud.google.com/sql/docs/postgres/add-manage-iam-users#grant-db-instance-user` |
| `infra/terraform/cloud_run.tf:59,127` | DATABASE_URL malformé — username contient `@` non-encodé | `value = "postgresql+asyncpg://${google_service_account.archiviste_runtime.email}@/archiviste?host=/cloudsql/..."` interpole `archiviste-runtime@<project>.iam.gserviceaccount.com`. L'URL résultante contient DEUX `@` non-encodés : `postgresql+asyncpg://archiviste-runtime@<project>.iam.gserviceaccount.com@/archiviste?...`. Parseur asyncpg (`workers/src/archiviste_workers/db.py:12-16` strip prefix puis `asyncpg.create_pool(url)`) va traiter le premier `@` comme séparateur user/host → user devient `archiviste-runtime`, host devient `<project>.iam.gserviceaccount.com@`. Connexion échoue avec hostname invalide. Idem côté gateway sqlx. RFC 3986 exige percent-encoding de `@` dans userinfo (`%40`) | Soit URL-encoder le `@` (`replace(email, "@", "%40")`) dans la valeur HCL, soit passer le username via env var séparée et reconstruire l'URL côté code, soit utiliser DSN libpq (params séparés). Recommandé : `value = "postgresql+asyncpg://${replace(google_service_account.archiviste_runtime.email, "@", "%40")}@/archiviste?host=/cloudsql/${google_sql_database_instance.archiviste_db.connection_name}"` |

#### MED

| File:line | Pattern | Evidence | Suggested fix |
|---|---|---|---|
| `docs/runbook/bootstrap-gcp.md:41-48,58-80` | ordre runbook incohérent — §4 dit `terraform apply` complet, §5 dit pousser image AVANT apply | §4 (lignes 41-48) : `terraform plan` puis `terraform apply -var-file=...` complet. §5 (ligne 79) : "Run `terraform apply -target=google_artifact_registry_repository.archiviste` first to create the AR repo, then push the placeholder, then run `terraform apply` (full)". Opérateur qui lit linéairement va lancer §4 d'abord et se planter sur Cloud Run "image not found". L'instruction `-target` est cachée en fin de §5 alors qu'elle doit précéder §4 | Réordonner : (1) auth+API enable, (2) state bucket, (3) `terraform apply -target=google_artifact_registry_repository.archiviste`, (4) push placeholder images, (5) `terraform apply` complet, (6) MISTRAL_API_KEY version, (7) pgvector, etc. OU fusionner §4 et §5 en une procédure unique numérotée |
| `workers/src/archiviste_workers/db.py:12-16` + `cloud_run.tf:127` | IAM DB auth incompatible avec asyncpg sans token provider explicite | Cloud SQL IAM auth requiert que le client présente un token OAuth2 court-vie (1h TTL) comme mot de passe. Le Cloud SQL Auth Proxy sidecar (avec flag `--auto-iam-authn`) peut faire cet échange, MAIS le sidecar n'est pas déclaré explicitement dans Cloud Run v2 — seul `volumes.cloud_sql_instance` est utilisé (`cloud_run.tf:30-35,98-103`), qui démarre un proxy GCP-managed. Vérifier si le proxy intégré Cloud Run v2 supporte `--auto-iam-authn` (option `enable_iam_authn` non visible dans `google_cloud_run_v2_service` v2). Si non, asyncpg essaiera de se connecter sans password → échec auth. Non testable depuis l'env de review | Documenter explicitement dans bootstrap : tester la connexion IAM auth post-apply via `gcloud sql connect archiviste-db --user=archiviste-runtime@<project>.iam.gserviceaccount.com`. Si Cloud Run v2 proxy intégré ne supporte pas IAM authn, fallback : password user classique (`type = "BUILT_IN"`) + secret stocké en Secret Manager. Alternative ADR : sidecar Cloud SQL Auth Proxy custom container avec `--auto-iam-authn` |

#### LOW

| File:line | Pattern | Evidence | Suggested fix |
|---|---|---|---|
| `infra/terraform/cloud_run.tf:64-67` | `WORKERS_URL` interpolé depuis `workers.uri` — dépendance circulaire potentielle entre 2 services | `google_cloud_run_v2_service.gateway` réfère `google_cloud_run_v2_service.workers.uri` → Terraform ordonne workers en premier, OK. Mais workers ingress=internal sans gateway DNS résolution préalable est sans risque. Note : `workers.uri` retourne `https://archiviste-workers-<hash>-ew.a.run.app` — gateway calls service-to-service à cette URL, vérifié OK avec IAM auth ID token (cf HIGH-2 binding). Pas un bug, mais à valider en smoke test PR-c | Aucun changement requis ; ajouter assertion dans smoke test deploy.yml : gateway logs montrent succès appel `${WORKERS_URL}/healthz` à boot |
| `docs/runbook/bootstrap-gcp.md:94-106` | pgvector bootstrap utilise `--user=postgres` — user `postgres` n'a pas de password configuré sans `random_password` Terraform | `gcloud sql connect archiviste-db --user=postgres` demande un password interactif. Avec `db-f1-micro` Cloud SQL crée user `postgres` avec password vide ou auto-généré (à vérifier console GCP). Sans password explicite côté Terraform, première connexion peut échouer | Documenter : `gcloud sql users set-password postgres --instance=archiviste-db --password=<random>` AVANT §7 OU utiliser `gcloud sql connect` avec auth IAM (`--user=<SA-email>` après `roles/cloudsql.instanceUser` accordé) |

### Round 2 verdict rationale

3 HIGH résolus (WORKERS_URL, run.invoker public, run.invoker service-to-service) — ces fixes sont propres, idempotents, et alignés AC. MED-1 budget alert correctement câblé. MED-2/4/5 OK. LOW-1 nettoyé.

Mais le fix HIGH-4 introduit 2 nouveaux HIGH : (a) URL malformée avec `@` non-encodé dans DATABASE_URL → connexion DB impossible côté gateway ET workers ; (b) IAM DB auth nécessite `roles/cloudsql.instanceUser` projet-wide sur SA runtime, absent du diff. Sans ces deux corrections, AC-3 (Cloud SQL accessible depuis Cloud Run) reste non satisfait — la mission de fix HIGH-4 est cosmétiquement traitée (user créé) mais fonctionnellement cassée (pas exploitable).

MED-3 (image placeholder) résolue contenu mais ordre runbook inversé : opérateur qui lit linéairement se plantera quand même. Cosmétiquement résolu, pas fonctionnellement.

Reste 1 MED supplémentaire (compatibilité asyncpg avec IAM auth via Cloud Run v2 proxy intégré — risque ADR-level si non supporté).

---

# Review INFRA-002a — verdict REQUEST_CHANGES (Round 1)

## Summary

Terraform core GCP livré pour 14 fichiers / 526 LOC. Backbone fonctionnel : backend GCS, 2 Cloud Run, Cloud SQL Postgres 16 sans IP publique, GCS bucket conforme uniform/PAP/lifecycle 30j, Secret Manager `MISTRAL_API_KEY` injecté via `secret_key_ref`, IAM 2 SA avec storage bucket-scoped, WIF avec CEL strict repo+branch, aucune `google_service_account_key`, budget 50 EUR. Très bon socle.

Cinq trous bloquants malgré tout : (1) gateway runtime exige `WORKERS_URL` env qui n'est jamais injectée — boot KO, (2) workers `ingress=internal` mais SA runtime n'a pas `roles/run.invoker` sur workers — appels gateway→workers refusés 403, (3) gateway `ingress=all` mais aucune policy `run.invoker allUsers` — `/healthz` public retourne 403 = AC-14 cassé, (4) `google_sql_user "archiviste"` non créé alors que `DATABASE_URL` y réfère — workers/gateway connexion DB KO, (5) variable `budget_email` déclarée et jamais wirée à un `google_monitoring_notification_channel` — alert AC-9 ne sera pas envoyé à l'owner.

Aucune fuite de secret, aucune key SA générée, CEL WIF exacte, contraintes least-privilege respectées, pas de IP publique Cloud SQL, GCS conforme. Outils `terraform fmt -check` et `terraform validate` non exécutés (terraform absent du PATH dans cet environnement) — à valider opérateur local avant merge.

## AC coverage

| AC | Evidence | Status |
|---|---|---|
| AC-1 (state backend, fmt/validate) | `versions.tf:15-18` backend GCS bucket `archiviste-tf-state` prefix `terraform/state` ; `fmt`/`validate` non runnables dans cet env (terraform absent) | PARTIAL — gate technique non vérifié |
| AC-2 (2 services Cloud Run, mem, ingress, min=0) | `cloud_run.tf:8-62` gateway 256Mi ingress=all min=0 ; `cloud_run.tf:64-129` workers 512Mi ingress=internal min=0 | OK pour valeurs déclarées ; image `:latest` placeholder (intentionnel, override GHA) |
| AC-3 (Cloud SQL pg16 + sidecar Unix socket + pgvector + pas de VPC) | `cloud_sql.tf:3-30` `POSTGRES_16` `db-f1-micro` `disk_size=10`, `ipv4_enabled=false`, PITR ON 7j ; `cloud_run.tf:24-29,80-85` `volumes.cloud_sql_instance` + mount `/cloudsql` ; pgvector via bootstrap manuel `docs/runbook/bootstrap-gcp.md:69-81` | OK |
| AC-4 (GCS uniform/PAP/lifecycle 30j) | `gcs.tf:2-18` `uniform_bucket_level_access=true`, `public_access_prevention="enforced"`, `lifecycle_rule { action.type="Delete" condition.age=30 }` | OK |
| AC-5 (secret + secret_key_ref vers workers) | `secrets.tf:3-10` ; `cloud_run.tf:113-121` env `LLM_API_KEY` via `secret_key_ref` version=`latest` (workers only — gateway n'a pas la clé, cohérent) | OK |
| AC-6 (2 SA + rôles exacts + storage bucket-scoped) | `iam.tf:3-13` 2 SA ; `iam.tf:17-32` 5 rôles `gha-deploy` exacts ; `iam.tf:36-48` `cloudsql.client`+`secretmanager.secretAccessor` runtime project-wide ; `iam.tf:51-55` `storage.objectAdmin` bucket-scoped via `google_storage_bucket_iam_member` (PAS project-wide) | OK |
| AC-7 (WIF + CEL exacte + zero JSON key) | `iam_wif.tf:4-28` pool+provider ; `iam_wif.tf:27` CEL `assertion.repository == 'doudoune444/archiviste-nocilia' && assertion.ref == 'refs/heads/main'` verbatim spec ; `grep -r google_service_account_key infra/terraform/` = 0 résultat | OK |
| AC-8 (Cloudflare) | Hors PR-a (déféré PR-b) | OUT_OF_SCOPE |
| AC-9 (budget 50 EUR + email owner) | `budget.tf:2-31` montant 50 EUR `threshold_percent=1.0` `disable_default_iam_recipients=false` ; **mais** `var.budget_email` déclarée `variables.tf:29-32` et JAMAIS référencée dans `budget.tf` — pas de `google_monitoring_notification_channel` créé | PARTIAL — voir MED-1 |
| AC-10..AC-14 | Hors PR-a (PRs b/c/d) | OUT_OF_SCOPE |

## Findings

### HIGH

| File:line | Pattern | Evidence | Suggested fix |
|---|---|---|---|
| `infra/terraform/cloud_run.tf:31-61` | gateway boot KO — `WORKERS_URL` env manquante | `gateway/src/config.rs:40` exige `std::env::var("WORKERS_URL").context("WORKERS_URL env var required")?` — gateway crash immédiat sans cette var. Cloud Run gateway ne déclare ni `WORKERS_URL` ni équivalent. AC-14 (`/healthz` 200 externe) impossible | Ajouter `env { name="WORKERS_URL" value=google_cloud_run_v2_service.workers.uri }` au container gateway |
| `infra/terraform/cloud_run.tf:64-129` + `iam.tf` | workers `ingress=internal` mais gateway runtime SA n'a aucun `roles/run.invoker` sur workers | `archiviste_runtime` SA est attaché à `gateway` (cloud_run.tf:14) et appelle workers `ingress=internal`. Sans `roles/run.invoker` sur workers, Cloud Run rejette 403 même service-to-service. Pas de `google_cloud_run_v2_service_iam_member` dans le diff | Ajouter `google_cloud_run_v2_service_iam_member` sur `workers` : member=`serviceAccount:${archiviste_runtime.email}`, role=`roles/run.invoker` |
| `infra/terraform/cloud_run.tf:8-12` | gateway `ingress=all` sans policy `run.invoker allUsers` → 403 public par défaut | Cloud Run v2 exige binding explicite `roles/run.invoker` à `allUsers` pour accès anonyme. Aucun `google_cloud_run_v2_service_iam_member` ni `_iam_policy` dans le diff. AC-14 (`https://archiviste.nocilia.fr/healthz` HTTP 200) impossible — toutes les requêtes Cloudflare→gateway recevront 403 | Ajouter `google_cloud_run_v2_service_iam_member` sur `gateway` : member=`allUsers`, role=`roles/run.invoker` (cohérent avec ingress=all + auth applicative anonymous V1) |
| `infra/terraform/cloud_sql.tf` (manquant) | `google_sql_user "archiviste"` jamais créé, mais `DATABASE_URL=postgresql+asyncpg://archiviste@/archiviste...` réfère ce user | `cloud_run.tf:53,109` construit URL avec user `archiviste` — la DB `archiviste_db` ne contient que le user `postgres` par défaut. Première connexion = `FATAL: role "archiviste" does not exist` | Ajouter `resource "google_sql_user" "archiviste"` (IAM auth `type=CLOUD_IAM_SERVICE_ACCOUNT` lié au runtime SA — recommandé) OU user/password classique + secret. Choix : IAM auth = pas de mot de passe à gérer, cohérent avec sidecar Auth Proxy |

### MED

| File:line | Pattern | Evidence | Suggested fix |
|---|---|---|---|
| `infra/terraform/budget.tf:24-30` + `variables.tf:29-32` | `var.budget_email` déclarée mais jamais utilisée → alert n'arrive pas à l'owner spécifié, seulement aux billing-account admins par défaut IAM | AC-9 demande "notification email à l'adresse propriétaire". `disable_default_iam_recipients=false` envoie aux billing admins, ce qui peut être l'owner ou non. Aucune ressource `google_monitoring_notification_channel type="email"` ni `notification_channels` non-vide | Créer `google_monitoring_notification_channel "owner"` `type="email" labels.email_address=var.budget_email` et `monitoring_notification_channels = [google_monitoring_notification_channel.owner.id]` |
| `docs/runbook/bootstrap-gcp.md:13-28` | API enablement list incomplet : `cloudbilling.googleapis.com` manquant | `google_billing_budget` nécessite `cloudbilling.googleapis.com` ET `billingbudgets.googleapis.com`. Le second est listé, le premier non. Premier `apply` peut échouer avec `Cloud Billing API has not been used in project` | Ajouter `cloudbilling.googleapis.com` à la liste `gcloud services enable` |
| `docs/runbook/bootstrap-gcp.md:40-47` | Pas de mention que les images Docker `:latest` doivent exister AVANT `terraform apply` initial | `cloud_run.tf:33,89` réfère `${ar_base}/gateway:latest` et `${ar_base}/workers:latest`. Premier apply Cloud Run échoue si image absente. PR-c (deploy.yml) push les images mais lui-même dépend du WIF provisionné par PR-a → poule/œuf | Documenter dans bootstrap : push manuel d'une image dummy `:latest` (ou `gcr.io/google-containers/pause`) post-création AR repo, OU `terraform apply -target` AR seul puis push, puis apply complet |
| `infra/terraform/cloud_sql.tf:8-27` | `require_ssl` / `ssl_mode` non défini sur `ip_configuration` | Mission brief : "Cloud SQL TLS forcé". Avec `ipv4_enabled=false` + Auth Proxy Unix socket, le risque est faible (proxy gère TLS) mais le défaut Cloud SQL accepte `ALLOW_UNENCRYPTED_AND_ENCRYPTED`. Defense-in-depth | Ajouter `ssl_mode = "ENCRYPTED_ONLY"` dans `ip_configuration` |
| `infra/terraform/cloud_run.tf:33,89` | Image tag `:latest` mutable → reproducibilité KO + risque rollback bancal | `:latest` ne pin pas un digest. Cohérent avec override GHA `:<git_sha>` mais terraform diff drift permanent à chaque deploy GHA (Cloud Run image change hors Terraform state) | Soit ignorer via `lifecycle { ignore_changes = [template[0].containers[0].image] }`, soit data source sur la dernière image AR. Sans ça, `terraform plan` futur affichera drift |

### LOW

| File:line | Pattern | Evidence | Suggested fix |
|---|---|---|---|
| `infra/terraform/budget.tf:1-4` | Commentaires obsolètes "must be set via provider default project billing account" alors que `billing_account = var.billing_account` est désormais explicite | Lignes 3-4 disent une chose, ligne 5 fait l'autre. Confusion lecteur | Supprimer commentaires lignes 3-4 |
| `infra/terraform/gcs.tf:2-18` | `versioning` non activé sur le bucket des conversations | Conversations en Markdown — perte accidentelle (suppression bug app) non récupérable. Mais lifecycle 30j compatible. Question conception | Optionnel : `versioning { enabled = true }` + lifecycle complémentaire |
| `infra/terraform/cloud_run.tf:8-129` | Pas de `timeout`, pas de `max_instance_count` explicite | Cloud Run par défaut max_instances=100, timeout=300s. Pas dans la spec mais hygiène cost-control beta | Définir `scaling { max_instance_count = 10 }` (limite blast radius runaway scale) |
| `infra/terraform/iam_wif.tf:33` | Binding sur `attribute.repository/${var.github_repo}` — autorise TOUTE branche du repo, ré-filtré ensuite par CEL `attribute_condition` au niveau provider | Defense-in-depth OK puisque CEL bloque hors-main au token-exchange, mais binding plus précis possible (`subject` plutôt que `attribute.repository`) | Optionnel : binder sur `principal://.../subject/repo:doudoune444/archiviste-nocilia:ref:refs/heads/main` (subject claim entier) pour belt-and-suspenders |
| `infra/terraform/secrets.tf:7-9` | `replication { auto {} }` — multi-régional automatique | Cohérent avec free tier Secret Manager, mais data residency UE non garantie. Pour conversations c'est dans GCS europe-west9 ; pour la clé Mistral c'est négligeable | Optionnel : `replication { user_managed { replicas { location = "europe-west9" } } }` pour cohérence régionale |
| Bootstrap | `terraform validate` / `terraform fmt -check` non exécutés (binary absent du PATH agent) | Mission demande gate AC-1 ; opérateur doit valider en local avant merge | Humain : `terraform -chdir=infra/terraform init && terraform validate && terraform fmt -check -recursive` |

## Out-of-scope changes

Aucun. Les 14 fichiers touchés sont tous dans la liste "PR a — Terraform core" du plan. CHANGELOG entry présente.

## Verdict
REQUEST_CHANGES

3 HIGH du round 1 RESOLVED proprement (WORKERS_URL, run.invoker allUsers, run.invoker service-to-service). MED 1/2/4/5 OK. LOW-1 nettoyé. Budget alert correctement câblé à l'owner.

Mais fix HIGH-4 introduit 2 nouveaux HIGH bloquants : (Round-2-HIGH-5) DATABASE_URL malformé — username SA contient `@` non-encodé → URI invalide, connexion DB KO côté gateway ET workers ; (Round-2-HIGH-6) IAM DB auth `CLOUD_IAM_SERVICE_ACCOUNT` exige `roles/cloudsql.instanceUser` projet-wide sur SA runtime, absent (`iam.tf:35-48` ne liste que `cloudsql.client` + `secretmanager.secretAccessor`). AC-3 fonctionnellement cassé.

MED-3 (image placeholder) cosmétiquement résolu mais runbook §4 → §5 reste mal ordonné — opérateur qui lit linéairement se plante quand même.

À corriger avant merge PR-a (sinon PR-c deploy.yml ship sur DB inaccessible). `terraform validate`/`fmt -check` toujours à exécuter côté opérateur (binary absent env review).
