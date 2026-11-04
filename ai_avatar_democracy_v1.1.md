# AI Avatar Democracy — A White Paper for Discussion

**Version 1.1 — A working proposal for legislative reform through AI-mediated direct democracy**

*v1.1 revises v1.0 after an adversarial red-team review and an evidence review of related work. The main changes are listed in the Document History appendix and in the separate changelog (`CHANGES-v1.1.md`).*

---

## Preface

As a long-run end state, this document proposes replacing the voting function of the lower legislative chamber of representative democracies. In its place, each eligible citizen would have a personal AI Avatar that votes on legislation according to the citizen's delegated preferences. The proposal grew out of conversations about dissatisfaction with representative democracy in many established democracies, and about why that dissatisfaction may not respond to electing different individuals within the existing system.

This is a working document, not a finished design. It names many problems it cannot solve and several it solves only partly. In a few places it may offer real improvements over current systems. It is offered for critique and refinement, not for immediate implementation.

The document was developed through iterative dialogue, and it bears the marks of that process. Strong claims have been tested and revised, weak arguments have been removed, and real limitations are stated openly. It reflects the views of its authors and should be read as a serious thought experiment, not a settled position.

**What changed in v1.1.** External review found that v1.0 sometimes spoke with more confidence than its evidence allowed. It had a section titled "Confirmed Strengths," three unsourced numbers, a speculative COVID exercise presented as findings, and several examples that readers could reasonably read as partisan. v1.1 restates the benefits as hypotheses and removes or labels the unsourced numbers. It replaces the loaded examples, adds a Related Work section, and makes a non-binding advisory stage the explicit first step. Claims that are still unverified are marked as such.

---

## Part 1: The Problem

Representative democracy in many established democracies faces several structural challenges. Electing better individuals within the existing framework may not solve them:

**The agency problem.** Voters choose representatives every 2–6 years from pre-packaged platform bundles. Once elected, representatives face incentives that can pull away from their voters' interests: campaign donors, party leadership, relationships with lobbyists, and their careers after office. This is the standard principal–agent framing. How large the gap is varies by country, and this paper does not try to measure it.

