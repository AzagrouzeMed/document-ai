# Contexte & Règles du Projet — DocumentAI (Factures FR / AR / Bilingue + RAG)

## 1. Objectif du Projet
Application web locale d'extraction et de recherche intelligente (RAG hybride) sur des factures et devis marocains/internationaux (Français, Arabe, Bilingue) au format PDF et JPG.
- **Contrainte stricte :** Aucun entraînement ou fine-tuning de modèle. Utilisation exclusive d'OCR, de VLM (Gemini API) avec sortie structurée JSON, et d'un moteur RAG hybride (SQLite + Vectoriel).

## 2. Architecture Cible & Environnement
- **OS & Shell :** Windows (PowerShell).
- **Environnement virtuel unique :** `.venv-documentai` (toujours exécuter les commandes Python et `pip` via `.\.venv-documentai\Scripts\python.exe`).
- **Dossier applicatif principal :** `DocumentAI_Web/`
  - `lancer_web.py` : point d'entrée de lancement du serveur.
  - `app.py` : backend API (FastAPI / routes web, upload, traitements, recherche RAG).
  - `moteur/` : logique métier (`extraction_gemini.py`, `validation_donnees.py`, stockage SQLite, indexation/recherche RAG).
  - `static/` & `templates/` : interface utilisateur web.
  - `data/` : stockage local des documents uploadés, JSON extraits, base SQLite et index vectoriel.
  - `.env` : contient `GEMINI_API_KEY` et `GEMINI_MODEL` (ne jamais exposer ni commiter).

## 3. Pipeline Technique (OCR + VLM + RAG)
1. **Pré-traitement :** Conversion PDF -> PNG (`pymupdf`) et normalisation images (`Pillow`).
2. **Extraction Structurée :** Appel Gemini API (`responseJsonSchema`) combinant image + lectures OCR, sans recalculer ni inventer les montants absents. Validation métier via `moteur/validation_donnees.py`.
3. **Stockage Hybride :**
   - **SQLite :** stockage exact des en-têtes (fournisseur, client, ICE, date, devise, total_ht, montant_tva, total_ttc) et lignes d'articles pour les requêtes exactes et agrégations.
   - **Recherche Vectorielle (RAG) :** embeddings multilingues (`BAAI/bge-m3` ou modèle multilingue léger compatible FR/AR) sur les textes/articles extraits pour la recherche sémantique avec citation des sources.

## 4. Règles pour l'Agent Antigravity
- Avant de créer un fichier, vérifier s'il existe déjà dans `DocumentAI_Web/moteur/`.
- Toujours charger `.env` au démarrage de l'application (`lancer_web.py` et `app.py`).
- Préserver la compatibilité stricte avec les langues **Français** et **Arabe** (encodage UTF-8 partout, y compris dans les réponses JSON et requêtes SQLite).
- Ne pas recréer de dossiers de benchmark, d'environnements virtuels secondaires ni de scripts `essai_*.py` à la racine.