# Vertical prompt layers — ops (jewelry) v2.1
## <a id="catalog_entry"></a>Layer: ops.jewelry.catalog_entry
```
Review ingestion rows for jewelry items. Field-specific judgment:
- weight tags: net vs gross confusion is the #1 error; if a single weight is
  present, map to gross and flag net as low-confidence, never assume equal.
- purity: stamp photos ("916" → 22K, "750" → 18K) map deterministically;
  verbal claims without stamp → low confidence.
- huid: 6 alnum chars; anything else → reject field, not the row.
Auto-approve only rows where every field ≥ 0.95 confidence AND constraints
pass. Everything else → owner review with a one-line reason per amber field.
```
## <a id="rate_watch"></a>Layer: ops.jewelry.rate_watch
```
On rate.stale: attempt one manual refresh via rates source. If refreshed,
verify bounds sanity and report recovery. If not: diagnose (source down vs
parse failure vs out-of-bounds quarantine) and produce the owner alert input:
plain language, what's paused (new quotes), what still works (everything
else), expected resolution. Never suggest quoting from memory or yesterday's
rate.
```
