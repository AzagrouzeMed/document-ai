"""Controles purs : une coherence arithmetique ne certifie pas une extraction."""
import math
from decimal import Decimal, localcontext

TOLERANCE = Decimal('0.03')
BASES = (('HT', 'prix_unitaire_ht', 'montant_ht'),
         ('TTC', 'prix_unitaire_ttc', 'montant_ttc'),
         ('GENERIQUE', 'prix_unitaire', 'montant'))


def _nombre(value):
    return type(value) in (int, float) and (type(value) is int or math.isfinite(value))


def _controle(code, champ, valeurs, operation):
    resultat = dict(code=code, champ=champ, etat='non_verifiable',
                    valeurs={k: v if _nombre(v) else None for k, v in valeurs.items()},
                    valeur_attendue=None, ecart=None, tolerance=float(TOLERANCE))
    if not all(_nombre(v) for v in valeurs.values()):
        return resultat
    nombres = [Decimal(str(v)) for v in valeurs.values()]
    with localcontext() as contexte:
        contexte.prec = max(50, sum(len(n.as_tuple().digits) + abs(n.as_tuple().exponent) for n in nombres) + 10)
        attendu = operation(nombres[0], nombres[1])
        ecart = abs(nombres[2] - attendu)
        try:
            sorties = float(attendu), float(ecart)
        except OverflowError:
            return resultat
        if not all(math.isfinite(v) for v in sorties):
            return resultat
        resultat.update(valeur_attendue=sorties[0], ecart=sorties[1],
                        etat='coherent' if ecart <= TOLERANCE else 'a_verifier')
    return resultat


def valider_calculs(document):
    """Trois controles par ligne et un controle des totaux ; aucune mutation.

    Seuls les nombres JSON finis sont exploitables, y compris zero et les negatifs.
    Les textes et booleens ne sont pas convertis. Ecart absolu, sans arrondi
    intermediaire. Les entrees non exploitables sont representees par null.
    Aucun rapprochement de la somme des articles avec le total du document.
    """
    resultats = []
    lignes = document.get('lignes', [])
    if not isinstance(lignes, list):
        lignes = []
    for index, ligne in enumerate(lignes):
        ligne = ligne if isinstance(ligne, dict) else {}
        for base, prix, montant in BASES:
            resultats.append(_controle(
                'LIGNE_QUANTITE_PRIX_' + base, f'lignes.{index}.{montant}',
                {k: ligne.get(k) for k in ('quantite', prix, montant)},
                lambda q, p: q * p))
    resultats.append(_controle(
        'TOTAL_HT_TVA_TTC', 'total_ttc',
        {k: document.get(k) for k in ('total_ht', 'montant_tva', 'total_ttc')},
        lambda ht, tva: ht + tva))
    return resultats
