from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_date

from inventory import services, statistiques


class Command(BaseCommand):
    help = (
        "Génère et envoie au gérant le rapport quotidien de l'état du stock. "
        "Destiné à être lancé une fois par jour (tâche planifiée)."
    )

    def add_arguments(self, parseur):
        parseur.add_argument(
            "--destinataire",
            help="Adresse email du destinataire (par défaut : ALERTE_EMAIL_DESTINATAIRE).",
        )
        parseur.add_argument(
            "--jour",
            help="Jour du rapport au format AAAA-MM-JJ (par défaut : aujourd'hui).",
        )
        parseur.add_argument(
            "--apercu",
            action="store_true",
            help="Affiche le résumé dans le terminal sans envoyer d'email.",
        )

    def handle(self, *args, **options):
        jour = None
        if options["jour"]:
            jour = parse_date(options["jour"])
            if jour is None:
                raise CommandError(
                    f"Date invalide : {options['jour']!r}. Format attendu : AAAA-MM-JJ."
                )

        stats_jour = statistiques.statistiques_du_jour(jour)
        kpis = statistiques.kpis_stock()
        nombre_alertes = statistiques.produits_en_alerte().count()

        self.stdout.write(f"Rapport du {stats_jour['jour'].strftime('%d/%m/%Y')}")
        self.stdout.write(f"  Produits actifs        : {kpis['produits_actifs']}")
        self.stdout.write(f"  Produits en rupture    : {kpis['produits_en_rupture']}")
        self.stdout.write(f"  Produits sous seuil    : {nombre_alertes}")
        self.stdout.write(f"  Mouvements du jour     : {stats_jour['mouvements_nombre']}")
        self.stdout.write(
            f"  Entrées / sorties      : {stats_jour['entrees_nombre']} / {stats_jour['sorties_nombre']}"
        )

        if options["apercu"]:
            self.stdout.write(self.style.WARNING("Mode aperçu : aucun email envoyé."))
            return

        envoye = services.envoyer_rapport_quotidien(
            destinataire=options["destinataire"], jour=jour
        )

        if envoye:
            self.stdout.write(self.style.SUCCESS("Rapport quotidien envoyé."))
        else:
            # Erreur explicite : la commande échoue visiblement (code de sortie non nul)
            # pour qu'une tâche planifiée puisse la détecter.
            raise CommandError(
                "Échec de l'envoi du rapport. Vérifiez ALERTE_EMAIL_DESTINATAIRE et la "
                "configuration SMTP (voir les logs pour le détail de l'erreur)."
            )
