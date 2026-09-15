from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.contrib.auth.models import User
from django.contrib.messages.views import SuccessMessageMixin
from django.core.exceptions import ValidationError
from django.db.models import F, Q
from django.http import HttpResponse
from django.shortcuts import redirect
from django.utils.dateparse import parse_date
from django.views.generic import CreateView, DetailView, FormView, ListView, TemplateView, UpdateView

from inventory import exports, services, statistiques
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


def filtrer_produits(request):
    """
    Applique recherche et filtres aux produits, d'après les paramètres GET.

    Partagé par la liste des produits et l'export Excel : l'export porte donc
    exactement sur ce que l'utilisateur a à l'écran.
    """
    queryset = Produit.objects.select_related("categorie", "fournisseur")

    recherche = request.GET.get("q", "").strip()
    if recherche:
        # Recherche simple sur le nom OU la référence.
        queryset = queryset.filter(
            Q(nom__icontains=recherche) | Q(reference__icontains=recherche)
        )

    categorie = request.GET.get("categorie", "")
    if categorie.isdigit():
        queryset = queryset.filter(categorie_id=categorie)

    fournisseur = request.GET.get("fournisseur", "")
    if fournisseur.isdigit():
        queryset = queryset.filter(fournisseur_id=fournisseur)

    actif = request.GET.get("actif", "")
    if actif in ("1", "0"):
        queryset = queryset.filter(actif=(actif == "1"))

    if request.GET.get("stock_bas"):
        # F() compare deux colonnes de la même ligne, côté base de données.
        queryset = queryset.filter(quantite_stock__lte=F("seuil_alerte"))

    return queryset


class TableauBordView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    """
    Tableau de bord du gérant : 4 KPI, produits à réapprovisionner et
    10 derniers mouvements. Réservé au groupe Gerant.
    """

    template_name = "inventory/tableau_bord.html"
    permission_required = "inventory.acceder_tableau_bord"

    def get_context_data(self, **kwargs):
        contexte = super().get_context_data(**kwargs)
        contexte["kpis"] = statistiques.kpis_stock()
        contexte["valeur_par_categorie"] = statistiques.valeur_par_categorie()
        contexte["produits_en_alerte"] = statistiques.produits_en_alerte()
        contexte["derniers_mouvements"] = statistiques.derniers_mouvements(10)
        contexte["section"] = "tableau_bord"
        return contexte


