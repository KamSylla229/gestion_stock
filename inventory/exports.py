"""
Génération dynamique du fichier Excel d'état du stock (openpyxl).

Aucun fichier n'est stocké sur le disque : le classeur est construit en mémoire
et renvoyé directement au navigateur.
"""

from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

COLONNES = [
    ("Référence", 16),
    ("Produit", 34),
    ("Catégorie", 20),
    ("Fournisseur", 24),
    ("Stock", 10),
    ("Seuil d'alerte", 14),
    ("Statut", 16),
    ("Prix d'achat", 14),
    ("Valeur du stock", 18),
]

STYLE_ENTETE_FOND = PatternFill("solid", fgColor="212529")
STYLE_ENTETE_POLICE = Font(bold=True, color="FFFFFF", size=11)

# Couleurs de la colonne Statut
REMPLISSAGE_STATUT = {
    "Rupture": PatternFill("solid", fgColor="F8D7DA"),
    "Sous seuil": PatternFill("solid", fgColor="FFF3CD"),
    "Stock normal": PatternFill("solid", fgColor="D1E7DD"),
}
POLICE_STATUT = {
    "Rupture": Font(bold=True, color="842029"),
    "Sous seuil": Font(bold=True, color="664D03"),
    "Stock normal": Font(color="0F5132"),
}

BORDURE_FINE = Border(*[Side(style="thin", color="DEE2E6")] * 4)

FORMAT_MONETAIRE = "#,##0.00"
FORMAT_ENTIER = "#,##0"


def statut_produit(produit) -> str:
    """Statut lisible utilisé dans le fichier Excel."""
    if produit.quantite_stock == 0:
        return "Rupture"
    if produit.quantite_stock <= produit.seuil_alerte:
        return "Sous seuil"
    return "Stock normal"


def generer_classeur_stock(produits) -> Workbook:
    """
    Construit le classeur Excel à partir d'un queryset de produits.

    Le queryset doit avoir préchargé categorie et fournisseur (select_related)
    pour éviter une requête par ligne.
    """
    classeur = Workbook()
    feuille = classeur.active
    feuille.title = "État du stock"

    genere_le = timezone.localtime().strftime("%d/%m/%Y à %H:%M")
    feuille["A1"] = "StockFlow — État du stock"
    feuille["A1"].font = Font(bold=True, size=14)
    feuille["A2"] = f"Document généré le {genere_le}"
    feuille["A2"].font = Font(italic=True, size=10, color="6C757D")

    ligne_entete = 4
    for index, (titre, largeur) in enumerate(COLONNES, start=1):
        cellule = feuille.cell(row=ligne_entete, column=index, value=titre)
        cellule.fill = STYLE_ENTETE_FOND
        cellule.font = STYLE_ENTETE_POLICE
        cellule.alignment = Alignment(horizontal="center", vertical="center")
        cellule.border = BORDURE_FINE
        feuille.column_dimensions[get_column_letter(index)].width = largeur
    feuille.row_dimensions[ligne_entete].height = 22

    ligne = ligne_entete + 1
    for produit in produits:
        statut = statut_produit(produit)
        valeur_stock = produit.quantite_stock * produit.prix_achat

        valeurs = [
            produit.reference,
            produit.nom,
            produit.categorie.nom if produit.categorie else "",
            produit.fournisseur.nom if produit.fournisseur else "",
            produit.quantite_stock,
            produit.seuil_alerte,
            statut,
            produit.prix_achat,
            valeur_stock,
        ]
        for index, valeur in enumerate(valeurs, start=1):
            cellule = feuille.cell(row=ligne, column=index, value=valeur)
            cellule.border = BORDURE_FINE

        # Formats numériques
        feuille.cell(row=ligne, column=5).number_format = FORMAT_ENTIER
        feuille.cell(row=ligne, column=6).number_format = FORMAT_ENTIER
        feuille.cell(row=ligne, column=8).number_format = FORMAT_MONETAIRE
        feuille.cell(row=ligne, column=9).number_format = FORMAT_MONETAIRE

        # Statut coloré selon la situation
        cellule_statut = feuille.cell(row=ligne, column=7)
        cellule_statut.fill = REMPLISSAGE_STATUT[statut]
        cellule_statut.font = POLICE_STATUT[statut]
        cellule_statut.alignment = Alignment(horizontal="center")

        ligne += 1

    derniere_ligne = ligne - 1
    if derniere_ligne >= ligne_entete:
        # Filtre automatique sur l'en-tête + colonnes figées sous l'en-tête.
        feuille.auto_filter.ref = (
            f"A{ligne_entete}:{get_column_letter(len(COLONNES))}{max(derniere_ligne, ligne_entete)}"
        )
    feuille.freeze_panes = feuille.cell(row=ligne_entete + 1, column=1)

    return classeur


