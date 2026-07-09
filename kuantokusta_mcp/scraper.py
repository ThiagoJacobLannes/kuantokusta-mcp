"""HTML scraper for KuantoKusta.pt search results and product pages."""

from __future__ import annotations

import json
import re
import logging
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode, urljoin

import httpx
from bs4 import BeautifulSoup, Tag

log = logging.getLogger("kuantokusta_mcp")

BASE_URL = "https://www.kuantokusta.pt"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "pt-PT,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "Cache-Control": "max-age=0",
    "sec-ch-ua": '"Google Chrome";v="125", "Chromium";v="125", "Not.A/Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}

SORT_OPTIONS = {
    "popular": 1,
    "expensive": 2,
    "cheap": 3,
    "alphabetical": 4,
    "newest": 5,
}

# Special filters that map to their own URL params (not features.X)
_SPECIAL_FILTER_PARAMS = {
    "isMarketplace": "isMarketplace",
    "isHotDeal": "isHotDeal",
    "hasSplitPayment": "hasSplitPayment",
    "isFastDelivery": "isFastDelivery",
    "verao": "campaigns",  # value becomes "verao"
}


@dataclass
class Product:
    name: str
    url: str
    price_from: float | None
    store_count: int | None
    image_url: str | None
    badges: list[str] = field(default_factory=list)
    rating: float | None = None
    product_id: str | None = None


@dataclass
class FilterOption:
    value: str
    label: str
    count: int
    active: bool = False


@dataclass
class FilterGroup:
    field: str        # URL param key (e.g. "area", "brand", "cor_s")
    title: str        # Display name (e.g. "Áreas", "Marcas", "Cor")
    param: str        # Actual URL query param (e.g. "area", "brands", "features.cor_s")
    options: list[FilterOption] = field(default_factory=list)


@dataclass
class SearchResult:
    products: list[Product]
    total: int
    page: int
    available_filters: list[FilterGroup] = field(default_factory=list)


@dataclass
class StoreOffer:
    store_name: str
    price: float
    url: str
    in_stock: bool = True
    free_shipping: bool = False
    shipping_cost: float = 0.0
    total_price: float = 0.0

    def __post_init__(self) -> None:
        if self.total_price == 0.0:
            self.total_price = self.price


@dataclass
class ProductDetail:
    name: str
    url: str
    description: str | None
    image_url: str | None
    offers: list[StoreOffer]
    specs: dict[str, str] = field(default_factory=dict)
    rating: float | None = None
    review_count: int | None = None


def _make_client() -> httpx.Client:
    """Return an httpx Client with browser-like headers and a cookie jar."""
    return httpx.Client(headers=HEADERS, follow_redirects=True, timeout=20)


def _prime_session(client: httpx.Client) -> None:
    """Visit the homepage so the server sets cookies and sees a real browser session."""
    try:
        client.get(BASE_URL)
    except Exception:
        pass


def _get(client: httpx.Client, url: str) -> BeautifulSoup:
    resp = client.get(url, follow_redirects=True, timeout=20)
    if resp.status_code == 403:
        _prime_session(client)
        resp = client.get(url, follow_redirects=True, timeout=20)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def _extract_next_data(soup: BeautifulSoup) -> dict[str, Any] | None:
    tag = soup.find("script", id="__NEXT_DATA__")
    if tag and tag.string:
        try:
            return json.loads(tag.string)
        except json.JSONDecodeError:
            pass
    return None


