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

from inventory.models import Mouvement, Produit

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


def produits_en_alerte():
    """Produits actifs dont le stock est au niveau ou en dessous du seuil."""
    return (
        Produit.objects.filter(actif=True, quantite_stock__lte=F("seuil_alerte"))
        .select_related("categorie")
        .order_by("quantite_stock", "nom")
    )


def derniers_mouvements(limite: int = 10):
    """Les N mouvements les plus récents, produit préchargé."""
    return Mouvement.objects.select_related("produit").order_by("-date_mouvement", "-id")[:limite]


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

    resume = {
        Mouvement.ENTREE: {"nombre": 0, "quantite_totale": 0},
        Mouvement.SORTIE: {"nombre": 0, "quantite_totale": 0},
    }
    for ligne in par_type:
        resume[ligne["type_mouvement"]] = {
            "nombre": ligne["nombre"],
            "quantite_totale": ligne["quantite_totale"],
        }

    entrees = resume[Mouvement.ENTREE]
    sorties = resume[Mouvement.SORTIE]

    return {
        "jour": jour,
        "entrees_nombre": entrees["nombre"],
        "entrees_quantite": entrees["quantite_totale"],
        "sorties_nombre": sorties["nombre"],
        "sorties_quantite": sorties["quantite_totale"],
        "mouvements_nombre": entrees["nombre"] + sorties["nombre"],
    }
