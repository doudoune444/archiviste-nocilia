---
description: Post-merge cleanup — vérifie PR mergée, prune worktree, délègue delete branch à /clean_gone
argument-hint: <ID>
---

L'utilisateur veut nettoyer après merge du ticket `$ARGUMENTS`.

`$ARGUMENTS` doit matcher `^[A-Z]+-[0-9]+$`. Sinon abort.

## Étapes

1. **Détecte branche feature** :
   - Préférence: `git branch --list "feat/$ARGUMENTS-*"` → première match
   - Si vide: tente `feat/$ARGUMENTS` puis abort si rien
   - Stocke comme `$BRANCH`

2. **Vérifie PR mergée**:
   ```
   gh pr list --head "$BRANCH" --state merged --json number,mergeCommit
   ```
   - Si vide: affiche `PR pour $BRANCH non mergée. Abort cleanup.` et abort
   - Sinon stocke PR number

3. **Refresh refs**:
   ```
   git fetch --prune origin
   ```

4. **Remove worktree si présent**:
   ```
   if git worktree list --porcelain | grep -q ".worktrees/$ARGUMENTS"; then
     git worktree remove ".worktrees/$ARGUMENTS"
   fi
   ```

5. **Affiche état + délégation**:
   ```
   Cleanup $ARGUMENTS:
     - PR #<num> mergée ✓
     - Worktree .worktrees/$ARGUMENTS supprimé (si existait)
     - Refs remote pruned

   Pour purger la branche locale $BRANCH (et autres branches gone):
     /clean_gone
   ```

## Garde-fous

- Ne **jamais** supprimer la branche locale toi-même — délègue à `/clean_gone` (plugin commit-commands).
- Ne **jamais** invoquer `git checkout/switch` (banni).
- Si la branche courante EST `$BRANCH` ET tu n'es pas dans un worktree (i.e. dans le repo principal), abort avec: `Tu es sur $BRANCH dans le repo principal. /clean_gone gérera le delete une fois sur main.`
- Worktree-aware: si tu es DANS `.worktrees/$ARGUMENTS/`, abort avec: `Quitte le worktree avant cleanup. Va dans le repo principal puis relance /cleanup $ARGUMENTS.`
