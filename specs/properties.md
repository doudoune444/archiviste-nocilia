# Invariants property-based

Chaque invariant a un ID. Les property tests le référencent dans un commentaire.

| ID | Invariant | Test | Outil |
|---|---|---|---|
| INV-1 | Le chunker conserve le nombre total de caractères (somme des longueurs de chunks == longueur d'origine, modulo overlap) | `workers/tests/test_chunker_properties.py` | hypothesis |
| INV-2 | Les dimensions d'embedding sont constantes sur tous les documents (ni troncature ni padding) | `workers/tests/test_embedder_properties.py` | hypothesis |
| INV-3 | Le top-k du retrieval est déterministe pour une même query + état d'index | `workers/tests/test_retrieval_properties.py` | hypothesis |
| INV-4 | Le Markdown de conversation round-trip : `parse(serialize(c)) == c` pour toute conversation `c` | `workers/tests/test_conversation_format.py` | hypothesis |
| INV-5 | Le rate limiter ne laisse jamais passer plus de `quota` requêtes par `window` pour un même utilisateur | `gateway/tests/rate_limit_properties.rs` | proptest |
| INV-6 | Auth : toute requête sans JWT valide retourne 401, jamais 200 avec un tier dégradé | `gateway/tests/auth_properties.rs` | proptest |
| INV-7 | `conversation_id` d'un ticket référence une ligne de conversation existante (FK enforcée en DB) | `workers/tests/test_ticket_creation.py` | hypothesis |
| INV-8 | Le cost tracker : `total_cost == somme(per_call_cost)` pour toute séquence d'appels | `workers/tests/test_cost_tracker_properties.py` | hypothesis |
| INV-9 | Tout score retourné par `/v1/retrieve` est borné : `score ∈ [0, 1]` (cosine similarity normalisée) | `workers/tests/test_retrieve_properties.py` | hypothesis |
| INV-10 | La réponse `/v1/retrieve` respecte `len(chunks) ≤ top_k` et tous les `chunk_id` sont uniques dans la réponse | `workers/tests/test_retrieve_properties.py` | hypothesis |

## Ajouter un invariant

1. Éditer ce fichier. Ajouter une ligne avec un nouvel ID.
2. Ajouter un test référençant `INV-X` dans une docstring/commentaire.
3. Commit les deux dans la même PR.
