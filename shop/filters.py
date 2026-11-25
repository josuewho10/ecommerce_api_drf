# IA: filtrado en memoria con list() rompía la regla "No filtrar en memoria".
# Solución manual: FilterSet declarativo + anotaciones en el QuerySet -> WHERE en SQL.
import django_filters
from .models import Product, Category


class ProductFilter(django_filters.FilterSet):
    """Filtros traducidos a SQL (lazy evaluation, sin traer rows a Python)."""

    min_price = django_filters.NumberFilter(
        field_name='final_price_value', lookup_expr='gte',
        label='Precio final mínimo (con descuento aplicado)'
    )
    max_price = django_filters.NumberFilter(
        field_name='final_price_value', lookup_expr='lte',
        label='Precio final máximo'
    )
    rating = django_filters.NumberFilter(
        field_name='avg_rating_value', lookup_expr='gte',
        label='Rating promedio mínimo (1-5)'
    )
    name = django_filters.CharFilter(field_name='name', lookup_expr='icontains')
    category = django_filters.ModelChoiceFilter(queryset=Category.objects.all())
    in_stock = django_filters.BooleanFilter(method='filter_in_stock', label='Solo con stock')

    class Meta:
        model = Product
        fields = ['category', 'is_active', 'name', 'min_price', 'max_price', 'rating', 'in_stock']

    def filter_in_stock(self, queryset, name, value):
        return queryset.filter(stock__gt=0) if value else queryset.filter(stock=0)