# About FloodAdapt — context for the agent

This tells the agent who we are, so it can (a) prioritise funding that fits us or
our users, and (b) write better "@claude, tell me more" eligibility briefings.

> ⚠️ This repo is **public**. Do **not** put confidential details here (exact
> license fees, pricing, commercial strategy). Keep those out, or add them via the
> `FLOODADAPT_CONTEXT_EXTRA` GitHub secret — merged in only during CI, never
> committed. **The text below is a draft written by the assistant from general
> knowledge — review and correct it, especially the marked `[VERIFY]` parts.**

## What FloodAdapt is
FloodAdapt is a flood-adaptation decision-support tool developed by **Deltares**.
It puts advanced flood modelling in the hands of non-experts: a user can rapidly
run "what-if" scenarios — combinations of weather events (storms, heavy rainfall,
king tides), future conditions (sea-level rise, climate projections), and
adaptation measures (floodwalls, pumps, home elevation, buyouts, nature-based
solutions) — and immediately see the resulting flood maps, affected assets, and
economic/social impacts. It builds on Deltares' open models (the SFINCS
compound-flood model and the Delft-FIAT impact model) and is designed to support
community-scale flood-resilience and adaptation planning.

[VERIFY] Licensing/availability — whether it's open-source, free to use, and/or
offered with paid licensing, hosting, or support. (Keep specifics out of this
public file; put them in the `FLOODADAPT_CONTEXT_EXTRA` secret.)

## Who the users are
Primarily **local and regional governments, floodplain and emergency managers,
water authorities, and coastal communities** who need to plan flood adaptation
without being modelling experts. Strong uptake in the **United States**, with
growing international use. [VERIFY] the main user types and geographies for us.

## Our organisation
**Deltares** is an independent, not-for-profit institute for applied research in
the field of water and the subsurface, based in **Delft, the Netherlands**. It
works worldwide, typically as a **research/technology partner in project
consortia** rather than a commercial vendor. [VERIFY] our preferred role on funded
projects (prime applicant vs partner vs sub-awardee vs software/tool provider),
and whether a US entity (e.g. Deltares USA) is used for US-based funding.

## Eligibility-relevant facts
- Deltares is **Netherlands/EU-based** — a natural fit for EU (Horizon Europe, EIC,
  Interreg), Dutch (NWO, RVO), and international development funding.
- For **US federal** funding, eligibility often requires a US-based applicant, so
  we typically participate **through a US partner/community or a US entity**
  rather than as the direct applicant. [VERIFY]
- Our **end-user organisations** (the municipalities/agencies above) are often the
  eligible applicants, with Deltares as the technical partner. [VERIFY]

## What funding fits us best
- **Application track:** research & innovation grants and calls for flood/coastal
  resilience, climate adaptation, flood risk mapping and decision-support tools
  (e.g. NWO, EU Horizon Europe / EIC, NSF, foundations, agency programs), and
  community resilience grants where we partner with the applicant.
- **Business Development track:** climate/water/flood-tech investment, innovation
  loans, and accelerators relevant to the FloodAdapt product. [VERIFY] how
  actively we want to pursue this.
