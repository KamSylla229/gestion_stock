from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand

# Répartition des responsabilités :
#
# Gerant      : pilote l'entreprise. Voit les chiffres (tableau de bord, valeur
#               du stock), exporte les données, crée et modifie les produits
#               (donc les prix), gère catégories et fournisseurs.
#
# Magasinier  : gère le quotidien. Enregistre les entrées et sorties, consulte
#               les produits et l'historique. N'a accès ni aux données
#               financières (tableau de bord), ni à l'export, ni aux prix.

PERMISSIONS_GERANT = [
    "view_produit", "add_produit", "change_produit",
    "view_categorie", "add_categorie", "change_categorie",
    "view_fournisseur", "add_fournisseur", "change_fournisseur",
    "view_mouvement", "add_mouvement",
    "acceder_tableau_bord",
    "exporter_stock",
]

PERMISSIONS_MAGASINIER = [
    "view_produit",
    "view_categorie",
    "view_fournisseur",
    "view_mouvement", "add_mouvement",
]

GROUPES = {
    "Gerant": PERMISSIONS_GERANT,
    "Magasinier": PERMISSIONS_MAGASINIER,
}


class Command(BaseCommand):
    help = "Crée (ou met à jour) les groupes Gerant et Magasinier avec leurs permissions."

    def handle(self, *args, **options):
        for nom_groupe, codes_permissions in GROUPES.items():
            groupe, cree = Group.objects.get_or_create(name=nom_groupe)

            permissions = Permission.objects.filter(
                codename__in=codes_permissions,
                content_type__app_label="inventory",
            )

            manquantes = set(codes_permissions) - set(permissions.values_list("codename", flat=True))
            if manquantes:
                # Ne pas masquer le problème : des permissions attendues sont absentes.
                raise SystemExit(
                    f"Permissions introuvables pour le groupe {nom_groupe} : {sorted(manquantes)}. "
                    "Avez-vous appliqué les migrations ?"
                )

            # set() remplace la liste : la commande est rejouable sans doublon
            # et corrige un groupe modifié à la main.
            groupe.permissions.set(permissions)

            etat = "créé" if cree else "mis à jour"
            self.stdout.write(self.style.SUCCESS(
                f"Groupe {nom_groupe} {etat} ({permissions.count()} permissions)."
            ))
