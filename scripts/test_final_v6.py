r"""Test final integral du split test avec le moteur V6 fige.

Placer dans scripts, a cote des trois fichiers V6 originaux.
Execution: .\.venv-ocr\Scripts\python.exe .\scripts\test_final_v6.py
Aucun sous-echantillonnage, rejeu ou ajustement sur le test.
"""
import argparse
import csv
import gc
import hashlib
import importlib.util
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from time import perf_counter

V6_HASHES = {
    'benchmark_dev.py': '3ad5aca48b02e7aedff143a6172e3d696f73349f7569f83048a69a1643320d85',
    'extract_json.py': '968eb9702afa0cf609646df1f1ae615ef950692eccb0d1ca175aca0d2763d52a',
    'fusion_ocr.py': 'f145898d5f0416cb00f575cb6dac9d1dc7c3fb1604e6f189543359cbdbed8f67',
}
ROUTES = {'fr': 'fr', 'ar': 'ar', 'fr-ar': 'fusion'}
TASKS = ['champs', 'articles', 'articles_numeriques', 'designations',
         'champs_normalises', 'articles_normalises', 'designations_normalisees']


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def verify_engine(scripts):
    actual = {}
    for name, expected in V6_HASHES.items():
        digest = hashlib.sha256((scripts/name).read_text(encoding='utf-8-sig').encode()).hexdigest()
        if digest != expected:
            raise ValueError(f'Fichier V6 modifie : {name}. Replacer la version V6 originale.')
        actual[name] = digest
    return actual


def select_test(dataset):
    annotations = dataset/'test'/'annotations'
    if not annotations.is_dir():
        raise ValueError(f'Split test introuvable : {annotations}')
    # DEV names only: no development reference values are read here.
    dev_ids = {p.stem for p in (dataset/'dev'/'annotations').glob('*.json')}
    selected, seen = [], set()
    for path in sorted(annotations.glob('*.json')):
        # Load metadata only into the inference manifest; never pass champs.
        meta = json.loads(path.read_text(encoding='utf-8-sig'))
        doc = meta['document_id']
        if doc in seen or doc in dev_ids or doc != path.stem:
            raise ValueError(f'Identifiant duplique, incoherent ou present en DEV : {doc}')
        seen.add(doc)
        lang, quality = meta['langue'], meta['augmentation']['type']
        if lang not in ROUTES or quality not in ['clean', 'scan', 'photo']:
            raise ValueError(f'Metadonnees non prises en charge : {doc}')
        image = (dataset/'test'/meta['image']).resolve()
        if not image.is_relative_to((dataset/'test').resolve()) or not image.is_file():
            raise ValueError(f'Image test absente ou hors split : {doc}')
        selected.append(dict(document_id=doc, langue=lang, qualite=quality,
                             image=str(image), annotation=str(path.resolve())))
    if not selected:
        raise ValueError('Le split test est vide.')
    return selected


def aggregate(benchmark, rows, scores, indices):
    result = {task: benchmark.counts(*(
        sum(scores[i][task][k] for i in indices) for k in ['tp', 'fp', 'fn']))
        for task in TASKS}
    result.update(executions=len(indices), echecs=sum(rows[i]['statut']=='echec' for i in indices))
    return result


