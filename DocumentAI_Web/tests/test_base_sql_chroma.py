"""Tests unitaires pour moteur/base_sql.py, moteur/index_vectoriel.py et POST /api/reindexer."""
import hashlib
import json
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from fastapi.testclient import TestClient

ROOT_WEB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_WEB))

import app as web  # noqa: E402
from moteur import base_sql, index_vectoriel  # noqa: E402


class FakeEmbedder:
    def encode(self, textes, normalize_embeddings=True, show_progress_bar=False, convert_to_numpy=True):
        dim = 24
        vecs = np.zeros((len(textes), dim), dtype='float32')
        for i, t in enumerate(textes):
            for tok in t.lower().split():
                h = int(hashlib.md5(tok.encode('utf-8')).hexdigest()[:8], 16)
                vecs[i, h % dim] += 1.0
            n = float(np.linalg.norm(vecs[i]))
            if n > 0:
                vecs[i] /= n
            else:
                vecs[i, 0] = 1.0
        return vecs


class BaseSqlEtChromaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        self.db_path = self.data / 'factures.db'
        self.chroma_dir = self.data / 'chroma_db'
        self.embedder = FakeEmbedder()
        for m in (
            patch.object(web, 'DATA', self.data),
            patch.object(index_vectoriel, '_EMBEDDER', self.embedder),
            patch.object(index_vectoriel, '_EMBEDDER_NAME', index_vectoriel.modele_embedding_nom()),
            patch.object(socket, 'create_connection', side_effect=AssertionError('Reseau interdit')),
        ):
            m.start()
            self.addCleanup(m.stop)
        self.client = TestClient(web.app)
        self.addCleanup(self.client.close)

    def test_base_sql_upsert_sans_doublon_et_consultation(self):
        doc = {
            'document_id': 'DOC-TEST-001',
            'type_document': 'facture',
            'fournisseur': 'ATLAS Fournitures SARL / أطلس للتوريدات',
            'ice_fournisseur': '973471338608099',
            'client': 'BAHJA Services',
            'ice_client': '924448527121068',
            'numero_document': 'DEMO-001',
            'date': '2025-11-17',
            'devise': 'MAD',
            'total_ht': 851.44,
            'montant_tva': 170.29,
            'total_ttc': 1021.73,
            'statut': 'a_verifier',
            'lignes': [
                {'page': 1, 'designation': 'Produit nettoyant 5 L / منظف 5 لترات', 'unite': 'Bidon', 'quantite': 8, 'prix_unitaire_ht': 106.43, 'montant_ht': 851.44, 'taux_tva': 20.0},
            ],
        }
        id1 = base_sql.enregistrer_facture(doc, source_fichier='facture1.jpg', chemin_base=self.db_path)
        doc['total_ttc'] = 1050.00
        doc['lignes'].append({'page': 1, 'designation': 'Stylo bleu', 'quantite': 10, 'prix_unitaire_ht': 2.5, 'montant_ht': 25.0})
        id2 = base_sql.enregistrer_facture(doc, source_fichier='facture1.jpg', chemin_base=self.db_path)
        self.assertEqual(id1, id2)
        lu = base_sql.lire_facture('DOC-TEST-001', chemin_base=self.db_path)
        self.assertEqual(lu['total_ttc'], 1050.0)
        self.assertEqual(len(lu['lignes']), 2)
        self.assertEqual(base_sql.compter_factures(self.db_path)['nombre_factures'], 1)
        self.assertEqual(len(base_sql.lister_factures(self.db_path, fournisseur='ATLAS')), 1)

    def test_chunking_et_indexation_chromadb(self):
        doc = {
            'document_id': 'DOC-CHROMA-01',
            'fournisseur': 'SAFA Fournitures',
            'ice_fournisseur': '001122334455667',
            'client': 'Client Test',
            'numero_document': 'F-2026-09',
            'date': '2026-03-15',
            'devise': 'MAD',
            'total_ht': 500.0,
            'montant_tva': 100.0,
            'total_ttc': 600.0,
            'lignes': [
                {'page': 1, 'designation': 'Imprimante laser / طابعة ليزر', 'quantite': 1, 'prix_unitaire_ht': 500.0, 'montant_ht': 500.0},
            ],
        }
        chunks = index_vectoriel.chunker_facture(doc, source_fichier='scan.pdf')
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0]['metadonnees']['type_chunk'], 'entete')
        self.assertEqual(chunks[1]['metadonnees']['type_chunk'], 'article')
        self.assertIn('SAFA Fournitures', chunks[1]['texte'])
        self.assertIn('2026-03-15', chunks[1]['texte'])

        res = index_vectoriel.indexer_facture(doc, source_fichier='scan.pdf', chroma_dir=self.chroma_dir, embedder=self.embedder)
        self.assertEqual(res['nombre_chunks'], 2)
        hits = index_vectoriel.rechercher_similaires('Imprimante laser', top_k=2, chroma_dir=self.chroma_dir, embedder=self.embedder)
        self.assertEqual(len(hits), 2)
        self.assertEqual(hits[0]['metadonnees']['doc_id'], 'DOC-CHROMA-01')

    def test_worker_et_endpoint_reindexer(self):
        # 1. Sauvegarde via sauvegarder_resultat -> alimente factures.db et chroma_db.
        job_id = 'a' * 32
        job_dir = self.data / job_id
        run_dir = job_dir / 'resultats' / 'DOC-WEB-01'
        run_dir.mkdir(parents=True)
        (job_dir / 'statut.json').write_text(json.dumps({'id': job_id, 'nom_fichier': 'devis.pdf'}), encoding='utf-8')
        src = job_dir / 'source.pdf'
        src.write_bytes(b'%PDF-1.4')
        brut = json.dumps({
            'document_id': 'DOC-WEB-01',
            'type_document': 'facture',
            'fournisseur': 'NOUR Services',
            'numero_document': 'FAC-01',
            'date': '2026-01-20',
            'devise': 'MAD',
            'total_ttc': 240.0,
            'statut': 'a_verifier',
            'lignes': [{'designation': 'Papier A4', 'quantite': 4, 'prix_unitaire_ht': 50.0, 'montant_ht': 200.0, 'page': 1}],
        })
        (run_dir / 'document.json').write_text(brut, encoding='utf-8')
        ret = web.sauvegarder_resultat(job_dir, src, brut, run=run_dir)
        self.assertEqual(ret['base_sql']['etat'], 'enregistre')
        self.assertEqual(ret['chroma']['etat'], 'indexe')
        self.assertEqual(ret['chroma']['nombre_chunks'], 2)

        # 2. Ajout d'un second document uniquement sur disque, puis POST /api/reindexer.
        job2 = self.data / ('b' * 32)
        run2 = job2 / 'resultats' / 'DOC-WEB-02'
        run2.mkdir(parents=True)
        (job2 / 'statut.json').write_text(json.dumps({'id': 'b' * 32, 'nom_fichier': 'ar.jpg'}), encoding='utf-8')
        (run2 / 'document.json').write_text(json.dumps({
            'document_id': 'DOC-WEB-02',
            'fournisseur': 'شركة النور',
            'numero_document': 'FAC-02',
            'date': '2026-02-01',
            'total_ttc': 500.0,
            'lignes': [
                {'designation': 'لوحة مفاتيح', 'quantite': 2, 'montant_ht': 200.0, 'page': 1},
                {'designation': 'فأرة', 'quantite': 3, 'montant_ht': 300.0, 'page': 1},
            ],
        }, ensure_ascii=False), encoding='utf-8')

        self.assertEqual(self.client.post('/api/reindexer').status_code, 403)
        resp = self.client.post('/api/reindexer', headers={'X-DocumentAI-Token': web.TOKEN})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body['factures_traitees'], 2)
        self.assertEqual(body['base_sql']['nombre_factures'], 2)
        self.assertEqual(body['base_sql']['nombre_lignes'], 3)
        self.assertEqual(body['chroma']['nombre_chunks'], 5)  # 2 chunks (DOC-WEB-01) + 3 chunks (DOC-WEB-02)


if __name__ == '__main__':
    unittest.main()
