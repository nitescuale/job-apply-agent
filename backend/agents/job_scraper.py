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


def _coerce_str(value: Any) -> str | None:
    """Best-effort extraction d'une string depuis un champ Schema.org.

    Schema.org permet à la plupart des champs string d'être renvoyés
    comme un object qualifié `{"@type": "Country", "name": "France"}` ou
    une liste `["FULL_TIME"]`. On déballe jusqu'à trouver une string
    propre. Sans ça, des appels comme `", ".join(...)` plantent en
    'sequence item 0: expected str instance, dict found' (cf. cas Indeed
    et plusieurs ATS Workday).
    """
    if isinstance(value, list) and value:
        value = value[0]
    if isinstance(value, dict):
        # Schema.org : `name` est la convention. Quelques sites mettent
        # le texte directement dans `value` ou `@id`.
        for key in ("name", "value", "@id"):
            v = value.get(key)
            if isinstance(v, str):
                s = v.strip()
                if s:
                    return s
        return None
    if isinstance(value, str):
        s = value.strip()
        return s or None
    return None


def _org_name(value: Any) -> str | None:
    return _coerce_str(value)


def _location(value: Any) -> str | None:
    if isinstance(value, list) and value:
        value = value[0]
    if isinstance(value, dict):
        addr = value.get("address")
        if isinstance(addr, list) and addr:
            addr = addr[0]
        if isinstance(addr, dict):
            # IMPORTANT : chacun de ces champs peut être string OU objet
            # {"@type": "Country", "name": "..."}. Sans _coerce_str on
            # passe un dict à `", ".join(...)` -> TypeError 500.
            parts = [
                _coerce_str(addr.get("addressLocality")),
                _coerce_str(addr.get("addressRegion")),
                _coerce_str(addr.get("addressCountry")),
            ]
            joined = ", ".join(p for p in parts if p)
            return joined or None
        if isinstance(addr, str):
            s = addr.strip()
            return s or None
        # Pas d'`address` exploitable — on tente le `name` du Place
        return _coerce_str(value.get("name"))
    return _coerce_str(value)


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
    """Mappe un JobPosting JSON-LD vers notre format de retour.

    Tous les champs string passent par `_coerce_str` parce que Schema.org
    autorise un object ou une liste là où on attend une string. Sans ça,
    `title=["Engineer"]` ou `employmentType={"@id":"FULL_TIME"}` fait
    planter le pipeline en aval (sérialisation, .join, etc.).
    """
    return {
        "title": _coerce_str(jp.get("title")),
        "company": _org_name(jp.get("hiringOrganization")),
        "location": _location(jp.get("jobLocation")),
        "employment_type": _coerce_str(jp.get("employmentType")),
        "posted_date": _coerce_str(jp.get("datePosted")),
        "valid_through": _coerce_str(jp.get("validThrough")),
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


# ──────────────────────────────────────────────────────────────────────────
# URL-based company inference (déterministe, post-LLM fallback)
# ──────────────────────────────────────────────────────────────────────────


# Brands dont le hostname ne suit pas le pattern "careers.<brand>.com". On
# matche en sous-chaîne sur le host normalisé.
_BRAND_HOSTS: tuple[tuple[str, str], ...] = (
    ("lifeattiktok.com", "TikTok"),
    ("careers.tiktok.com", "TikTok"),
    ("jobs.tiktok.com", "TikTok"),
    ("bytedance.com", "ByteDance"),
    ("jobs.bytedance.com", "ByteDance"),
    ("metacareers.com", "Meta"),
    ("careers.google.com", "Google"),
    ("amazon.jobs", "Amazon"),
    ("careers.microsoft.com", "Microsoft"),
    ("jobs.apple.com", "Apple"),
)

# Patterns d'ATS hébergés : `https://jobs.lever.co/<company>/...`, etc.
# Le premier segment de path après l'host donne le slug entreprise.
_PATH_ATS: tuple[tuple[str, str], ...] = (
    ("jobs.lever.co", "lever-path"),
    ("boards.greenhouse.io", "greenhouse-path"),
    ("job-boards.greenhouse.io", "greenhouse-path"),
    ("apply.workable.com", "workable-path"),
)

# Patterns d'ATS où le slug est dans le sous-domaine :
# `<company>.greenhouse.io`, `<company>.lever.co`, `<company>.workdayjobs.com`.
_SUBDOMAIN_ATS: tuple[str, ...] = (
    ".greenhouse.io",
    ".lever.co",
    ".workdayjobs.com",
    ".myworkdayjobs.com",
    ".recruitee.com",
    ".teamtailor.com",
    ".bamboohr.com",
    ".smartrecruiters.com",
    ".jobvite.com",
)


def _humanize_slug(slug: str) -> str:
    """`tik-tok` -> `Tik Tok`, `bnp-paribas` -> `Bnp Paribas`, `bnp` -> `BNP`.

    Cas acronyme : UN SEUL token court (≤ 4 chars) → upper-case (BNP, IBM,
    EY). Multi-token → Title-case par token, sinon on transformerait
    "tik-tok" en "TIK TOK".
    """
    parts = re.split(r"[-_]+", slug)
    parts = [p for p in parts if p]
    if not parts:
        return ""
    if len(parts) == 1 and len(parts[0]) <= 4:
        return parts[0].upper()
    return " ".join(p[:1].upper() + p[1:].lower() for p in parts)


def infer_company_from_url(url: str | None) -> str | None:
    """Devine le nom de l'entreprise depuis l'URL d'une offre, déterministe.

    Stratégie en cascade :
      1. brands hardcodés (lifeattiktok.com -> TikTok, metacareers.com -> Meta...)
      2. ATS hébergés sur un path (jobs.lever.co/<X>/ -> X)
      3. ATS hébergés sur un sous-domaine (<X>.greenhouse.io -> X)
      4. hostnames `careers.<X>.com` / `jobs.<X>.com` / `work.<X>.com` -> X
    Renvoie None si aucun pattern ne matche — on ne devine pas au hasard.
    """
    if not url:
        return None
    try:
        parsed = urlparse(url)
    except (ValueError, AttributeError):
        return None
    host = (parsed.hostname or "").lower().lstrip(".")
    path = parsed.path or ""
    if not host:
        return None

    # 1. brands hardcodés (matche en sous-chaîne pour tolérer www., m., etc.)
    for needle, brand in _BRAND_HOSTS:
        if needle in host:
            return brand

    # 2. ATS path-based : on prend le 1er segment du path
    for needle, _kind in _PATH_ATS:
        if host.endswith(needle):
            segments = [s for s in path.split("/") if s]
            if segments:
                return _humanize_slug(segments[0])

    # 3. ATS subdomain-based : le slug est avant le suffixe ATS
    for suffix in _SUBDOMAIN_ATS:
        if host.endswith(suffix):
            slug = host[: -len(suffix)]
            # Cas Workday : `docusign.wd1.myworkdayjobs.com` -> slug=
            # `docusign.wd1`. On prend le segment le plus à gauche (le
            # nom de l'organisation, le reste est l'instance Workday).
            if "." in slug:
                slug = slug.split(".")[0]
            # Pour `boards.greenhouse.io` (déjà géré en path), on aurait
            # `boards` ici — on skip si le slug ressemble à un mot générique
            if slug and slug not in {"boards", "jobs", "www", "apply", "careers"}:
                return _humanize_slug(slug)

    # 4. `careers.<X>.com`, `jobs.<X>.com`, etc.
    # On exclut le préfixe SI le reste est un host ATS connu, sinon on
    # transformerait `jobs.lever.co` en "Lever" alors qu'on n'a pas de
    # signal sur l'organisation cliente.
    _ATS_HOSTS_TAIL = (
        "lever.co", "greenhouse.io", "workable.com", "workdayjobs.com",
        "myworkdayjobs.com", "recruitee.com", "teamtailor.com",
        "bamboohr.com", "smartrecruiters.com", "jobvite.com",
    )
    for prefix in ("careers.", "jobs.", "work.", "join."):
        if host.startswith(prefix):
            rest = host[len(prefix):]
            if any(rest == t or rest.endswith("." + t) for t in _ATS_HOSTS_TAIL):
                # `jobs.lever.co` : pas de signal sans path → on s'abstient
                return None
            # On garde le 2e niveau du domaine : `tiktok.com` -> `tiktok`
            parts = rest.split(".")
            if len(parts) >= 2 and parts[0] not in {"www", ""}:
                return _humanize_slug(parts[0])

    return None


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
