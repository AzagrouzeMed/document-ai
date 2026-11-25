"""Tests du chargement .env, du RAG hybride SQLite + FAISS et des endpoints associes."""
import hashlib
import json
import os
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
import rag_hybride  # noqa: E402
import stockage  # noqa: E402
from moteur import extraction_gemini as gemini  # noqa: E402


class FakeMultilingualEmbedder:
    """Embedder deterministe de test simulant un espace multilingue FR/AR."""

    CONCEPTS = (
        ('imprimante', 'طابعة', 'papier', 'ورق', 'cartouche', 'فحص'),
        ('safa', 'امانة', 'amana', 'fournitures', 'مكتبية'),
        ('atlas', 'أطلس', 'خدمات', 'services', 'maintenance', 'صيانة'),
        ('paiement', 'دفع', 'virement', 'شيك', '30 jours'),
        ('total', 'ttc', 'ht', 'tva', 'مبلغ', 'إجمالي', 'ضريبة', 'فاتورة'),
    )

    def encode(self, textes, normalize_embeddings=True, show_progress_bar=False, convert_to_numpy=True):
        dim = 32
        vecs = np.zeros((len(textes), dim), dtype='float32')
        for row, t in enumerate(textes):
            norm = stockage.normaliser_texte_recherche(t)
            for idx, groupe in enumerate(self.CONCEPTS):
                for mot in groupe:
                    if stockage.normaliser_texte_recherche(mot) in norm:
                        vecs[row, idx] += 1.0
            for token in norm.split():
                h = int(hashlib.md5(token.encode('utf-8')).hexdigest()[:8], 16)
                vecs[row, 8 + (h % (dim - 8))] += 0.5
            n = float(np.linalg.norm(vecs[row]))
            if n > 0:
                vecs[row] /= n
            else:
                vecs[row, 0] = 1.0
        return vecs


