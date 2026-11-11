"""Integration web/SQLite : extraction et PDF simules, bases temporaires."""
import json
import socket
import sqlite3
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as web
from fastapi.testclient import TestClient


JSON_FINAL = """ {
  "document_id": "DOC-SIMULE",
  "type_document": "facture",
  "numero_document": "FACT-FICTIVE-001",
  "fournisseur": "Fournisseur fictif",
  "client": "Client fictif",
  "ice_fournisseur": "001234",
  "statut": "a_verifier",
  "fichier_source": "source.pdf",
  "pages_traitees": [1],
  "devise": "MAD",
  "total_ht": 12.30,
  "montant_tva": null,
  "total_ttc": null,
  "lignes": [
    {"designation": "Premier", "quantite": 0.12345678901234567890123456789,
     "prix_unitaire_ht": 10.000000000000000000001, "montant_ht": null, "page": 1},
    {"designation": "Second", "quantite": 2.5, "prix_unitaire": 3.4567,
     "montant": 8.64175, "page": 1}
  ]
}
"""


class StockageWebTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        self.base = self.data / 'document_ai.db'
        self.s = web.stockage
        for mocker in (
            patch.object(web, 'DATA', self.data),
            patch.object(web, 'SLOTS'),
            patch.object(web.subprocess, 'run', side_effect=AssertionError('Extraction reelle interdite')),
            # TestClient appelle ASGI en memoire. Ne pas bloquer le socketpair
            # interne de Windows ; interdire les connexions reseau applicatives.
            patch.object(socket, 'create_connection', side_effect=AssertionError('Reseau interdit')),
            patch.object(web, 'export_pdf', side_effect=lambda d, c, p: p.write_bytes(b'PDF fictif')),
        ):
            mocker.start()
            self.addCleanup(mocker.stop)
        self.p, self.source = self.creer_traitement('a' * 32)
        self.client = TestClient(web.app)
        self.addCleanup(self.client.close)
        self.url = '/api/traitements/' + self.p.name

    def creer_traitement(self, identifiant):
        p = self.data / identifiant
        p.mkdir()
        source = p / 'source.pdf'
        source.write_bytes(b'Original fictif, aucun OCR')
        web.write_status(p, id=identifiant, nom_fichier='facture fictive.pdf',
                         source='source.pdf', etat='attente', created_at=web.now())
        return p, source

    def executer_worker(self, brut=JSON_FINAL, *, p=None, source=None, retour=0):
        p = p or self.p
        source = source or self.source
        run = p / 'resultats' / 'DOC-SIMULE'

        def extraction_simulee(*args, **kwargs):
            run.mkdir(parents=True, exist_ok=True)
            (run / 'document.json').write_bytes(brut.encode('utf-8'))
            (run / 'controles.json').write_text(json.dumps(dict(
                statut=json.loads(brut).get('statut') if isinstance(json.loads(brut), dict) else None,
                revue_humaine_requise=True, alertes=['Revue humaine requise.'])), encoding='utf-8')
            (run / 'preuves.json').write_text('{}', encoding='utf-8')
            (run / 'texte_ocr.txt').write_text('Texte fictif', encoding='utf-8')
            return SimpleNamespace(returncode=retour)

        with patch.object(web.subprocess, 'run', side_effect=extraction_simulee) as extraction:
            web.worker(p, source, 'auto', True)
        extraction.assert_called_once()
        return run, web.load(p / 'statut.json')

    def test_sauvegarde_articles_lien_original_et_precision(self):
        run, statut = self.executer_worker()
        self.assertEqual(statut['etat'], 'termine')
        self.assertEqual(statut['resultat_statut'], 'a_verifier')
        self.assertEqual(statut['sauvegarde']['etat'], 'enregistre')
        facture = self.s.lire_facture(statut['sauvegarde']['facture_id'], self.base)
        self.assertEqual(facture['traitement_id'], self.p.name)
        self.assertEqual(facture['document_id'], 'DOC-SIMULE')
        self.assertEqual(facture['chemin_original'], str(self.source.resolve()))
        self.assertEqual(facture['statut'], 'a_verifier')
        self.assertEqual(facture['json_brut'], JSON_FINAL)
        self.assertEqual((run / 'document.json').read_text(encoding='utf-8'), JSON_FINAL)
        self.assertEqual(facture['total_ht'], '12.30')
        self.assertIsNone(facture['total_ttc'])
        self.assertEqual([a['designation'] for a in facture['lignes']], ['Premier', 'Second'])
        self.assertEqual(facture['lignes'][0]['quantite'], '0.12345678901234567890123456789')
        self.assertEqual(facture['lignes'][0]['prix_unitaire_ht'], '10.000000000000000000001')
        self.assertIsNone(facture['lignes'][0]['montant_ht'])
        self.assertEqual(self.s.lire_validations(facture['id'], self.base), [])
        # Le nom affiche reste celui importe, sans reecrire le fichier brut.
        self.assertEqual(self.client.get(self.url + '/resultat').json()['document']['fichier_source'],
                         'facture fictive.pdf')
        self.assertEqual(web.export_pdf.call_args.args[0]['fichier_source'], 'facture fictive.pdf')

    def test_json_brut_conserve_aussi_les_fins_de_ligne(self):
        brut = JSON_FINAL.replace('\n', '\r\n')
        run, statut = self.executer_worker(brut)
        facture = self.s.lire_facture(statut['sauvegarde']['facture_id'], self.base)
        self.assertEqual(facture['json_brut'], brut)
        self.assertEqual((run / 'document.json').read_bytes(), brut.encode('utf-8'))

    def test_extraction_partielle(self):
        brut = JSON_FINAL.replace('"statut": "a_verifier"', '"statut": "partiel"')
        _, statut = self.executer_worker(brut)
        self.assertEqual(statut['etat'], 'termine')
        self.assertEqual(statut['resultat_statut'], 'partiel')
        facture = self.s.lire_facture(statut['sauvegarde']['facture_id'], self.base)
        self.assertEqual(facture['statut'], 'partiel')
        self.assertEqual(facture['json_brut'], brut)
        self.assertEqual(len(facture['lignes']), 2)
        self.assertEqual(self.s.lire_validations(facture['id'], self.base), [])

    def test_consultations_et_nouvelle_tentative_sans_doublon(self):
        _, statut = self.executer_worker()
        facture_id = statut['sauvegarde']['facture_id']
        # Simulation : la transaction a reussi mais son retour n'a pas ete publie.
        statut.pop('sauvegarde')
        (self.p / 'statut.json').write_text(json.dumps(statut), encoding='utf-8')
        self.s.initialiser_base(self.base)
        for _ in range(3):
            retour = web.sauvegarder_resultat(self.p, self.source, JSON_FINAL)
            self.assertEqual(retour['facture_id'], facture_id)
        with patch.object(web, 'sauvegarder_resultat', side_effect=AssertionError('GET ne doit pas sauvegarder')):
            for _ in range(2):
                for suffix in ('', '/resultat', '/source', '/telecharger/json'):
                    self.assertEqual(self.client.get(self.url + suffix).status_code, 200)
                self.assertEqual(self.client.get('/api/traitements').status_code, 200)
        self.assertEqual(len(self.s.lister_factures(self.base)), 1)
        self.assertEqual(len(self.s.lire_articles(facture_id, self.base)), 2)

    def test_meme_numero_deux_traitements_distincts(self):
        _, premier = self.executer_worker()
        p, source = self.creer_traitement('b' * 32)
        _, second = self.executer_worker(p=p, source=source)
        self.assertNotEqual(premier['sauvegarde']['facture_id'], second['sauvegarde']['facture_id'])
        factures = self.s.lister_factures(self.base)
        self.assertEqual(len(factures), 2)
        self.assertEqual(factures[0]['numero_document'], factures[1]['numero_document'])
        self.assertEqual({f['traitement_id'] for f in factures}, {self.p.name, p.name})

    def test_tentatives_simultanees_sans_doublon(self):
        self.s.initialiser_base(self.base)
        barriere = Barrier(4)

        def enregistrer(_):
            barriere.wait(timeout=10)
            return self.s.enregistrer_facture(JSON_FINAL, self.base,
                traitement_id=self.p.name, chemin_original=str(self.source.resolve()))

        with ThreadPoolExecutor(max_workers=4) as pool:
            identifiants = list(pool.map(enregistrer, range(4)))
        self.assertEqual(len(set(identifiants)), 1)
        self.assertEqual(len(self.s.lister_factures(self.base)), 1)
        self.assertEqual(len(self.s.lire_articles(identifiants[0], self.base)), 2)
        # L'index garantit aussi l'unicite en dehors de la fonction de sauvegarde.
        c = self.s.ouvrir_connexion(self.base)
        try:
            with self.assertRaises(sqlite3.IntegrityError), c:
                c.execute('INSERT INTO factures (traitement_id, json_brut) VALUES (?, ?)',
                          (self.p.name, '{}'))
        finally:
            c.close()

    def test_echec_sqlite_fichiers_diagnostic_et_reprise(self):
        original = self.source.read_bytes()
        with patch.object(self.s, 'enregistrer_facture', side_effect=sqlite3.OperationalError('base indisponible simulee')):
            run, statut = self.executer_worker()
        self.assertEqual(statut['etat'], 'termine')
        self.assertEqual(statut['resultat_statut'], 'a_verifier')
        self.assertEqual(statut['sauvegarde']['etat'], 'echec')
        self.assertIsNone(statut['sauvegarde']['facture_id'])
        self.assertIn('OperationalError', statut['sauvegarde']['diagnostic'])
        self.assertIn('base indisponible simulee', (self.p / 'stockage_erreur.log').read_text(encoding='utf-8'))
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual((run / 'document.json').read_text(encoding='utf-8'), JSON_FINAL)
        for nom in ('controles.json', 'preuves.json', 'texte_ocr.txt', 'rapport.pdf'):
            self.assertTrue((run / nom).is_file())
        self.assertTrue((self.p / 'traitement.log').is_file())
        self.assertEqual(self.client.get(self.url + '/resultat').status_code, 200)
        self.assertEqual(self.client.get(self.url + '/telecharger/json').status_code, 200)
        public = self.client.get(self.url).json()
        self.assertEqual(public['sauvegarde']['etat'], 'echec')
        self.assertEqual(self.s.lister_factures(self.base), [])
        premier = web.sauvegarder_resultat(self.p, self.source, JSON_FINAL)
        second = web.sauvegarder_resultat(self.p, self.source, JSON_FINAL)
        self.assertEqual(premier['etat'], 'enregistre')
        self.assertEqual(premier['facture_id'], second['facture_id'])
        self.assertEqual(len(self.s.lister_factures(self.base)), 1)

    def test_echec_initialisation_ne_masque_pas_extraction(self):
        self.base.mkdir()  # Une vraie erreur SQLite, uniquement dans le dossier temporaire.
        run, statut = self.executer_worker()
        self.assertEqual(statut['etat'], 'termine')
        self.assertEqual(statut['sauvegarde']['etat'], 'echec')
        self.assertTrue((run / 'document.json').is_file())
        self.assertEqual(self.client.get(self.url + '/resultat').status_code, 200)

    def test_article_invalide_echec_sauvegarde_seulement(self):
        brut = JSON_FINAL.replace('"quantite": 2.5', '"quantite": true')
        run, statut = self.executer_worker(brut)
        self.assertEqual(statut['etat'], 'termine')
        self.assertEqual(statut['sauvegarde']['etat'], 'echec')
        self.assertIn('ValueError', statut['sauvegarde']['diagnostic'])
        self.assertEqual(self.s.lister_factures(self.base), [])
        self.assertEqual((run / 'document.json').read_text(encoding='utf-8'), brut)

    def test_echec_pdf_nempeche_pas_sauvegarde(self):
        with patch.object(web, 'export_pdf', side_effect=RuntimeError('PDF simule indisponible')):
            _, statut = self.executer_worker()
        self.assertEqual(statut['etat'], 'termine')
        self.assertEqual(statut['sauvegarde']['etat'], 'enregistre')
        self.assertFalse(statut['pdf_disponible'])
        self.assertTrue(statut['avertissement_pdf'])
        self.assertEqual(len(self.s.lister_factures(self.base)), 1)

    def test_traitement_echoue_ou_json_inexploitable_non_sauvegarde(self):
        for brut, retour in ((JSON_FINAL, 1), ('[]', 0), ('{}', 0)):
            with self.subTest(brut=brut, retour=retour):
                with patch.object(web, 'sauvegarder_resultat') as sauvegarde:
                    _, statut = self.executer_worker(brut, retour=retour)
                self.assertEqual(statut['etat'], 'echec')
                sauvegarde.assert_not_called()
                self.assertFalse(self.base.exists())


if __name__ == '__main__':
    unittest.main()
