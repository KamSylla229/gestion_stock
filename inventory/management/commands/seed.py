import random

from django.core.management.base import BaseCommand

from inventory import services
from inventory.models import Categorie, Fournisseur, Mouvement, Produit

# (reference, nom, categorie, prix_achat, prix_vente, seuil_alerte)
PRODUITS = [
    ("ALI-001", "Riz local 25kg", "Alimentation", 12000, 14000, 10),
    ("ALI-002", "Riz importé 25kg", "Alimentation", 15000, 17500, 10),
    ("ALI-003", "Farine de maïs 5kg", "Alimentation", 2000, 2500, 15),
    ("ALI-004", "Huile de palme 5L", "Alimentation", 4500, 5500, 10),
    ("ALI-005", "Huile végétale 5L", "Alimentation", 5000, 6000, 10),
    ("ALI-006", "Sucre en poudre 1kg", "Alimentation", 600, 800, 20),
    ("ALI-007", "Tomate concentrée 400g", "Alimentation", 350, 500, 25),
    ("ALI-008", "Spaghetti 500g", "Alimentation", 400, 600, 20),
    ("ALI-009", "Sel de cuisine 1kg", "Alimentation", 200, 350, 15),
    ("ALI-010", "Lait en poudre 400g", "Alimentation", 1500, 1900, 15),
    ("ALI-011", "Cube Maggi (boîte de 50)", "Alimentation", 1200, 1600, 10),
    ("ALI-012", "Gari 5kg", "Alimentation", 2500, 3200, 10),
    ("HYG-001", "Savon de Marseille", "Hygiène & Entretien", 300, 500, 20),
    ("HYG-002", "Savon liquide 1L", "Hygiène & Entretien", 900, 1300, 15),
    ("HYG-003", "Détergent en poudre 1kg", "Hygiène & Entretien", 700, 1000, 15),
    ("HYG-004", "Eau de javel 1L", "Hygiène & Entretien", 500, 800, 15),
    ("HYG-005", "Papier hygiénique (pack de 4)", "Hygiène & Entretien", 800, 1200, 20),
    ("HYG-006", "Dentifrice", "Hygiène & Entretien", 500, 800, 20),
    ("HYG-007", "Brosse à dents", "Hygiène & Entretien", 200, 400, 25),
    ("HYG-008", "Désinfectant sol 1L", "Hygiène & Entretien", 900, 1400, 10),
    ("BOI-001", "Eau minérale 1.5L (pack de 6)", "Boissons", 1500, 2000, 15),
    ("BOI-002", "Boisson gazeuse 1.5L", "Boissons", 700, 1000, 20),
    ("BOI-003", "Jus de fruit local 1L", "Boissons", 600, 900, 15),
    ("BOI-004", "Bière (casier de 12)", "Boissons", 6000, 7200, 8),
    ("BOI-005", "Café soluble 100g", "Boissons", 1200, 1700, 10),
]

FOURNISSEURS = [
    "Grossiste Dantokpa",
    "Import Cotonou SARL",
    "Bénin Distribution",
    "Fournisseur Ayélala",
]

CATEGORIES = ["Alimentation", "Hygiène & Entretien", "Boissons"]

NB_MOUVEMENTS_SUPPLEMENTAIRES = 35  # + 1 entrée initiale par produit (25) = 60 au total


class Command(BaseCommand):
    help = "Remplit la base avec des données de démonstration : catégories, fournisseurs, produits, mouvements."

    def handle(self, *args, **options):
        if Mouvement.objects.exists():
            self.stdout.write(self.style.WARNING("Des mouvements existent déjà, seed annulé pour éviter les doublons."))
            return

        # Seed fixe : les données générées sont toujours les mêmes d'une exécution à l'autre.
        random.seed(42)

        categories = {nom: Categorie.objects.get_or_create(nom=nom)[0] for nom in CATEGORIES}
        fournisseurs = [Fournisseur.objects.get_or_create(nom=nom)[0] for nom in FOURNISSEURS]

        produits = []
        for i, (reference, nom, nom_categorie, prix_achat, prix_vente, seuil_alerte) in enumerate(PRODUITS):
            produit, _ = Produit.objects.get_or_create(
                reference=reference,
                defaults={
                    "nom": nom,
                    "categorie": categories[nom_categorie],
                    "fournisseur": fournisseurs[i % len(fournisseurs)],
                    "prix_achat": prix_achat,
                    "prix_vente": prix_vente,
                    "seuil_alerte": seuil_alerte,
                },
            )
            produits.append(produit)

        # Une entrée de stock initiale par produit : 25 mouvements.
        for produit in produits:
            quantite_initiale = random.randint(20, 100)
            services.enregistrer_mouvement(
                produit, Mouvement.ENTREE, quantite_initiale, motif="Stock initial"
            )

        # Mouvements supplémentaires répartis au hasard sur les produits.
        # 70% de sorties (ventes) si du stock est disponible, sinon des entrées (réappro).
        for _ in range(NB_MOUVEMENTS_SUPPLEMENTAIRES):
            produit = random.choice(produits)
            if produit.quantite_stock > 0 and random.random() < 0.7:
                quantite = random.randint(1, produit.quantite_stock)
                services.enregistrer_mouvement(produit, Mouvement.SORTIE, quantite, motif="Vente")
            else:
                quantite = random.randint(5, 40)
                services.enregistrer_mouvement(
                    produit, Mouvement.ENTREE, quantite, motif="Réapprovisionnement"
                )

        self.stdout.write(self.style.SUCCESS(
            f"{len(categories)} catégories, {len(fournisseurs)} fournisseurs, "
            f"{len(produits)} produits, {Mouvement.objects.count()} mouvements créés."
        ))
