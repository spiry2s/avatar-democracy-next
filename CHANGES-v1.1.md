# CHANGES — AI Avatar Democracy v1.0 → v1.1

**Inputs used:**
- `whitepaper-redteam-v1.md` (30 issues)
- `review-v1.md` (code review; used for context only)
- The evidence-review canvas, read from its store file
- Hidalgo's Augmented Democracy FAQ (fetched directly)

Titles, authors, and venues of the academic references were checked against arXiv, DOI, or publisher metadata on 4 Nov 2026.

**Numbering in v1.1:**
- Part numbering is unchanged, so references like "Part 6" still point to the same part.
- Related Work was inserted as **Part 2A**.
- Strengths are now H1–H16 and keep v1.0's numbers.
- Design Decisions 1–37 keep their numbers. New decisions are **38–43**.

## Brief items (user's numbered goals)

| # | Goal | Where |
|---|------|-------|
| 1 | Rename Part 3 and make each benefit testable | Part 3 is now "Expected Benefits (Hypotheses)". Each item gives the hypothesis, its qualifications, its basis (logic / analogy / needs evidence), and a test. |
| 2 | Unsourced numbers | **85–95% compliance:** removed (Part 8); compliance is now stated to be an empirical unknown. **"Tens of billions":** withdrawn (H5); only the arithmetic against ~$7T in outlays [S6] is kept, and it is explicitly labeled as not supporting the estimate. **COVID "meaningfully better":** the net assessment is now "Unknown" (Part 7). The "smaller-than-feared" line was removed from Part 6. |
| 3 | Politically loaded examples | See the flag table below. |
| 4 | Internal contradictions | **Strength 16 vs. party machinery:** H16 withdrawn; Limitation 9 and "bootstrap collusion" added to Part 6. **Maintainer centralization vs. Strength 3:** v1.1 now separates *security* requirements (critical-infrastructure grade) from *control*, which must stay plural (DD38–40). H3 is now conditional, and the remaining tension is listed in Part 6. **Legitimacy vs. "not replacing human judgment":** H1 is rewritten as "disclosed agency, not a referendum ballot", and Part 2's "isn't" list now admits a machine interpreter sits between citizen and vote. |
| 5 | Missing attacks in Part 6 | 7 new Serious items and 6 new Important items (see M9 below). |
| 6 | Related Work | New Part 2A: a summary table (Case, Domain, What, Key finding, Lesson, Sources) plus prose on Hidalgo, ~730 words. Covers vTaiwan/Pol.is, Swiss direct democracy, LiquidFeedback, DAOs, Hidalgo (quoted directly), the Habermas Machine, CCAI, silicon sampling (Argyle; Bisbee et al.), Demoex/Flux/Net Party, and Estonia. Includes an explicit section on what we accept and reject from Hidalgo. The **Stage 0 advisory/shadow chamber** is now the mandatory first stage (Part 2, Part 8). |
| 7 | Weaken "electoral impact" | Part 8 verification now says weak verification supports Stage 0 and civic signals only. New DD42 gates compliance metrics on the "published verification tier" (eID-grade binding is the default bar), and Part 8 uses the same term. |
| 8 | Document history | Appendix B now has a v1.1 entry. |

## Red-team items, one by one

