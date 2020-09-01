from typing import TYPE_CHECKING, List, Union
from urllib.parse import urlencode

from django.conf import settings
from django.db import transaction

from ...core.taxes import TaxedMoney, zero_taxed_money
from ..models import Product, ProductChannelListing
from ..tasks import update_products_discounted_prices_task

if TYPE_CHECKING:
    # flake8: noqa
    from datetime import date, datetime

    from django.db.models.query import QuerySet

    from ..models import Product, ProductVariant, Category


def calculate_revenue_for_variant(
    variant: "ProductVariant", start_date: Union["date", "datetime"]
) -> TaxedMoney:
    """Calculate total revenue generated by a product variant."""
    revenue = zero_taxed_money()
    for order_line in variant.order_lines.all():
        if order_line.order.created >= start_date:
            net = order_line.unit_price_net * order_line.quantity
            gross = order_line.unit_price_gross * order_line.quantity
            revenue += TaxedMoney(net, gross)
    return revenue


@transaction.atomic
def delete_categories(categories_ids: List[str]):
    """Delete categories and perform all necessary actions.

    Set products of deleted categories as unpublished, delete categories
    and update products minimal variant prices.
    """
    from ..models import Product, Category

    categories = Category.objects.select_for_update().filter(pk__in=categories_ids)
    categories.prefetch_related("products")

    products = Product.objects.none()
    for category in categories:
        products = products | collect_categories_tree_products(category)

    ProductChannelListing.objects.filter(product__in=products).update(
        is_published=False, publication_date=None
    )
    product_ids = list(products.values_list("id", flat=True))
    categories.delete()
    update_products_discounted_prices_task.delay(product_ids=product_ids)


def collect_categories_tree_products(category: "Category") -> "QuerySet[Product]":
    """Collect products from all levels in category tree."""
    products = category.products.all()
    descendants = category.get_descendants()
    for descendant in descendants:
        products = products | descendant.products.all()
    return products


def get_products_ids_without_variants(products_list: "List[Product]") -> "List[str]":
    """Return list of product's ids without variants."""
    products_ids = [product.id for product in products_list]
    products_ids_without_variants = Product.objects.filter(
        id__in=products_ids, variants__isnull=True
    ).values_list("id", flat=True)
    return list(products_ids_without_variants)
