# Spec icônes — L'Archiviste de Nocilia

Décisions figées depuis `selecteur-icones.html`. Les icônes retenues sont des
**emoji / glyphes** (couleur native, multicolore) posés sur une tuile de fond `bg`.
La couleur du fond compte ; l'emoji garde ses couleurs propres (pas de teinte `ink`).

| Usage | Emoji | Fond (`bg`) |
|---|---|---|
| **Archiviste** — marque sidebar + avatar assistant | 🪶 Plume | `#5b4baa` (violet vif) |
| **Lacunes** — mode `lore_gap` | 🔖 Signet | `#4a2a30` (bordeaux) |
| **États & Métriques** — usage / coût / stats | 📊 Métrique | `#1c1b22` (encre) |

## Statut

- **Archiviste** — appliqué dans `violet-editorial/chat-variantes/chat-1-filet.html`
  (`.brand-mono` + `.role-avatar`, emoji 🪶 sur fond `--icon-bg: #5b4baa`).
- **Lacunes** + **États & Métriques** — pas encore d'élément UI dans la maquette
  chat. À placer quand les écrans correspondants seront conçus (réponse mode
  `lore_gap`, panneau usage/métriques). Fonds figés ci-dessus pour le PRD UI.

## Note d'implémentation

Emoji = rendu dépendant de l'OS/navigateur (Apple, Segoe, Noto diffèrent). Si un
rendu cohérent cross-plateforme devient nécessaire, basculer vers un set SVG
(les variantes trait existaient dans le sélecteur) sera à arbitrer dans le PRD.
