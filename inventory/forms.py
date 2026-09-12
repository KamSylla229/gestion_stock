from django import forms

from inventory.models import Produit


def appliquer_classes_bootstrap(champs):
    """Ajoute les classes Bootstrap 5 aux widgets d'un formulaire."""
    for champ in champs.values():
        if isinstance(champ.widget, forms.CheckboxInput):
            champ.widget.attrs.setdefault("class", "form-check-input")
        elif isinstance(champ.widget, forms.Select):
            champ.widget.attrs.setdefault("class", "form-select")
        else:
            champ.widget.attrs.setdefault("class", "form-control")


class ProduitForm(forms.ModelForm):
    """
    Formulaire de création / modification d'un produit.

    quantite_stock est volontairement absent : il ne peut être modifié
    que par inventory/services.py, via un Mouvement.
    """

    class Meta:
        model = Produit
        fields = [
            "reference",
            "nom",
            "categorie",
            "fournisseur",
            "prix_achat",
            "prix_vente",
            "seuil_alerte",
            "actif",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        appliquer_classes_bootstrap(self.fields)


class MouvementForm(forms.Form):
    """
    Formulaire d'entrée / sortie de stock.

    Ce n'est volontairement PAS un ModelForm : la création du Mouvement et
    la mise à jour du stock doivent passer par services.enregistrer_mouvement(),
    jamais par un form.save() qui contournerait la logique métier.
    """

    produit = forms.ModelChoiceField(
        queryset=Produit.objects.filter(actif=True),
        label="Produit",
        empty_label="— Choisir un produit —",
    )
    quantite = forms.IntegerField(min_value=1, label="Quantité")
    motif = forms.CharField(max_length=255, required=False, label="Motif")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        appliquer_classes_bootstrap(self.fields)