def write_csv(path, rows):
    if not rows:
        return
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def execute(benchmark, selected, scripts, run, protocol):
    from paddleocr import PaddleOCR
    rows, scores, initializations = [], [], {}
    def record(row, score):
        rows.append(row)
        scores.append(score)
        write_csv(run/'resume.csv', rows)
        print(f" -> {row['statut']} : champs {row['champs_corrects']}/10 ; F1 articles {row['f1_articles']:.3f}", flush=True)
    for lang, recognizer in [('fr', 'latin_PP-OCRv5_mobile_rec'), ('ar', 'arabic_PP-OCRv5_mobile_rec')]:
        if not any(s['langue'] in [lang, 'fr-ar'] for s in selected):
            continue
        print(f'Chargement OCR {lang}...', flush=True)
        start = perf_counter()
        ocr = PaddleOCR(device='cpu', lang=lang, ocr_version='PP-OCRv5',
            text_detection_model_name='PP-OCRv5_mobile_det', text_recognition_model_name=recognizer,
            use_doc_orientation_classify=False, use_doc_unwarping=False, use_textline_orientation=False)
        initializations[lang] = round(perf_counter()-start, 3)
        for sample in selected:
            if sample['langue'] not in [lang, 'fr-ar']:
                continue
            print(f"Test {sample['document_id']} {sample['langue']}/{sample['qualite']} lecture={lang}", flush=True)
            record(*benchmark.run_case(ocr, sample, lang, run, scripts/'extract_json.py', False))
        del ocr
        gc.collect()
    for sample in selected:
        if sample['langue'] != 'fr-ar':
            continue
        doc = run/sample['document_id']
        print(f"Fusion {sample['document_id']}", flush=True)
        record(*benchmark.run_case(None, sample, 'fusion', run, scripts/'extract_json.py', False,
            doc/'lecture_ar'/'ocr.json', doc/'lecture_fr'/'ocr.json'))
    final_indices = [i for i,r in enumerate(rows) if r['lecture']==ROUTES[r['langue']]]
    if len(final_indices)!=len(selected):
        raise RuntimeError('Nombre de resultats finaux incoherent.')
    branches = sorted({(r['langue'], r['lecture']) for r in rows})
    by_branch = {f'{lang}/lecture_{lecture}': aggregate(benchmark, rows, scores,
        [i for i,r in enumerate(rows) if (r['langue'],r['lecture'])==(lang,lecture)])
        for lang,lecture in branches}
    by_group = {f'{lang}/{quality}': aggregate(benchmark, rows, scores,
        [i for i in final_indices if (rows[i]['langue'],rows[i]['qualite'])==(lang,quality)])
        for lang,quality in sorted({(s['langue'],s['qualite']) for s in selected})}
    write_csv(run/'resume_final.csv', [rows[i] for i in final_indices])
    summary = dict(split='test', version_script=6, mode='OCR_LLM_regles', rejeu_ocr=False,
        fusion_bilingue=True, factures_distinctes=len(selected), executions=len(rows),
        echecs=sum(r['statut']=='echec' for r in rows),
        resultat_final=aggregate(benchmark, rows, scores, final_indices),
        routes_finales=ROUTES, resultats_par_branche=by_branch, resultats_finaux_par_groupe=by_group,
        initialisation_ocr_secondes=initializations, paddleocr=version('paddleocr'),
        notes=['Split test integral, moteur V6 fige avant execution.',
            'Une seule sortie finale par facture, route choisie par langue, jamais par reference.',
            'Les echecs sont conserves et penalises; les branches sources ne sont pas additionnees au score final.',
            'Scores exacts et normalises separes; normalisation diagnostique V6 inchangee.',
            'Dataset synthetique : des mises en page peuvent etre partagees avec DEV.',
            'La fusion a un temps extraction seul; le cout complet bilingue inclut les deux lectures OCR.'])
    dump(run/'bilan.json', summary)
    protocol['statut'] = 'termine'
    protocol['sha256_scripts_apres'] = verify_engine(scripts)
    dump(run/'protocole_test.json', protocol)


def main():
    scripts = Path(__file__).resolve().parent
    root = scripts.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, default=root/'dataset_document_ai'/'data'/'dataset_v1')
    args = parser.parse_args()
    try:
        hashes = verify_engine(scripts)
        selected = select_test(args.dataset.resolve())
    except (OSError, ValueError, KeyError) as exc:
        parser.error(str(exc))
    run = root/'results'/'test_final_v6'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    run.mkdir(parents=True, exist_ok=False)
    groups = Counter((s['langue'],s['qualite']) for s in selected)
    protocol = dict(split='test', version_moteur=6, sha256_scripts=hashes,
        sha256_lanceur=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        dataset=str(args.dataset.resolve()), selection='integralite_du_split_test',
        documents_selectionnes=[s['document_id'] for s in selected],
        groupes=[dict(langue=k[0], qualite=k[1], nombre=n) for k,n in sorted(groups.items())],
        chevauchement_identifiants_dev=0, seuils_ou_regles_modifies=False,
        routes_finales=ROUTES, statut='en_cours',
        note='Disjonction par identifiant verifiee; independance des mises en page non garantie.')
    dump(run/'selection.json', selected)
    dump(run/'protocole_test.json', protocol)
    spec = importlib.util.spec_from_file_location('benchmark_v6_frozen', scripts/'benchmark_dev.py')
    benchmark = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(benchmark)
    print(f'V6 verifiee. TEST FINAL : {len(selected)} factures. Sortie : {run}', flush=True)
    try:
        execute(benchmark, selected, scripts, run, protocol)
    except Exception as exc:
        protocol.update(statut='interrompu', erreur=str(exc))
        dump(run/'protocole_test.json', protocol)
        raise
    print(f'\nTEST FINAL TERMINE : {run}', flush=True)
    print('Envoyer resume_final.csv, bilan.json et protocole_test.json.', flush=True)


if __name__ == '__main__':
    main()