| ID | Issue | v1.1 change |
|----|-------|-------------|
| 1 | "Confirmed Strengths" | **Fixed.** Renamed; every item is tagged and given a test. A Part 9 cross-link was added. |
| 2 | 85–95% compliance | **Fixed.** Number deleted, and the red-team's suggested "public whip, not legal instruction" language adopted. "Unenforceable binding" added to Part 6 Serious. Demoex/Flux/Net Party added as cautionary analogues. |
| 3 | Strength 16 vs. bootstrap | **Fixed.** H16 withdrawn and replaced by Limitation 9 and the Part 6 "bootstrap collusion" item. |
| 4 | Certifier and "allied" providers as choke points | **Fixed.** Part 6 Serious item added on certifier, cloud, and app-store concentration. New DD38 (plural certifiers), DD39 (franchise never conditional on a vendor), and DD40 (protocol, not platform, with concentration caps). DD34's "allied democracies" restriction is now an open question. DD36 notes that it protects build integrity, not legitimacy. |
| 5 | Strength 1 referendum analogy | **Fixed.** H1 rewritten as disclosed agency; unreviewed auto-votes carry less weight. Part 9 adds an explicit "no equivalence" line. |
| 6 | Cost unsupported | **Fixed.** Figure withdrawn (H5), and the components a real cost model must include are listed. |
| 7 | COVID as findings | **Fixed.** Part 7 renamed "Scenario Notes (Speculative, Not Empirical Findings)". Net assessment is "Unknown". Part 6 no longer leans on it. |
| 8 | Hunter Biden example | **Fixed.** Example removed. H13 restated as an open question and marked unverified, including the counter-possibility that continuous voting makes narrative control *more* valuable. |
| 9 | Missing Part 6 attacks (10) | **Partly fixed (no owners or working groups assigned yet).** All ten attacks are now in Part 6, but none has a named owner. Each attack and where it now sits in Part 6: (1) cloud/app-store legislature: Serious. (2) Coerced configuration: Serious, together with (5) preference-data breach/blackmail. (3) Removal of majoritarian filters: Serious, together with (4) per-section gridlock; DD11 also gains section dependencies. (6) Notification fatigue: Important, plus DD13 edit. (7) Second-chamber/judicial nullification: Important. (8) Bootstrap collusion: Serious. (9) Foreign mass delegation: Important. (10) Liability: Important. Also added from the evidence review: preference fidelity (Serious), digital divide (Important), no deliberation step (Important), and, from the code review, delegate ventriloquism (Serious item plus DD41). |
| 10 | Weak verification vs. "electoral impact" | **Fixed.** Claim removed; civic-signal-only rule added (DD42). |
| M11 | Strength 2 anti-federalism | **Fixed.** H2 now calls this a regime choice; hybrids are possible. |
| M12 | Strength 3 depends on deployment | **Fixed.** H3 made conditional, with the counter-evidence from liquid democracy and DAOs. |
| M13 | Strength 4 overclaims | **Fixed.** H4 now says "process more text"; fidelity and injection resistance are open. |
| M14 | "Eliminates" Christmas-tree bills | **Fixed.** H11 now says "raises the cost". Line-item veto evidence [S7, S8] added. |
| M15 | Wikipedia/NGO foils | **Fixed.** H10 now compares against "fixed electoral terms or permanent bureaucratic tenure" and adds LiquidFeedback concentration evidence. |
| M16 | vTaiwan "demonstrated effectiveness" | **Fixed.** DD7 now says "inspired by vTaiwan's consultation experiments". Part 2A gives sourced outcomes and the post-2018 stall. |
| M17 | DD20/DD32 chicken-and-egg | **Fixed.** Both now apply only at Stage 2. Stages 0–1 are stated to be unprotected (Part 2, Part 6). |
| M18 | "No constitutional change" vs. replacing the chamber | **Fixed.** New "Transition versus end state" subsection with Stages 0/1/2. Part 8 bullet clarified. |
| M19 | 2016 Republican primary | **Fixed.** Year and party removed. Reworded as generic insurgent nominations "on both left and right", which show ballot access, not obedience. No non-US analogue was added because the sources contain none (see below). |
| M20 | Trust decline uncited | **Fixed.** Now cites Valgarðsson et al. (BJPS 2025) and Pew, with the nuance that representative and implementing institutions differ and that the decline is not uniform. |
| M21 | Pejorative capture list | **Fixed.** Neutral taxonomy ("economic, occupational, ideological, identity-based, and foreign-state"), with examples from across the spectrum. The 535-vs-200M ratio is marked as unmeasured. |
| m22 | Preface vs. Part 3 tone | **Fixed** via item 1. The Preface also adds a "what changed" note. |
| m23 | NIH/gain-of-function | **Fixed.** Replaced with drug approvals, defense procurement, central-bank operations, and science-agency grants. |
| m24 | Joe Rogan | **Fixed.** Replaced with "high-reach figures across the spectrum (podcast hosts, prime-time broadcasters, religious, union, and community leaders)". |
| m25 | Strength 9 low bar | **Fixed.** Moved to Limitation 8. |
| m26 | Strength 12 "probable" | **Fixed.** H12 now says the direction is contingent, with backlash possible. |
| m27 | Strengths 14–15 soft causal claims | **Fixed.** Both marked speculative. H14 cites the Swiss evidence that parties relocate. |
| m28 | Open proposals vs. flooding | **Fixed.** DD6 adds per-identity rate limits and a co-sponsor or deposit requirement. Parameters left open. |
| m29 | Assertive timeline | **Fixed.** Relabeled "Illustrative timeline" with explicit kill/pause conditions. |
| m30 | Part 9 doesn't police Part 3 | **Fixed.** Part 3 intro and Part 9 cross-reference each other, and Part 9 governs. |

