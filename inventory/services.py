import logging

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone

from inventory.models import Mouvement, Produit

logger = logging.getLogger(__name__)


def franchit_seuil_alerte(stock_avant: int, stock_apres: int, seuil: int) -> bool:
    """
    Vrai uniquement si le stock vient de PASSER au niveau ou en dessous du seuil.

    Exemples :
      15 -> 8, seuil 10  : franchissement       -> True
       8 -> 6, seuil 10  : déjà sous le seuil   -> False
      20 -> 15, seuil 10 : toujours au-dessus   -> False

    C'est ce test qui évite d'envoyer une alerte à chaque sortie d'un produit
    déjà en alerte.
    """
    return stock_avant > seuil >= stock_apres


def niveau_alerte(stock_apres: int) -> str:
    """Statut affiché dans l'email : rupture totale ou stock critique."""
    return "RUPTURE" if stock_apres == 0 else "CRITIQUE"


def notifier_alerte_stock(
    produit: Produit,
    stock_avant: int,
    quantite_sortie: int,
    destinataire: str = None,
) -> bool:
    """
    Envoie au gérant un email d'alerte (HTML + texte) pour un produit dont le
    stock vient de franchir son seuil.

    Toutes les données nécessaires sont passées en paramètres : la fonction ne
    dépend d'aucune variable globale et peut être appelée/testée isolément.

    Renvoie True si l'email est parti, False en cas d'échec. Un échec d'envoi
    n'interrompt jamais l'opération métier appelante.
    """
    destinataire = destinataire or getattr(settings, "ALERTE_EMAIL_DESTINATAIRE", "")
    if not destinataire:
        logger.warning("Alerte stock non envoyée : ALERTE_EMAIL_DESTINATAIRE non configuré.")
        return False

    stock_apres = stock_avant - quantite_sortie
    contexte = {
        "produit": produit,
        "stock_avant": stock_avant,
        "quantite_sortie": quantite_sortie,
        "stock_apres": stock_apres,
        "seuil_alerte": produit.seuil_alerte,
        "niveau": niveau_alerte(stock_apres),
        "date_alerte": timezone.localtime(),
        "site_url": getattr(settings, "SITE_URL", ""),
        "nom_application": "StockFlow",
    }

    sujet = f"[StockFlow] Alerte stock — {produit.nom}"
    corps_texte = render_to_string("inventory/emails/alerte_stock.txt", contexte)
    corps_html = render_to_string("inventory/emails/alerte_stock.html", contexte)

    return _envoyer_email(sujet, corps_texte, corps_html, [destinataire])


def envoyer_rapport_quotidien(destinataire: str = None, jour=None) -> bool:
    """
    Envoie au gérant le rapport quotidien de l'état du stock.

    Utilisé par la commande `python manage.py rapport_quotidien`. S'appuie sur
    le module statistiques, donc sur les mêmes calculs que le tableau de bord.
    """
    # Import local : évite une dépendance circulaire au chargement du module.
    from inventory import statistiques

    destinataire = destinataire or getattr(settings, "ALERTE_EMAIL_DESTINATAIRE", "")
    if not destinataire:
        logger.warning("Rapport quotidien non envoyé : ALERTE_EMAIL_DESTINATAIRE non configuré.")
        return False

    stats_jour = statistiques.statistiques_du_jour(jour)
    contexte = {
        "kpis": statistiques.kpis_stock(),
        "stats_jour": stats_jour,
        "produits_en_alerte": statistiques.produits_en_alerte(),
        "derniers_mouvements": statistiques.derniers_mouvements(10),
        "site_url": getattr(settings, "SITE_URL", ""),
        "nom_application": "StockFlow",
    }

    sujet = f"[StockFlow] Rapport quotidien du {stats_jour['jour'].strftime('%d/%m/%Y')}"
    corps_texte = render_to_string("inventory/emails/rapport_quotidien.txt", contexte)
    corps_html = render_to_string("inventory/emails/rapport_quotidien.html", contexte)

    return _envoyer_email(sujet, corps_texte, corps_html, [destinataire])


def _envoyer_email(sujet: str, corps_texte: str, corps_html: str, destinataires: list) -> bool:
    """
    Envoie un email multipart (texte + HTML).

    Toute erreur d'envoi est journalisée mais jamais propagée : un SMTP
    indisponible ne doit pas faire échouer une vente ou une commande.
    """
    try:
        email = EmailMultiAlternatives(
            subject=sujet,
            body=corps_texte,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=destinataires,
        )
        email.attach_alternative(corps_html, "text/html")
        email.send()
        return True
    except Exception:
        logger.exception("Échec de l'envoi de l'email : %s", sujet)
        return False


@transaction.atomic
def enregistrer_mouvement(
    produit: Produit,
    type_mouvement: str,
    quantite: int,
    motif: str = "",
    utilisateur=None,
    document: str = "",
    destination: str = "",
) -> Mouvement:
    """
    Seule fonction autorisée à modifier Produit.quantite_stock.

    Crée un Mouvement et met à jour le stock du produit dans la même
    transaction : soit les deux opérations réussissent, soit aucune.

    Trois types de mouvement :
      ENTREE     : augmente le stock (réception, régularisation à la hausse)
      SORTIE     : diminue le stock (vente)
      AJUSTEMENT : diminue le stock hors vente (casse, perte, écart
                   d'inventaire). Le motif y est obligatoire.

    Lève ValidationError (exception métier) si la quantité est invalide,
    si le stock est insuffisant, si le motif manque sur un ajustement ou
    si le type de mouvement est inconnu.

    Si le mouvement fait franchir le seuil d'alerte du produit, une alerte
    email est programmée APRÈS validation de la transaction
    (transaction.on_commit) : aucune alerte pour un mouvement annulé.
    """
    if quantite is None or quantite <= 0:
        raise ValidationError("La quantité doit être strictement positive.")

    if type_mouvement not in dict(Mouvement.TYPE_CHOICES):
        raise ValidationError(f"Type de mouvement inconnu : {type_mouvement}")

    if type_mouvement == Mouvement.AJUSTEMENT and not motif.strip():
        raise ValidationError("Un ajustement doit être justifié par un motif.")

    stock_avant = produit.quantite_stock

    if type_mouvement == Mouvement.ENTREE:
        produit.quantite_stock += quantite
    else:
        # SORTIE et AJUSTEMENT diminuent tous deux le stock.
        if quantite > produit.quantite_stock:
            raise ValidationError(
                f"Stock insuffisant pour {produit.nom} : "
                f"{produit.quantite_stock} en stock, {quantite} demandés."
            )
        produit.quantite_stock -= quantite

    produit.save(update_fields=["quantite_stock"])

    mouvement = Mouvement.objects.create(
        produit=produit,
        type_mouvement=type_mouvement,
        quantite=quantite,
        motif=motif,
        utilisateur=utilisateur,
        stock_apres=produit.quantite_stock,
        document=document,
        destination=destination,
    )

    if type_mouvement in Mouvement.TYPES_SORTANTS and franchit_seuil_alerte(
        stock_avant, produit.quantite_stock, produit.seuil_alerte
    ):
        transaction.on_commit(
            lambda: notifier_alerte_stock(produit, stock_avant, quantite)
        )

    return mouvement
