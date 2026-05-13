# ADR 0008 — Waiver LOC pour EVAL-001 (runner Ragas fondateur)

- Status: accepted
- Date: 2026-05-13
- Decider: humain (auteur du projet)

## Context

`.claude/rules/vertical-slice.md` impose `≤ 300 LOC diff (excluding migrations
and generated files). Beyond → split ticket.` Cette règle vise à garder les PR
review-friendly et à éviter les méga-changements feature-creep.

EVAL-001 (runner Ragas golden_qa + gates A/B + workflow CI) atteint :
- **989 LOC Python production** (vs budget 300, dépassement 3.3x), répartis
  sur 8 modules orthogonaux :
  - `eval/loader.py` (77) — schéma Pydantic + parser JSONL
  - `eval/stub_llm.py` (29) — règle déterministe AC-4
  - `eval/metrics.py` (122) — keyword_overlap_rate, context_recall_structural, Ragas wrapper
  - `eval/gates.py` (131) — Gate A (live) + Gate B (live/offline, déterministe)
  - `eval/clients.py` (131) — clients httpx `/v1/retrieve` + `/v1/generate`
  - `eval/run_writer.py` (131) — schéma run + redaction secrets AC-16
  - `eval/baseline_skip.py` (56) — détection `chore(eval): bump baseline` AC-17
  - `eval/ragas_runner.py` (+312 net) — orchestration CLI, 4 exit codes, logs structurés
- +555 LOC tests (37 cas, AC-1..AC-17), 166 LOC workflow YAML, 64 LOC README,
  62 LOC pyproject, 8 LOC fixture CI sanitisée.

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
