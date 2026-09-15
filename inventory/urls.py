from django.contrib.auth import views as auth_views
from django.urls import path
from django.views.generic import RedirectView

from inventory import views

app_name = "inventory"

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="inventory:produit_liste"), name="accueil"),

    # Tableau de bord et export (réservés au gérant)
    path("tableau-bord/", views.TableauBordView.as_view(), name="tableau_bord"),
    path("export/stock.xlsx", views.ExportStockExcelView.as_view(), name="export_stock_excel"),
    path("export/mouvements.xlsx", views.ExportMouvementsExcelView.as_view(), name="export_mouvements_excel"),

    # Authentification (vues natives Django)
    path(
        "connexion/",
        auth_views.LoginView.as_view(
            template_name="inventory/connexion.html",
            # Un utilisateur déjà connecté n'a rien à faire sur la page de
            # connexion : on le renvoie directement vers l'application.
            redirect_authenticated_user=True,
        ),
        name="connexion",
    ),
    path("deconnexion/", auth_views.LogoutView.as_view(), name="deconnexion"),

    # Produits
    path("produits/", views.ProduitListView.as_view(), name="produit_liste"),
    path("produits/nouveau/", views.ProduitCreateView.as_view(), name="produit_creer"),
    path("produits/<int:pk>/", views.ProduitDetailView.as_view(), name="produit_detail"),
    path("produits/<int:pk>/modifier/", views.ProduitUpdateView.as_view(), name="produit_modifier"),

    # Mouvements
    path("mouvements/", views.MouvementListView.as_view(), name="mouvement_liste"),
    path("mouvements/entree/", views.EntreeStockView.as_view(), name="entree_stock"),
    path("mouvements/sortie/", views.SortieStockView.as_view(), name="sortie_stock"),
    path("mouvements/ajustement/", views.AjustementStockView.as_view(), name="ajustement_stock"),

    # Commandes fournisseurs
    path("commandes/", views.CommandeListView.as_view(), name="commande_liste"),
    path("commandes/nouvelle/", views.CommandeCreateView.as_view(), name="commande_creer"),
    path("commandes/<int:pk>/", views.CommandeDetailView.as_view(), name="commande_detail"),
    path("commandes/<int:pk>/lignes/ajouter/", views.AjouterLigneCommandeView.as_view(), name="commande_ajouter_ligne"),
    path("commandes/<int:pk>/envoyer/", views.EnvoyerCommandeView.as_view(), name="commande_envoyer"),
    path("commandes/<int:pk>/annuler/", views.AnnulerCommandeView.as_view(), name="commande_annuler"),
    path("lignes/<int:pk>/retirer/", views.RetirerLigneCommandeView.as_view(), name="ligne_retirer"),
    path("lignes/<int:pk>/receptionner/", views.ReceptionnerLigneView.as_view(), name="ligne_receptionner"),
]
