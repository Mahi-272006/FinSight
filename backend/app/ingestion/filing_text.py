"""Filing text processing: HTML -> paragraphs -> sections -> chunks.

Three PURE stages, no I/O, fully unit-testable on fixture HTML:

1. `extract_paragraphs` -- strip non-content elements from filing HTML and
   produce normalized text lines (paragraph units).
2. `segment_sections` -- best-effort mapping of lines to 10-K/10-Q Items
   (Business / Risk Factors / MD&A / Financial Statements ...).
3. `chunk_paragraphs` -- group consecutive paragraphs into retrieval-sized
   chunks with overlap.

Design decisions worth knowing:

- PARSER: BeautifulSoup with the `lxml` backend -- lxml recovers from the
  malformed HTML that filers genuinely produce (unclosed tags, stray <),
  where html.parser silently truncates text. A truncated 10-K section would
  corrupt retrieval invisibly, so the forgiving parser is the safe choice.

- INLINE XBRL: modern 10-Ks wrap the VISIBLE document text in `ix:` tags --
  we KEEP that text (it is the document). What we strip is the machine-
  readable financial-data block appended at the end, which is duplicated in
  `style="display:none"` containers, plus the `<ix:header>` metadata block.
  Stripping those removes hundreds of lines of XBRL noise per filing.

- SEGMENTATION IS BEST-EFFORT BY DESIGN: header regexes cannot be perfect
  across 20 years of varying filer formatting, so every guard is biased
  toward NOT matching (a missed header yields an "unknown" section, which
  merely loses a label; a WRONG match mislabels text, which lies to the
  reader). Three guards: (a) the line must START with the item pattern
  (mid-sentence cross-references like "...per Item 7 above..." never match);
  (b) header lines are short (<= 200 chars -- a prose paragraph that merely
  begins with "Item 7." is long and is skipped); (c) table-of-contents lines
  are filtered by dot leaders ("Item 7. .......... 45") and digit-only
  titles. When fewer than one distinct item is found, the whole document
  becomes one "unknown"-section -- retrieval still works, the label is
  honest.

- CHUNK OVERLAP exists so a question whose answer spans a chunk boundary
  can still retrieve both sides. The cost is that boundary sentences appear
  in two chunks (possible duplicate retrieval) -- the standard, accepted
  RAG trade-off, documented here because it WILL show up in results.

- GIANT PARAGRAPHS (financial tables extracted as one long line) are split
  hard at sentence boundaries when possible, at max_chars otherwise. Data is
  never dropped: every character of the filing lands in exactly one chunk
  (except intentional overlap).
"""

import re
import warnings
from dataclasses import dataclass
from typing import Sequence

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

# SEC filings are XHTML-flavored: many declare `<?xml version=...?>` while
# containing malformed HTML that ONLY the HTML parser recovers from
# (unclosed tags, stray '&' in visible text). We parse as HTML on purpose;
# bs4 warns about the declaration, which is expected and harmless here.
# Filter this SPECIFIC warning -- all other warnings stay visible.
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

# ---------------------------------------------------------------------------
# Stage 1: HTML -> paragraphs
# ---------------------------------------------------------------------------

# Tags whose text content is NOT document prose.
_STRIP_TAGS = ["script", "style", "noscript", "ix:header"]

# Tags that mark a line break in the rendered document. Appending a newline
# inside each keeps get_text() from running separate paragraphs together.
_BLOCK_TAGS = ["p", "div", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "li", "table"]

# Whitespace runs (incl. non-breaking spaces used heavily in financial
# tables) collapse to a single space.
_WS_RE = re.compile(r"[ \t\xa0]+")

# Hidden containers (inline XBRL's duplicated machine-readable data).
_HIDDEN_STYLE_RE = re.compile(r"display\s*:\s*none", re.IGNORECASE)


