"""Chunking structure et indexation vectorielle ChromaDB (FR, AR, bilingue)."""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

from moteur.extraction_gemini import _charger_env

MODELE_EMBEDDING_DEFAUT = 'BAAI/bge-m3'
COLLECTION_NOM = 'factures_rag'

_EMBEDDER_LOCK = threading.Lock()
_EMBEDDER: Any = None
_EMBEDDER_NAME: str | None = None


def chroma_dir_par_defaut() -> Path:
    racine_web = Path(__file__).resolve().parent.parent
    data_dir = Path(os.environ.get('DOCUMENTAI_DATA_DIR', str(racine_web / 'data'))).resolve()
    return data_dir / 'chroma_db'


def modele_embedding_nom() -> str:
    _charger_env()
    return os.environ.get('DOCUMENTAI_EMBEDDING_MODEL', MODELE_EMBEDDING_DEFAUT).strip() or MODELE_EMBEDDING_DEFAUT


def _charger_embedder(*, local_files_only: bool = False):
    global _EMBEDDER, _EMBEDDER_NAME
    nom = modele_embedding_nom()
    with _EMBEDDER_LOCK:
        if _EMBEDDER is not None and _EMBEDDER_NAME == nom:
            return _EMBEDDER
        # Reutilise le singleton deja charge dans rag_hybride s'il utilise le meme modele.
        try:
            import rag_hybride

            if rag_hybride._EMBEDDER is not None and rag_hybride._EMBEDDER_NAME == nom:
                _EMBEDDER = rag_hybride._EMBEDDER
                _EMBEDDER_NAME = nom
                return _EMBEDDER
        except Exception:
            pass

        from sentence_transformers import SentenceTransformer

        kwargs: dict[str, Any] = {'device': 'cpu'}
        if local_files_only:
            kwargs['local_files_only'] = True
        model = SentenceTransformer(nom, **kwargs)
        _EMBEDDER = model
        _EMBEDDER_NAME = nom
        return model


def encoder_textes(textes: list[str], *, embedder=None, local_files_only: bool = False) -> list[list[float]]:
    if not textes:
        return []
    model = embedder if embedder is not None else _charger_embedder(local_files_only=local_files_only)
    vecteurs = model.encode(
        textes,
        normalize_embeddings=True,
        show_progress_bar=False,
        convert_to_numpy=True,
    )
    return vecteurs.tolist()


def _fmt(valeur: Any, defaut: str = 'Non renseigné') -> str:
    if valeur is None:
        return defaut
    texte = str(valeur).strip()
    return texte if texte else defaut


def _float_ou_none(valeur: Any) -> float | None:
    if valeur is None or isinstance(valeur, bool):
        return None
    try:
        return float(str(valeur).replace(',', '.').strip())
    except (TypeError, ValueError):
        return None