## Politically loaded examples (red-team flag table)

| Flag | Change |
|------|--------|
| Hunter Biden laptop (S13) | Removed. |
| NIH gain-of-function (L1) | Replaced with neutral administrative examples. |
| Joe Rogan (L3) | Replaced with a cross-spectrum list. |
| Wikipedia / NGO fact-checkers (S10) | Removed. |
| Capture antagonist list (Part 1) | Replaced with a neutral taxonomy. |
| 2016 Republican primary / "populist energy" | Replaced with neutral, two-sided wording. |
| "Western nations" framing | Changed to "many established democracies" (Preface, Part 1). |
| CARES Act / mandate scoring (Part 7) | Specific bill and mandate verdicts removed. Now discussed only as procedural mechanisms ("large emergency spending packages"; "emergency authority via legislative vote"), explicitly without judging any measure. |

## Other additions not requested by the red-team

- **Code review → DD41.** From `review-v1.md` (paper-relevant points only): DD41 requires grounded delegation, because the prototype approximates delegates from model knowledge. DD19 now requires summaries to quote the bill text. The emergency path is noted as "must not become a self-declared emergency power". The other code findings are about software, not the paper, and were not carried over.
- **DD43.** Fidelity benchmarking (CIP Digital Twin Evaluation Framework) is required before any Stage 2 authority.
- **H6 vs. ballot secrecy.** H6 now notes that public per-citizen logs conflict with the secret ballot. DD15 and DD18 move toward aggregate or pseudonymous, verifiable-but-not-attributable logs. The new Part 6 coercion item covers the same tension.
- **Part 10.** Now names relevant experts from the evidence review.
- **References.** New Appendix A.

## Deliberately not done, or only partly done

- **Non-US analogue for outsider primary capture (M19).** The sources contain none, so the year was dropped instead of inventing an example.
- **Item 9 asked for each new Part 6 item to have an "owner".** Not added: the paper has no named working groups to assign them to. The items are listed without owners.
- **Tags in the form `logic | analogy | needs evidence` (item 1).** Used as a "Basis:" line rather than inline code tags, to keep the author's prose style.
- **No sourced replacement for the cost or compliance numbers.** None exists in the sources, so both are withdrawn rather than re-estimated.
- **DD8 endorsement thresholds (50k/500k/5M).** These were already labeled illustrative in v1.0 and the red-team didn't flag them, so they were left as they were.

## Verification notes

