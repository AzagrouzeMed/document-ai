"""Adaptateur experimental 2 : tableau et en-tete; moteur V6 inchange.
Placer a cote de extract_json.py. Memes arguments CLI que ce dernier.
Teste sur le modele public Wuro; aucune precision generale revendiquee.
"""
import hashlib
import json
import re
from datetime import date
from pathlib import Path
from statistics import median
import extract_json as engine

EXPECTED = '968eb9702afa0cf609646df1f1ae615ef950692eccb0d1ca175aca0d2763d52a'
original_numeric = engine.numeric
original_table = engine.table_geometry
original_header = engine.rules_header
original_supported = engine.header_supported
original_checks = engine.checks

INVOICE_TITLE = re.compile(
    r'^\s*FACTURE\s+(?:N[°ºo.]?\s*)?([A-Za-z0-9][A-Za-z0-9/-]*)'
    r'\s+du\s+(\d{2})/(\d{2})/(\d{4})\s*$', re.I)


def invoice_heading(text):
    match = INVOICE_TITLE.fullmatch(text)
    if not match:
        return None
    try:
        issued = date(int(match[4]), int(match[3]), int(match[2])).isoformat()
    except ValueError:
        return None
    return match[1], issued


def currencies(text):
    found = set()
    for pattern, code in [(r'€|\bEUR\b', 'EUR'), (r'\b(?:MAD|DH)\b', 'MAD'),
                          (r'\bUSD\b', 'USD')]:
        if re.search(pattern, text, re.I):
            found.add(code)
    return found


def supported_extended(key, value, ids, blockmap):
    if not ids or any(i not in blockmap for i in ids):
        return False
    if key in ['date', 'numero_facture']:
        # A normalized date is supported only by an explicit invoice heading,
        # not by a due date or by digits inferred from the invoice number.
        headings = [invoice_heading(blockmap[i]['texte']) for i in ids]
        headings = [v for v in headings if v is not None]
        if headings:
            return len(headings)==1 and value==headings[0][1 if key=='date' else 0]
    if key=='devise':
        found = set().union(*(currencies(blockmap[i]['texte']) for i in ids))
        if found:
            return found=={value}
    return original_supported(key, value, ids, blockmap)


def checks_extended(invoice, blocks):
    fields, proofs, alerts = original_checks(invoice, blocks)
    blockmap={b['id']: b for b in blocks}
    source_map={s.champ:s.blocs for s in invoice.sources}
    for key in ['date', 'devise']:
        value=fields.get(key)
        ids=source_map.get(key, [])
        if value is not None and supported_extended(key,value,ids,blockmap):
            proofs[key]['statut']='support_textuel'
            proofs[key]['normalisation']='date JJ/MM/AAAA vers ISO' if key=='date' else 'symbole monetaire vers code ISO'
            alerts=[a for a in alerts if a!=f'Valeur a verifier dans les blocs cites : {key}']
    return fields, proofs, alerts


