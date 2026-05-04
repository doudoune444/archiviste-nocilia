# Specs — sources de vérité (humain-only)

Ce répertoire est le contrat entre l'humain (toi, propriétaire du projet) et tout agent LLM travaillant sur le code.

**Règle** : les agents peuvent **lire** les fichiers ici, peuvent **écrire** dans `plans/` et `reviews/`, mais ne **modifient jamais** `acceptance/`, `golden_qa.jsonl`, `properties.md` ni `openapi/` sans approbation humaine explicite via review d'une PR signée.

## Structure

| Chemin | Auteur | Rôle |
|---|---|---|
| `acceptance/<ID>.md` | humain (assisté par `spec-author` via `/spec`) | Critères d'acceptation du ticket `<ID>`. Le « quoi ». |
| `plans/<ID>.md` | agent `architect` | Plan d'implémentation dérivé de l'acceptance. Le « comment ». |
| `reviews/<ID>.md` | agent `reviewer` | Review adverse du diff. Verdict + findings. |
| `golden_qa.jsonl` | humain | Set Q/A de référence pour l'eval RAG (Ragas). |
| `properties.md` | humain | Invariants pour les property-based tests. |
| `openapi/gateway-to-workers.yml` | humain (avec assist agent) | Contrat REST. |
| `threat-model.md` | humain | Modèle de menaces STRIDE par composant. |

## Workflow par ticket

```
/spec <ID> "<brief>"  →   acceptance/<ID>.md   (spec-author, boucle Socratique, Status: draft → ready)
       ↓
/plan <ID>            →   plans/<ID>.md        (architect, validé humain)
       ↓
/impl <ID>            →   code + tests         (implementer)
       ↓
/review <ID>          →   reviews/<ID>.md      (reviewer, verdict)
       ↓
/eval [<ID>]          →   eval/runs/<ID>-*.json (eval-runner si chemin RAG)
       ↓
/ship <ID>            →   PR ouverte, gates vérifiés
       ↓
humain merge
```

## Convention d'ID

`<EPIC>-<NUM>` où EPIC ∈ {FOUND, ING, RET, GEN, EVAL, OBS, SEC, INFRA, DOC, OPS}.

Exemples : `FOUND-001`, `RET-005`, `EVAL-002`.
