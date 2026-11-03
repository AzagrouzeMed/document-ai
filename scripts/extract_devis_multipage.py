"""Experimental OCR-only extractor for quote pages with height/width/quantity.
Never interprets dimensions as prices. No OCR, LLM or reference annotation read.
Usage: python extract_devis_multipage.py --ocr page1.json page2.json
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
    end_labels=[b for b in blocks if cy(b)>cy(title) and
                norm(b['texte']).startswith(('totaldesprestations','arretedupresentdevis'))]
    bottom=min((b['bbox'][1] for b in continuation+end_labels),default=float('inf'))
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


def merge_pages(raws):
    prepared=[r.get('res',r) for r in raws]
    indexes=[r.get('page_index') for r in prepared]
    if any(type(i) is not int or i<0 for i in indexes):
        raise ValueError('Index de page OCR absent ou invalide.')
    if len(set(indexes))!=len(indexes): raise ValueError('Page dupliquee; regroupement refuse.')
    names={str(r.get('input_path','')).replace('\\','/').rsplit('/',1)[-1] for r in prepared}
    if len(names)!=1 or not next(iter(names)): raise ValueError('PDF sources differents ou absents.')
    prepared.sort(key=lambda r:r['page_index'])
    if [r['page_index'] for r in prepared]!=list(range(len(prepared))):
        raise ValueError('Pages non consecutives depuis la premiere page.')
    parsed=[extract(r) for r in prepared]
    result={k:v for k,v in parsed[0][0].items() if k not in ['lignes','page','extraction_document_complete']}
    result.update(lignes=[],pages_traitees=[r['page_index']+1 for r in prepared],
                  couverture_pages_complete=False,extraction_document_complete=False)
    proofs={}; alerts=['Extracteur experimental; revue humaine des champs et des dimensions requise.',
                       'Prix unitaires, HT et TVA non extraits; aucune repartition du TTC par ligne.']
    keys=['type_document','fournisseur','ice_fournisseur','client','numero_devis','date','section']
    for key in keys:
        seen={v[key] for v,_,_ in parsed if v.get(key) is not None}
        if len(seen)>1: raise ValueError(f'Conflit entre les pages : {key}.')
        result[key]=next(iter(seen)) if seen else None
        sources=[s[key] for _,s,_ in parsed if key in s]
        if sources: proofs[key]={'sources':sources}
    for values,source,controls in parsed:
        offset=len(result['lignes'])
        for line in values['lignes']:
            result['lignes'].append(dict(line,page=values['page']))
        for key,evidence in source.items():
            if key.startswith('lignes.'):
                _,number_,field=key.split('.',2)
                proofs[f'lignes.{offset+int(number_)}.{field}']=evidence
        alerts.extend(a for a in controls['alertes'] if a.startswith(('Cellule','Designation')))
    all_blocks=[]
    for r in prepared:
        for i,(t,b) in enumerate(zip(r['rec_texts'],r['rec_boxes']),1):
            all_blocks.append(dict(id=i,texte=t,bbox=b,page=r['page_index']+1))
    last=len(prepared)
    targets=[]
    for b in all_blocks:
        m=re.search(r'suite\s+du\s+tableau\s+en\s+page\s+(\d+)',b['texte'],re.I)
        if m:
            target=int(m[1]);targets.append(target)
            if target!=b['page']+1: raise ValueError('Renvoi de page incoherent.')
    covers=all(t<=last for t in targets)
    # An explicit total-of-prestations marker closes the last page's table.
    endings=[b for b in all_blocks if b['page']==last and
             norm(b['texte']).startswith('totaldesprestations')]
    coverage=covers and len(endings)==1
    result['couverture_pages_complete']=coverage
    if not coverage: alerts.append('Couverture des pages non confirmee; suite ou fin du tableau absente.')
    quantities=[b for b in endings if re.fullmatch(r'TOTAL\s+DES\s+PRESTATIONS\s*:\s*\d+\s+.+',b['texte'],re.I)]
    expected=None
    if len(quantities)==1:
        m=re.search(r':\s*(\d+)',quantities[0]['texte'])
        expected=int(m[1]);result['quantite_totale_annoncee']=expected
        proofs['quantite_totale_annoncee']=dict(page=last,blocs=quantities)
    # A total TTC is a document-level value, never a unit price or a line value.
    titles=[b for b in all_blocks if norm(b['texte'])=='prixtotalttc']
    totals=[]
    for title in titles:
        height=title['bbox'][3]-title['bbox'][1]
        candidates=[]
        for b in all_blocks:
            if b['page']!=title['page']: continue
            match=re.fullmatch(r'\s*(\d[\d \u00a0\u202f]*(?:[.,]\d{1,2})?)\s*(Dhs?|MAD|€|EUR)\s*',b['texte'],re.I)
            if (match and b['bbox'][1]>=title['bbox'][3] and
                b['bbox'][1]-title['bbox'][3]<height*2 and abs(cx(b)-cx(title))<height*2):
                amount=float(re.sub(r'[ \u00a0\u202f]','',match[1]).replace(',','.'))
                code='EUR' if match[2].upper() in ['€','EUR'] else 'MAD'
                candidates.append((b,amount,code))
        if len(candidates)==1: totals.append((title,*candidates[0]))
    if len(titles)==1 and len(totals)==1:
        title,b,amount,code=totals[0]
        result['total_ttc']=amount;result['devise']=code
        for key in ['total_ttc','devise']:
            proofs[key]=dict(page=b['page'],blocs=[title,b])
    elif titles: alerts.append('Total TTC absent ou ambigu; aucune valeur retenue.')
    extracted=sum(l['quantite'] for l in result['lignes']) if all(l['quantite'] is not None for l in result['lignes']) else None
    matching=expected is not None and extracted==expected
    if not matching: alerts.append('Quantite totale non confirmee par le total annonce.')
    result['extraction_document_complete']=False
    controls=dict(statut='a_verifier',alertes=alerts,nombre_articles=len(result['lignes']),
        nombre_pages=last,quantite_totale_extraite=extracted,quantite_totale_annoncee=expected,
        controle_quantite_total='coherent' if matching else 'a_verifier',
        couverture_pages_complete=coverage,extraction_document_complete=False,
        revue_humaine_requise=True,
        note='Regroupement des pages et extraction des dimensions; aucune validation generale ni extraction exhaustive des champs.')
    return result,proofs,controls


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ocr',type=Path,nargs='+',required=True)
    parser.add_argument('--output',type=Path,default=Path('demo/devis_complet'))
    args=parser.parse_args()
    run=args.output.resolve()/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    run.mkdir(parents=True,exist_ok=False)
    def save(name,value):
        (run/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    try:
        raws=[json.loads(path.read_text(encoding='utf-8-sig')) for path in args.ocr]
        values,proofs,controls=merge_pages(raws)
        save('devis.json',values);save('preuves.json',proofs);save('controles.json',controls)
        save('execution.json',dict(adaptateur='devis_dimensions_multipage_1',ocr=[str(p.resolve()) for p in args.ocr],
            generation_modele_executee=False,utilise_annotations_reference=False,
            resultats_test_v6_applicables=False))
    except Exception as exc:
        save('controles.json',dict(statut='echec_extraction',erreur=str(exc),revue_humaine_requise=True))
        raise SystemExit(f'ERREUR : {exc}\nDiagnostic : {run}')
    print(f"Articles : {len(values['lignes'])}; pages : {values['pages_traitees']}")
    print(json.dumps({k:v for k,v in values.items() if k!='lignes'},ensure_ascii=False,indent=2))
    print(f'TERMINE : {run}\nEnvoyer devis.json et controles.json.')


if __name__=='__main__': main()
