"""Tests pour backend/agents/job_scraper.py."""
import pytest

from backend.agents.job_scraper import (
    _humanize_slug,
    infer_company_from_url,
    scrape_job,
)


JSONLD_HTML = """
<!doctype html>
<html><head>
<title>Senior Python Developer - ACME</title>
<meta property="og:description" content="Join our backend team.">
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "JobPosting",
  "title": "Senior Python Developer",
  "datePosted": "2026-04-01",
  "validThrough": "2026-06-01",
  "employmentType": "FULL_TIME",
  "hiringOrganization": {"@type": "Organization", "name": "ACME"},
  "jobLocation": {
    "@type": "Place",
    "address": {
      "@type": "PostalAddress",
      "addressLocality": "Paris",
      "addressCountry": "FR"
    }
  },
  "baseSalary": {
    "@type": "MonetaryAmount",
    "currency": "EUR",
    "value": {"@type": "QuantitativeValue", "minValue": 55000, "maxValue": 70000, "unitText": "YEAR"}
  },
  "jobLocationType": "TELECOMMUTE",
  "description": "<p>Build APIs with FastAPI</p>"
}
</script>
</head><body><h1>Senior Python Developer</h1><p>Build cool stuff.</p></body></html>
"""


def test_scrape_jsonld_extracts_all_fields():
    r = scrape_job(JSONLD_HTML, url="https://example.com/jobs/1")
    assert r["title"] == "Senior Python Developer"
    assert r["company"] == "ACME"
    assert "Paris" in r["location"]
    assert r["employment_type"] == "FULL_TIME"
    assert r["posted_date"] == "2026-04-01"
    assert r["valid_through"] == "2026-06-01"
    assert "55000" in r["salary"] and "70000" in r["salary"]
    assert r["remote"] is True
    assert "FastAPI" in r["description"]
    assert "<" not in r["description"]  # HTML stripped
    assert r["source"] == "json-ld"
    assert r["url"] == "https://example.com/jobs/1"


def test_scrape_falls_back_to_meta_when_no_jsonld():
    html = """
    <html><head>
      <title>Fallback Title</title>
      <meta property="og:title" content="Better Title">
      <meta property="og:description" content="Cool job">
    </head><body>content</body></html>
    """
    r = scrape_job(html, url="https://unknown-site.com/job")
    assert r["title"] == "Better Title"
    assert r["description"] == "Cool job"


def test_scrape_falls_back_to_title_tag_when_no_meta():
    html = "<html><head><title>Plain Title</title></head><body>text body content</body></html>"
    r = scrape_job(html, url="https://random.com/")
    assert r["title"] == "Plain Title"
    assert "text body content" in (r.get("description") or "")


def test_scrape_jsonld_inside_graph():
    html = """
    <html><head><script type="application/ld+json">
    {"@context": "https://schema.org", "@graph": [
      {"@type": "WebPage", "name": "ignore me"},
      {"@type": "JobPosting", "title": "Found via graph",
       "hiringOrganization": {"name": "GraphCo"}}
    ]}
    </script></head><body></body></html>
    """
    r = scrape_job(html, url="https://x.com/")
    assert r["title"] == "Found via graph"
    assert r["company"] == "GraphCo"


def test_scrape_empty_html_raises():
    with pytest.raises(ValueError):
        scrape_job("", url="https://x.com")


def test_scrape_whitespace_html_raises():
    with pytest.raises(ValueError):
        scrape_job("   \n\t  ", url="https://x.com")


def test_scrape_decodes_html_entities_in_description():
    """Le JSON-LD peut contenir une description HTML-encodée, parfois double-encodée."""
    html = """
    <html><head><script type="application/ld+json">
    {
      "@context": "https://schema.org",
      "@type": "JobPosting",
      "title": "Dev",
      "description": "Allianz recherche un &lt;strong&gt;Data Scientist&lt;/strong&gt; pour le service R&amp;amp;D."
    }
    </script></head><body></body></html>
    """
    r = scrape_job(html, url="https://x.com/")
    desc = r["description"]
    assert "<" not in desc and ">" not in desc
    assert "&lt;" not in desc and "&amp;" not in desc
    assert "Data Scientist" in desc
    assert "R&D" in desc  # &amp;amp; → &amp; → &


def test_scrape_malformed_jsonld_does_not_crash():
    html = """
    <html><head>
      <title>Page Title</title>
      <script type="application/ld+json">{ this is not valid json }</script>
    </head><body>body</body></html>
    """
    r = scrape_job(html, url="https://x.com/")
    assert r["title"] == "Page Title"


# ──────────────────────────────────────────────────────────────────────────
# infer_company_from_url — fallback déterministe quand le LLM cale
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url,expected",
    [
        # Brands hardcodés
        ("https://lifeattiktok.com/positions/12345", "TikTok"),
        ("https://www.lifeattiktok.com/job/abc", "TikTok"),
        ("https://careers.tiktok.com/position/42", "TikTok"),
        ("https://jobs.bytedance.com/en/job/xyz", "ByteDance"),
        ("https://www.metacareers.com/jobs/123", "Meta"),
        ("https://careers.google.com/jobs/results/123", "Google"),
        ("https://www.amazon.jobs/en/jobs/456", "Amazon"),
        # ATS path-based : segment de path = entreprise
        ("https://jobs.lever.co/openai/some-role", "Openai"),
        ("https://boards.greenhouse.io/airbnb/jobs/4123", "Airbnb"),
        # ATS subdomain-based : sous-domaine = entreprise
        ("https://stripe.greenhouse.io/job/1234", "Stripe"),
        ("https://figma.lever.co/positions/xyz", "Figma"),
        ("https://docusign.wd1.myworkdayjobs.com/External", "Docusign"),
        # Patterns careers./jobs.<X>.com
        ("https://careers.shopify.com/job/123", "Shopify"),
        ("https://jobs.netflix.com/jobs/456", "Netflix"),
        ("https://work.deezer.com/positions", "Deezer"),
        # Acronymes courts en MAJ préservés (≤ 4 chars par token)
        ("https://careers.bnp.com/job/x", "BNP"),
        ("https://careers.ibm.com/job/x", "IBM"),
    ],
)
def test_infer_company_from_url_returns_brand(url, expected):
    assert infer_company_from_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        # URL générique : pas de fallback → None (on ne devine pas au hasard)
        "https://example.com/jobs/123",
        "https://www.linkedin.com/jobs/view/456",
        "https://en.wikipedia.org/wiki/anything",
        "",
        None,
        "not even a url",
    ],
)
def test_infer_company_from_url_returns_none_when_unclear(url):
    assert infer_company_from_url(url) is None


def test_infer_company_skips_generic_subdomain_slugs():
    """`boards.greenhouse.io` est géré par le path, pas le subdomain →
    on ne doit pas renvoyer 'Boards' depuis le sous-domaine."""
    # Sans path utile, on retombe sur None plutôt que 'Boards'.
    assert infer_company_from_url("https://boards.greenhouse.io/") is None
    assert infer_company_from_url("https://jobs.lever.co/") is None


def test_humanize_slug_handles_acronyms_and_kebab():
    assert _humanize_slug("openai") == "Openai"
    assert _humanize_slug("ey") == "EY"
    assert _humanize_slug("ibm") == "IBM"
    assert _humanize_slug("bnp-paribas") == "Bnp Paribas"
    assert _humanize_slug("tik-tok") == "Tik Tok"
    assert _humanize_slug("") == ""