def header_explicit_totals(blocks, table, parties):
    values, sources = original_header(blocks, table, parties)
    top=[b for b in blocks if b['bbox'][3]<table['y_debut']]
    headings=[(b,invoice_heading(b['texte'])) for b in top if invoice_heading(b['texte']) is not None]
    if headings:
        for key in ['numero_facture','date']:
            values[key]=None
            sources.pop(key,None)
        if len(headings)==1:
            b,parsed=headings[0]
            values['numero_facture'],values['date']=parsed
            sources['numero_facture']=sources['date']=[b['id']]

    # Conservative supplier fallback: name opposite a billing-address heading,
    # repeated literally in a legal footer. Never use a guessed company name.
    billing=[b for b in top if engine.norm(b['texte'])=='adressedefacturation']
    if values['fournisseur'] is None and len(billing)==1 and len(headings)==1:
        label=billing[0]
        height=label['bbox'][3]-label['bbox'][1]
        candidates=sorted([b for b in top if b['bbox'][2]<label['bbox'][0]-height and
            b['bbox'][1]>headings[0][0]['bbox'][3] and
            abs(engine.cy(b)-engine.cy(label))<height*2],key=lambda b:(engine.cy(b),engine.cx(b)))
        if candidates:
            name=candidates[0]
            text=name['texte'].strip()
            footer=[b for b in blocks if b['bbox'][1]>table['y_fin'] and
                    re.search(r'\b(?:SIRET|SIREN|RCS|capital)\b',b['texte'],re.I) and
                    text.casefold() in b['texte'].casefold()]
            if (len(footer)==1 and len(text)>=4 and any(c.isalpha() for c in text) and
                not re.search(r'[\d@\[\]]|\b(?:rue|avenue|adresse|telephone|facture)\b',text,re.I)):
                values['fournisseur']=text
                sources['fournisseur']=[name['id'],footer[0]['id']]

    # Reject template placeholders as party names; ICE checks remain unchanged.
    for key in ['client','fournisseur']:
        if values.get(key) and re.search(r'[\[\]]',values[key]):
            values[key]=None
            sources.pop(key,None)
    for key,label in [('total_ht','montanttotalht'),('montant_tva','montanttotaltva'),('total_ttc','montanttotalttc')]:
        titles=[b for b in blocks if engine.norm(b['texte'])==label and engine.cy(b)>=table['y_fin']]
        if not titles: continue
        # Never substitute one tax rate's subtotal for the total tax amount.
        values[key]=None
        sources.pop(key,None)
        if len(titles)!=1: continue
        title=titles[0]
        height=title['bbox'][3]-title['bbox'][1]
        cells=[b for b in blocks if b['bbox'][0]>title['bbox'][2] and
               abs(engine.cy(b)-engine.cy(title))<height*.7 and numeric_currency(b['texte']) is not None]
        if len(cells)==1:
            values[key]=numeric_currency(cells[0]['texte'])
            sources[key]=[cells[0]['id']]
    found=set().union(*(currencies(b['texte']) for b in blocks))
    if len(found)==1:
        code=next(iter(found))
        # Require a monetary value carrying that currency, not footer wording.
        cells=[b for b in blocks if currencies(b['texte'])=={code} and numeric_currency(b['texte']) is not None]
        if cells:
            values['devise']=code
            sources['devise']=[b['id'] for b in cells]
    elif len(found)>1:
        values['devise']=None
        sources.pop('devise',None)
    return values,sources


def numeric_currency(text):
    # Currency suffix only, never percentages, OCR letters or inferred amounts.
    return original_numeric(re.sub(r'\s*(?:€|EUR|MAD|DH|USD|\$)\s*$', '', str(text), flags=re.I))