def extract_paragraphs(html: str | bytes) -> list[str]:
    """Convert filing HTML into normalized text lines.

    Returns a list of non-empty, whitespace-normalized strings, one per
    rendered text block (paragraph, table row, heading). Empty lines are
    dropped -- they carry no retrieval value and would only waste chunk
    budget.
    """
    soup = BeautifulSoup(html, "lxml")

    # Full removal: these elements' text content is code, styles, or
    # duplicated XBRL payloads -- never document prose.
    for tag_name in _STRIP_TAGS:
        for tag in soup.find_all(tag_name):
            tag.decompose()
    # Inline XBRL duplicates the financial data in display:none containers
    # (the visible statements are the real text; these hidden copies are for
    # machines). Strip whole containers.
    for tag in soup.find_all(style=_HIDDEN_STYLE_RE):
        tag.decompose()

    # Line breaks at block boundaries, spaces inside table rows.
    for tag_name in _BLOCK_TAGS:
        for tag in soup.find_all(tag_name):
            tag.append("\n")
    for tag_name in ("td", "th"):
        for tag in soup.find_all(tag_name):
            tag.append(" ")

    text = soup.get_text()
    paragraphs: list[str] = []
    for line in text.splitlines():
        line = _WS_RE.sub(" ", line).strip()
        if line:
            paragraphs.append(line)
    return paragraphs


# ---------------------------------------------------------------------------
# Stage 2: paragraphs -> sections
# ---------------------------------------------------------------------------

# Canonical section names are derived from the header's OWN TITLE text, not
# from item numbers -- because item numbers are form-dependent and a single
# number can even mean two things inside one form:
#   10-K Item 2      = Properties
#   10-Q Part I  Item 2  = Management's Discussion and Analysis
#   10-Q Part II Item 2  = Unregistered Sales of Equity Securities
# Number-based naming LIED about live-ingested 10-Q content (MD&A chunks
# labeled "properties", financial statements labeled "business") -- caught by
# inspecting section distributions on real MSFT filings. Title keywords are
# direct evidence: what the filer literally wrote the section is called, and
# they are also robust to filers who deviate from standard numbering.
#
# Ordered rules, matched against the lowercased title (most specific first).
# NOTE: no apostrophes in patterns -- extracted titles carry varying
# apostrophe encodings (', \u2019, mojibake), so match phrases that avoid
# them ("discussion and analysis" instead of "management's discussion").
_TITLE_RULES: tuple[tuple[str, str], ...] = (
    ("risk factor", "risk_factors"),
    ("discussion and analysis", "mdna"),
    ("quantitative and qualitative", "market_risk"),
    ("controls and procedures", "controls_and_procedures"),
    ("financial statements", "financial_statements"),
    ("legal proceedings", "legal_proceedings"),
    ("unregistered sales", "unregistered_sales_of_equity"),
    ("unresolved staff comments", "unresolved_staff_comments"),
    ("mine safety", "mine_safety_disclosures"),
    ("defaults upon senior", "defaults_upon_senior_securities"),
    ("issuer purchases of equity", "issuer_purchases_of_equity_securities"),
    ("market for registrant", "market_for_common_equity"),
    ("other information", "other_information"),
    ("selected financial data", "selected_financial_data"),
    ("security ownership", "security_ownership"),
    ("accounting fees", "principal_accounting_fees"),
    ("relationships and related", "relationships_and_related_transactions"),
    ("cybersecurity", "cybersecurity"),
    ("exhibits", "exhibits_and_schedules"),
    ("properties", "properties"),
    ("business", "business"),
)


def section_name(title: str, item_number: str) -> str:
    """Map a header's title + item number to a canonical section name.

    Falls back to a generic `item_N` when the title carries no recognizable
    keyword. Generic is the HONEST fallback: we know which item it is, we
    just don't invent a content claim we can't support.
    """
    lowered = title.lower()
    for keyword, name in _TITLE_RULES:
        if keyword in lowered:
            return name
    return f"item_{item_number}"

# A line beginning an Item header, e.g. "Item 7. Management's Discussion...",
# "ITEM 1A RISK FACTORS", "Item 7:", "ITEM 7A.".
_HEADER_RE = re.compile(r"^\s*item\s+(\d{1,2}[a-z]?)\s*[.:\-]?\s*(.*)$", re.IGNORECASE)

# Table-of-contents page reference: dot leaders, or a bare page number at
# the end of the line ("Item 7. Management's Discussion and Analysis 34").
_TOC_DOTS_RE = re.compile(r"\.{3,}")
_TOC_PAGE_RE = re.compile(r"\s\d{1,3}$")

