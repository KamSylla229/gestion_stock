from django.contrib import admin

from inventory.models import Categorie, Fournisseur, Mouvement, Produit


@admin.register(Categorie)
class CategorieAdmin(admin.ModelAdmin):
    list_display = ("nom", "actif")
    list_filter = ("actif",)
    search_fields = ("nom",)


@admin.register(Fournisseur)
class FournisseurAdmin(admin.ModelAdmin):
    list_display = ("nom", "telephone", "email", "actif")
    list_filter = ("actif",)
    search_fields = ("nom", "email")


@admin.register(Produit)
class ProduitAdmin(admin.ModelAdmin):
    list_display = (
        "reference",
        "nom",
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


@admin.register(Mouvement)
class MouvementAdmin(admin.ModelAdmin):
    list_display = ("produit", "type_mouvement", "quantite", "date_mouvement")
    list_filter = ("type_mouvement", "date_mouvement")
    search_fields = ("produit__nom", "produit__reference")

    def get_readonly_fields(self, request, obj=None):
        # obj is None => formulaire de création : tout est éditable.
        # obj existant => formulaire d'édition : tout devient readonly,
        # pour qu'un mouvement déjà créé ne puisse plus être trafiqué
        # (sinon quantite_stock se désynchroniserait de l'historique).
        if obj is None:
            return ()
        return ("produit", "type_mouvement", "quantite", "motif", "date_mouvement")
