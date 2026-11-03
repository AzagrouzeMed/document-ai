"""Structural and arithmetic validation; does NOT replace human annotation review."""
import argparse
import csv
import hashlib
import json
from collections import Counter,defaultdict
from pathlib import Path
from PIL import Image
from pypdf import PdfReader
from generate_dataset import Invoice


def validate(root):
    manifest=list(csv.DictReader((root/'manifest.csv').open(encoding='utf-8-sig',newline='')))
    ids=set();hashes=set();templates=defaultdict(set);groups=defaultdict(set);summary=Counter()
    for row in manifest:
        doc_id=row['document_id'];split=row['split']
        assert doc_id not in ids, f'Duplicate ID {doc_id}'
        ids.add(doc_id)
        a=json.loads((root/row['annotation']).read_text(encoding='utf-8'))
        assert a['document_id']==doc_id and a['split']==split
        Invoice.model_validate(a['champs'])
        with Image.open(root/row['image']) as im:
            w,h=im.size
            assert (w,h)==(a['image_dimensions']['width'],a['image_dimensions']['height'])
        digest=hashlib.sha256((root/row['image']).read_bytes()).hexdigest()
        assert digest==a['sha256_image'] and digest not in hashes, f'Duplicate image {doc_id}'
        hashes.add(digest)
        assert len(PdfReader(root/row['pdf']).pages)==1, f'PDF not one page {doc_id}'
        for z in a['zones']:
            x0,y0,x1,y1=z['bbox']
            assert 0<=x0<x1<=w and 0<=y0<y1<=h,(doc_id,z)
            assert z['page']==1
        for key in ['fournisseur','ice','numero_facture','date','total_ht','montant_tva','total_ttc']:
            assert any(z.get('champ')==key for z in a['zones']),(doc_id,key)
        for kind in ['logo','cachet','signature']:
            present=any(z['type']==kind for z in a['zones'])
            assert present==a['marques'][kind],(doc_id,kind)
        assert len(a['questions_reponses'])==5
        for qa in a['questions_reponses']:
            assert any(z.get('champ')==qa['champ'] and z['bbox']==qa['bbox'] for z in a['zones'])
            value=a['champs'][qa['champ']]
            expected=f'{value:.2f} MAD' if qa['champ']=='total_ttc' else value
            assert qa['reponse']==expected, (doc_id,'incorrect QA answer')
        templates[split].add(a['template_id']);groups[split].add(a['group_id'])
        summary[f'{split}/{a["langue"]}']+=1
    assert templates['dev'].isdisjoint(templates['test']),'Template leakage'
    assert groups['dev'].isdisjoint(groups['test']),'Invoice family leakage'
    actual_json=list(root.glob('*/annotations/*.json'))
    assert len(actual_json)==len(manifest),'Unexpected or missing annotations'
    report=dict(status='PASS',documents=len(manifest),distribution=dict(summary),templates_disjoints=True,groupes_disjoints=True,doublons_images=0,verification='structure,calculs,geometrie,PDF et correspondance des sources',validation_humaine='RESTE A FAIRE')
    (root/'validation_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--dataset',type=Path,default=Path(__file__).resolve().parents[1]/'data'/'dataset_v1')
    args=p.parse_args();print(json.dumps(validate(args.dataset),ensure_ascii=False,indent=2))