- **Hidalgo quotes.** Checked verbatim against cesarhidalgo.com/augmented-democracy (archival FAQ, last updated 2019).
- **Bibliographic metadata.** Author lists, titles, and venues for R6–R11, R13, R14, R16–R18 and S4 (v1.1 numbering after the post-review renumbering) were confirmed against arXiv, DOI, or publisher pages. Correction: the evidence review describes IJCAI 2019/17 as "lab experiments". It is in fact Caragiannis & Micha's *theoretical* critique, so v1.1 cites it only as formal analysis, and the "lab experiments show worse outcomes" claim was **dropped** as unsourced.
- **Numeric facts taken from the evidence review without re-opening the primary page:** vTaiwan's 26 cases and ~80%, Swiss turnout findings, LiquidFeedback's one-in-six figure, DAO concentration (3–5 voters), Habermas Machine N=5,734, Demoex/Flux/Net Party vote shares, Estonia's 51%, and GovTrack's 2,232 pages and 18–28 hours.
- **Demoex.** Facts rest on Wikipedia and secondary reporting; the official site returned HTTP 500 during the evidence review. This is noted in the paper.

## Post-review edits (Critic's v1.1 acceptance review, `v1.1-review.md`)

The Critic rated v1.1 "ready with small edits". These edits were applied in place:

1. **Verification gate wording.** DD42 now reads: "Until a published verification tier is met (eID-grade binding is the default bar; whether weaker methods can qualify is an open question), Avatar tallies are civic signals only." Part 8, Stage 1 (Part 2), the timeline, and the kill conditions now all use the same term, "published verification tier".
2. **Brand layer softened (Part 8).** A "conscience" defense is now treated as a campaign liability and logged as a defection event, not as automatic proof of permanent unfitness. The wording is taken from the review.
3. **Basis for the DAO participation figures (Part 2A).** The single "~5% of token supply" figure is replaced with the basis each study uses: ~5% of tokens cast per proposal [R10, Falk et al.]; ~5% of total token supply on average and 34% of delegated tokens [R11, SoK: Attacks on DAOs]; low participation among token holders, without a single percentage [R9, Messias et al.]. These bases were read from the arXiv papers themselves. The 34% figure is the "higher share of already-delegated tokens" the review refers to, now attributed to its source.
4. **CIP framework [R19] rescoped.** DD43, the Part 2A table, and the reference entry now describe it as a framework, mainly for group-level digital twins, to be *extended* to individual Avatar fidelity. It is no longer presented as an existing benchmark for individual Avatars.
5. **Daily Beast [R3] demoted.** MIT Technology Review [R1] and Crowd.Law [R2] support the 26-case and ~80% figures. The stall is now "later coverage describes a post-2018 stall [R2, R3]", so Crowd.Law is the lead citation and the Daily Beast is secondary.
6. **Related Work shortened.** It was ~1,400 words of prose and is now a table plus a short "Engagement with Hidalgo" section, ~730 words. Some detail was cut to fit, for example Google Votes, the Habermas Machine replication, and the Argyle/Bisbee prose. **Orphaned reference removed:** Google Votes (old R7). References R8–R25 were renumbered R7–R24 throughout the paper. Every remaining reference is cited at least once in the body.
7. **Item #9 relabelled** "Partly fixed (no owners or working groups assigned yet)", as noted above.

**Raised by the Critic but not changed (not in the requested edit list):** Part 8 still says "the most promising route to binding influence". The Critic suggested "One plausible route…". Also unchanged: S6 (~$7T), S7/S8 (line-item veto summary), R4 (Sciarini & Tresch) and Google Votes, which the Critic marked unverified. Google Votes has now been removed.

### Post-review edits (citation verification, 4 Nov 2026)

Confirmed corrections only. Applied after checking primary/reputable sources for S6, S7/S8, and R4.

