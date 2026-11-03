"""Small development benchmark: FR/AR/bilingual x clean/scan/photo.
Inference only sees images/OCR; reference fields are read afterwards for scoring.
Bilingual passes are scored separately. No best-of-ground-truth selection.
"""
import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import traceback
import unicodedata
from collections import Counter, defaultdict
import re
from datetime import datetime, timezone
from decimal import Decimal
from importlib.metadata import version
from pathlib import Path
from time import perf_counter

FIELDS = {
    'fournisseur':'fournisseur', 'ice_fournisseur':'ice',
    'client':'client.nom', 'ice_client':'client.ice',
    'numero_facture':'numero_facture', 'date':'date', 'devise':'devise',
    'total_ht':'total_ht', 'montant_tva':'montant_tva', 'total_ttc':'total_ttc',
}


def nested(data, path):
    for key in path.split('.'):
        data = data[key]
    return data


def normalized(value, number=False):
    if value is None:return None
    if number:
        return str(Decimal(str(value)).quantize(Decimal('0.01')))
    return ' '.join(unicodedata.normalize('NFC',str(value)).casefold().split())



def normalized_text(value):
    """Diagnostic normalization only. Exact scores remain unchanged.

    Remove accents/harakat/tatweel/bidi formatting, preserving hamza/madda; map Persian yeh/kaf to Arabic.
    A leading Latin product code on an otherwise Arabic description moves to
    the end. Arabic words are never reordered, translated or spell-corrected.
    """
    if value is None:return None
    text=unicodedata.normalize('NFKD',str(value)).casefold()
    text=''.join(c for c in text if (not unicodedata.combining(c) or c in '\u0653\u0654\u0655') and unicodedata.category(c)!='Cf')
    text=unicodedata.normalize('NFC',text)
    text=text.translate(str.maketrans({'ی':'ي','ک':'ك','ـ':''}))
    text=' '.join(text.split())
    if '/' not in text and any('ARABIC' in unicodedata.name(c,'') for c in text):
        match=re.fullmatch(r'([a-z]+[0-9][a-z0-9-]*)\s+(.+)',text)
        if match:text=match[2]+' '+match[1]
    return text

def counts(tp, fp, fn):
    return dict(tp=tp,fp=fp,fn=fn,precision=tp/(tp+fp) if tp+fp else 0.0,
                rappel=tp/(tp+fn) if tp+fn else 0.0,
                f1=2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else 0.0)


def evaluate(prediction, reference):
    detail=[];tp=fp=fn=0
    ntp=nfp=nfn=0
    for key,path in FIELDS.items():
        expected=nested(reference,path)
        actual=prediction.get(key) if prediction else None
        number=key in ['total_ht','montant_tva','total_ttc']
        ok=actual is not None and normalized(actual,number)==normalized(expected,number)
        tp+=int(ok);fn+=int(not ok);fp+=int(not ok and actual is not None)
        normalizer=(lambda v:normalized(v,True)) if number else normalized_text
        normalized_ok=actual is not None and normalizer(actual)==normalizer(expected)
        ntp+=int(normalized_ok);nfn+=int(not normalized_ok);nfp+=int(not normalized_ok and actual is not None)
        detail.append(dict(champ=key,reference=expected,prediction=actual,correct=ok,correct_normalise=normalized_ok))
    def line_key(line):
        return (normalized(line.get('designation')),
                normalized(line.get('quantite'),True),
                normalized(line.get('prix_unitaire_ht'),True),
                normalized(line.get('montant_ht'),True))
    expected=Counter(line_key(x) for x in reference['lignes'])
    actual=Counter(line_key(x) for x in (prediction or {}).get('lignes',[]))
    matched=sum((expected & actual).values())
    def normalized_line(line):
        return (normalized_text(line.get('designation')),)+tuple(normalized(line.get(k),True) for k in ['quantite','prix_unitaire_ht','montant_ht'])
    gold_normalized=Counter(normalized_line(x) for x in reference['lignes'])
    actual_normalized=Counter(normalized_line(x) for x in (prediction or {}).get('lignes',[]))
    nmatched=sum((gold_normalized & actual_normalized).values())
    def separate(key):
        def signature(line):
            if key=='numeriques':return tuple(normalized(line.get(k),True) for k in ['quantite','prix_unitaire_ht','montant_ht'])
            return normalized_text(line.get('designation')) if key=='normalise' else normalized(line.get('designation'))
        gold=Counter(signature(x) for x in reference['lignes'])
        pred=Counter(signature(x) for x in (prediction or {}).get('lignes',[]))
        n=sum((gold & pred).values())
        return counts(n,sum(pred.values())-n,sum(gold.values())-n)
    return dict(champs=counts(tp,fp,fn),champs_normalises=counts(ntp,nfp,nfn),articles_normalises=counts(nmatched,sum(actual_normalized.values())-nmatched,sum(gold_normalized.values())-nmatched),designations_normalisees=separate('normalise'),articles=counts(matched,sum(actual.values())-matched,sum(expected.values())-matched),articles_numeriques=separate('numeriques'),designations=separate('designations'),detail=detail,n_articles_attendus=sum(expected.values()),n_articles_predits=sum(actual.values()))


