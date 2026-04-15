from __future__ import annotations

import csv
import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html import unescape
from io import StringIO
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from .models import IntelItem, MarketSnapshot, SourceDefinition

USER_AGENT = "intel-center/0.1 (+https://local-workspace)"
XML_NAMESPACES = {
    "atom": "http://www.w3.org/2005/Atom",
}


class FetchError(RuntimeError):
    """Raised when a source fails to load or parse."""


def default_loader(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.read().decode("utf-8", errors="replace")


def _parse_datetime(raw: str | None) -> datetime:
    if not raw:
        return datetime.now(UTC)
    raw = raw.strip()
    for candidate in (raw, raw.replace("Z", "+00:00")):
        try:
            parsed = datetime.fromisoformat(candidate)
            return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            continue
    try:
        parsed = parsedate_to_datetime(raw)
        return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return datetime.now(UTC)


def _strip_html(text: str | None) -> str:
    if not text:
        return ""
    no_tags = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", no_tags).strip()


def _item_id(source: SourceDefinition, title: str, url: str) -> str:
    stem = re.sub(r"[^a-z0-9]+", "-", f"{source.id}-{title.lower()}")[:80].strip("-")
    return stem or re.sub(r"[^a-z0-9]+", "-", url.lower())[:80].strip("-")


def _build_item(
    source: SourceDefinition,
    title: str,
    url: str,
    published_at: str | None,
    summary: str | None,
    *,
    journal_name: str = "",
    journal_quartile: str = "",
    impact_factor: str = "",
    venue_type: str = "",
) -> IntelItem:
    clean_summary = _strip_html(summary)
    return IntelItem(
        id=_item_id(source, title, url),
        title_en=title.strip(),
        source=source.name,
        url=url,
        published_at=_parse_datetime(published_at),
        track=source.track,
        tags=list(source.tags),
        source_id=source.id,
        summary_en=clean_summary,
        journal_name=journal_name.strip(),
        journal_quartile=journal_quartile.strip(),
        impact_factor=str(impact_factor).strip() if impact_factor else "",
        venue_type=venue_type.strip(),
        evidence_level=source.evidence_level,
        source_class=source.source_class,
        source_admission_status=source.admission_status,
        curation_tier=source.curation_tier,
        is_archive=source.source_class == "archive",
    )


def parse_rss_or_atom(source: SourceDefinition, payload: str) -> list[IntelItem]:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise FetchError(f"{source.id}: invalid XML") from exc

    items: list[IntelItem] = []
    if root.tag.endswith("feed"):
        for entry in root.findall("atom:entry", XML_NAMESPACES):
            title = entry.findtext("atom:title", default="", namespaces=XML_NAMESPACES)
            summary = entry.findtext("atom:summary", default="", namespaces=XML_NAMESPACES)
            published = entry.findtext("atom:published", default="", namespaces=XML_NAMESPACES) or entry.findtext(
                "atom:updated",
                default="",
                namespaces=XML_NAMESPACES,
            )
            link_el = entry.find("atom:link", XML_NAMESPACES)
            url = ""
            if link_el is not None:
                url = link_el.attrib.get("href", "").strip()
            if title and url:
                items.append(
                    _build_item(
                        source,
                        title,
                        url,
                        published,
                        summary,
                        journal_name="arXiv" if "arxiv" in (source.id + (source.url or "")).lower() else source.name,
                        venue_type="preprint" if "arxiv" in (source.id + (source.url or "")).lower() else "feed",
                    )
                )
        return items

    for node in root.findall(".//item"):
        title = node.findtext("title", default="")
        url = node.findtext("link", default="")
        summary = node.findtext("description", default="")
        published = node.findtext("pubDate", default="") or node.findtext("dc:date", default="")
        if title and url:
            items.append(_build_item(source, title, url, published, summary, journal_name=source.name, venue_type="institution"))
    return items


def parse_europe_pmc(source: SourceDefinition, payload: str) -> list[IntelItem]:
    try:
        blob = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise FetchError(f"{source.id}: invalid JSON") from exc

    results = blob.get("resultList", {}).get("result", [])
    items: list[IntelItem] = []
    for result in results:
        title = result.get("title") or ""
        if not title:
            continue
        doi = result.get("doi")
        pmid = result.get("pmid")
        url = ""
        if pmid:
            url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
        elif doi:
            url = f"https://doi.org/{doi}"
        else:
            url = result.get("fullTextUrlList", {}).get("fullTextUrl", [{}])[0].get("url") or ""
        if not url:
            continue
        summary = result.get("abstractText") or result.get("journalTitle") or result.get("authorString") or ""
        published = result.get("firstPublicationDate") or result.get("pubYear") or ""
        items.append(
            _build_item(
                source,
                title,
                url,
                published,
                summary,
                journal_name=result.get("journalTitle") or "",
                venue_type="journal",
            )
        )
    return items


def parse_psyarxiv_json(source: SourceDefinition, payload: str) -> list[IntelItem]:
    try:
        blob = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise FetchError(f"{source.id}: invalid PsyArXiv JSON") from exc

    items: list[IntelItem] = []
    for result in blob.get("data", []):
        attributes = result.get("attributes", {})
        links = result.get("links", {})
        title = (attributes.get("title") or "").strip()
        url = (links.get("html") or "").strip()
        if not title or not url:
            continue
        published = attributes.get("date_published") or attributes.get("date_modified") or attributes.get("date_created")
        summary = attributes.get("description") or ""
        item = _build_item(
            source,
            title,
            url,
            published,
            summary,
            journal_name="PsyArXiv",
            venue_type="preprint",
        )
        items.append(item)
    return items


def parse_neuroblu_publications(source: SourceDefinition, payload: str) -> list[IntelItem]:
    items: list[IntelItem] = []
    pattern = re.compile(
        r'<p[^>]+class="overline publications">(?P<journal>.*?)</p>.*?'
        r'<h3 class="heading-4 publications">(?P<title>.*?)</h3>.*?'
        r'(?:<p[^>]+class="details body-1">(?P<authors>.*?)</p>.*?)*'
        r'<a[^>]+href="(?P<url>[^"]+)"[^>]*>.*?<p[^>]+class="date body-1">(?P<date>.*?)</p>.*?</a>.*?'
        r'<div class="text-block">(?P<label>.*?)</div>',
        flags=re.DOTALL,
    )
    author_pattern = re.compile(r'<p[^>]+class="details body-1">(?P<authors>.*?)</p>', flags=re.DOTALL)
    for match in pattern.finditer(payload):
        title = _strip_html(unescape(match.group("title")))
        url = unescape(match.group("url")).strip()
        published = _strip_html(unescape(match.group("date")))
        venue_label = _strip_html(unescape(match.group("label"))).lower()
        journal_name = _strip_html(unescape(match.group("journal"))) or "NeuroBlu"
        author_match = author_pattern.search(match.group(0))
        author_text = _strip_html(unescape(author_match.group("authors"))) if author_match else ""
        venue_type = "journal" if "peer-reviewed" in venue_label else "preprint" if "conference abstract" in venue_label else "institution"
        summary = author_text or f"{journal_name} · {venue_label.title()}"
        items.append(
            _build_item(
                source,
                title,
                url,
                published,
                summary,
                journal_name=journal_name,
                venue_type=venue_type,
            )
        )
    return items


def fetch_source_items(
    source: SourceDefinition,
    loader: Callable[[str], str] = default_loader,
    fixture_bundle: dict | None = None,
    date_cutoff: datetime | None = None,
) -> list[IntelItem]:
    if fixture_bundle:
        fixture_items = fixture_bundle.get("sources", {}).get(source.id)
        if fixture_items is not None:
            return [
                _build_item(
                    source=source,
                    title=item["title"],
                    url=item["url"],
                    published_at=item.get("published_at"),
                    summary=item.get("summary", ""),
                    journal_name=item.get("journal", ""),
                    journal_quartile=item.get("journal_quartile", ""),
                    impact_factor=item.get("impact_factor", ""),
                    venue_type=item.get("venue_type", ""),
                )
                for item in fixture_items
            ]

    try:
        if source.kind == "pubmed":
            return _fetch_pubmed_items(source, loader, date_cutoff=date_cutoff)
        if source.kind == "europe_pmc":
            return _fetch_europe_pmc_items(source, loader, date_cutoff=date_cutoff)
        if source.kind == "psyarxiv":
            return _fetch_psyarxiv_items(source, loader, date_cutoff=date_cutoff)
        if source.kind == "html_publications":
            return _fetch_html_publications(source, loader, date_cutoff=date_cutoff)
        if source.kind in {"rss", "atom"}:
            if not source.url:
                raise FetchError(f"{source.id}: missing URL")
            if source.kind == "atom" and "arxiv" in (source.url or "").lower():
                return _fetch_arxiv_items(source, loader, date_cutoff=date_cutoff)
            return parse_rss_or_atom(source, loader(source.url))
        raise FetchError(f"{source.id}: unsupported source kind {source.kind}")
    except Exception as exc:  # noqa: BLE001
        if isinstance(exc, FetchError):
            raise
        raise FetchError(f"{source.id}: {exc}") from exc


def fetch_market_snapshot(
    definition: dict,
    loader: Callable[[str], str] = default_loader,
    fixture_bundle: dict | None = None,
) -> MarketSnapshot:
    symbol = definition["symbol"]
    if fixture_bundle:
        fixture = fixture_bundle.get("market_snapshots", {}).get(symbol)
        if fixture is not None:
            return MarketSnapshot(
                symbol=symbol,
                close=fixture.get("close"),
                change_pct=fixture.get("change_pct"),
                as_of=fixture.get("as_of"),
                status=fixture.get("status", "ok"),
            )

    url = definition["url"]
    try:
        payload = loader(url)
        reader = csv.DictReader(StringIO(payload))
        row = next(reader, None)
        if not row:
            raise FetchError(f"{symbol}: empty CSV")
        open_price = _safe_float(row.get("Open"))
        close_price = _safe_float(row.get("Close"))
        change_pct = None
        if open_price and close_price:
            change_pct = ((close_price - open_price) / open_price) * 100
        return MarketSnapshot(
            symbol=symbol,
            close=close_price,
            change_pct=change_pct,
            as_of=row.get("Date"),
            status="ok",
        )
    except Exception:  # noqa: BLE001
        return MarketSnapshot(symbol=symbol, close=None, change_pct=None, as_of=None, status="unavailable")


def _safe_float(raw: str | None) -> float | None:
    if raw in {None, "", "N/D"}:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _fetch_europe_pmc_items(
    source: SourceDefinition,
    loader: Callable[[str], str],
    *,
    date_cutoff: datetime | None,
) -> list[IntelItem]:
    query = urllib.parse.quote_plus(source.query or "")
    items: list[IntelItem] = []
    for page in range(1, max(source.max_pages, 1) + 1):
        url = (
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
            f"?query={query}&format=json&pageSize={source.page_size}&page={page}&sort={urllib.parse.quote('P_DATE_D desc')}"
        )
        page_items = parse_europe_pmc(source, loader(url))
        if not page_items:
            break
        items.extend(page_items)
        if date_cutoff:
            oldest = min(item.published_at for item in page_items)
            if oldest < date_cutoff:
                break
        if len(page_items) < source.page_size:
            break
    return _apply_date_cutoff(items, date_cutoff)


def _fetch_arxiv_items(
    source: SourceDefinition,
    loader: Callable[[str], str],
    *,
    date_cutoff: datetime | None,
) -> list[IntelItem]:
    if not source.url:
        raise FetchError(f"{source.id}: missing URL")
    items: list[IntelItem] = []
    for page in range(max(source.max_pages, 1)):
        start = page * source.page_size
        url = _replace_query_params(source.url, {"start": str(start), "max_results": str(source.page_size)})
        page_items = parse_rss_or_atom(source, loader(url))
        if not page_items:
            break
        items.extend(page_items)
        if date_cutoff:
            oldest = min(item.published_at for item in page_items)
            if oldest < date_cutoff:
                break
        if len(page_items) < source.page_size:
            break
    return _apply_date_cutoff(items, date_cutoff)


def _apply_date_cutoff(items: list[IntelItem], date_cutoff: datetime | None) -> list[IntelItem]:
    if not date_cutoff:
        return items
    return [item for item in items if item.published_at >= date_cutoff]


def _fetch_psyarxiv_items(
    source: SourceDefinition,
    loader: Callable[[str], str],
    *,
    date_cutoff: datetime | None,
) -> list[IntelItem]:
    base_url = source.url or "https://api.osf.io/v2/preprints/?filter%5Bprovider%5D=psyarxiv"
    items: list[IntelItem] = []
    for page in range(1, max(source.max_pages, 1) + 1):
        url = _replace_query_params(
            base_url,
            {
                "page[size]": str(source.page_size),
                "page": str(page),
            },
        )
        page_items = parse_psyarxiv_json(source, loader(url))
        if not page_items:
            break
        items.extend(page_items)
        if date_cutoff:
            oldest = min(item.published_at for item in page_items)
            if oldest < date_cutoff:
                break
        if len(page_items) < source.page_size:
            break
    return _apply_date_cutoff(items, date_cutoff)


def _fetch_html_publications(
    source: SourceDefinition,
    loader: Callable[[str], str],
    *,
    date_cutoff: datetime | None,
) -> list[IntelItem]:
    if not source.url:
        raise FetchError(f"{source.id}: missing URL")
    items = parse_neuroblu_publications(source, loader(source.url))
    return _apply_date_cutoff(items, date_cutoff)


def _replace_query_params(url: str, updates: dict[str, str]) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    for key, value in updates.items():
        query[key] = [value]
    flattened = urlencode(query, doseq=True)
    return urlunparse(parsed._replace(query=flattened))


def _fetch_pubmed_items(
    source: SourceDefinition,
    loader: Callable[[str], str],
    *,
    date_cutoff: datetime | None,
) -> list[IntelItem]:
    items: list[IntelItem] = []
    for page in range(max(source.max_pages, 1)):
        retstart = page * source.page_size
        search_url = (
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
            f"?db=pubmed&retmode=json&retmax={source.page_size}&retstart={retstart}&sort=pub+date&term={urllib.parse.quote(source.query or '')}"
        )
        search_blob = json.loads(loader(search_url))
        ids = [str(pmid) for pmid in search_blob.get("esearchresult", {}).get("idlist", []) if str(pmid).strip()]
        if not ids:
            break
        fetch_url = (
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
            f"?db=pubmed&retmode=xml&id={','.join(ids)}"
        )
        page_items = parse_pubmed_xml(source, loader(fetch_url))
        if not page_items:
            break
        items.extend(page_items)
        if date_cutoff:
            oldest = min(item.published_at for item in page_items)
            if oldest < date_cutoff:
                break
        if len(ids) < source.page_size:
            break
    return _apply_date_cutoff(items, date_cutoff)


def parse_pubmed_xml(source: SourceDefinition, payload: str) -> list[IntelItem]:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise FetchError(f"{source.id}: invalid PubMed XML") from exc

    items: list[IntelItem] = []
    for article in root.findall(".//PubmedArticle"):
        medline = article.find("MedlineCitation")
        if medline is None:
            continue
        article_node = medline.find("Article")
        if article_node is None:
            continue

        title = _strip_html("".join(article_node.findtext("ArticleTitle", default="")).strip())
        pmid = medline.findtext("PMID", default="").strip()
        if not title or not pmid:
            continue

        journal_name = article_node.findtext("Journal/Title", default="").strip()
        abstract_parts = []
        for abstract_text in article_node.findall("Abstract/AbstractText"):
            label = abstract_text.attrib.get("Label", "").strip()
            text = "".join(abstract_text.itertext()).strip()
            if not text:
                continue
            if label:
                abstract_parts.append(f"{label}: {text}")
            else:
                abstract_parts.append(text)
        summary = " ".join(abstract_parts).strip()
        published = _pubmed_pubdate(article_node)
        items.append(
            _build_item(
                source,
                title,
                f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                published,
                summary,
                journal_name=journal_name,
                venue_type=_infer_pubmed_venue_type(journal_name),
            )
        )
    return items


def _pubmed_pubdate(article_node: ET.Element) -> str:
    article_date = article_node.find("ArticleDate")
    if article_date is not None:
        year = article_date.findtext("Year", default="").strip()
        month = article_date.findtext("Month", default="").strip()
        day = article_date.findtext("Day", default="").strip()
        if year and month and day:
            return f"{year}-{month.zfill(2)}-{day.zfill(2)}"

    pub_date = article_node.find("Journal/JournalIssue/PubDate")
    if pub_date is None:
        return ""
    year = pub_date.findtext("Year", default="").strip()
    month = _normalize_pubmed_month(pub_date.findtext("Month", default="").strip())
    day = pub_date.findtext("Day", default="").strip() or "01"
    medline = pub_date.findtext("MedlineDate", default="").strip()
    if year:
        return f"{year}-{(month or '01').zfill(2)}-{day.zfill(2)}"
    if medline:
        year_match = re.search(r"(19|20)\d{2}", medline)
        if year_match:
            return f"{year_match.group(0)}-01-01"
    return ""


def _normalize_pubmed_month(raw: str) -> str:
    if raw.isdigit():
        return raw
    mapping = {
        "jan": "01",
        "feb": "02",
        "mar": "03",
        "apr": "04",
        "may": "05",
        "jun": "06",
        "jul": "07",
        "aug": "08",
        "sep": "09",
        "oct": "10",
        "nov": "11",
        "dec": "12",
    }
    return mapping.get(raw[:3].lower(), "")


def _infer_pubmed_venue_type(journal_name: str) -> str:
    lower = journal_name.strip().lower()
    if lower in {"medrxiv", "medrxiv : the preprint server for health sciences", "biorxiv"}:
        return "preprint"
    return "journal"
