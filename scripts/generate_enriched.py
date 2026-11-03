"""Generate three fictitious invoices, with local assets and source annotations."""
import json
from decimal import Decimal
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, select_autoescape
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
FR = dict(title='FACTURE',supplier='Émetteur',client='Facturé à',contact='Contact',contact_name='Service achats',date='Date de facture',due='Échéance',order='Bon de commande',currency='Devise',ref='Réf.',item='Désignation',qty='Qté',unit='PU HT',amount='Montant HT',payment='RÈGLEMENT',terms='Conditions',net30='30 jours nets',bank='Banque',delivery='Livraison',ht='Total HT',tax='TVA 20 %',ttc='Total TTC',stamp='SERVICE COMPTABILITÉ',signature='Signature du responsable',simulated='Paraphe fictif · spécimen',note='Merci pour votre confiance. Indiquer la référence de facture lors du règlement. Tous les montants sont exprimés en MAD.',page='Page')
AR = dict(title='فاتورة',supplier='المورد',client='الزبون',contact='جهة الاتصال',contact_name='مصلحة المشتريات',date='تاريخ الفاتورة',due='تاريخ الاستحقاق',order='طلب الشراء',currency='العملة',ref='المرجع',item='البيان',qty='الكمية',unit='ثمن الوحدة',amount='المبلغ دون الضريبة',payment='معلومات الأداء',terms='الشروط',net30='الأداء خلال 30 يوما',bank='البنك',delivery='التسليم',ht='دون الضريبة',tax='الضريبة 20 %',ttc='شامل الضريبة',stamp='مصلحة المحاسبة',signature='توقيع المسؤول',simulated='توقيع تجريبي',note='شكرا على ثقتكم. يرجى ذكر مرجع الفاتورة عند الأداء. جميع المبالغ بالدرهم المغربي.',page='الصفحة')
ITEMS = [('PAP-A4','Ramette papier A4','رزمة ورق A4',10,'50.00'),('TON-01','Cartouche toner noir','خرطوشة حبر أسود',2,'350.00'),('CLAS-08','Classeur à levier','ملف برافعة',8,'25.00'),('LIV-01','Frais de livraison','مصاريف التسليم',1,'50.00')]

def generate(browser, env, index, lang):
    is_ar = lang == 'ar'
    labels = AR if is_ar else FR if lang == 'fr' else {k: FR[k] + ' / ' + AR[k] for k in FR}
    lines = []
    for ref, fr, ar, qty, price in ITEMS:
        lines.append(dict(reference=ref, designation=ar if is_ar else fr if lang=='fr' else fr+' / '+ar,quantite=qty,prix_unitaire_ht=float(price),montant_ht=float(Decimal(price)*qty)))
    ht = sum(Decimal(str(x['montant_ht'])) for x in lines)
    vat = (ht*Decimal('0.20')).quantize(Decimal('0.01'))
    f = dict(fournisseur='أطلس للتوريدات' if is_ar else 'Atlas Fournitures SARL',adresse_fournisseur='12، شارع المثال، مراكش، المغرب' if is_ar else '12, avenue Exemple, Marrakech, Maroc',ice=f'900000000000{index:03d}',numero_facture=f'ENR-2026-{index:03d}',date='2026-10-20',date_echeance='2026-11-19',date_livraison='2026-10-20',bon_commande=f'BC-DEMO-{index:03d}',devise='MAD',client=dict(nom='واحة للخدمات' if is_ar else 'Oasis Services SARL',adresse='24، شارع النموذج، الدار البيضاء' if is_ar else '24, rue Modèle, Casablanca, Maroc',ice='900000000000099'),mode_paiement='تحويل بنكي' if is_ar else 'Virement bancaire',rib='000000000000000000000000',lignes=lines,total_ht=float(ht),montant_tva=float(vat),total_ttc=float(ht+vat))
    doc_id=f'facture_enrichie_{index:03d}'
    html=env.get_template('facture.html').render(lang='ar' if is_ar else 'fr',direction='rtl' if is_ar else 'ltr',t=labels,f=f)
    page=browser.new_page(viewport=dict(width=794,height=1123),device_scale_factor=1)
    try:
        page.set_content(html)
        page.evaluate('document.fonts.ready')
        # Preserve a single coordinate system across PNG and PDF, independent of CSS DPI rounding.
        zones=page.locator('[data-field], [data-region]').evaluate_all('''els => els.map(el => {const r=el.getBoundingClientRect();return {champ:el.dataset.field || null,type:el.dataset.region || 'champ',page:1,bbox:[r.left,r.top,r.right,r.bottom]}})''')
        for z in zones:
            x0,y0,x1,y1=z['bbox']
            assert 0<=x0<x1<=794 and 0<=y0<y1<=1123, z
        assert page.locator('.note').bounding_box()['y'] + page.locator('.note').bounding_box()['height'] < page.locator('footer').bounding_box()['y'], 'Footer overlap'
        out=ROOT/'data'/'raw'
        out.mkdir(parents=True,exist_ok=True)
        (out/f'{doc_id}.html').write_text(html,encoding='utf-8')
        page.screenshot(path=str(out/f'{doc_id}.png'))
        page.pdf(path=str(out/f'{doc_id}.pdf'),width='794px',height='1123px',print_background=True)
        question='ما هو المجموع شامل الضريبة؟' if is_ar else 'Quel est le montant total TTC ?'
        source=next(z for z in zones if z['champ']=='total_ttc')
        a=dict(document_id=doc_id,langue=lang,synthetique=True,template_id='enriched_01',famille='pilot_enriched',image=f'{doc_id}.png',image_dimensions=dict(width=794,height=1123),bbox_format='xyxy',bbox_unite='pixels_image',pdf_dimensions_css_px=dict(width=794,height=1123),champs=f,zones=zones,questions_reponses=[dict(question=question,reponse=f"{f['total_ttc']:.2f} MAD",champ='total_ttc',page=1,bbox=source['bbox'])])
        ann=ROOT/'data'/'annotations';ann.mkdir(parents=True,exist_ok=True)
        (ann/f'{doc_id}.json').write_text(json.dumps(a,ensure_ascii=False,indent=2),encoding='utf-8')
        print(f'OK : {doc_id} ({lang})')
    finally:
        page.close()

if __name__=='__main__':
    env=Environment(loader=FileSystemLoader(ROOT/'templates'),autoescape=select_autoescape(['html']))
    env.filters['money']=lambda v: f'{v:,.2f}'.replace(',',' ').replace('.',',')
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        try:
            for i, lang in enumerate(['fr','ar','fr-ar'],1):
                generate(browser,env,i,lang)
        finally:
            browser.close()
    print('Termine : 3 PDF, 3 PNG, 3 HTML et 3 JSON.')
