"""Requetes et reponses simulees : aucun OCR ni appel API reel."""
import base64
import json
import socket
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from moteur import extraction_gemini as gemini
import traitement_document as pipeline


def raw(text):
    return dict(rec_texts=[text],rec_boxes=[[0,0,10,10]])


def response():
    data={k:None for k in gemini.TEXT_FIELDS+gemini.NUM_FIELDS}
    data.update(type_document='facture',fournisseur='Nom visible uniquement sur image',
                lignes=[],incertitudes=['total_ttc : chiffre illisible, lectures divergentes.'])
    return {'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':json.dumps(data)}]}}]}


class LecturesTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.base=Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.object(socket.socket,'connect',side_effect=AssertionError('Reseau interdit')))
        self.stack.enter_context(patch.object(gemini,'urlopen',side_effect=AssertionError('API interdite')))
        self.stack.enter_context(patch.object(gemini,'configuration',return_value=('cle_fictive_a_ne_pas_archiver','modele-fictif')))
        self.image=self.stack.enter_context(patch.object(gemini,'page_png',return_value=b'image-fictive'))
        self.request=self.stack.enter_context(patch.object(gemini,'request_json',return_value=response()))

    def sections(self,payload):
        parts=payload['contents'][0]['parts']
        images=[p for p in parts if 'inlineData' in p]
        self.assertEqual(len(images),1)
        self.assertEqual(base64.b64decode(images[0]['inlineData']['data']),b'image-fictive')
        return [json.loads(p['text'].split(' : ',1)[1]) for p in parts if 'text' in p]

    def test_deux_lectures_et_diagnostic(self):
        fr=raw('Texte francais');ar={'res':raw('?? ????')}
        gemini.extract('fictif.pdf',1,fr,self.base,lectures_ocr={'fr':fr,'ar':ar})
        payload=self.request.call_args.args[1];sections=self.sections(payload)
        self.assertEqual([s['langue'] for s in sections],['fr','ar'])
        self.assertEqual([s['blocs'][0]['texte'] for s in sections],['Texte francais','?? ????'])
        self.assertEqual(payload['generationConfig']['responseJsonSchema'],gemini.output_schema())
        self.assertEqual(payload['generationConfig']['maxOutputTokens'],32768)
        self.assertEqual(payload['generationConfig']['thinkingConfig'],{'thinkingBudget':0})
        archive=json.loads((self.base/'requete_sans_image.json').read_text(encoding='utf-8'))
        self.assertEqual(archive['lectures_ocr'],sections)
        self.assertEqual(archive['blocs'],sections[0]['blocs'])
        self.assertNotIn('cle_fictive_a_ne_pas_archiver',json.dumps(archive))
        self.assertNotIn('inlineData',archive)
        self.image.assert_called_once_with('fictif.pdf',1)
        self.request.assert_called_once()

    def test_raw_seul_compatible(self):
        gemini.extract('fictif.pdf',0,raw('Lecture unique'),self.base)
        sections=self.sections(self.request.call_args.args[1])
        self.assertEqual(len(sections),1)
        self.assertEqual(sections[0]['langue'],'primaire')
        self.assertEqual(sections[0]['blocs'][0]['texte'],'Lecture unique')

    def test_texte_sans_coordonnees(self):
        lecture=dict(rec_texts=['Avec boite','Sans boite'],rec_boxes=[[0,0,10,10]])
        gemini.extract('fictif.pdf',0,lecture,self.base,lectures_ocr={'fr':lecture})
        blocks=self.sections(self.request.call_args.args[1])[0]['blocs']
        self.assertEqual(blocks[1],dict(id=2,texte='Sans boite',bbox=None))
        self.assertEqual(gemini.ocr_blocks({'rec_texts':['Seul']}),[dict(id=1,texte='Seul',bbox=None)])

    def test_valeur_visuelle_et_ambiguite(self):
        data,proofs,alerts=gemini.extract('fictif.pdf',0,raw('Texte different'),self.base)
        self.assertEqual(data['fournisseur'],'Nom visible uniquement sur image')
        self.assertEqual(proofs['fournisseur']['blocs_ocr_correspondance_textuelle'],[])
        self.assertIsNone(data['total_ttc'])
        self.assertIn('total_ttc : chiffre illisible, lectures divergentes.',alerts)

    def pages(self):
        pages={}
        for index in range(2):
            pages[index]={}
            for langue in ['fr','ar']:
                p=self.base/f'{index}_{langue}.json'
                # L'arabe ne franchit pas le seuil de choose : seul FR est retenu.
                p.write_text(json.dumps(raw(f'Page {index+1} lecture {langue}')),encoding='utf-8')
                pages[index][langue]=p
        return pages

    def test_process_pages_transmet_toutes_lectures_page_courante(self):
        pages=self.pages()
        self.assertIsNone(pipeline.choose(pages[0])[1])
        with patch.object(pipeline,'load_dimensions',return_value=None):
            pipeline.process_pages(pages,self.base,False,Path('fictif.pdf'))
        self.assertEqual(self.request.call_count,2)
        for index,call in enumerate(self.request.call_args_list):
            sections=self.sections(call.args[1])
            self.assertEqual([s['langue'] for s in sections],['fr','ar'])
            self.assertEqual([s['blocs'][0]['texte'] for s in sections],
                             [f'Page {index+1} lecture fr',f'Page {index+1} lecture ar'])

    def test_secours_conserve_ocr_primaire(self):
        pages=self.pages();expected=pipeline.read(pages[0]['fr'])
        with patch.object(pipeline,'load_dimensions',return_value=None), \
             patch.object(gemini,'extract',side_effect=RuntimeError('Panne simulee')), \
             patch('moteur.extraction_ocr_prioritaire.direct',return_value=({}, {}, [])) as direct:
            pipeline.process_pages({0:pages[0]},self.base,False,Path('fictif.pdf'))
        direct.assert_called_once_with(expected,1)

    def test_checked_output_conserve_lignes_partielles_et_ignore_ligne_vide(self):
        payload={
            'lignes':[
                {'designation':'Article sans qte','quantite':None,'montant_ht':120.0},
                {'designation':None,'quantite':None,'montant_ht':None},
                {'designation':'صيانة','quantite':2,'prix_unitaire_ht':50.0,'montant_ht':100.0},
            ]
        }
        data,_,alerts=gemini.checked_output(payload,raw('صيانة'),1)
        self.assertEqual(len(data['lignes']),2)
        self.assertEqual(data['lignes'][0]['designation'],'Article sans qte')
        self.assertEqual(data['lignes'][1]['designation'],'صيانة')
        self.assertTrue(any('entierement vide' in a for a in alerts))


if __name__=='__main__':unittest.main()
