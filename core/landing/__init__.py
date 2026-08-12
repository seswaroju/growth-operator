"""Landing-page capability (LP-1) — generic engine.

Rule Zero: this package is **platform-invariant**. It knows generic conversion primitives
(`hero`, `product_grid`, `trust_bar`, `whatsapp_cta`, …) but never a vertical noun. Strategy + trust
copy live in `verticals/<v>/landing/`. The pipeline is deterministic:

    campaign context + vertical strategy + brand + products
        → ExperienceStrategy (semantic decisions)
        → LandingPageSpec (executable, schema-validated)
        → deterministic renderer (curated components, escaped copy — no LLM, no arbitrary HTML/JS)
        → tenant-branded page

See project-management/LANDING_PAGE_DESIGN.md.
"""
