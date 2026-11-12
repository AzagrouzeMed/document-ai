# RAG documentaire

État initial : partiel. Les quatre questions prédéfinies interrogent les champs SQLite. La recherche par mots affiche des extraits FTS5. Ni cette recherche ni ces quatre questions ne généraient de réponse Gemini.

Parcours ajouté : formulaire dans le détail → POST /api/factures/{id}/question-document (jeton local requis) → recherche FTS5 limitée à facture_id → cinq extraits maximum → client moteur/extraction_gemini.py → réponse JSON et identifiants contrôlés → réponse et pages dans l’interface. Aucun enregistrement de question ni modification de validation.

Le mode question retire quelques mots courants français et recherche les autres en OR ; la recherche par mots reste en AND. Recherche lexicale : certaines reformulations et variantes arabes peuvent ne pas trouver le passage. Pas de base vectorielle ni de LangGraph.

Les pages_document, lectures FR/AR, triggers FTS5, contrainte anti-doublons et reprise des archives OCR étaient déjà présents ; ils sont conservés. Aucun OCR relancé. Une question ne déclenche pas la migration et ne réimporte pas les archives.

Gemini reçoit uniquement la question et les textes sélectionnés avec des identifiants E1…E5. Les pages affichées viennent du serveur. Une référence étrangère, une réponse interrompue ou un échec Gemini rendent la génération indisponible, avec les extraits conservés. Sans références, le serveur impose un message d’information manquante. Les consignes demandent d’ignorer les instructions OCR et les connaissances externes ; cela ne certifie pas la vérité d’une réponse du modèle.

## Démonstration dans une base séparée

Depuis la racine du dépôt, dans PowerShell :

```powershell
$env:DOCUMENTAI_DATA_DIR = Join-Path ([IO.Path]::GetTempPath()) ('documentai-rag-' + [guid]::NewGuid().ToString('N'))
.\.venv-documentai\Scripts\python.exe -B DocumentAI_Web/docs/demo_rag.py
.\.venv-documentai\Scripts\python.exe -B DocumentAI_Web/lancer_web.py --sans-navigateur
```

Ouvrir http://127.0.0.1:8000, Factures enregistrées, puis DEMO-RAG.
Dans « Poser une question sur le document », saisir « Quelles sont les conditions de paiement ? ».
La source attendue est la page 2, avec paiement par virement à 30 jours.
La génération réelle nécessite une configuration Gemini déjà fournie au terminal (GEMINI_API_KEY et GEMINI_MODEL). Ne pas mettre de clé dans ce document. Sans configuration, les extraits restent visibles et la génération est indiquée indisponible.

## Vérification

```powershell
.\.venv-documentai\Scripts\python.exe -B -m unittest DocumentAI_Web.tests.test_rag_document DocumentAI_Web.tests.test_questions_factures_web DocumentAI_Web.tests.test_recherche_document -v
```

Les tests utilisent SQLite temporaire, des textes fictifs et le transport Gemini simulé : paiement et page 2, absence OCR, absence résultat sans appel Gemini, référence inventée, panne HTTP 503 simulée, isolation de deux factures, information absente et non-modification de la base. Le transport réseau Gemini est interdit et sa configuration remplacée par des valeurs fictives.

Restent à vérifier avec Gemini réel : compatibilité du modèle configuré, qualité de formulation, comportement face aux instructions malveillantes dans l’OCR et affichage dans le navigateur. Aucun appel API réel exécuté pendant cette intervention.
