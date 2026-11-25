"""Stockage relationnel SQLite dedie aux factures et lignes extraites (factures.db)."""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


def chemin_par_defaut() -> Path:
    racine_web = Path(__file__).resolve().parent.parent
    data_dir = Path(os.environ.get('DOCUMENTAI_DATA_DIR', str(racine_web / 'data'))).resolve()
    return data_dir / 'factures.db'


def ouvrir_connexion(chemin_base: Path | str | None = None, *, lecture_seule: bool = False) -> sqlite3.Connection:
    chemin = Path(chemin_base) if chemin_base is not None else chemin_par_defaut()
    if lecture_seule:
        connexion = sqlite3.connect(chemin.resolve().as_uri() + '?mode=ro', uri=True)
        connexion.execute('PRAGMA query_only = ON')
    else:
        chemin.parent.mkdir(parents=True, exist_ok=True)
        connexion = sqlite3.connect(chemin)
    connexion.row_factory = sqlite3.Row
    connexion.execute('PRAGMA foreign_keys = ON')
    return connexion


def initialiser_base(chemin_base: Path | str | None = None) -> Path:
    """Cree les tables factures et lignes_factures sans effacer de donnees existantes."""
    chemin = Path(chemin_base) if chemin_base is not None else chemin_par_defaut()
    with closing(ouvrir_connexion(chemin)) as connexion:
        with connexion:
            connexion.execute('BEGIN IMMEDIATE')
            connexion.execute("""
                CREATE TABLE IF NOT EXISTS factures (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    doc_id TEXT NOT NULL UNIQUE,
                    source_fichier TEXT,
                    type_document TEXT,
                    fournisseur TEXT,
                    ice_fournisseur TEXT,
                    client TEXT,
                    ice_client TEXT,
                    numero_document TEXT,
                    date_facture TEXT,
                    devise TEXT,
                    total_ht REAL,
                    montant_tva REAL,
                    total_ttc REAL,
                    statut TEXT,
                    json_complet TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            connexion.execute("""
                CREATE TABLE IF NOT EXISTS lignes_factures (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    facture_id INTEGER NOT NULL,
                    page INTEGER,
                    designation TEXT,
                    unite TEXT,
                    quantite REAL,
                    prix_unitaire_ht REAL,
                    montant_ht REAL,
                    taux_tva REAL,
                    montant_ttc REAL,
                    FOREIGN KEY (facture_id)
                        REFERENCES factures(id)
                        ON DELETE CASCADE
                )
            """)
            connexion.execute('CREATE INDEX IF NOT EXISTS idx_factures_fournisseur ON factures(fournisseur)')
            connexion.execute('CREATE INDEX IF NOT EXISTS idx_factures_date ON factures(date_facture)')
            connexion.execute('CREATE INDEX IF NOT EXISTS idx_factures_numero ON factures(numero_document)')
            connexion.execute('CREATE INDEX IF NOT EXISTS idx_lignes_facture_id ON lignes_factures(facture_id)')
    return chemin


def _texte_ou_none(valeur: Any) -> str | None:
    if valeur is None:
        return None
    texte = str(valeur).strip()
    return texte if texte else None


def _nombre_ou_none(valeur: Any) -> float | None:
    if valeur is None or isinstance(valeur, bool):
        return None
    if isinstance(valeur, str):
        valeur = valeur.strip().replace(',', '.')
        if not valeur:
            return None
    try:
        dec = Decimal(str(valeur))
    except (InvalidOperation, ValueError):
        return None
    if not dec.is_finite():
        return None
    return float(dec)


def _entier_ou_none(valeur: Any) -> int | None:
    if valeur is None or isinstance(valeur, bool):
        return None
    try:
        return int(valeur)
    except (TypeError, ValueError):
        return None


def enregistrer_facture(
    donnees: dict | str,
    *,
    source_fichier: str | None = None,
    doc_id: str | None = None,
    json_complet: str | None = None,
    chemin_base: Path | str | None = None,
) -> int:
    """Insere ou met a jour une facture et ses lignes sans doublon sur doc_id."""
    if isinstance(donnees, str):
        json_brut = json_complet or donnees
        doc = json.loads(donnees)
    else:
        doc = dict(donnees)
        json_brut = json_complet or json.dumps(doc, ensure_ascii=False)

    if not isinstance(doc, dict):
        raise ValueError('Le document doit etre un objet JSON.')

    identifiant = _texte_ou_none(doc_id or doc.get('document_id'))
    if not identifiant:
        raise ValueError('doc_id / document_id requis pour enregistrer la facture.')

    initialiser_base(chemin_base)
    now_iso = datetime.now(timezone.utc).isoformat()
    source = _texte_ou_none(source_fichier or doc.get('fichier_source'))
    date_f = _texte_ou_none(doc.get('date_facture') or doc.get('date'))
    numero = _texte_ou_none(doc.get('numero_document') or doc.get('numero_facture'))

    valeurs_facture = (
        identifiant,
        source,
        _texte_ou_none(doc.get('type_document')),
        _texte_ou_none(doc.get('fournisseur')),
        _texte_ou_none(doc.get('ice_fournisseur')),
        _texte_ou_none(doc.get('client')),
        _texte_ou_none(doc.get('ice_client')),
        numero,
        date_f,
        _texte_ou_none(doc.get('devise')),
        _nombre_ou_none(doc.get('total_ht')),
        _nombre_ou_none(doc.get('montant_tva')),
        _nombre_ou_none(doc.get('total_ttc')),
        _texte_ou_none(doc.get('statut')),
        json_brut,
        now_iso,
    )

    lignes = doc.get('lignes') or []
    if not isinstance(lignes, list):
        raise ValueError("'lignes' doit etre une liste.")

    with closing(ouvrir_connexion(chemin_base)) as connexion:
        with connexion:
            connexion.execute('BEGIN IMMEDIATE')
            existante = connexion.execute(
                'SELECT id FROM factures WHERE doc_id = ?', (identifiant,)
            ).fetchone()
            if existante is not None:
                facture_id = int(existante['id'])
                connexion.execute(
                    """
                    UPDATE factures SET
                        source_fichier = ?,
                        type_document = ?,
                        fournisseur = ?,
                        ice_fournisseur = ?,
                        client = ?,
                        ice_client = ?,
                        numero_document = ?,
                        date_facture = ?,
                        devise = ?,
                        total_ht = ?,
                        montant_tva = ?,
                        total_ttc = ?,
                        statut = ?,
                        json_complet = ?
                    WHERE id = ?
                    """,
                    (*valeurs_facture[1:15], facture_id),
                )
                connexion.execute('DELETE FROM lignes_factures WHERE facture_id = ?', (facture_id,))
            else:
                cur = connexion.execute(
                    """
                    INSERT INTO factures (
                        doc_id, source_fichier, type_document, fournisseur, ice_fournisseur,
                        client, ice_client, numero_document, date_facture, devise,
                        total_ht, montant_tva, total_ttc, statut, json_complet, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    valeurs_facture,
                )
                facture_id = int(cur.lastrowid)

            for ligne in lignes:
                if not isinstance(ligne, dict):
                    continue
                connexion.execute(
                    """
                    INSERT INTO lignes_factures (
                        facture_id, page, designation, unite, quantite,
                        prix_unitaire_ht, montant_ht, taux_tva, montant_ttc
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        facture_id,
                        _entier_ou_none(ligne.get('page')),
                        _texte_ou_none(ligne.get('designation')),
                        _texte_ou_none(ligne.get('unite')),
                        _nombre_ou_none(ligne.get('quantite')),
                        _nombre_ou_none(
                            ligne.get('prix_unitaire_ht')
                            if ligne.get('prix_unitaire_ht') is not None
                            else ligne.get('prix_unitaire')
                        ),
                        _nombre_ou_none(
                            ligne.get('montant_ht')
                            if ligne.get('montant_ht') is not None
                            else ligne.get('montant')
                        ),
                        _nombre_ou_none(ligne.get('taux_tva')),
                        _nombre_ou_none(ligne.get('montant_ttc')),
                    ),
                )
        return facture_id


def lire_facture(doc_id_ou_id: str | int, chemin_base: Path | str | None = None) -> dict | None:
    chemin = Path(chemin_base) if chemin_base is not None else chemin_par_defaut()
    if not chemin.exists():
        return None
    with closing(ouvrir_connexion(chemin, lecture_seule=True)) as connexion:
        connexion.execute('BEGIN')
        if isinstance(doc_id_ou_id, int) and not isinstance(doc_id_ou_id, bool):
            row = connexion.execute('SELECT * FROM factures WHERE id = ?', (doc_id_ou_id,)).fetchone()
        else:
            row = connexion.execute('SELECT * FROM factures WHERE doc_id = ?', (str(doc_id_ou_id),)).fetchone()
        if row is None:
            return None
        facture = dict(row)
        facture['lignes'] = [
            dict(r)
            for r in connexion.execute(
                'SELECT * FROM lignes_factures WHERE facture_id = ? ORDER BY id',
                (facture['id'],),
            )
        ]
        return facture


def lister_factures(
    chemin_base: Path | str | None = None,
    *,
    fournisseur: str | None = None,
    date_debut: str | None = None,
    date_fin: str | None = None,
    statut: str | None = None,
    inclure_lignes: bool = False,
) -> list[dict]:
    chemin = Path(chemin_base) if chemin_base is not None else chemin_par_defaut()
    if not chemin.exists():
        return []
    clauses: list[str] = []
    params: list[Any] = []
    if fournisseur:
        clauses.append('LOWER(fournisseur) LIKE ?')
        params.append(f'%{fournisseur.strip().lower()}%')
    if date_debut:
        clauses.append('date_facture >= ?')
        params.append(date_debut.strip())
    if date_fin:
        clauses.append('date_facture <= ?')
        params.append(date_fin.strip())
    if statut:
        clauses.append('statut = ?')
        params.append(statut.strip())
    where_sql = (' WHERE ' + ' AND '.join(clauses)) if clauses else ''

    with closing(ouvrir_connexion(chemin, lecture_seule=True)) as connexion:
        connexion.execute('BEGIN')
        rows = connexion.execute(
            f'SELECT * FROM factures{where_sql} ORDER BY id',
            tuple(params),
        ).fetchall()
        factures = [dict(r) for r in rows]
        if inclure_lignes and factures:
            lignes_par_id: dict[int, list[dict]] = {}
            for lr in connexion.execute('SELECT * FROM lignes_factures ORDER BY id'):
                d = dict(lr)
                lignes_par_id.setdefault(d['facture_id'], []).append(d)
            for f in factures:
                f['lignes'] = lignes_par_id.get(f['id'], [])
        return factures


def compter_factures(chemin_base: Path | str | None = None) -> dict:
    chemin = Path(chemin_base) if chemin_base is not None else chemin_par_defaut()
    if not chemin.exists():
        return {'chemin': str(chemin), 'nombre_factures': 0, 'nombre_lignes': 0}
    with closing(ouvrir_connexion(chemin, lecture_seule=True)) as connexion:
        connexion.execute('BEGIN')
        nb_f = connexion.execute('SELECT COUNT(*) FROM factures').fetchone()[0]
        nb_l = connexion.execute('SELECT COUNT(*) FROM lignes_factures').fetchone()[0]
        return {'chemin': str(chemin), 'nombre_factures': int(nb_f), 'nombre_lignes': int(nb_l)}
