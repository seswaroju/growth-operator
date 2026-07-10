# Vertical prompt layers — ops (kirana) v1.0
## <a id="ingest"></a>Layer: ops.kirana.ingest
```
Review price-sheet ingestion rows. Judgment rules: MRP vs selling-price
columns often both present — map MRP to mrp_minor, note selling price as an
offer overlay suggestion, never silently overwrite. Unit inference from pack
strings ("5kg", "500 ml", "12 pc"). Alias capture: vernacular column values
become aliases, not titles. Auto-approve ≥0.95 confidence rows.
```
## <a id="stock"></a>Layer: ops.kirana.stock
```
Daily: list items below low_stock_threshold and items with zero movement 30d,
as an owner digest section. Suggestions only; no ordering authority.
```
