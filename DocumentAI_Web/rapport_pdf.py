"""Rapport d'extraction; ne constitue ni facture ni devis commercial."""
from pathlib import Path
from xml.sax.saxutils import escape
import re
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer

ROOT=Path(__file__).resolve().parent
LABELS={'fournisseur':'Fournisseur','ice_fournisseur':'ICE fournisseur','client':'Client',
        'ice_client':'ICE client','numero_document':'Numéro du document','date':'Date',
        'date_echeance':'Échéance','adresse_fournisseur':'Adresse fournisseur','adresse_client':'Adresse de facturation','adresse_livraison':'Adresse de livraison','taux_tva':'Taux TVA (%)',
        'devise':'Devise','total_ht':'Total HT','montant_tva':'TVA','total_ttc':'Total TTC','total_document':'Total (base non précisée)'}


def text(value):
    if value is None:return 'Non extrait'
    raw=str(value)
    if re.search(r'[\u0600-\u06ff]',raw):
        import arabic_reshaper
        from bidi.algorithm import get_display
        raw=get_display(arabic_reshaper.reshape(raw))
    return escape(raw).replace('\n','<br/>')


def export_pdf(document,controls,path):
    if 'DocAI' not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont('DocAI',str(ROOT/'assets/DejaVuSans.ttf')))
        pdfmetrics.registerFont(TTFont('DocAI-Bold',str(ROOT/'assets/DejaVuSans-Bold.ttf')))
        pdfmetrics.registerFontFamily('DocAI',normal='DocAI',bold='DocAI-Bold')
    navy=colors.HexColor('#183344');teal=colors.HexColor('#087e83')
    body=ParagraphStyle('body',fontName='DocAI',fontSize=8.5,leading=12,spaceAfter=6,splitLongWords=True)
    small=ParagraphStyle('small',parent=body,fontSize=7,leading=10)
    heading=ParagraphStyle('heading',parent=body,fontName='DocAI-Bold',fontSize=13,leading=18,textColor=teal,spaceBefore=12,spaceAfter=8)
    title=ParagraphStyle('title',parent=heading,fontSize=23,leading=30,textColor=navy)
    white=ParagraphStyle('white',parent=small,fontName='DocAI-Bold',textColor=colors.white)
    def p(v,style=body):return Paragraph(text(v),style)
    def table(headers,rows,widths):
        t=Table([[p(v,white) for v in headers]]+[[p(v,small) for v in r] for r in rows],colWidths=widths,repeatRows=1,hAlign='LEFT',splitInRow=1)
        t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),navy),('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.HexColor('#f0f5f6'),colors.white]),
            ('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),7),('RIGHTPADDING',(0,0),(-1,-1),7),
            ('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6)]))
        return t
    story=[p('Rapport d’extraction',title),p(document.get('document_id','Document')),
           p('Source : '+document.get('fichier_source','Document')),
           p('Type : '+document.get('type_document','inconnu')+' | Statut : '+document.get('statut','a_verifier')),
           p('Document de synthèse automatique. Ne remplace pas le document original ni une vérification humaine.',small),
           p('Champs du document',heading)]
    rows=[]
    for k,label in LABELS.items():
        value=document.get(k)
        if isinstance(value,(int,float)) and k in ['total_ht','montant_tva','total_ttc']:
            value=f'{value:,.2f}'.replace(',',' ').replace('.',',')+' '+str(document.get('devise') or '')
        rows.append([label,value])
    story.append(table(['Champ','Valeur extraite'],rows,[160,355]))
    lines=document.get('lignes',[])
    story.append(p(f'Articles et prestations ({len(lines)})',heading))
    if lines:
        # Explicit columns preserve HT/TTC even when both occur in a document.
        labels={'designation':'Désignation','quantite':'Qté','hauteur_cm':'H (cm)',
                'largeur_cm':'L (cm)','prix_unitaire_ht':'PU HT','montant_ht':'Montant HT',
                'prix_unitaire_ttc':'PU TTC','montant_ttc':'Montant TTC',
                'prix_unitaire':'PU (*)','montant':'Montant (*)','taux_tva':'TVA (%)','page':'Page'}
        keys=[k for k in labels if k in ['designation','quantite','page'] or any(l.get(k) is not None for l in lines)]
        names=labels
        neutral=False
        if any(l.get('prix_unitaire') is not None or l.get('montant') is not None for l in lines):
            story.append(p('(*) Base fiscale non précisée sur le document.'))
        rest=515-190-32-38
        count=len(keys)-3
        widths=[190 if k=='designation' else 32 if k=='quantite' else 38 if k=='page' else rest/max(count,1) for k in keys]
        def cell(line,key):
            value=line.get(key)
            if neutral and key in ['prix_unitaire','montant'] and value is None:
                ht=line.get('prix_unitaire_ht' if key=='prix_unitaire' else 'montant_ht')
                return None if ht is None else str(ht)+' HT'
            if key=='quantite' and value is not None and line.get('unite'):return str(value)+' '+line['unite']
            return value
        story.append(table([names[k] for k in keys],[[cell(l,k) for k in keys] for l in lines],widths))
    else:story.append(p('Aucune ligne structurée extraite. Consulter le texte OCR et les alertes.'))
    story.append(p('Alertes et limites',heading))
    alerts=controls.get('alertes',[])
    for i,a in enumerate(alerts or ['Aucune alerte enregistrée. Une revue humaine reste nécessaire.'],1):
        story.append(p(f'{i}. {a}'))
    pages=document.get('pages_traitees',[])
    story.extend([Spacer(1,10),p('Pages traitées : '+', '.join(map(str,pages)),small),
                  p('Les champs « Non extrait » restent inconnus. Aucun prix, taux de TVA ou montant manquant n’est déduit automatiquement.',small)])
    def footer(c,doc):
        c.setStrokeColor(colors.HexColor('#d5dfe3'));c.line(40,35,555,35)
        c.setFont('DocAI',7);c.setFillColor(navy);c.drawString(40,23,'Document AI - extraction à vérifier');c.drawRightString(555,23,f'Page {doc.page}')
    doc=SimpleDocTemplate(str(path),pagesize=(595.28,841.89),leftMargin=40,rightMargin=40,topMargin=35,bottomMargin=50,
                          title='Rapport d’extraction - '+document.get('document_id',''),author='Document AI')
    doc.build(story,onFirstPage=footer,onLaterPages=footer)
    return Path(path)
