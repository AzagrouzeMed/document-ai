"""Validate frozen V6 on new DEV invoices, excluding the 90 examined invoices.

Place beside benchmark_dev.py, extract_json.py and fusion_ocr.py.
No OCR/extraction rules are modified. The test split is never selected.
"""
import argparse
import hashlib
import importlib.util
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


V6_HASHES = {'benchmark_dev.py': '3ad5aca48b02e7aedff143a6172e3d696f73349f7569f83048a69a1643320d85', 'extract_json.py': '968eb9702afa0cf609646df1f1ae615ef950692eccb0d1ca175aca0d2763d52a', 'fusion_ocr.py': 'f145898d5f0416cb00f575cb6dac9d1dc7c3fb1604e6f189543359cbdbed8f67'}

GROUPS = [(lang,quality) for lang in ['fr','ar','fr-ar']
          for quality in ['clean','scan','photo']]


def engine_hashes(folder):
    hashes = {}
    for name,expected in V6_HASHES.items():
        path = folder/name
        if not path.is_file():raise ValueError(f'Fichier V6 absent : {path}')
        digest = hashlib.sha256(path.read_text(encoding='utf-8-sig').encode()).hexdigest()
        if digest!=expected:
            raise ValueError(f'Version V6 differente : {name}. Replacer le fichier V6 original avant la validation.')
        hashes[name] = digest
    return hashes


def excluded_documents(reference):
    records = json.loads((reference/'selection.json').read_text(encoding='utf-8-sig'))
    ids = {row['document_id'] for row in records}
    if len(ids)!=45 or len(records)!=45:
        raise ValueError('Chaque dossier de reference doit contenir exactement 45 documents deja examines.')
    return ids


def choose_new_documents(dataset, excluded, per_group):
    groups = defaultdict(list)
    annotations = dataset/'dev'/'annotations'
    if not annotations.is_dir():raise ValueError(f'Dataset DEV introuvable : {annotations}')
    seen = set()
    for path in sorted(annotations.glob('*.json')):
        # Only metadata determines selection. Field values are not passed to inference.
        metadata = json.loads(path.read_text(encoding='utf-8-sig'))
        id_ = metadata['document_id']
        if id_ in seen:raise ValueError(f'Identifiant DEV duplique : {id_}')
        seen.add(id_)
        if id_ in excluded:continue
        key = (metadata['langue'],metadata['augmentation']['type'])
        image = (dataset/'dev'/metadata['image']).resolve()
        groups[key].append(dict(document_id=id_,langue=key[0],qualite=key[1],
                                image=str(image),annotation=str(path.resolve())))
    selected = []
    for key in GROUPS:
        available = groups[key]
        if len(available)<per_group:
            raise ValueError(f'Pas assez de nouvelles factures DEV pour {key[0]}/{key[1]} : {len(available)} disponibles.')
        selected.extend(available[:per_group])
    if {s['document_id'] for s in selected}&excluded:
        raise ValueError('Chevauchement avec les factures de correction.')
    for sample in selected:
        if not Path(sample['image']).is_file():raise ValueError(f"Image absente : {sample['image']}")
    return selected


def main():
    scripts = Path(__file__).resolve().parent
    root = scripts.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference',type=Path,action='append',required=True,help='Dossier de 45 factures deja examinees; fournir les deux references.')
    parser.add_argument('--dataset',type=Path,default=root/'dataset_document_ai'/'data'/'dataset_v1')
    parser.add_argument('--par-groupe',type=int,default=5)
    args = parser.parse_args()
    if args.par_groupe<1:parser.error('--par-groupe doit etre positif.')
    try:
        hashes = engine_hashes(scripts)
        references = [path.resolve() for path in args.reference]
        excluded = set().union(*(excluded_documents(path) for path in references))
        if len(excluded)!=90:raise ValueError('Fournir les deux lots distincts de 45 factures deja examines : 90 exclusions sont requises.')
        dataset = args.dataset.resolve()
        selected = choose_new_documents(dataset,excluded,args.par_groupe)
    except (ValueError,OSError,KeyError) as exc:parser.error(str(exc))
    counts = Counter((s['langue'],s['qualite']) for s in selected)
    protocol = dict(version_moteur=6,sha256_scripts=hashes,
                    references_exclusions=[str(path) for path in references],documents_exclus=sorted(excluded),
                    documents_selectionnes=[s['document_id'] for s in selected],
                    groupes=[dict(langue=k[0],qualite=k[1],nombre=counts[k]) for k in GROUPS],
                    chevauchement=0,split='dev',selection='ordre_des_identifiants_apres_exclusion',
                    seuils_ou_regles_modifies=False,
                    note='Nouveaux documents du meme dataset synthetique; les mises en page peuvent etre partagees. Ceci ne remplace pas le test final.')
    # Freeze selection in a manifest before any OCR/model execution.
    protocols = root/'results'/'validation_protocoles'
    protocols.mkdir(parents=True,exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    protocol_path = protocols/(stamp+'.json')
    protocol_path.write_text(json.dumps(protocol,ensure_ascii=False,indent=2),encoding='utf-8')
    spec = importlib.util.spec_from_file_location('benchmark_v6_frozen',scripts/'benchmark_dev.py')
    benchmark = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(benchmark)
    benchmark.select_documents = lambda _dataset,_count: [dict(row) for row in selected]
    results = root/'results'/'benchmark_dev'
    previous = set(results.iterdir()) if results.is_dir() else set()
    print(f'V6 verifiee. {len(selected)} nouvelles factures DEV; 0 chevauchement avec les 90 anciennes.',flush=True)
    print(f'Protocole : {protocol_path}',flush=True)
    sys.argv = [str(scripts/'benchmark_dev.py'),'--dataset',str(dataset),
                '--par-groupe',str(args.par_groupe),'--fusion-bilingue']
    benchmark.main()
    created = [p for p in results.iterdir() if p not in previous and p.is_dir()]
    # Correlate by the full selected document set, not a newest-directory guess.
    matching = []
    for folder in created:
        path = folder/'selection.json'
        if not path.is_file():continue
        ids = {s['document_id'] for s in json.loads(path.read_text(encoding='utf-8-sig'))}
        if ids==set(protocol['documents_selectionnes']):matching.append(folder)
    if len(matching)!=1:
        raise RuntimeError('Dossier de validation ambigu. Le protocole reste disponible dans validation_protocoles.')
    folder = matching[0]
    (folder/'protocole_validation.json').write_text(json.dumps(protocol,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'\nVALIDATION TERMINEE : {folder}',flush=True)
    print('Envoyer resume.csv, bilan.json et protocole_validation.json.',flush=True)


if __name__=='__main__':main()