def _parse_price(text: str) -> float | None:
    """Convert Portuguese price string like '1.234,56€' to float."""
    if not text:
        return None
    cleaned = re.sub(r"[^\d,.]", "", text).replace(".", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _field_to_param(field_name: str) -> str:
    """Map a filter group field name to the URL query parameter name.

    KuantoKusta conventions:
      area          → area
      family        → family
      brand         → brands          (site uses plural)
      price         → priceRange      (handled separately)
      special_*     → individual params
      anything else → features.<field>  (product-type-specific attributes)
    """
    mapping = {
        "area": "area",
        "family": "family",
        "brand": "brands",
        "price": "priceRange",
        "special_filters": "special_filters",
    }
    return mapping.get(field_name, f"features.{field_name}")


def _parse_filters_from_next_data(data: dict[str, Any]) -> list[FilterGroup]:
    """Extract dynamic filter groups from __NEXT_DATA__ basePage.filtersGroups.

    Each group has: title, field, filters[]{id, label, value, quantity, active}
    The URL param for each group is derived from the field name.
    """
    groups: list[FilterGroup] = []
    try:
        base_page = (
            data.get("props", {}).get("pageProps", {}).get("basePage", {})
        )
        raw_groups = base_page.get("filtersGroups") or []

        for rg in raw_groups:
            field_name = rg.get("field", "")
            title = rg.get("title", field_name)
            raw_filters = [f for f in (rg.get("filters") or []) if f.get("id")]

            if not raw_filters or field_name == "price":
                # Skip price (we use explicit price_min/max) and empty groups
                continue

            param = _field_to_param(field_name)

            options = [
                FilterOption(
                    value=f["value"],
                    label=f.get("label", f["value"]),
                    count=f.get("quantity", 0),
                    active=f.get("active", False),
                )
                for f in raw_filters
            ]
            groups.append(FilterGroup(field=field_name, title=title, param=param, options=options))

    except Exception as exc:
        log.debug("Filter parse failed: %s", exc)

    return groups


def _parse_products_from_next_data(data: dict[str, Any]) -> list[Product]:
    """Extract products from __NEXT_DATA__ at props.pageProps.basePage.data."""
    products: list[Product] = []
    try:
        base_page = data.get("props", {}).get("pageProps", {}).get("basePage", {})
        items = base_page.get("data")
        if not items or not isinstance(items, list):
            return []

        for item in items:
            name = item.get("name", "")
            pid = str(item.get("id", ""))
            rel_url = item.get("url", "")
            url = urljoin(BASE_URL, rel_url)
            price = item.get("priceMin")
            store_count = item.get("totalOffers")
            images = item.get("images") or []
            image_url = images[0] if images else None

            raw_badges = item.get("badges") or {}
            badges: list[str] = []
            if isinstance(raw_badges, dict):
                if raw_badges.get("goodDeal"):
                    badges.append("Boa compra")
                discount = raw_badges.get("discountPercentage") or raw_badges.get("discount")
                if discount:
                    badges.append(f"-{discount}%")
            elif isinstance(raw_badges, list):
                badges = [str(b) for b in raw_badges if b]

            rating_data = item.get("rating") or {}
            rating = rating_data.get("ratingCount")

            products.append(
                Product(
                    name=name,
                    url=url,
                    price_from=float(price) if price is not None else None,
                    store_count=int(store_count) if store_count is not None else None,
                    image_url=image_url,
                    badges=badges,
                    rating=float(rating) if rating else None,
                    product_id=pid,
                )
            )
    except Exception as exc:
        log.debug("__NEXT_DATA__ product parse failed: %s", exc)
    return products


def _parse_products_from_html(soup: BeautifulSoup) -> list[Product]:
    """Fallback: parse product cards directly from rendered HTML."""
    products: list[Product] = []
    seen_urls: set[str] = set()
    product_links = soup.find_all("a", href=re.compile(r"^/p/\d+/"))

    for link in product_links:
        assert isinstance(link, Tag)
        href = link.get("href", "")
        full_url = urljoin(BASE_URL, href)
        if full_url in seen_urls:
            continue
        seen_urls.add(full_url)

        m = re.match(r"/p/(\d+)/", href)
        pid = m.group(1) if m else None

        name_tag = link.find(["h2", "h3", "h4", "strong", "span"])
        name = name_tag.get_text(strip=True) if name_tag else link.get_text(strip=True)
        if not name or len(name) < 4:
            continue

        img = link.find("img")
        image_url = img.get("src") or img.get("data-src") if img else None

        card = link
        for _ in range(5):
            parent = card.parent
            if parent is None:
                break
            card = parent

        card_text = card.get_text(" ", strip=True)
        price_match = re.search(r"(?:desde\s*)?([\d]+[.,][\d]{2})\s*€", card_text)
        price = _parse_price(price_match.group(1)) if price_match else None
        store_match = re.search(r"em\s+(\d+)\s+loja", card_text)
        store_count = int(store_match.group(1)) if store_match else None
        badges = re.findall(r"(?:Boa compra|-\d+%[^\s]*|Hot Deal)", card_text)

        products.append(
            Product(
                name=name,
                url=full_url,
                price_from=price,
                store_count=store_count,
                image_url=image_url,
                badges=badges,
                product_id=pid,
            )
        )

    return products


def _parse_offers_from_next_data(
    data: dict[str, Any], product_id: str | None = None
) -> list[StoreOffer]:
    """Extract store offers from product detail __NEXT_DATA__.

    Path: props.pageProps.basePage.product.offers
    Each offer: storeName, price, totalPrice, storeSlug, shipping, filters
    """
    offers: list[StoreOffer] = []
    try:
        product = (
            data.get("props", {})
            .get("pageProps", {})
            .get("basePage", {})
            .get("product", {})
        )
        raw_offers = product.get("offers") or []
        for o in raw_offers:
            store = o.get("storeName", "")
            price = o.get("price")
            total_price = o.get("totalPrice") or price
            slug = o.get("storeSlug", "")
            in_stock = product.get("tags", {}).get("inStock", True)
            free_shipping = (o.get("filters") or {}).get("isFreeShipping", False)
            shipping_cost = (o.get("shipping") or {}).get("minimumPrice", 0)

            pid = product.get("oldProductId") or product_id or str(product.get("id", ""))
            buy_url = (
                f"{BASE_URL}/clique_premium.php?id={pid}&loja={slug}"
                if pid and slug else ""
            )

            if store and price is not None:
                offers.append(
                    StoreOffer(
                        store_name=store,
                        price=float(price),
                        url=buy_url,
                        in_stock=bool(in_stock),
                        free_shipping=free_shipping,
                        shipping_cost=float(shipping_cost) if shipping_cost else 0.0,
                        total_price=float(total_price) if total_price else float(price),
                    )
                )
    except Exception as exc:
        log.debug("__NEXT_DATA__ offers parse failed: %s", exc)
    return sorted(offers, key=lambda o: o.total_price)


def _parse_offers_from_html(soup: BeautifulSoup) -> list[StoreOffer]:
    """Fallback: parse store offers from rendered HTML."""
    offers: list[StoreOffer] = []
    buy_links = soup.find_all("a", href=re.compile(r"https?://"))
    seen: set[str] = set()

    for link in buy_links:
        assert isinstance(link, Tag)
        href = str(link.get("href", ""))
        if not href or href in seen or "kuantokusta" in href:
            continue

        container = link
        for _ in range(4):
            p = container.parent
            if p is None:
                break
            container = p

        text = container.get_text(" ", strip=True)
        price_match = re.search(r"([\d]+[.,][\d]{2})\s*€", text)
        if not price_match:
            continue

        price = _parse_price(price_match.group(1))
        if not price:
            continue

        store_img = container.find("img")
        store_name = store_img.get("alt", "") if store_img else ""
        if not store_name:
            segments = [s.strip() for s in text.split() if s.strip()]
            store_name = segments[0] if segments else "Loja"

        seen.add(href)
        offers.append(StoreOffer(store_name=store_name, price=price, url=href))

    return sorted(offers, key=lambda o: o.price)


def search_products(
    query: str,
    area: str | None = None,
    family: str | None = None,
    sort: str = "cheap",
    price_min: int | None = None,
    price_max: int | None = None,
    brands: list[str] | None = None,
    features: dict[str, str] | None = None,
    page: int = 1,
) -> SearchResult:
    """Search KuantoKusta and return products + available filters.

    Dynamic product-type filters (color, capacity, energy class, etc.) are
    passed as `features={"cor_s": "inox", "tipologia_s": "combinados"}`.
    The available values for each filter come from the previous call's
    `available_filters` — so the first call should omit features to discover
    what options exist.
    """
    params: dict[str, Any] = {"q": query}
    params["sort"] = SORT_OPTIONS.get(sort, 3)
    if area:
        params["area"] = area
    if family:
        params["family"] = family
    if price_min is not None or price_max is not None:
        lo = price_min or 0
        hi = price_max or 99999
        params["priceRange"] = f"{lo}_{hi}"
    if brands:
        params["brands"] = ",".join(brands)
    if features:
        for feat_field, feat_value in features.items():
            params[f"features.{feat_field}"] = feat_value
    if page > 1:
        params["pag"] = page

    url = f"{BASE_URL}/search?{urlencode(params, encoding='utf-8')}"
    log.info("Fetching search: %s", url)

    with _make_client() as client:
        soup = _get(client, url)

    next_data = _extract_next_data(soup)

    products: list[Product] = []
    total = 0
    available_filters: list[FilterGroup] = []

    if next_data:
        products = _parse_products_from_next_data(next_data)
        base_page = next_data.get("props", {}).get("pageProps", {}).get("basePage", {})
        total = base_page.get("total", 0)
        available_filters = _parse_filters_from_next_data(next_data)

    if not products:
        products = _parse_products_from_html(soup)

    log.info("Parsed %d products (total=%d, filters=%d)", len(products), total, len(available_filters))
    return SearchResult(
        products=products,
        total=total,
        page=page,
        available_filters=available_filters,
    )


def get_product_detail(product_url: str) -> ProductDetail:
    if not product_url.startswith("http"):
        product_url = urljoin(BASE_URL, product_url)

    log.info("Fetching product: %s", product_url)

    with _make_client() as client:
        soup = _get(client, product_url)

    next_data = _extract_next_data(soup)

    h1 = soup.find("h1")
    name = h1.get_text(strip=True) if h1 else ""
    og_image = soup.find("meta", property="og:image")
    image_url = og_image.get("content") if og_image else None
    og_desc = soup.find("meta", property="og:description")
    description = og_desc.get("content") if og_desc else None

    product_data: dict[str, Any] = {}
    if next_data:
        product_data = (
            next_data.get("props", {})
            .get("pageProps", {})
            .get("basePage", {})
            .get("product", {})
        )

    if product_data.get("name"):
        name = product_data["name"]
    if product_data.get("imageThumbnail"):
        image_url = product_data["imageThumbnail"]
    if product_data.get("shortDescription"):
        description = product_data["shortDescription"]
    if next_data and not description:
        feat_desc = (
            next_data.get("props", {})
            .get("pageProps", {})
            .get("features", {})
            .get("description") or ""
        )
        if feat_desc:
            desc_soup = BeautifulSoup(feat_desc, "html.parser")
            description = desc_soup.get_text(" ", strip=True)[:500]

    pid = str(product_data.get("oldProductId") or product_data.get("id") or "")
    offers: list[StoreOffer] = []
    if next_data:
        offers = _parse_offers_from_next_data(next_data, pid)
    if not offers:
        offers = _parse_offers_from_html(soup)

    specs: dict[str, str] = {}
    if next_data:
        features_data = (
            next_data.get("props", {})
            .get("pageProps", {})
            .get("features") or {}
        )
        if isinstance(features_data, dict):
            for group in features_data.get("featuresGroup") or []:
                for feat in group.get("features") or []:
                    if feat.get("name") and feat.get("value"):
                        specs[feat["name"]] = feat["value"]

    if not specs:
        for dl in soup.find_all("dl"):
            for dt, dd in zip(dl.find_all("dt"), dl.find_all("dd")):
                specs[dt.get_text(strip=True)] = dd.get_text(strip=True)
    if not specs:
        for row in soup.find_all("tr"):
            cells = row.find_all(["th", "td"])
            if len(cells) == 2:
                specs[cells[0].get_text(strip=True)] = cells[1].get_text(strip=True)

    return ProductDetail(
        name=name,
        url=product_url,
        description=description,
        image_url=image_url,
        offers=offers,
        specs=specs,
    )
