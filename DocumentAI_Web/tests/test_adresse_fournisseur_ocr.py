"""Adresse du secours local : archive complete et dispositions synthetiques."""
import copy
import json
import socket
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from moteur.extraction_ocr_prioritaire import direct, corriger_adresse_fournisseur
from moteur.extraction_vision import validate_output
from moteur.validation_donnees import valider_calculs


class AdresseFournisseurTests(unittest.TestCase):
    def setUp(self):
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('Reseau interdit'))
        guard.start();self.addCleanup(guard.stop)

    def test_archive_complete_nom_totaux_et_preuve(self):
        r=json.loads((Path(__file__).parent/'fixtures/demo_2025_00072_adresse_ocr.json').read_text(encoding='utf-8'))
        before=copy.deepcopy(r);data,proofs,alerts=direct(r,1)
        self.assertEqual(data['fournisseur'],r['rec_texts'][7])
        self.assertEqual(data['adresse_fournisseur'],r['rec_texts'][9])
        self.assertNotEqual(data['adresse_fournisseur'],r['rec_texts'][8])
        self.assertEqual(proofs['adresse_fournisseur'][0]['blocs_ocr'],[10])
        self.assertTrue(proofs['adresse_fournisseur'][0]['corroboration_ocr'])
        self.assertEqual([data[k] for k in ['total_ht','montant_tva','total_ttc']],[1719.99,344,2063.99])
        self.assertEqual(valider_calculs(data)[-1]['etat'],'coherent')
        self.assertEqual(r,before)

    def fixture(self,mirror=False,adresse=True,arabe=False):
        entries=[('المورد' if arabe else 'Fournisseur',[600,100,800,130]),
                 ('Fournisseur Exemple SARL',[600,140,950,170]),
                 ('Téléphone : +212 500000000',[600,180,950,210]),
                 ('ICE: 123456789012345',[600,260,950,290]),
                 ('الزبون' if arabe else 'Client',[50,100,200,130]),
                 ('Client Exemple',[50,140,350,170]),
                 ('99 avenue Client',[50,220,350,250]),
                 ('Désignation / البيان',[50,400,350,430])]
        if adresse:entries.append(('12 rue des Fleurs, Casablanca',[600,220,950,250]))
        if mirror:entries=[(t,[1000-b[2],b[1],1000-b[0],b[3]]) for t,b in entries]
        return dict(rec_texts=[t for t,b in entries],rec_boxes=[b for t,b in entries])

    def adresse_seule(self,r):
        # Nom deja etabli : tester l'adresse sans modifier les heuristiques de nom.
        prepared=dict(fournisseur=r['rec_texts'][1],lignes=[],preuves=[dict(
            champ='fournisseur',texte_visible=r['rec_texts'][1],blocs_ocr=[2])])
        alerts=corriger_adresse_fournisseur(prepared,r)
        data,proofs,notices=validate_output(prepared,r,1)
        return data,proofs,alerts+notices

    def test_adresse_entre_telephone_et_ice_des_deux_cotes(self):
        for mirror in [False,True]:
            for arabe in [False,True]:
                with self.subTest(mirror=mirror,arabe=arabe):
                    data,proofs,_=self.adresse_seule(self.fixture(mirror=mirror,arabe=arabe))
                    self.assertEqual(data['fournisseur'],'Fournisseur Exemple SARL')
                    self.assertEqual(data['adresse_fournisseur'],'12 rue des Fleurs, Casablanca')
                    self.assertTrue(proofs['adresse_fournisseur'][0]['corroboration_ocr'])

    def test_ne_prend_pas_adresse_client(self):
        for mirror in [False,True]:
            data,_,alerts=self.adresse_seule(self.fixture(mirror=mirror,adresse=False))
            self.assertIsNone(data['adresse_fournisseur'])
            self.assertTrue(any('Adresse fournisseur non extraite' in a for a in alerts))

    def test_plusieurs_adresses_dans_zone_ambigue(self):
        r=self.fixture();r['rec_texts'].append('15 avenue Autre');r['rec_boxes'].append([600,300,950,330])
        data,_,alerts=self.adresse_seule(r)
        self.assertIsNone(data['adresse_fournisseur'])
        self.assertTrue(any('Adresse fournisseur non extraite' in a for a in alerts))


if __name__=='__main__':unittest.main()