class EnvStartupTests(unittest.TestCase):
    def test_chargement_env_et_absence_503_sur_post_traitements(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            fake_pdf = b'%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF'
            with (
                patch.object(web, 'DATA', data_dir),
                patch.object(web, 'validate_file'),
                patch.object(web.POOL, 'submit') as mock_submit,
                patch.dict(os.environ, {'GEMINI_API_KEY': 'cle-test-env', 'GEMINI_MODEL': 'gemini-2.5-flash'}),
            ):
                client = TestClient(web.app)
                cfg = client.get('/api/config').json()
                self.assertTrue(cfg['gemini_configure'])
                self.assertEqual(cfg['modele'], 'gemini-2.5-flash')
                resp = client.post(
                    '/api/traitements',
                    headers={'X-DocumentAI-Token': web.TOKEN},
                    files={'file': ('facture.pdf', fake_pdf, 'application/pdf')},
                    data={'lang': 'auto', 'sans_llm': 'false'},
                )
                self.assertEqual(resp.status_code, 202)
                self.assertIn('id', resp.json())
                mock_submit.assert_called_once()
                client.close()

    def test_charger_env_depuis_fichier_avec_bom(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_dir = Path(tmp)
            (env_dir / '.env').write_text('\ufeffGEMINI_API_KEY="cle-bom"\nGEMINI_MODEL=models/gemini-2.5-flash\n', encoding='utf-8')
            with (
                patch.dict(os.environ, {}, clear=True),
                patch('moteur.extraction_gemini.Path.cwd', return_value=env_dir),
            ):
                gemini._charger_env()
                self.assertEqual(os.environ.get('GEMINI_API_KEY'), 'cle-bom')
                self.assertEqual(os.environ.get('GEMINI_MODEL'), 'models/gemini-2.5-flash')


class RagHybrideTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        self.base = self.data / 'document_ai.db'
        self.index_dir = self.data / 'rag_index'
        stockage.initialiser_base(self.base)

        self.f1 = stockage.enregistrer_facture({
            'document_id': 'DOC-FR-001',
            'type_document': 'facture',
            'numero_document': 'FACT-2025-001',
            'fournisseur': 'SAFA Fournitures SARL',
            'ice_fournisseur': '001234567890123',
            'client': 'AMANA Services',
            'date': '2025-09-17',
            'devise': 'MAD',
            'total_ht': '1000.00',
            'montant_tva': '200.00',
            'total_ttc': '1200.00',
            'statut': 'a_verifier',
            'fichier_source': 'facture_safa.pdf',
            'lignes': [
                {'designation': 'Imprimante laser monochrome', 'quantite': '1', 'prix_unitaire_ht': '800.00', 'montant_ht': '800.00', 'page': 1},
                {'designation': 'Ramettes papier A4', 'quantite': '4', 'prix_unitaire_ht': '50.00', 'montant_ht': '200.00', 'page': 1},
            ],
        }, self.base, traitement_id='1' * 32)
        stockage.enregistrer_pages_document(
            self.f1,
            [dict(numero_page=1, langue='fr', texte_ocr='SAFA Fournitures SARL\nImprimante laser et papier A4.\nPaiement par virement à 30 jours.', source='ocr/page_1_fr.json')],
            self.base,
            traitement_id='1' * 32,
        )

        self.f2 = stockage.enregistrer_facture({
            'document_id': 'DOC-AR-002',
            'type_document': 'facture',
            'numero_document': 'FACT-2025-002',
            'fournisseur': 'شركة أطلس للخدمات',
            'ice_fournisseur': '009988776655443',
            'client': 'مؤسسة النور',
            'date': '2025-06-14',
            'devise': 'MAD',
            'total_ht': '500.50',
            'montant_tva': '100.10',
            'total_ttc': '600.60',
            'statut': 'partiel',
            'fichier_source': 'facture_atlas.jpg',
            'lignes': [
                {'designation': 'صيانة الطابعة المكتبية', 'quantite': '2', 'montant_ttc': '600.60', 'page': 1},
            ],
        }, self.base, traitement_id='2' * 32)
        stockage.enregistrer_pages_document(
            self.f2,
            [dict(numero_page=1, langue='ar', texte_ocr='فاتورة رقم FACT-2025-002\nشركة أطلس للخدمات\nصيانة الطابعة', source='ocr/page_1_ar.json')],
            self.base,
            traitement_id='2' * 32,
        )

        self.embedder = FakeMultilingualEmbedder()
        for m in (
            patch.object(web, 'DATA', self.data),
            patch.object(rag_hybride, '_EMBEDDER', self.embedder),
            patch.object(rag_hybride, '_EMBEDDER_NAME', rag_hybride.modele_embedding_nom()),
            patch.object(socket, 'create_connection', side_effect=AssertionError('Reseau interdit')),
            patch.object(gemini, 'urlopen', side_effect=AssertionError('API reseau interdite')),
        ):
            m.start()
            self.addCleanup(m.stop)

        self.client = TestClient(web.app)
        self.addCleanup(self.client.close)

    def test_requete_structuree_sqlite_calculs_exact_decimal(self):
        res = stockage.rechercher_factures_structurees(self.base, devise='MAD')
        self.assertEqual(res['calculs']['nombre_factures'], 2)
        agg = res['calculs']['par_devise']['MAD']
        self.assertEqual(agg['total_ht'], '1500.50')
        self.assertEqual(agg['montant_tva'], '300.10')
        self.assertEqual(agg['total_ttc'], '1800.60')

        res_ice = stockage.rechercher_factures_structurees(self.base, ice='009988776655443')
        self.assertEqual(len(res_ice['factures']), 1)
        self.assertEqual(res_ice['factures'][0]['numero_document'], 'FACT-2025-002')

        res_ar = stockage.rechercher_factures_structurees(self.base, fournisseur='اطلس')
        self.assertEqual(len(res_ar['factures']), 1)

        with self.assertRaises(ValueError):
            stockage.executer_sql_lecture_seule('DELETE FROM factures', chemin_base=self.base)
        with self.assertRaises(ValueError):
            stockage.executer_sql_lecture_seule('SELECT json_brut FROM factures', chemin_base=self.base)

    def test_indexation_faiss_et_sync_incrementale(self):
        s1 = rag_hybride.synchroniser_index(self.base, self.index_dir, embedder=self.embedder)
        self.assertEqual(s1['etat'], 'synchronise')
        self.assertEqual(s1['nombre_factures'], 2)
        self.assertGreaterEqual(s1['nombre_vecteurs'], 6)

        s2 = rag_hybride.synchroniser_index(self.base, self.index_dir, embedder=self.embedder)
        self.assertEqual(s2['etat'], 'a_jour')

        # Ajout d'une troisieme facture -> sync incrementale sans tout reconstruire
        stockage.enregistrer_facture({
            'numero_document': 'FACT-2026-003',
            'fournisseur': 'EuroTech',
            'date': '2026-01-10',
            'devise': 'EUR',
            'total_ttc': '300.00',
            'lignes': [{'designation': 'Clavier sans fil'}],
        }, self.base, traitement_id='3' * 32)
        s3 = rag_hybride.synchroniser_index(self.base, self.index_dir, embedder=self.embedder)
        self.assertEqual(s3['etat'], 'synchronise')
        self.assertEqual(s3['nombre_factures'], 3)

    def test_api_rag_hybride_fr_ar_avec_gemini_et_secours(self):
        # 1. Reindexation via API
        r_idx = self.client.post('/api/rag/indexer', headers={'X-DocumentAI-Token': web.TOKEN}, json={})
        self.assertEqual(r_idx.status_code, 200)
        self.assertEqual(r_idx.json()['nombre_factures'], 2)

        # 2. Question FR avec Gemini simule et citation de sources
        gemini_ok = {
            'candidates': [{
                'finishReason': 'STOP',
                'content': {'parts': [{'text': json.dumps({
                    'reponse': 'Le total TTC pour SAFA Fournitures SARL est de 1200.00 MAD.',
                    'references': ['S1'],
                })}]},
            }]
        }
        with (
            patch.object(gemini, 'configuration', return_value=('cle-fictive', 'gemini-2.5-flash')),
            patch.object(gemini, 'request_json', return_value=gemini_ok),
        ):
            r_fr = self.client.post(
                '/api/rag/question',
                headers={'X-DocumentAI-Token': web.TOKEN},
                json={'question': 'Quel est le total TTC de SAFA Fournitures ?'},
            )
        self.assertEqual(r_fr.status_code, 200)
        body_fr = r_fr.json()
        self.assertEqual(body_fr['etat'], 'reponse')
        self.assertEqual(body_fr['langue_question'], 'fr')
        self.assertEqual(body_fr['calculs']['par_devise']['MAD']['total_ttc'], '1200.00')
        self.assertEqual(body_fr['references'], ['S1'])
        self.assertTrue(body_fr['sources'][0]['citee'])
        self.assertTrue(body_fr['moteur_vectoriel']['actif'])

        # 3. Question en arabe sans LLM (mode secours deterministe)
        r_ar = self.client.post(
            '/api/rag/question',
            headers={'X-DocumentAI-Token': web.TOKEN},
            json={'question': 'ما هو المبلغ الإجمالي لفاتورة شركة أطلس للخدمات؟', 'utiliser_llm': False},
        )
        self.assertEqual(r_ar.status_code, 200)
        body_ar = r_ar.json()
        self.assertEqual(body_ar['etat'], 'secours_local')
        self.assertEqual(body_ar['langue_question'], 'ar')
        self.assertEqual(body_ar['calculs']['par_devise']['MAD']['total_ttc'], '600.60')
        self.assertTrue(any(s['numero_document'] == 'FACT-2025-002' for s in body_ar['sources']))

        # 4. Protection CSRF et endpoint GET /api/rag/factures
        self.assertEqual(self.client.post('/api/rag/question', json={'question': 'test'}).status_code, 403)
        r_filtre = self.client.get('/api/rag/factures', params={'fournisseur': 'SAFA'})
        self.assertEqual(r_filtre.status_code, 200)
        self.assertEqual(len(r_filtre.json()['factures']), 1)


if __name__ == '__main__':
    unittest.main()
