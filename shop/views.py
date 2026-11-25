# IA: TypeError "ProductViewSet() received an invalid keyword 'throttle_scope'".
# Solución manual: throttle_scope es atributo de clase que lee ScopedRateThrottle,
# no un kwarg de @action/as_view(). Se selecciona el throttle en get_throttles().
from decimal import Decimal

from django.db import transaction
from django.db.models import Avg, Count, DecimalField, ExpressionWrapper, F, Value
from django.db.models.functions import Coalesce
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from drf_spectacular.utils import extend_schema

from .filters import ProductFilter
from .models import Category, Product, Review
from .serializers import (
    CategorySerializer, ProductSerializer, PurchaseSerializer, ReviewSerializer,
)


class CategoryViewSet(viewsets.ModelViewSet):
    queryset = Category.objects.all()
    serializer_class = CategorySerializer
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['name', 'description']
    ordering_fields = ['name', 'created_at']
    ordering = ['name']

    @extend_schema(summary="Productos de una categoría", responses=ProductSerializer(many=True))
    @action(detail=True, methods=['get'])
    def products(self, request, pk=None):
        category = self.get_object()
        queryset = category.products.filter(is_active=True).annotate(
            final_price_value=ExpressionWrapper(
                F('price') * (Value(Decimal('1')) - F('discount_percent') / Value(Decimal('100'))),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            ),
            avg_rating_value=Coalesce(Avg('reviews__rating'), Value(0.0)),
            reviews_total=Count('reviews', distinct=True),
        )
        page = self.paginate_queryset(queryset)
        serializer = ProductSerializer(page, many=True, context={'request': request})
        return self.get_paginated_response(serializer.data)


class ProductViewSet(viewsets.ModelViewSet):
    """
    Filtrado 100% en SQL gracias a las anotaciones:
      final_price_value -> precio con descuento (calculado en la BD)
      avg_rating_value  -> AVG de reseñas
      reviews_total     -> COUNT de reseñas

    Ejemplos:
      /api/products/?category=1&min_price=200&max_price=1500&rating=4&ordering=-final_price_value
      /api/products/?search=iphone&in_stock=true
    """
    serializer_class = ProductSerializer
    filterset_class = ProductFilter
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['name', 'description', 'category__name']
    ordering_fields = ['price', 'final_price_value', 'avg_rating_value', 'stock', 'name', 'created_at']
    ordering = ['-created_at']

    # Scope leído por ScopedRateThrottle (settings: DEFAULT_THROTTLE_RATES['review'])
    throttle_scope = 'review'

    def get_queryset(self):
        return Product.objects.select_related('category').annotate(
            final_price_value=ExpressionWrapper(
                F('price') * (Value(Decimal('1')) - F('discount_percent') / Value(Decimal('100'))),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            ),
            avg_rating_value=Coalesce(Avg('reviews__rating'), Value(0.0)),
            reviews_total=Count('reviews', distinct=True),
        )

    def get_throttles(self):
        # Solo la acción 'reviews' usa el límite anti-spam (5/hora).
        if self.action == 'reviews':
            return [ScopedRateThrottle()]
        return super().get_throttles()

    @extend_schema(summary="Reseñas del producto (GET/POST)")
    @action(detail=True, methods=['get', 'post'])
    def reviews(self, request, pk=None):
        product = self.get_object()

        if request.method == 'GET':
            page = self.paginate_queryset(product.reviews.all())
            serializer = ReviewSerializer(page, many=True, context={'request': request})
            return self.get_paginated_response(serializer.data)

        # El product viene de la URL, no del body (evita suplantación).
        data = request.data.copy()
        data['product'] = product.pk
        serializer = ReviewSerializer(data=data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @extend_schema(summary="Comprar producto (descuenta stock)", request=PurchaseSerializer)
    @action(detail=True, methods=['post'])
    def purchase(self, request, pk=None):
        product = self.get_object()
        serializer = PurchaseSerializer(data=request.data, context={'product': product})
        serializer.is_valid(raise_exception=True)
        quantity = serializer.validated_data['quantity']

        # Transacción + bloqueo de fila: evita condiciones de carrera en el stock.
        with transaction.atomic():
            locked = Product.objects.select_for_update().get(pk=product.pk)
            if quantity > locked.stock:
                return Response({'detail': 'Stock insuficiente.'}, status=status.HTTP_409_CONFLICT)
            locked.stock = F('stock') - quantity
            locked.save(update_fields=['stock'])
            locked.refresh_from_db()
            total = Decimal(product.final_price) * quantity

        return Response({
            'detail': f'Compra realizada: {quantity} x {product.name}',
            'unit_price': f'{product.final_price:.2f}',
            'total': f'{total:.2f}',
            'remaining_stock': locked.stock,
        }, status=status.HTTP_200_OK)


class ReviewViewSet(viewsets.ModelViewSet):
    queryset = Review.objects.select_related('product').all()
    serializer_class = ReviewSerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['product', 'rating']
    ordering_fields = ['created_at', 'rating']
    ordering = ['-created_at']