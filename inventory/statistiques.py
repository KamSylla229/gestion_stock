"""
Calculs statistiques sur le stock.

Ce module est partagé par le tableau de bord et la commande rapport_quotidien :
les chiffres affichés à l'écran et ceux envoyés par email viennent donc
exactement du même code.

Tous les calculs sont faits par la base de données (aggregate / annotate) :
aucun chargement inutile d'objets en mémoire.
"""

from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, DecimalField, F, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from inventory.models import Categorie, Mouvement, Produit

CHAMP_MONETAIRE = DecimalField(max_digits=14, decimal_places=2)


def kpis_stock(jours_activite: int = 7) -> dict:
    """
    Les 4 indicateurs clés du gérant :

    - produits_actifs   : taille du catalogue réellement exploité
    - valeur_stock      : argent immobilisé (quantité × prix d'achat)
    - produits_en_alerte: produits à réapprovisionner (stock <= seuil)
    - mouvements_recents: activité des derniers jours
    """
    agregats = Produit.objects.filter(actif=True).aggregate(
        produits_actifs=Count("id"),
        quantite_totale=Coalesce(Sum("quantite_stock"), 0),
        valeur_stock=Coalesce(
            Sum(F("quantite_stock") * F("prix_achat"), output_field=CHAMP_MONETAIRE),
            Decimal("0.00"),
            output_field=CHAMP_MONETAIRE,
        ),
        # Un seul passage en base compte aussi les produits à réapprovisionner.
        produits_en_alerte=Count("id", filter=Q(quantite_stock__lte=F("seuil_alerte"))),
        produits_en_rupture=Count("id", filter=Q(quantite_stock=0)),
    )

    depuis = timezone.now() - timedelta(days=jours_activite)
    agregats["mouvements_recents"] = Mouvement.objects.filter(date_mouvement__gte=depuis).count()
    agregats["jours_activite"] = jours_activite
    return agregats


def valeur_par_categorie():
    """
    Valeur du stock ventilée par catégorie, de la plus forte à la plus faible.

    annotate() calcule la somme (quantité × prix d'achat) par catégorie en une
    seule requête. `pourcentage` sert uniquement à dimensionner les barres à
    l'écran : il est relatif à la catégorie la plus valorisée.
    """
    categories = list(
        Categorie.objects.annotate(
            valeur=Coalesce(
                Sum(
                    F("produits__quantite_stock") * F("produits__prix_achat"),
                    filter=Q(produits__actif=True),
                    output_field=CHAMP_MONETAIRE,
                ),
                Decimal("0.00"),
                output_field=CHAMP_MONETAIRE,
            )
        )
        .filter(valeur__gt=0)
        .order_by("-valeur")
    )

    valeur_maximale = categories[0].valeur if categories else Decimal("0.00")
    for categorie in categories:
        categorie.pourcentage = (
            round(categorie.valeur / valeur_maximale * 100) if valeur_maximale else 0
        )
    return categories


# Périodes proposées par le sélecteur du tableau de bord.
PERIODES = [
    ("jour", "Jour", 1),
    ("semaine", "7 jours", 7),
    ("mois", "Mois", 30),
]
PERIODE_PAR_DEFAUT = "semaine"


def jours_de_la_periode(cle: str) -> int:
    """Nombre de jours correspondant à une clé de période, 7 par défaut."""
    for code, _, jours in PERIODES:
        if code == cle:
            return jours
    return 7


def activite_periode(jours: int = 7) -> dict:
    """
    Activité sur les N derniers jours : nombre de mouvements par type et
    valeur des sorties au prix d'achat.
    """
    depuis = timezone.now() - timedelta(days=jours)
    mouvements = Mouvement.objects.filter(date_mouvement__gte=depuis)

    compteurs = {type_mouvement: 0 for type_mouvement, _ in Mouvement.TYPE_CHOICES}
    for ligne in mouvements.values("type_mouvement").annotate(nombre=Count("id")):
        compteurs[ligne["type_mouvement"]] = ligne["nombre"]

    valeur_sorties = mouvements.filter(
        type_mouvement__in=Mouvement.TYPES_SORTANTS
    ).aggregate(
        total=Coalesce(
            Sum(
                F("quantite") * F("produit__prix_achat"),
                output_field=CHAMP_MONETAIRE,
            ),
            Decimal("0.00"),
            output_field=CHAMP_MONETAIRE,
        )
    )["total"]

    return {
        "jours": jours,
        "entrees": compteurs[Mouvement.ENTREE],
        "sorties": compteurs[Mouvement.SORTIE],
        "ajustements": compteurs[Mouvement.AJUSTEMENT],
        "total": sum(compteurs.values()),
        "valeur_sorties": valeur_sorties,
    }


