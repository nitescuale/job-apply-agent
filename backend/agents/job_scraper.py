"""Job scraper — extrait les données pratiques d'une offre depuis le HTML via Scrapling."""
from __future__ import annotations

import html
import json
import logging
import re
from typing import Any
from urllib.parse import urlparse

from scrapling import Selector

logger = logging.getLogger(__name__)


def _first(sel: Selector, query: str) -> str | None:
    """Retourne le premier match d'un sélecteur CSS (avec ::text ou ::attr) ou None."""
    matches = sel.css(query)
    return str(matches[0]) if matches else None


def _parse_jsonld(page: Selector) -> dict | None:
    """Cherche un objet JobPosting dans les balises JSON-LD de la page."""
    scripts = page.css('script[type="application/ld+json"]::text')
    for script in scripts:
        try:
            data = json.loads(str(script))
        except (json.JSONDecodeError, TypeError):
            continue

        candidates: list = []
        if isinstance(data, list):
            candidates = data
        elif isinstance(data, dict):
            if data.get("@type") == "JobPosting":
                return data
            graph = data.get("@graph")
            if isinstance(graph, list):
                candidates = graph

        for c in candidates:
            if isinstance(c, dict) and c.get("@type") == "JobPosting":
                return c
    return None


def _strip_html(s: Any) -> str | None:
    """Décode les entités HTML (gère le double-encodage) puis retire les balises."""
    if not s:
        return None
    text = str(s)
    # Boucle pour gérer le double-encodage type "&amp;lt;strong&amp;gt;"
    for _ in range(3):
        decoded = html.unescape(text)
        if decoded == text:
            break
        text = decoded
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def _org_name(value: Any) -> str | None:
    if isinstance(value, dict):
        return value.get("name")
    return value if isinstance(value, str) else None


def _location(value: Any) -> str | None:
    if isinstance(value, list) and value:
        value = value[0]
    if isinstance(value, dict):
        addr = value.get("address")
        if isinstance(addr, dict):
            parts = [addr.get("addressLocality"), addr.get("addressRegion"), addr.get("addressCountry")]
            joined = ", ".join(p for p in parts if p)
            return joined or None
        return value.get("name")
    return value if isinstance(value, str) else None


def _salary(value: Any) -> str | None:
    if not value:
        return None
    if isinstance(value, dict):
        currency = value.get("currency")
        v = value.get("value", value)
        if isinstance(v, dict):
            lo, hi = v.get("minValue"), v.get("maxValue")
            unit = v.get("unitText")
            if lo and hi:
                return f"{lo}-{hi} {currency or ''} ({unit or 'period'})".strip()
            single = v.get("value")
            if single:
                return f"{single} {currency or ''}".strip()
        return str(v)
    return str(value)


def _from_jsonld(jp: dict) -> dict:
    """Mappe un JobPosting JSON-LD vers notre format de retour."""
    return {
        "title": jp.get("title"),
        "company": _org_name(jp.get("hiringOrganization")),
        "location": _location(jp.get("jobLocation")),
        "employment_type": jp.get("employmentType"),
        "posted_date": jp.get("datePosted"),
        "valid_through": jp.get("validThrough"),
        "salary": _salary(jp.get("baseSalary")),
        "remote": jp.get("jobLocationType") == "TELECOMMUTE",
        "description": _strip_html(jp.get("description")),
    }


def _from_meta(page: Selector) -> dict:
    """Lit les balises Open Graph / meta standard."""
    def meta(name: str) -> str | None:
        return (
            _first(page, f'meta[property="og:{name}"]::attr(content)')
            or _first(page, f'meta[name="{name}"]::attr(content)')
        )

    return {"title": meta("title"), "description": meta("description")}


