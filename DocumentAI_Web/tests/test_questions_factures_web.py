"""Questions structurees : SQLite temporaire, aucune extraction ni ecriture."""
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
import socket
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app as web
from fastapi.testclient import TestClient


class QuestionsFacturesTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.data=Path(temp.name)
        self.base=self.data/'document_ai.db'
        web.stockage.initialiser_base(self.base)
        self.document={
            'numero_document':'FACT-001','statut':'partiel',
            'fournisseur':'Atelier \u00c9t\u00e9','client':'\u0634\u0631\u0643\u0629',
            'date':'2026-11-09','total_ttc':'123.40','devise':'MAD',
            'fichier_source':'C:/prive/source.pdf','lignes':[{'designation':'Article'}],
        }
        self.identifiant=web.stockage.enregistrer_facture(self.document,self.base)
        for mocker in (
            patch.object(web,'DATA',self.data),
            patch.object(web,'worker',side_effect=AssertionError('Extraction interdite')),
            patch.object(web.subprocess,'run',side_effect=AssertionError('Sous-processus interdit')),
            patch.object(socket,'create_connection',side_effect=AssertionError('Reseau interdit')),
        ):
            mocker.start()
            self.addCleanup(mocker.stop)
        self.client=TestClient(web.app)
        self.addCleanup(self.client.close)

    def question(self,code,identifiant=None):
        identifiant=self.identifiant if identifiant is None else identifiant
        return self.client.get(f'/api/factures/{identifiant}/questions/{code}')

    def test_quatre_questions_numero_statut_sans_ecriture(self):
        avant=self.base.read_bytes()
        with ExitStack() as stack:
            for nom in ('initialiser_base','enregistrer_facture','corriger_champ','ajouter_article'):
                stack.enter_context(patch.object(web.stockage,nom,side_effect=AssertionError('Ecriture interdite')))
            lire=stack.enter_context(patch.object(web.stockage,'lire_facture',wraps=web.stockage.lire_facture))
            for code,valeur in [('fournisseur',self.document['fournisseur']),('client',self.document['client']),
                                ('date_facture','2026-11-09'),('total_ttc','123.40')]:
                with self.subTest(code=code):
                    response=self.question(code)
                    self.assertEqual(response.status_code,200)
                    resultat=response.json()
                    self.assertEqual(set(resultat),{'facture_id','question','numero_document','statut','valeur','reponse'})
                    self.assertEqual(resultat['valeur'],valeur)
                    self.assertEqual(resultat['question'],code)
                    self.assertEqual(resultat['numero_document'],'FACT-001')
                    self.assertEqual(resultat['statut'],'partiel')
                    self.assertIn(valeur,resultat['reponse'])
                    self.assertIn('FACT-001',resultat['reponse'])
                    self.assertIn('partiel',resultat['reponse'])
                    self.assertIn('reste \u00e0 v\u00e9rifier',resultat['reponse'])
                    self.assertNotIn('C:/prive',response.text)
                    self.assertNotIn('json_brut',response.text)
            self.assertEqual(lire.call_count,4)
            self.assertTrue(all(c.kwargs['lecture_seule'] for c in lire.call_args_list))
        self.assertEqual(self.base.read_bytes(),avant)
        self.assertEqual(web.stockage.lire_validations(self.identifiant,self.base),[])
        self.assertIn('123.40 MAD',self.question('total_ttc').json()['reponse'])

    def test_valeur_absente_numero_statut_absents_et_aucun_total_de_remplacement(self):
        identifiant=web.stockage.enregistrer_facture({'total_document':'999.99','lignes':[]},self.base)
        avant=self.base.read_bytes()
        for code in web.QUESTIONS_FACTURE:
            response=self.question(code,identifiant)
            self.assertEqual(response.status_code,200)
            resultat=response.json()
            self.assertIsNone(resultat['valeur'])
            self.assertIn('Information non disponible',resultat['reponse'])
            self.assertNotIn('999.99',resultat['reponse'])
            self.assertIn('Statut non renseign',resultat['reponse'])
        self.assertEqual(self.base.read_bytes(),avant)

    def test_question_non_autorisee(self):
        avant=self.base.read_bytes()
        with patch.object(web.stockage,'lire_facture',side_effect=AssertionError('Lecture inutile interdite')):
            for code in ('json_brut','chemin_original','total_ht','client%20%3D%20NULL%3B--'):
                self.assertEqual(self.question(code).status_code,422)
        self.assertEqual(self.base.read_bytes(),avant)

    def test_facture_inexistante_et_base_absente(self):
        self.assertEqual(self.question('client',999999).status_code,404)
        absent=self.data/'absent'
        with patch.object(web,'DATA',absent):
            self.assertEqual(self.question('client').status_code,404)
        self.assertFalse(absent.exists())

    def test_valeur_corrigee_lue_dans_sqlite_json_brut_inchange(self):
        brut=web.stockage.lire_facture(self.identifiant,self.base)['json_brut']
        self.assertEqual(self.question('fournisseur').json()['valeur'],self.document['fournisseur'])
        nouveau='\u0634\u0631\u0643\u0629 <b>Corrig\u00e9e</b>'
        response=self.client.post(f'/api/factures/{self.identifiant}/corrections',
            json={'champ':'fournisseur','nouvelle_valeur':nouveau},
            headers={'X-DocumentAI-Token':web.TOKEN})
        self.assertEqual(response.status_code,200)
        avant=self.base.read_bytes()
        resultat=self.question('fournisseur').json()
        self.assertEqual(resultat['valeur'],nouveau)
        self.assertIn(nouveau,resultat['reponse'])
        self.assertEqual(resultat['statut'],'partiel')
        self.assertEqual(self.base.read_bytes(),avant)
        self.assertEqual(web.stockage.lire_facture(self.identifiant,self.base)['json_brut'],brut)
        self.assertEqual(len(web.stockage.lire_validations(self.identifiant,self.base)),1)

    def test_total_zero_precision_exacte_et_statut_a_verifier(self):
        for total in ('0','123.1234567890123456789'):
            identifiant=web.stockage.enregistrer_facture({'statut':'a_verifier','total_ttc':total,'devise':'KWD','lignes':[]},self.base)
            resultat=self.question('total_ttc',identifiant).json()
            self.assertEqual(resultat['valeur'],total)
            self.assertIn(total+' KWD',resultat['reponse'])
            self.assertIn('reste \u00e0 v\u00e9rifier',resultat['reponse'])
            self.assertNotIn('Total TTC : Information non disponible',resultat['reponse'])

    def test_erreur_sqlite_message_public(self):
        with patch.object(web.stockage,'lire_facture',side_effect=sqlite3.OperationalError('C:/prive/base.db')):
            response=self.question('client')
        self.assertEqual(response.status_code,503)
        self.assertIn('indisponible',response.json()['detail'])
        self.assertNotIn('C:/prive',response.text)


if __name__=='__main__':
    unittest.main()
