"""Tests for filing text processing (M4): extraction, segmentation, chunking.

All fixtures are hand-built HTML/paragraph lists -- zero network, zero model
loads. Every guard (TOC filter, length cap, ix:header strip, hidden-block
strip) has a dedicated failure-direction test: not just "it works when the
input is clean", but "it does NOT misfire when the input is adversarial".
"""

from app.ingestion.filing_text import (
    UNKNOWN_SECTION,
    Section,
    chunk_document,
    chunk_section,
    extract_paragraphs,
    segment_sections,
    _split_giant_paragraph,
)

# ---------------------------------------------------------------------------
# Fixture: a miniature but structurally realistic 10-K
# ---------------------------------------------------------------------------

FIXTURE_HTML = """
<html><head>
<script>var tracking = "should-be-stripped";</script>
<style>.x { color: red; }</style>
</head><body>
<ix:header xmlns:ix="http://www.xbrl.org/2013/inlineXBRL">
  <div>Financial Report</div>
  <div>Document Fiscal Year Focus: 2025</div>
</ix:header>
<h1>UNITED STATES</h1>
<p>SECURITIES AND EXCHANGE COMMISSION</p>
<p>FORM 10-K For the fiscal year ended June 30, 2025</p>
<table>
<tr><td>Table of Contents</td></tr>
<tr><td>Item 1.</td><td>Business........................................1</td></tr>
<tr><td>Item 1A.</td><td>Risk Factors.......................12</td></tr>
<tr><td>Item 7.</td><td>Management's Discussion and Analysis....45</td></tr>
<tr><td>Item 8.</td><td>Financial Statements.....................60</td></tr>
</table>
<h2>Item 1. Business</h2>
<p>We build productivity platforms and services.</p>
<p>Our three reportable segments are productivity, intelligent cloud, and
more personal computing.</p>
<h2>ITEM 1A. RISK FACTORS</h2>
<p>Our business is subject to intense competition and rapid technological
change. Cybersecurity incidents could harm our reputation.</p>
<p>We face substantial international operations risk including currency
fluctuations and regulatory compliance.</p>
<h2>Item 7. Management's Discussion and Analysis of Financial Condition and
Results of Operations</h2>
<p>Revenue increased driven by growth in Azure and Office 365 commercial.</p>
<p>Operating income increased as revenue growth outpaced cost growth.
See Item 7. Management's Discussion above for prior-year detail.</p>
<h2>Item 8. Financial Statements</h2>
<div style="display:none">
  <p>Revenue 281724000000 245122000000</p>
  <p>NetIncome 101831000000 88136000000</p>
</div>
<p>The following table sets forth our consolidated statements of
operations (in millions).</p>
</body></html>
"""


class TestExtractParagraphs:
    def test_script_and_style_stripped(self):
        paragraphs = extract_paragraphs(FIXTURE_HTML)
        joined = "\n".join(paragraphs)
        assert "should-be-stripped" not in joined
        assert "color: red" not in joined

    def test_ix_header_stripped(self):
        # ix:header holds machine metadata (fiscal-year focus), not prose.
        paragraphs = extract_paragraphs(FIXTURE_HTML)
        assert not any("Document Fiscal Year Focus" in p for p in paragraphs)

    def test_hidden_xbrl_block_stripped(self):
        # The display:none block duplicates financial data in noise form;
        # the VISIBLE table prose must remain.
        paragraphs = extract_paragraphs(FIXTURE_HTML)
        joined = "\n".join(paragraphs)
        assert "281724000000" not in joined
        assert "consolidated statements" in joined

    def test_visible_text_preserved(self):
        paragraphs = extract_paragraphs(FIXTURE_HTML)
        joined = "\n".join(paragraphs)
        assert "We build productivity platforms" in joined
        assert "Cybersecurity incidents" in joined

    def test_block_tags_become_separate_lines(self):
        paragraphs = extract_paragraphs(FIXTURE_HTML)
        # Adjacent <p> elements must not run together into one line.
        assert "SECURITIES AND EXCHANGE COMMISSION" in paragraphs
        assert "FORM 10-K" in "".join(paragraphs)
        for para in paragraphs:
            assert "\n" not in para  # lines are single-line by construction

    def test_whitespace_and_nbsp_collapsed(self):
        html = "<p>Hello&nbsp;&nbsp; world \t there</p>"
        assert extract_paragraphs(html) == ["Hello world there"]

    def test_empty_input(self):
        assert extract_paragraphs("") == []

    def test_table_cells_survive_as_row_text(self):
        paragraphs = extract_paragraphs(FIXTURE_HTML)
        joined = "\n".join(paragraphs)
        assert "Table of Contents" in joined


