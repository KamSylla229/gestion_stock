from django.contrib import admin

from inventory.models import Categorie, Fournisseur, Mouvement, Produit


@admin.register(Categorie)
class CategorieAdmin(admin.ModelAdmin):
    list_display = ("nom", "actif")
    list_filter = ("actif",)
    search_fields = ("nom",)


@admin.register(Fournisseur)
class FournisseurAdmin(admin.ModelAdmin):
    list_display = ("nom", "contact", "telephone", "email", "delai_jours", "actif")
    list_filter = ("actif",)
    search_fields = ("nom", "contact", "email")


@admin.register(Produit)
class ProduitAdmin(admin.ModelAdmin):
    list_display = (
        "reference",
        "nom",
        "unite",
        "categorie",
        "fournisseur",
        "prix_achat",
        "prix_vente",
        "quantite_stock",
        "seuil_alerte",
        "actif",
    )
    list_filter = ("categorie", "fournisseur", "actif")
    search_fields = ("reference", "nom")
    # quantite_stock est affiché mais non modifiable : il ne peut évoluer que
    # via services.enregistrer_mouvement(), sinon il se désynchroniserait de
    # l'historique des mouvements.
    readonly_fields = ("quantite_stock", "date_creation")


@admin.register(Mouvement)
class MouvementAdmin(admin.ModelAdmin):
    """
    Historique en consultation seule.

    Créer un mouvement ici contournerait services.enregistrer_mouvement() : le
    mouvement serait enregistré sans que le stock du produit bouge. L'ajout et
    la modification sont donc désactivés — les entrées et sorties se font dans
    l'application (menu « Entrée de stock » / « Sortie de stock »).
    """

    list_display = (
        "date_mouvement", "produit", "type_mouvement", "quantite",
        "stock_apres", "document", "utilisateur",
    )
    list_filter = ("type_mouvement", "date_mouvement", "utilisateur")
    search_fields = ("produit__nom", "produit__reference", "document", "destination")
    readonly_fields = (
        "produit", "type_mouvement", "quantite", "motif", "utilisateur",
        "stock_apres", "document", "destination", "date_mouvement",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
