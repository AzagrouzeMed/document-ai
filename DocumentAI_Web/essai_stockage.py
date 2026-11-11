"""Essai fictif, execute uniquement sur demande explicite."""
if __package__:
    from .stockage import initialiser_base, enregistrer_facture, lire_facture, lire_articles
else:
    from stockage import initialiser_base, enregistrer_facture, lire_facture, lire_articles



document = {
    "numero_document": "TEST-BDD-002",
    "fournisseur": "Fournisseur fictif",
    "client": "Client fictif",
    "date": "2026-11-06",
    "devise": "MAD",
    "lignes": [
        {
            "designation": "Clavier",
            "quantite": 2,
            "prix_unitaire_ht": 100,
            "montant_ht": 200,
        },
        {
            "designation": "Souris",
            "quantite": 3,
            "prix_unitaire_ht": 50,
            "montant_ht": 150,
        },
    ],
}

def main():
    initialiser_base()
    facture_id = enregistrer_facture(document)

    print("Facture :", lire_facture(facture_id)["numero_document"])

    for article in lire_articles(facture_id):
        print(
            article["designation"],
            article["quantite"],
            article["prix_unitaire_ht"],
            article["montant_ht"],
        )


if __name__ == "__main__":
    main()
