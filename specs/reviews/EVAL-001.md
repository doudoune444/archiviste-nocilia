# Review — EVAL-001

## Verdict

REQUEST_CHANGES

Lint + tests local : ruff green, mypy --strict green, pytest 37/37 green (eval suite).
Diff prod Python : 989 LOC (waiver ADR-0008 noté, voir Findings #5).

## Findings

| File:line | Severity | Pattern | Evidence | Suggested fix |
|---|---|---|---|---|
| `eval/baseline.json:1-7` | HIGH | Spec violation AC-17 schéma | Baseline committé = `{"faithfulness":0.0, "answer_relevancy":0.0, ...}` à la racine. AC-17 exige "schéma strictement identique à un `eval/runs/<timestamp>.json`" (= top-level `metrics:{}`, `breakdown_by_mode`, `entries`, etc). Conséquence runtime : `apply_gate_b` lit `baseline_run.get("metrics", {})` ligne 80 de `gates.py` → renvoie `{}` → toutes les comparaisons Gate B logguent `gate_b_metric_skipped reason=null metric=<...>` (cf `gates.py:85-89` + `:120-124`). **Gate B est de facto un no-op tant qu'un humain n'a pas commit un baseline au bon schéma.** | Remplacer `eval/baseline.json` par un baseline au schéma run-file (champs `mode`/`started_at`/`finished_at`/`git_sha`/`runner_mode`/`totals`/`breakdown_by_mode`/`metrics`/`entries`). Pour l'amorçage, accepter explicitement `metrics:{faithfulness:null,...}` + `breakdown_by_mode.canon.{context_recall_structural:0.0, keyword_overlap_rate:0.0}` puis bump réel au premier run vert. Ajouter un test contrat `tests/test_baseline_schema.py` qui valide `eval/baseline.json` via le même Pydantic que `RunFile`. |
| `.github/workflows/eval.yml:41-55` + `eval/baseline_skip.py:14-31` | HIGH | Trust boundary AC-17 contournable | Le baseline-skip-check inspecte UNIQUEMENT `HEAD^..HEAD` (commit HEAD du PR). Un attaquant qui veut faire passer du code malveillant en évitant Gate B peut : (1) commiter le malware sur `feat/<x>` ; (2) ajouter un dernier commit `chore(eval): bump baseline` touchant uniquement `eval/baseline.json` ; (3) push → workflow voit HEAD = baseline bump only → `skip=true` → Gate B sautée → CI verte malgré régression. La validation porte sur le mauvais périmètre : Gate B doit valider tout le PR diff (`merge-base..HEAD`), pas seulement le dernier commit. | Comparer sur le périmètre PR complet : `git diff --name-only $(git merge-base origin/main HEAD) HEAD` doit contenir uniquement `eval/baseline.json` ET tous les commits du PR doivent matcher le pattern (ou : exiger que le PR soit un single-commit). Sinon enforce Gate B. Mettre à jour `should_skip_gate_b()` + workflow step en cohérence. Ajouter test d'attaque dans `test_baseline_skip.py` (PR multi-commits avec dernier commit baseline-only doit retourner False). |
| `eval/run_writer.py:120-131` + `eval/ragas_runner.py:274-275` | HIGH | Secret leak résiduel / dead branch | (a) `write_run` ne redacte QUE la sérialisation finale (`raw.replace(secret, "[REDACTED]")`) ; le `secret` doit être présent **textuellement** dans le JSON. Ni `LLM_API_KEY` ni `WORKERS_URL` ne sont injectés dans `answer`/`citations`/`request_id` dans le pipeline actuel → la redaction ne se déclenche jamais en pratique, mais le test AC-16 (`test_secrets_not_leaked_in_run_file`) passe trivialement car aucun chemin ne fait fuiter. Si demain un `httpx.HTTPError` est stringifié avec l'URL complète (cas `EntryError.detail=str(exc)` ligne 71/119 de `clients.py`) **et** que `WORKERS_URL` contient des credentials, fuite possible — mais `detail` n'est pas sérialisé dans le run file. (b) Ligne 128 contient `re.escape(secret) if False else secret` — branche morte (`if False`), `re.escape` jamais appliqué, viole `clean-code.md` "no commented-out code / dead branches". (c) `_redact_string` + `_redact_entry` (lignes 73-86) sont des fonctions **mortes**, jamais référencées. | (1) Supprimer `if False else` ligne 128 ; (2) supprimer les fonctions mortes `_redact_string`/`_redact_entry` ; (3) renforcer le test AC-16 en faisant fuiter un secret intentionnellement (ex : mocker `httpx.HTTPError("...sk-fakesecret...")` et asserter redaction effective dans `entry.status=upstream_error` + log) — sinon AC-16 est testé contre un golden-path qui ne fuit jamais. |
| `eval/ragas_runner.py:240-242` + `eval/metrics.py:11-14` + `eval/stub_llm.py:20-29` | HIGH | Tautological metric — gaming AC-7 offline | En `--mode offline`, `_run_entry_offline` construit `answer = " ".join(expected_answer_keywords) + chunks…` puis `_compute_entry_metrics` calcule `compute_keyword_overlap(answer, expected_answer_keywords)` — substring match contre les **mêmes** keywords qui viennent d'être collés dans la réponse. Résultat : `keyword_overlap_rate=1.0` constant pour toute entrée avec `expected_answer_keywords` non-vide, indépendamment de la qualité du retrieve. Le `keyword_overlap_rate` de la Gate B offline (AC-11 phrase 2 (b)) devient une mesure sans valeur informationnelle. Aggravé par : `seed_test_corpus.py` est un **stub** qui ne seed rien (`print("seed_test_corpus: stub — implement when ingestion pipeline lands")`) → CI offline tournera contre une DB vide → `retrieved_contexts=[]` → `context_recall_structural=0.0` constant aussi. **Gate B offline ne mesure rien en CI.** | (1) Le `keyword_overlap_rate` en offline doit utiliser une source d'évidence **autre** que les keywords (ex: scorer contre `retrieved_chunks[].text` only, OU réordonner la règle stub pour mettre keywords après le tronc, et matcher sur le tronc). (2) Si le métrique reste tel quel, expliciter dans `gates.py` + spec que la gate `keyword_overlap_rate` offline est une property check sur la **présence des chunks** uniquement et renommer le métrique (`stub_self_consistency_rate`). (3) Wirer un seed corpus réel (ou un fixture jsonl ingéré directement en DB) — sinon merge le ticket EVAL-001 avec un acknowledgement explicite que `seed_test_corpus.py` est un blocker `no-workaround.md` à ouvrir en `docs/blockers.md`. |
| `eval/tests/` (absence) | MED | Spec violation AC-4 — test missing | AC-4 : "deux exécutions consécutives offline sur la même DB produisent des métriques byte-identiques". Test oracle ligne 78 spec : "double exécution `--mode offline` consécutive, assert hash SHA-256 byte-identique des fichiers de run (modulo timestamps)". Plan step 91 le liste. **Aucun test au niveau runner CLI ne fait ça** — seul `test_build_stub_answer_deterministic_double_run` teste la pure-function `build_stub_answer`, ce qui est trivial (pas d'I/O). Sans le test CLI, rien ne garantit que `aggregate_breakdown`, `_compute_entry_metrics`, ou la sérialisation `write_run` ne réintroduisent du non-déterminisme (ex: ordre de dict, floats arrondis). | Ajouter `test_runner_cli_offline_byte_identical_double_run` qui invoque `_run_runner(...)` deux fois sur le même fixture + same DB (mock httpserver), lit les deux `run.json`, retire `started_at`/`finished_at`/`git_sha` (et seulement ces 3 champs), hash SHA-256 → assert égal. Si l'égalité échoue (ex: dict ordering, float repr), corriger côté writer (sort_keys=True, format float fixe). |
| `eval/ragas_runner.py:274-299` | MED | Quality — error-rate check shadowed | Quand `--baseline` fourni mais fichier absent (auto-create path), le runner retourne `exit 0` directement après `write_run(args.baseline, run)` sans vérifier `error_rate > 10%`. Si 100% des entrées sont en erreur (workers down mid-run après healthz OK), le runner écrit un baseline poubelle (toutes erreurs) + verdict `PASS (no baseline yet)`. Le baseline ainsi commit-able contiendrait des nulls partout, et un futur run normal serait alors comparé à un baseline absurde. | Évaluer `error_rate_exceeded` AVANT le branchement auto-create baseline (déplacer le bloc lignes 308-314 plus haut), et **refuser** l'auto-create si error rate > 10%. Émettre stderr `cannot bootstrap baseline: error rate <X>% exceeds 10%` + exit 1. |
| `eval/ragas_runner.py:78` + `:71` | LOW | `except Exception` — swallow trop large | `_get_git_sha` (`except Exception: return "unknown"`) et `_check_workers_reachable` (`except Exception: return False`) capturent tout, y compris `KeyboardInterrupt` / `SystemExit` ne sont pas couverts (`BaseException` exclus) mais `MemoryError` / programmation errors le sont. `clean-code.md` + `no-workaround.md` : forbidden "Catching + swallowing the error". Acceptable ici (best-effort init) mais cite explicitement les exceptions attendues (`subprocess.CalledProcessError`, `FileNotFoundError`, `httpx.HTTPError`, `OSError`). | `except (subprocess.CalledProcessError, FileNotFoundError, OSError):` pour `_get_git_sha`. `except (httpx.HTTPError, OSError):` pour `_check_workers_reachable`. |
| `eval/clients.py:46-52`, `:90-96` | LOW | Dead field | `_extra_headers: dict[str, str] = field(default_factory=dict, repr=False)` jamais peuplé par aucun caller. Premature flexibility (`clean-code.md` "No over-configurability"). | Supprimer le champ jusqu'au premier vrai caller. |
| `eval/run_writer.py:117` + `eval/ragas_runner.py:179-187` | LOW | Champ redondant `mode` vs `runner_mode` | `RunFile.mode == RunFile.runner_mode` (cf `_build_run` ligne 178-182 : `mode=runner_mode, runner_mode=runner_mode`). AC-5 cite `mode` ET `runner_mode` dans le schéma → ambigu, mais conserver les deux comme alias trompe le futur lecteur. | Décider : soit drop `mode` (et amender AC-5), soit donner sémantique distincte (ex: `mode = "eval"`, `runner_mode = "live"/"offline"`). Doc + commit. |

## Spec coverage

| AC | Couverture | Test |
|---|---|---|
| AC-1 | ✓ | `eval/tests/test_loader.py::test_load_*` (7 cas) |
| AC-2 | ✓ | `test_runner_cli.py::test_missing_mode_exits_2` |
| AC-3 | ✗ | Aucun test n'asserte que `/v1/retrieve` ET `/v1/generate` sont appelés séparément en live avec `top_k=5` identique. `test_runner_cli.py` ne couvre que offline. AC-3 spec line 77 demandait `pytest-httpserver` interception live → absent. |
| AC-4 | ✗ partial | Voir Finding #5. Seul le stub pur est testé, pas le runner full. |
| AC-5 | ✗ partial | Aucun test ne valide le run output via Pydantic/JSON Schema (plan step 79). Fixtures `run_canon_only.json` / `run_mixed.json` existent mais inutilisées. |
| AC-6 | ✗ | Pas de test asserting `metrics` racine = moyenne canon-only (test oracle ligne 80). Fixture `run_mixed.json` existe mais non chargée par un test. |
| AC-7 | ✓ partial | `test_metrics.py::test_keyword_overlap_*` couvrent la fonction pure ; voir Finding #4 sur la métrique tautologique en offline. |
| AC-8 | ✓ | `test_metrics.py::test_context_recall_structural_*` |
| AC-9 | ✓ | `test_runner_cli.py::test_baseline_absent_auto_create` + `test_gates.py::test_gate_b_no_baseline_skipped` |
| AC-10 | ✓ | `test_gates.py::test_gate_a_live_*` |
| AC-11 | ✓ | `test_gates.py::test_gate_b_*` (live tolerance + offline property checks) |
| AC-12 | ✓ partial | `test_gates.py::test_gates_pass_returns_passed_results` ; pas d'assert sur le format `event=eval_summary` stdout end-to-end |
| AC-13 | ✓ partial | `.github/workflows/eval.yml` existe, `actions/upload-artifact@v4` présent, mais pas de `actionlint` step CI |
| AC-14 | ✓ | `workflow_dispatch` + input `baseline` présents, README documente coût $0.01/sample |
| AC-15 | ✓ partial | `test_error_rate_exceeds_threshold_exits_1` couvre 1 cas ; pas de test des 3 status (`timeout`, `upstream_error`, `malformed`) discriminés |
| AC-16 | ✗ | Voir Finding #3. Test passe trivialement car aucun chemin du runner offline n'injecte les secrets dans le run output. Property test à 100 runs (plan step 91) absent. |
| AC-17 | ✗ partial | `test_baseline_skip.py` 4 cas ; voir Finding #2 sur le contournement HEAD-only. |

## Property invariants

Aucun INV de `specs/properties.md` n'est applicable à EVAL-001 (eval = loop de mesure, pas runtime invariant). Plan confirme ligne 94 "Aucun INV property-based". OK.

## Security

- **Secrets in code** : pas de hardcoded credentials détectés dans le diff.
- **Sensitive types** : `LLM_API_KEY` lu via `os.environ.get` (`run_writer.py:67`) → pas wrappé en `pydantic.SecretStr` ; toutefois le runner n'est pas un service longue durée et la valeur ne traverse pas l'app — acceptable mais à noter. Workflow live (`eval.yml:159, 169`) passe `LLM_API_KEY` via `${{ secrets.* }}` → OK.
- **SQL** : pas d'accès DB direct depuis le runner (passe par `/v1/retrieve`).
- **SSRF** : `_check_workers_reachable` (`ragas_runner.py:74-79`) appelle `httpx.get(workers_url)` avec URL passée en CLI ; risque limité car opérateur-controlled, mais en mode CI, `--workers-url` est hardcodé `http://localhost:8000`. OK.
- **Trust boundary AC-17** : voir Finding #2 — **bypass critique**.
- **CORS / CSP / rate-limit** : N/A, runner est CLI batch.
- **Logs** : `structlog` JSON ; pas de PII détectée loggée. OK.
- **gitleaks** : non-bloquant ici, baseline.json en clair (métriques numériques uniquement) — OK conformément à AC-17.

## Out-of-scope changes

Tous les fichiers touchés sont dans le périmètre du plan (`specs/plans/EVAL-001.md` "Files to touch"). RAS.

## LOC waiver ADR-0008 — verdict reviewer

Lecture détaillée des 8 modules production :

- **`gates.py` 131 LOC** : SRP respecté (Gate A + Gate B + helper privé). Fonctions ≤ 40 lignes (max `apply_gate_b` ≈ 35). **Légitime**.
- **`run_writer.py` 131 LOC** : violations clean-code : `_redact_string`/`_redact_entry` morts (≈ 14 LOC), `if False else` ligne 128 (1 LOC), `dict[str, object]` typing pessimiste. **Sur-ingénierie partielle** : drop ~20 LOC en faisant le ménage Finding #3.
- **`clients.py` 131 LOC** : 2 clients quasi-duplicate (`RetrieveClient` / `GenerateClient`), `_extra_headers` mort, `RetrievedChunk` redéfini en double avec `stub_llm.py`. Une factorisation `_post_json(endpoint, payload, ...) -> dict | EntryError` économiserait ~25 LOC sans premature abstraction (3e occurrence : retrieve, generate, future ingest). **Sur-ingénierie modérée**.
- **`metrics.py` 122 LOC** : `_run_ragas_evaluate` (40 LOC) borderline ; OK.
- **`loader.py` 77 LOC** : 3 validators redondants (`validate_id_present` est doublé par `Field(min_length=1)`). ~10 LOC à trim.
- **`baseline_skip.py` 56 LOC** : OK individuellement, mais contournable (Finding #2) → refactor inévitable.
- **`stub_llm.py` 29 LOC** : OK.
- **`ragas_runner.py` ~312 net** : `main()` fait 144 lignes (`# noqa: PLR0915`), violation `clean-code.md` "≤ 40 lignes body". À splitter : `_handle_auto_create_baseline()`, `_emit_summary()`, `_compute_error_rate()`.

**Avis reviewer** : le waiver est *partiellement* justifié — la décomposition modulaire est saine, mais ~50-80 LOC peuvent être retirées par cleanup (Findings #3, #7, #8, #9 + `main()` ≤ 40 lignes). Néanmoins le split EVAL-001a/b proposé par le plan créerait plus de glue que d'économie ; ne pas re-splitter. **Demander le trim avant merge**, garder waiver ADR-0008 mais l'amender pour citer le trim post-review.

## Recommandations de cleanup (à appliquer avant APPROVE)

1. **Fix HIGH #1** : remplacer `eval/baseline.json` par un schéma run-file conforme AC-17.
2. **Fix HIGH #2** : élargir `should_skip_gate_b` au périmètre PR complet (`merge-base..HEAD`) + ajouter test d'attaque multi-commits.
3. **Fix HIGH #3** : retirer `_redact_string`/`_redact_entry` morts + `if False else` ; renforcer test AC-16 avec injection effective.
4. **Fix HIGH #4** : ré-architecter `keyword_overlap_rate` offline pour ne plus être tautologique (ou renommer + documenter limitation) ; ouvrir blocker pour `seed_test_corpus.py` stub.
5. **Fix MED #5** : ajouter test CLI double-run byte-identique.
6. **Fix MED #6** : déplacer error-rate check avant auto-create baseline.
7. Refactor `main()` ≤ 40 lignes (`clean-code.md`).
8. Trim LOC ~50 (cleanup #3 + dead `_extra_headers` + factor `_post_json`).

Une fois ces 8 points adressés, verdict basculera vers APPROVE.