def chunker_facture(
    donnees: dict,
    *,
    doc_id: str | None = None,
    source_fichier: str | None = None,
) -> list[dict]:
    """Construit les chunks structures (1 en-tete + N articles) avec metadonnees."""
    identifiant = str(doc_id or donnees.get('document_id') or '').strip()
    if not identifiant:
        raise ValueError('doc_id requis pour le chunking vectoriel.')

    fournisseur = _fmt(donnees.get('fournisseur'), '')
    client = _fmt(donnees.get('client'), '')
    ice_f = _fmt(donnees.get('ice_fournisseur'), '')
    ice_c = _fmt(donnees.get('ice_client'), '')
    numero = _fmt(donnees.get('numero_document') or donnees.get('numero_facture'), '')
    date_f = _fmt(donnees.get('date_facture') or donnees.get('date'), '')
    devise = _fmt(donnees.get('devise'), '')
    type_doc = _fmt(donnees.get('type_document'), 'facture')
    fichier = _fmt(source_fichier or donnees.get('fichier_source'), '')
    total_ht = donnees.get('total_ht')
    tva = donnees.get('montant_tva')
    total_ttc = donnees.get('total_ttc')
    total_ttc_num = _float_ou_none(total_ttc)

    meta_commune = {
        'doc_id': identifiant,
        'fournisseur': fournisseur,
        'date_facture': date_f,
        'total_ttc': total_ttc_num if total_ttc_num is not None else 0.0,
        'numero_document': numero,
        'devise': devise,
        'source_fichier': fichier,
    }

    resume_parties = [
        f"Document {identifiant} ({type_doc}) n° {numero or identifiant}.",
        f"Fournisseur / المورد : {fournisseur or 'Non renseigné'}" + (f" (ICE : {ice_f})" if ice_f else '') + '.',
        f"Client / الزبون : {client or 'Non renseigné'}" + (f" (ICE : {ice_c})" if ice_c else '') + '.',
        f"Date de facture / تاريخ الفاتورة : {date_f or 'Non renseignée'}.",
    ]
    montants = []
    if total_ht is not None:
        montants.append(f"Total HT : {total_ht} {devise}".strip())
    if tva is not None:
        montants.append(f"TVA : {tva} {devise}".strip())
    if total_ttc is not None:
        montants.append(f"Total TTC : {total_ttc} {devise}".strip())
    if montants:
        resume_parties.append("Montants / المبالغ : " + ' | '.join(montants) + '.')
    if fichier:
        resume_parties.append(f"Fichier source : {fichier}.")

    chunks = [{
        'id': f'{identifiant}::entete',
        'texte': ' '.join(resume_parties),
        'metadonnees': {**meta_commune, 'type_chunk': 'entete'},
    }]

    for idx, ligne in enumerate(donnees.get('lignes') or [], 1):
        if not isinstance(ligne, dict):
            continue
        designation = _fmt(ligne.get('designation'), '')
        unite = _fmt(ligne.get('unite'), '')
        qte = ligne.get('quantite')
        pu = ligne.get('prix_unitaire_ht') if ligne.get('prix_unitaire_ht') is not None else ligne.get('prix_unitaire')
        mt_ht = ligne.get('montant_ht') if ligne.get('montant_ht') is not None else ligne.get('montant')
        mt_ttc = ligne.get('montant_ttc')
        taux_tva = ligne.get('taux_tva')
        page = ligne.get('page')

        morceaux = [
            f"Article {idx} — Désignation / البيان : {designation or 'Article sans libellé'}.",
        ]
        if qte is not None:
            morceaux.append(f"Quantité / الكمية : {qte}" + (f" {unite}" if unite else '') + '.')
        if pu is not None:
            morceaux.append(f"Prix unitaire : {pu} {devise}".strip() + '.')
        if mt_ht is not None:
            morceaux.append(f"Montant HT : {mt_ht} {devise}".strip() + '.')
        if mt_ttc is not None:
            morceaux.append(f"Montant TTC : {mt_ttc} {devise}".strip() + '.')
        if taux_tva is not None:
            morceaux.append(f"TVA : {taux_tva} %.")
        if page is not None:
            morceaux.append(f"Page : {page}.")
        morceaux.append(
            f"Contexte facture {numero or identifiant} ({identifiant}) — "
            f"Fournisseur / المورد : {fournisseur or 'Non renseigné'}, Date : {date_f or 'Non renseignée'}."
        )

        chunks.append({
            'id': f'{identifiant}::article::{idx}',
            'texte': ' '.join(morceaux),
            'metadonnees': {**meta_commune, 'type_chunk': 'article'},
        })
    return chunks


def _collection_chroma(chroma_dir: Path | str | None = None):
    import chromadb

    dossier = Path(chroma_dir) if chroma_dir is not None else chroma_dir_par_defaut()
    dossier.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(dossier))
    return client.get_or_create_collection(
        name=COLLECTION_NOM,
        metadata={'hnsw:space': 'cosine'},
    )


