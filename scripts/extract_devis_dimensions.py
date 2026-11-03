"""Experimental OCR-only extractor for a quote page with height/width/quantity.
Never interprets dimensions as prices. No OCR, LLM or reference annotation read.
Usage: python extract_devis_dimensions.py --ocr devis_page_1_ocr.json
"""
import argparse
import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from statistics import median


def norm(text):
    return ''.join(c for c in unicodedata.normalize('NFKD',text).casefold()
                   if c.isalnum() and not unicodedata.combining(c))


def cx(b): return (b['bbox'][0]+b['bbox'][2])/2
def cy(b): return (b['bbox'][1]+b['bbox'][3])/2


def number(text):
    text=text.strip()
    if not re.fullmatch(r'[0-9]+(?:[.,][0-9]+)?',text): return None
    return float(text.replace(',','.'))


def extract(raw):
    raw=raw.get('res',raw)
    texts,boxes=raw.get('rec_texts',[]),raw.get('rec_boxes',[])
    if not texts or len(texts)!=len(boxes): raise ValueError('Blocs OCR invalides.')
    blocks=[dict(id=i,texte=t,bbox=box) for i,(t,box) in enumerate(zip(texts,boxes),1)]
    proofs={}
    def one(items,label):
        if len(items)!=1: raise ValueError(f'{label} absent ou ambigu.')
        return items[0]
    title=one([b for b in blocks if norm(b['texte']) in
               ['designationdesprestations','designation','descriptiondesprestations']], 'Titre du tableau')
    h=title['bbox'][3]-title['bbox'][1]
    band=[b for b in blocks if abs(cy(b)-cy(title))<h]
    columns={key:one([b for b in band if norm(b['texte'])==label],label)
             for key,label in [('hauteur_cm','hauteurcm'),('largeur_cm','largeurcm'),('quantite','quantite')]}
    ordered=[title]+[columns[k] for k in ['hauteur_cm','largeur_cm','quantite']]
    if any(cx(a)>=cx(b) for a,b in zip(ordered,ordered[1:])):
        raise ValueError('Ordre designation/hauteur/largeur/quantite non confirme.')
    centers=[cx(b) for b in ordered]
    edges=[(a+b)/2 for a,b in zip(centers,centers[1:])]
    continuation=[b for b in blocks if cy(b)>cy(title) and
                  re.search(r'suite\s+du\s+tableau\s+en\s+page\s+\d+',b['texte'],re.I)]
    bottom=min((b['bbox'][1] for b in continuation),default=float('inf'))
    cells=[b for b in blocks if title['bbox'][3]<cy(b)<bottom]
    # Assign each standalone number only to the column under its heading.
    groups={k:[] for k in columns}
    for b in cells:
        if number(b['texte']) is None or cx(b)<edges[0]: continue
        key='hauteur_cm' if cx(b)<edges[1] else ('largeur_cm' if cx(b)<edges[2] else 'quantite')
        groups[key].append(b)
    anchors=sorted(groups['quantite'],key=cy)
    if not anchors: raise ValueError('Aucune ligne de quantite detectee.')
    if any(cy(b)-cy(a)<h*1.2 for a,b in zip(anchors,anchors[1:])):
        raise ValueError('Deux quantites sur une meme rangee; revue necessaire.')
    lines=[]; alerts=['Extracteur experimental de devis a dimensions; verification humaine requise.',
                      'Les dimensions en cm ne sont pas des prix; aucun montant calcule.']
    for i,anchor in enumerate(anchors):
        line={};source={}
        for key,group in groups.items():
            matches=[b for b in group if abs(cy(b)-cy(anchor))<h*.8]
            line[key]=number(matches[0]['texte']) if len(matches)==1 else None
            source[key]=[b['id'] for b in matches]
            if len(matches)!=1: alerts.append(f'Cellule {key} absente ou ambigue a la ligne {i+1}.')
        desc=[b for b in cells if cx(b)<edges[0] and abs(cy(b)-cy(anchor))<h*.8]
        desc.sort(key=lambda b:(cy(b),cx(b)))
        line['designation']=' '.join(b['texte'] for b in desc) or None
        if not desc: alerts.append(f'Designation absente a la ligne {i+1}.')
        source['designation']=[b['id'] for b in desc]
        line.update(prix_unitaire_ht=None,montant_ht=None)
        lines.append(line)
        for key,ids in source.items(): proofs[f'lignes.{i}.{key}']=ids
    top=[b for b in blocks if b['bbox'][3]<title['bbox'][1]]
    values=dict(type_document='devis',fournisseur=None,ice_fournisseur=None,client=None,
                numero_devis=None,date=None,devise=None,total_ht=None,montant_tva=None,total_ttc=None)
    for key,pattern in [('client',r'^Client\s*:\s*(.+)$'),
                        ('numero_devis',r'^Devis\s*:\s*(.+)$'),
                        ('date',r'^Date\s*:\s*(\d{2}/\d{2}/\d{4})$'),
                        ('ice_fournisseur',r'^ICE\s*:\s*(\d{15})$')]:
        matches=[(b,re.fullmatch(pattern,b['texte'].strip(),re.I)) for b in top]
        matches=[(b,m) for b,m in matches if m]
        if len(matches)==1:
            b,m=matches[0];value=m[1].strip()
            if key=='date': value=datetime.strptime(value,'%d/%m/%Y').date().isoformat()
            values[key]=value;proofs[key]=[b['id']]
    # Company legal suffix joins OCR fragments on the same top-left heading row.
    legal=[b for b in top if norm(b['texte']) in ['sarl','sa','sasu','eurl'] and cx(b)<edges[0]]
    if len(legal)==1:
        label=legal[0];height=label['bbox'][3]-label['bbox'][1]
        names=sorted([b for b in top if b['bbox'][2]<=label['bbox'][0] and
                      abs(cy(b)-cy(label))<height*.5],key=lambda b:b['bbox'][0])
        if names and all(not re.search(r'@|\d|:',b['texte']) for b in names):
            names=names+[label];values['fournisseur']=' '.join(b['texte'] for b in names)
            proofs['fournisseur']=[b['id'] for b in names]
    # ICE ownership is accepted only in the company header before the Client band.
    if values['ice_fournisseur'] is not None:
        clientblocks=[b for b in top if re.match(r'^Client\s*:',b['texte'],re.I)]
        ice=next(b for b in blocks if b['id']==proofs['ice_fournisseur'][0])
        if values['fournisseur'] is None or not clientblocks or cy(ice)>=min(cy(b) for b in clientblocks):
            values['ice_fournisseur']=None;proofs.pop('ice_fournisseur',None)
    sections=[b for b in cells if cx(b)<edges[0] and cy(b)<cy(anchors[0])-h and
              number(b['texte']) is None]
    sections.sort(key=cy)
    values.update(section=' '.join(b['texte'] for b in sections) or None,lignes=lines)
    if sections: proofs['section']=[b['id'] for b in sections]
    # This tool extracts one page. It never declares the whole quote complete.
    page_index=raw.get('page_index')
    page_number=page_index+1 if isinstance(page_index,int) and page_index>=0 else None
    alerts.append('Une seule page traitee; aucun regroupement multipage effectue.')
    if continuation: alerts.append('Suite du tableau annoncee; document incomplet sans la page suivante.')
    alerts.append('Prix, devise et totaux non extraits par cet adaptateur de dimensions.')
    values.update(page=page_number,extraction_document_complete=False)
    byid={b['id']:b for b in blocks}
    evidence={key:dict(page=page_number,blocs=[byid[i] for i in ids]) for key,ids in proofs.items()}
    return values,evidence,dict(statut='a_verifier',alertes=alerts,nombre_articles=len(lines),
        revue_humaine_requise=True,extraction_document_complete=False)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ocr',type=Path,required=True)
    parser.add_argument('--output',type=Path,default=Path('demo/test_devis_dimensions'))
    args=parser.parse_args()
    run=args.output.resolve()/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    run.mkdir(parents=True,exist_ok=False)
    def save(name,value):
        (run/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    try:
        raw=json.loads(args.ocr.read_text(encoding='utf-8-sig'))
        values,proofs,controls=extract(raw)
        save('devis.json',values);save('preuves.json',proofs);save('controles.json',controls)
        save('execution.json',dict(adaptateur='devis_dimensions_1',ocr=str(args.ocr.resolve()),
            generation_modele_executee=False,utilise_annotations_reference=False,
            resultats_test_v6_applicables=False))
    except Exception as exc:
        save('controles.json',dict(statut='echec_extraction',erreur=str(exc),revue_humaine_requise=True))
        raise SystemExit(f'ERREUR : {exc}\nDiagnostic : {run}')
    print(f"Articles : {len(values['lignes'])}; page : {values['page']}")
    print(json.dumps({k:v for k,v in values.items() if k!='lignes'},ensure_ascii=False,indent=2))
    print(f'TERMINE : {run}\nEnvoyer devis.json et controles.json.')


if __name__=='__main__': main()
