---
description: Orchestrateur séquentiel spec→plan→impl→review→ship. Détecte phase via artifacts, affiche prochaine commande à lancer.
argument-hint: <ID>
---

L'utilisateur veut avancer le ticket `$ARGUMENTS` dans le cycle.

`$ARGUMENTS` doit matcher `^[A-Z]+-[0-9]+$`. Sinon abort.

## Détection de phase

Lis l'état du file-system (artifacts = state machine). Détermine la phase courante en vérifiant dans cet ordre.

**Convention Status** : section `## Status` en fin de spec, ligne suivante = `draft` ou `ready` (pas `Status: ready` inline). Lecture : `tail -5 specs/acceptance/<ID>.md | grep -E '^(ready|draft)$'`.

1. **Spec absente**: `specs/acceptance/<ID>.md` n'existe pas
   → Affiche: `Phase: SPEC. Run: /spec <ID> "<brief>"`

2. **Spec en draft**: fichier existe, status = `draft`
   → Affiche: `Phase: SPEC (draft). Continue: /spec <ID> pour itérer, ou édite la section ## Status pour passer à ready`

3. **Spec ready, plan absent**: status = `ready` ET `specs/plans/<ID>.md` absent
   → Affiche: `Phase: PLAN. Run: /plan <ID>`

4. **Plan présent, pas d'impl**: plan existe ET `git log main..HEAD --oneline -- gateway/ workers/ infra/ migrations/` est vide
   → Affiche: `Phase: IMPL. Run: /impl <ID>`. Vérifie au passage qu'on est sur une branche feature (ni main/master/develop) — si main, dis: `Crée d'abord une branche feature ou un worktree: git worktree add -b feat/<ID>-slug .worktrees/<ID>`.

5. **Impl committé, review absente**: commits feat/fix présents ET `specs/reviews/<ID>.md` absent
   → Affiche: `Phase: REVIEW. Run: /review`

6. **Review REQUEST_CHANGES**: review existe avec verdict `REQUEST_CHANGES` ou `BLOCK`
   → Affiche: `Phase: IMPL (fix review findings). Run: /impl <ID>` avec extrait des findings HIGH

7. **Review APPROVE, pas de PR**: verdict `APPROVE` ET aucune PR (`gh pr list --search "<ID>" --state all --json number,state` vide)
   → Affiche: `Phase: SHIP. Run: /ship <ID>`

8. **PR ouverte**: PR existe avec state `OPEN`
   → Affiche: `Phase: PR ouverte (#<num>). Attendre merge, puis /cleanup <ID>`

9. **PR mergée**: PR existe avec state `MERGED`
   → Affiche: `Phase: MERGÉE (#<num>). Run: /cleanup <ID>`

10. **PR fermée sans merge**: PR existe avec state `CLOSED` ET pas de merge
   → Affiche: `Phase: PR #<num> fermée sans merge. Décide: rouvrir, ou abandonner et /cleanup <ID>`

11. **Tout fait**: rien d'autre détecté (rare)
   → Affiche: `Cycle <ID> terminé.`

## Sortie

Format de sortie unique pour l'utilisateur:

```
Cycle <ID> — Phase: <NOM>
État:
  - Spec: <absent|draft|ready>
  - Plan: <absent|présent>
  - Impl: <X commits>
  - Review: <absent|REQUEST_CHANGES|APPROVE>
  - PR: <#num|absent>

Prochaine action: <commande à taper>
```

## Garde-fous

- Ne **jamais** auto-invoquer la commande suivante. Affiche-la, attends que l'humain la lance.
- Ne **jamais** modifier d'artifact (`specs/**`) — lecture seule.
- Si artifact absent où il devrait exister, indique-le mais ne le crée pas.
- Si `git branch --show-current` retourne `main`/`master`/`develop` ET phase ≥ IMPL, ABORT avec message: `Tu es sur <branch>. Crée un worktree feature avant d'implémenter.`
