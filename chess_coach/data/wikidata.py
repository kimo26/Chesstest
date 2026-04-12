"""Wikidata + Wikipedia sources for opening descriptions.

The entire opening-coaching corpus is grounded in real Wikipedia text rather
than LLM-invented prose. The pipeline is:

1. Hit the Wikidata SPARQL endpoint (https://query.wikidata.org/sparql) for
   every item that is an instance of "chess opening" (Q103944) and has an
   English-language Wikipedia article. We collect the Wikidata Q-id, the
   item label, the ECO code (property P1528 / P1363 depending on model
   revision — we try both), and the sitelink title for the English Wikipedia.

2. For each article title, hit the MediaWiki Extracts API to pull a plain
   text extract of the page (``prop=extracts&explaintext=true``).

3. Upsert a row into ``wiki_openings`` keyed by Q-id. A later step in the
   RAG generator matches these rows to ``opening_nodes`` via ECO code and
   name, and uses the ``extract`` column as the raw source material for
   chunking.

We never ask the LLM to write the description itself; the LLM is only used
to extract structured metadata (themes, plans, key squares) from the
Wikipedia text, which is a closed summarisation task.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx

from .. import db
from ..config import settings


WIKIDATA_SPARQL_URL = "https://query.wikidata.org/sparql"
WIKIPEDIA_API_URL = "https://en.wikipedia.org/w/api.php"

# Wikidata entity: "chess opening" = Q103944
# Property for ECO code: P1528 (preferred) — fall back to P1363 if absent.
SPARQL_QUERY = """
SELECT ?item ?itemLabel ?eco ?ecoAlt ?article ?articleTitle WHERE {
  ?item wdt:P31/wdt:P279* wd:Q103944 .
  OPTIONAL { ?item wdt:P1528 ?eco . }
  OPTIONAL { ?item wdt:P1363 ?ecoAlt . }
  ?article schema:about ?item ;
           schema:isPartOf <https://en.wikipedia.org/> ;
           schema:name ?articleTitle .
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}
"""


@dataclass
class WikiOpening:
    qid: str
    title: str
    label: str
    eco_code: str | None
    wikipedia_url: str
    extract: str = ""
    revision_id: int | None = None


# ----------------------------------------------------------------------------
# SPARQL
# ----------------------------------------------------------------------------

async def fetch_chess_openings_from_wikidata() -> list[WikiOpening]:
    """Run the SPARQL query and return a list of openings with article titles."""
    headers = {
        "Accept": "application/sparql-results+json",
        "User-Agent": settings.user_agent,
    }
    async with httpx.AsyncClient(timeout=60.0, headers=headers) as client:
        resp = await client.get(WIKIDATA_SPARQL_URL, params={"query": SPARQL_QUERY, "format": "json"})
        resp.raise_for_status()
        data = resp.json()

    out: dict[str, WikiOpening] = {}
    for row in data["results"]["bindings"]:
        item_uri = row["item"]["value"]
        qid = item_uri.rsplit("/", 1)[-1]
        label = row.get("itemLabel", {}).get("value", "")
        article_title = row.get("articleTitle", {}).get("value", label)
        article_url = row.get("article", {}).get("value", "")
        eco = (
            row.get("eco", {}).get("value")
            or row.get("ecoAlt", {}).get("value")
            or None
        )
        # Merge duplicates — SPARQL can return multiple rows per item if
        # both ECO properties are present.
        existing = out.get(qid)
        if existing is None:
            out[qid] = WikiOpening(
                qid=qid,
                title=article_title,
                label=label,
                eco_code=eco,
                wikipedia_url=article_url,
            )
        elif eco and not existing.eco_code:
            existing.eco_code = eco
    return list(out.values())


# ----------------------------------------------------------------------------
# Wikipedia extracts
# ----------------------------------------------------------------------------

async def fetch_wikipedia_extracts(
    titles: list[str],
    *,
    batch_size: int = 20,
) -> dict[str, tuple[str, int | None]]:
    """Batch-fetch plain-text extracts for a list of article titles.

    Returns a dict ``{title: (extract, revision_id)}``.
    """
    results: dict[str, tuple[str, int | None]] = {}
    headers = {"User-Agent": settings.user_agent}
    async with httpx.AsyncClient(timeout=60.0, headers=headers) as client:
        for i in range(0, len(titles), batch_size):
            batch = titles[i : i + batch_size]
            params = {
                "action": "query",
                "format": "json",
                "prop": "extracts|revisions",
                "explaintext": "true",
                "exsectionformat": "plain",
                "rvprop": "ids",
                "titles": "|".join(batch),
                "redirects": "1",
            }
            resp = await client.get(WIKIPEDIA_API_URL, params=params)
            resp.raise_for_status()
            data = resp.json()

            pages = data.get("query", {}).get("pages", {}) or {}
            normalized = {
                n["from"]: n["to"]
                for n in data.get("query", {}).get("normalized", []) or []
            }
            redirects = {
                r["from"]: r["to"]
                for r in data.get("query", {}).get("redirects", []) or []
            }

            for page in pages.values():
                canonical = page.get("title", "")
                extract = page.get("extract", "") or ""
                rev_id = None
                revs = page.get("revisions") or []
                if revs:
                    rev_id = revs[0].get("revid")
                # Reverse-map the canonical title to every requested title
                # that eventually resolved to it.
                for requested in batch:
                    resolved = normalized.get(requested, requested)
                    resolved = redirects.get(resolved, resolved)
                    if resolved == canonical and extract:
                        results[requested] = (extract, rev_id)

            # Be polite: MediaWiki rate-limits heavy clients.
            await asyncio.sleep(0.5)

    return results


# ----------------------------------------------------------------------------
# Persistence
# ----------------------------------------------------------------------------

async def upsert_wiki_openings(rows: list[WikiOpening]) -> int:
    """Insert or update rows in ``wiki_openings``. Only rows with a non-empty
    extract are written."""
    n = 0
    async with db.acquire() as conn:
        async with conn.transaction():
            for r in rows:
                if not r.extract:
                    continue
                await conn.execute(
                    """
                    INSERT INTO wiki_openings (
                        wikidata_qid, title, eco_code, aliases,
                        wikipedia_url, extract, revision_id, fetched_at
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,NOW())
                    ON CONFLICT (wikidata_qid) DO UPDATE SET
                        title         = EXCLUDED.title,
                        eco_code      = EXCLUDED.eco_code,
                        aliases       = EXCLUDED.aliases,
                        wikipedia_url = EXCLUDED.wikipedia_url,
                        extract       = EXCLUDED.extract,
                        revision_id   = EXCLUDED.revision_id,
                        fetched_at    = NOW()
                    """,
                    r.qid,
                    r.title,
                    r.eco_code,
                    [r.label] if r.label and r.label != r.title else [],
                    r.wikipedia_url,
                    r.extract,
                    r.revision_id,
                )
                n += 1
    return n


async def refresh_wiki_openings() -> int:
    """Full pipeline: SPARQL -> extracts -> upsert. Returns rows written."""
    openings = await fetch_chess_openings_from_wikidata()
    if not openings:
        return 0
    titles = [o.title for o in openings]
    extracts = await fetch_wikipedia_extracts(titles)
    for o in openings:
        extract, rev = extracts.get(o.title, ("", None))
        o.extract = extract
        o.revision_id = rev
    return await upsert_wiki_openings([o for o in openings if o.extract])


# ----------------------------------------------------------------------------
# Lookup helpers used by the RAG generator
# ----------------------------------------------------------------------------

async def find_for_node(node: dict[str, Any]) -> dict[str, Any] | None:
    """Match an ``opening_nodes`` row to the best ``wiki_openings`` row.

    Prefers: exact ECO match + name similarity, then ECO alone, then fuzzy
    name match via pg_trgm.
    """
    name = (node.get("opening_name") or "").strip()
    eco = node.get("eco_code")

    if name:
        row = await db.fetchrow(
            """
            SELECT id, title, extract, wikipedia_url, eco_code, revision_id
            FROM wiki_openings
            WHERE ($1::text IS NULL OR eco_code = $1)
              AND similarity(title, $2) > 0.4
            ORDER BY (eco_code = $1)::int DESC,
                     similarity(title, $2) DESC
            LIMIT 1
            """,
            eco,
            name,
        )
        if row:
            return dict(row)

    if eco:
        row = await db.fetchrow(
            "SELECT id, title, extract, wikipedia_url, eco_code, revision_id "
            "FROM wiki_openings WHERE eco_code = $1 LIMIT 1",
            eco,
        )
        if row:
            return dict(row)

    return None
