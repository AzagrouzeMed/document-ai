"""First FR/AR OCR run. Uses dev images only; annotations select samples, never supply answers."""
import argparse
import csv
import gc
import json
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from time import perf_counter


def select_samples(dataset):
    folder = dataset / 'dev' / 'annotations'
    if not folder.is_dir():
        raise FileNotFoundError(f'Dossier introuvable : {folder}. Utiliser --dataset CHEMIN.')
    selected = {}
    for path in sorted(folder.glob('*.json')):
        # Read only metadata. No reference text, field value or gold box goes to OCR.
        meta = json.loads(path.read_text(encoding='utf-8'))
        lang = meta['langue']
        if lang in ('fr', 'ar', 'fr-ar') and lang not in selected:
            if meta.get('augmentation', {}).get('type') != 'clean':
                continue
            image = folder.parent / meta['image']
            if not image.is_file():
                raise FileNotFoundError(str(image))
            selected[lang] = dict(document_id=meta['document_id'], image=image, langue=lang)
        if len(selected) == 3:
            break
    if len(selected) != 3:
        raise ValueError('Il faut une image propre par langue FR, AR et FR-AR dans dev.')
    return [selected[lang] for lang in ('fr', 'ar', 'fr-ar')]


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path,
                        default=root / 'dataset_document_ai' / 'data' / 'dataset_v1')
    parser.add_argument('--output', type=Path, default=root / 'results' / 'ocr_pilot')
    args = parser.parse_args()
    samples = select_samples(args.dataset.resolve())
    for sample in samples:
        print(f"Selection : {sample['langue']} / {sample['document_id']}", flush=True)
    # Import after validating paths so missing data does not start model downloads.
    from paddleocr import PaddleOCR
    from PIL import Image, ImageDraw

    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output = args.output.resolve() / run_id
    output.mkdir(parents=True, exist_ok=False)
    manifest = dict(paddleocr=version('paddleocr'), paddlepaddle=version('paddlepaddle'),
                    dataset=str(args.dataset.resolve()), device='cpu',
                    ocr_version='PP-OCRv5', detection='PP-OCRv5_mobile_det',
                    recognition={'fr': 'latin_PP-OCRv5_mobile_rec',
                                 'ar': 'arabic_PP-OCRv5_mobile_rec'},
                    pretraitement='aucun redressement ou debruitage externe',
                    samples=[dict(document_id=s['document_id'], langue=s['langue'],
                                  image=str(s['image'])) for s in samples],
                    notes='Premier passage : temps incluant tout chargement paresseux. '
                          'Non comparable a un benchmark chaud. Les deux lectures '
                          'bilingues restent separees. Confiance != precision mesuree.')
    rows = []
    for model_lang, rec_name in [('fr', 'latin_PP-OCRv5_mobile_rec'),
                                 ('ar', 'arabic_PP-OCRv5_mobile_rec')]:
        print(f'\nInitialisation OCR {model_lang} (telechargement au premier lancement)...', flush=True)
        start = perf_counter()
        ocr = PaddleOCR(
            device='cpu', lang=model_lang, ocr_version='PP-OCRv5',
            text_detection_model_name='PP-OCRv5_mobile_det',
            text_recognition_model_name=rec_name,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
        )
        init_seconds = perf_counter() - start
        for sample in samples:
            if sample['langue'] not in (model_lang, 'fr-ar'):
                continue
            destination = output / sample['document_id'] / f'lecture_{model_lang}'
            destination.mkdir(parents=True)
            print(f"Lecture {sample['document_id']} avec {model_lang}...", flush=True)
            start = perf_counter()
            results = list(ocr.predict(input=str(sample['image'])))
            elapsed = perf_counter() - start
            if len(results) != 1:
                raise RuntimeError('Une image doit produire exactement un resultat de page.')
            result = results[0]
            result.save_to_json(str(destination / 'ocr.json'))
            saved = json.loads((destination / 'ocr.json').read_text(encoding='utf-8'))
            data = saved.get('res', saved)
            texts = data.get('rec_texts', [])
            scores = data.get('rec_scores', [])
            boxes = data.get('rec_polys', [])
            if len(texts) != len(scores):
                raise RuntimeError('Nombre de textes/confiances incoherent.')
            (destination / 'texte.txt').write_text('\n'.join(texts), encoding='utf-8')
            # Draw numbered outlines only; no extra font download is needed.
            with Image.open(sample['image']) as im:
                visual = im.convert('RGB')
            draw = ImageDraw.Draw(visual)
            for index, poly in enumerate(boxes, 1):
                points = [(int(p[0]), int(p[1])) for p in poly]
                if not points:
                    continue
                draw.line(points + [points[0]], fill='#dd3848', width=2)
                draw.text(points[0], str(index), fill='#0033cc')
            visual.save(destination / 'zones_detectees.jpg', quality=90)
            row = dict(document_id=sample['document_id'], langue_document=sample['langue'],
                       langue_modele=model_lang, blocs_reconnus=len(texts),
                       confiance_moyenne=round(sum(scores)/len(scores), 4) if scores else '',
                       inference_secondes=round(elapsed, 3),
                       initialisation_modele_secondes=round(init_seconds, 3))
            rows.append(row)
            print(f"OK : {len(texts)} blocs, {elapsed:.1f} s -> {destination}", flush=True)
        del ocr
        gc.collect()
    with (output / 'resume.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (output / 'configuration.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'\nTERMINE : {output}', flush=True)
    print('3 factures / 4 lectures : FR, AR et deux lectures du bilingue.')
    print('Envoyer resume.csv et un extrait du texte reconnu. Les scores de qualite restent a mesurer.')


if __name__ == '__main__':
    main()
