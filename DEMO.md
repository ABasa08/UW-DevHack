# PerkPilot: judge demo on your Shopify store (about 4 minutes)

Everything in this script happens in the real Shopify store: the storefront theme, Shopify checkout, Shopify admin and the Shopify order timeline. Payments are Shopify **test** payments in a development store.

## Before you record (10 minutes, once)

1. Backend in Shopify mode, the app running, and the tunnel up (see README → *Run it against your Shopify store*). Open the tunnel URL once in Chrome and click **Visit Site**.
2. In Shopify admin, open **Apps → PerkPilot → Setup**:
   - **Connect store** (or **Sync now**).
   - **Prepare the judge demo:** choose the product (e.g. your hoodie) and the variant the shopper will ask for (e.g. Black / M), then **Prepare scenario**. Copy the shopper request it gives you, for example *"Find a black hoodie in size M under $65 including shipping and tax, delivered by Thursday."*
   - **Fit-learning history:** choose the next size up (e.g. L) and **Load sample history**. That adds 20 delivered size-M orders and 2 confirmed exchanges, all labeled as sample data.
   - Check that the **PerkPilot assistant** app embed is on and the **PerkPilot fit guidance** block is on the product template (the links are right there).
3. Make sure the variant you'll exchange to (e.g. L) has stock, and the payment provider is in test mode.
4. Open three tabs: the storefront product page, Shopify admin → **Apps → PerkPilot**, and Shopify admin → **Orders**.

---

### Scene 1: Catch the mistake (0:00–0:45) · storefront

1. On the product page, click **Shopping assistant** (bottom right).
2. Paste the shopper request and click **Understand my request**. The confirmation card shows what was understood (hoodie · black · M · ≤ $65 total · by Thursday) and which interpreter ran. Click **Confirm requirements**.
3. The **Shopping agent proposal** stacks the discount code with the Visa card-linked credit and claims a lower effective cost. Click **Verify before checkout →**.
4. **Blocked before checkout.** With the code, Shopify's total is below the card offer's threshold, so the claimed credit doesn't exist and the savings are unsupported.
5. The corrected options: **Option A** (code, within budget) is recommended; **Option B** (no code) qualifies for the credit but is over the shopper's maximum.

*Say:* "AI can propose, but verified rules control what reaches checkout. The tax and total here come from Shopify itself, and a future statement credit can't override the maximum charge the shopper approved."

### Scene 2: Confirm the purchase (0:45–1:20) · storefront → Shopify checkout

6. Click **Use option A**. All checks pass, including **Shopify agrees on the total**. The breakdown shows *Tax (calculated by Shopify)*.
7. Tick the approval box and click **Approve $X and continue**. PerkPilot re-verifies, creates the Shopify draft order, and Shopify must total it to the cent.
8. Click **Pay $X on [store]'s checkout →**. Shopify's own checkout opens with exactly that item, code and shipping. Pay with the test card `4242 4242 4242 4242`.
9. Back on the store, open the assistant → **My orders**. The order shows **Paid · Visa •••• 4242 (Shopify test payment)** and **Verified purchase** (the order total matches the approval).

*Say:* "PerkPilot never touched the card. Shopify took the payment, and PerkPilot built the purchase record from the paid Shopify order."

### Scene 3: Fix the problem (1:20–2:20) · admin → storefront

10. In **Apps → PerkPilot → Orders**, open the order and click **Ship & mark delivered (test order)**. That creates the Shopify fulfillment and a DELIVERED event, the same events a carrier would post. (Or fulfil it in Shopify's Orders tab.)
11. In the storefront assistant, open the order → **Report a problem** → *"This feels too tight."* → **Submit**.
12. The resolution center shows: Shopify's fulfillment record shows size M was delivered, so it isn't an order error; there are **2 prior confirmed fit exchanges** for size M among 21 fulfilled orders; so it's a **Suspected fit issue**, not a proven defect.
13. Choose **Exchange for Black size L** (re-verified against Shopify's live stock and price), tick the box, and click **Confirm exchange**. A free replacement order is created in Shopify, and a return is opened for the original. Show it in the **Orders** tab, tagged `perkpilot-exchange`.
14. Ship and deliver the replacement (step 10 again, on the replacement order), then click **Fits well** in the assistant.

### Scene 4: Turn the return into learning (2:20–3:05) · Shopify admin

15. **Apps → PerkPilot → Rule review**: **3 confirmed outcomes** among **21 fulfilled orders** (14%), the evidence (two labeled sample, one live order), the limitations, and the exact wording shoppers will see.
16. Click **Approve guidance**. The card shows *Published to the Shopify product page*.

*Say:* "Confirmed outcomes create a controlled improvement that a person approved. It's not unexplained AI behavior."

### Scene 5: Prevent the next mistake (3:05–3:45) · storefront

17. Reload the product page and select size M. The **Fit guidance** block shows *"Some customers reported a tight fit in size M. Would you like to review size L?"* with the evidence. Nothing changes until the shopper clicks **Switch to size L**.
18. Open the assistant in a private window (a new shopper) and send the same request. The recommendation carries the same guidance, and **Review size L** re-verifies with size L.
19. To show the counterfactual, **Pause rule** in Rule review and reload: the guidance disappears from both. Resume it.

### Scene 6: Close with the dashboard (3:45–4:00) · Shopify admin

20. **Apps → PerkPilot → Dashboard**: the blocked checkout mistake, the verified Shopify order, the exchange, the approved rule, and the live Shopify order feed.

*Close:* "PerkPilot doesn't just help customers buy. It remembers what they approved, helps fix what went wrong, and makes the next purchase more reliable, all inside the merchant's Shopify store."

---

## If something goes wrong

- **The assistant shows a warning page:** open the tunnel URL in a tab and click **Visit Site** (free ngrok domains).
- **The order doesn't appear after paying:** the assistant polls Shopify directly, so wait a few seconds. In the admin, **Orders → (order) → Refresh from Shopify** re-reads it.
- **The scenario button fails:** the app needs the `write_draft_orders` and `write_discounts` scopes. Redeploy `shopify.app.toml` and approve the update in Shopify admin.
- **Replay:** run **Prepare scenario** again (it reuses the discount code). Each new purchase is a new Shopify test order.