class ExportStockExcelView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    """
    Télécharge l'état du stock au format Excel, en respectant les filtres
    éventuellement appliqués à la liste des produits. Réservé au groupe Gerant.
    """

    permission_required = "inventory.exporter_stock"

    def get(self, request, *args, **kwargs):
        produits = filtrer_produits(request).order_by("nom")
        classeur = exports.generer_classeur_stock(produits)

        reponse = HttpResponse(
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        reponse["Content-Disposition"] = f'attachment; filename="{exports.nom_fichier_export()}"'
        classeur.save(reponse)  # écrit le classeur directement dans la réponse HTTP
        return reponse


class ProduitListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    model = Produit
    paginate_by = 20
    context_object_name = "produits"
    permission_required = "inventory.view_produit"
    extra_context = {"section": "produits"}

    def get_queryset(self):
        return filtrer_produits(self.request)

    def get_context_data(self, **kwargs):
        contexte = super().get_context_data(**kwargs)
        contexte["categories"] = Categorie.objects.all()
        contexte["fournisseurs"] = Fournisseur.objects.all()
        contexte["filtres"] = self.request.GET
        contexte["querystring"] = querystring_sans_page(self.request)
        return contexte


class ProduitDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    model = Produit
    context_object_name = "produit"
    permission_required = "inventory.view_produit"
    extra_context = {"section": "produits"}

    def get_context_data(self, **kwargs):
        contexte = super().get_context_data(**kwargs)
        # Mouvement.Meta.ordering = ["-date_mouvement", "-id"] : les plus récents d'abord.
        mouvements = list(self.object.mouvements.select_related("utilisateur")[:20])
        contexte["mouvements"] = mouvements
        contexte["dernier_mouvement"] = mouvements[0] if mouvements else None
        contexte["valeur_immobilisee"] = self.object.quantite_stock * self.object.prix_achat
        contexte["marge_unitaire"] = self.object.prix_vente - self.object.prix_achat
        return contexte


class ProduitCreateView(LoginRequiredMixin, PermissionRequiredMixin, SuccessMessageMixin, CreateView):
    model = Produit
    form_class = ProduitForm
    success_message = "Produit « %(nom)s » créé avec succès."
    extra_context = {"titre": "Nouveau produit", "section": "produits"}
    permission_required = "inventory.add_produit"


class ProduitUpdateView(LoginRequiredMixin, PermissionRequiredMixin, SuccessMessageMixin, UpdateView):
    model = Produit
    form_class = ProduitForm
    success_message = "Produit « %(nom)s » modifié avec succès."
    extra_context = {"titre": "Modifier le produit", "section": "produits"}
    permission_required = "inventory.change_produit"


class MouvementCreateView(LoginRequiredMixin, PermissionRequiredMixin, FormView):
    """
    Vue de base pour l'entrée et la sortie de stock.

    Toute la logique métier reste dans services.enregistrer_mouvement() :
    la vue se contente de valider le formulaire, d'appeler le service et
    de traduire une éventuelle erreur métier en message utilisateur.
    """

    template_name = "inventory/mouvement_form.html"
    form_class = MouvementForm
    permission_required = "inventory.add_mouvement"
    type_mouvement = None
    titre = ""
    section = ""

    def get_initial(self):
        initial = super().get_initial()
        # Permet d'arriver depuis la fiche produit avec le produit pré-sélectionné.
        produit = self.request.GET.get("produit", "")
        if produit.isdigit():
            initial["produit"] = produit
        return initial

    def get_form_kwargs(self):
        # Le formulaire adapte ses libellés et ses champs au type de mouvement.
        kwargs = super().get_form_kwargs()
        kwargs["type_mouvement"] = self.type_mouvement
        return kwargs

    def get_context_data(self, **kwargs):
        contexte = super().get_context_data(**kwargs)
        contexte["titre"] = self.titre
        contexte["type_mouvement"] = self.type_mouvement
        contexte["section"] = self.section

        # Panneau latéral : état du stock du produit choisi, s'il y en a un.
        produit = self._produit_selectionne()
        contexte["produit_selectionne"] = produit
        if produit:
            contexte["valeur_stock_selectionne"] = produit.quantite_stock * produit.prix_achat
        return contexte

    def _produit_selectionne(self):
        """Produit issu du formulaire soumis, ou de l'URL (?produit=...)."""
        identifiant = self.request.POST.get("produit") or self.request.GET.get("produit", "")
        if not str(identifiant).isdigit():
            return None
        return Produit.objects.filter(pk=identifiant).first()

    def form_valid(self, form):
        produit = form.cleaned_data["produit"]
        try:
            services.enregistrer_mouvement(
                produit=produit,
                type_mouvement=self.type_mouvement,
                quantite=form.cleaned_data["quantite"],
                motif=form.cleaned_data["motif"],
                # Traçabilité : on enregistre qui effectue l'opération.
                utilisateur=self.request.user,
                document=form.cleaned_data.get("document", ""),
                destination=form.cleaned_data.get("destination", ""),
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
    section = "entree"


class SortieStockView(MouvementCreateView):
    type_mouvement = Mouvement.SORTIE
    titre = "Sortie de stock"
    section = "sortie"


class AjustementStockView(MouvementCreateView):
    """Constat de perte : casse, vol ou écart d'inventaire."""

    type_mouvement = Mouvement.AJUSTEMENT
    titre = "Ajustement de stock"
    section = "ajustement"


class MouvementListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    """Historique des mouvements, filtrable par produit, type et période."""

    model = Mouvement
    paginate_by = 20
    context_object_name = "mouvements"
    permission_required = "inventory.view_mouvement"
    extra_context = {"section": "historique"}

    def get_queryset(self):
        queryset = Mouvement.objects.select_related("produit", "utilisateur").annotate(
            # Valeur du mouvement au prix d'achat, calculée par la base.
            valeur=F("quantite") * F("produit__prix_achat"),
        )

        # Recherche libre : nom ou référence du produit, ou référence du bon.
        recherche = self.request.GET.get("q", "").strip()
        if recherche:
            queryset = queryset.filter(
                Q(produit__nom__icontains=recherche)
                | Q(produit__reference__icontains=recherche)
                | Q(document__icontains=recherche)
            )

        produit = self.request.GET.get("produit", "")
        if produit.isdigit():
            queryset = queryset.filter(produit_id=produit)

        utilisateur = self.request.GET.get("utilisateur", "")
        if utilisateur.isdigit():
            queryset = queryset.filter(utilisateur_id=utilisateur)

        type_mouvement = self.request.GET.get("type", "")
        if type_mouvement in dict(Mouvement.TYPE_CHOICES):
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
        # Seuls les utilisateurs ayant réellement enregistré un mouvement.
        contexte["utilisateurs"] = (
            User.objects.filter(mouvements__isnull=False).distinct().order_by("username")
        )
        contexte["filtres"] = self.request.GET
        contexte["querystring"] = querystring_sans_page(self.request)
        return contexte