def indexer_facture(
    donnees: dict,
    *,
    doc_id: str | None = None,
    source_fichier: str | None = None,
    chroma_dir: Path | str | None = None,
    embedder=None,
    local_files_only: bool = False,
) -> dict:
    """Genere les chunks d'une facture et les met a jour dans ChromaDB."""
    chunks = chunker_facture(donnees, doc_id=doc_id, source_fichier=source_fichier)
    identifiant = chunks[0]['metadonnees']['doc_id']
    collection = _collection_chroma(chroma_dir)
    collection.delete(where={'doc_id': identifiant})
    embeddings = encoder_textes(
        [c['texte'] for c in chunks],
        embedder=embedder,
        local_files_only=local_files_only,
    )
    collection.upsert(
        ids=[c['id'] for c in chunks],
        documents=[c['texte'] for c in chunks],
        metadatas=[c['metadonnees'] for c in chunks],
        embeddings=embeddings,
    )
    return {'doc_id': identifiant, 'nombre_chunks': len(chunks)}


def indexer_lot(
    factures: list[dict],
    *,
    chroma_dir: Path | str | None = None,
    embedder=None,
    local_files_only: bool = False,
) -> dict:
    """Indexe un lot complet de factures dans ChromaDB (par batch)."""
    tous_chunks: list[dict] = []
    doc_ids: list[str] = []
    for f in factures:
        chunks = chunker_facture(f)
        tous_chunks.extend(chunks)
        doc_ids.append(chunks[0]['metadonnees']['doc_id'])

    collection = _collection_chroma(chroma_dir)
    if not tous_chunks:
        return {'nombre_factures': 0, 'nombre_chunks': 0, 'modele': modele_embedding_nom()}

    for did in set(doc_ids):
        collection.delete(where={'doc_id': did})

    embeddings = encoder_textes(
        [c['texte'] for c in tous_chunks],
        embedder=embedder,
        local_files_only=local_files_only,
    )
    collection.upsert(
        ids=[c['id'] for c in tous_chunks],
        documents=[c['texte'] for c in tous_chunks],
        metadatas=[c['metadonnees'] for c in tous_chunks],
        embeddings=embeddings,
    )
    return {
        'nombre_factures': len(set(doc_ids)),
        'nombre_chunks': len(tous_chunks),
        'modele': modele_embedding_nom(),
    }


def rechercher_similaires(
    requete: str,
    *,
    top_k: int = 8,
    where: dict | None = None,
    chroma_dir: Path | str | None = None,
    embedder=None,
    local_files_only: bool = False,
) -> list[dict]:
    """Recherche semantique dans ChromaDB sur les chunks de factures et d'articles."""
    dossier = Path(chroma_dir) if chroma_dir is not None else chroma_dir_par_defaut()
    if not dossier.exists():
        return []
    collection = _collection_chroma(dossier)
    total = collection.count()
    if total == 0:
        return []
    q_emb = encoder_textes([requete], embedder=embedder, local_files_only=local_files_only)
    kwargs: dict[str, Any] = {
        'query_embeddings': q_emb,
        'n_results': min(top_k, total),
        'include': ['documents', 'metadatas', 'distances'],
    }
    if where:
        kwargs['where'] = where
    res = collection.query(**kwargs)
    ids = (res.get('ids') or [[]])[0]
    docs = (res.get('documents') or [[]])[0]
    metas = (res.get('metadatas') or [[]])[0]
    dists = (res.get('distances') or [[]])[0]
    resultats = []
    for cid, doc, meta, dist in zip(ids, docs, metas, dists):
        resultats.append({
            'id': cid,
            'texte': doc,
            'metadonnees': meta,
            'score': round(1.0 - float(dist), 4),
        })
    return resultats


def statistiques_chroma(chroma_dir: Path | str | None = None) -> dict:
    dossier = Path(chroma_dir) if chroma_dir is not None else chroma_dir_par_defaut()
    if not dossier.exists():
        return {'chemin': str(dossier), 'nombre_chunks': 0, 'modele': modele_embedding_nom()}
    collection = _collection_chroma(dossier)
    return {
        'chemin': str(dossier),
        'nombre_chunks': int(collection.count()),
        'modele': modele_embedding_nom(),
    }
