# StockFlow — règles du projet

## Contexte
Application Django de gestion de stock pour PME au Bénin.
MVP livrable en 5 jours × 4h. Projet commercial, pas un exercice.

## Stack imposée — ne jamais proposer autre chose
- Python 3.12, Django 5.x
- Templates Django + Bootstrap 5 via CDN
- SQLite en dev, PostgreSQL en prod (Render)
- django-environ, openpyxl, whitenoise
- Un seul settings.py + fichier .env

## Interdit cette semaine
Docker, Celery, Redis, Django REST Framework, React, Tailwind,
htmx, bibliothèques de graphiques, système de permissions maison.

## Interdit tout court
- Ajouter une fonctionnalité non demandée explicitement
- Modifier Produit.quantite_stock ailleurs que dans services.py
- Supprimer des données (on désactive, on ne supprime pas)
- Écrire du code que je n'ai pas demandé "au cas où"

## Structure
config/          projet Django
inventory/       application unique
Modèles : Categorie, Fournisseur, Produit, Mouvement

## Langue
Code, modèles, champs et interface en français.
Commentaires en français.

## Comportement attendu
Réponses courtes. Si une décision est ambiguë, tu poses la question
avant d'écrire du code. Tu ne refactores jamais sans qu'on te le demande.
