"""Validate frozen V5 on new DEV invoices, excluding the 45 correction invoices.

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


V5_HASHES = {
    'benchmark_dev.py': '3d78ce2a0d0a5beb6025d3d5be88b6a5a5e4ad0b4b3c2ef414f6b87ddce4670c',
    'extract_json.py': '2662faa50c8a315c0000c3a16ea2c65e4664937c8a08c1bffa7e16495bc6e9c7',
    'fusion_ocr.py': '3f081471217365500d5d2a5cc4c21452622d8ec97d35ff62d4d95ee58080fb0e',
}
GROUPS = [(lang,quality) for lang in ['fr','ar','fr-ar']
          for quality in ['clean','scan','photo']]


def engine_hashes(folder):
    hashes = {}
    for name,expected in V5_HASHES.items():
        path = folder/name
        if not path.is_file():raise ValueError(f'Fichier V5 absent : {path}')
        digest = hashlib.sha256(path.read_text(encoding='utf-8-sig').encode()).hexdigest()
        if digest!=expected:
            raise ValueError(f'Version V5 differente : {name}. Replacer le fichier V5 original avant la validation.')
        hashes[name] = digest
    return hashes


def excluded_documents(reference):
    records = json.loads((reference/'selection.json').read_text(encoding='utf-8-sig'))
    ids = {row['document_id'] for row in records}
    if len(ids)!=45 or len(records)!=45:
        raise ValueError('Le dossier de reference doit contenir les 45 factures utilisees pour les corrections.')
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
    parser.add_argument('--reference',type=Path,required=True,help='Ancien dossier benchmark des 45 factures de correction.')
    parser.add_argument('--dataset',type=Path,default=root/'dataset_document_ai'/'data'/'dataset_v1')
    parser.add_argument('--par-groupe',type=int,default=5)
    args = parser.parse_args()
    if args.par_groupe<1:parser.error('--par-groupe doit etre positif.')
    try:
        hashes = engine_hashes(scripts)
        reference = args.reference.resolve()
        excluded = excluded_documents(reference)
        dataset = args.dataset.resolve()
        selected = choose_new_documents(dataset,excluded,args.par_groupe)
    except (ValueError,OSError,KeyError) as exc:parser.error(str(exc))
    counts = Counter((s['langue'],s['qualite']) for s in selected)
    protocol = dict(version_moteur=5,sha256_scripts=hashes,
                    reference_exclusions=str(reference),documents_exclus=sorted(excluded),
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
    spec = importlib.util.spec_from_file_location('benchmark_v5_frozen',scripts/'benchmark_dev.py')
    benchmark = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(benchmark)
    benchmark.select_documents = lambda _dataset,_count: [dict(row) for row in selected]
    results = root/'results'/'benchmark_dev'
    previous = set(results.iterdir()) if results.is_dir() else set()
    print(f'V5 verifiee. {len(selected)} nouvelles factures DEV; 0 chevauchement avec les 45 anciennes.',flush=True)
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
