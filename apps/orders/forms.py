from django import forms


class PurchaseForm(forms.Form):
    buyer_email = forms.EmailField(label="Twój e-mail")
    quantity = forms.IntegerField(label="Liczba biletów", min_value=1, initial=1)

    def __init__(self, *args, max_quantity: int = 10, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["quantity"].max_value = max_quantity
        self.fields["quantity"].widget = forms.Select(
            choices=[(i, i) for i in range(1, max_quantity + 1)]
        )
