from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.messages.views import SuccessMessageMixin
from django.core.exceptions import ValidationError
from django.db.models import F, Q
from django.shortcuts import redirect
from django.utils.dateparse import parse_date
from django.views.generic import CreateView, DetailView, FormView, ListView, UpdateView

from inventory import services
from inventory.forms import MouvementForm, ProduitForm
from inventory.models import Categorie, Fournisseur, Mouvement, Produit


def querystring_sans_page(request):
    """
    Renvoie les paramètres GET courants sans 'page', pour que les liens de
    pagination conservent les filtres appliqués.
    """
    parametres = request.GET.copy()
    parametres.pop("page", None)
    return parametres.urlencode()


class ProduitListView(LoginRequiredMixin, ListView):
    model = Produit
    paginate_by = 20
    context_object_name = "produits"

    def get_queryset(self):
        queryset = Produit.objects.select_related("categorie", "fournisseur")

        recherche = self.request.GET.get("q", "").strip()
        if recherche:
            # Recherche simple sur le nom OU la référence.
            queryset = queryset.filter(
                Q(nom__icontains=recherche) | Q(reference__icontains=recherche)
            )

        categorie = self.request.GET.get("categorie", "")
        if categorie.isdigit():
            queryset = queryset.filter(categorie_id=categorie)

        fournisseur = self.request.GET.get("fournisseur", "")
        if fournisseur.isdigit():
            queryset = queryset.filter(fournisseur_id=fournisseur)

        actif = self.request.GET.get("actif", "")
        if actif in ("1", "0"):
            queryset = queryset.filter(actif=(actif == "1"))

        if self.request.GET.get("stock_bas"):
            # F() compare deux colonnes de la même ligne, côté base de données.
            queryset = queryset.filter(quantite_stock__lte=F("seuil_alerte"))

        return queryset

    def get_context_data(self, **kwargs):
        contexte = super().get_context_data(**kwargs)
        contexte["categories"] = Categorie.objects.all()
        contexte["fournisseurs"] = Fournisseur.objects.all()
        contexte["filtres"] = self.request.GET
        contexte["querystring"] = querystring_sans_page(self.request)
        return contexte


class ProduitDetailView(LoginRequiredMixin, DetailView):
    model = Produit
    context_object_name = "produit"

    def get_context_data(self, **kwargs):
        contexte = super().get_context_data(**kwargs)
        # Mouvement.Meta.ordering = ["-date_mouvement"] : les plus récents d'abord.
        contexte["mouvements"] = self.object.mouvements.all()[:20]
        return contexte


class ProduitCreateView(LoginRequiredMixin, SuccessMessageMixin, CreateView):
    model = Produit
    form_class = ProduitForm
    success_message = "Produit « %(nom)s » créé avec succès."
    extra_context = {"titre": "Nouveau produit"}


class ProduitUpdateView(LoginRequiredMixin, SuccessMessageMixin, UpdateView):
    model = Produit
    form_class = ProduitForm
    success_message = "Produit « %(nom)s » modifié avec succès."
    extra_context = {"titre": "Modifier le produit"}


class MouvementCreateView(LoginRequiredMixin, FormView):
    """
    Vue de base pour l'entrée et la sortie de stock.

    Toute la logique métier reste dans services.enregistrer_mouvement() :
    la vue se contente de valider le formulaire, d'appeler le service et
    de traduire une éventuelle erreur métier en message utilisateur.
    """

    template_name = "inventory/mouvement_form.html"
    form_class = MouvementForm
    type_mouvement = None
    titre = ""

    def get_initial(self):
        initial = super().get_initial()
        # Permet d'arriver depuis la fiche produit avec le produit pré-sélectionné.
        produit = self.request.GET.get("produit", "")
        if produit.isdigit():
            initial["produit"] = produit
        return initial

    def get_context_data(self, **kwargs):
        contexte = super().get_context_data(**kwargs)
        contexte["titre"] = self.titre
        contexte["type_mouvement"] = self.type_mouvement
        return contexte

    def form_valid(self, form):
        produit = form.cleaned_data["produit"]
        try:
            services.enregistrer_mouvement(
                produit=produit,
                type_mouvement=self.type_mouvement,
                quantite=form.cleaned_data["quantite"],
                motif=form.cleaned_data["motif"],
            )
        except ValidationError as erreur:
            # Erreur métier (stock insuffisant, quantité invalide) : on
            # réaffiche le formulaire avec les données saisies et le message.
            form.add_error(None, erreur)
            return self.form_invalid(form)

        messages.success(
            self.request,
            f"Mouvement enregistré. Stock de {produit.nom} : {produit.quantite_stock}.",
        )
        return redirect("inventory:produit_detail", pk=produit.pk)


class EntreeStockView(MouvementCreateView):
    type_mouvement = Mouvement.ENTREE
    titre = "Entrée de stock"


class SortieStockView(MouvementCreateView):
    type_mouvement = Mouvement.SORTIE
    titre = "Sortie de stock"


class MouvementListView(LoginRequiredMixin, ListView):
    """Historique des mouvements, filtrable par produit, type et période."""

    model = Mouvement
    paginate_by = 20
    context_object_name = "mouvements"

    def get_queryset(self):
        queryset = Mouvement.objects.select_related("produit")

        produit = self.request.GET.get("produit", "")
        if produit.isdigit():
            queryset = queryset.filter(produit_id=produit)

        type_mouvement = self.request.GET.get("type", "")
        if type_mouvement in (Mouvement.ENTREE, Mouvement.SORTIE):
            queryset = queryset.filter(type_mouvement=type_mouvement)

        # parse_date renvoie None si la chaîne n'est pas une date valide :
        # le filtre est alors simplement ignoré, sans erreur 500.
        date_debut = parse_date(self.request.GET.get("date_debut", "") or "")
        if date_debut:
            queryset = queryset.filter(date_mouvement__date__gte=date_debut)

        date_fin = parse_date(self.request.GET.get("date_fin", "") or "")
        if date_fin:
            queryset = queryset.filter(date_mouvement__date__lte=date_fin)

        return queryset

    def get_context_data(self, **kwargs):
        contexte = super().get_context_data(**kwargs)
        contexte["produits"] = Produit.objects.order_by("nom")
        contexte["types_mouvement"] = Mouvement.TYPE_CHOICES
        contexte["filtres"] = self.request.GET
        contexte["querystring"] = querystring_sans_page(self.request)
        return contexte