def table_extended(blocks):
    """Fallback only for explicitly headed PU, quantity, HT, VAT, TTC tables."""
    try:
        return original_table(blocks)
    except ValueError:
        pass
    cx, cy, norm = engine.cx, engine.cy, engine.norm
    def unique(items, message):
        if len(items)!=1: raise ValueError(message)
        return items[0]
    title = unique([b for b in blocks if norm(b['texte']) in ['designation','description']],
                   'En-tete designation absent ou ambigu.')
    height = title['bbox'][3]-title['bbox'][1]
    band = [b for b in blocks if abs(cy(b)-cy(title))<height*.6]
    price = unique([b for b in band if norm(b['texte']) in ['puht','prixunitaireht']], 'En-tete prix ambigu.')
    qty = unique([b for b in band if norm(b['texte']) in ['qte','quantite']], 'En-tete quantite ambigu.')
    merged = unique([b for b in band if re.fullmatch(r'(?:u)?montanthttvamontantttc',norm(b['texte']))],
                    'Ordre explicite Montant HT / TVA / Montant TTC requis pour cet adaptateur.')
    if not cx(title)<cx(price)<cx(qty)<cx(merged):
        raise ValueError('Disposition non prise en charge par cet adaptateur.')
    endings = [b for b in blocks if cy(b)>cy(title)+height*2 and
               (norm(b['texte'])=='recapitulatif' or norm(b['texte']).startswith('datelimitedepaiement'))]
    if not endings: raise ValueError('Limite de tableau absente.')
    bottom = min(b['bbox'][1] for b in endings)
    cells = [b for b in blocks if title['bbox'][3]<cy(b)<bottom]
    numbers = [b for b in cells if numeric_currency(b['texte']) is not None]
    # Right edges group monetary cells consistently despite different digit counts.
    clusters = []
    for b in sorted(numbers,key=lambda b:b['bbox'][2]):
        if not clusters or b['bbox'][2]-median(v['bbox'][2] for v in clusters[-1])>height*1.2:
            clusters.append([])
        clusters[-1].append(b)
    if len(clusters)!=4: raise ValueError('Quatre colonnes numeriques PU/Qte/HT/TTC requises; revue necessaire.')
    price_cells, qty_cells, ht_cells, ttc_cells = clusters
    if (abs(median(cx(b) for b in price_cells)-cx(price))>height*1.5 or
        abs(median(cx(b) for b in qty_cells)-cx(qty))>height*1.5):
        raise ValueError('Cellules non alignees avec les en-tetes PU/Qte.')
    anchors = sorted(qty_cells,key=cy)
    if any(cy(b)-cy(a)<height*1.5 for a,b in zip(anchors,anchors[1:])):
        raise ValueError('Rangees ambigues.')
    # Every accepted row needs exactly one of each of the four numeric cells.
    rows=[]; sources=[]
    desc_right=min(b['bbox'][0] for b in price_cells)-height*.2
    warnings=['Adaptateur experimental : verifier les trois lignes et les champs generaux.',
              'Colonnes Unite, TVA et TTC exclues des designations; texte Objet hors lignes.']
    for i,anchor in enumerate(anchors):
        line={}; chosen=[]
        for key,column in [('quantite',qty_cells),('prix_unitaire_ht',price_cells),('montant_ht',ht_cells),('_ttc',ttc_cells)]:
            b=unique([b for b in column if abs(cy(b)-cy(anchor))<height*.65], 'Cellule manquante ou ambigue.')
            chosen.append(b)
            if key!='_ttc':
                line[key]=numeric_currency(b['texte'])
                sources.append(dict(champ=f'lignes.{i}.{key}',blocs=[b['id']]))
        top=min(b['bbox'][1] for b in chosen)-height*.2
        end=(min(b['bbox'][1] for b in [anchors[i+1]])-height*.2) if i+1<len(anchors) else bottom
        descriptions=sorted([b for b in cells if b['bbox'][2]<desc_right and top<cy(b)<end],key=lambda b:(cy(b),cx(b)))
        if not descriptions: raise ValueError('Designation absente; revue necessaire.')
        line['designation']=' '.join(b['texte'] for b in descriptions)
        sources.append(dict(champ=f'lignes.{i}.designation',blocs=[b['id'] for b in descriptions]))
        rows.append(line)
    return rows,sources,warnings,dict(y_debut=title['bbox'][1], y_fin=bottom,
        colonnes_numeriques={key:median(cx(b) for b in group) for key,group in
                            [('quantite',qty_cells),('prix_unitaire_ht',price_cells),('montant_ht',ht_cells)]},
        pente=0, adaptateur_experimental=True)


if __name__=='__main__':
    path=Path(engine.__file__)
    if hashlib.sha256(path.read_text(encoding='utf-8-sig').encode()).hexdigest()!=EXPECTED:
        raise SystemExit('extract_json.py V6 original requis; fichier different.')
    engine.numeric=numeric_currency
    engine.table_geometry=table_extended
    engine.rules_header=header_explicit_totals
    engine.header_supported=supported_extended
    engine.checks=checks_extended
    # Mark generated artifacts as experimental, not frozen V6 benchmark outputs.
    main=engine.main
    print('ADAPTATEUR DEMO 2 EXPERIMENTAL - en-tete et tableau; V6 conservee.',flush=True)
    # All JSON outputs written by the engine retain their names; add explicit provenance.
    original_write=Path.write_text
    def marked_write(self,data,*args,**kwargs):
        if self.name=='execution.json':
            value=json.loads(data)
            value.update(adaptateur='demo_experimental_2',version_script='6+demo2',
                         resultats_test_v6_applicables=False)
            data=json.dumps(value,ensure_ascii=False,indent=2)
        return original_write(self,data,*args,**kwargs)
    Path.write_text=marked_write
    main()