def produits_les_plus_mouvementes(jours: int = 7, limite: int = 5):
    """
    Produits ayant le plus quitté le stock sur la période.

    annotate() additionne les quantités sorties par produit côté base ;
    filter() dans l'agrégat ne compte que les mouvements de la période.
    """
    depuis = timezone.now() - timedelta(days=jours)
    return (
        Produit.objects.annotate(
            total_sorties=Coalesce(
                Sum(
                    "mouvements__quantite",
                    filter=Q(
                        mouvements__date_mouvement__gte=depuis,
                        mouvements__type_mouvement__in=Mouvement.TYPES_SORTANTS,
                    ),
                ),
                0,
            )
        )
        .filter(total_sorties__gt=0)
        .order_by("-total_sorties", "nom")[:limite]
    )


def statistiques_produit(produit, jours: int = 30) -> dict:
    """
    Indicateurs d'une fiche produit sur les N derniers jours.

    - sorties_total / sorties_moyenne : rythme de consommation
    - couverture : jours de stock restants à ce rythme
    - derniere_entree : dernier réapprovisionnement
    - franchissements_seuil : nombre de fois où le stock est passé sous le
      seuil, utile pour conseiller un seuil mieux calibré
    """
    depuis = timezone.now() - timedelta(days=jours)

    sortant = produit.mouvements.filter(
        type_mouvement__in=Mouvement.TYPES_SORTANTS, date_mouvement__gte=depuis
    )
    total_sorties = sortant.aggregate(total=Coalesce(Sum("quantite"), 0))["total"]
    moyenne = total_sorties / jours if total_sorties else 0

    couverture = int(produit.quantite_stock / moyenne) if moyenne else None

    derniere_entree = (
        produit.mouvements.filter(type_mouvement=Mouvement.ENTREE)
        .order_by("-date_mouvement", "-id")
        .first()
    )

    return {
        "jours": jours,
        "sorties_total": total_sorties,
        "sorties_moyenne": round(moyenne, 1),
        "couverture": couverture,
        "derniere_entree": derniere_entree,
        "franchissements_seuil": compter_franchissements_seuil(produit, depuis),
    }


def compter_franchissements_seuil(produit, depuis) -> int:
    """
    Compte les passages du stock AU-DESSUS du seuil vers le seuil ou en dessous.

    On relit les mouvements de la période dans l'ordre chronologique et on
    compte les transitions : deux mouvements consécutifs sous le seuil ne
    comptent que pour un seul franchissement.

    Les mouvements antérieurs au champ stock_apres sont ignorés (valeur nulle).
    Le volume reste faible : un seul produit, sur une période courte.
    """
    seuil = produit.seuil_alerte
    etats = (
        produit.mouvements.filter(date_mouvement__gte=depuis, stock_apres__isnull=False)
        .order_by("date_mouvement", "id")
        .values_list("stock_apres", flat=True)
    )

    franchissements = 0
    au_dessus = True
    for stock_apres in etats:
        sous_le_seuil = stock_apres <= seuil
        if sous_le_seuil and au_dessus:
            franchissements += 1
        au_dessus = not sous_le_seuil
    return franchissements


def produits_en_alerte():
    """Produits actifs dont le stock est au niveau ou en dessous du seuil."""
    return (
        Produit.objects.filter(actif=True, quantite_stock__lte=F("seuil_alerte"))
        .select_related("categorie")
        .order_by("quantite_stock", "nom")
    )


def derniers_mouvements(limite: int = 10):
    """Les N mouvements les plus récents, produit préchargé."""
    return (
        Mouvement.objects.select_related("produit", "utilisateur")
        .order_by("-date_mouvement", "-id")[:limite]
    )


def statistiques_du_jour(jour=None) -> dict:
    """
    Chiffres de la journée pour le rapport quotidien.

    annotate() regroupe les mouvements par type en une seule requête, au lieu
    d'en faire une par type.
    """
    jour = jour or timezone.localdate()

    par_type = (
        Mouvement.objects.filter(date_mouvement__date=jour)
        .values("type_mouvement")
        .annotate(nombre=Count("id"), quantite_totale=Coalesce(Sum("quantite"), 0))
    )

    # Un compteur par type existant : ajouter un type au modèle suffit,
    # aucun risque d'oublier de l'additionner ici.
    resume = {
        type_mouvement: {"nombre": 0, "quantite_totale": 0}
        for type_mouvement, _ in Mouvement.TYPE_CHOICES
    }
    for ligne in par_type:
        resume[ligne["type_mouvement"]] = {
            "nombre": ligne["nombre"],
            "quantite_totale": ligne["quantite_totale"],
        }

    entrees = resume[Mouvement.ENTREE]
    sorties = resume[Mouvement.SORTIE]
    ajustements = resume[Mouvement.AJUSTEMENT]

    return {
        "jour": jour,
        "entrees_nombre": entrees["nombre"],
        "entrees_quantite": entrees["quantite_totale"],
        "sorties_nombre": sorties["nombre"],
        "sorties_quantite": sorties["quantite_totale"],
        "ajustements_nombre": ajustements["nombre"],
        "ajustements_quantite": ajustements["quantite_totale"],
        "mouvements_nombre": sum(compteur["nombre"] for compteur in resume.values()),
    }
