# ADR 0008 — Waiver LOC pour EVAL-001 (runner Ragas fondateur)

- Status: accepted
- Date: 2026-05-13
- Decider: humain (auteur du projet)

## Context

`.claude/rules/vertical-slice.md` impose `≤ 300 LOC diff (excluding migrations
and generated files). Beyond → split ticket.` Cette règle vise à garder les PR
review-friendly et à éviter les méga-changements feature-creep.

EVAL-001 (runner Ragas golden_qa + gates A/B + workflow CI) atteint :
- **989 LOC Python production** à la livraison initiale (c589087), puis **1190 LOC post-review-fixes**
  (review HIGH-1..HIGH-4, MED-5 adressés), répartis sur 9 modules :
  - `eval/loader.py` (77) — schéma Pydantic + parser JSONL
  - `eval/stub_llm.py` (29) — règle déterministe AC-4
  - `eval/metrics.py` (122) — keyword_overlap_rate, context_recall_structural, Ragas wrapper
  - `eval/gates.py` (131) — Gate A (live) + Gate B (live/offline, déterministe)
  - `eval/clients.py` (129) — clients httpx `/v1/retrieve` + `/v1/generate` (dead `_extra_headers` supprimé)
  - `eval/run_writer.py` (119) — schéma run + redaction secrets AC-16 (dead code supprimé, redaction câblée)
  - `eval/baseline_skip.py` (84) — détection merge-base AC-17 (anti multi-commit attack)
  - `eval/ragas_runner.py` (389) — orchestration CLI, 4 exit codes, logs structurés (main() décomposé ≤40L)
  - `eval/seed_test_corpus.py` (110) — seed DB CI (psycopg2, contextes alignés ci_smoke_qa.jsonl)
- +669 LOC tests (46 cas, AC-1..AC-17 + baseline schema + byte-identical + property-100-runs),
  168 LOC workflow YAML, 64 LOC README, 63 LOC pyproject, 8 LOC fixture CI sanitisée.

**Post-review note**: le reviewer a demandé un trim de 50-80 LOC (dead code + main() ≤40L).
Le dead code a été supprimé (`_redact_string`/`_redact_entry` morts → `_redact_raw` câblé,
`_extra_headers` supprimé, `if False else` retiré, `except Exception` pinné).
`main()` décomposé en `_run_all_entries`, `_resolve_ragas_metrics`, `_handle_auto_create_baseline`,
`_emit_summary_and_exit` — chaque fonction ≤40 lignes.
`seed_test_corpus.py` implémenté réellement (+110 LOC) — la réduction du trim a été compensée
par la correction du stub HIGH-3.

Le plan `specs/plans/EVAL-001.md` hypothèse 1 avait anticipé l'overrun et
documenté un split fallback `EVAL-001a` / `EVAL-001b` (offline-only / live-only).

## Decision

**Waiver explicite, ship EVAL-001 en un seul vertical slice.** Pas de split.

Le LOC plafond 300 est suspendu pour ce ticket précisément. La règle reste
active pour tous les autres tickets — ce waiver n'est pas un précédent
implicite.

## Rationale

1. **Cohésion fonctionnelle inséparable.** Le runner Ragas est un orchestrateur
   atomique : loader → clients → stub/live → métriques → gates → writer →
   workflow. Splitter en `EVAL-001a` (offline) / `EVAL-001b` (live + Gate A)
   force la duplication de `ragas_runner.py` CLI + `workflow.yml` + tests
   d'intégration, et le split de `metrics.py` (déterministes vs Ragas) crée une
   API instable redéfinie au deuxième ticket. Coût de split estimé : +150 LOC
   de glue, deux PR review serial, retard CI gate effective de 1 semaine.

2. **Modules individuellement clean.** Aucune fonction ne dépasse 40 lignes
   (rule `clean-code.md`). Aucun module n'a plus d'une responsabilité (SRP
   respecté). La taille agrégée vient du nombre de modules (8), pas de leur
   complexité unitaire. Un reviewer humain peut lire chaque module isolément
   en <5 min.

3. **Ticket infra fondateur, pas feature applicative.** EVAL-001 pose
   l'instrumentation qualité du RAG (boucle de mesure offline + gate
   no-regression). Les features applicatives à venir (GEN-003, RET-002, …)
   bénéficient directement de cette boucle. Couper la mesure en deux retarde
   la gate effective sans gain de review-ability.

4. **Précédent GEN-002.** GEN-002 a livré avec +15 LOC sur le cap 300, justifié
   en CHANGELOG par "fonctionnellement inséparables". EVAL-001 applique le même
   raisonnement à plus grande échelle, formalisé en ADR (vs note CHANGELOG)
   parce que l'écart est significativement plus large.

## Consequences

### Positives

- Une seule PR review au lieu de deux séquentielles.
- Workflow CI offline + workflow_dispatch live opérationnels dès le merge.
- Pas de glue inter-tickets jetable.

### Négatives

- Review humain plus long (estimation 30-45 min vs 15-20 min pour 300 LOC).
- Précédent psychologique : autres tickets pourraient invoquer cet ADR pour
  justifier overrun. **Mitigation** : ce waiver est ad-hoc, non-réutilisable
  sans nouvel ADR. Le commit message + CHANGELOG citent explicitement ADR-0008
  comme exception, pas comme règle générale.

### Risques résiduels

- Si reviewer détecte sur-ingénierie (ex : `gates.py` 131 LOC alors qu'on
  pourrait inliner), retour au split via `/review REQUEST_CHANGES`. Le plan
  step 12 split fallback reste activable post-review.

## Alternatives considérées

- **A. Split strict EVAL-001a/b.** Plan hypothèse 1. Rejeté pour les raisons
  rationale (1) et (3).
- **C. Trim agressif vers ~500 LOC.** Inliner gates dans runner, fusionner
  metrics et stub. Rejeté : la séparation par responsabilité est précisément
  ce qui rend chaque module lisible. Mutualiser réduit la testabilité unitaire.

## References

- `specs/plans/EVAL-001.md` hypothèse 1 (split fallback)
- `.claude/rules/vertical-slice.md` (rule originale)
- CHANGELOG GEN-002 note (précédent +15 LOC documenté)