def nom_fichier_export(prefixe: str = "stock") -> str:
    """Nom de fichier horodaté, ex : stockflow_stock_2026-09-15_1530.xlsx"""
    return f"stockflow_{prefixe}_{timezone.localtime().strftime('%Y-%m-%d_%H%M')}.xlsx"


COLONNES_MOUVEMENTS = [
    ("Date", 18),
    ("Référence", 16),
    ("Produit", 32),
    ("Type", 14),
    ("Quantité", 11),
    ("Stock après", 13),
    ("Motif", 22),
    ("Destination", 26),
    ("Document", 16),
    ("Valeur", 14),
    ("Utilisateur", 18),
]

REMPLISSAGE_TYPE = {
    "Entrée": PatternFill("solid", fgColor="D1E7DD"),
    "Sortie": PatternFill("solid", fgColor="F8D7DA"),
    "Ajustement": PatternFill("solid", fgColor="FFF3CD"),
}
POLICE_TYPE = {
    "Entrée": Font(bold=True, color="0F5132"),
    "Sortie": Font(bold=True, color="842029"),
    "Ajustement": Font(bold=True, color="664D03"),
}


def generer_classeur_mouvements(mouvements) -> Workbook:
    """
    Construit le classeur Excel de l'historique des mouvements.

    Le queryset doit avoir préchargé produit et utilisateur (select_related).
    """
    classeur = Workbook()
    feuille = classeur.active
    feuille.title = "Historique"

    genere_le = timezone.localtime().strftime("%d/%m/%Y à %H:%M")
    feuille["A1"] = "StockFlow — Historique des mouvements"
    feuille["A1"].font = Font(bold=True, size=14)
    feuille["A2"] = f"Document généré le {genere_le}"
    feuille["A2"].font = Font(italic=True, size=10, color="6C757D")

    ligne_entete = 4
    for index, (titre, largeur) in enumerate(COLONNES_MOUVEMENTS, start=1):
        cellule = feuille.cell(row=ligne_entete, column=index, value=titre)
        cellule.fill = STYLE_ENTETE_FOND
        cellule.font = STYLE_ENTETE_POLICE
        cellule.alignment = Alignment(horizontal="center", vertical="center")
        cellule.border = BORDURE_FINE
        feuille.column_dimensions[get_column_letter(index)].width = largeur
    feuille.row_dimensions[ligne_entete].height = 22

    ligne = ligne_entete + 1
    for mouvement in mouvements:
        libelle_type = mouvement.get_type_mouvement_display()
        valeurs = [
            timezone.localtime(mouvement.date_mouvement).strftime("%d/%m/%Y %H:%M"),
            mouvement.produit.reference,
            mouvement.produit.nom,
            libelle_type,
            mouvement.quantite,
            mouvement.stock_apres,
            mouvement.motif,
            mouvement.destination,
            mouvement.document,
            mouvement.quantite * mouvement.produit.prix_achat,
            mouvement.utilisateur.username if mouvement.utilisateur else "",
        ]
        for index, valeur in enumerate(valeurs, start=1):
            cellule = feuille.cell(row=ligne, column=index, value=valeur)
            cellule.border = BORDURE_FINE

        feuille.cell(row=ligne, column=5).number_format = FORMAT_ENTIER
        feuille.cell(row=ligne, column=6).number_format = FORMAT_ENTIER
        feuille.cell(row=ligne, column=10).number_format = FORMAT_MONETAIRE

        cellule_type = feuille.cell(row=ligne, column=4)
        cellule_type.fill = REMPLISSAGE_TYPE[libelle_type]
        cellule_type.font = POLICE_TYPE[libelle_type]
        cellule_type.alignment = Alignment(horizontal="center")

        ligne += 1

    derniere_ligne = max(ligne - 1, ligne_entete)
    feuille.auto_filter.ref = (
        f"A{ligne_entete}:{get_column_letter(len(COLONNES_MOUVEMENTS))}{derniere_ligne}"
    )
    feuille.freeze_panes = feuille.cell(row=ligne_entete + 1, column=1)

    return classeur