# Header lines are short; prose paragraphs that happen to start with
# "Item 7." run much longer. Chosen after inspecting real filings: actual
# headers are < 150 chars; the guard is 200 for margin.
_MAX_HEADER_CHARS = 200

UNKNOWN_SECTION = "unknown"


@dataclass(frozen=True)
class Section:
    """A labeled slice of the paragraph list (one 10-K/10-Q Item)."""

    name: str                       # canonical name, e.g. "mdna"; "unknown" if unlabeled
    item_number: str | None         # "7", "1a", ...; None for unlabeled text
    para_start: int                 # inclusive index into the paragraph list
    para_end: int                   # exclusive


def _header_item(line: str) -> tuple[str, str] | None:
    """Return (item_number, title) if this line is a genuine Item header.

    Four anti-false-positive guards, all derived from inspecting a real
    MSFT 10-K (its TOC, body, and per-page running headers):

    1. Table-of-contents dot leaders ("Item 7. ..... 45").
    2. Trailing page number ("Item 1. Business 1" -- the TOC's page cell
       lands on the same extracted line when cells are separate <td>s).
    3. Empty or digit-only title: bare "Item 7." / "Item 1" lines are TOC
       cells and per-page running headers, not section starts.
    4. Line length: prose paragraphs that merely BEGIN with "Item 7." run
       long; real headers are short.

    Prefer missing a header (text merges into the previous section, retrieval
    still works) over inventing one (mislabels every chunk after it).

    Returns the title alongside the number because section NAMING reads the
    title (item numbers are form-dependent -- see _TITLE_RULES).
    """
    match = _HEADER_RE.match(line)
    if not match:
        return None
    if _TOC_DOTS_RE.search(line):
        return None
    if _TOC_PAGE_RE.search(line):
        return None
    title = match.group(2).strip()
    if not title or title.isdigit():
        return None
    if len(line) > _MAX_HEADER_CHARS:
        return None
    return match.group(1).lower(), title


def segment_sections(paragraphs: Sequence[str]) -> list[Section]:
    """Split paragraphs into Sections at Item headers.

    Text before the first header (cover page, TOC) becomes one "unknown"
    section -- kept, not dropped: the cover page and TOC occasionally carry
    citable content, and dropping data violates the pipeline's rule.
    A document with no recognizable headers yields a single "unknown"
    section spanning everything.
    """
    if not paragraphs:
        return []
    headers: list[tuple[int, str, str]] = []  # (paragraph index, item, title)
    current_item: str | None = None
    for i, line in enumerate(paragraphs):
        header = _header_item(line)
        if header is None:
            continue
        item, title = header
        # Same-item repeat while already inside that item's section is page
        # furniture (running headers/footers, e.g. "Item 1A. Risk Factors"
        # at the top of every page) -- absorb it as ordinary text instead of
        # fragmenting the section into per-page slices.
        if item == current_item:
            continue
        current_item = item
        headers.append((i, item, title))

    if not headers:
        return [Section(UNKNOWN_SECTION, None, 0, len(paragraphs))]

    sections: list[Section] = []
    if headers[0][0] > 0:
        sections.append(Section(UNKNOWN_SECTION, None, 0, headers[0][0]))

    for pos, (start, item, title) in enumerate(headers):
        end = headers[pos + 1][0] if pos + 1 < len(headers) else len(paragraphs)
        name = section_name(title, item)
        sections.append(Section(name, item, start, end))
    return sections


# ---------------------------------------------------------------------------
# Stage 3: paragraphs -> chunks
# ---------------------------------------------------------------------------

DEFAULT_CHUNK_CHARS = 2400
DEFAULT_OVERLAP_CHARS = 300


@dataclass
class FilingChunk:
    """One retrieval unit: text plus its provenance within the filing."""

    text: str
    section: str                    # canonical section name ("mdna", "unknown", ...)
    item_number: str | None
    chunk_index: int                # sequential within the whole filing
    para_start: int                 # inclusive paragraph indices (into the
    para_end: int                   # filing-wide paragraph list, for citation)