# ---------------------------------------------------------------------------
# Segmentation
# ---------------------------------------------------------------------------


class TestSegmentSections:
    def test_fixture_sections_found(self):
        paragraphs = extract_paragraphs(FIXTURE_HTML)
        sections = segment_sections(paragraphs)
        names = [s.name for s in sections]
        assert names[0] == UNKNOWN_SECTION  # cover page before first header
        assert "business" in names
        assert "risk_factors" in names
        assert "mdna" in names
        assert "financial_statements" in names

    def test_toc_lines_not_mistaken_for_headers(self):
        # The TOC has "Item 7." style lines with dot leaders -- they must not
        # create spurious sections (the guard is the dots regex).
        paragraphs = extract_paragraphs(FIXTURE_HTML)
        sections = segment_sections(paragraphs)
        mdna_sections = [s for s in sections if s.name == "mdna"]
        assert len(mdna_sections) == 1  # exactly one: the real body header

    def test_section_text_slices_are_correct(self):
        paragraphs = extract_paragraphs(FIXTURE_HTML)
        sections = segment_sections(paragraphs)
        mdna = next(s for s in sections if s.name == "mdna")
        body = "\n".join(paragraphs[mdna.para_start:mdna.para_end])
        assert "Revenue increased" in body
        # The NEXT section's text must not bleed in.
        assert "consolidated statements" not in body

    def test_cross_reference_line_is_not_a_header(self):
        # The length guard: a prose paragraph that merely BEGINS with
        # "Item 7." is not a header once it exceeds _MAX_HEADER_CHARS
        # (real headers stay well under it). (First version of this test
        # used a 181-char line -- UNDER the limit -- so the parser matched
        # it correctly and the test failed; the guard was never the bug.)
        long_prose = (
            "Item 7. This paragraph explains a cross reference to another "
            "section of this report in considerable detail, discussing "
            "prior-year comparatives and the basis of presentation, and "
            "therefore runs far beyond two hundred characters in total."
        )
        assert len(long_prose) > 200
        paragraphs = [
            "Item 1. Business",
            long_prose,
            "Item 8. Financial Statements",
        ]
        sections = segment_sections(paragraphs)
        names = [s.name for s in sections]
        assert names == ["business", "financial_statements"]

    def test_short_item_line_within_limit_is_a_header(self):
        # Boundary: a line just under the limit IS treated as a header --
        # the guard rejects long lines, not "Item" lines as such. The title
        # is gibberish, so the name honestly falls back to item_7 rather
        # than claiming mdna it cannot verify.
        line = "Item 7. " + "x" * 180  # 188 chars, under the 200 limit
        assert len(line) <= 200
        sections = segment_sections([line, "some body text"])
        assert sections[0].name == "item_7"

    def test_section_name_from_title_not_item_number(self):
        # The live bug this replaced: 10-Q Item 2 is MD&A but the old
        # number-based map labeled it "properties" (the 10-K's Item 2).
        # Names must come from the title, not the number.
        from app.ingestion.filing_text import section_name

        assert section_name("Management's Discussion and Analysis", "2") == "mdna"
        assert section_name("Properties", "2") == "properties"
        assert section_name("Unregistered Sales of Equity Securities and Use of Proceeds", "2") == "unregistered_sales_of_equity"
        assert section_name("Financial Statements and Supplementary Data", "1") == "financial_statements"
        assert section_name("Business", "1") == "business"
        assert section_name("Something Unrecognized", "14") == "item_14"

    def test_10q_headers_named_by_title(self):
        # Real MSFT 10-Q header lines (the distribution-inspection bug):
        # Item 1 = Financial Statements, Item 2 = MD&A, Item 3 = Market Risk.
        paragraphs = [
            "ITEM 1. FINANCIAL STATEMENTS",
            "statements text",
            "ITEM 2. MANAGEMENT'S DISCUSSION AND ANALYSIS OF FINANCIAL CONDITION",
            "mdna text",
            "ITEM 3. QUANTITATIVE AND QUALITATIVE DISCLOSURES ABOUT MARKET RISK",
            "risk text",
        ]
        sections = segment_sections(paragraphs)
        assert [s.name for s in sections] == [
            "financial_statements", "mdna", "market_risk",
        ]

    def test_digit_only_title_is_a_page_reference(self):
        # "Item 7. 34" (TOC page ref without dots) is filtered by the
        # digit-only-title guard.
        paragraphs = ["Item 7. 34", "Item 8. Financial Statements", "text"]
        sections = segment_sections(paragraphs)
        assert [s.name for s in sections] == [UNKNOWN_SECTION, "financial_statements"]

    def test_no_headers_single_unknown_section(self):
        paragraphs = ["just some text", "more text"]
        sections = segment_sections(paragraphs)
        assert len(sections) == 1
        assert sections[0].name == UNKNOWN_SECTION
        assert (sections[0].para_start, sections[0].para_end) == (0, 2)

    def test_bare_item_line_is_page_furniture_not_header(self):
        # Real MSFT 10-K pattern: TOC cells and per-page running headers
        # render as bare "Item 1" / "Item 1." lines. Only titled lines are
        # genuine headers.
        paragraphs = ["ITEM 1. BUSINESS", "Item 1", "Item 1.", "text"]
        sections = segment_sections(paragraphs)
        assert [s.name for s in sections] == ["business"]

    def test_toc_row_with_trailing_page_number_rejected(self):
        # TOC page-number cell lands on the same extracted line when cells
        # are separate <td>s: "Item 1. Business 1" -- trailing-number guard.
        # The rejected TOC line sits BEFORE the real header, so it becomes a
        # leading "unknown" section (kept, not dropped) -- the guard's job is
        # that it does NOT become a spurious "business" section.
        paragraphs = ["Item 1. Business 1", "ITEM 1. BUSINESS", "text"]
        sections = segment_sections(paragraphs)
        assert [s.name for s in sections] == ["unknown", "business"]

    def test_repeated_titled_header_of_current_item_absorbed(self):
        # Full-title running header on every page ("Item 1A. Risk Factors")
        # must not fragment the section into per-page slices.
        paragraphs = [
            "ITEM 1A. RISK FACTORS",
            "Item 1A. Risk Factors",
            "Item 1A. Risk Factors",
            "Item 8. Financial Statements",
        ]
        sections = segment_sections(paragraphs)
        assert [s.name for s in sections] == ["risk_factors", "financial_statements"]
        # The absorbed repeats live INSIDE the risk_factors slice.
        risk = sections[0]
        assert (risk.para_start, risk.para_end) == (0, 3)

    def test_same_item_after_different_item_is_reentry(self):
        # Leaving Item 1A and coming back to Item 1 is a genuine re-entry
        # (not page furniture) and must start a new section.
        paragraphs = [
            "Item 1A. Risk Factors",
            "Item 1. Business",
        ]
        sections = segment_sections(paragraphs)
        assert [s.name for s in sections] == ["risk_factors", "business"]

    def test_empty_paragraphs(self):
        assert segment_sections([]) == []

    def test_unknown_item_number_gets_generic_name(self):
        paragraphs = ["Item 22. Something Unusual", "text"]
        sections = segment_sections(paragraphs)
        # Header at index 0: no leading unknown section, one section total.
        assert len(sections) == 1
        assert sections[0].name == "item_22"


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------


