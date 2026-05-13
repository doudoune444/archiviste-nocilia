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

---

## Re-review pass 2 (2026-05-13)

Re-passe adversariale après commits `be85637` (fix HIGH 1-4 + main split), `9e900be` (tests MED-5 + AC-16 prop + AC-17 multi-commit), `3320468` (ADR/CHANGELOG).
Lints + tests local : ruff green, mypy strict green (18 fichiers), pytest **46/46 green** (was 37/37). LOC prod = 1190 (vs 989 pass 1 ; +201 du fait du seed implem + tests).

### Verdict pass 2

**REQUEST_CHANGES** (reste 2 HIGH + 1 MED nouveaux ; HIGH-1/HIGH-2 réels résolus ; HIGH-3/HIGH-4 partiellement gamés ; nouveau bug bloquant en CI workflow).

### Statut des findings pass 1

| Finding | Statut | Vérification |
|---|---|---|
| HIGH-1 baseline schéma | RÉSOLU | `eval/baseline.json` contient désormais `mode`/`started_at`/`finished_at`/`git_sha`/`runner_mode`/`totals`/`breakdown_by_mode`{4 modes}/`metrics`{4 ragas null}/`entries`. `test_baseline_schema.py` (6 tests) valide la conformité. Note minor : `_note` field non-standard, harmless. |
| HIGH-2 merge-base | RÉSOLU | `baseline_skip.py:49-71` utilise `git merge-base origin/main HEAD` ; workflow `fetch-depth: 0` ; test `test_should_not_skip_gate_b_multicommit_attack` (test_baseline_skip.py:91-99) confirme : `extra_module.py` + `baseline.json` dans full diff → `should_skip=False`. Fallback `HEAD^` (line 62) acceptable pour repo local sans `origin/main`. |
| HIGH-3 keyword_overlap | **NON RÉSOLU (gaming v2)** | Voir Finding pass2 #1 ci-dessous. |
| HIGH-4 redaction wiring | RÉSOLU partiellement | `_redact_raw` wired dans `write_run` (line 117) ; dead funcs `_redact_string`/`_redact_entry` + `if False else` supprimées ; `_extra_headers` retiré de `clients.py`. **MAIS** test property 100 runs (`test_secrets_redaction_property_100_runs`) ne fait pas fuiter — il instancie une `RunFile` sans secret dans aucun champ, donc `_redact_raw` n'a rien à remplacer et le test passe trivialement (false-confidence). Voir Finding pass2 #2. |
| MED-5 byte-identical | RÉSOLU | `test_offline_double_run_byte_identical` invoque le runner deux fois via subprocess, retire `started_at`/`finished_at`/`git_sha`/`request_id`, hash SHA-256 → assert égal. `sort_keys=True` ajouté dans `write_run` ligne 116. Champs ignorés exhaustifs (vérifié : pas d'autre uuid/timestamp non-déterministe dans le run dict). |
| MED-6 error-rate auto-create | RÉSOLU | `_handle_auto_create_baseline` (ragas_runner.py:265-270) check `error_rate > ERROR_RATE_THRESHOLD` AVANT `write_run`. |
| LOC `main()` ≤ 40 | RÉSOLU | `main()` lignes 336-385 = 50 lignes brut, ~37 statements. Plus de `noqa: PLR0915`. Décomposé en `_run_all_entries`, `_resolve_ragas_metrics`, `_handle_auto_create_baseline`, `_emit_summary_and_exit`. |
| LOW except Exception | RÉSOLU | `_get_git_sha` et `_check_workers_reachable` capturent désormais `(subprocess.CalledProcessError, FileNotFoundError, OSError)` et `(httpx.HTTPError, OSError)` respectivement. |

### Findings pass 2

| File:line | Severity | Pattern | Evidence | Suggested fix |
|---|---|---|---|---|
| `.github/workflows/eval.yml:75-92` + `eval/seed_test_corpus.py:96-98` | **HIGH** | CI workflow non fonctionnel — Gate B inopérante | Trois bugs combinés rendent l'eval offline CI inexécutable : (1) **Schéma DB absent** : workflow crée l'extension `vector` mais **n'applique aucune migration** (`migrations/0002_schema.sql` non lancé) → `INSERT INTO documents` du seed lèvera `relation "documents" does not exist`. ci.yml fait pareil mais schemathesis n'utilise que `/healthz`, donc passe. (2) **`DATABASE_URL` non set au seed step** (line 80-81) → seed_test_corpus.py:96-98 imprime "DATABASE_URL not set — skipping seed" et exit 0 silencieux. Aucun seed effectif. (3) **`LLM_API_KEY` non set au step `start workers`** (line 83-92) → `LlmClient.from_env()` lifespan (workers/main.py:64) lève `LlmConfigError("LLM_API_KEY missing or empty")` → uvicorn crash → `/healthz` jamais up → runner exit code 3 (`workers unreachable`) → CI rouge **non par régression eval mais par bug workflow**. Net : la gate B offline ne s'exécute jamais en CI. | (1) Ajouter step `psql ... -f migrations/0002_schema.sql` avant le seed (et `0001_*.sql` aussi). (2) Exporter `DATABASE_URL=postgresql://postgres:postgres@localhost:5432/archiviste` (sync URL pour psycopg2) au step seed. (3) Exporter `LLM_PROVIDER=mistral` + `LLM_MODEL=mistral-small-latest` + `LLM_API_KEY=ci-placeholder` au step start workers (idem ci.yml:142-145). (4) Valider en local : `act -j offline` ou test e2e workflow manuel avant merge. |
| `eval/ragas_runner.py:108-112,167-172` + `eval/seed_test_corpus.py:32-36` | **HIGH** | Tautologie HIGH-3 déplacée mais persistante | Le fix HIGH-3 change `compute_keyword_overlap(answer, keywords)` en `compute_keyword_overlap(chunk_corpus, keywords)` (ragas_runner.py:168-169). Mais `seed_test_corpus.py:33` injecte précisément `chunk_text = " ".join(keywords)` dans la DB pour chaque `expected_context`. Donc en CI offline : chunks retrieved = chunks dont `text == " ".join(expected_answer_keywords)` → `compute_keyword_overlap` retourne True systématiquement par construction du seed. **Le métrique a juste été déplacé du tautological self-match (answer→keywords) au seed-circular self-match (seed-injected chunk text→keywords).** Le `keyword_overlap_rate` mesure : "le seed a-t-il bien inséré les keywords dans les chunks ET ces chunks ont-ils été retrieved", PAS la qualité du retrieval réel d'un corpus production. De plus, l'embedding seed (`ZERO_EMBEDDING` = 1024 zéros) rend toutes les distances cosinus égales (NaN ou 0) → ordre top-k arbitraire (probable tie-break par `c.id ASC`), pas un signal sémantique. Gate B offline (AC-11 phrase 2 (b)) reste sans valeur informationnelle. | Option A (recommandée) : renommer le métrique `seed_alignment_rate` et **expliciter en spec/README** qu'il est un property check de tuyauterie (`seed→retrieve→runner`), pas une métrique de qualité ; documenter que la vraie gate qualité offline est `context_recall_structural`. Option B : faire diverger le chunk text du seed et des keywords (ex : `chunk_text = entry["question"] + " " + entry["id"]` au lieu de `" ".join(keywords)`) pour que `keyword_overlap_rate` mesure réellement "la question récupère les chunks qui contiennent les keywords" — mais alors le rate sera 0 stable (encore inutile sauf à enrichir le seed avec du vrai contenu). Option C : ouvrir un ticket aval EVAL-002 "real corpus seed for CI" et documenter dans `docs/blockers.md` que la gate B offline est aujourd'hui une *fumée*. |
| `eval/tests/test_runner_cli.py:136-188` | **MED** | Test property AC-16 false-confidence | `test_secrets_redaction_property_100_runs` construit 100 `RunFile` à la main sans jamais injecter le sentinel `test-llm-token-xyz9999-prop` dans aucun champ de la `RunFile` (id/question/answer/citations sont tous des constantes safe `"answer {i}"`, etc). Le test asserte que le sentinel n'apparaît pas dans `run.json` — c'est vrai par construction du test, indépendamment de `_redact_raw`. Si on commentait `raw = _redact_raw(raw, secrets)` (ligne 117 de run_writer.py), le test continuerait à passer. Le property test ne couvre donc PAS la redaction effective. Test `test_secrets_not_leaked_in_run_file` (line 104-133) souffre du même biais : `httpserver` répond `{"chunks": [{"source_path": "intro_p01", "text": "some text"}]}`, aucun secret n'y apparaît. | Injecter le sentinel dans un champ réellement sérialisé : (a) ajouter `entry.answer = f"answer with {sentinel_llm_token} embedded"` avant `write_run` ; (b) asserter que `run.json` contient `[REDACTED]` (positive assertion) ET que le sentinel n'apparaît plus. Ou (c) mocker `httpserver` pour retourner un `chunks[0].text` contenant le sentinel, et asserter redaction post-write. Sans cela, AC-16 n'est pas testé. |

### Verdict argumenté

**REQUEST_CHANGES** : 2 HIGH bloquants subsistent (CI workflow inopérant + tautologie HIGH-3 déplacée), 1 MED (test redaction false-confidence). Les fixes HIGH-1, HIGH-2, MED-5, MED-6 sont **réels** (vérifiés en code + tests pass) ; HIGH-4 a un fix code correct mais une couverture test faible ; HIGH-3 a un fix code cosmétique. Le bug workflow CI est NOUVEAU (introduit par be85637 quand `seed_test_corpus.py` est passé de stub no-op à insert psycopg2 réel — mais sans wirer les pré-requis schema + env vars). C'est un cas typique d'introduction de fonctionnalité sans test end-to-end du chemin happy-path en CI.

### Recommandations cleanup pass 2

1. **Fix HIGH pass2 #1** : appliquer migrations + exporter `DATABASE_URL` au seed + exporter `LLM_PROVIDER`/`LLM_API_KEY=ci-placeholder` aux workers. Tester via `gh workflow run` sur une PR de cleanup.
2. **Fix HIGH pass2 #2** : renommer `keyword_overlap_rate` offline ou diverger seed des keywords + ouvrir ticket EVAL-002 pour seed réaliste.
3. **Fix MED pass2 #3** : injecter sentinel dans `entry.answer` du test property et asserter `[REDACTED]` en sortie.

Une fois ces 3 points adressés, verdict basculera vers APPROVE.
