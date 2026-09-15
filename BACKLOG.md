# Backlog StockFlow

Fonctionnalités volontairement reportées après la v1.0. Le périmètre de la v1.0
a été tenu fermé pour livrer un produit fiable plutôt qu'un produit large.

---

# Écart avec les maquettes de référence

La refonte visuelle (`ea19f94`) a repris l'identité des maquettes sur les
7 écrans existants. Les blocs **A** et **B** ont ensuite été livrés.

Restent à faire : **C** (champs sur Produit / Fournisseur), **D** (écrans
entiers), **E** (indicateurs dont la règle de calcul doit être tranchée) et
**F** (hors périmètre v1).

Le classement est fait par **prérequis technique**, pas par écran : c'est ce qui
détermine l'ordre de réalisation.

## A. Sans modification du schéma — FAIT

Livrés sans toucher au schéma. `django.contrib.humanize` a été préféré à
`USE_THOUSAND_SEPARATOR` : ce dernier aurait aussi formaté les identifiants
(`value="1 234"` dans les listes déroulantes), cassant les formulaires.

| # | Élément | Écran |
|---|---|---|
| ~~A1~~ | Séparateur de milliers sur les montants (`18 450 000 F` au lieu de `18450000`) — `USE_THOUSAND_SEPARATOR = True` | partout |
| ~~A2~~ | Colonne « Valeur » (stock × prix d'achat) dans la liste des produits | Produits |
| ~~A3~~ | Onglets d'état complets : Tous / En stock / Sous seuil / Rupture / Désactivés | Produits |
| ~~A4~~ | Pagination numérotée (1 2 3 … 10) au lieu de Précédent / Suivant | Produits, Historique |
| ~~A5~~ | KPI « Mouvements du jour » séparés entrées / sorties (12 / 9) | Tableau de bord |
| ~~A6~~ | KPI « Sorties de la semaine » valorisées au prix d'achat | Tableau de bord |
| ~~A7~~ | Carte « Produits les plus mouvementés » (sorties et restant sur 7 jours) | Tableau de bord |
| ~~A8~~ | Sélecteur de période Jour / 7 jours / Mois | Tableau de bord |
| ~~A9~~ | KPI fiche produit : sorties sur 30 jours, moyenne par jour, couverture en jours, dernière entrée | Fiche produit |
| ~~A10~~ | Marge affichée en pourcentage en plus du montant | Fiche produit |
| ~~A11~~ | Coordonnées du fournisseur complètes sur la fiche produit (déjà en base, partiellement affichées) | Fiche produit |
| ~~A12~~ | ~~Recherche libre dans l'historique~~ — livré avec le bloc B | Historique |
| ~~A13~~ | Export Excel de l'**historique des mouvements** (l'export actuel ne porte que sur l'état du stock) | Historique |
| ~~A14~~ | Bouton « Sortir tout le stock disponible » | Sortie |
| ~~A15~~ | Compteurs « Sorties aujourd'hui » et « Valeur de la sortie » dans le panneau latéral | Sortie |
| ~~A16~~ | Encadré conseil « Le seuil a été franchi N fois en 30 jours » | Fiche produit |

## B. Champs sur `Mouvement` — FAIT

Migration `0004` : `utilisateur`, `stock_apres`, `document`, `destination`,
et le type `AJUSTEMENT`. Les sept éléments ci-dessous sont livrés.

Décision métier retenue : un **ajustement diminue** le stock (casse, vol,
écart d'inventaire) et exige un motif. Une correction à la hausse se fait
par une entrée avec le motif « Régularisation ».

Les mouvements enregistrés avant cette migration ont `utilisateur` et
`stock_apres` à vide : l'interface affiche « — ».

| # | Élément | Écran |
|---|---|---|
| ~~B1~~ | Colonne « Par » / utilisateur dans l'historique, la fiche produit et le tableau de bord | 3 écrans |
| ~~B2~~ | Filtre par utilisateur dans l'historique | Historique |
| ~~B3~~ | Colonne « Stock après » figée au moment du mouvement | Historique, fiche produit |
| ~~B4~~ | Colonne « Document » (BON-0412, BL-2214) + recherche par référence de bon | Historique |
| ~~B5~~ | Champs « Client ou destination » et « Référence du bon » au formulaire de sortie | Sortie |
| ~~B6~~ | Type de mouvement « Ajustement » (casse, écart d'inventaire) | Sortie, historique |
| ~~B7~~ | Colonne « Valeur » du mouvement | Historique |

## C. Nécessite des champs sur `Produit` / `Fournisseur`

| # | Élément | Champ à ajouter |
|---|---|---|
| C1 | Unité de mesure affichée partout (« 14 barres », « 0 sac ») | `Produit.unite` |
| C2 | Délai de livraison habituel du fournisseur | `Fournisseur.delai_jours` |
| C3 | Contact nommé chez le fournisseur | `Fournisseur.contact` |
| C4 | Date de dernière commande et bouton « Commander N unités » | modèle `Commande` à créer |

## D. Écrans entiers à construire

### D1 · Page « Alertes et réapprovisionnement » (maquette 7)
- Trois colonnes : Rupture totale / Sous le seuil / À surveiller
- Estimation des jours restants avant rupture (basée sur les sorties des 30 derniers jours)
- Tableau de proposition de réapprovisionnement : quantité à commander, coût estimé, délai
- Total à engager
- Aperçu du dernier email d'alerte envoyé, avec son statut
- Bouton « Régler les seuils » (édition groupée des seuils)
- Bouton « Générer un bon de commande »

Prérequis : C2 (délai fournisseur) pour les colonnes délai.

### D2 · Page « Rapports » (maquette 8)
- Cinq types : État du stock, Mouvements, Rotation des produits, Activité par utilisateur, Pertes et casses
- Filtres période / catégories / statut
- Aperçu à l'écran : tableau par catégorie avec références, quantité, valeur, part en %, alertes
- Graphique de répartition de la valeur
- Export Excel **et PDF**

Prérequis : B1 pour « Activité par utilisateur », B6 pour « Pertes et casses ».

### D3 · Page « Paramètres »
- Nom et identité de l'entreprise (affichés dans la sidebar, les emails et les exports)
- Adresse de destination des alertes
- Heure d'envoi du rapport quotidien
- Gestion des catégories et des fournisseurs sans passer par `/admin/`

### D4 · Compléments d'authentification (maquette 1)
- Case « Rester connecté »
- Lien « Mot de passe oublié » et parcours de réinitialisation par email
- Bouton œil pour afficher le mot de passe saisi
- Journal des connexions (« chaque connexion est enregistrée avec sa date et son heure »)

## E. Indicateurs avancés

Calculs à spécifier avant de coder — la règle métier n'est pas évidente.

| # | Indicateur | Question à trancher |
|---|---|---|
| E1 | Rotation moyenne (21 jours) | Sur quelle base : sorties / stock moyen, sur quelle période ? |
| E2 | Stock dormant (14 réf.) | Seuil d'inactivité : 60 jours ? configurable ? |
| E3 | Marge potentielle | Sur le stock actuel, au prix de vente affiché |
| E4 | Variation « +4,1 % sur 7 jours » | Variation de quoi : valeur du stock, sorties ? |
| E5 | Couverture en jours | Moyenne des sorties sur 30 jours, ou pondérée ? |

## F. Hors périmètre v1 — catalogue d'upsell

Explicitement exclus par la dernière page des maquettes.

- Multi-dépôts et transferts entre dépôts
- Inventaire physique et régularisation
- Scan code-barres / QR
- Quantité réservée (affichée « Réservé 0 » dans la maquette)
- Rafraîchissement temps réel (« actualisé il y a 8 s »)

---

## Priorité haute

### Déploiement
Volontairement exclu de cette itération.
- Installer `whitenoise`, réactiver son middleware et le `STORAGES` associé
  (les deux emplacements sont commentés dans `config/settings.py`)
- Installer `psycopg[binary]` et basculer `DATABASE_URL` sur PostgreSQL
- Renseigner `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `SITE_URL` et `DEBUG=False`
- Planifier `rapport_quotidien` (cron ou tâche planifiée de l'hébergeur)

### SMTP réel
La configuration est en place et pilotée par `.env`, mais l'envoi n'a été validé
qu'avec le backend console. À faire : renseigner un fournisseur réel (Gmail avec
mot de passe d'application, ou Brevo), puis valider avec `python manage.py tester_email`.

## Priorité moyenne

### Gestion des catégories et fournisseurs dans l'application
Ils ne se créent et se modifient aujourd'hui que via `/admin/`. Une interface
dédiée éviterait de donner l'accès à l'administration Django au gérant.

### Inventaire physique et régularisation
Pouvoir enregistrer un comptage réel et générer automatiquement le mouvement
d'ajustement correspondant — avec un type `AJUSTEMENT` en plus de
`ENTREE` / `SORTIE`, et un motif obligatoire.

### Rapport hebdomadaire et mensuel
La commande `rapport_quotidien` accepte déjà un paramètre `--jour`. Étendre le
principe à une période (produits les plus vendus, rotation du stock).

### Anti-répétition des alertes
Une alerte part au franchissement du seuil. Si un produit remonte au-dessus du
seuil puis repasse dessous, une nouvelle alerte part — c'est voulu. À surveiller
en usage réel : si le volume d'emails devient gênant, ajouter une temporisation
(pas plus d'une alerte par produit et par 24 h).

## Priorité basse

- **Multi-entrepôts** : un stock par dépôt, avec transferts entre dépôts
- **Rôles supplémentaires** : comptable (lecture seule des données financières),
  vendeur (sorties uniquement)
- **Audit avancé** : journal des connexions et des modifications de prix
- **Scan code-barres / QR** : saisie des mouvements au lecteur ou au téléphone
- **Notifications WhatsApp** : canal plus efficace que l'email au Bénin
- **API REST** : pour une intégration avec une caisse ou un logiciel comptable
- **Application mobile** : consultation du stock hors du bureau
- **Intégration fournisseurs** : commandes de réapprovisionnement automatiques
- **Prévision de demande** : estimation des besoins à partir de l'historique
- **Graphiques** : courbes d'évolution du stock et des ventes

## Dette technique identifiée

- **Python 3.11** alors que la cible du projet est 3.12. Sans impact fonctionnel
  connu, à aligner lors de la mise en production.
- **Concurrence sur le stock** : `enregistrer_mouvement()` est atomique, mais
  deux sorties simultanées sur le même produit pourraient théoriquement lire le
  même stock de départ. Sous PostgreSQL, ajouter un `select_for_update()` sur le
  produit règle le problème. Non critique pour une PME mono-poste.
