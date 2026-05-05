---
description: Orchestrateur séquentiel spec→plan→impl→review→ship. Détecte phase via artifacts, affiche prochaine commande à lancer.
argument-hint: <ID>
---

L'utilisateur veut avancer le ticket `$ARGUMENTS` dans le cycle.

`$ARGUMENTS` doit matcher `^[A-Z]+-[0-9]+$`. Sinon abort.

## Détection de phase

Lis l'état du file-system (artifacts = state machine). Détermine la phase courante en vérifiant dans cet ordre:

1. **Spec absente**: `specs/acceptance/<ID>.md` n'existe pas
   → Affiche: `Phase: SPEC. Run: /spec <ID> "<brief>"`

2. **Spec en draft**: fichier existe, contient `Status: draft`
   → Affiche: `Phase: SPEC (draft). Continue: /spec <ID> pour itérer, ou marque Status: ready`

3. **Spec ready, plan absent**: `Status: ready` ET `specs/plans/<ID>.md` absent
   → Affiche: `Phase: PLAN. Run: /plan <ID>`

4. **Plan présent, pas d'impl**: plan existe ET `git log main..HEAD --oneline -- gateway/ workers/ infra/ migrations/` est vide
   → Affiche: `Phase: IMPL. Run: /impl <ID>`. Vérifie au passage qu'on est sur une branche feature (ni main/master/develop) — si main, dis: `Crée d'abord une branche feature ou un worktree: git worktree add -b feat/<ID>-slug .worktrees/<ID>`.

5. **Impl committé, review absente**: commits feat/fix présents ET `specs/reviews/<ID>.md` absent
   → Affiche: `Phase: REVIEW. Run: /review`

6. **Review REQUEST_CHANGES**: review existe avec verdict `REQUEST_CHANGES` ou `BLOCK`
   → Affiche: `Phase: IMPL (fix review findings). Run: /impl <ID>` avec extrait des findings HIGH

7. **Review APPROVE, pas de PR**: verdict `APPROVE` ET pas de PR ouverte (`gh pr list --head $(git branch --show-current) --json number` vide)
   → Affiche: `Phase: SHIP. Run: /ship <ID>`

8. **PR ouverte**: PR existe pour la branche courante
   → Affiche: `Phase: PR ouverte (#<num>). Attendre merge, puis /cleanup <ID>`

9. **Tout fait**: rien d'autre détecté
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