**The bundling problem.** Voters cannot express preferences on individual issues. They choose between candidate packages that combine positions on dozens of unrelated questions. A voter who agrees with Party A on economics and Party B on social issues has no meaningful way to express that combination. In most two-party systems, both major parties are uneasy coalitions rather than coherent worldviews. (Unbundling is also the core motivation of Hidalgo's "augmented democracy" proposal; see Related Work.)

**The attention problem.** Modern legislation is long and technical. In the US Congress, very large omnibus bills are routine rather than exceptional. GovTrack documented a roughly 2,232-page spending bill in 2018 that members had roughly 18–28 hours to read before voting [S1], and The Economist has charted how US spending bills keep getting longer [S2]. Citizens have no realistic way to track what their representatives do. Representatives have no realistic way to engage seriously with the volume of legislation they vote on.

**The capture problem.** Concentrated interests organize more cheaply than diffuse publics. Whether economic, occupational, ideological, identity-based, or foreign-state, they can shape policy by influencing relatively few decision-makers [S3]. This applies across the spectrum: corporate and trade lobbies, labor organizations, advocacy groups of every orientation, and foreign governments all use the same channels. Influencing the 535 members of the US Congress is plausibly cheaper than influencing 200 million voters directly. We have no measured cost model for that ratio, though, and mass influence through media and platforms is itself cheap (see Part 6).

**The temporal problem.** Major information about candidates often surfaces in the final weeks before an election. That creates a high-stakes information environment that can reward manipulation, selective disclosure, and manufactured controversy. Decisions made in 30 days bind governance for 4 years.

These problems are not unique to any one country or political tradition. Large cross-national work finds that trust in *representative* institutions (parliaments, governments, parties) has declined in many democracies since around 1990, but the decline is not uniform. Trust in *implementing* institutions, such as the police and civil service, is often stable or rising, and several countries buck the trend [S4]. In the United States, trust in the federal government has been low by historical standards for decades [S5].

This proposal does not claim to solve all of these problems. It targets specific ones and accepts that others will persist.

---

## Part 2: Core Proposal

### Definitions

- **AI Avatar (or Avatar Politician):** An AI agent that votes on legislation on behalf of one citizen. One per eligible voter.
- **Delegate:** A person, institution, or information source the citizen has told their Avatar to follow on specific issues.
- **Citizen / Principal:** The human voter who configures and oversees their Avatar.
- **Proxy Chamber:** All Avatars taken together, replacing the voting function of the lower legislative chamber (end state only; see "Transition versus end state" below).
- **Endorsement:** An Avatar's signal that a proposed bill should go to the Proxy Chamber for a vote.

### The basic design

Each eligible voter has a personal AI Avatar. The citizen configures it with:

- **Per-issue delegations** ("On fiscal policy, vote according to Senator X's positions; on environmental issues, follow Scientist Y's analysis; on social issues, follow my stated values")
- **Stated values and priorities** (set up initially with something like a political compass tool, then expanded through ongoing interaction)
- **Override authority**: the citizen can override their Avatar on any specific vote in real time

When legislation comes to a vote, each Avatar votes according to its principal's delegated preferences. In the end state, the aggregate result replaces the lower chamber's vote on the bill.

In this initial proposal (v0.1), the second chamber, executive branch, and judiciary stay as they are. Their relationship to the Proxy Chamber is the same as in current bicameral systems.

### Transition versus end state

v1.0 blurred two different things. The end state is a Proxy Chamber that replaces the lower chamber's vote, which requires constitutional change. The transition works inside existing institutions. v1.1 separates them into three stages. Moving from one stage to the next depends on evidence, not on a calendar.

- **Stage 0: Advisory / shadow chamber.** An unofficial, non-binding Avatar Assembly runs in parallel with the real legislature. It predicts how its users would have voted on real bills and publishes the totals next to the actual roll-call result. This is close to what Hidalgo proposes (see Related Work). It needs no change in law, it makes agreement between Avatars and citizens measurable, and it can fail cheaply.
- **Stage 1: Pledged representatives (public whip).** Candidates run on a pledge to follow Avatar mandates (the Avatar Party, Part 8). The mandate is a published instruction, a whip, not a legal one. Mandates only count when they come from users at or above the published verification tier (Decision 42, Part 8).
- **Stage 2: Formal adoption.** A jurisdiction passes a law or amends its constitution to give a Proxy Chamber formal voting authority. At first this might cover only a limited class of bills, or take an advisory-plus-veto form. This stage should be attempted only after Stage 0–1 evidence meets published thresholds for preference fidelity, identity integrity, and resistance to concentration (see Part 6 and Related Work).

Safeguards that assume the system already has legal force (e.g., Design Decisions 20 and 32) apply only at Stage 2. During Stages 0–1 those protections do not exist, and that is a real vulnerability (Part 6).

### What this is and isn't

This is **not**:
- AI making policy decisions on its own
- Replacement of human judgment with machine judgment. It *does*, however, put a machine interpreter between the citizen's judgment and the recorded vote. We treat that as a disclosed agency relationship (Part 3, H1), not as the citizen casting a ballot personally
- Removal of human politicians from government in general
- A solution to political polarization, tribal media, or culture war
- A solution to problems in the executive branch and administrative governance

This **is**:
- A reform of the legislative voting process
- A potentially more direct expression of citizen preferences than periodic elections allow, *if* Avatars turn out to be faithful to their principals (an open empirical question)
- A way to scale citizen engagement with legislation that is impossible today
- An attempt to reduce specific failures of representative government while accepting that others will persist

---

## Part 2A: Related Work

Avatar Democracy draws on real experiments and research. Some of it supports the case, and several findings cut against it. The table summarizes the cases. Hidalgo, the closest relative, is discussed below it. Sources are in Appendix A.

| Case | Domain | What | Key finding | Lesson | Sources |
|---|---|---|---|---|---|
| vTaiwan / Pol.is | Legislative consultation (Taiwan) | Pol.is opinion-mapping plus offline meetings; non-binding | ~26 cases by 2018; ~80% led to decisive government action (e.g., Uber). Later coverage describes a post-2018 stall | Consensus tools fade without a binding hook. Not bill drafting (DD7) | R1, R2; stall: R2, R3 |
| Swiss direct democracy | National referendums and initiatives | Issue votes layered on a representative system | Campaigns and party coalitions shape outcomes [R4]. Turnout is selective and falls with complexity [R5] | Parties and campaigns move rather than disappear (H14) | R4, R5 |
| LiquidFeedback (Pirate Party) | Liquid democracy inside a party | Per-issue revocable delegation to humans | Power-law voting weight. Removing delegations would change ~1 in 6 outcomes. In formal models, delegation need not beat direct voting | Expect super-delegates. Revocability alone does not prevent concentration (H10) | R6, R7, R8 |
| DAOs (Compound, Uniswap, others) | On-chain governance | Token-weighted voting and delegation | ~3–5 voters often cast half the votes [R9]. Participation bases differ: ~5% of tokens cast per proposal [R10]; ~5% of total supply, 34% of delegated tokens [R11]. Apathy enables attacks [R11] | Low participation concentrates power, whoever casts the votes | R9–R11 |
| Habermas Machine | AI-mediated deliberation | AI drafts group statements from private opinions | 5,734 UK participants preferred its statements to human mediators'. It reduced division within groups | Evidence favors AI as mediator, not proxy. We lack a deliberation step (Part 6) | R14 |
| Collective Constitutional AI | Public input into model values | ~1,000 representative US adults drafted principles via Pol.is | Model matched the baseline and showed lower bias on the dimensions tested | Relevant to DD22–26, not to voting on statutes | R15, R16 |
| Silicon sampling | LLM preference simulation | Persona-conditioned LLMs reproduce opinion patterns | Averages are often matched, but variance collapses and inferences are unstable | Individual fidelity is unproven. CIP's framework is a starting point to extend (DD43) | R17, R18, R19 |
| Demoex, Flux, Net Party | Mandate-bound parties | Representatives vote as online members decide | Demoex won municipal seats (~1.7–2.9%), then quit. Flux (~0.15%) was deregistered. Net Party (~1%) won no seat | Local seats are possible. None scaled or published compliance (Part 8). Secondary sources | R20–R22 |
| Estonia | National e-government | eID, X-Road, i-Voting since 2005 | 51% of 2023 parliamentary votes were cast online | Identity infrastructure is solvable. This says nothing about Avatars | R23, R24 |

### Engagement with Hidalgo

César Hidalgo's augmented democracy uses digital twins to let people "participate directly in a large volume of democratic decisions" [R12]. His diagnosis matches ours: the bottleneck is "the limited time and cognitive bandwidth of people," not communication. Gudiño, Grandi and Hidalgo found that fine-tuned language models predicted individual Brazilian participants' policy choices better than a left/right bundling rule [R13].

Hidalgo argues explicitly against our end state. His "proposal is not to replace our current democracy." Experimental systems should be "by definition, unofficial, meaning that the decisions made by the agent would not be enforceable and have no bearing on official policy." The idea "is never to offload the democratic process to a senate of digital twins." In 2019 he judged that "our technology is not yet ready for creating competent digital twins," and he recommended open protocols over platforms [R12].

We accept most of this:
- Binding deployment without non-binding evidence would be reckless. A Hidalgo-style advisory chamber is therefore the mandatory first stage (Stage 0), and any binding authority requires published evidence thresholds to be met first.
- Building on protocols rather than platforms is the strongest available answer to concentration in clouds and certifiers (Design Decision 40).
- We do not accept that non-binding status must be permanent. Hidalgo describes a "dial" that can turn from almost no decisions to most of them [R12]. The burden of proof for turning it is ours, and nothing here meets it yet.

---

## Part 3: Expected Benefits (Hypotheses)

v1.0 called this section "Confirmed Strengths." That was wrong: none of these benefits has been observed in a deployed system. Each item is restated here as a hypothesis, with its basis (**logic**, **analogy**, or **needs evidence**) and, where possible, a way to test it. All of them are subject to the narrower claim in Part 9. The original numbering is kept for traceability. Two v1.0 items were moved or withdrawn.

**H1. Legitimacy through disclosed agency.** *Hypothesis:* An Avatar vote can carry democratic legitimacy as **disclosed agency**. The citizen chose the configuration, can see what the Avatar did, and can easily override it. The chain of authorization is transparent rather than fictional, as it arguably is with a representative elected on a bundle of positions. *Qualification:* This is **not** the same as a referendum ballot. Suppose the citizen never reviewed an auto-vote, and a model cast it by approximating their delegates and values. That vote should not carry the moral weight of a ballot the citizen cast in person. The more a vote rests on machine interpretation, and the less on explicit instruction or review, the weaker its claim to legitimacy. *Basis:* logic (a normative argument). *Test:* in Stage 0, measure how often citizens review, override, or reject their Avatar's votes, and whether they see those votes as their own.

**H2. Equal national vote weight.** *Hypothesis:* One citizen, one Avatar, one vote removes gerrymandering and district-based distortion in the chamber it applies to. *Qualification:* This trades geographic federalism for population equality. Many constitutions deliberately give territory extra weight, through upper chambers or federal councils, partly to protect regional minorities. Removing that is a choice of regime, not a free upgrade, and hybrid designs remain possible. *Basis:* logic (true by design for the Proxy Chamber). Whether it is desirable is contested.

**H3. Spread-out resistance to lobbying (conditional).** *Hypothesis:* Suppose Avatars really do run on diverse clients and models, controlled by many households and providers. Then classic targeted capture (bribery, revolving doors, privileged access) becomes much harder than capturing a small legislature. *Qualification:* This holds only if that diverse deployment actually happens (Design Decisions 34, 38–40). The default market outcome may be a handful of cloud apps, which would concentrate power instead. It also does not hold against mass influence through media and influencers, bias in training or fine-tuning, or prompt injection. None of those requires corrupting each Avatar individually. Evidence from liquid democracy and DAOs shows power concentrating quickly when participation is low (Related Work). *Basis:* analogy (concentrated-interest theory [S3]); needs evidence. *Test:* in Stages 0–1, measure provider concentration, delegate concentration, and the reach of influence operations.

**H4. More bill text actually processed.** *Hypothesis:* Avatars can process far more bill text than legislators usually read, including long omnibus bills, and can flag provisions for citizens. *Qualification:* Whether they process it *faithfully* is an open engineering problem (Part 6). That means no hallucinations, no missed hidden provisions, and resistance to instructions injected into the text. *Basis:* logic plus the current ability of models to handle long documents; needs evidence. *Test:* red-team benchmarks using real bills with planted provisions and injection attempts.

**H5. Affordable cost (unknown).** *Hypothesis:* The infrastructure may be affordable relative to national budgets. *Qualification:* v1.0 estimated "tens of billions of dollars annually" for a country the size of the US. No model stood behind that figure, and it is **withdrawn**. If the cost did fall in that range, it would be under 1% of US federal outlays of $7.010 trillion in fiscal year 2025 [S6]. That checks only the arithmetic, not the estimate itself. A real estimate needs a published model. It has to cover running multiple models on every bill section for every citizen, identity infrastructure, audits and red-teaming, security, dispute resolution, and liability. Affordability is a hypothesis, not a finding. *Basis:* needs evidence.

**H6. Auditability.** *Hypothesis:* Every Avatar vote can be logged and verified. That is more traceable than voice votes, unanimous consent, and unrecorded committee actions. *Qualification:* Publicly attributing each vote to a named citizen conflicts with the secret ballot and creates risks of coercion and blackmail (Part 6). The design goal is votes that the public can *verify but not attribute*. Whether that can be done at scale is unverified. *Basis:* logic; needs design work.

**H7. Step-by-step deployment.** *Hypothesis:* The system can be tested in parallel or in a limited form, expanded if it works, and rolled back if it doesn't. v1.1 makes this concrete through the staged path in Part 2. *Basis:* logic. Hidalgo's proposal for experimental systems is a direct precedent for Stage 0 [R12].

**H8. Fewer barriers to participation.** *Hypothesis:* Once configured, an Avatar lowers the time cost of taking part in many decisions. That could reduce the under-representation of citizens whose lives leave no room for constant political engagement. *Supporting context:* participation in direct digital democracy platforms has been very low; Hidalgo cites Decide Madrid and Senador Virtual [R12]. *Qualification:* Setup and oversight are not free. The time and digital skills needed to configure an Avatar are unequally distributed. Delegation with little attention can also slide into rubber-stamping or concentration in a few super-delegates (Related Work; Part 6). *Basis:* logic; needs evidence. *Test:* in Stage 0, compare the demographics of people who actively configure and override their Avatars with those of the electorate.

**H9. (Moved to Part 4.)** v1.0's "crisis response not made worse" was a constraint (the system should not make things worse), not a benefit. It now appears as Limitation 8.

**H10. Revocable delegation.** *Hypothesis:* Citizens can withdraw a delegation instantly, unlike fixed electoral terms or permanent bureaucratic tenure. That gives them faster recourse when a delegate loses their trust. *Qualification:* Revocability alone does not prevent concentration. In LiquidFeedback, delegation produced a few super-voters with very large weight (a power-law distribution) [R6]. Formal analyses find that delegation need not improve outcomes compared with direct voting [R7, R8]. Delegates who win large audiences can themselves be captured. *Basis:* logic. Evidence from human liquid democracy ranges from contested to negative. *Test:* in Stage 0, measure delegate concentration (for example, how many delegates control a majority of the delegated weight) and how quickly citizens revoke delegations.

**H11. Per-section voting.** *Hypothesis:* If each section must pass on its own, "Christmas tree" bundling becomes more costly. (Christmas tree bills attach unpopular provisions to popular ones so they pass together.) *Qualification:* This does not *eliminate* strategic packaging. Drafters can rename, nest, or cross-reference provisions, and the second chamber or the executive can bundle them again. Losing logrolling can also block package deals that benefit everyone (see the gridlock risk in Part 6). The closest analogue, line-item vetoes in US states, has not shown reliable long-run reductions in spending [S7, S8]. *Basis:* logic; needs evidence.

**H12. Pressure on remaining institutions (direction uncertain).** *Hypothesis:* The visible contrast between transparent Avatar voting and the opaque processes of the second chamber could build pressure for further reform. *Qualification:* It could just as easily push the second chamber to dig in or shut the experiment down. Which way it goes depends on circumstances. *Basis:* speculation.

**H13. Less leverage for election-timed information (open question).** *Hypothesis:* A continuous system holds thousands of legislative votes, so any single information event may have less leverage than one that lands just before a single high-stakes election. That could weaken incentives to time, suppress, or manufacture disclosures around elections. *Qualification:* No empirical study supports this causal claim; it is unverified. Control over concentrated media and delegate channels may become *more* valuable in a continuous system, not less. (v1.0 illustrated this point with one contested US episode. That example has been removed, because a single partisan-coded case cannot establish a general mechanism.) *Basis:* speculation; needs evidence.

**H14. Fewer coordination points centered on politicians (speculative).** *Hypothesis:* Taking legislators out of the chamber's vote removes one point around which parties coordinate their cues. *Qualification:* The Swiss evidence suggests parties and campaigns shift their effort to issue votes rather than disappearing [R4]. Media figures and delegates will also take on coordinating roles. *Basis:* speculation; needs evidence.

**H15. Less performative voting (speculative).** *Hypothesis:* Configuring an Avatar in private may yield slightly more honest votes than legislators publicly performing group identity. Any effect is likely to be modest. *Basis:* speculation. It conflicts with any design that makes each citizen's configuration public (H6).

**H16. (Withdrawn.)** v1.0 claimed "structural party-capture resistance": a party formed around the Avatar mandate would have nothing to capture. That contradicted Part 8, where the bootstrap *is* a party apparatus, with candidate recruitment, compliance scoring, primary challenges, and branding. The Avatar Party moves capture to candidate selection, client software defaults, verification vendors, and model certifiers. It does not eliminate party-shaped power. This point now appears as Limitation 9 and as a problem in Part 6.

---

## Part 4: Honest Limitations

The system does **not** solve, and should not be claimed to solve, the following:

**1. Administrative and executive governance.** Much consequential government action never passes through a legislature: regulatory rulemaking, agency grants, executive orders, carrying out foreign policy, military decisions. The Avatar system has no leverage over any of it. Examples include drug approvals by medical regulators, defense procurement, central-bank operations, and research grants from science agencies. All of these are administrative and would be unaffected.

**2. Tribal polarization among citizens.** Citizens still form tribal identities, follow tribal media, and choose their delegates along tribal lines. Polarization moves; it does not go away.

**3. Influencers and audience capture.** Citizens may delegate en masse to high-reach figures across the spectrum: podcast hosts, prime-time broadcasters, and religious, union, and community leaders. Influence could then concentrate in people who win audiences rather than people who win elections. Evidence from liquid democracy suggests this concentration is likely, not just possible [R6]. Continuous democracy may lower the stakes of any single moment, but the dynamic persists.

**4. Funding loops between interest groups and policy.** The mechanism changes, from donations in exchange for policy to endorsements in exchange for policy. The loop between organized interests and policy outcomes remains.

**5. Underlying scientific and factual uncertainty.** The system does not make hard policy questions easier. It changes who decides, not what is true.

**6. Quality of public discourse.** The system does not directly address media polarization, social media dynamics, or other problems in the information ecosystem. It also has no built-in step for deliberation (compare the Habermas Machine in Related Work).

**7. Who counts as "us."** Non-voters, minors, and future generations are no more represented in Avatar voting than in current systems.

**8. No improvement in crisis response** *(moved from v1.0 Strength 9)*. Executive branches already make most crisis decisions, often bypassing legislatures, and the Avatar system inherits this. The design tries not to make it worse, but that is a constraint, not a benefit.

**9. Party-shaped power moves rather than disappears** *(replaces v1.0 Strength 16)*. During the bootstrap, the Avatar Party is itself a party, with recruiters, brand managers, and enforcers. Afterwards, power sits with the defaults in client software, verification providers, certifiers, and large delegates. All of them are targets for capture.

These limitations are real and significant. The honest case for the Avatar system is "possibly a better legislative process within an otherwise unchanged political environment," not "a cure for democratic dysfunction."

---

## Part 5: Design Decisions

Decisions 1–37 keep their v1.0 numbering. Edits are marked *(v1.1)*. Decisions 38–43 are new.

### Avatar configuration

1. The Avatar layer separates the citizen from the underlying foundation model. Avatars are configured with specific delegations and stated values. They do not answer policy questions with raw foundation-model output.
2. Delegation chains can be set per issue, so citizens can trust different sources for different domains.
3. Citizens can override their Avatar on any specific vote in real time.
4. Changes to preferences are rate-limited asymmetrically. Adding a new delegation involves a cooling-off period of at least a week; revoking trust takes effect immediately. The system is slow to grant authority and quick to withdraw it.
5. Every vote carries equal weight, regardless of how engaged the citizen has been recently. Weighting by engagement was considered and rejected because it would favor activists over ordinary citizens.

### Bill lifecycle

6. Open proposal rights. Any verified citizen can propose a bill, and senators and the executive can also sponsor bills. *(v1.1)* To limit flooding, proposals are rate-limited per verified identity. A proposal needs a small number of co-sponsors or a refundable deposit before it enters the endorsement pool. Exact parameters are still open.
7. GitHub-style versioned drafting. Bills are drafted in open, editable repositories and stay publicly visible throughout the process. *(v1.1)* This is inspired by vTaiwan's consultation experiments. v1.0's claim that vTaiwan "demonstrated effectiveness" for this model is withdrawn. vTaiwan is a non-binding Pol.is consultation process, not versioned bill drafting, and its use for major decisions stalled after around 2018 (Related Work).
8. Endorsement thresholds are tiered by the scope of the bill: roughly 50,000 endorsements for ordinary legislation, 500,000 for major reforms, and 5,000,000 for constitutional-level changes. (These numbers are illustrative for a country the size of the US and would be scaled to population.)
9. A mandatory cooling-off period of 2–4 weeks separates draft freeze from the proxy vote. This leaves time for analysis, for opponents to organize, and for Avatars to consult delegates.
10. An optional structured review filter. Bills can opt into formal review, and citizens can configure their Avatars to vote only on bills that completed it. This creates market pressure for procedural legitimacy without mandating it.
11. Every bill is voted on section by section. Each section must meet the passage threshold on its own for the bill to pass. *(v1.1)* Sections may declare explicit dependencies ("this section takes effect only if section N passes"), so that packages that only work together can be voted on together. This addresses part of the gridlock risk in Part 6.
12. Path to final passage: Proxy Chamber vote → second chamber review → executive signature. The last two steps stay as they are in v0.1.

### Endorsement mechanics

13. Hybrid endorsement model. Avatars can endorse bills on the citizen's behalf, based on their configuration. Citizens are notified and have 24 hours to revoke. Notify-with-revocation is the default; each citizen can switch to manual approval or fully automatic endorsement. *(v1.1)* Notification fatigue can quietly turn this default into unreviewed automation (Part 6). Review rates should be published, and a high share of unreviewed endorsements should count against a bill's legitimacy signals.
14. Identity verification through eID. Each Avatar is bound to a verified citizen. This is needed to resist fake accounts (Sybil attacks) and involves privacy trade-offs that must be addressed.
15. Endorsement transparency. Endorsement patterns are publicly visible and analyzed for coordinated or suspicious activity. *(v1.1)* Data should be published in aggregate or pseudonymous form wherever possible, because publicly attributing endorsements to individual citizens creates a risk of coercion (Part 6).
16. Random sample verification. The system contacts a random subset of endorsers to confirm their endorsements are genuine.
17. Detection of bots and anomalies in endorsement patterns.

### Audit and oversight

18. Public, standardized audit logs that researchers, journalists, and citizens can access. *(v1.1)* Logs should be tamper-evident and verifiable without being publicly attributable to individual citizens.
19. Independent, competing AI summary services write neutral plain-language summaries of every bill and flag hidden provisions. These services are part of the audit infrastructure, not a central authority. *(v1.1)* Summaries must quote and link the bill text they rely on, so claims about provisions can be checked against the source.

### System governance

20. Once the system is established, dismantling it requires a supermajority of roughly two-thirds of Avatars, similar to the thresholds for constitutional amendments. *(v1.1)* This applies only at Stage 2 (formal adoption). Stages 0–1 have no such protection.
21. Open-source foundation models with public weights. This is necessary for trust but not sufficient.

### Capture defenses

22. Any model approved for Avatar use must document and disclose where its training data came from.
23. Approved models must be trained on diverse corpora, not just be different models trained on the same corpus.
24. Mandatory disclosure of RLHF (reinforcement learning from human feedback), including the demographics of labelers and the instructions they were given.
25. Citizens can choose among several model variants that were RLHF-trained independently.
26. Continuous, independent red-team testing of political reasoning, with published findings.
27. Defenses against prompt injection in bill processing. Processing is sandboxed, and bills use standardized formats that Avatars treat as data, not instructions.
28. Each bill is processed by models with different architectures, as redundancy against weaknesses specific to one architecture.
29. Delegate authentication. Avatars confirm they are following the actual delegate, not an impersonator.
30. Delegate drift detection. Citizens are notified when a delegate takes an unusual position or shifts sharply.
31. Detection of anomalies in Avatar reasoning patterns at scale.

### Maintainer governance (layered defense)

32. **Constitutional authority.** Once formally adopted (Stage 2), the system is protected by constitutional or equivalent status, and fundamental changes require a supermajority. *(v1.1)* This does not protect Stages 0–1.
33. **Independent oversight body with multiple stakeholders.** Members come from executive appointment, legislative selection, random selection (sortition) from technical and civil-society pools, and limited international observation. Terms are fixed, staggered, and non-renewable (e.g., 9 years). The body can only certify or decertify models for Avatar use; it does not build or modify them. *(v1.1)* See Decision 38: no single certifier should be able to decide on its own who can take part.
34. **Multiple competing implementations.** Models and Avatar software are built by competing organizations: open-source consortia, commercial providers, government laboratories, and international providers. Citizens choose their providers. *(v1.1)* v1.0 limited international providers to "allied democracies." That restricts supply along geopolitical lines, so it is now an open question rather than a rule.
35. **Mandatory transparency infrastructure**, as set out in the capture defenses (22–26).
36. **Cryptographic guarantees.** Reproducible builds, model updates signed by multiple parties, threshold signatures for deployment, and public ledgers of changes. *(v1.1)* These protect the *integrity* of builds. They say nothing about whether the certifier's decisions are legitimate.
37. **Defenses at the citizen level.** Citizens can run several Avatars from different providers, audit their own Avatar's reasoning, switch providers instantly, and override any vote.

### New in v1.1

38. **More than one certifier.** At least two independent certifiers, with rules for recognizing each other's decisions. Decertification by one certifier should not remove a model from use on its own; it should trigger a public notice and a review.
39. **The right to vote never depends on a vendor.** A citizen can always vote directly, or through an uncertified open-weights client that is labeled as such, without losing their vote. Certification affects defaults and labels, not the right to take part.
40. **Protocol, not platform.** Avatars and personal preference data are built on open protocols, in the spirit of Hidalgo's recommendation for personal data stores [R12]. No single cloud is the default, and published limits cap any one provider's share of active Avatars.
41. **Delegation only where grounded.** An Avatar may follow a delegate on a section only if the delegate has published positions that cover it. Otherwise it abstains or asks the citizen. It must never guess a public figure's views from a model's training data. *(Raised in the code review of the prototype, where the Avatar falls back on a delegate's "known public positions" taken from the model's own knowledge.)*
42. **Political weight depends on the published verification tier.** Until a published verification tier is met (eID-grade binding is the default bar; whether weaker methods can qualify is an open question), Avatar tallies are civic signals only. They do not create voting obligations for candidates or feed compliance scores (Part 8).
43. **Fidelity benchmarks before authority.** Before any Stage 2 authority is granted, Avatars must pass group and individual fidelity benchmarks: accuracy against citizens' own choices, preserved variation in views, and representativeness across groups. No such benchmark for individual Avatars exists yet; one starting point is to extend CIP's Digital Twin Evaluation Framework [R19] from group-level digital twins to individual Avatar fidelity. Results are published for each provider.

---

## Part 6: Live Problems Requiring Further Work

### Serious problems

**Maintainer governance under sustained capture pressure.** The layered defense (decisions 32–40) raises the bar for capture but does not eliminate it. Several risks remain: slow capture spread across many actors and many years, consensus capture in which every approved provider shares the same blind spots, and citizen apathy weakening oversight.

**Concentration in certifiers, clouds, and app stores (new).** Whoever certifies models effectively decides who can vote through an Avatar. If most citizens use one of a few cloud apps, those vendors become a de facto chamber. v1.0 also called for "national-security-grade governance" of the infrastructure (Part 7) while claiming that Avatars run in "millions of households" (H3). Those two pull in opposite directions. v1.1's position: requirements for *security* (build integrity, signing, audit) should meet the standard for critical infrastructure. *Control* over which Avatars may run must stay in many hands (Decisions 38–40). We have not shown that this combination is stable, and until we do, H3 stays conditional.

**Preference fidelity (new).** The whole proposal assumes that an Avatar votes the way its principal would. Silicon-sampling research shows that language models can reproduce average opinion patterns while collapsing the variation in views and producing unstable conclusions [R17, R18]. Errors that cancel out on average can still misrepresent particular individuals and minorities. This has never been tested on individual citizens voting on real legislation.

**Correlated AI errors across the population on novel issues.** Current legislatures fail in ways that are scattered and visible. The Avatar system might fail in ways that are concentrated and invisible. That could happen when many Avatars face a novel issue with no existing delegations and reason through it with similar foundation models. Revocability may allow some self-correction over a few weeks, but the size of this risk is unknown. *(v1.1: v1.0 said the COVID exercise showed this risk to be "smaller than feared." That statement has been removed, because the exercise was not evidence.)*

**Human legislators cannot be bound during the bootstrap (new).** In Stage 1, enforcement is only political, reputational, and brand-based (Part 8). Soft enforcement is weakest exactly when a mandate conflicts with donors, coalition partners, or a legislator's political survival. No legal doctrine today binds a representative to outside instructions, and many constitutions protect a representative's free mandate. Compliance is an empirical unknown.

**Coerced configuration and exposure of preference data (new).** Households, employers, communities, parties, or states may pressure citizens over how they configure their Avatars, or check that they have done so. A secret ballot protects against this; a persistent configuration that others can inspect does not. Together, eID, stated values, and audit logs create a new surface for intelligence gathering. A breach could expose millions of detailed political profiles to blackmail or targeting. Decisions 15 and 18 reduce attribution, but the core tension between auditability and secrecy is unresolved.

**Resistance to fake accounts and astroturfing in endorsements.** Identity verification, transparency, sampling, and anomaly detection are partial solutions that need serious engineering investment. The problem is most acute in Stages 0–1, where verification is weak (Part 8).

**Adversarial bill flooding.** Hostile actors could flood the system with bills designed to waste attention, slip in harmful provisions, or win through procedural tricks. AI summary services, per-section voting, and proposal rate limits (Decision 6) defend against this in part. The threat is not fully mitigated.

**Capture of foundation models (several routes).** Provider corruption, RLHF bias, prompt injection, training data poisoning, and manipulation of delegates are all real routes of attack with only partial defenses. The capture defenses (22–31) address them but require extensive, ongoing engineering. *(v1.1)* A related failure: an Avatar "follows" a delegate whose views it has inferred from training data rather than from published positions. In practice that means following the model vendor's picture of that person (Decision 41).

**Gridlock from per-section voting, and removal of majoritarian filters (new).** Per-section voting (H11) may block package deals that only work as a whole, which could leave large omnibus legislation more stuck than bundling ever did. Removing geographic weighting and filtering through representatives (H2) also removes protections that some constitutions deliberately give to regional and other minorities. These are trade-offs, not free gains. We have not yet analyzed how minorities would be protected against a national majority.

**Collusion during the bootstrap (new).** Before Decisions 32–40 exist, a small group controls defaults, recruitment, and the mandate feed: the Avatar Party leadership plus early model, client, and verification vendors. This is the most valuable window for capture, and the least protected.

### Important problems

These carry over from v1.0: foundation model bias (mitigated, not eliminated); volatility cascades when Avatars react to the same news at once (partly mitigated); dependence on the information ecosystem for oversight; detailed design of the audit layer; exit conditions for v0.1; and the privacy trade-offs of eID verification. Delegate concentration as a form of concentrated power remains on the list, now with empirical support from liquid democracy and DAOs (Related Work).

New or expanded in v1.1:

- **Notification fatigue leading to rubber-stamping.** In practice, the notify-with-revocation default (Decision 13) may become fully automatic politics.
- **Foreign influence through mass delegation.** Foreign states or networks can build up or co-opt high-reach delegates and steer large blocs of delegated votes without touching a single Avatar.
- **Outputs blocked during the transition.** During the move from Stage 1 to Stage 2, the second chamber, the courts, or the executive may block or nullify Proxy Chamber outputs for strategic reasons. This goes beyond the "legitimacy clash" v1.0 named.
- **Legal liability and gaps in accountability.** When an Avatar-driven vote causes harm, it is unclear who is responsible: the citizen, the delegate, the model provider, or the certifier.
- **Configuration effort and the digital divide.** After setup, each additional vote costs the citizen nothing. But configuring and overseeing an Avatar well takes skill and time, and those are unequally distributed.
- **No deliberation step.** The system adds up preferences but does nothing to form them. The best recent evidence for AI in democracy concerns mediated deliberation [R14], which this design lacks.

### Open design questions

- Emergency vote path: letting genuinely time-sensitive issues skip the cooling-off period without it becoming a self-declared emergency power.
- Detecting conflicts between bills.
- Geographic or community-level Proxy Chambers for local issues.
- Stage-gate thresholds: what measured fidelity, identity integrity, and concentration levels should be required before Stage 2?
- Which international providers should be eligible (Decision 34)?

---

## Part 7: Scenario Notes (Speculative, Not Empirical Findings)

*v1.0 called this section "Stress Tests Conducted" and presented its output as findings. These are armchair walkthroughs, with no simulation, data, or formal protocol. They help generate questions. They are not evidence and should not be cited as evidence.*

### COVID-19 (scenario walkthrough)

We walked through how the Avatar system might have interacted with the legislative side of the pandemic response from 2020 onward. We do not judge any particular bill or mandate as good or bad.

**What might have worked differently:** Large emergency spending packages could have been voted on section by section, making each provision visible and separately accountable. Whether that would have produced better outcomes, or just slower ones, is unknown. Questions of emergency authority could have gone through an explicit legislative vote rather than executive action alone. That is a difference in procedure, not a verdict on any particular measure. The audit trail for emergency spending would plausibly have been more complete.

**Probably about the same:** The initial pandemic response, which is an executive function. Changing scientific guidance, where revocability might help and correlated model errors might hurt.

**No way for the system to make a difference:** Administrative grant and regulatory decisions. Media polarization. Tribalism among citizens. Underlying scientific uncertainty.

**Net assessment:** Unknown. The walkthrough points to mechanisms worth testing: per-section voting under time pressure, and correlated Avatar responses to fast-changing guidance. It does not show that legislative outcomes would have been better.

### Foundation model capture (threat walkthrough)

We walked through five routes of attack:

1. Training data poisoning
2. Bias introduced through RLHF and fine-tuning
3. Prompt injection at the Avatar layer
4. Manipulation targeting delegates
5. Corruption at the provider level

v1.1 adds three more from the red-team review: certifier capture, concentration in clouds or app stores, and Avatars guessing delegates' views from training data (Part 6).

**Net assessment:** There are partial defenses against most of these routes and full defenses against none. Capture is the central design challenge of the system. *(v1.1)* v1.0 concluded that the infrastructure needs "national-security-grade governance, not community-governed open-source software." We now separate two things. The *security* of builds, signing, and audit should meet critical-infrastructure standards. *Control* over which Avatars may run must not be centralized, or the system loses the spread-out resistance it was meant to provide (H3). Decisions 38–40 attempt this balance; whether it holds is an open problem.

### Walkthroughs still to do

- Adversarial bill flooding
- Volatility
- AI safety regulation
- Coerced configuration
- A measurement protocol for Stage 0: what an advisory chamber should measure, and for how long

---

## Part 8: Bootstrap Path

No single act can bring about the end state. Any transition path has to deal with:

- Constitutional barriers. These are especially severe in the United States, where amendments are nearly impossible.
- Political incentives. Politicians have no reason to vote themselves out of a job.
- Missing technical and institutional infrastructure. Many countries have no eID system, and nowhere are there certifying bodies, approved models, or software.

### Stage 0 first: the advisory chamber

*(New in v1.1.)* Before any candidate is bound to anything, run an unofficial Avatar Assembly alongside a real legislature (Part 2). Publish its predicted citizen tallies next to the legislature's actual roll-call votes. Also publish:

- fidelity measurements: how often citizens agree with, override, or disown their Avatar's votes;
- how concentrated providers and delegates are;
- the verification tiers of participating users.

This follows Hidalgo's recommendation for unofficial, non-binding experiments [R12]. It needs no election victory, and it produces the evidence the later stages depend on.

### The Avatar Party approach (Stage 1)

One plausible route to binding influence is to **form a political party whose candidates run on the platform of being bound by Avatar mandates**. This approach:

- Needs no constitutional change *for the transition*, because existing election systems already allow any platform. (The end state, a Proxy Chamber replacing the lower chamber's vote, does need constitutional or legal change; see Stage 2.)
- Avoids the problem that politicians won't vote against themselves. Avatar Party candidates *want* to be bound; that is their platform.
- Produces a proof of concept with real political stakes as soon as even a few seats are won.
- Allows step-by-step refinement, starting with low-stakes elections (city council, state legislature) and scaling up.
- Can be repeated in other countries with suitable adjustments.

**Historical caution.** The closest real analogues are Demoex (Sweden), Flux (Australia), and the Net Party (Argentina). They show that a mandate-bound seat can be won locally: Demoex held a municipal seat across three elections. But all three stayed electorally tiny, other parties refused to cooperate with them, and none scaled [R20–R22]. Any Avatar Party plan should expect the same headwinds.

### Compliance enforcement

Avatar Party representatives are held to their Avatar mandate through four layers:

- **Political layer:** Mandate and actual vote are shown side by side, publicly and in real time. A defection becomes news immediately.
- **Selection layer:** Recruit candidates whose temperament suits carrying out delegated decisions rather than acting as independent statesmen.
- **Reputation layer:** An "Avatar compliance score" is the central campaign metric. Falling below a threshold triggers a primary challenge.
- **Brand layer:** For candidates who ran as mandate executors, a "conscience" defense should be treated as a campaign liability and logged as a defection event, not as automatic proof of permanent unfitness.

*(v1.1)* v1.0 estimated "practical compliance: 85-95% in early stages." No model, analogue, or data supported that figure, and it has been **removed**. None of the analogue parties published validated compliance rates, so compliance is an empirical unknown. In Stage 1, the Avatar mandate is a **public whip, not a legal instruction**. That remains true until a jurisdiction creates enforceable mechanisms (agency, recall, or resignation triggers), where its constitution allows that at all. Defections still reveal where the system is weak, and they should be logged and published.

Each of these layers can itself be captured (Limitation 9, and "bootstrap collusion" in Part 6).

### Verification in early stages

Before national eID systems exist, verification can use:

- Verified social media accounts and ID.me-style services.
- Phone or SMS verification through paid providers.
- Selfie-with-ID checks of the kind banks use for know-your-customer (KYC) rules.
- In-person registration drives at libraries, community centers, and events.
- Progressive trust: new users start restricted and gain access as they verify more.

*(v1.1)* These methods are good enough for a **Stage 0 advisory chamber** and for civic signaling. They are not strong enough to carry electoral weight. Each can be defeated at the scale of hundreds of thousands of accounts. A weakly verified mandate that a party then enforces on its legislators invites astroturfing. So, under Decision 42, until a published verification tier is met (eID-grade binding is the default bar; whether weaker methods can qualify is an open question), Avatar tallies are civic signals only. They do not create voting obligations for candidates or feed compliance scores. Compliance metrics count only mandates from users at or above the published verification tier.

### Country-specific paths

- **United States and United Kingdom:** Contest an existing party's primaries or candidate selections, especially when anti-establishment sentiment runs high, as it does at times on both left and right. Insurgent candidates have sometimes won major-party nominations against establishment favorites. That shows outsiders can get on the ballot. It does not show the winners would obey an external AI whip.
- **Continental Europe:** Form new parties aimed at clearing proportional-representation thresholds (typically 5–10%).
- **Smaller advanced democracies (Estonia, Iceland, Switzerland, Taiwan):** Adoption may be easier here, with fewer institutional barriers and digital-government infrastructure already in place. Estonia's eID and i-Voting are the clearest case [R23, R24]. These countries are also natural sites for Stage 0 advisory chambers.
- **Westminster-style parliamentary systems with strong party discipline:** The hardest path, because party leadership controls everything.

### Illustrative timeline (with kill conditions)

The ranges below are illustrations, not forecasts. They depend on eID politics, court rulings, electoral fortunes, and whether Stage 0 evidence turns out favorable.

- **Years 1–3:** Build infrastructure and run Stage 0 advisory chambers. Pilot in non-state governance settings such as corporate boards, professional associations, and DAOs, keeping in mind the DAO evidence on concentration.
- **Years 2–5:** Avatar Party candidates win local elections (Stage 1), with mandates counted only from users at or above the published verification tier.
- **Years 4–8:** State and provincial seats.
- **Years 6–10:** Federal seats in receptive districts.
- **Years 8–15:** Critical mass in some legislatures. The first small country adopts the system formally, in parallel or advisory form.
- **Years 15–30:** A major democracy adopts it formally (Stage 2) through normal democratic processes, if the evidence supports that.

**Kill or pause conditions (illustrative).** Any of the following should halt progress until it is resolved:

- Stage 0 shows that Avatars systematically misrepresent their principals, or misrepresent identifiable groups.
- Delegate or provider concentration exceeds published limits.
- Verification cannot reach the published verification tier that Stage 1 requires.
- Coerced configuration is documented.

This is shorter than a path that starts with constitutional change, but it is still a project of several decades. Major democratic reforms have historically taken about that long.

---

## Part 9: What This Document Does Not Claim

To be clear, this proposal does not claim that:

- AI Avatars produce better policy than human legislators in every case.
- It eliminates political dysfunction, polarization, or capture.
- Implementation is easy or near-term.
- It has no failure modes.
- It is a complete political theory.
- *(v1.1)* Any benefit in Part 3 has been observed. All of them are hypotheses.
- *(v1.1)* An Avatar's vote is equivalent to the citizen casting a ballot in person.

The honest claim is narrower. Under specific, identifiable conditions, an AI Avatar system *might* produce better legislative outcomes than current representative democracy on specific dimensions. Other dimensions would stay the same or could get worse. That possibility is worth testing, starting with non-binding experiments. Read every hypothesis in Part 3 through this paragraph; where a Part 3 item seems to claim more, this paragraph governs.

The proposal is a starting point for serious design work, not a finished blueprint.

---

## Part 10: Invitation for Critique

This document began as a dialogue between two people. It was then revised after one adversarial red-team review and one evidence review. Further development needs:

- Political scientists who know constitutional design and democratic theory.
- Machine learning researchers who know foundation model capture, alignment, bias, and how faithfully models can simulate preferences.
- Cryptographers with experience in verification systems, threshold signatures, and voting that is verifiable yet secret.
- Legal scholars on binding representatives, free-mandate doctrines, and identity verification frameworks.
- Practitioners from Estonia, Taiwan, Switzerland, and other digital democracy efforts.
- People who have run liquid-democracy experiments or mandate-bound parties.
- Critics whose priors differ sharply from the authors', who will find weaknesses we missed.

The evidence review points to people and groups whose work bears directly on this proposal:

- César Hidalgo (augmented democracy)
- Audrey Tang and g0v (vTaiwan)
- Hélène Landemore (open democracy)
- The Collective Intelligence Project (Collective Constitutional AI; digital-twin evaluation)
- Anson Kahng and Ariel Procaccia (formal limits of liquid democracy)
- Bryan Ford (delegative democracy)
- Beth Simone Noveck and The GovLab (Crowd.Law)
- Lisa Argyle (silicon sampling)
- The authors of the Habermas Machine study

Specific invitations:

- Identify failure modes not addressed in Part 6.
- Stress-test the bootstrap path against political reality in your country.
- Critique the maintainer governance design against historical patterns of capture.
- Propose attack routes against the capture defenses that we have not considered.
- Propose a measurement protocol and stage-gate thresholds for a Stage 0 advisory chamber.
- Judge whether the limitations in Part 4 fundamentally undermine the case for the system.

---

## Appendix A: References

Sources come from the evidence review that informed v1.1. Titles, authors, and venues of the academic papers were checked against arXiv, DOI, or publisher metadata on 4 Nov 2026. Where a claim relies on a secondary source, the entry says so.

**Supporting sources for specific claims**

- [S1] GovTrack, "Members literally don't have enough time to read some bills before a vote is held" (2018). https://www.govtrack.us/posts/303/2018-03-29_members-literally-dont-have-enough-time-to-read-some-bills-before-a-vote-is-held-this-change-would-require-they-do
- [S2] The Economist, "America's elephantine spending bills are becoming increasingly unreadable" (2020). https://www.economist.com/graphic-detail/2020/12/29/americas-elephantine-spending-bills-are-becoming-increasingly-unreadable
- [S3] OpenStax, *Principles of Economics 3e*, §18.2 "Special Interest Politics" (Olson-style concentrated-interest logic). https://openstax.org/books/principles-economics-3e/pages/18-2-special-interest-politics
- [S4] Valgarðsson, Jennings, Stoker, Bunting, Devine, McKay et al., "A Crisis of Political Trust? Global Trends in Institutional Trust from 1958 to 2019," *British Journal of Political Science* (2025). https://doi.org/10.1017/S0007123424000498
- [S5] Pew Research Center, "Public Trust in Government: 1958–2025." https://www.pewresearch.org/politics/2025/12/04/public-trust-in-government-1958-2025/
- [S6] U.S. Department of the Treasury, Bureau of the Fiscal Service, *Combined Statement of Receipts, Outlays, and Balances of the United States Government* for Fiscal Year 2025: total outlays $7,010.0 billion ($7.010 trillion). https://fiscaldata.treasury.gov/static-data/published-reports/combined-statements/cs2025/2025_cs-final.pdf
- [S7] Douglas Holtz-Eakin, "The Line Item Veto and Public Sector Budgets: Evidence from the States," NBER Working Paper 2531 (1988); also *Journal of Public Economics* 36 (1988): 269–292. Finds long-run state budgets are not altered by an item veto. https://www.nber.org/papers/w2531
- [S8] David A. Van den Berg, "Veto Politics: Can a Line-Item Veto Reduce Spending?," Federal Reserve Bank of Richmond *Econ Focus / Region Focus* (Spring 2009): 30–31. Summarizes Holtz-Eakin: no statistically significant long-run effect on budget size. https://www.richmondfed.org/-/media/richmondfedorg/publications/research/econ_focus/2009/spring/pdf/feature4.pdf

**Related work**

- [R1] MIT Technology Review, "The simple but ingenious system Taiwan uses to crowdsource its laws" (2018). https://www.technologyreview.com/2018/08/21/240284/the-simple-but-ingenious-system-taiwan-uses-to-crowdsource-its-laws/
- [R2] The GovLab / Crowd.Law, "Case: vTaiwan." https://congress.crowd.law/case-vtaiwan.html
- [R3] The Daily Beast, "Taiwan tried to digitize democracy with vTaiwan. It was a huge flop." https://www.thedailybeast.com/taiwan-tried-to-digitize-democracy-with-vtaiwan-it-was-a-huge-flop/
- [R4] Pascal Sciarini & Anke Tresch, "Campaign Effects in Direct-Democratic Votes in Switzerland," *Journal of Elections, Public Opinion & Parties* 21, no. 3 (2011): 333–357. https://doi.org/10.1080/17457289.2011.588334 ; accepted manuscript: https://archive-ouverte.unige.ch/unige:17305
- [R5] Goldberg et al., *European Journal of Political Research*, selective participation in Swiss votes. https://onlinelibrary.wiley.com/doi/full/10.1111/1475-6765.12493
- [R6] Kling, Kunegis, Hartmann, Strohmaier & Staab, "Voting Behaviour and Power in Online Democracy: A Study of LiquidFeedback in Germany's Pirate Party" (arXiv:1503.07723). https://arxiv.org/abs/1503.07723
- [R7] Kahng, Mackenzie & Procaccia, "Liquid Democracy: An Algorithmic Perspective," *Journal of Artificial Intelligence Research*. https://www.jair.org/index.php/jair/article/view/12261
- [R8] Caragiannis & Micha, "A Contribution to the Critique of Liquid Democracy," *IJCAI 2019*. https://ijcai.org/proceedings/2019/17
- [R9] Messias, Pahari, Chandrasekaran, Gummadi & Loiseau, "Understanding Blockchain Governance: Analyzing Decentralized Voting to Amend DeFi Smart Contracts" (arXiv:2305.17655). https://arxiv.org/abs/2305.17655
- [R10] Falk, Pathan, Rigas & Tsoukalas, "Blockchain Governance: An Empirical Analysis of User Engagement on DAOs" (arXiv:2407.10945). https://arxiv.org/abs/2407.10945
- [R11] Feichtinger, Fritsch, Heimbach, Vonlanthen & Wattenhofer, "SoK: Attacks on DAOs" (arXiv:2406.15071). https://arxiv.org/abs/2406.15071
- [R12] César A. Hidalgo, "Augmented Democracy" (FAQ; archival page, last updated 2019). https://cesarhidalgo.com/augmented-democracy. Quotations in Part 2A were checked against this page on 4 Nov 2026.
- [R13] Gudiño, Grandi & Hidalgo, "Large language models (LLMs) as agents for augmented democracy," *Philosophical Transactions of the Royal Society A* 382 (2024). https://doi.org/10.1098/rsta.2024.0100
- [R14] Tessler, Bakker, Jarrett, Sheahan, Chadwick, Koster et al., "AI can help humans find common ground in democratic deliberation," *Science* (2024). https://doi.org/10.1126/science.adq2852
- [R15] Anthropic, "Collective Constitutional AI: Aligning a Language Model with Public Input." https://www.anthropic.com/research/collective-constitutional-ai-aligning-a-language-model-with-public-input
- [R16] Huang, Siddarth, Lovitt, Liao, Durmus et al., "Collective Constitutional AI: Aligning a Language Model with Public Input" (arXiv:2406.07814); CIP write-up: https://www.cip.org/blog/ccai. https://arxiv.org/abs/2406.07814
- [R17] Argyle, Busby, Fulda, Gubler, Rytting & Wingate, "Out of One, Many: Using Language Models to Simulate Human Samples," *Political Analysis* (2023). https://doi.org/10.1017/pan.2023.2
- [R18] Bisbee, Clinton, Dorff, Kenkel & Larson, "Synthetic Replacements for Human Survey Data? The Perils of Large Language Models," *Political Analysis* (2024). https://doi.org/10.1017/pan.2024.5
- [R19] Collective Intelligence Project, "The Digital Twin Evaluation Framework." A framework for evaluating digital twins, mainly at group/demographic level; cited here as a starting point to extend, not as an existing benchmark for individual Avatar fidelity. https://blog.cip.org/p/the-digital-twin-evaluation-framework
- [R20] Demoex. Secondary sources only: the official site returned HTTP 500 when the evidence review checked it. https://en.wikipedia.org/wiki/Demoex; see also https://inthesetimes.com/article/online-platforms-giving-accessible-direct-democracy-to-voters
- [R21] Flux (political party). Wikipedia (secondary). https://en.wikipedia.org/wiki/Flux_(political_party)
- [R22] Net Party (Partido de la Red). Wikipedia (secondary). https://en.wikipedia.org/wiki/Net_Party
- [R23] e-Estonia, "e-Democracy." https://e-estonia.com/solutions/e-governance/e-democracy/; X-Road: https://e-estonia.com/solutions/interoperability-services/x-road/
- [R24] e-Residency blog, "Firsts from Estonia" (2023 majority-online vote). https://www.e-resident.gov.ee/blog/posts/firsts-from-estonia/

---

## Appendix B: Document History

- v0.1: Initial framework. The lower chamber is replaced, and the second chamber is kept as a backstop.
- v0.2: Bill lifecycle, endorsement mechanics, audit infrastructure.
- v0.3: Taxonomy locked, COVID stress test, known limitations explicitly catalogued.
- v0.4: Capture stress test, capture defenses, evolutionary argument set out.
- v1.0: Full white paper compiled, including the bootstrap path. Ready for external critique.
- **v1.1 (current):** Revised after the first external red-team review (30 issues) and an evidence review of related work. Changes:
  - **Benefits as hypotheses.** "Confirmed Strengths" became "Expected Benefits (Hypotheses)," each with a stated basis and a test. Strength 9 moved to the limitations. Strength 16 was withdrawn because it contradicted the Avatar Party bootstrap.
  - **Unsourced numbers removed.** The 85–95% compliance figure and the "tens of billions" cost were withdrawn. The COVID "meaningfully better" verdict is now "unknown," and Part 7 is relabeled as speculative scenario notes.
  - **Neutral examples.** Politically loaded examples were replaced with neutral or balanced ones.
  - **Related work.** New Part 2A covers vTaiwan/Pol.is, Swiss direct democracy, LiquidFeedback, DAOs, Hidalgo's augmented democracy, the Habermas Machine, Collective Constitutional AI, silicon sampling, Demoex/Flux/Net Party, and Estonia. It engages with Hidalgo's case against binding proxies.
  - **Staged path.** A staged path was added (advisory → pledged → formal), with a non-binding advisory chamber as the first stage.
  - **Contradictions.** Three were resolved or stated openly: legitimacy vs. machine judgment, centralized maintainer governance vs. distributed resistance, and Strength 16 vs. the party apparatus.
  - **More live problems.** Part 6 now covers certifier and cloud concentration, preference fidelity, legislators who cannot be bound, coercion and data exposure, gridlock and minority protection, bootstrap collusion, notification fatigue, foreign mass delegation, transition nullification, liability, the digital divide, and the lack of a deliberation step.
  - **Design and bootstrap.** Design Decisions 38–43 were added. Early verification now supports civic signals only, not electoral weight. The timeline is marked illustrative and has kill conditions.
  - **References.** A references appendix was added.
  - **Post-review edits (same day).** The verification-gate term was aligned (DD42 and Part 8). The brand-layer language was softened. DAO participation bases are now stated per study. The CIP framework is described as a starting point to extend. The Daily Beast citation was demoted. Related Work was compressed into a table plus Hidalgo prose, and an orphaned reference was dropped (references renumbered).
