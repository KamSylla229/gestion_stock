from datetime import timedelta

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
    # Personne à joindre chez ce fournisseur.
    contact = models.CharField(max_length=100, blank=True)
    telephone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    adresse = models.CharField(max_length=255, blank=True)
    # Délai habituel entre la commande et la livraison. Sert à savoir s'il
    # reste assez de stock pour tenir jusqu'au réapprovisionnement.
    delai_jours = models.PositiveIntegerField(null=True, blank=True)
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
    # Unité de vente, affichée à côté des quantités : sac, barre, casier,
    # carton… Texte libre, car elle varie d'un métier à l'autre.
    unite = models.CharField(max_length=30, blank=True)
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

    @property
    def unite_affichee(self):
        """Unité à afficher après une quantité, « unité » si non renseignée."""
        return self.unite or "unité"


class Commande(models.Model):
    """
    Commande passée à un fournisseur.

    Cycle de vie :
      BROUILLON  -> on compose la commande, les lignes sont modifiables
      ENVOYEE    -> transmise au fournisseur, les lignes sont figées
      PARTIELLE  -> une partie seulement des quantités a été livrée
      RECUE      -> tout a été livré
      ANNULEE    -> abandonnée (on n'efface jamais une commande)

    La réception d'une ligne crée une entrée de stock : elle passe donc
    obligatoirement par services.receptionner_ligne_commande().
    """

    BROUILLON = "BROUILLON"
    ENVOYEE = "ENVOYEE"
    PARTIELLE = "PARTIELLE"
    RECUE = "RECUE"
    ANNULEE = "ANNULEE"
    STATUT_CHOICES = [
        (BROUILLON, "Brouillon"),
        (ENVOYEE, "Envoyée"),
        (PARTIELLE, "Reçue partiellement"),
        (RECUE, "Reçue"),
        (ANNULEE, "Annulée"),
    ]

    # Statuts dans lesquels on peut encore réceptionner des marchandises.
    STATUTS_RECEPTIONNABLES = (ENVOYEE, PARTIELLE)

    reference = models.CharField(max_length=20, unique=True)
    fournisseur = models.ForeignKey(
        Fournisseur, on_delete=models.PROTECT, related_name="commandes"
    )
    statut = models.CharField(max_length=15, choices=STATUT_CHOICES, default=BROUILLON)
    commentaire = models.CharField(max_length=255, blank=True)

    cree_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="commandes",
        null=True,
        blank=True,
    )
    date_creation = models.DateTimeField(auto_now_add=True)
    date_envoi = models.DateTimeField(null=True, blank=True)
    date_reception = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Commande"
        verbose_name_plural = "Commandes"
        ordering = ["-date_creation", "-id"]
        permissions = [
            ("receptionner_commande", "Peut réceptionner une commande"),
        ]

    def __str__(self):
        return f"{self.reference} — {self.fournisseur.nom}"

    def get_absolute_url(self):
        return reverse("inventory:commande_detail", kwargs={"pk": self.pk})

    @property
    def montant_total(self):
        """Coût total de la commande, au prix négocié à la commande."""
        return sum(ligne.montant for ligne in self.lignes.all())

    @property
    def modifiable(self):
        """Seul un brouillon accepte l'ajout ou le retrait de lignes."""
        return self.statut == self.BROUILLON

    @property
    def receptionnable(self):
        return self.statut in self.STATUTS_RECEPTIONNABLES

    @property
    def date_livraison_prevue(self):
        """Date de livraison estimée d'après le délai habituel du fournisseur."""
        if not self.date_envoi or not self.fournisseur.delai_jours:
            return None
        return self.date_envoi + timedelta(days=self.fournisseur.delai_jours)


class LigneCommande(models.Model):
    """Une ligne de commande : un produit, une quantité, un prix."""

    commande = models.ForeignKey(
        Commande, on_delete=models.CASCADE, related_name="lignes"
    )
    produit = models.ForeignKey(
        Produit, on_delete=models.PROTECT, related_name="lignes_commande"
    )
    quantite_commandee = models.PositiveIntegerField()
    quantite_recue = models.PositiveIntegerField(default=0)
    # Prix figé au moment de la commande : le prix du produit peut changer ensuite.
    prix_unitaire = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        verbose_name = "Ligne de commande"
        verbose_name_plural = "Lignes de commande"
        ordering = ["id"]
        # Un produit n'apparaît qu'une fois par commande.
        constraints = [
            models.UniqueConstraint(
                fields=["commande", "produit"], name="produit_unique_par_commande"
            )
        ]

    def __str__(self):
        return f"{self.quantite_commandee} × {self.produit.nom}"

    @property
    def montant(self):
        return self.quantite_commandee * self.prix_unitaire

    @property
    def quantite_restante(self):
        return max(self.quantite_commandee - self.quantite_recue, 0)

    @property
    def soldee(self):
        return self.quantite_recue >= self.quantite_commandee


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

    # Renseigné quand l'entrée provient de la réception d'une commande.
    ligne_commande = models.ForeignKey(
        "LigneCommande",
        on_delete=models.PROTECT,
        related_name="mouvements",
        null=True,
        blank=True,
    )

    date_mouvement = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Mouvement"
        verbose_name_plural = "Mouvements"
        # -id départage les mouvements enregistrés dans la même fraction de
        # seconde : sans lui, leur ordre d'affichage serait indéterminé.
        ordering = ["-date_mouvement", "-id"]

    def __str__(self):
        return f"{self.get_type_mouvement_display()} de {self.quantite} — {self.produit.nom}"
