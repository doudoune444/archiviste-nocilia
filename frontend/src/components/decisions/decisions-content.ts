/**
 * Décisions & pistes d'amélioration — editorial content module (issue #351).
 *
 * Static, versioned prose, deliberately separated from the layout so the wording
 * can be edited without touching the rendering component. Text is taken verbatim
 * from the validated mockup (`v6/v03-conv-botright.html`).
 *
 * Rich paragraphs are modelled as segment arrays rather than HTML strings so the
 * layout never has to inject raw markup (no `dangerouslySetInnerHTML`); inline
 * `code` spans are an explicit segment kind.
 */

export interface TextSegment {
  kind: "text";
  text: string;
}

export interface CodeSegment {
  kind: "code";
  text: string;
}

export type ProseSegment = TextSegment | CodeSegment;

export interface Decision {
  title: string;
  kicker: string;
  state: readonly ProseSegment[];
  improvement: readonly ProseSegment[];
}

export const DECISIONS_TITLE = "Décisions & pistes d'amélioration";

export const DECISIONS_SUBTITLE =
  "Les décisions sont adaptées à mon besoin et à mes contraintes ; de meilleures solutions restent envisageables.";

export const IMPROVEMENT_LABEL = "Piste d'amélioration";

function text(value: string): TextSegment {
  return { kind: "text", text: value };
}

function code(value: string): CodeSegment {
  return { kind: "code", text: value };
}

export const DECISIONS: readonly Decision[] = [
  {
    title: "Deux appels LLM en série",
    kicker: "classification + génération",
    state: [
      text("Chaque requête passe par deux appels au même modèle, "),
      code("mistral-small-latest"),
      text(
        ". D'abord une classification d'intention qui écarte le hors-sujet sans lancer de recherche inutile, puis la génération de la réponse. Deux étapes séparées, plus lisibles et plus faciles à déboguer."
      ),
    ],
    improvement: [
      text(
        "Aiguiller selon la difficulté : garder ce petit modèle pour les questions simples, n'escalader vers un modèle plus puissant que les questions complexes — de meilleures réponses sans payer le gros modèle partout. Et reformuler la question avant la recherche pour récupérer des passages plus pertinents."
      ),
    ],
  },
  {
    title: "Récupération — pgvector, top-5",
    kicker: "PostgreSQL + HNSW · cosinus",
    state: [
      text(
        "La recherche vit dans PostgreSQL via l'extension pgvector : pour chaque question, on garde les "
      ),
      code("5"),
      text(
        " passages les plus proches par le sens (HNSW, cosinus). Un seul datastore à gérer plutôt qu'une base vectorielle séparée, et des temps de réponse courts."
      ),
    ],
    improvement: [
      text(
        "Cette recherche par le sens peut rater un passage pertinent formulé avec d'autres mots. Lui ajouter une recherche par mots-clés, puis reclasser les résultats, permettrait de ne plus dépendre de la formulation exacte de la question."
      ),
    ],
  },
  {
    title: "Représenter le sens des passages",
    kicker: "mistral-embed · 1024 dimensions",
    state: [
      text(
        "Pour comparer des sens plutôt que des mots, chaque passage est transformé en une suite de nombres (un « vecteur ») qui capture son sens. C'est le modèle "
      ),
      code("mistral-embed"),
      text(
        " qui s'en charge, avec la même clé d'API que le modèle de réponse — rien de plus à héberger."
      ),
    ],
    improvement: [
      code("mistral-embed"),
      text(
        " est généraliste. Il saisit moins bien le vocabulaire propre à l'univers du projet, ce qui peut faire passer un bon passage à côté. Un modèle plus performant — ou adapté au domaine — rapprocherait mieux les sens et améliorerait directement la pertinence des réponses."
      ),
    ],
  },
  {
    title: "Découper les documents",
    kicker: "512 tokens · recouvrement 64",
    state: [
      text("En amont, les documents sont d'abord découpés en blocs d'environ "),
      code("512"),
      text(" unités de texte qui se chevauchent un peu ("),
      code("64"),
      text("), pour ne pas couper une idée en deux. Simple et robuste."),
    ],
    improvement: [
      text(
        "Cette taille fixe reste arbitraire. Elle peut séparer deux phrases liées ou réunir des idées sans rapport, ce qui brouille la recherche. Un découpage qui suit la structure du texte (titres, paragraphes) donnerait des blocs plus cohérents — donc des passages plus pertinents."
      ),
    ],
  },
  {
    title: "Mesurer la qualité des réponses",
    kicker: "4 métriques Ragas · 46 Q/R",
    state: [
      text(
        "La qualité n'est pas jugée « au feeling » : un modèle-juge note automatiquement les réponses sur un jeu de "
      ),
      code("46"),
      text(
        " questions de référence, selon 4 critères (fidélité aux sources, pertinence, précision et couverture du contexte). Chiffré et reproductible."
      ),
    ],
    improvement: [
      code("46"),
      text(
        " questions, c'est encore peu. Le jeu couvre mal les cas rares, et un modèle-juge n'est pas infaillible. L'élargir et le diversifier — avec quelques vérifications humaines — rendrait la mesure plus fiable et révélerait des angles morts actuels."
      ),
    ],
  },
];