def _site_specific(page: Selector, url: str) -> dict:
    """Sélecteurs spécifiques par site pour combler ce qui manque."""
    host = (urlparse(url).hostname or "").lower()
    out: dict[str, Any] = {}

    if "linkedin.com" in host:
        out["title"] = _first(page, ".top-card-layout__title::text") or _first(
            page, ".jobs-unified-top-card__job-title::text"
        )
        out["company"] = _first(page, ".topcard__org-name-link::text") or _first(
            page, ".jobs-unified-top-card__company-name a::text"
        )
        out["location"] = _first(page, ".topcard__flavor--bullet::text") or _first(
            page, ".jobs-unified-top-card__bullet::text"
        )
        out["source"] = "linkedin"
    elif "hellowork.com" in host:
        out["title"] = _first(page, 'h1[data-cy="jobTitle"]::text')
        out["company"] = _first(page, '[data-cy="companyName"]::text')
        out["location"] = _first(page, '[data-cy="jobLocation"]::text')
        out["source"] = "hellowork"
    elif "indeed." in host:
        out["title"] = _first(page, "h1.jobsearch-JobInfoHeader-title::text")
        out["company"] = _first(page, '[data-testid="inlineHeader-companyName"] a::text')
        out["location"] = _first(page, '[data-testid="job-location"]::text')
        out["source"] = "indeed"
    elif "welcometothejungle.com" in host:
        out["title"] = _first(page, '[data-testid="job-title"]::text') or _first(page, "h1::text")
        out["company"] = _first(page, '[data-testid="job-company"]::text')
        out["source"] = "wttj"
    elif "jobteaser.com" in host:
        out["title"] = _first(page, "h1::text")
        out["source"] = "jobteaser"

    return {k: v for k, v in out.items() if v}


def scrape_job(html: str, url: str = "") -> dict[str, Any]:
    """Extrait les infos pratiques d'une offre d'emploi depuis le HTML rendu.

    Stratégie en cascade : JSON-LD JobPosting (universel) → meta Open Graph →
    sélecteurs spécifiques au site → fallback texte.

    Args:
        html: HTML brut de la page (typiquement document.documentElement.outerHTML)
        url: URL de la page d'origine, utilisée pour le routing site-specific

    Returns:
        dict avec champs : url, title, company, location, employment_type,
        posted_date, valid_through, salary, remote, description, source.
        Seuls les champs trouvés sont présents.

    Raises:
        ValueError: si html est vide.
    """
    if not html or not html.strip():
        raise ValueError("html cannot be empty")

    page = Selector(content=html, url=url)
    result: dict[str, Any] = {"url": url} if url else {}

    # 1. JSON-LD (le plus fiable, présent sur la majorité des sites SEO-friendly)
    jp = _parse_jsonld(page)
    if jp:
        logger.info("scraper: JobPosting JSON-LD trouvé")
        result.update({k: v for k, v in _from_jsonld(jp).items() if v is not None})
        result["source"] = "json-ld"

    # 2. Meta Open Graph pour combler les manques
    for k, v in _from_meta(page).items():
        if v and not result.get(k):
            result[k] = v

    # 3. Sélecteurs spécifiques au site
    for k, v in _site_specific(page, url).items():
        if not result.get(k):
            result[k] = v

    # 4. Fallback titre depuis <title>
    if not result.get("title"):
        result["title"] = _first(page, "title::text")

    # 5. Fallback description : texte visible (nav/footer/scripts/styles ignorés)
    if not result.get("description"):
        body_matches = page.css("body")
        if body_matches:
            text = body_matches[0].get_all_text(
                separator=" ",
                strip=True,
                ignore_tags=("script", "style", "nav", "footer", "header", "aside"),
            )
            cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
            if cleaned:
                # Marge large : le texte "Voir plus" est dans le DOM mais peut être
                # loin dans la page ; la moulinette LLM filtre le bruit ensuite.
                result["description"] = cleaned[:15000]

    if "source" not in result:
        result["source"] = "mixed"

    logger.info(
        "scraper: title=%r company=%r location=%r source=%s",
        result.get("title"),
        result.get("company"),
        result.get("location"),
        result.get("source"),
    )
    return result
