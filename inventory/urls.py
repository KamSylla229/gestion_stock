from django.contrib.auth import views as auth_views
from django.urls import path
from django.views.generic import RedirectView

from inventory import views

app_name = "inventory"

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="inventory:produit_liste"), name="accueil"),

    # Authentification (vues natives Django)
    path(
        "connexion/",
        auth_views.LoginView.as_view(template_name="inventory/connexion.html"),
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
]
