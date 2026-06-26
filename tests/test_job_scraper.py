"""Tests pour backend/agents/job_scraper.py."""
import pytest

from backend.agents.job_scraper import (
    _coerce_str,
    _from_jsonld,
    _humanize_slug,
    _location,
    _org_name,
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


# ──────────────────────────────────────────────────────────────────────────
# _coerce_str + Schema.org coercion (régression : 500
# "sequence item 0: expected str instance, dict found")
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Paris", "Paris"),
        ("  Paris  ", "Paris"),
        ("", None),
        (None, None),
        ([], None),
        (["Engineer"], "Engineer"),
        (["Engineer", "Other"], "Engineer"),  # premier élément
        ({"@type": "Country", "name": "France"}, "France"),
        ({"name": "Acme"}, "Acme"),
        ({"value": "Senior"}, "Senior"),
        ({"@id": "FULL_TIME"}, "FULL_TIME"),
        ({}, None),
        ({"name": ""}, None),  # name vide → None plutôt que ""
    ],
)
def test_coerce_str_extracts_string_from_various_shapes(raw, expected):
    assert _coerce_str(raw) == expected


def test_org_name_handles_dict_organization():
    """Cas Schema.org standard."""
    assert _org_name({"@type": "Organization", "name": "ACME"}) == "ACME"


def test_org_name_handles_string():
    assert _org_name("ACME") == "ACME"


def test_org_name_handles_list():
    """Certains sites mettent l'org en liste."""
    assert _org_name([{"name": "ACME"}, {"name": "Other"}]) == "ACME"


def test_location_handles_dict_country_RÉGRESSION():
    """Régression du bug 500 'sequence item 0: expected str instance, dict found'.

    Plusieurs ATS (Workday, Indeed enrichi, certaines pages SAP
    SuccessFactors) sérialisent addressCountry comme un objet
    `{"@type": "Country", "name": "France"}` au lieu d'une string.
    Sans coerce, `", ".join(...)` plante en TypeError → 500 côté backend.
    """
    job_location = {
        "@type": "Place",
        "address": {
            "@type": "PostalAddress",
            "addressLocality": "Paris",
            "addressRegion": {"@type": "Region", "name": "Île-de-France"},
            "addressCountry": {"@type": "Country", "name": "France"},
        },
    }
    assert _location(job_location) == "Paris, Île-de-France, France"


def test_location_handles_address_as_list():
    """Schema.org : l'`address` peut être enveloppée dans une liste."""
    job_location = {
        "@type": "Place",
        "address": [
            {
                "@type": "PostalAddress",
                "addressLocality": "Paris",
                "addressCountry": "France",
            }
        ],
    }
    assert _location(job_location) == "Paris, France"


def test_location_handles_address_as_plain_string():
    job_location = {"@type": "Place", "address": "Paris, France"}
    assert _location(job_location) == "Paris, France"


def test_location_falls_back_to_place_name():
    """Pas d'address → on tente le `name` du Place."""
    assert _location({"@type": "Place", "name": "Remote — Worldwide"}) == "Remote — Worldwide"


def test_location_handles_pure_string():
    assert _location("Paris, France") == "Paris, France"


def test_location_handles_list_of_places():
    job_location = [
        {"@type": "Place", "address": {"addressLocality": "Paris"}},
        {"@type": "Place", "address": {"addressLocality": "London"}},
    ]
    assert _location(job_location) == "Paris"


def test_from_jsonld_does_not_crash_on_dict_employment_type_RÉGRESSION():
    """Le JSON-LD peut mettre employmentType en object/list — sans coerce
    on stockait un dict dans le résultat puis le sérialiseur JSON ou un
    .join en aval plantait."""
    jp = {
        "@type": "JobPosting",
        "title": "Engineer",
        "hiringOrganization": {"@type": "Organization", "name": "ACME"},
        "jobLocation": {
            "@type": "Place",
            "address": {
                "addressLocality": "Paris",
                "addressCountry": {"@type": "Country", "name": "France"},
            },
        },
        "employmentType": ["FULL_TIME"],
        "datePosted": "2026-04-01",
        "validThrough": "2026-06-01",
        "description": "We need a great engineer.",
    }
    out = _from_jsonld(jp)
    assert out["title"] == "Engineer"
    assert out["company"] == "ACME"
    assert out["location"] == "Paris, France"
    assert out["employment_type"] == "FULL_TIME"


def test_scrape_jsonld_dict_country_does_not_500_RÉGRESSION():
    """Test end-to-end : scrape_job ne doit pas raise quand
    addressCountry est un dict (cas reporté en prod sur certains sites)."""
    html = """
    <!doctype html>
    <html><head><title>Engineer</title>
    <script type="application/ld+json">
    {
      "@context": "https://schema.org",
      "@type": "JobPosting",
      "title": "Senior Engineer",
      "hiringOrganization": {"@type": "Organization", "name": "BigCorp"},
      "jobLocation": {
        "@type": "Place",
        "address": {
          "@type": "PostalAddress",
          "addressLocality": "Paris",
          "addressCountry": {"@type": "Country", "name": "France"}
        }
      }
    }
    </script>
    </head><body>body</body></html>
    """
    r = scrape_job(html, url="https://example.com/job/1")
    assert r["title"] == "Senior Engineer"
    assert r["company"] == "BigCorp"
    assert r["location"] == "Paris, France"
