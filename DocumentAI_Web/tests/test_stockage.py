"""Tests autonomes : aucun acces ? la base reelle, aucun moteur importe."""
import copy
import importlib
import json
import sqlite3
import tempfile
import unittest
from decimal import Decimal, localcontext
from pathlib import Path
from unittest.mock import patch

from DocumentAI_Web import stockage as s

SCHEMA_INITIAL = "CREATE TABLE factures (\n                    id INTEGER PRIMARY KEY,\n                    numero_document TEXT,\n                    fournisseur TEXT,\n                    client TEXT,\n                    date_facture TEXT,\n                    devise TEXT,\n                    total_ht_centimes INTEGER,\n                    montant_tva_centimes INTEGER,\n                    total_ttc_centimes INTEGER,\n                    statut TEXT NOT NULL DEFAULT 'a_verifier',\n                    chemin_original TEXT,\n                    json_brut TEXT NOT NULL,\n                    cree_le TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP\n                );\nCREATE TABLE lignes_facture (\n                    id INTEGER PRIMARY KEY,\n                    facture_id INTEGER NOT NULL,\n                    numero_ligne INTEGER NOT NULL,\n                    designation TEXT,\n                    quantite TEXT,\n                    unite TEXT,\n                    prix_unitaire_ht TEXT,\n                    montant_ht TEXT,\n                    prix_unitaire_ttc TEXT,\n                    montant_ttc TEXT,\n                    prix_unitaire TEXT,\n                    montant TEXT,\n                    taux_tva TEXT,\n                    page INTEGER,\n                    FOREIGN KEY (facture_id)\n                        REFERENCES factures(id),\n                    UNIQUE (facture_id, numero_ligne)\n                );\nCREATE TABLE validations (\n                    id INTEGER PRIMARY KEY,\n                    facture_id INTEGER NOT NULL,\n                    champ TEXT NOT NULL,\n                    ancienne_valeur TEXT,\n                    nouvelle_valeur TEXT,\n                    action TEXT NOT NULL\n                        CHECK (action IN ('correction', 'confirmation')),\n                    auteur TEXT,\n                    date_action TEXT NOT NULL\n                        DEFAULT CURRENT_TIMESTAMP,\n                    FOREIGN KEY (facture_id)\n                        REFERENCES factures(id)\n                );"


class StockageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name) / 'test.db'
        s.initialiser_base(self.base)
        self.document = {
            'numero_document': 'FICTIF-001', 'fournisseur': "L'atelier fictif",
            'client': 'Client fictif', 'ice_fournisseur': '001234',
            'ice_client': '009876', 'date': '2026-11-06',
            'date_echeance': '2026-12-06', 'adresse_client': 'Adresse fictive',
            'adresse_fournisseur': 'Autre adresse fictive',
            'adresse_livraison': 'Livraison fictive', 'devise': 'MAD',
            'statut': 'partiel', 'total_ht': '12.30', 'total_ttc': None,
            'total_document': '14.76', 'taux_tva': '20',
            'lignes': [
                {'designation': 'Premier', 'quantite': '0.123456789012345678901234567890',
                 'prix_unitaire_ht': '1.234567890123456789012345678901',
                 'montant_ht': '0.15', 'prix_unitaire_ttc': '1.48',
                 'montant_ttc': '0.18', 'prix_unitaire': '3.456', 'montant': '4.567',
                 'hauteur_cm': '12.5', 'largeur_cm': '3.2', 'page': 1},
                {'designation': 'Deuxieme', 'quantite': '2.5'},
            ],
        }

    def enregistrer(self, document=None):
        return s.enregistrer_facture(self.document if document is None else document, self.base)

    def test_reouverture_et_champs_reels(self):
        identifiant = self.enregistrer()
        with s.ouvrir_connexion(self.base) as c:
            self.assertEqual(c.execute('PRAGMA foreign_keys').fetchone()[0], 1)
        c.close()
        facture = s.lire_facture(identifiant, self.base)
        for champ in ('ice_fournisseur', 'ice_client', 'date_echeance', 'adresse_client',
                      'adresse_fournisseur', 'adresse_livraison', 'devise', 'statut'):
            self.assertEqual(facture[champ], self.document[champ])
        self.assertEqual(facture['date_facture'], self.document['date'])
        self.assertEqual(facture['total_ht_centimes'], 1230)
        self.assertIsNone(facture['total_ttc_centimes'])
        self.assertIsNone(facture['montant_tva'])
        self.assertEqual(facture['total_document'], '14.76')
        articles = s.lire_articles(identifiant, self.base)
        self.assertEqual([a['designation'] for a in articles], ['Premier', 'Deuxieme'])
        for champ, valeur in self.document['lignes'][0].items():
            self.assertEqual(articles[0][champ], valeur)
        self.assertIsNone(articles[1]['montant_ht'])
        self.assertIsNone(articles[1]['prix_unitaire'])
        self.assertEqual(facture['lignes'], articles)
        self.assertEqual(len(s.lister_factures(self.base)), 1)
        self.assertIsNone(s.lire_facture(9999, self.base))

    def test_json_original_et_decimal_json(self):
        brut = ' { "devise": "JPY", "total_ttc": 12.12345678901234567890123456789, "lignes": [] }\n'
        identifiant = self.enregistrer(brut)
        f = s.lire_facture(identifiant, self.base)
        self.assertEqual(f['json_brut'], brut)
        self.assertEqual(f['total_ttc'], '12.12345678901234567890123456789')
        self.assertIsNone(f['total_ttc_centimes'])
        s.corriger_champ(identifiant, 'total_ttc', '15.001', chemin_base=self.base)
        self.assertEqual(s.lire_facture(identifiant, self.base)['json_brut'], brut)
        copie = copy.deepcopy(self.document)
        self.enregistrer()
        self.assertEqual(self.document, copie)
        decimal_id = self.enregistrer({'lignes': [{'quantite': Decimal('1.00000000000000000000001')}]})
        self.assertEqual(s.lire_articles(decimal_id, self.base)[0]['quantite'], '1.00000000000000000000001')

    def test_precision_devises(self):
        for devise in ('MAD', 'EUR'):
            with self.subTest(devise=devise):
                with self.assertRaises(ValueError):
                    self.enregistrer({'devise': devise, 'total_ht': '0.001'})
        for devise in ('JPY', 'KWD', None):
            f = s.lire_facture(self.enregistrer({'devise': devise, 'total_ht': '1.12345'}), self.base)
            self.assertEqual(f['total_ht'], '1.12345')
            self.assertIsNone(f['total_ht_centimes'])
        self.assertEqual(s.montant_vers_centimes('-0.01'), -1)
        self.assertEqual(s.montant_vers_centimes('12.3000'), 1230)
        with localcontext() as contexte:
            contexte.prec = 2
            self.assertEqual(s.montant_vers_centimes('123456.78'), 12345678)
        with self.assertRaises(ValueError):
            s.montant_vers_centimes('999999999999999999999')

    def test_nombres_invalides(self):
        for valeur in (True, False, 'NaN', 'Infinity', '-Infinity', float('nan'), float('inf'), {}, ''):
            with self.subTest(valeur=repr(valeur)):
                with self.assertRaises(ValueError):
                    s.nombre_vers_texte(valeur)
        with self.assertRaises(ValueError):
            self.enregistrer('{"total_ttc":NaN}')
        with self.assertRaises(ValueError):
            self.enregistrer({'total_ht': True})

    def test_annulation_complete_article_invalide(self):
        self.document['lignes'][1]['quantite'] = True
        with self.assertRaises(ValueError):
            self.enregistrer()
        self.assertEqual(s.lister_factures(self.base), [])
        with s.ouvrir_connexion(self.base) as c:
            self.assertEqual(c.execute('SELECT count(*) FROM lignes_facture').fetchone()[0], 0)
        c.close()

    def test_cles_etrangeres_et_ajout_article(self):
        with self.assertRaises(sqlite3.IntegrityError):
            s.ajouter_article(999, 1, 'Orphelin', '1.2', '3.4', None, self.base)
        identifiant = self.enregistrer({'lignes': []})
        s.ajouter_article(identifiant, 2, 'B', '1.2', '3.4', None, self.base)
        s.ajouter_article(identifiant, 1, 'A', '2.3', None, None, self.base)
        self.assertEqual([a['designation'] for a in s.lire_articles(identifiant, self.base)], ['A', 'B'])
        with self.assertRaises(ValueError):
            s.ajouter_article(identifiant, 3, 'C', True, None, None, self.base)

    def test_correction_historique(self):
        identifiant = self.enregistrer()
        brut = s.lire_facture(identifiant, self.base)['json_brut']
        validation = s.corriger_champ(identifiant, 'total_ht', '20.50', 'Alice fictive', self.base)
        f = s.lire_facture(identifiant, self.base)
        historique = s.lire_validations(identifiant, self.base)
        self.assertEqual(f['total_ht'], '20.50')
        self.assertEqual(f['total_ht_centimes'], 2050)
        self.assertEqual(f['statut'], 'partiel')
        self.assertEqual(f['json_brut'], brut)
        h = historique[0]
        self.assertEqual((h['id'], h['ancienne_valeur'], h['nouvelle_valeur'], h['auteur']),
                         (validation, '12.30', '20.50', 'Alice fictive'))
        self.assertTrue(h['date_action'].endswith('+00:00'))
        self.assertEqual(h['action'], 'correction')
        s.corriger_champ(identifiant, 'total_ht', None, chemin_base=self.base)
        self.assertIsNone(s.lire_facture(identifiant, self.base)['total_ht_centimes'])
        self.assertEqual(s.lire_validations(identifiant, self.base)[1]['ancienne_valeur'], '20.50')

    def test_corrections_refusees(self):
        identifiant = self.enregistrer()
        for champ, valeur in (('json_brut', '{}'), ('id', 2), ('client = NULL;--', 'x'),
                              ('client', 12), ('total_ht', True), ('total_ht', '0.001'),
                              ('statut', 'confirmee')):
            with self.subTest(champ=champ, valeur=valeur):
                with self.assertRaises(ValueError):
                    s.corriger_champ(identifiant, champ, valeur, chemin_base=self.base)
        with self.assertRaises(ValueError):
            s.corriger_champ(999, 'client', 'Inconnu', chemin_base=self.base)
        self.assertEqual(s.lire_validations(identifiant, self.base), [])

    def test_annulation_si_historique_echoue(self):
        identifiant = self.enregistrer()
        with s.ouvrir_connexion(self.base) as c:
            c.execute("CREATE TRIGGER echec BEFORE INSERT ON validations BEGIN SELECT RAISE(ABORT, 'test'); END")
        c.close()
        with self.assertRaises(sqlite3.IntegrityError):
            s.corriger_champ(identifiant, 'client', 'Changement', chemin_base=self.base)
        self.assertEqual(s.lire_facture(identifiant, self.base)['client'], 'Client fictif')
        self.assertEqual(s.lire_validations(identifiant, self.base), [])

    def test_correction_devise(self):
        identifiant = self.enregistrer({'devise': 'KWD', 'total_ht': '0.001'})
        with self.assertRaises(ValueError):
            s.corriger_champ(identifiant, 'devise', 'MAD', chemin_base=self.base)
        self.assertEqual(s.lire_facture(identifiant, self.base)['devise'], 'KWD')
        self.assertEqual(s.lire_validations(identifiant, self.base), [])
        s.corriger_champ(identifiant, 'total_ht', '0.01', chemin_base=self.base)
        s.corriger_champ(identifiant, 'devise', 'EUR', chemin_base=self.base)
        self.assertEqual(s.lire_facture(identifiant, self.base)['total_ht_centimes'], 1)
        s.corriger_champ(identifiant, 'devise', 'JPY', chemin_base=self.base)
        self.assertIsNone(s.lire_facture(identifiant, self.base)['total_ht_centimes'])

    def test_initialisation_repetee(self):
        identifiant = self.enregistrer()
        s.corriger_champ(identifiant, 'ice_client', 'CORRIGE', chemin_base=self.base)
        avant = s.lire_facture(identifiant, self.base)
        for _ in range(3):
            s.initialiser_base(self.base)
        self.assertEqual(s.lire_facture(identifiant, self.base), avant)
        self.assertEqual(len(s.lire_validations(identifiant, self.base)), 1)

    def test_migration_schema_initial(self):
        ancien = Path(self.temp.name) / 'ancien.db'
        c = sqlite3.connect(ancien)
        c.executescript(SCHEMA_INITIAL)
        brut = json.dumps(self.document)
        c.execute('INSERT INTO factures (numero_document, total_ht_centimes, json_brut) VALUES (?, ?, ?)',
                  ('ANCIEN', 1230, brut))
        c.execute('INSERT INTO lignes_facture (facture_id, numero_ligne, designation) VALUES (1, 1, ?)', ('Premier',))
        c.commit()
        c.close()
        s.initialiser_base(ancien)
        f = s.lire_facture(1, ancien)
        self.assertEqual(f['numero_document'], 'ANCIEN')
        self.assertEqual(f['ice_client'], '009876')
        self.assertEqual(f['total_ht'], '12.30')
        self.assertEqual(f['total_ht_centimes'], 1230)
        self.assertEqual(f['json_brut'], brut)
        self.assertEqual(f['lignes'][0]['hauteur_cm'], '12.5')
        s.initialiser_base(ancien)
        self.assertEqual(s.lire_facture(1, ancien), f)

    def test_migration_annulee_si_json_historique_invalide(self):
        ancien = Path(self.temp.name) / 'migration_invalide.db'
        c = sqlite3.connect(ancien)
        c.executescript(SCHEMA_INITIAL)
        c.execute('INSERT INTO factures (json_brut) VALUES (?)', ('invalide',))
        c.commit()
        colonnes_avant = c.execute('PRAGMA table_info(factures)').fetchall()
        c.close()
        with self.assertRaises(ValueError):
            s.initialiser_base(ancien)
        c = sqlite3.connect(ancien)
        self.assertEqual(c.execute('PRAGMA table_info(factures)').fetchall(), colonnes_avant)
        self.assertEqual(c.execute('SELECT json_brut FROM factures').fetchone()[0], 'invalide')
        c.close()

    def test_types_articles_et_structure(self):
        for document in ([], {'lignes': {}}, {'lignes': [None]},
                         {'lignes': [{'page': True}]}, {'client': 123},
                         {'lignes': [{'quantite': 'Infinity'}]}):
            with self.subTest(document=document):
                with self.assertRaises(ValueError):
                    self.enregistrer(document)
        self.assertEqual(s.lister_factures(self.base), [])

    def test_statut_absent_et_migration_ancien_statut(self):
        identifiant = self.enregistrer({'lignes': []})
        self.assertIsNone(s.lire_facture(identifiant, self.base)['statut'])
        ancien = Path(self.temp.name) / 'statut_ancien.db'
        c = sqlite3.connect(ancien)
        c.executescript(SCHEMA_INITIAL)
        c.execute("INSERT INTO factures (statut, json_brut) VALUES (?, ?)", ('partiel', '{}'))
        c.commit()
        c.close()
        s.initialiser_base(ancien)
        self.assertEqual(s.lire_facture(1, ancien)['statut'], 'partiel')
        nouveau = s.enregistrer_facture({'lignes': []}, ancien)
        self.assertIsNone(s.lire_facture(nouveau, ancien)['statut'])
        s.corriger_champ(1, 'statut', None, chemin_base=ancien)
        self.assertIsNone(s.lire_facture(1, ancien)['statut'])
        self.assertEqual(s.lire_validations(1, ancien)[0]['ancienne_valeur'], 'partiel')
        s.initialiser_base(ancien)
        self.assertIsNone(s.lire_facture(1, ancien)['statut'])
        self.assertIsNone(s.lire_facture(nouveau, ancien)['statut'])

    def test_precision_total_generique_et_champ_invalide(self):
        for devise in ('MAD', 'EUR', 'mad'):
            with self.assertRaises(ValueError):
                self.enregistrer({'devise': devise, 'total_document': '0.001'})
        identifiant = self.enregistrer({'devise': 'KWD', 'total_document': '0.001'})
        with self.assertRaises(ValueError):
            s.corriger_champ(identifiant, 'devise', 'EUR', chemin_base=self.base)
        with self.assertRaises(ValueError):
            s.corriger_champ(identifiant, [], 'x', chemin_base=self.base)
        autre = self.enregistrer({'devise': 'EUR'})
        with self.assertRaises(ValueError):
            s.corriger_champ(autre, 'total_document', '0.001', chemin_base=self.base)
        self.assertEqual(s.lire_validations(identifiant, self.base), [])
        self.assertEqual(s.lire_validations(autre, self.base), [])

    def test_import_sans_effet(self):
        from DocumentAI_Web import essai_stockage
        with patch('sqlite3.connect', side_effect=AssertionError('Connexion pendant import')):
            importlib.reload(s)
            importlib.reload(essai_stockage)


if __name__ == '__main__':
    unittest.main()
