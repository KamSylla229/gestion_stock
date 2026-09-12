from django.core.exceptions import ValidationError
from django.db import transaction

from inventory.models import Mouvement, Produit


@transaction.atomic
def enregistrer_mouvement(produit: Produit, type_mouvement: str, quantite: int, motif: str = "") -> Mouvement:
    """
    Seule fonction autorisée à modifier Produit.quantite_stock.

    Crée un Mouvement et met à jour le stock du produit dans la même
    transaction : soit les deux opérations réussissent, soit aucune.

    Lève ValidationError (exception métier) si la quantité est invalide,
    si le stock est insuffisant ou si le type de mouvement est inconnu.
    """
    if quantite is None or quantite <= 0:
        raise ValidationError("La quantité doit être strictement positive.")

    if type_mouvement == Mouvement.ENTREE:
        produit.quantite_stock += quantite
    elif type_mouvement == Mouvement.SORTIE:
        if quantite > produit.quantite_stock:
            raise ValidationError(
                f"Stock insuffisant pour {produit.nom} : "
                f"{produit.quantite_stock} en stock, {quantite} demandés."
            )
        produit.quantite_stock -= quantite
    else:
        raise ValidationError(f"Type de mouvement inconnu : {type_mouvement}")

    produit.save(update_fields=["quantite_stock"])

    return Mouvement.objects.create(
        produit=produit,
        type_mouvement=type_mouvement,
        quantite=quantite,
        motif=motif,
    )
