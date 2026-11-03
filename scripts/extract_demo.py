"""Adaptateur experimental de tableau; ne remplace pas le moteur V6.
Placer a cote de extract_json.py. Memes arguments CLI que ce dernier.
Teste sur le modele public Wuro; aucune precision generale revendiquee.
"""
import hashlib
import json
import re
from pathlib import Path
from statistics import median
import extract_json as engine

EXPECTED = '968eb9702afa0cf609646df1f1ae615ef950692eccb0d1ca175aca0d2763d52a'
original_numeric = engine.numeric
original_table = engine.table_geometry
original_header = engine.rules_header


def header_explicit_totals(blocks, table, parties):
    values, sources = original_header(blocks, table, parties)
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
    # Mark generated artifacts as experimental, not frozen V6 benchmark outputs.
    main=engine.main
    print('ADAPTATEUR DEMO EXPERIMENTAL - moteur V6 conserve, tableau adapte.',flush=True)
    # All JSON outputs written by the engine retain their names; add explicit provenance.
    original_write=Path.write_text
    def marked_write(self,data,*args,**kwargs):
        if self.name=='execution.json':
            value=json.loads(data)
            value.update(adaptateur='demo_experimental_1',version_script='6+demo1',
                         resultats_test_v6_applicables=False)
            data=json.dumps(value,ensure_ascii=False,indent=2)
        return original_write(self,data,*args,**kwargs)
    Path.write_text=marked_write
    main()
