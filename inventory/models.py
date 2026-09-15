from django.conf import settings
from django.db import models
from django.urls import reverse


class Categorie(models.Model):
    """Regroupe les produits par famille (ex: Alimentation, Boissons)."""

    nom = models.CharField(max_length=100, unique=True)
    actif = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Catégorie"
        verbose_name_plural = "Catégories"
        ordering = ["nom"]

    def __str__(self):
        return self.nom


class Fournisseur(models.Model):
    """Un fournisseur qui livre des produits à l'entreprise."""

    nom = models.CharField(max_length=150)
    telephone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    adresse = models.CharField(max_length=255, blank=True)
    actif = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Fournisseur"
        verbose_name_plural = "Fournisseurs"
        ordering = ["nom"]

    def __str__(self):
        return self.nom


class Produit(models.Model):
    """
    Un article vendu par l'entreprise.

    quantite_stock ne doit JAMAIS être modifié directement ailleurs que
    dans inventory/services.py, pour garantir qu'il reste synchronisé
    avec l'historique des Mouvement.
    """

    reference = models.CharField(max_length=50, unique=True)
    nom = models.CharField(max_length=150)
    categorie = models.ForeignKey(
        Categorie, on_delete=models.PROTECT, related_name="produits"
    )
    fournisseur = models.ForeignKey(
        Fournisseur,
        on_delete=models.PROTECT,
        related_name="produits",
        null=True,
        blank=True,
    )
    prix_achat = models.DecimalField(max_digits=10, decimal_places=2)
    prix_vente = models.DecimalField(max_digits=10, decimal_places=2)
    quantite_stock = models.PositiveIntegerField(default=0)
    seuil_alerte = models.PositiveIntegerField(default=5)
    actif = models.BooleanField(default=True)
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Produit"
        verbose_name_plural = "Produits"
        ordering = ["nom"]
        # Permissions métier, en plus des add/change/delete/view générés par Django.
        # Elles protègent les fonctionnalités réservées au gérant.
        permissions = [
            ("acceder_tableau_bord", "Peut accéder au tableau de bord"),
            ("exporter_stock", "Peut exporter les données de stock"),
        ]

    def __str__(self):
        return f"{self.reference} — {self.nom}"

    def get_absolute_url(self):
        # Utilisée automatiquement par CreateView/UpdateView pour rediriger
        # vers la fiche du produit après un enregistrement réussi.
        return reverse("inventory:produit_detail", kwargs={"pk": self.pk})

    @property
    def stock_bas(self):
        return self.quantite_stock <= self.seuil_alerte


class Mouvement(models.Model):
    """
    Trace un mouvement de stock (entrée ou sortie) sur un produit.

    Un Mouvement ne doit jamais être créé directement dans une vue ou un
    script : il doit passer par services.enregistrer_mouvement(), seule
    fonction autorisée à modifier Produit.quantite_stock en même temps.
    """

    ENTREE = "ENTREE"
    SORTIE = "SORTIE"
    AJUSTEMENT = "AJUSTEMENT"
    TYPE_CHOICES = [
        (ENTREE, "Entrée"),
        (SORTIE, "Sortie"),
        (AJUSTEMENT, "Ajustement"),
    ]

    # Types qui diminuent le stock.
    TYPES_SORTANTS = (SORTIE, AJUSTEMENT)

    produit = models.ForeignKey(
        Produit, on_delete=models.PROTECT, related_name="mouvements"
    )
    type_mouvement = models.CharField(max_length=10, choices=TYPE_CHOICES)
    quantite = models.PositiveIntegerField()
    motif = models.CharField(max_length=255, blank=True)

    # Qui a enregistré le mouvement. PROTECT : on ne supprime pas un utilisateur
    # qui a de l'historique. null=True pour les mouvements antérieurs à ce champ.
    utilisateur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="mouvements",
        null=True,
        blank=True,
    )

    # Stock du produit juste après ce mouvement, figé au moment de l'écriture.
    # Permet de relire l'historique sans avoir à le rejouer. null pour les
    # mouvements enregistrés avant l'ajout du champ.
    stock_apres = models.PositiveIntegerField(null=True, blank=True)

    # Pièce justificative : bon de sortie, bordereau de livraison...
    document = models.CharField(max_length=50, blank=True)

    # Client, chantier ou provenance selon le type de mouvement.
    destination = models.CharField(max_length=255, blank=True)

    date_mouvement = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Mouvement"
        verbose_name_plural = "Mouvements"
        # -id départage les mouvements enregistrés dans la même fraction de
        # seconde : sans lui, leur ordre d'affichage serait indéterminé.
        ordering = ["-date_mouvement", "-id"]

    def __str__(self):
        return f"{self.get_type_mouvement_display()} de {self.quantite} — {self.produit.nom}"