1. **S6 / H5 — US federal spending figure**
   - **Old (H5):** "under 1% of current US federal spending of roughly $7 trillion a year [S6]"
   - **New (H5):** "under 1% of US federal outlays of $7.010 trillion in fiscal year 2025 [S6]"
   - **Old (S6):** Committee for a Responsible Federal Budget, "Explaining Federal Spending and Revenue." https://www.crfb.org/papers/explaining-federal-spending-and-revenue
   - **New (S6):** U.S. Department of the Treasury, Bureau of the Fiscal Service, *Combined Statement of Receipts, Outlays, and Balances of the United States Government* for Fiscal Year 2025: total outlays $7,010.0 billion ($7.010 trillion). https://fiscaldata.treasury.gov/static-data/published-reports/combined-statements/cs2025/2025_cs-final.pdf
   - **Verdict:** Needs correction. "Roughly $7 trillion" was in the right ballpark but unnamed fiscal year and secondary source. FY 2025 Treasury Combined Statement reports $7,010.0 billion. (FY 2024 was about $6.75T Treasury / ~$6.8T CBO.)

2. **S7 / S8 — line-item veto (H11)**
   - **Paper claim checked:** "The closest analogue, line-item vetoes in US states, has not shown reliable long-run reductions in spending [S7, S8]."
   - **Body text:** Unchanged (claim confirmed).
   - **Old (S7):** NBER Working Paper w2531 (line-item veto and state spending). https://www.nber.org/papers/w2531
   - **New (S7):** Douglas Holtz-Eakin, "The Line Item Veto and Public Sector Budgets: Evidence from the States," NBER Working Paper 2531 (1988); also *Journal of Public Economics* 36 (1988): 269–292. Finds long-run state budgets are not altered by an item veto. https://www.nber.org/papers/w2531
   - **Old (S8):** Federal Reserve Bank of Richmond, *Econ Focus* (Spring 2009), line-item veto feature. https://www.richmondfed.org/-/media/richmondfedorg/publications/research/econ_focus/2009/spring/pdf/feature4.pdf
   - **New (S8):** David A. Van den Berg, "Veto Politics: Can a Line-Item Veto Reduce Spending?," Federal Reserve Bank of Richmond *Econ Focus / Region Focus* (Spring 2009): 30–31. Summarizes Holtz-Eakin: no statistically significant long-run effect on budget size. https://www.richmondfed.org/-/media/richmondfedorg/publications/research/econ_focus/2009/spring/pdf/feature4.pdf
   - **Verdict:** Claim correct; references needed fuller bibliographic detail. **Not in the paper / not used as H11 support:** Line Item Veto Act of 1996 (enacted 9 Apr 1996, effective 1 Jan 1997); Clinton used the authority 82 times across 11 appropriations laws; *Clinton v. City of New York*, 524 U.S. 417 (1998), 6–3, struck the Act down under the Presentment Clause (Art. I, § 7, cl. 2). Those federal facts are historically correct but address constitutionality of unilateral presidential cancellation of parts of statutes, not long-run spending reduction from state item vetoes — so they do not support H11's argument and were left out of the body.

3. **R4 — Sciarini & Tresch**
   - **Old (R4):** Sciarini & Tresch, campaign effects in Swiss direct democracy. https://access.archive-ouverte.unige.ch/access/metadata/b592c575-240d-4e06-8b19-3373ff8a84bd/download
   - **New (R4):** Pascal Sciarini & Anke Tresch, "Campaign Effects in Direct-Democratic Votes in Switzerland," *Journal of Elections, Public Opinion & Parties* 21, no. 3 (2011): 333–357. https://doi.org/10.1080/17457289.2011.588334 ; accepted manuscript: https://archive-ouverte.unige.ch/unige:17305
   - **Related Work table:** Split the key-finding attribution so campaigns/party coalitions cite [R4] and turnout/complexity cite [R5] (Goldberg et al.), matching what each paper actually shows.
   - **Verdict:** Needs bibliographic correction. Authors/title/venue/year confirmed from the accepted manuscript. Abstract and body confirm strong campaign effects conditional on party-coalition type, intensity, and direction — supporting H14 / Related Work on parties and campaigns shaping Swiss issue votes. Turnout/complexity belongs to R5, not R4.

