# ADR 0007 — Pillow pour la compression des images embarquées dans les Google Docs

- Status: accepted
- Date: 2026-05-12
- Decider: humain (auteur du projet)

## Context

ING-014 extrait les images embarquées (`inlineObjects`) des Google Docs vers un sidecar
`<doc_slug>.images/` versionné. Ces images peuvent être de grande dimension ou fort poids :
un PNG 4096×4096 en sortie native Drive peut facilement dépasser 10–20 MiB, rendant le
repo gonflé sans borne à chaque run. La spec ING-014 impose un pipeline de compression
deux étages (AC-3a/3b/3c) :

1. Étage 1 — resize Lanczos : si `max(width, height) > 2048 px`, redimensionner à 2048 px
   en préservant le ratio (idempotent, toujours appliqué).
2. Étage 2 — re-encode JPEG q=85 : si bytes post-resize > 5 MiB, ré-encoder en JPEG
   (perte de transparence acceptée — fond blanc). MIME final devient `image/jpeg`.
3. Étage 3 — cap dur : si bytes post-JPEG > 5 MiB (image bruit incompressible), placeholder
   `#oversized-<objectId>` + `images_failed++` + aucune écriture.

Pillow (PIL fork, MIT) est la bibliothèque Python de référence pour ces opérations
(resize LANCZOS, re-encode JPEG, parsing multi-format). Elle embarque des décodeurs C natifs
(libjpeg, libpng, zlib) : c'est du code FFI. Selon `.claude/rules/security.md` A06
(« New dep above 1k LOC or any FFI: requires ADR »), cet ajout requiert une décision
documentée.

## Decision

Accepter `Pillow>=10.4` comme dépendance de `scripts/pyproject.toml` exclusivement,
jamais de `workers/pyproject.toml` ni de `gateway/Cargo.toml`.

Mesures de sécurité appliquées (ING-014 spec Security L122) :
- `PIL.Image.MAX_IMAGE_PIXELS` configuré à `2048*2048*4 ≈ 16.7 M pixels` au module-load
  de `scripts/gdrive_export/images.py`. Toute image dont la taille décodée dépasse ce seuil
  avant resize lève `DecompressionBombError` (CVE Pillow historique) → catché et reclassé
  en placeholder AC-7 (`reason="undecodable"`).
- Aucun décodage de payload utilisateur final : les images proviennent exclusivement de
  `lh*.googleusercontent.com` (domaines contrôlés Google), sur des Docs écrits par
  l'auteur du projet (SA mono-auteur, périmètre mono-projet).
- Pillow n'est jamais exposé en chemin réseau entrant (CLI offline uniquement).

Scope d'usage : `scripts/` (outil CLI offline), jamais Cloud Run.

## Consequences

### Easier
- Pipeline compression deux étages (resize Lanczos + JPEG q=85) disponible en ~15 LOC
  sans réécrire de codec natif.
- Protection contre DecompressionBomb via `MAX_IMAGE_PIXELS` cap configurable au
  module-load.
- Dédup intra-doc via MD5 post-compression (AC-5) naturellement supporté.
- `pip-audit` côté `workers/` reste vierge ; blast radius CVE Pillow isolé à `scripts/`.

### Harder
- Un second venv à auditer : `pip-audit` doit couvrir `scripts/` séparément (déjà prévu
  par ADR-0006 — la CI matrix inclut `scripts/`).
- Pillow embarque du code C natif (libjpeg, libpng, zlib, libtiff) : la surface d'attaque
  lors du décodage image est réelle, mitigée par le cap `MAX_IMAGE_PIXELS` et le fait que
  la source est Google-controlled.
- Le comportement précis du re-save Pillow (strip EXIF, colorspace) est un effet de bord
  accepté en phase 1 (Non-goals spec ING-014 L36).

### Cost
- ~30 deps transitives de plus dans `scripts/uv.lock` (wheels binaires Pillow incluent
  libjpeg/libpng pré-compilés).
- ~50ms d'import supplémentaire au boot du script (acceptable, offline CLI).

## Alternatives considered

- **Pas de compression** — rejeté : le repo grossit sans borne à chaque sync (un PNG
  4096×4096 non compressé peut peser 20–50 MiB ; 50 gdocs × 3 images moyennes → jusqu'à
  7 Go de diff par run). Non viable long terme.
- **ImageMagick subprocess** (`convert`, `magick`) — rejeté : dépendance système externe
  non garantie sur toutes les plateformes (macOS `brew`, Linux `apt`, CI runner) ;
  injection shell si le chemin de fichier est dérivé de données externes ; difficile à
  tester sans ImageMagick installé ; version matrix à gérer. Pillow est pip-installable,
  hermétique dans le venv.
- **wand** (binding Python pour ImageMagick) — rejeté : même problème que ImageMagick
  subprocess + binding supplémentaire avec son propre historique CVE ; moins de traction
  communautaire que Pillow pour ce cas d'usage.
- **cairosvg / skia-python** — non applicables : les images embarquées Drive sont en
  format raster (PNG/JPEG/GIF/WebP), pas vectoriel.
- **Compression manuelle zlib sur PNG** — rejeté : non standard, ne couvre pas JPEG/GIF/
  WebP, nécessite de réimplémenter PNG chunk parser. Complexité injustifiée vs Pillow.

## Amendment trigger

Cet ADR doit être amendé si :
- `workers/` doit traiter des images (ex. ingestion d'images dans le pipeline RAG) →
  réévaluer l'isolation venv et la config `MAX_IMAGE_PIXELS` globale.
- Pillow publie un CVE CRITICAL nécessitant pin ou remplacement.
- Le re-encodage WebP/AVIF est requis (ticket futur ING-014-bis) → amender étage 2.

## References

- `specs/acceptance/ING-014.md` AC-3/3a/3b/3c/AC-7 — pipeline compression + placeholders.
- `specs/acceptance/ING-014.md` Security L122 — `MAX_IMAGE_PIXELS` cap + DecompressionBomb.
- `.claude/rules/security.md` A06 — règle « new dep above 1k LOC or any FFI requires ADR ».
- ADR-0006 — isolation `scripts/` venv (précédent).
- Pillow CVE history — https://github.com/python-pillow/Pillow/security/advisories
