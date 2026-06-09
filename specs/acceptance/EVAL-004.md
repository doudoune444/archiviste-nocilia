# EVAL-004 — Premier run live Mistral + re-bake baseline + recalibration seuils Gate A

## Contexte

EVAL-003 a câblé le juge Ragas Mistral (`mistral-large-2411`) mais a délibérément reporté le premier run live payant et le re-bake de `eval/baseline.json` (EVAL-003 Non-goals §1) : le baseline actuel est un bootstrap all-zeros (`git_sha: "initial"`, `metrics: null`) et les seuils Gate A absolus (`gates.py:9-14` : faithfulness/answer_relevancy 0.85, context_precision/context_recall 0.70) sont des cibles non encore confrontées à un run réel jugé par Mistral. Ce ticket exécute le premier run live payant, fige ses scores réels comme baseline versionné, et recalibre les seuils Gate A à partir des scores canon observés — pour que Gate A et Gate B mesurent désormais une qualité réelle plutôt que des valeurs aspirationnelles.

## Acceptance criteria

- AC-1 : Un run live est exécuté par l'humain via le `workflow_dispatch` existant (EVAL-001 AC-14) en `--mode live --set specs/golden_qa.jsonl`, ciblant les **workers prod** (`WORKERS_URL` prod, auth OIDC OBS-007/OBS-009), sur les **46 entrées** du golden set, avec le juge par défaut `RAGAS_JUDGE_PROVIDER=mistral` (`mistral-large-2411`, EVAL-003) ; le fichier `eval/runs/<timestamp>.json` produit a `runner_mode: "live"`, `totals.entries == 46`, et `totals.errors / totals.entries ≤ 0.10` (sinon le run est rejeté et non committé, AC-9 EVAL-001 / Failure modes). La cible prod garantit que le baseline figé reflète le corpus/embeddings/pipeline que Gate B protège en prod.
- AC-2 : `eval/baseline.json` après ce ticket est **byte-identique** au fichier `eval/runs/<timestamp>.json` produit en AC-1 (copie verbatim, aucun champ édité à la main) ; l'invariant « baseline == un vrai fichier de run » (EVAL-001 AC-17) est préservé — `runner_mode: "live"`, `metrics` racine agrégés sur les seules entrées canon, `git_sha` = SHA du run réel.
- AC-3 : Le commit qui met à jour `eval/baseline.json` a pour message exact `chore(eval): bump baseline`, ne modifie **que** `eval/baseline.json` (aucun autre fichier dans ce commit), et est le commit **HEAD** de la PR au moment du merge — de sorte que la règle EVAL-001 AC-17 (Gate-B skip si HEAD == bump baseline ET diff == `eval/baseline.json` seul) s'applique et que Gate B est skippée pour cette PR.
- AC-4 : L'édition des seuils Gate A dans `eval/gates.py` (`GATE_A_THRESHOLDS`) est un commit **distinct** du commit de bump baseline (AC-3), et ce commit d'édition des seuils est ordonné **avant** le commit de bump baseline dans l'historique de la PR (le bump baseline reste HEAD).
- AC-5 : Chaque seuil de `GATE_A_THRESHOLDS` est recalculé par la règle `seuil = floor((métrique_canon_observée − 0.05) × 100) / 100` (soustraction d'une marge fixe de 0.05, arrondi à l'inférieur à 2 décimales), où `métrique_canon_observée` est la valeur du champ `metrics.<métrique>` racine du run AC-1 (agrégé canon uniquement) ; la règle est appliquée aux quatre métriques `faithfulness`, `answer_relevancy`, `context_precision`, `context_recall`.
- AC-6 : Le seuil recalculé par la règle AC-5 ne descend **jamais** sous le plancher de sécurité `0.50` ; si pour une métrique le seuil calculé `floor((observé − 0.05) × 100) / 100 < 0.50`, le run est traité comme un **hard stop** : aucun seuil n'est édité, aucun baseline n'est figé, l'humain investigue la métrique dégradée (la gate n'est **jamais** silencieusement désactivée par un seuil ≤ 0).
- AC-7 : Si l'application de la règle AC-5 produit pour une métrique un seuil **inférieur** au seuil actuel correspondant (faithfulness/answer_relevancy 0.85, context_precision/context_recall 0.70) mais **≥ 0.50** (au-dessus du plancher AC-6), cet abaissement est une concession de qualité approuvée humain : il est listé nommément dans le corps de la PR et dans `eval/README.md` (section Gates) avec `<métrique> : <ancien_seuil> → <nouveau_seuil> (observé <valeur>)` ; aucun abaissement n'est introduit sans cette mention explicite.
- AC-8 : `eval/README.md` section « Gates » et « Estimated Cost » sont mis à jour pour refléter (a) les seuils Gate A effectifs post-recalibration, (b) la bande de coût observée du run live Mistral réel `~$0.50–$1.00/run` (remplaçant l'estimation OpenAI `~$0.46/run`), et (c) la date / `git_sha` du run de baseline figé.
- AC-9 : Aucune migration, aucun changement de `specs/openapi/gateway-to-workers.yml`, aucun changement de `specs/golden_qa.jsonl`, aucun changement du code runner (`eval/*.py` hors `eval/gates.py`) ne sont introduits par ce ticket (`git diff` sur ces chemins → 0 changement, hormis `eval/gates.py` limité à `GATE_A_THRESHOLDS` et `eval/README.md`/`eval/baseline.json`).
- AC-10 : Le baseline figé (AC-2) est auto-cohérent **par construction** : un run hypothétique évalué contre lui-même produit des deltas Gate B nuls, donc strictement dans les tolérances EVAL-001 AC-11 (faithfulness/answer_relevancy −0.02, context_precision/context_recall −0.03) ; cette auto-cohérence est une propriété logique des tolérances Gate B (delta == 0 ≥ tolérance négative) et ne requiert **aucun** run live payant additionnel pour être établie.

## Non-goals

- Pas de réalignement du job prod OBS-009 (`RAGAS_JUDGE_PROVIDER=openai` dans `cloud_run_job.tf`) vers Mistral — OBS-009 a été spécifié avec un juge OpenAI ; l'incohérence juge prod (OpenAI) vs juge baseline/CI (Mistral) est un **follow-up explicite** (ticket aval OBS/EVAL) et n'est pas tranchée ici.
- Pas de modification des tolérances Gate B (`GATE_B_TOLERANCES`, `GATE_B_OFFLINE_TOLERANCES` dans `gates.py:16-26`) — seul `GATE_A_THRESHOLDS` est recalibré.
- Pas de modification du code runner, du chemin de génération (`/v1/generate`, `LLM_PROVIDER`), du juge (EVAL-003), ni du prompt système.
- Pas de modification de `specs/golden_qa.jsonl` (source de vérité humaine, 46 entrées) ni du fixture CI `eval/fixtures/ci_smoke_qa.jsonl`.
- Pas de re-pin du snapshot juge Mistral (`mistral-large-2411`) — repris tel quel d'EVAL-003.
- Pas d'introduction d'une marge de variance/multi-run pour le calcul de seuil — marge fixe 0.05 sur un run unique (pas d'estimation de variance phase 1).
- Pas de cap coût codé sur le run — coût borné par la taille du golden set + la posture EVAL-001 (séquentiel, sans retry, abort à 10% d'erreurs) ; l'humain confirme le prix Mistral avant le déclenchement.
- Pas d'automatisation du bump baseline ni de la recalibration — geste humain explicite (run dispatch payant + commits).
- Pas de gate offline (CI PR) issue de ce ticket : Gate A est `--mode live` only **par design** (`gates.py:52`) ; les seuils recalibrés ne mordent qu'au **prochain run dispatch live**, jamais sur une PR. Recalibrer une gate manuelle-only est intentionnel et n'introduit aucun gate CI nouveau.

## Pre-conditions

- **EVAL-003 mergé** (`213e11c`) : juge Ragas configurable, défaut `mistral` (`mistral-large-2411`) effectivement câblé dans `eval/metrics.py` ; `RAGAS_JUDGE_PROVIDER` / `RAGAS_JUDGE_MODEL` opérationnels.
- **EVAL-001 mergé** : runner live `workflow_dispatch`, Gate A (`gates.py`), Gate B + règle de skip baseline-bump (AC-17), invariant `eval/baseline.json` versionné == fichier de run.
- **Clé `LLM_API_KEY` Mistral disponible** côté `workflow_dispatch` (secret repo / env), avec crédit suffisant pour un run de 46 entrées jugées Mistral.
- **Prix Mistral confirmé humain** avant déclenchement du run payant (bande visée `~$0.50–$1.00/run`).
- **Workers prod accessibles** depuis le runner live (`WORKERS_URL` prod, auth OIDC OBS-007/OBS-009), pipeline RET-001 + GEN-001 prod opérationnel pour les 46 entrées — le baseline figé reflète le corpus/embeddings prod que Gate B protégera.

## Failure modes

- Taux d'erreur du run live > 10% (`totals.errors / totals.entries > 0.10`) → run rejeté (EVAL-001 AC-15, exit `1`), **non** committé comme baseline ; l'humain investigue avant re-run, aucun baseline n'est figé sur un run dégradé.
- `LLM_API_KEY` Mistral absente / sans crédit au déclenchement → échec du juge en run live (EVAL-003 Failure modes), aucun fichier de run exploitable, aucun bump.
- Snapshot `mistral-large-2411` indisponible côté API Mistral → échec explicite reproductible (EVAL-003 Failure modes) → déclenche le follow-up re-pin, pas de baseline figé.
- Une métrique canon observée < seuil actuel sans mention de concession (AC-7 non respecté) → la PR est invalide (review humaine), l'abaissement silencieux est interdit.
- Une métrique canon dont le seuil calculé tombe sous le plancher `0.50` (AC-6) → **hard stop** : run non figé comme baseline, seuils non édités, escalade humaine ; la gate n'est jamais désactivée par un seuil ≤ 0.
- Commit de bump baseline non-HEAD ou diff non limité à `eval/baseline.json` → règle EVAL-001 AC-17 ne s'applique pas, Gate B s'exécute et peut faire échouer la CI sur le re-bake lui-même → ordering AC-3/AC-4 obligatoire.

## Touch points (informatif, non contraignant pour l'architect)

- `eval/baseline.json` — **remplacé** (humain-only, approbation requise) : contenu = run live Mistral réel verbatim (AC-2). Commit `chore(eval): bump baseline`, HEAD de la PR (AC-3).
- `eval/gates.py` — **modifié** : valeurs de `GATE_A_THRESHOLDS` recalibrées par la règle AC-5 (uniquement le dict de seuils, pas la logique). Commit séparé, ordonné avant le bump baseline (AC-4).
- `eval/README.md` — **modifié** : seuils Gate A effectifs, concessions nommées (AC-7), bande de coût Mistral réelle, date/`git_sha` du baseline (AC-8).
- `eval/runs/<timestamp>.json` — artefact source du run live (gitignored sauf baseline) ; sa copie devient `eval/baseline.json`.
- `CHANGELOG.md` — entrée `## [Unreleased]` section EVAL.
- **Non touchés** : `migrations/*.sql`, `specs/openapi/gateway-to-workers.yml`, `specs/golden_qa.jsonl`, `eval/fixtures/`, tout `eval/*.py` hors `eval/gates.py` (AC-9).

## Test oracle

- AC-1 : integration / manuel auteur · run `workflow_dispatch` live → artefact `eval/runs/<ts>.json` avec `runner_mode=="live"`, `totals.entries==46`, `totals.errors/46 ≤ 0.10`.
- AC-2 : contract · `diff <(jq -S . eval/baseline.json) <(jq -S . eval/runs/<ts>.json)` → 0 (byte-identique modulo ordre de clés) ; `eval/baseline.json` a `runner_mode=="live"` et `git_sha` non-`"initial"`.
- AC-3 : integration CI · `git log -1 --format=%s` == `chore(eval): bump baseline` ET `git diff --name-only HEAD~1 HEAD` == `eval/baseline.json` seul → règle EVAL-001 AC-17 (Gate B skip) déclenchée, CI verte.
- AC-4 : contract · l'historique de la PR contient un commit éditant `eval/gates.py` (`GATE_A_THRESHOLDS`) **avant** le commit HEAD de bump baseline (`git log --oneline` ordre) ; les deux commits sont distincts (diffs disjoints).
- AC-5 : unit · pour chaque métrique, `nouveau_seuil == floor((observé − 0.05) * 100) / 100` (table de vérification sur les 4 valeurs `metrics.*` du run AC-1 vs les valeurs committées dans `GATE_A_THRESHOLDS`).
- AC-6 : unit / review humaine · pour chaque métrique, assert `floor((observé − 0.05) * 100) / 100 ≥ 0.50` ; si une valeur viole le plancher, vérifier qu'aucun seuil n'est édité et qu'aucun baseline n'est figé (hard stop, pas de gate ≤ 0 committée).
- AC-7 : contract · pour toute métrique dont `0.50 ≤ nouveau_seuil < ancien_seuil`, `grep` du README + corps de PR contient la ligne `<métrique> : <ancien> → <nouveau>` ; aucun abaissement non documenté.
- AC-8 : contract · `grep` README pour les 4 seuils Gate A post-recalibration, la bande `~$0.50–$1.00`, et le `git_sha`/date du baseline.
- AC-9 : contract · `git diff` sur `migrations/`, `specs/openapi/gateway-to-workers.yml`, `specs/golden_qa.jsonl`, `eval/fixtures/`, `eval/*.py` hors `gates.py` → 0 changement.
- AC-10 : logique / review humaine · démontrer que delta(baseline, baseline) == 0 pour les 4 métriques et que `0 ≥ tolérance_négative` pour chaque tolérance Gate B EVAL-001 AC-11 → Gate B passe par construction, sans run payant additionnel.

## Performance / SLO

- Durée du run live : dominée par les 46 × (appels `/v1/generate` Mistral + judge Mistral) ; séquentiel, non gated (EVAL-001 §Performance), observable via `started_at`/`finished_at`.
- Coût observé visé : `~$0.50–$1.00/run` (46 entrées, juge `mistral-large-2411`) — documenté README (AC-8), non un cap codé. Un **seul** run live payant requis pour ce ticket (AC-10 auto-cohérence est logique, sans run additionnel).

## Security / trust boundary

- `LLM_API_KEY` Mistral lue via `pydantic.SecretStr` (EVAL-001 AC-16 / EVAL-003 AC-7) ; jamais sérialisée dans `eval/baseline.json`, logs, ni artefact CI — vérifier que le run figé comme baseline ne contient aucun fragment de clé (AC-2 préserve EVAL-001 AC-16).
- `eval/baseline.json` versionné en clair = métriques numériques + métadonnées run (non sensible) ; `gitleaks` CI conservé ne doit pas flag le baseline.
- Aucune nouvelle surface réseau ajoutée en CI : le run live reste `workflow_dispatch`-only (jamais sur PR).

## Observability

- Le fichier de run live (devenu baseline) est la trace durable du run de référence (consommé par OBS-004 / Gate B futurs).
- Logging structlog EVAL-001 inchangé (`event=eval_summary`, etc.) ; aucune observabilité custom ajoutée.

## Effort estimate

S — un run `workflow_dispatch` payant (geste humain, hors LOC), copie verbatim du run en `eval/baseline.json` (1 commit), édition de 4 valeurs dans `GATE_A_THRESHOLDS` (1 commit séparé, ordonné avant), mise à jour README + CHANGELOG. 0 migration, 0 OpenAPI, 0 code runner, 0 changement golden set. Diff code < 30 lignes (hors baseline.json) ; vertical slice respecté.

## Open questions

- Aucune. Les 4 questions ouvertes (OQ-1 plancher seuil, OQ-2 cible run, OQ-3 gate live-only, OQ-4 auto-cohérence) sont résolues et encodées dans les AC / Non-goals.

## Status

draft