def select_documents(dataset, per_cell):
    selected=[]
    # Metadata selects a predefined stratified DEV sample. Values are not input to inference.
    groups=defaultdict(list)
    for path in sorted((dataset/'dev'/'annotations').glob('*.json')):
        a=json.loads(path.read_text(encoding='utf-8'))
        group=(a['langue'],a['augmentation']['type'])
        groups[group].append(dict(document_id=a['document_id'],langue=a['langue'],
                                  qualite=a['augmentation']['type'],
                                  image=str((dataset/'dev'/a['image']).resolve()),
                                  annotation=str(path.resolve())))
    for lang in ['fr','ar','fr-ar']:
        for quality in ['clean','scan','photo']:
            candidates=groups[(lang,quality)]
            if len(candidates)<per_cell:
                raise ValueError(f'Groupe dev insuffisant : {lang}/{quality}')
            selected+=candidates[:per_cell]
    return selected


def run_case(ocr, sample, model_lang, run, extractor, sans_llm, ocr_source=None, secondary_source=None, cached_case=None):
    folder=run/sample['document_id']/f'lecture_{model_lang}'
    folder.mkdir(parents=True)
    row=dict(document_id=sample['document_id'],langue=sample['langue'],qualite=sample['qualite'],lecture=model_lang,statut='echec',ocr_secondes='',extraction_secondes='',champs_corrects=0,champs_evalues=10,f1_articles=0.0,f1_articles_numeriques=0.0,f1_designations=0.0,champs_corrects_normalises=0,f1_articles_normalises=0.0,f1_designations_normalisees=0.0,articles_attendus=0,articles_predits=0,alertes='',corrections_roles='',propositions_rejetees='',erreur='')
    prediction=None;start=perf_counter()
    try:
        ocr_path=folder/'ocr.json'
        if ocr_source is not None:
            shutil.copyfile(ocr_source,ocr_path)
            row['ocr_secondes']=''  # Cached OCR: do not present replay time as OCR latency.
        else:
            results=list(ocr.predict(input=sample['image']))
            row['ocr_secondes']=round(perf_counter()-start,3)
            if len(results)!=1:raise ValueError('Une seule page OCR attendue.')
            results[0].save_to_json(str(ocr_path))
        command=[sys.executable,str(extractor),'--ocr',str(ocr_path),'--output',str(folder/'extraction')]
        if secondary_source is not None:
            secondary_path=folder/'ocr_fr.json'
            shutil.copyfile(secondary_source,secondary_path)
            command+=['--ocr-secondaire',str(secondary_path)]
        if sans_llm:command.append('--sans-llm')
        if cached_case is not None:
            replies=list(cached_case.glob('extraction/*/reponse_brute.txt'))
            if len(replies)!=1:raise RuntimeError('Reponse archivee absente ou ambigue; aucune generation lancee.')
            command+=['--reponse-modele',str(replies[0])]

        environment=os.environ.copy();environment['PYTHONUTF8']='1'
        start=perf_counter()
        process=subprocess.run(command,capture_output=True,text=True,encoding='utf-8',errors='replace',env=environment,timeout=660)
        row['extraction_secondes']=round(perf_counter()-start,3)
        (folder/'extraction.log').write_text(process.stdout+'\n'+process.stderr,encoding='utf-8')
        if process.returncode!=0:raise RuntimeError('Extraction echouee : voir extraction.log')
        outputs=sorted((folder/'extraction').glob('*/facture.json'))
        if len(outputs)!=1:raise RuntimeError('Une facture.json attendue dans ce cas.')
        prediction=json.loads(outputs[0].read_text(encoding='utf-8'))
        controls=json.loads((outputs[0].parent/'controles.json').read_text(encoding='utf-8'))
        row['alertes']=len(controls.get('alertes',[]))
        row['corrections_roles']=len(controls.get('corrections_par_regles',[]))
        rejected=outputs[0].parent/'rejets.json'
        row['propositions_rejetees']=len(json.loads(rejected.read_text(encoding='utf-8'))) if rejected.is_file() else 0
        row['statut']='succes_technique'
    except Exception as exc:
        row['erreur']=str(exc)
        (folder/'erreur.log').write_text(traceback.format_exc(),encoding='utf-8')
    # Gold values are read only AFTER the inference attempt, solely by the evaluator.
    reference=json.loads(Path(sample['annotation']).read_text(encoding='utf-8'))['champs']
    metrics=evaluate(prediction,reference)
    row.update(champs_corrects=metrics['champs']['tp'],champs_corrects_normalises=metrics['champs_normalises']['tp'],f1_articles_normalises=round(metrics['articles_normalises']['f1'],4),f1_designations_normalisees=round(metrics['designations_normalisees']['f1'],4),f1_articles=round(metrics['articles']['f1'],4),f1_articles_numeriques=round(metrics['articles_numeriques']['f1'],4),f1_designations=round(metrics['designations']['f1'],4),articles_attendus=metrics['n_articles_attendus'],articles_predits=metrics['n_articles_predits'])
    (folder/'evaluation.json').write_text(json.dumps(metrics,ensure_ascii=False,indent=2),encoding='utf-8')
    return row,metrics


