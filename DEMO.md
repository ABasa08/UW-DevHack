# PerkPilot: 4-minute judge demo script

**Before you start:** both servers are running, and the browser is open at http://localhost:3000 on a desktop-width window. Click **Reset** in the dark demo bar. The clock should read **Tue, Oct 20, 10:00 AM PT**, and the persona should be **Jordan**.

> The demo request: *Find black running shoes in size 10, under $120 including shipping and tax, delivered by Friday.*

---

### Scene 1: Catch the mistake (0:00–0:45)

1. On the storefront, click **Use the demo request**, then **Ask the assistant**.
2. The confirmation card shows what was understood (running shoes · black · US 10 · ≤ $120 total · by Fri Oct 23) and which interpreter ran. Click **Confirm requirements**.
3. The agent proposal reads: STRIDE15 coupon + $10 card credit, "effective cost $100". Click **Verify before checkout →**.
4. **Blocked before checkout.** The card-linked offer requires $120+ *after* discounts, and the charge is $110. The claimed savings are therefore unsupported.
5. Scroll to the corrected options. **Option A** (coupon, $110, within limit) is recommended. **Option B** (no coupon, $125, $10 later credit, effective $115) fails the $120 limit.

*Say:* "AI can propose, but verified rules control what reaches checkout. A future statement credit can't override the maximum charge you approved."

### Scene 2: Confirm the purchase (0:45–1:20)

6. Click **Use option A**. Everything passes. Review the breakdown ($110 + $8 + $7 − $15 = **$110**), "card-linked offer: not eligible", Friday delivery, and **Visa Demo Card •••• 4242 [Simulated]**.
7. Tick the approval checkbox, then click **Place order · $110.00**.
8. The purchase record opens. Point out "What you approved", the SHA-256 record hash, *hash matches* / *event chain intact*, and the audit trail.

### Scene 3: Fix the problem (1:20–2:20)

9. Click **Demo: advance clock to delivery →**. It is now Friday, and the order is delivered.
10. Click **Report a problem**, choose the chip **These shoes feel too tight.**, then **Submit**.
11. The resolution center shows: the delivered size matches the order (not an order error), **2 prior confirmed fit exchanges** for size 10 among 21 orders, and the classification **Suspected fit issue** (*not a proven defect*). The evidence panel separates *confirmed by records* from *reported by you*.
12. Select **Exchange for Black size 10.5**. The replacement is re-verified (stock, same product, no extra charge). Tick the box and click **Confirm exchange**.
13. Click **Demo: advance to replacement delivery →**, then **Fits well**. A "Learning signal created" notice appears.

*Optional:* click **Re-send this confirmation (idempotency check)**. It reports "Already processed. No second refund or exchange was issued."

### Scene 4: Turn the return into learning (2:20–3:05)

14. Click **Open merchant rule review →** (or Merchant console → Rule review).
15. Walk through the card: **3 confirmed outcomes**, **21 fulfilled orders**, **14%**, the shopper-facing wording, the evidence (two seeded and one *this session*), and the limitations (small sample; one unconfirmed report excluded; not a defect).
16. Click **Approve guidance**.

### Scene 5: Prevent the next mistake (3:05–3:45)

17. Click **← Storefront**, switch **Shopping as → Sam**, then repeat the demo request and confirm it.
18. The guidance banner appears: *"Some customers reported a tight fit in size 10. Would you like to review size 10.5?"*
19. Toggle **Merchant-approved learned guidance** off: the banner disappears (baseline). Toggle it on again.
20. Click **Review size 10.5**. The requirements become v2 (US 10.5) with an audited reason, and nothing changes without Sam's choice.

### Scene 6: Close with the dashboard (3:45–4:00)

21. Open **Merchant console**. It shows proposals blocked, verified orders, the completed exchange, rules approved 1/1, and live activity.

> "PerkPilot doesn't just help customers buy. It remembers what they approved, helps fix what went wrong, and makes the next purchase more reliable."

---

### Backup paths

- **Model or network down:** the app runs entirely on the deterministic fallback interpreter (shown in the demo bar). Nothing in the core flow depends on an external service.
- **Something went sideways:** click **Reset** and replay. All IDs are deterministic (`pur_0001`, `clm_0001`, `rule_0001`, …).
- **Extra scenarios:** switch to **Riley** → Orders for seeded edge cases: wrong color delivered → *Order mismatch*; overcharged → *Price mismatch* (refund of the $15 difference); late delivery → *Delivery mismatch*; "I changed my mind" → *Customer preference*; "This isn't what I expected" → a follow-up question, with no guessing.
- **Changed amount after approval:** in Merchant console → Scenario tools, set the AeroRunner price to $114 *after* a shopper reaches the approve step. Placing the order then returns *NEEDS_RECONFIRMATION* with the new quote. Click **Restore $110** afterward.
- **Evaluation:** Merchant console → Evaluation → **Run evaluation** (52/52 scenarios on synthetic fixtures).