class TestChunking:
    def test_chunks_respect_max_chars(self):
        paragraphs = extract_paragraphs(FIXTURE_HTML)
        chunks = chunk_document(paragraphs, max_chars=200, overlap_chars=40)
        assert chunks
        for chunk in chunks:
            assert len(chunk.text) <= 200

    def test_overlap_creates_boundary_continuity(self):
        # Two chunks separated by a boundary must share their seam text.
        paragraphs = [f"Paragraph number {i} with a little bit of text." for i in range(20)]
        chunks = chunk_document(paragraphs, max_chars=150, overlap_chars=50)
        assert len(chunks) >= 2
        # The second chunk starts with the tail of the first (overlap).
        first_tail = chunks[0].text.split("\n")[-1]
        assert first_tail in chunks[1].text

    def test_chunk_indices_contiguous(self):
        paragraphs = extract_paragraphs(FIXTURE_HTML)
        chunks = chunk_document(paragraphs, max_chars=200, overlap_chars=40)
        indices = [c.chunk_index for c in chunks]
        assert indices == list(range(len(chunks)))

    def test_section_labels_flow_into_chunks(self):
        paragraphs = extract_paragraphs(FIXTURE_HTML)
        chunks = chunk_document(paragraphs, max_chars=300, overlap_chars=50)
        sections_seen = {c.section for c in chunks}
        assert "risk_factors" in sections_seen
        assert "mdna" in sections_seen

    def test_giant_paragraph_hard_split(self):
        # A single huge "table row" line (no sentence periods) must be split
        # into <= max_chars pieces, preserving all characters in order.
        giant = "123456789 " * 100  # 1000 chars, no periods
        section = Section("financial_statements", "8", 0, 1)
        chunks, next_index = chunk_section([giant], section, 0, max_chars=300, overlap_chars=0)
        assert len(chunks) >= 3
        for c in chunks:
            assert len(c.text) <= 300
        # Reassembling (dropping whitespace lost at strip boundaries) keeps
        # every digit in order.
        reassembled = "".join(c.text for c in chunks).replace(" ", "")
        assert reassembled == ("123456789" * 100)

    def test_giant_paragraph_prefers_sentence_boundary(self):
        sentence = "This is a complete sentence. " * 40
        pieces = _split_giant_paragraph(sentence, max_chars=200)
        assert all(len(p) <= 200 for p in pieces)
        # Sentence pieces end with a period (boundary respected).
        assert all(p.endswith(".") for p in pieces[:-1])

    def test_deterministic_output(self):
        paragraphs = extract_paragraphs(FIXTURE_HTML)
        run1 = chunk_document(paragraphs, max_chars=200, overlap_chars=40)
        run2 = chunk_document(paragraphs, max_chars=200, overlap_chars=40)
        assert [c.text for c in run1] == [c.text for c in run2]
        assert [c.chunk_index for c in run1] == [c.chunk_index for c in run2]

    def test_empty_document_yields_no_chunks(self):
        assert chunk_document([]) == []

    def test_para_indices_reference_filing_wide_list(self):
        # chunk.para_start/end index into the ORIGINAL paragraph list so the
        # UI can highlight the source region later.
        paragraphs = ["a", "b", "c", "d", "e", "f", "g", "h"]
        section = Section("business", "1", 2, 6)  # paragraphs c..f
        chunks, _ = chunk_section(paragraphs, section, 0, max_chars=10**6)
        assert len(chunks) == 1
        assert chunks[0].para_start == 2
        assert chunks[0].para_end == 6
