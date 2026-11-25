from decimal import Decimal
from rest_framework import serializers
from rest_framework.reverse import reverse
from .models import Category, Product, Review


class CategorySerializer(serializers.HyperlinkedModelSerializer):
    """HATEOAS: el cliente navega por los enlaces, sin documentación externa."""
    url = serializers.HyperlinkedIdentityField(view_name='category-detail')
    products_url = serializers.SerializerMethodField()
    products_count = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = ['url', 'id', 'name', 'slug', 'description',
                  'products_count', 'products_url', 'created_at']
        read_only_fields = ['created_at']

    def get_products_url(self, obj):
        return reverse('category-products', kwargs={'pk': obj.pk}, request=self.context.get('request'))

    def get_products_count(self, obj):
        return obj.products.filter(is_active=True).count()


class ReviewSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)

    class Meta:
        model = Review
        fields = ['id', 'product', 'product_name', 'user_name', 'rating', 'comment', 'created_at']
        read_only_fields = ['created_at']

    def validate_rating(self, value):
        if not 1 <= value <= 5:
            raise serializers.ValidationError("El rating debe estar entre 1 y 5.")
        return value


class ProductSerializer(serializers.HyperlinkedModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='product-detail')
    category_url = serializers.HyperlinkedRelatedField(
        view_name='category-detail', source='category', queryset=Category.objects.all()
    )
    category_name = serializers.CharField(source='category.name', read_only=True)

    # Campos calculados de solo lectura (SerializerMethodField)
    final_price = serializers.SerializerMethodField()
    average_rating = serializers.SerializerMethodField()
    reviews_count = serializers.SerializerMethodField()
    savings = serializers.SerializerMethodField()
    stock_status = serializers.SerializerMethodField()
    reviews_url = serializers.SerializerMethodField()
    purchase_url = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            'url', 'id', 'name', 'slug', 'description',
            'price', 'discount_percent', 'final_price', 'savings',
            'stock', 'stock_status', 'image',
            'category_url', 'category_name', 'is_active',
            'average_rating', 'reviews_count', 'reviews_url', 'purchase_url',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['created_at', 'updated_at']

    # --- Reutilizan las anotaciones SQL si existen (evita N+1 queries) ---
    def get_final_price(self, obj):
        value = getattr(obj, 'final_price_value', None) or obj.final_price
        return f"{Decimal(value):.2f}"

    def get_savings(self, obj):
        return f"{Decimal(obj.price) - Decimal(self.get_final_price(obj)):.2f}"

    def get_average_rating(self, obj):
        value = getattr(obj, 'avg_rating_value', None)
        if value is None:
            value = obj.reviews.aggregate_avg() if False else None
            from django.db.models import Avg
            value = obj.reviews.aggregate(a=Avg('rating'))['a'] or 0
        return round(float(value), 2)

    def get_reviews_count(self, obj):
        value = getattr(obj, 'reviews_total', None)
        return value if value is not None else obj.reviews.count()

    def get_stock_status(self, obj):
        if obj.stock == 0:
            return 'agotado'
        if obj.stock <= 5:
            return 'últimas unidades'
        return 'disponible'

    def get_reviews_url(self, obj):
        return reverse('product-reviews', kwargs={'pk': obj.pk}, request=self.context.get('request'))

    def get_purchase_url(self, obj):
        return reverse('product-purchase', kwargs={'pk': obj.pk}, request=self.context.get('request'))

    # --- Validación en el serializador, NO en la vista (regla de la guía) ---
    def validate_price(self, value):
        if value <= 0:
            raise serializers.ValidationError("El precio debe ser mayor a 0.")
        return value

    def validate(self, data):
        """Validación cruzada precio/descuento (soporta PATCH parcial)."""
        price = data.get('price', getattr(self.instance, 'price', None))
        discount = data.get('discount_percent', getattr(self.instance, 'discount_percent', 0))

        if discount is not None and not 0 <= discount <= 100:
            raise serializers.ValidationError({'discount_percent': 'Debe estar entre 0 y 100.'})
        if price is not None and discount == 100:
            raise serializers.ValidationError('Un descuento del 100% dejaría el producto gratis.')
        return data


class PurchaseSerializer(serializers.Serializer):
    """Entrada del endpoint de compra. La lógica de stock se valida aquí."""
    quantity = serializers.IntegerField(min_value=1)

    def validate_quantity(self, value):
        product = self.context['product']
        if product.stock == 0:
            raise serializers.ValidationError(f"'{product.name}' está agotado.")
        if value > product.stock:
            raise serializers.ValidationError(
                f"Stock insuficiente: solo quedan {product.stock} unidades."
            )
        return value