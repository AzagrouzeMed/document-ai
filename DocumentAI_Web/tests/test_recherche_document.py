"""Archives et recherche documentaire : donnees simulees, SQLite temporaire."""
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import json
import socket
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app as web
from fastapi.testclient import TestClient


class RechercheDocumentTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.data=Path(temp.name)
        self.base=self.data/'document_ai.db'
        self.s=web.stockage
        self.s.initialiser_base(self.base)
        self.traitement='a'*32
        self.document={'document_id':'DOC-SIMULE','numero_document':'FACT-001','statut':'partiel','lignes':[{'designation':'Article'}]}
        self.brut=json.dumps(self.document,ensure_ascii=False)
        self.identifiant=self.s.enregistrer_facture(self.brut,self.base,traitement_id=self.traitement)
        self.pages=[
            dict(numero_page=1,langue='fr',texte_ocr='Facture originale\nLivraison de caf\u00e9 et mat\u00e9riel.',source='ocr/page_1_fr.json'),
            dict(numero_page=1,langue='ar',texte_ocr='\u0641\u0627\u062a\u0648\u0631\u0629 \u062a\u0633\u0644\u064a\u0645 \u0627\u0644\u0642\u0647\u0648\u0629',source='ocr/page_1_ar.json'),
            dict(numero_page=2,langue='fr',texte_ocr='Conditions de garantie\nMat\u00e9riel <b>fragile</b>. Prix 100%.',source='ocr/page_2_fr.json'),
        ]
        for mocker in (
            patch.object(web,'DATA',self.data),
            patch.object(web.subprocess,'run',side_effect=AssertionError('Extraction interdite')),
            patch.object(socket,'create_connection',side_effect=AssertionError('Reseau interdit')),
            patch.object(web,'SLOTS'),
            patch.object(web,'export_pdf',side_effect=lambda d,c,p:p.write_bytes(b'PDF simule')),
        ):
            mocker.start();self.addCleanup(mocker.stop)
        self.client=TestClient(web.app);self.addCleanup(self.client.close)

    def sauvegarder(self,pages=None,identifiant=None,traitement=None):
        return self.s.enregistrer_pages_document(identifiant or self.identifiant,
            self.pages if pages is None else pages,self.base,traitement_id=traitement or self.traitement)

    def rechercher(self,q,identifiant=None):
        return self.client.get(f'/api/factures/{identifiant or self.identifiant}/recherche',params={'q':q})

    def exiger_fts(self):
        if not self.s.fts5_disponible():self.skipTest('FTS5 absent ; le diagnostic est couvert separement.')

    def test_fr_ar_sources_pages_et_lectures_separees(self):
        self.exiger_fts();self.sauvegarder()
        for mot,page,langue,source in [('cafe',1,'fr','ocr/page_1_fr.json'),
                                     ('\u062a\u0633\u0644\u064a\u0645',1,'ar','ocr/page_1_ar.json'),
                                     ('garantie',2,'fr','ocr/page_2_fr.json')]:
            with self.subTest(mot=mot):
                response=self.rechercher(mot);self.assertEqual(response.status_code,200)
                resultat=response.json();self.assertEqual(resultat['etat'],'resultats')
                self.assertEqual(len(resultat['passages']),1)
                passage=resultat['passages'][0]
                self.assertEqual((passage['numero_page'],passage['langue'],passage['source']),(page,langue,source))
                self.assertIn(passage['passage'],next(p['texte_ocr'] for p in self.pages if p['source']==source))
                self.assertNotIn(str(self.data),response.text)
        self.assertIn('<b>fragile</b>',self.rechercher('fragile').json()['passages'][0]['passage'])

    def test_separation_factures_meme_numero(self):
        self.exiger_fts();self.sauvegarder()
        autre=self.s.enregistrer_facture(self.document,self.base,traitement_id='b'*32)
        self.sauvegarder([dict(numero_page=8,langue='fr',texte_ocr='Confidentiel autre facture cafe',source='ocr/page_8_fr.json')],autre,'b'*32)
        self.assertEqual(self.rechercher('confidentiel').json()['etat'],'aucun_resultat')
        passage=self.rechercher('cafe',autre).json()['passages'][0]
        self.assertEqual(passage['numero_page'],8)
        self.assertIn('Confidentiel',passage['passage'])

    def test_absence_texte_ancienne_facture_sans_lien_et_absence_resultat(self):
        self.assertEqual(self.rechercher('cafe').json()['message'],'Aucun texte OCR enregistr\u00e9')
        ancien=self.s.enregistrer_facture(self.document,self.base)
        self.assertEqual(self.rechercher('cafe',ancien).json()['etat'],'aucun_texte')
        with self.assertRaises(ValueError):
            self.sauvegarder(self.pages,ancien)
        self.exiger_fts();self.sauvegarder()
        self.assertEqual(self.rechercher('introuvable').json()['message'],'Aucun passage trouv\u00e9')

    def test_caracteres_speciaux_operateurs_litteraux_et_validation(self):
        self.exiger_fts();self.sauvegarder()
        for q in ('"caf\u00e9"*','caf\u00e9 + livraison','100%'):
            response=self.rechercher(q)
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json()['etat'],'resultats')
        self.assertEqual(self.rechercher('cafe OR absent').json()['etat'],'aucun_resultat')
        self.assertEqual(self.rechercher("'; DROP TABLE factures; --").status_code,200)
        for q in ('','   ','* % \" [] -','x'*201,' '.join(['a']*21)):
            self.assertEqual(self.rechercher(q).status_code,422)
        self.assertEqual(len(self.s.lister_factures(self.base)),1)

    def test_sauvegarde_repetee_migration_reexecutable_et_json_inchange(self):
        self.exiger_fts();self.sauvegarder()
        self.s.corriger_champ(self.identifiant,'client','Corrige',chemin_base=self.base)
        avant=self.s.lire_facture(self.identifiant,self.base)
        for _ in range(3):
            self.s.initialiser_base(self.base)
            self.assertEqual(self.sauvegarder(),3)
        self.assertEqual(self.s.lire_facture(self.identifiant,self.base),avant)
        self.assertEqual(avant['json_brut'],self.brut)
        self.assertEqual(len(self.s.lire_validations(self.identifiant,self.base)),1)
        self.assertEqual(len(self.rechercher('cafe').json()['passages']),1)
        with self.s.ouvrir_connexion(self.base) as c:
            self.assertEqual(c.execute('SELECT count(*) FROM pages_document').fetchone()[0],3)
        c.close()
        # Ne pas remplacer une lecture deja archivee par une version differente.
        self.sauvegarder([{**self.pages[0],'texte_ocr':'Remplacement'}])
        self.assertEqual(self.rechercher('cafe').json()['etat'],'resultats')

    def test_sauvegarde_atomique_et_lien_etranger(self):
        with self.assertRaises(ValueError):
            self.sauvegarder([self.pages[0],{**self.pages[1],'source':'C:/prive.json'}])
        with self.s.ouvrir_connexion(self.base) as c:
            self.assertEqual(c.execute('SELECT count(*) FROM pages_document').fetchone()[0],0)
        c.close()
        with self.assertRaises(ValueError):self.sauvegarder(traitement='mauvais')
        with self.assertRaises(ValueError):self.sauvegarder(identifiant=999999)

    def test_recherche_sans_ecriture_et_questions_conservees(self):
        self.exiger_fts();self.sauvegarder();avant=self.base.read_bytes()
        with ExitStack() as stack:
            for nom in ('initialiser_base','enregistrer_facture','enregistrer_pages_document','corriger_champ','ajouter_article'):
                stack.enter_context(patch.object(self.s,nom,side_effect=AssertionError('Ecriture interdite')))
            for _ in range(2):
                self.assertEqual(self.rechercher('cafe').status_code,200)
                self.assertEqual(self.rechercher('absent').json()['etat'],'aucun_resultat')
                for code in web.QUESTIONS_FACTURE:
                    self.assertEqual(self.client.get(f'/api/factures/{self.identifiant}/questions/{code}').status_code,200)
        self.assertEqual(self.base.read_bytes(),avant)

    def test_facture_inexistante_erreur_sqlite_et_schema_sans_pages(self):
        self.assertEqual(self.rechercher('cafe',999999).status_code,404)
        with patch.object(web,'DATA',self.data/'absent'):
            self.assertEqual(self.rechercher('cafe').status_code,404)
        self.assertFalse((self.data/'absent').exists())
        # Une ancienne base sans la nouvelle migration reste consultable sans ecriture.
        ancienne=self.data/'ancienne';ancienne.mkdir();base=ancienne/'document_ai.db'
        self.s.initialiser_base(base)
        identifiant=self.s.enregistrer_facture(self.document,base)
        c=self.s.ouvrir_connexion(base)
        with c:c.execute('DROP TABLE pages_document')
        c.close();avant=base.read_bytes()
        with patch.object(web,'DATA',ancienne):
            self.assertEqual(self.rechercher('cafe',identifiant).json()['etat'],'aucun_texte')
        self.assertEqual(base.read_bytes(),avant)
        with patch.object(self.s,'rechercher_pages_document',side_effect=sqlite3.OperationalError('prive')):
            response=self.rechercher('cafe')
        self.assertEqual(response.status_code,503);self.assertNotIn('prive',response.text)

    def test_fts_absent_textes_conserves_et_reprise_index(self):
        autre=self.data/'sans_fts.db'
        with patch.object(self.s,'fts5_disponible',return_value=False):
            self.s.initialiser_base(autre)
        identifiant=self.s.enregistrer_facture(self.document,autre,traitement_id=self.traitement)
        self.s.enregistrer_pages_document(identifiant,self.pages,autre,traitement_id=self.traitement)
        resultat=self.s.rechercher_pages_document(identifiant,'cafe',autre)
        self.assertEqual(resultat['etat'],'fts5_indisponible')
        self.assertIn('FTS5',resultat['message'])
        self.assertEqual(self.s.lire_facture(identifiant,autre)['numero_document'],'FACT-001')
        if self.s.fts5_disponible():
            self.s.initialiser_base(autre)
            self.assertEqual(self.s.rechercher_pages_document(identifiant,'cafe',autre)['etat'],'resultats')
        self.sauvegarder()
        with patch.object(self.s,'fts5_disponible',return_value=False):
            response=self.rechercher('cafe')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['etat'],'fts5_indisponible')

    def preparer_archives(self):
        p=self.data/self.traitement;p.mkdir()
        source=p/'source.pdf';source.write_bytes(b'Original fictif')
        web.write_status(p,id=p.name,nom_fichier='Fictive.pdf',source='source.pdf',etat='attente')
        run=p/'resultats'/'DOC-SIMULE';(run/'ocr').mkdir(parents=True)
        for page in self.pages:
            raw=dict(page_index=page['numero_page']-1,rec_texts=page['texte_ocr'].split('\n'),input_path='C:/prive.pdf')
            if page['langue']=='ar':raw={'res':raw}
            (run/page['source']).write_text(json.dumps(raw,ensure_ascii=False),encoding='utf-8')
        return p,source,run

    def test_worker_sauvegarde_nouveaux_traitements_et_reprise_sans_doublon(self):
        self.exiger_fts();p,source,run=self.preparer_archives()
        def extraction_simulee(*args,**kwargs):
            (run/'document.json').write_text(self.brut,encoding='utf-8')
            (run/'controles.json').write_text('{"alertes":[]}',encoding='utf-8')
            return SimpleNamespace(returncode=0)
        with patch.object(web.subprocess,'run',side_effect=extraction_simulee):web.worker(p,source,'auto',True)
        statut=web.load(p/'statut.json')
        self.assertEqual(statut['etat'],'termine')
        self.assertEqual(statut['sauvegarde']['facture_id'],self.identifiant)
        self.assertEqual(statut['sauvegarde']['textes_ocr']['nombre_lectures'],3)
        for _ in range(2):self.assertEqual(web.sauvegarder_resultat(p,source,self.brut)['textes_ocr']['nombre_lectures'],3)
        self.assertEqual(len(self.s.lister_factures(self.base)),1)
        self.assertEqual(self.rechercher('garantie').json()['passages'][0]['numero_page'],2)

    def test_erreur_archivage_ne_masque_pas_facture_ou_resultats(self):
        p,source,run=self.preparer_archives()
        with patch.object(self.s,'enregistrer_pages_document',side_effect=sqlite3.OperationalError('simule')):
            statut=web.sauvegarder_resultat(p,source,self.brut,run=run)
        self.assertEqual(statut['etat'],'enregistre')
        self.assertEqual(statut['textes_ocr']['etat'],'echec')
        self.assertTrue((run/'ocr'/'page_1_fr.json').is_file())
        self.assertEqual(self.s.lire_facture(self.identifiant,self.base)['json_brut'],self.brut)
        self.assertTrue((p/'stockage_ocr_erreur.log').is_file())

    def test_archive_page_incoherente_refusee_sans_inventer_langue(self):
        p,source,run=self.preparer_archives()
        (run/'ocr'/'page_1_fr.json').write_text('{"page_index":4,"rec_texts":["erreur"]}',encoding='utf-8')
        self.assertEqual(web.sauvegarder_resultat(p,source,self.brut,run=run)['textes_ocr']['etat'],'echec')
        # Le fichier texte agrege seul ne permet pas de deviner les pages et langues.
        vide=p/'vide';vide.mkdir();(vide/'texte_ocr.txt').write_text('Sans provenance',encoding='utf-8')
        self.assertEqual(web.lire_archives_ocr(vide),[])


if __name__=='__main__':unittest.main()