def main():
    root=Path(__file__).resolve().parents[1]
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',type=Path,default=root/'dataset_document_ai'/'data'/'dataset_v1')
    parser.add_argument('--par-groupe',type=int,default=1)
    parser.add_argument('--sans-llm',action='store_true')
    parser.add_argument('--rejouer',type=Path,help='Dossier benchmark contenant les OCR sauvegardes; aucun nouvel OCR.')
    parser.add_argument('--fusion-bilingue',action='store_true',help='Ajouter une branche fusion apres les lectures FR/AR; conserver les branches separees.')
    parser.add_argument('--reponses-cache',action='store_true',help='Valider les reponses modele archivees du rejeu, sans nouvelle generation; mode diagnostic.')
    args=parser.parse_args()
    if args.reponses_cache and (not args.rejouer or args.sans_llm):parser.error('--reponses-cache requiert --rejouer et exclut --sans-llm.')
    if args.par_groupe<1:parser.error('--par-groupe doit etre >= 1')
    dataset=args.dataset.resolve()
    if not (dataset/'dev'/'annotations').is_dir():parser.error('Dataset dev introuvable. Fournir --dataset CHEMIN.')
    extractor=Path(__file__).resolve().parent/'extract_json.py'
    if not extractor.is_file():parser.error('Placer extract_json.py corrige dans le meme dossier scripts.')
    if 'Version corrigee 6' not in extractor.read_text(encoding='utf-8-sig'):parser.error('Placer extract_json.py version 6 et fusion_ocr.py dans scripts.')
    if args.fusion_bilingue and not (extractor.parent/'fusion_ocr.py').is_file():parser.error('fusion_ocr.py manque dans scripts.')
    if args.rejouer:
        cache=args.rejouer.resolve()
        selected=json.loads((cache/'selection.json').read_text(encoding='utf-8-sig'))
        for sample in selected:
            annotation=dataset/'dev'/'annotations'/(sample['document_id']+'.json')
            if not annotation.is_file():parser.error(f'Annotation dev introuvable : {annotation}')
            sample['annotation']=str(annotation)
    else:
        selected=select_documents(dataset,args.par_groupe)
    run=root/'results'/'benchmark_dev'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    run.mkdir(parents=True,exist_ok=False)
    (run/'selection.json').write_text(json.dumps(selected,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'{len(selected)} factures dev : FR/AR/bilingues x propre/scan/photo.',flush=True)
    print('Branches FR/AR conservees; fusion optionnelle par positions, sans selection par les references.',flush=True)
    if not args.rejouer:
        from paddleocr import PaddleOCR
    rows=[];metrics=[];initialisations={}
    import gc
    for model_lang,recognizer in [('fr','latin_PP-OCRv5_mobile_rec'),('ar','arabic_PP-OCRv5_mobile_rec')]:
        if args.rejouer:
            ocr=None
        else:
            print(f'Chargement OCR {model_lang}...',flush=True);start=perf_counter()
            ocr=PaddleOCR(device='cpu',lang=model_lang,ocr_version='PP-OCRv5',text_detection_model_name='PP-OCRv5_mobile_det',text_recognition_model_name=recognizer,use_doc_orientation_classify=False,use_doc_unwarping=False,use_textline_orientation=False)
            initialisations[model_lang]=round(perf_counter()-start,3)
        for sample in selected:
            if sample['langue'] not in [model_lang,'fr-ar']:continue
            print(f"Traitement {sample['document_id']} {sample['langue']}/{sample['qualite']} lecture={model_lang}",flush=True)
            source=cache/sample['document_id']/f'lecture_{model_lang}'/'ocr.json' if args.rejouer else None
            row,score=run_case(ocr,sample,model_lang,run,extractor,args.sans_llm,source,cached_case=cache/sample['document_id']/f'lecture_{model_lang}' if args.reponses_cache else None)
            rows.append(row);metrics.append(score)
            with (run/'resume.csv').open('w',encoding='utf-8-sig',newline='') as stream:
                writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
            print(f" -> {row['statut']} : champs {row['champs_corrects']}/10 ; F1 articles {row['f1_articles']:.3f}",flush=True)
        del ocr;gc.collect()
    if args.fusion_bilingue:
        for sample in selected:
            if sample['langue']!='fr-ar':continue
            document=run/sample['document_id']
            print(f"Fusion {sample['document_id']} : lecture arabe + francaise alignees",flush=True)
            row,score=run_case(None,sample,'fusion',run,extractor,args.sans_llm,
                               document/'lecture_ar'/'ocr.json',document/'lecture_fr'/'ocr.json',
                               cached_case=cache/sample['document_id']/'lecture_fusion' if args.reponses_cache else None)
            rows.append(row);metrics.append(score)
            with (run/'resume.csv').open('w',encoding='utf-8-sig',newline='') as stream:
                writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
            print(f" -> {row['statut']} : champs {row['champs_corrects']}/10 ; F1 articles {row['f1_articles']:.3f} ; normalise {row['f1_articles_normalises']:.3f}",flush=True)
    by_branch={}
    branches=[('fr','fr'),('ar','ar'),('fr-ar','fr'),('fr-ar','ar')]
    if args.fusion_bilingue:branches.append(('fr-ar','fusion'))
    for lang,model in branches:
        indices=[i for i,row in enumerate(rows) if row['langue']==lang and row['lecture']==model]
        branch={}
        for task in ['champs','articles','articles_numeriques','designations','champs_normalises','articles_normalises','designations_normalisees']:
            branch[task]=counts(*(sum(metrics[i][task][v] for i in indices) for v in ['tp','fp','fn']))
        branch['executions']=len(indices);branch['echecs']=sum(rows[i]['statut']=='echec' for i in indices)
        by_branch[f'{lang}/lecture_{model}']=branch
    summary=dict(factures_distinctes=len(selected),executions=len(rows),echecs=sum(r['statut']=='echec' for r in rows),resultats_par_branche=by_branch,initialisation_ocr_secondes=initialisations,paddleocr='OCR en cache' if args.rejouer else version('paddleocr'),rejeu_ocr=bool(args.rejouer),version_script=6,fusion_bilingue=bool(args.fusion_bilingue),mode='validation_reponses_archivees' if args.reponses_cache else ('regles' if args.sans_llm else 'OCR_LLM_regles'),normalisation_textuelle='NFKD sans diacritiques, tatweel, controles bidi; yeh/kaf persans vers arabes; code Latin initial vers fin des descriptions arabes; aucun dictionnaire ni traduction',notes=['DEV uniquement, echantillon de diagnostic, pas un score final.','Une erreur de champ non nul compte comme FP et FN; une absence comme FN.','Un article est exact si designation, quantite, prix et montant correspondent.', 'Articles numeriques : quantite, prix et montant exacts ensemble; designations : comparaison textuelle separee sans traduction.','Les articles identiques sont comptes avec multiplicite.','Les echecs sont conserves et penalises; aucune selection du meilleur resultat bilingue.', 'La fusion est une branche supplementaire: ne pas additionner ses comptes aux branches sources pour un score par document.', 'Les scores normalises sont diagnostiques et ne remplacent pas les scores exacts.','La confiance OCR et le nombre d alertes ne constituent pas la precision mesuree.','Validation seule, sans generation: les temps ne sont pas une latence OCR-LLM.' if args.reponses_cache else 'La latence inclut les chargements du modele local pour chaque extraction.'])
    if args.rejouer:
        changes=[];totals={'champs_gagnes':0,'champs_perdus':0}
        for row,score in zip(rows,metrics):
            previous=cache/row['document_id']/f"lecture_{row['lecture']}"/'evaluation.json'
            if not previous.is_file():continue
            old=json.loads(previous.read_text(encoding='utf-8-sig'))
            old_detail={d['champ']:d for d in old['detail']}
            gained=[d['champ'] for d in score['detail'] if d['correct'] and not old_detail[d['champ']]['correct']]
            lost=[d['champ'] for d in score['detail'] if not d['correct'] and old_detail[d['champ']]['correct']]
            totals['champs_gagnes']+=len(gained);totals['champs_perdus']+=len(lost)
            changes.append(dict(document_id=row['document_id'],lecture=row['lecture'],champs_gagnes=gained,champs_perdus=lost,
                                articles_tp_avant=old['articles']['tp'],articles_tp_apres=score['articles']['tp']))
        comparison=dict(reference=str(cache),mode=summary['mode'],comptes_par_execution=totals,detail=changes,
                        note='Branches sources et fusion comparees separement; ne pas sommer pour une precision par document. Les OCR sont identiques; une regeneration peut modifier les propositions du modele.')
        (run/'comparaison_precedente.json').write_text(json.dumps(comparison,ensure_ascii=False,indent=2),encoding='utf-8')
    (run/'bilan.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'\nTERMINE : {run}\nEnvoyer resume.csv et bilan.json.',flush=True)


if __name__=='__main__':main()