def _split_giant_paragraph(para: str, max_chars: int) -> list[str]:
    """Split a paragraph longer than max_chars into pieces.

    Prefers sentence boundaries (". " found before the cut) so pieces stay
    readable; falls back to a hard character cut when no boundary exists in
    the first half (typical for financial table rows, which have no
    periods). Every character is preserved.
    """
    pieces: list[str] = []
    rest = para
    while len(rest) > max_chars:
        cut = rest.rfind(". ", 0, max_chars)
        if cut < max_chars // 2:
            cut = max_chars - 1
        else:
            cut += 1  # keep the period with its sentence
        pieces.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    if rest:
        pieces.append(rest)
    return pieces


def chunk_section(
    paragraphs: Sequence[str],
    section: Section,
    chunk_index_start: int,
    max_chars: int = DEFAULT_CHUNK_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
) -> tuple[list[FilingChunk], int]:
    """Chunk one section's paragraphs; returns (chunks, next chunk index).

    Accumulates paragraphs until max_chars would be exceeded, then flushes
    and starts the next chunk with the trailing ~overlap_chars of the
    previous one (continuity across the boundary). A paragraph longer than
    max_chars is hard-split into its own chunks after flushing the buffer
    (mixing a giant table line with prose would waste the whole budget).

    Invariants (all tested):
    - every chunk's text length <= max_chars, except a single paragraph
      that cannot be split further by sentence (still <= max_chars after
      _split_giant_paragraph, by construction of the hard cut);
    - chunk_indices are contiguous from chunk_index_start;
    - the section's paragraphs all appear (overlap duplicates only the
      intentional trailing region).
    """
    chunks: list[FilingChunk] = []
    buffer: list[str] = []
    buf_chars = 0
    next_index = chunk_index_start

    def flush(end_para: int) -> None:
        nonlocal buffer, buf_chars, next_index
        if not buffer:
            return
        start_para = end_para - len(buffer)
        chunks.append(FilingChunk(
            text="\n".join(buffer),
            section=section.name,
            item_number=section.item_number,
            chunk_index=next_index,
            para_start=start_para,
            para_end=end_para,
        ))
        next_index += 1
        # Keep the trailing paragraphs (up to overlap_chars) as the head of
        # the next chunk.
        overlap: list[str] = []
        overlap_len = 0
        for para in reversed(buffer):
            if overlap_len + len(para) > overlap_chars:
                break
            overlap.insert(0, para)
            overlap_len += len(para)
        buffer = overlap
        buf_chars = overlap_len

    # Iterate ONLY this section's paragraphs. (Real bug caught by
    # test_para_indices_reference_filing_wide_list: the first version walked
    # the FULL paragraph list, so every section chunked the entire document
    # -- duplicated text across sections and negative para_start values.)
    section_paragraphs = paragraphs[section.para_start:section.para_end]
    for offset, para in enumerate(section_paragraphs):
        abs_idx = section.para_start + offset
        if len(para) > max_chars:
            flush(abs_idx)
            for piece in _split_giant_paragraph(para, max_chars):
                chunks.append(FilingChunk(
                    text=piece,
                    section=section.name,
                    item_number=section.item_number,
                    chunk_index=next_index,
                    para_start=abs_idx,
                    para_end=abs_idx + 1,
                ))
                next_index += 1
            buffer = []
            buf_chars = 0
            continue

        if buf_chars + len(para) + 1 > max_chars and buffer:
            flush(abs_idx)
        buffer.append(para)
        buf_chars += len(para) + 1

    flush(section.para_end)
    return chunks, next_index


def chunk_document(
    paragraphs: Sequence[str],
    sections: Sequence[Section] | None = None,
    max_chars: int = DEFAULT_CHUNK_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
) -> list[FilingChunk]:
    """Chunk every section of the filing; chunk indices run across the
    whole document (a filing-level sequence, not per-section)."""
    if sections is None:
        sections = segment_sections(paragraphs)
    all_chunks: list[FilingChunk] = []
    next_index = 0
    for section in sections:
        chunks, next_index = chunk_section(
            paragraphs, section, next_index, max_chars, overlap_chars
        )
        all_chunks.extend(chunks)
    return all_chunks
