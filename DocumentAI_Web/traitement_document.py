"""Point d'entree commun pour PDF/images et OCR archives.
Python 3.11. Les formats inconnus produisent un resultat partiel et le texte OCR.
Les moteurs sont isoles dans des sous-processus; la V6 historique reste intacte.
"""
import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parent
FIELDS=['fournisseur','ice_fournisseur','client','ice_client','numero_document','date',
        'devise','total_ht','montant_tva','total_ttc','date_echeance','adresse_fournisseur','adresse_client','adresse_livraison','taux_tva','total_document']


def save(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')


def read(path): return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def unwrap(value): return value.get('res',value)


def norm(text):
    return ''.join(c for c in unicodedata.normalize('NFKD',text).casefold()
                   if c.isalnum() and not unicodedata.combining(c))


def load_dimensions():
    spec=importlib.util.spec_from_file_location('dimensions',ROOT/'moteur'/'extract_devis_multipage.py')
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    return mod


def schema(raw):
    raw=unwrap(raw);texts=raw.get('rec_texts',[])
    labels={norm(t) for t in texts}
    text='\n'.join(texts)
    quote=bool(re.search(r'(?im)^\s*devis\b',text))
    invoice=bool(re.search(r'(?im)^\s*facture\b|فاتورة',text))
    kind='devis' if quote and not invoice else ('facture' if invoice and not quote else 'inconnu')
    dims={'hauteurcm','largeurcm','quantite'}.issubset(labels)
    return dict(type_document=kind,tableau='dimensions' if dims else 'a_identifier',
                methode='libelles OCR et geometrie; identification heuristique')


def raw_blocks(raw,page):
    r=unwrap(raw)
    return [dict(id=i,page=page,texte=t,bbox=b) for i,(t,b) in
            enumerate(zip(r.get('rec_texts',[]),r.get('rec_boxes',[])),1)]


def partial_header(raw,page):
    """Only explicit unique heading values; no guessing missing party roles."""
    blocks=raw_blocks(raw,page);values={k:None for k in FIELDS};proofs={}
    patterns={
        'client':r'^Client\s*:\s*(.+)$',
        'fournisseur':r'^Fournisseur\s*:\s*(.+)$',
        'numero_document':r'^(?:Devis|Facture)\s*(?:N[°ºo.]?)?\s*:\s*(.+)$',
        'date':r'^Date(?:\s+(?:de\s+)?(?:facture|devis))?\s*:\s*(\d{2}/\d{2}/\d{4}|\d{4}-\d{2}-\d{2})$',
    }
    for key,pat in patterns.items():
        found=[(b,re.fullmatch(pat,b['texte'].strip(),re.I)) for b in blocks]
        found=[(b,m) for b,m in found if m]
        if len(found)!=1:continue
        b,m=found[0];value=m[1].strip()
        if '[' in value or ']' in value:continue
        if key=='date':
            try:value=datetime.strptime(value,'%d/%m/%Y' if '/' in value else '%Y-%m-%d').date().isoformat()
            except ValueError:continue
        values[key]=value;proofs[key]=dict(page=page,blocs=[b])
    return values,proofs


def ocr_document(source,run,lang):
    from paddleocr import PaddleOCR
    pages={}
    languages=['fr','ar'] if lang=='auto' else [lang]
    recognizers={'fr':'latin_PP-OCRv5_mobile_rec','ar':'arabic_PP-OCRv5_mobile_rec'}
    for language in languages:
        print(f'OCR {language} : chargement...',flush=True)
        model=PaddleOCR(device='cpu',text_detection_model_name='PP-OCRv5_mobile_det',
            text_recognition_model_name=recognizers[language],use_doc_orientation_classify=False,
            use_doc_unwarping=False,use_textline_orientation=False)
        for index,result in enumerate(model.predict(input=str(source))):
            path=run/'ocr'/f'page_{index+1}_{language}.json';path.parent.mkdir(exist_ok=True)
            result.save_to_json(str(path));raw=unwrap(read(path))
            # Archive a stable source identity and page index for later grouping.
            raw['input_path']=str(source);raw['page_index']=index;save(path,raw)
            pages.setdefault(index,{})[language]=path
        del model
        import gc
        gc.collect()
    if not pages:raise ValueError('Aucune page OCR produite.')
    if lang=='auto' and any(set(v)!={'fr','ar'} for v in pages.values()):
        raise ValueError('Les lectures FR et AR ne couvrent pas les memes pages.')
    return pages


def cached_pages(paths):
    pages={};names=set()
    for p in paths:
        r=unwrap(read(p));index=r.get('page_index')
        if type(index) is not int or index<0:raise ValueError('OCR archive : page_index requis.')
        if index in pages:raise ValueError('Deux OCR archives pour une meme page.')
        if len(r.get('rec_texts',[]))!=len(r.get('rec_boxes',[])) or not r.get('rec_texts'):
            raise ValueError('OCR archive invalide.')
        names.add(str(r.get('input_path','')).replace('\\','/').rsplit('/',1)[-1])
        pages[index]={'cache':p.resolve()}
    if len(names)!=1 or not next(iter(names)):raise ValueError('Les OCR doivent provenir du meme document.')
    return pages


def choose(variants):
    if 'cache' in variants:return variants['cache'],None,'cache'
    if len(variants)==1:
        language=next(iter(variants));return variants[language],None,language
    ar=' '.join(unwrap(read(variants['ar'])).get('rec_texts',[]))
    arabic=sum('\u0600'<=c<='\u06ff' for c in ar)
    if arabic>=10 and arabic/max(1,sum(c.isalpha() for c in ar))>=.10:
        fr=' '.join(unwrap(read(variants['fr'])).get('rec_texts',[]))
        if sum(c.isascii() and c.isalpha() for c in fr)>=15:
            return variants['ar'],variants['fr'],'fusion'
        return variants['ar'],None,'ar'
    return variants['fr'],None,'fr'


def attach_page(value,page):
    if isinstance(value,dict):return {**{k:attach_page(v,page) for k,v in value.items()},'page':page}
    if isinstance(value,list):return [attach_page(v,page) for v in value]
    return value


def financial_page(primary,secondary,page,run,sans_llm):
    target=run/'pages'/f'page_{page}';target.mkdir(parents=True,exist_ok=True)
    cmd=[sys.executable,str(ROOT/'moteur'/'extract_demo_v2.py'),'--ocr',str(primary),'--output',str(target)]
    if secondary:cmd+=['--ocr-secondaire',str(secondary)]
    if sans_llm:cmd+=['--sans-llm']
    env=os.environ.copy();env['PYTHONUTF8']='1'
    proc=subprocess.run(cmd,capture_output=True,text=True,encoding='utf-8',errors='replace',env=env,timeout=660)
    (target/'extraction.log').write_text(proc.stdout+'\n'+proc.stderr,encoding='utf-8')
    files=list(target.glob('*/facture.json'))
    if proc.returncode or len(files)!=1:
        raise ValueError('Structure de tableau non prise en charge ou extraction interrompue; voir le journal de page.')
    folder=files[0].parent;value=read(files[0]);controls=read(folder/'controles.json')
    value['numero_document']=value.pop('numero_facture',None)
    source=read(folder/'preuves.json')
    if 'numero_facture' in source:source['numero_document']=source.pop('numero_facture')
    return value,attach_page(source,page),controls['alertes']


def process_pages(pages,run,sans_llm,source=None):
    dim=load_dimensions();chosen=[];routes=[]
    for index,variants in sorted(pages.items()):
        primary,secondary,language=choose(variants)
        raw=read(primary);structure=schema(raw)
        # A clear dimensions schema in the French reading is preferred for that schema only.
        if 'fr' in variants and schema(read(variants['fr']))['tableau']=='dimensions':
            primary=variants['fr'];secondary=None;raw=read(primary);structure=schema(raw);language='fr'
        chosen.append((index,primary,secondary,raw,structure,language))
    if chosen and all(v[4]['tableau']=='dimensions' for v in chosen):
        try:
            data,proofs,controls=dim.merge_pages([v[3] for v in chosen])
            data['numero_document']=data.pop('numero_devis',None)
            for v in chosen:routes.append(dict(page=v[0]+1,lecture=v[5],structure=v[4],moteur='dimensions_multipage'))
            return data,proofs,controls['alertes'],routes,False
        except ValueError as exc:
            # Keep page-level data if grouping is ambiguous; never silently hide the issue.
            grouping_error=str(exc)
    else:grouping_error=None
    outputs=[];proofs={};alerts=[];partial=False
    if grouping_error:alerts.append('Regroupement refuse : '+grouping_error);partial=True
    for index,primary,secondary,raw,structure,language in chosen:
        page=index+1
        print(f'Page {page} : {structure["type_document"]}, {structure["tableau"]}, lecture={language}',flush=True)
        route=dict(page=page,lecture=language,structure=structure)
        try:
            if structure['tableau']=='dimensions':
                data,src,control=dim.extract(raw);notices=control['alertes'];route['moteur']='dimensions_page'
                data['numero_document']=data.pop('numero_devis',None)
            elif source is not None and not sans_llm:
                from moteur.extraction_gemini import extract as gemini
                try:
                    data,src,notices=gemini(source,index,raw,run/'gemini'/f'page_{page}')
                    route['moteur']='gemini_image_et_ocr'
                    if any(data.get(k) is None for k in ['fournisseur','client','numero_document','date']) or not data.get('lignes'):
                        partial=True
                except Exception as api_error:
                    # No silent return to Qwen: preserve a local partial OCR result.
                    from moteur.extraction_ocr_prioritaire import direct
                    data,src,notices=direct(raw,page)
                    notices=['Gemini indisponible; extraction OCR locale partielle conservee : '+str(api_error)]+notices
                    route['moteur']='ocr_local_apres_echec_gemini';partial=True
            else:
                from moteur.extraction_ocr_prioritaire import direct
                data,src,notices=direct(raw,page)
                route['moteur']='ocr_local_sans_api'
                if any(data.get(k) is None for k in ['fournisseur','client','numero_document','date']) or not data.get('lignes'):partial=True
                if data.get('type_document')=='inconnu':data['type_document']=structure['type_document']
            lines=data.get('lignes',[])
            for line in lines:line['page']=page
        except Exception as exc:
            from moteur.extraction_souple import extract
            data,src,notices=extract(raw,page)
            data['type_document']=structure['type_document']
            notices=[str(exc),'Extraction de secours utilisee; mode visuel non abouti ou moteur geometrique non applicable.']+notices
            partial=True;route['moteur']='extraction_souple_partielle'
        outputs.append(dict(page=page,donnees=data));proofs[f'page_{page}']=src
        alerts.extend(f'Page {page} : {a}' for a in notices);routes.append(route)
    result={k:None for k in FIELDS};result['lignes']=[]
    types={v['donnees'].get('type_document','inconnu') for v in outputs}-{'inconnu'}
    result['type_document']=next(iter(types)) if len(types)==1 else 'inconnu'
    for key in FIELDS:
        candidates=[v['donnees'][key] for v in outputs if v['donnees'].get(key) is not None]
        distinct={json.dumps(v,ensure_ascii=False,sort_keys=True) for v in candidates}
        if len(distinct)==1:result[key]=candidates[0]
        elif len(distinct)>1:alerts.append(f'Conflit entre pages : {key}; valeur globale laissee vide.');partial=True
    for v in outputs:result['lignes'].extend(v['donnees'].get('lignes',[]))
    result['resultats_par_page']=outputs
    if [v[0] for v in chosen]!=list(range(len(chosen))):
        alerts.append('Pages manquantes : les pages archivees ne commencent pas a zero ou ne sont pas consecutives.');partial=True
    # Arbitrary PDFs lack a trusted end marker: processed pages are not proof of document completeness.
    result['extraction_document_complete']=False
    return result,proofs,alerts,routes,partial


def traiter_document(input_path=None,ocr_cache=None,output=None,lang='auto',sans_llm=False):
    source=Path(input_path).resolve() if input_path else None
    if source and (not source.is_file() or source.suffix.lower() not in ['.pdf','.png','.jpg','.jpeg','.bmp','.tif','.tiff','.webp']):
        raise ValueError('Fournir un PDF ou une image existante.')
    if bool(source)==bool(ocr_cache):raise ValueError('Fournir soit input_path, soit ocr_cache.')
    doc_id='DOC-'+uuid.uuid4().hex[:12].upper()
    run=Path(output or ROOT/'results'/'documents').resolve()/doc_id;run.mkdir(parents=True,exist_ok=False)
    try:
        pages=cached_pages([Path(p) for p in ocr_cache]) if ocr_cache else ocr_document(source,run,lang)
        data,proofs,alerts,routes,partial=process_pages(pages,run,sans_llm,source)
        for key in FIELDS:data.setdefault(key,None)
        from moteur.extraction_vision import Line
        for line in data.get('lignes',[]):
            for key in Line.model_fields:line.setdefault(key,None)
        data.update(document_id=doc_id,fichier_source=source.name if source else 'OCR archives',
            pages_traitees=[i+1 for i in sorted(pages)],statut='partiel' if partial else 'a_verifier')
        alerts.append('Revue humaine requise; les scores de la V6 ne mesurent pas cette nouvelle chaine unifiee.')
        controls=dict(statut=data['statut'],revue_humaine_requise=True,alertes=alerts,
                      nombre_articles=len(data.get('lignes',[])),nombre_pages=len(pages))
        text=[]
        for index,variants in sorted(pages.items()):
            for language,path in variants.items():
                text.append(f'PAGE {index+1} / {language}\n'+'\n'.join(unwrap(read(path)).get('rec_texts',[])))
        (run/'texte_ocr.txt').write_text('\n\n'.join(text),encoding='utf-8')
        save(run/'document.json',data);save(run/'preuves.json',proofs);save(run/'controles.json',controls)
        save(run/'execution.json',dict(version='gemini_api_experimentale_1',routes=routes,
            mode_demande='OCR_local_sans_API' if sans_llm else 'Gemini_image_et_OCR' if source else 'OCR_cache_direct',
            ocr_reutilise=bool(ocr_cache),langue_demandee=lang,
            sha256_scripts={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'moteur').glob('*.py')}))
        print(f'TERMINE : {run}\nStatut : {data["statut"]}; pages : {len(pages)}; lignes : {controls["nombre_articles"]}',flush=True)
        return run
    except Exception as exc:
        save(run/'controles.json',dict(statut='echec',erreur=str(exc),revue_humaine_requise=True))
        raise RuntimeError(f'{exc}\nDiagnostic : {run}') from exc


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--input',type=Path,help='PDF ou image; OCR automatique FR/AR.')
    group.add_argument('--ocr-cache',type=Path,nargs='+',help='Reutiliser un OCR par page; aucun nouvel OCR.')
    parser.add_argument('--output',type=Path)
    parser.add_argument('--lang',choices=['auto','fr','ar'],default='auto')
    parser.add_argument('--sans-llm',action='store_true')
    args=parser.parse_args()
    try:traiter_document(args.input,args.ocr_cache,args.output,args.lang,args.sans_llm)
    except Exception as exc:raise SystemExit(str(exc))


if __name__=='__main__':main()
