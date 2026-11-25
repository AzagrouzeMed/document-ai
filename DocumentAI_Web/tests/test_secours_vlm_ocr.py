"""Tests de resilience Gemini (retry/cascade), secours Ollama VLM et parseur OCR bilingue."""
import io
import json
import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from moteur import extraction_gemini as gemini  # noqa: E402
from moteur.extraction_ocr_prioritaire import direct  # noqa: E402
import traitement_document as pipeline  # noqa: E402


def _reponse_ok(designation='Article test'):
    data = {k: None for k in gemini.TEXT_FIELDS + gemini.NUM_FIELDS}
    data.update(
        type_document='facture',
        fournisseur='Fournisseur Test',
        client='Client Test',
        numero_document='F-100',
        date='2025-11-17',
        devise='MAD',
        total_ht=100.0,
        montant_tva=20.0,
        total_ttc=120.0,
        lignes=[{
            'designation': designation,
            'unite': None,
            'quantite': 2.0,
            'prix_unitaire_ht': 50.0,
            'montant_ht': 100.0,
            'prix_unitaire_ttc': None,
            'montant_ttc': None,
            'prix_unitaire': None,
            'montant': None,
            'taux_tva': 20.0,
            'hauteur_cm': None,
            'largeur_cm': None,
        }],
        incertitudes=[],
    )
    return {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': json.dumps(data)}]}}]}


class ResilienceEtSecoursTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('Reseau interdit'))
        guard.start()
        self.addCleanup(guard.stop)

    def test_retry_et_cascade_modeles_sur_503(self):
        # 1. Retry dans request_json sur HTTP 503 puis succes.
        err_503 = HTTPError('https://exemple.invalid', 503, 'Unavailable', {}, io.BytesIO(b'{}'))

        class FakeResp:
            def __init__(self, body):
                self._body = body

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return self._body

        with (
            patch.object(gemini, 'configuration', return_value=('cle-fictive', 'modele-a')),
            patch.object(gemini.time, 'sleep') as sleep_mock,
            patch.object(gemini, 'urlopen', side_effect=[err_503, FakeResp(json.dumps(_reponse_ok()).encode('utf-8'))]),
        ):
            res = gemini.request_json('/models/modele-a:generateContent', {'x': 1})
            self.assertIn('candidates', res)
            sleep_mock.assert_called_once()

        # 2. Cascade de modeles lorsque modele-a reste en 503 -> bascule sur modele-b.
        raw = {'rec_texts': ['Facture', 'Article cascade'], 'rec_boxes': [[0, 0, 10, 10], [0, 20, 100, 30]]}
        calls = []

        def fake_request(path, payload=None):
            calls.append(path)
            if 'modele-a' in path:
                raise RuntimeError('Gemini HTTP 503 : service temporairement indisponible')
            return _reponse_ok('Article cascade')

        with (
            patch.object(gemini, 'configuration', return_value=('cle-fictive', 'modele-a')),
            patch.object(gemini, 'page_png', return_value=b'png'),
            patch.object(gemini, 'request_json', side_effect=fake_request),
            patch.dict(os.environ, {'GEMINI_FALLBACK_MODELS': 'modele-b,modele-c'}),
        ):
            data, _, alerts = gemini.extract('fictif.pdf', 0, raw, self.base / 'cascade')
        self.assertEqual(data['lignes'][0]['designation'], 'Article cascade')
        self.assertEqual(calls, ['/models/modele-a:generateContent', '/models/modele-b:generateContent'])
        self.assertTrue(any('modele-b' in a for a in alerts))

    def test_secours_ollama_vlm_apres_echec_gemini(self):
        source = self.base / 'facture.jpg'
        source.write_bytes(b'img')
        ocr_p = self.base / 'ocr.json'
        ocr_p.write_text(json.dumps({
            'page_index': 0,
            'input_path': 'facture.jpg',
            'rec_texts': ['Facture', 'Produit Ollama'],
            'rec_boxes': [[0, 0, 50, 20], [0, 40, 100, 60]],
        }), encoding='utf-8')
        ollama_json = json.loads(_reponse_ok('Produit Ollama')['candidates'][0]['content']['parts'][0]['text'])

        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return json.dumps({'message': {'content': json.dumps(ollama_json)}, 'done_reason': 'stop'}).encode('utf-8')

        with (
            patch.object(pipeline, 'ocr_document', return_value={0: {'fr': ocr_p}}),
            patch.object(gemini, 'extract', side_effect=RuntimeError('Gemini HTTP 503 : service temporairement indisponible')),
            patch.object(gemini, 'ollama_disponible', return_value=True),
            patch.object(gemini, 'page_png', return_value=b'png'),
            patch.object(gemini, 'urlopen', return_value=FakeResp()) as ollama_call,
            patch.dict(os.environ, {'OLLAMA_VLM_MODEL': 'qwen2.5vl:3b'}),
        ):
            run_dir = pipeline.traiter_document(input_path=source, output=self.base / 'sorties', sans_llm=False)

        doc = json.loads((run_dir / 'document.json').read_text(encoding='utf-8'))
        exec_d = json.loads((run_dir / 'execution.json').read_text(encoding='utf-8'))
        self.assertEqual(doc['lignes'][0]['designation'], 'Produit Ollama')
        self.assertEqual(exec_d['routes'][0]['moteur'], 'ollama_vlm_apres_echec_gemini')
        self.assertEqual(exec_d['routes'][0]['source_articles'], 'ollama_vlm')
        req_sent = json.loads(ollama_call.call_args.args[0].data.decode('utf-8'))
        self.assertEqual(req_sent['model'], 'qwen2.5vl:3b')
        self.assertEqual(req_sent['format'], gemini.output_schema())

    def test_parseur_ocr_bilingue_et_adresse_multiligne(self):
        # Test avec en-tetes fusionnes FR/AR (comme invoice_00052) et adresse fournisseur multi-lignes.
        raw = {
            'rec_texts': [
                'ÉMETTEU / المورد',
                'ATLAS Fournitures SARL / أطلس للتوريدات',
                '2, rue Exemple',
                '40000 Marrakech',
                '+212 5 00 00 00 00',
                'ICE: 973471338608099',
                'FACTURÉ À / الزبون',
                'BAHJA Services / بهجة للخدمات',
                'ICE: 924448527121068',
                'Réf./ المرجعDésignation/ البيان',
                'Qt/نمن الوحدة الكمبة',
                'المبلغ دون الضرببةا',
                'ENT-01',
                'Produit nettoyant 5 L / منظف 5 لترات',
                '8',
                '106,43',
                '851,44',
                'Total HT / دون الضريبة',
                '851,44',
                'Total TTC / شامل الضريبة',
                '1 021,73',
            ],
            'rec_boxes': [
                [100, 100, 320, 130],
                [100, 140, 680, 175],
                [100, 180, 340, 210],
                [100, 215, 340, 245],
                [100, 255, 320, 285],
                [100, 290, 350, 320],
                [850, 100, 1070, 130],
                [850, 140, 1300, 175],
                [850, 255, 1120, 285],
                [90, 420, 440, 455],
                [830, 420, 1190, 455],
                [1195, 420, 1500, 455],
                [90, 480, 170, 510],
                [240, 480, 620, 515],
                [832, 480, 860, 510],
                [970, 480, 1050, 510],
                [1200, 480, 1290, 510],
                [940, 560, 1180, 595],
                [1380, 560, 1490, 595],
                [940, 610, 1210, 645],
                [1360, 610, 1490, 645],
            ],
        }
        data, _, _ = direct(raw, 1)
        self.assertEqual(data['fournisseur'], 'ATLAS Fournitures SARL / أطلس للتوريدات')
        self.assertEqual(data['adresse_fournisseur'], '2, rue Exemple, 40000 Marrakech')
        self.assertEqual(data['ice_fournisseur'], '973471338608099')
        self.assertEqual(data['client'], 'BAHJA Services / بهجة للخدمات')
        self.assertEqual(len(data['lignes']), 1)
        self.assertEqual(data['lignes'][0]['designation'], 'Produit nettoyant 5 L / منظف 5 لترات')
        self.assertEqual(data['lignes'][0]['quantite'], 8.0)
        self.assertEqual(data['lignes'][0]['prix_unitaire'], 106.43)
        self.assertEqual(data['lignes'][0]['montant_ht'], 851.44)


if __name__ == '__main__':
    unittest.main()
