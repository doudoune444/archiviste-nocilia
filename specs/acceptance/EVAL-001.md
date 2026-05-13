# EVAL-001 — Runner Ragas golden set + gate CI no-regression

## Contexte

Le walking skeleton RAG (RET-001 + GEN-001 + GEN-002) répond mais aucune mesure de qualité n'existe : faithfulness, answer_relevancy, context_precision, context_recall ne sont pas tracées, et toute régression sémantique passe silencieusement la CI. Le golden set `specs/golden_qa.jsonl` (46 entrées : 35 canon, 4 off_topic, 4 lore_gap, 3 mystery) doit alimenter un runner Ragas qui exécute le pipeline réel, calcule les métriques, et bloque la PR si les seuils absolus ou no-regression sont violés.

## Critères d'acceptation

- AC-1 : Le runner charge `specs/golden_qa.jsonl` et échoue avec un message d'erreur explicite citant l'`id` fautif et le champ invalide si une entrée ne respecte pas le schéma `{id: str non-vide, mode: ∈ {canon, off_topic, lore_gap, mystery}, question: str non-vide, expected_contexts: [str], expected_answer_keywords: [str], difficulty?: str}` ; aucune métrique n'est calculée tant qu'une entrée est invalide.
- AC-2 : Le runner accepte un flag `--mode {live,offline}` ; sans flag, le runner refuse de démarrer avec un message explicite `mode required: live|offline`.
- AC-3 : En `--mode live`, pour chaque entrée du golden set, le runner appelle `POST /v1/retrieve` puis `POST /v1/generate` (workers) de façon découplée, avec `top_k=5` figé identique aux deux appels, et persiste pour chaque entrée `{id, mode, question, retrieved_contexts: [source_path], answer: str, citations: [...]}` dans le fichier de run.
- AC-4 : En `--mode offline`, le runner appelle `POST /v1/retrieve` réel (retrieval réel sur la base) mais remplace l'appel LLM par un stub déterministe (réponse construite à partir des chunks récupérés et des `expected_answer_keywords` selon une règle figée documentée dans le runner), et utilise un evaluator déterministe (pas de LLM-as-judge) ; deux exécutions consécutives offline sur la même DB produisent des métriques byte-identiques.
- AC-5 : Le runner produit `eval/runs/<timestamp>.json` au schéma `{mode, started_at, finished_at, git_sha, runner_mode: live|offline, totals: {entries, ok, errors}, breakdown_by_mode: {canon: {...}, off_topic: {...}, lore_gap: {...}, mystery: {...}}, metrics: {faithfulness, answer_relevancy, context_precision, context_recall}, entries: [{id, mode, status, metrics: {...}, retrieved_contexts, answer}]}` où `metrics` au niveau racine n'agrège QUE les entrées `mode=canon`.
- AC-6 : Les métriques `faithfulness`, `answer_relevancy`, `context_precision`, `context_recall` sont calculées via Ragas sur l'ensemble des entrées `mode=canon` ; les modes `off_topic`, `lore_gap`, `mystery` ne contribuent PAS à ces métriques racine et apparaissent uniquement dans `breakdown_by_mode` avec leurs métriques structurelles déterministes (cf AC-7).
- AC-7 : Pour les modes non-canon, `breakdown_by_mode` contient des métriques déterministes : (a) `off_topic` → `keyword_overlap_rate` (fraction d'entrées dont l'`answer` contient au moins un keyword de `expected_answer_keywords`), (b) `lore_gap` → `keyword_overlap_rate` identique, (c) `mystery` → `keyword_overlap_rate` identique ; ces métriques ne déclenchent aucune gate phase 1.
- AC-8 : Pour chaque entrée `mode=canon`, le runner calcule un `context_recall_structural` = fraction des `expected_contexts` présents dans `retrieved_contexts` (match exact sur `source_path`) ; cette valeur figure dans `entries[].metrics` et est agrégée moyenne dans `breakdown_by_mode.canon.context_recall_structural`.
- AC-9 : Le runner expose `--baseline <path>` qui charge un fichier de run de référence ; en l'absence de ce flag, aucune gate no-regression n'est appliquée mais la gate absolue (AC-10) reste active.
- AC-10 : Gate A (absolus, mode canon) : si l'une des valeurs `metrics.faithfulness < 0.85`, `metrics.answer_relevancy < 0.85`, `metrics.context_precision < 0.70`, `metrics.context_recall < 0.70` est constatée, le runner exit code `1` et écrit sur stderr la liste des seuils violés avec valeur observée et seuil.
- AC-11 : Gate B (no-regression, mode canon) : si `--baseline` est fourni, le runner exit code `1` si `metrics.faithfulness < baseline.faithfulness - 0.02`, `metrics.answer_relevancy < baseline.answer_relevancy - 0.02`, `metrics.context_precision < baseline.context_precision - 0.03`, ou `metrics.context_recall < baseline.context_recall - 0.03` ; les drops dans la tolérance produisent exit code `0`.
- AC-12 : Quand AC-10 et AC-11 passent toutes deux (ou que AC-11 n'est pas appliquée faute de baseline), le runner exit code `0` et écrit sur stdout un résumé `event=eval_summary` JSON contenant `{mode_runner, totals, metrics, gate_a, gate_b}`.
- AC-13 : Un workflow GitHub Actions déclenché sur `pull_request` (target `main`) appelle le runner en `--mode offline --baseline eval/baseline.json` ; l'échec du runner échoue la CI ; le fichier de run est uploadé en artefact CI.
- AC-14 : Le workflow expose un `workflow_dispatch` qui permet à l'humain de lancer le runner en `--mode live` avec un input optionnel `--baseline` ; ce mode n'est jamais déclenché automatiquement sur PR.
- AC-15 : Le runner gère trois erreurs par entrée sans interrompre le run global : (a) timeout d'un appel `/v1/retrieve` ou `/v1/generate` > 60 s → entrée marquée `status: "timeout"`, métriques nulles, comptée dans `totals.errors` ; (b) réponse non-2xx workers → `status: "upstream_error"` + status code ; (c) réponse JSON malformée → `status: "malformed"`. Si `totals.errors / totals.entries > 0.10`, le runner exit code `1` indépendamment des gates.
- AC-16 : Aucun secret (clé LLM, DSN DB, URL workers complète avec credentials) n'apparaît dans le fichier de run, dans les logs stdout/stderr, ni dans l'artefact CI ; le runner lit `LLM_API_KEY`, `WORKERS_URL`, `DATABASE_URL` depuis l'environnement et ne les sérialise jamais.
- AC-17 : Le fichier `eval/baseline.json` est versionné en clair (exception `.gitignore` `!eval/baseline.json` déjà présente, confirmée) et son schéma est strictement identique à un `eval/runs/<timestamp>.json` produit par le runner ; toute mise à jour du baseline est un commit humain explicite avec scope `chore(eval): bump baseline`.

## Non-goals

- Pas de LLM-as-judge custom (eval-rubric maison) — Ragas natif uniquement phase 1.
- Pas de gate qualité sur modes `off_topic`, `lore_gap`, `mystery` — reporté à un ticket EVAL-* aval quand le pipeline aura les modes 2/3/4 (post GEN-001).
- Pas d'évaluation multi-turn ou conversationnelle — chaque entrée golden est standalone.
- Pas de génération automatique du baseline — l'humain le bump explicitement après validation.
- Pas d'instrumentation Langfuse / OpenTelemetry depuis le runner — tickets OBS-* dédiés.
- Pas de Ragas en `--mode live` sur PR (coût LLM + non-déterminisme) — réservé manuel + cron / workflow_dispatch.
- Pas d'évaluation des `citations` (alignement source_path retournés vs `expected_contexts`) au-delà du `context_recall_structural` — futur ticket EVAL-*.
- Pas de gate sur `latency_ms` ou `cost_eur` — tickets SEC-* / OBS-* dédiés.
- Pas de retry automatique sur entrée en erreur — un échec = un échec, tolérance globale 10% suffit.
- Pas de modification de `specs/golden_qa.jsonl` (source de vérité humaine, déjà à 46 entrées).

## Pre-conditions

- RET-001 mergé : endpoint `POST /v1/retrieve` opérationnel.
- GEN-001 mergé : endpoint `POST /v1/generate` opérationnel avec mode `canon`.
- ING-001 + ING-003 + ING-013 + ING-014 mergés : corpus lore ingéré, embeddings disponibles dans `chunks`, `source_path` aligné avec les chemins du golden set (`livre-l-ame-perdue/...`, `cartographie-v2/...`, `lore-divers/...`).
- `eval/baseline.json` initialisé : premier run `--mode offline` en local par l'humain pour établir la baseline avant activation de la gate B (peut être un commit séparé dans la même PR).
- Docker compose stack disponible localement et en CI pour exposer workers (port 8000) et postgres (5432).

## Failure modes

- Schéma `golden_qa.jsonl` invalide → exit code `2`, stderr cite `id` + champ, aucun appel pipeline.
- Flag `--mode` absent → exit code `2`, stderr `mode required: live|offline`.
- Workers indisponible au démarrage du runner (ping initial échoue) → exit code `3`, stderr `workers unreachable at <URL>` (URL sans credentials).
- Plus de 10% d'entrées en erreur (`totals.errors / totals.entries > 0.10`) → exit code `1`, stderr `error rate <X>% exceeds 10% threshold`.
- Gate A violée → exit code `1`, stderr liste les métriques violées avec `observed=X.XX threshold=Y.YY`.
- Gate B violée (baseline fourni) → exit code `1`, stderr liste les drops avec `observed=X.XX baseline=Y.YY delta=-Z.ZZ tolerance=T.TT`.
- Fichier `--baseline` fourni mais schéma invalide → exit code `2`, stderr `invalid baseline schema at <path>`.

## Touch points (informatif, non contraignant pour l'architect)

- `eval/ragas_runner.py` — point d'entrée CLI, chargement golden set, dispatch live/offline, écriture run.
- `eval/loader.py` — parse + validation JSONL golden set, schéma Pydantic.
- `eval/clients.py` — wrappers HTTP `POST /v1/retrieve` + `POST /v1/generate` avec timeout 60 s.
- `eval/stub_llm.py` — stub déterministe pour `--mode offline` (réponse construite from chunks + keywords).
- `eval/metrics.py` — calcul Ragas (canon) + métriques déterministes (off_topic/lore_gap/mystery/recall structurel).
- `eval/gates.py` — Gate A absolue + Gate B no-regression, application + reporting.
- `eval/baseline.json` — fichier de référence versionné (humain-only updates).
- `eval/runs/` — répertoire artefacts gitignored (sauf baseline).
- `.github/workflows/eval.yml` — workflow PR offline + dispatch live.
- `workers/pyproject.toml` — ajout dépendance `ragas` (ADR `docs/adr/NNNN-eval-ragas.md` si > 1k LOC ou nouveau provider LLM-judge utilisé par Ragas).

## Test oracle

- AC-1 : unit · `pytest` sur `loader.py` avec fixtures JSONL valides + invalides (id manquant, mode hors ensemble, expected_contexts non-liste) ; assert lève + message contient `id` fautif.
- AC-2 : integration CLI · invocation sans `--mode` → exit code 2 + stderr regex.
- AC-3 : integration · stack docker-compose up, runner `--mode live` contre workers réel (mock LLM via `LLM_PROVIDER` test) ; assert le fichier de run contient bien `retrieved_contexts` + `answer` + `citations` pour chaque entrée et que deux appels séparés ont eu lieu (interception via `pytest-httpserver` ou journal workers).
- AC-4 : property · double exécution `--mode offline` consécutive, assert hash SHA-256 byte-identique des fichiers de run (modulo timestamps).
- AC-5 : unit · valider schéma de sortie via Pydantic + JSON Schema sur un run fixture.
- AC-6 : unit · construire un run fixture mixte canon+off_topic, assert `metrics` racine = moyenne sur canon uniquement.
- AC-7 : unit · construire un run fixture off_topic, assert `keyword_overlap_rate` calculé selon règle (au moins un keyword present dans answer).
- AC-8 : unit · entrée canon avec `expected_contexts=[A,B]` et `retrieved_contexts=[A,C]` → `context_recall_structural=0.5`.
- AC-9 : integration CLI · sans `--baseline` → gate A appliquée, gate B skipped (log) ; avec `--baseline` → les deux appliquées.
- AC-10 : integration CLI · run fixture avec `faithfulness=0.80` → exit code 1 + stderr contient `faithfulness observed=0.80 threshold=0.85`.
- AC-11 : integration CLI · baseline `faithfulness=0.90`, run `faithfulness=0.87` → drop 0.03 > tolérance 0.02 → exit code 1.
- AC-12 : integration CLI · run conforme → exit code 0 + stdout contient `event=eval_summary`.
- AC-13 : contract · `.github/workflows/eval.yml` parsé par `actionlint` ; déclencheur `pull_request` cible main ; étape upload-artifact présente.
- AC-14 : contract · `workflow_dispatch` exposé avec input `baseline` ; assert via inspection YAML.
- AC-15 : integration · injecter mock workers qui timeout sur 1 entrée, qui répond 500 sur 1 autre, qui répond JSON invalide sur 1 autre, sur 46 entrées → `totals.errors=3`, run continue, gates appliquées sur les 43 restantes ; avec 6 erreurs (> 10%) → exit code 1.
- AC-16 : property · sur 100 runs simulés avec env `LLM_API_KEY=sk-xxxxx`, `DATABASE_URL=postgres://user:pwd@h/db`, assert aucune sous-chaîne `sk-xxxxx` ni `pwd` dans le fichier de run ni dans stdout/stderr capturés.
- AC-17 : contract · CI rejette toute PR dont le diff modifie `eval/baseline.json` sans le label humain `eval-baseline-bump` (ou règle équivalente) ; `gitleaks` ne flag pas le baseline.

## Performance / SLO

- `--mode offline` complet sur 46 entrées : runtime ≤ 90 s sur runner GitHub `ubuntu-latest` standard (4 vCPU / 16 GiB).
- `--mode live` non gated SLO phase 1 (dépend du provider LLM) ; observable via `started_at` / `finished_at` dans le run.
- Pas de parallélisation des appels en phase 1 (séquentiel, pour stabilité Ragas et budget LLM contrôlé).

## Security / trust boundary

- Lecture `LLM_API_KEY` via `pydantic.SecretStr` ou `secrecy` équivalent Python (cf `.claude/rules/security.md` A09).
- Aucune valeur de secret jamais dans le fichier de run, stdout, stderr, ni artefact CI (AC-16 + scan `gitleaks` CI conservé).
- `eval/baseline.json` versionné en clair = données non-sensibles (métriques numériques + métadonnées run) ; vérifier absence de payload réponse LLM brut dans la structure du baseline (AC-5 limite à métriques agrégées, pas de `answer` complets).
- Workflow GitHub Actions : pas de `pull_request_target`, uniquement `pull_request` (pas d'accès secrets aux forks) ; `workflow_dispatch` réservé aux mainteneurs (permissions repo).
- `golden_qa.jsonl` : confirmé hors `.gitignore`, versionné en clair (46 entrées) ; pas de retrait à effectuer dans cette PR (note d'écart vs reco initiale).

## Observability

- Logs runner JSON structurés : `event=eval_start`, `event=eval_entry` (par entrée : id, mode, status, latencies), `event=eval_summary` (totaux + métriques + gates), `event=eval_error` (par erreur entrée).
- Aucune métrique exportée OpenTelemetry phase 1 (le fichier de run + l'artefact CI sont la source de vérité).
- Le `request_id` UUID v4 généré par le runner pour chaque entrée est propagé en header `X-Request-Id` aux deux appels workers et présent dans `entries[].request_id` du run.

## Effort estimate

L — runner CLI + clients HTTP + stub LLM + métriques (Ragas + déterministes) + gates A/B + workflow CI + tests intégration + initialisation baseline. Au-delà de 300 LOC probable ; à splitter possiblement en `EVAL-001a` (runner + offline + gate A) et `EVAL-001b` (gate B + live + workflow) si l'architect le confirme dans le plan.

## Open questions

- OQ-1 : Le runner doit-il créer `eval/baseline.json` automatiquement lors de la première exécution si absent, ou exiger qu'il soit posé à la main par l'humain avant tout merge de cette PR ? (impact : ordering du commit baseline dans la PR EVAL-001).
- OQ-2 : Le stub LLM déterministe (`--mode offline`) — la "règle figée" mentionnée à AC-4 doit-elle être spécifiée ici (ex. "concaténer keywords + premiers 200 chars de chaque chunk") ou laissée à l'architect ? Risque : si la règle est trop pauvre, `faithfulness` offline sera artificiellement bas et la gate A absolue ne pourra pas être atteinte en offline.
- OQ-3 : Gate A absolue en `--mode offline` — les seuils `faithfulness ≥ 0.85` etc. sont-ils atteignables avec un stub LLM, ou la gate A ne doit s'appliquer qu'en `--mode live` (et seule la gate B no-regression s'applique en offline sur PR) ? Implication directe sur la viabilité de la CI gate.
- OQ-4 : Le splitting EVAL-001a / EVAL-001b évoqué dans l'effort estimate L — humain à confirmer ou laisser à l'architect en `/plan` ?
- OQ-5 : `--mode live` workflow_dispatch — quel provider LLM par défaut (cohérent avec `LLM_PROVIDER=mistral` de GEN-001 AC-10) ? Faut-il un `LLM_PROVIDER` distinct pour Ragas-as-judge (souvent OpenAI par défaut chez Ragas) ?
- OQ-6 : Détection PR baseline bump (AC-17 test oracle) — règle CI exacte à figer : label humain ? CODEOWNERS sur `eval/baseline.json` ? Commit message convention `chore(eval): bump baseline` enforced ?

## Status

draft
