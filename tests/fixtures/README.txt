Test fixtures for grounding (whitespace folding, § / Art. section headings). Fetched 2026-11-04 (Europe/Warsaw). No LLM calls made for this prep.

1) govinfo_hr7521_excerpt.txt
   Source: https://www.govinfo.gov/content/pkg/BILLS-118hr7521ih/html/BILLS-118hr7521ih.htm
   (H.R. 7521, 118th Congress, Introduced in House). SEC. 1 and SEC. 2(a)-(b) with govinfo's original hard
   line breaks and indentation, plus 3 single-spaced model quotes from raw/us_analyze_run2.json that failed
   with quote_not_in_source. All 3 match once whitespace runs are collapsed.
   License: US federal government work, public domain (17 U.S.C. § 105). Safe to commit.

2) lovdata_helligdagsfredloven_excerpt.txt
   Source: https://lovdata.no/dokument/NL/lov/1995-02-24-12 (LOV-1995-02-24-12, helligdagsfredloven), §§ 2-5.
   Rendered one line per heading, paragraph and list item. Lovdata's "Endret ved ..." amendment notes are kept
   (that's what a page scrape contains). Also includes the model quotes from raw/no_analyze_run2.json that failed
   with section_unmatched.
   License: Norwegian statutes are not copyright-protected (åndsverkloven § 14). Lovdata's terms
   (https://lovdata.no/info/vilkar) § 2.1 forbid redistributing content from its open services. But § 2.3 exempts
   "gjeldende formelle lover" (current formal laws): they may be freely copied, used and shared under NLOD 2.0 if
   Lovdata is credited as the source. Single documents only, no bulk download or systematic extraction.
   Concerns before committing: (a) credit "Source: Lovdata (lovdata.no), NLOD 2.0" in the repo. (b) The
   amendment notes and headings layout are Lovdata editorial content. They're probably covered by the same
   exception, but stripping them is the safest choice. (c) Don't add a scraper that pulls many acts from
   lovdata.no; that's prohibited mass extraction.

3) sejm_DU_2024_1823_excerpt.txt
   Source: Ustawa z dnia 19 listopada 2024 r. o likwidacji Fundacji Platforma Przemysłu Przyszłości,
   Dz.U. 2024 poz. 1823 (status: obowiązujący), ELI DU/2024/1823.
     details: https://api.sejm.gov.pl/eli/acts/DU/2024/1823
     PDF:     https://api.sejm.gov.pl/eli/acts/DU/2024/1823/text.pdf   (official Dziennik Ustaw PDF)
     HTML:    https://api.sejm.gov.pl/eli/acts/DU/2024/1823/text.html
   Art. 1-2 (with ust. and pkt), twice: once as pdftotext -layout of the official PDF, once as tags-stripped
   text.html, so both layouts can be tested.
   License: official normative acts are not subject to copyright (Art. 4 pkt 1 of the Polish Copyright Act,
   ustawa o prawie autorskim i prawach pokrewnych). Safe to commit. Crediting "Dziennik Ustaw 2024 poz. 1823,
   via api.sejm.gov.pl" is good practice.
   Quirks worth tests: the PDF hyphenates across lines ("regional-\nnego"), and superscripts are lost
   ("art. 231" in the PDF text is really art. 23^1 of the Kodeks pracy). The HTML puts "1." on its own line,
   apart from the ust. text.

Nothing here contains personal data or secrets.
