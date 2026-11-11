"""Corrections d'en-tete et historique : SQLite temporaire, aucune extraction."""
import json
import socket
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app as web
from fastapi.testclient import TestClient


class CorrectionsFacturesTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.data=Path(temp.name)
        self.base=self.data/'document_ai.db'
        web.stockage.initialiser_base(self.base)
        self.brut=' {"fournisseur":"Avant", "client":null, "statut":"partiel", "lignes":[{"designation":"Article"}]}\n'
        self.identifiant=web.stockage.enregistrer_facture(self.brut,self.base)
        for mocker in (
            patch.object(web,'DATA',self.data),
            patch.object(web,'worker',side_effect=AssertionError('Extraction interdite')),
            patch.object(web.subprocess,'run',side_effect=AssertionError('Extraction interdite')),
            patch.object(socket,'create_connection',side_effect=AssertionError('Reseau interdit')),
        ):
            mocker.start()
            self.addCleanup(mocker.stop)
        self.client=TestClient(web.app)
        self.addCleanup(self.client.close)
        self.url=f'/api/factures/{self.identifiant}'
        self.headers={'X-DocumentAI-Token':web.TOKEN}

    def corriger(self,champ='fournisseur',valeur='Apr\u00e8s',identifiant=None):
        url=self.url if identifiant is None else f'/api/factures/{identifiant}'
        return self.client.post(url+'/corrections',json={'champ':champ,'nouvelle_valeur':valeur},headers=self.headers)

    def historique(self):
        response=self.client.get(self.url+'/historique')
        self.assertEqual(response.status_code,200)
        return response.json()

    def test_correction_persistante_historique_statut_et_json_brut(self):
        self.assertEqual(self.historique(),[])
        with patch.object(web.stockage,'corriger_champ',wraps=web.stockage.corriger_champ) as corriger:
            response=self.corriger(valeur='\u0634\u0631\u0643\u0629 <b>\u00c9t\u00e9</b>')
            self.assertEqual(response.status_code,200)
            corriger.assert_called_once()
        facture=web.stockage.lire_facture(self.identifiant,self.base)
        self.assertEqual(facture['fournisseur'],'\u0634\u0631\u0643\u0629 <b>\u00c9t\u00e9</b>')
        self.assertEqual(facture['statut'],'partiel')
        self.assertEqual(facture['json_brut'],self.brut)
        self.assertEqual(facture['lignes'][0]['designation'],'Article')
        historique=self.historique()
        self.assertEqual(len(historique),1)
        correction=historique[0]
        self.assertEqual(correction['id'],response.json()['validation_id'])
        self.assertEqual(correction['ancienne_valeur'],'Avant')
        self.assertEqual(correction['nouvelle_valeur'],facture['fournisseur'])
        self.assertEqual(correction['champ'],'fournisseur')
        self.assertEqual(correction['action'],'correction')
        self.assertTrue(correction['date_action'].endswith('+00:00'))
        self.assertEqual(self.client.get(self.url).json()['fournisseur'],facture['fournisseur'])
        self.assertEqual(self.client.get('/api/factures').json()[0]['fournisseur'],facture['fournisseur'])

    def test_vider_champ_et_ancienne_valeur_absente(self):
        self.assertEqual(self.corriger('client','Nouveau client').status_code,200)
        self.assertEqual(self.corriger('client','   ').status_code,200)
        self.assertIsNone(web.stockage.lire_facture(self.identifiant,self.base)['client'])
        historique=self.historique()
        self.assertIsNone(historique[0]['ancienne_valeur'])
        self.assertEqual(historique[1]['ancienne_valeur'],'Nouveau client')
        self.assertIsNone(historique[1]['nouvelle_valeur'])
        self.assertEqual(self.corriger('fournisseur',None).status_code,200)
        self.assertIsNone(self.client.get(self.url).json()['fournisseur'])
        self.assertEqual(web.stockage.lire_facture(self.identifiant,self.base)['json_brut'],self.brut)

    def test_champs_interdits_et_donnees_invalides_aucun_changement(self):
        avant=self.base.read_bytes()
        for champ in ('json_brut','statut','total_ht','devise','chemin_original','traitement_id','lignes',
                      'client = NULL;--','id',[],None):
            with self.subTest(champ=champ):
                self.assertEqual(self.corriger(champ,'x').status_code,422)
        for valeur in (True,123,{},[], 'x'*501,'abc\x00def'):
            with self.subTest(valeur=repr(valeur)):
                self.assertEqual(self.corriger('client',valeur).status_code,422)
        for date in ('2026-02-30','09/11/2026','20261109','2026-1-01'):
            self.assertEqual(self.corriger('date_facture',date).status_code,422)
        for payload in ([],{}, {'champ':'client'}, {'champ':'client','nouvelle_valeur':'x','auteur':'inattendu'}):
            self.assertEqual(self.client.post(self.url+'/corrections',json=payload,headers=self.headers).status_code,422)
        self.assertEqual(self.client.post(self.url+'/corrections',content='{',headers=self.headers).status_code,422)
        self.assertEqual(self.base.read_bytes(),avant)
        self.assertEqual(self.historique(),[])

    def test_champs_autorises_dates_et_identifiants_en_texte(self):
        detail=self.client.get(self.url).json()
        self.assertEqual(set(detail['champs_modifiables']),set(web.CHAMPS_ENTETE))
        for champ in web.CHAMPS_ENTETE:
            valeur='2026-11-09' if champ.startswith('date_') else '001234'
            self.assertEqual(self.corriger(champ,valeur).status_code,200)
            self.assertEqual(self.client.get(self.url).json()[champ],valeur)
        self.assertEqual(len(self.historique()),len(web.CHAMPS_ENTETE))

    def test_facture_inexistante_et_base_absente(self):
        self.assertEqual(self.corriger(identifiant=999999).status_code,404)
        self.assertEqual(self.client.get('/api/factures/999999/historique').status_code,404)
        with patch.object(web,'DATA',self.data/'absent'):
            self.assertEqual(self.corriger().status_code,404)
            self.assertEqual(self.client.get(self.url+'/historique').status_code,404)
        self.assertFalse((self.data/'absent').exists())

    def test_protection_post_et_historique_en_lecture_seule(self):
        response=self.client.post(self.url+'/corrections',json={'champ':'client','nouvelle_valeur':'x'})
        self.assertEqual(response.status_code,403)
        self.corriger()
        avant=self.base.read_bytes()
        with patch.object(web.stockage,'lire_validations',wraps=web.stockage.lire_validations) as lire:
            self.historique()
            self.assertTrue(lire.call_args.kwargs['lecture_seule'])
        self.assertEqual(self.base.read_bytes(),avant)

    def test_echec_historique_annule_la_correction_sans_diagnostic_public(self):
        c=web.stockage.ouvrir_connexion(self.base)
        with c:
            c.execute("CREATE TRIGGER echec BEFORE INSERT ON validations BEGIN SELECT RAISE(ABORT, 'diagnostic prive'); END")
        c.close()
        response=self.corriger()
        self.assertEqual(response.status_code,503)
        self.assertNotIn('diagnostic prive',response.text)
        facture=web.stockage.lire_facture(self.identifiant,self.base)
        self.assertEqual(facture['fournisseur'],'Avant')
        self.assertEqual(facture['json_brut'],self.brut)
        self.assertEqual(self.historique(),[])
        with patch.object(web.stockage,'lire_validations',side_effect=sqlite3.OperationalError('prive')):
            response=self.client.get(self.url+'/historique')
        self.assertEqual(response.status_code,503)
        self.assertNotIn('prive',response.text)


if __name__=='__main__':
    unittest.main()
