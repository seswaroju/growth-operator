# Vertical prompt layers — nurture (kirana) v1.0
## <a id="reorder"></a>Layer: nurture.kirana.reorder
```
Weekly staples nudge. INPUT: customer's repeat items + median cycle days.
Compose ONE message listing 3-6 items they're likely out of ("time for atta
& dal?") with a one-tap reorder of their usual basket. No prices in the nudge
(figures require ledger; the draft they tap into will show computed totals).
SKIP if: ordered <5 days ago, cap reached, or no repeat pattern (need ≥3
cycles). Output 'SKIP' with reason.
```
