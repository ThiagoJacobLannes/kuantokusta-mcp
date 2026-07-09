"""FastMCP stdio server exposing KuantoKusta.pt search tools."""

from __future__ import annotations

import logging
import sys
from typing import Any

from mcp.server.fastmcp import FastMCP

from .scraper import get_product_detail, search_products

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s kuantokusta-mcp %(levelname)s %(message)s",
)
log = logging.getLogger("kuantokusta_mcp")

mcp = FastMCP("kuantokusta")


@mcp.tool()
def search(
    query: str,
    area: str | None = None,
    family: str | None = None,
    sort: str = "cheap",
    price_min: int | None = None,
    price_max: int | None = None,
    brands: list[str] | None = None,
    features: dict[str, str] | None = None,
    page: int = 1,
) -> dict[str, Any]:
    """Search for products on KuantoKusta.pt — Portugal's main price comparator.

    Returns up to 48 products per page plus `available_filters` showing all
    filter options that can be applied to narrow the results. Filters are
    **dynamic** and change per product type — always read `available_filters`
    from the response rather than guessing values.

    Recommended workflow:
      1. Call search with just `query` (and optionally `sort`/price range).
      2. Read `available_filters` in the response to see areas, brands, and
         product-specific filters (color, capacity, energy class, typology…).
      3. Call search again with the desired filter values to get refined results.

    Args:
        query:    Search keywords, e.g. "frigorífico", "iphone 15", "tv 55 polegadas".
        area:     Top-level category — use a value from `available_filters` where
                  field="area". Example: "Electrodomésticos", "Informática".
        family:   Sub-category within an area — use a value from `available_filters`
                  where field="family". Example: "Grandes Electrodomésticos".
        sort:     "cheap" (default) | "expensive" | "popular" | "alphabetical" | "newest".
        price_min: Minimum price in EUR (integer).
        price_max: Maximum price in EUR (integer).
        brands:   List of brand names — use values from `available_filters` where
                  field="brand". Example: ["Samsung", "LG"].
        features: Dict of product-type-specific filter values — keys and valid values
                  come from `available_filters` where field is NOT area/family/brand.
                  Example: {"tipologia_s": "combinados", "cor_s": "inox",
                             "classe_energetica_s": "classe a"}.
                  For a laptop search the keys would be different (ram_s, os_s, etc.)
                  — always derive them from the previous call's available_filters.
        page:     Results page number (default 1; ~48 products per page).
    """
    try:
        result = search_products(
            query=query,
            area=area,
            family=family,
            sort=sort,
            price_min=price_min,
            price_max=price_max,
            brands=brands,
            features=features,
            page=page,
        )
        return {
            "total_results": result.total,
            "page": result.page,
            "products": [
                {
                    "name": p.name,
                    "price_from_eur": p.price_from,
                    "store_count": p.store_count,
                    "url": p.url,
                    "image_url": p.image_url,
                    "badges": p.badges,
                    "product_id": p.product_id,
                }
                for p in result.products
            ],
            "available_filters": [
                {
                    "field": fg.field,
                    "title": fg.title,
                    "param": fg.param,
                    "options": [
                        {
                            "value": opt.value,
                            "label": opt.label,
                            "count": opt.count,
                            "active": opt.active,
                        }
                        for opt in fg.options
                    ],
                }
                for fg in result.available_filters
            ],
        }
    except Exception as exc:
        log.exception("search failed")
        return {"error": str(exc)}


@mcp.tool()
def get_product(url: str) -> dict[str, Any]:
    """Get full details and all store prices for a KuantoKusta product.

    Pass the product URL from a `search` result. Returns the product name,
    description, technical specs (45+ attributes), and all store offers sorted
    by **total price** (product + shipping) — so you always see the true
    cheapest option, not just the lowest sticker price.

    Args:
        url: KuantoKusta product URL from a search result, e.g.
             "https://www.kuantokusta.pt/p/1967884/mini-com-congelador-smeg-fa120e"
    """
    try:
        detail = get_product_detail(url)
        return {
            "name": detail.name,
            "url": detail.url,
            "description": detail.description,
            "image_url": detail.image_url,
            "specs": detail.specs,
            "offers": [
                {
                    "store": o.store_name,
                    "price_eur": o.price,
                    "shipping_eur": o.shipping_cost,
                    "total_price_eur": o.total_price,
                    "free_shipping": o.free_shipping,
                    "buy_url": o.url,
                    "in_stock": o.in_stock,
                }
                for o in detail.offers
            ],
            "best_price_eur": detail.offers[0].price if detail.offers else None,
            "best_total_price_eur": detail.offers[0].total_price if detail.offers else None,
            "best_store": detail.offers[0].store_name if detail.offers else None,
        }
    except Exception as exc:
        log.exception("get_product failed")
        return {"error": str(exc)}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
