import json
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
ANNOTATIONS = ROOT / "data" / "annotations"

RAW.mkdir(parents=True, exist_ok=True)
ANNOTATIONS.mkdir(parents=True, exist_ok=True)

VERSIONS = [
    {
        "langue": "fr",
        "direction": "ltr",
        "titre": "FACTURE",
        "fournisseur": "Atlas Fournitures",
        "designation": "Ramette papier A4",
        "labels": [
            "Fournisseur", "ICE", "Numéro", "Date",
            "Désignation", "Quantité", "Prix unitaire HT",
            "Montant HT", "Total HT", "TVA 20 %", "Total TTC",
        ],
    },
    {
        "langue": "ar",
        "direction": "rtl",
        "titre": "فاتورة",
        "fournisseur": "أطلس للتوريدات",
        "designation": "رزمة ورق A4",
        "labels": [
            "المورد", "المعرف الموحد للمقاولة", "رقم الفاتورة", "التاريخ",
            "البيان", "الكمية", "ثمن الوحدة دون الضريبة",
            "المبلغ دون الضريبة", "المجموع دون الضريبة",
            "الضريبة 20 %", "المجموع شامل الضريبة",
        ],
    },
    {
        "langue": "fr-ar",
        "direction": "ltr",
        "titre": "FACTURE / فاتورة",
        "fournisseur": "Atlas Fournitures / أطلس للتوريدات",
        "designation": "Papier A4 / ورق A4",
        "labels": [
            "Fournisseur / المورد", "ICE / المعرف الموحد",
            "Numéro / الرقم", "Date / التاريخ",
            "Désignation / البيان", "Quantité / الكمية",
            "Prix HT / ثمن الوحدة", "Montant HT / المبلغ",
            "Total HT / دون الضريبة", "TVA / الضريبة",
            "Total TTC / شامل الضريبة",
        ],
    },
]


def generate(browser, version, index):
    doc_id = f"facture_{index:03d}"
    labels = version["labels"]

    fields = {
        "fournisseur": version["fournisseur"],
        "ice": f"900000000000{index:03d}",
        "numero_facture": f"DEMO-2026-{index:03d}",
        "date": "2026-10-20",
        "devise": "MAD",
        "lignes": [{
            "designation": version["designation"],
            "quantite": 10,
            "prix_unitaire_ht": 50.00,
            "montant_ht": 500.00,
        }],
        "total_ht": 500.00,
        "montant_tva": 100.00,
        "total_ttc": 600.00,
    }

    html = f"""
    <!doctype html>
    <html lang="{version['langue'].split('-')[0]}"
          dir="{version['direction']}">
    <head>
      <meta charset="UTF-8">
      <style>
        @page {{ size: A4; margin: 0; }}
        * {{ box-sizing: border-box; }}
        body {{
          margin: 0;
          padding: 48px;
          width: 794px;
          height: 1123px;
          background: white;
          color: #18283b;
          font-family: Arial, sans-serif;
          font-size: 16px;
          line-height: 1.7;
        }}
        h1 {{ color: #175e78; font-size: 32px; }}
        .notice {{ color: #666; font-size: 13px; }}
        .identity {{ margin: 35px 0; }}
        table {{ width: 100%; border-collapse: collapse; }}
        th, td {{
          border: 1px solid #cbd5df;
          padding: 12px;
          text-align: start;
        }}
        th {{ background: #eaf2f6; }}
        .totals {{ margin-top: 35px; }}
        .grand {{ font-size: 22px; font-weight: bold; }}
        [data-field] {{ unicode-bidi: isolate; }}
      </style>
    </head>
    <body>
      <h1>{version['titre']}</h1>
      <p class="notice">
        DOCUMENT FICTIF — TEST UNIQUEMENT / وثيقة تجريبية
      </p>
      <div class="identity">
        <p>{labels[0]} :
          <span data-field="fournisseur">{fields['fournisseur']}</span>
        </p>
        <p>{labels[1]} :
          <span dir="ltr" data-field="ice">{fields['ice']}</span>
        </p>
        <p>{labels[2]} :
          <span dir="ltr" data-field="numero_facture">
            {fields['numero_facture']}
          </span>
        </p>
        <p>{labels[3]} :
          <span dir="ltr" data-field="date">{fields['date']}</span>
        </p>
      </div>
      <table>
        <thead>
          <tr>
            <th>{labels[4]}</th><th>{labels[5]}</th>
            <th>{labels[6]}</th><th>{labels[7]}</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td data-field="lignes.0.designation">
              {version['designation']}
            </td>
            <td data-field="lignes.0.quantite">10</td>
            <td data-field="lignes.0.prix_unitaire_ht">50.00</td>
            <td data-field="lignes.0.montant_ht">500.00</td>
          </tr>
        </tbody>
      </table>
      <div class="totals">
        <p>{labels[8]} :
          <span dir="ltr" data-field="total_ht">500.00 MAD</span>
        </p>
        <p>{labels[9]} :
          <span dir="ltr" data-field="montant_tva">100.00 MAD</span>
        </p>
        <p class="grand">{labels[10]} :
          <span dir="ltr" data-field="total_ttc">600.00 MAD</span>
        </p>
      </div>
    </body>
    </html>
    """

    page = browser.new_page(
        viewport={"width": 794, "height": 1123},
        device_scale_factor=1,
    )

    try:
        page.set_content(html)
        page.evaluate("document.fonts.ready")

        # Coordonnées dans l'image PNG : [x_min, y_min, x_max, y_max].
        zones = page.locator("[data-field]").evaluate_all("""
            elements => elements.map(element => {
                const r = element.getBoundingClientRect();
                return {
                    champ: element.dataset.field,
                    page: 1,
                    bbox: [r.left, r.top, r.right, r.bottom]
                };
            })
        """)

        page.screenshot(path=str(RAW / f"{doc_id}.png"))
        page.pdf(
            path=str(RAW / f"{doc_id}.pdf"),
            format="A4",
            print_background=True,
        )

        annotation = {
            "document_id": doc_id,
            "langue": version["langue"],
            "synthetique": True,
            "template_id": "pilot_01",
            "image": f"{doc_id}.png",
            "image_dimensions": {"width": 794, "height": 1123},
            "bbox_format": "xyxy",
            "bbox_unite": "pixels_image",
            "champs": fields,
            "zones": zones,
        }

        destination = ANNOTATIONS / f"{doc_id}.json"
        destination.write_text(
            json.dumps(annotation, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"Créé : {doc_id} ({version['langue']})")
    finally:
        page.close()


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            for index, version in enumerate(VERSIONS, start=1):
                generate(browser, version, index)
        finally:
            browser.close()

    print("Terminé : 3 PDF, 3 PNG et 3 annotations JSON.")


if __name__ == "__main__":
    main()