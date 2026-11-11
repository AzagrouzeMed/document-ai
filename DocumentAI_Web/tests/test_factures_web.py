"""Consultation SQLite via ASGI, bases temporaires, sans extraction ni reseau."""
import socket
from contextlib import ExitStack
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as web
from fastapi.testclient import TestClient


class FacturesWebTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.data = Path(temp.name) / 'donnees'
        self.base = self.data / 'document_ai.db'
        for mocker in (
            patch.object(web, 'DATA', self.data),
            patch.object(web, 'worker', side_effect=AssertionError('Extraction interdite')),
            patch.object(web.subprocess, 'run', side_effect=AssertionError('Sous-processus interdit')),
            patch.object(socket, 'create_connection', side_effect=AssertionError('Reseau interdit')),
        ):
            mocker.start()
            self.addCleanup(mocker.stop)
        self.client = TestClient(web.app)
        self.addCleanup(self.client.close)

    def initialiser(self):
        web.stockage.initialiser_base(self.base)

    def enregistrer(self, statut='partiel'):
        return web.stockage.enregistrer_facture({
            'numero_document': 'F-001', 'fournisseur': 'Atelier \u00c9t\u00e9',
            'client': '\u0634\u0631\u0643\u0629', 'date': '2026-11-09',
            'devise': 'MAD', 'statut': statut, 'total_ht': '12.30',
            'fichier_source': 'C:/prive/source.pdf',
            'lignes': [{'designation': '<img src=x onerror=alert(1)>',
                        'quantite': '1.234567890123456789', 'page': 1},
                       {'designation': '\u062e\u062f\u0645\u0629', 'quantite': None}],
        }, self.base, traitement_id='a'*32)

    def test_base_absente_aucune_creation(self):
        self.assertEqual(self.client.get('/api/factures').json(), [])
        self.assertEqual(self.client.get('/api/factures/1').status_code, 404)
        self.assertFalse(self.data.exists())

    def test_base_vide_et_404(self):
        self.initialiser()
        avant = self.base.read_bytes()
        response = self.client.get('/api/factures')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])
        self.assertEqual(self.client.get('/api/factures/999').status_code, 404)
        self.assertEqual(self.base.read_bytes(), avant)

    def test_liste_detail_unicode_precision_et_aucune_ecriture(self):
        self.initialiser()
        identifiant = self.enregistrer()
        avant = self.base.read_bytes()
        interdits = ['initialiser_base', 'enregistrer_facture', 'corriger_champ', 'ajouter_article']
        with ExitStack() as stack:
            for nom in interdits:
                stack.enter_context(patch.object(web.stockage, nom, side_effect=AssertionError('Ecriture interdite')))
            for _ in range(2):
                response = self.client.get('/api/factures')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), [{
                    'id': identifiant, 'numero_document': 'F-001',
                    'fournisseur': 'Atelier \u00c9t\u00e9', 'client': '\u0634\u0631\u0643\u0629',
                    'date_facture': '2026-11-09', 'devise': 'MAD', 'statut': 'partiel'}])
                detail = self.client.get(f'/api/factures/{identifiant}')
                self.assertEqual(detail.status_code, 200)
                facture = detail.json()
                self.assertEqual(facture['statut'], 'partiel')
                self.assertEqual(facture['total_ht'], '12.30')
                self.assertIsNone(facture['total_ttc'])
                self.assertEqual(len(facture['lignes']), 2)
                self.assertEqual(facture['lignes'][0]['quantite'], '1.234567890123456789')
                self.assertEqual(facture['lignes'][0]['designation'], '<img src=x onerror=alert(1)>')
                self.assertEqual(facture['lignes'][1]['designation'], '\u062e\u062f\u0645\u0629')
                self.assertIsNone(facture['lignes'][1]['quantite'])
                for prive in ['chemin_original', 'json_brut', 'traitement_id', 'diagnostic']:
                    self.assertNotIn(prive, facture)
                    self.assertNotIn(prive, response.json()[0])
        self.assertEqual(self.base.read_bytes(), avant)
        self.assertEqual(len(web.stockage.lister_factures(self.base)), 1)
        self.assertEqual(len(web.stockage.lire_articles(identifiant, self.base)), 2)

    def test_statut_a_verifier_et_absences(self):
        self.initialiser()
        identifiant = web.stockage.enregistrer_facture({'statut': 'a_verifier', 'lignes': []}, self.base)
        detail = self.client.get(f'/api/factures/{identifiant}').json()
        self.assertEqual(detail['statut'], 'a_verifier')
        self.assertIsNone(detail['numero_document'])
        self.assertEqual(detail['lignes'], [])

    def test_erreur_sqlite_sans_diagnostic_public(self):
        self.data.mkdir()
        self.base.write_bytes(b'base invalide fictive')
        avant = self.base.read_bytes()
        for url in ['/api/factures', '/api/factures/1']:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 503)
            message = response.json()['detail']
            self.assertIn('indisponible', message)
            self.assertNotIn(str(self.base), message)
            self.assertNotIn('sqlite', message.lower())
        self.assertEqual(self.base.read_bytes(), avant)

    def test_connexion_lecture_seule_refuse_ecriture(self):
        self.initialiser()
        connexion = web.stockage.ouvrir_connexion(self.base, lecture_seule=True)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                connexion.execute("INSERT INTO factures (json_brut) VALUES ('{}')")
        finally:
            connexion.close()


if __name__ == '__main__':
    unittest.main()
