"""Sandboxed evaluation runs.

Every run seeds a fresh in-memory copy of the demo store, so it never touches the live
demo state. Only the resulting Evaluation Run document is persisted. Labeled fixtures
here are separate from the seeded outcomes that generate the learned rule.
"""

from __future__ import annotations

from .ai import ai_status, deterministic_interpreter, parse_shopping_request
from .core import DomainError, now, today
from .demo import advance
from .fixtures import seed
from .learning import approve_rule
from .models import Requirements
from .resolution import accept_remedy, classify, confirm_outcome, create_claim, generate_remedies
from .shopping import confirm_checkout, confirm_intent, parse_intent, recommend, verify_proposal
from .store import BaseStore, MemoryStore
from .verifier import verify

SCENARIO_VERSION = "demo-v1"

BASE_REQ = {"category": "running_shoes", "color": "black", "size": "10", "max_charge_cents": 12000,
            "budget_includes_shipping_and_tax": True, "deadline_date": "2026-10-23", "quantity": 1}


def _prop(**kw):
    p = {"product_id": "aerox", "variant_id": "aerox-bla-10", "quantity": 1, "shipping_option": "standard",
         "coupon_codes": ["STRIDE15"], "card_offer_ids": [], "payment_method_id": "pm_visa_4242", "source": "evaluation"}
    p.update(kw)
    return p


VERIFIER_CASES = [
    ("Correct product with eligible coupon", {}, _prop(), "PASS", None),
    ("Agent claims card credit after coupon", {}, _prop(card_offer_ids=["clo_visa10"], claimed_charge_cents=11000, claimed_effective_cost_cents=10000), "FAIL", "CARD_OFFER_ELIGIBILITY"),
    ("Wrong color", {}, _prop(variant_id="aerox-whi-10"), "FAIL", "COLOR"),
    ("Wrong size", {}, _prop(variant_id="aerox-bla-10.5"), "FAIL", "SIZE"),
    ("Over budget without coupon", {}, _prop(coupon_codes=[]), "FAIL", "BUDGET"),
    ("Expired coupon", {}, _prop(coupon_codes=["SPRING20"]), "FAIL", "COUPON_EXPIRED"),
    ("Stacked coupons in one group", {}, _prop(coupon_codes=["STRIDE15", "WELCOME10"]), "FAIL", "OFFER_STACKING"),
    ("Misses delivery deadline", {}, _prop(product_id="tempo", variant_id="tempo-bla-10", coupon_codes=[]), "FAIL", "DELIVERY_DEADLINE"),
    ("Insufficient inventory", {}, _prop(product_id="pace", variant_id="pace-bla-10", coupon_codes=[]), "FAIL", "STOCK"),
    ("Card offer on non-enrolled debit card", {"max_charge_cents": 13000}, _prop(coupon_codes=[], card_offer_ids=["clo_visa10"], payment_method_id="pm_visa_1881"), "FAIL", "CARD_OFFER_ELIGIBILITY"),
    ("Different product type", {}, _prop(product_id="forge", variant_id="forge-bla-10", coupon_codes=[]), "FAIL", "PRODUCT_CATEGORY"),
    ("Valid: alternate coupon", {}, _prop(coupon_codes=["WELCOME10"]), "PASS", None),
    ("Valid: no deadline, Tempo Lite", {"deadline_date": None}, _prop(product_id="tempo", variant_id="tempo-bla-10", coupon_codes=[]), "PASS", None),
    ("Valid: size 10.5 when confirmed 10.5", {"size": "10.5"}, _prop(variant_id="aerox-bla-10.5"), "PASS", None),
    ("Valid: card credit legitimately earned", {"max_charge_cents": 13000}, _prop(coupon_codes=[], card_offer_ids=["clo_visa10"], claimed_effective_cost_cents=11500), "PASS", None),
]

PARSER_CASES = [
    ("Find black running shoes in size 10, under $120 including shipping and tax, delivered by Friday.",
     {"category": "running_shoes", "color": "black", "size": "10", "max_charge_cents": 12000, "deadline_date": "2026-10-23"}),
    ("I need white running shoes size 10.5, max $150 total, by Thursday",
     {"category": "running_shoes", "color": "white", "size": "10.5", "max_charge_cents": 15000, "deadline_date": "2026-10-22"}),
    ("trail shoes size 11 in green, budget $160 all in",
     {"category": "trail_running", "color": "green", "size": "11", "max_charge_cents": 16000}),
    ("walking shoes, grey, size 9.5, under $90 including everything",
     {"category": "walking_shoes", "color": "grey", "size": "9.5", "max_charge_cents": 9000}),
    ("Gym trainers size 10 under $110 total delivered by tomorrow",
     {"category": "training_shoes", "size": "10", "max_charge_cents": 11000, "deadline_date": "2026-10-21"}),
    ("black running shoes", {"category": "running_shoes", "color": "black", "size": None, "max_charge_cents": None}),
]

CLASSIFY_CASES = [
    ("Wrong color delivered", "sc_wrong_color", "wrong_color", "ORDER_MISMATCH"),
    ("Incorrect charge", "sc_incorrect_charge", "charge_issue", "PRICE_OR_DELIVERY_MISMATCH"),
    ("Missed delivery promise", "sc_late_delivery", "late_delivery", "PRICE_OR_DELIVERY_MISMATCH"),
    ("Customer preference change", "sc_preference", "preference", "CUSTOMER_PREFERENCE"),
    ("Ambiguous complaint (abstain)", "sc_ambiguous", "unclear", "NEEDS_REVIEW"),
    ("Claims wrong color but records match (abstain)", "sc_preference", "wrong_color", "NEEDS_REVIEW"),
    ("Claims overcharge but settlement matches (abstain)", "sc_ambiguous", "charge_issue", "NEEDS_REVIEW"),
    ("Tight fit on correctly delivered size", "hist_05", "fit_tight", "SUSPECTED_PRODUCT_FIT"),
]

HELD_OUT_SHOPPERS = [
    ("Size 10 black, phrasing A", "black running shoes size 10, $120 max including shipping and tax, by Friday", "10.5"),
    ("Size 10 black, phrasing B", "Need size 10 black runners by Friday, budget $120 total.", "10.5"),
    ("Size 10 white", "white running shoes size 10 under $150 all in", "10.5"),
    ("Size 10.5 control", "black running shoes size 10.5 under $120 including shipping and tax", None),
    ("Size 11 control", "running shoes size 11 under $140 total", None),
]


def _issue(pt):
    return {"problem_type": pt, "details": "", "requested_outcome": None, "mentioned_size": None, "mentioned_color": None,
            "missing_information": []}


def _case(name, expected, observed, ok, **extra):
    return {"name": name, "expected": expected, "observed": observed, "pass": bool(ok), **extra}


def canonical_flow(store: BaseStore) -> list[dict]:
    """The full judge-demo sequence driven through the real services."""
    steps = []

    def step(name, ok, detail=""):
        steps.append({"step": name, "pass": bool(ok), "detail": detail})
        if not ok:
            raise AssertionError(f"{name}: {detail}")

    try:
        it = parse_intent(store, "shp_jordan", "Find black running shoes in size 10, under $120 including shipping and tax, delivered by Friday.")
        step("Parse request", not it["missing_essential"], str(it["parsed_requirements"]))
        confirm_intent(store, it["intent_id"], Requirements(**it["parsed_requirements"]), None)
        step("Shopper confirms requirements", True)
        rec = recommend(store, it["intent_id"])
        step("Recommend AeroRunner X", rec["top_pick"] and rec["top_pick"]["product_id"] == "aerox")
        bad = verify_proposal(store, it["intent_id"], rec["agent_proposal"])
        failed = [c["rule"] for c in bad["result"]["checks"] if c["status"] == "FAIL"]
        step("Verifier blocks invalid card credit", bad["result"]["status"] == "FAIL" and "CARD_OFFER_ELIGIBILITY" in failed, ",".join(failed))
        opt = next(o for o in bad["result"]["options"] if o["option_id"] == bad["result"]["recommended_option"])
        good = verify_proposal(store, it["intent_id"], {**opt["proposal"], "source": "corrected_option"})
        charge = good["result"]["price_breakdown"]["charge_cents"]
        step("Corrected option passes at $110", good["result"]["status"] == "PASS" and charge == 11000, str(charge))
        pur = confirm_checkout(store, good["quote_id"], "shp_jordan", charge, True)["purchase"]
        step("Purchase record created", pur["approved_charge_cents"] == 11000)
        advance(store, until="delivered", purchase_id=pur["purchase_id"])
        claim = create_claim(store, pur["purchase_id"], "shp_jordan", "These shoes feel too tight.")
        step("Complaint classified as suspected fit", claim["classification"] == "SUSPECTED_PRODUCT_FIT", claim["classification"])
        rems = generate_remedies(store, claim["claim_id"])
        ex = next(r for r in rems if r["remedy_type"] == "exchange" and r["status"] == "eligible")
        step("Eligible exchange to 10.5 offered", ex["target_variant_id"] == "aerox-bla-10.5")
        accept_remedy(store, ex["remedy_id"], "shp_jordan", True, "eval-1", "original_payment_method")
        exp = store.find_one("purchases", {"parent_purchase_id": pur["purchase_id"]})
        advance(store, until="delivered", purchase_id=exp["purchase_id"])
        out = confirm_outcome(store, claim["claim_id"], "shp_jordan", "fits")
        rule = out["rule_proposal"]
        step("Third validated outcome proposes a rule", rule and rule["status"] == "proposed", rule and rule["rule_id"])
        it2 = parse_intent(store, "shp_sam", "Find black running shoes in size 10, under $120 including shipping and tax, delivered by Friday.")
        confirm_intent(store, it2["intent_id"], Requirements(**it2["parsed_requirements"]), None)
        step("Proposed rule does not affect shoppers before approval", not recommend(store, it2["intent_id"])["guidance"])
        approve_rule(store, rule["rule_id"], "eval_merchant", None)
        g = recommend(store, it2["intent_id"])["guidance"]
        step("Approved rule shows guidance to next shopper", bool(g) and g[0]["suggest_size"] == "10.5")
        step("Earlier confirmed order unchanged", store.get("purchases", pur["purchase_id"])["variant_id"] == "aerox-bla-10")
    except (AssertionError, DomainError, StopIteration, KeyError, TypeError) as exc:
        if not steps or steps[-1]["pass"]:
            steps.append({"step": "Unexpected error", "pass": False, "detail": f"{type(exc).__name__}: {exc}"})
    return steps


def run_evaluation(live_store: BaseStore) -> dict:
    parser_mode = ai_status()
    sections = {}

    # ---- 1. checkout verifier
    sb = MemoryStore()
    seed(sb)
    vcases = []
    for name, req_over, prop, expected, rule in VERIFIER_CASES:
        res = verify(sb, {**BASE_REQ, **req_over}, prop, "shp_jordan", with_options=False)
        failed = [c["rule"] for c in res["checks"] if c["status"] == "FAIL"]
        ok = res["status"] == expected and (rule is None or rule in failed)
        vcases.append(_case(name, f"{expected}{' · ' + rule if rule else ''}", f"{res['status']}{' · ' + ','.join(failed) if failed else ''}", ok))
    invalid = [c for c, spec in zip(vcases, VERIFIER_CASES) if spec[3] == "FAIL"]
    valid = [c for c, spec in zip(vcases, VERIFIER_CASES) if spec[3] == "PASS"]
    sections["checkout_verifier"] = {
        "cases": vcases,
        "violation_detection": f"{sum(c['pass'] for c in invalid)}/{len(invalid)}",
        "false_rejections": f"{sum(not c['pass'] for c in valid)}/{len(valid)}",
    }

    # ---- 2. request parsing (uses the live interpreter mode when Gemini is configured)
    pcases, fields_ok, fields_total = [], 0, 0
    for text, expected in PARSER_CASES:
        got = parse_shopping_request(text, today(sb))["requirements"]
        diffs = {k: got.get(k) for k, v in expected.items() if got.get(k) != v}
        fields_ok += len(expected) - len(diffs)
        fields_total += len(expected)
        pcases.append(_case(text, expected, got if diffs else "all labeled fields match", not diffs, mismatched=diffs))
    sections["request_parsing"] = {"cases": pcases, "field_accuracy": f"{fields_ok}/{fields_total}",
                                   "interpreter": parser_mode, "runs": 1}

    with deterministic_interpreter():
        # ---- 3. claim classification incl. abstention
        ccases = []
        for name, pid, pt, expected in CLASSIFY_CASES:
            res = classify(sb, sb.get("purchases", pid), _issue(pt))
            ccases.append(_case(name, expected, res["classification"], res["classification"] == expected))
        repeat = all(classify(sb, sb.get("purchases", "sc_wrong_color"), _issue("wrong_color"))["classification"] == "ORDER_MISMATCH"
                     for _ in range(5))
        ccases.append(_case("Same evidence, same answer (5 repeats)", "ORDER_MISMATCH x5", "consistent" if repeat else "varied", repeat))
        abst = [c for c in ccases if "abstain" in c["name"]]
        sections["claim_classification"] = {"cases": ccases, "accuracy": f"{sum(c['pass'] for c in ccases)}/{len(ccases)}",
                                            "appropriate_abstention": f"{sum(c['pass'] for c in abst)}/{len(abst)}"}

        # ---- 4. remedy policy compliance
        rcases = []
        sb2 = MemoryStore()
        seed(sb2)
        claim = create_claim(sb2, "sc_incorrect_charge", "shp_riley", "I was charged more than I approved.")
        rem = next(r for r in generate_remedies(sb2, claim["claim_id"]) if r["remedy_type"] == "partial_refund")
        accept_remedy(sb2, rem["remedy_id"], "shp_riley", True, "k1", "original_payment_method")
        dup = accept_remedy(sb2, rem["remedy_id"], "shp_riley", True, "k1", "original_payment_method")
        n_refunds = len(sb2.find("events", {"event_type": "refund.simulated", "subject_id": "sc_incorrect_charge"}))
        rcases.append(_case("Duplicate remedy acceptance", "1 simulated refund", f"{n_refunds} refund(s), duplicate={dup['duplicate']}", n_refunds == 1 and dup["duplicate"]))
        rcases.append(_case("Overcharge refund equals difference", "$15.00", f"${rem['amount_cents'] / 100:.2f}", rem["amount_cents"] == 1500))
        amb = create_claim(sb2, "sc_ambiguous", "shp_riley", "This isn't what I expected.")
        types = {r["remedy_type"] for r in generate_remedies(sb2, amb["claim_id"])}
        rcases.append(_case("Ambiguous claim gets no automatic remedy", "manual_review only", ",".join(sorted(types)), types == {"manual_review"}))
        pref = create_claim(sb2, "sc_preference", "shp_riley", "I changed my mind, I'd rather have white.")
        prem = generate_remedies(sb2, pref["claim_id"])
        refund_offered = any(r["remedy_type"] == "refund" and r["status"] == "eligible" for r in prem)
        rcases.append(_case("Refund remains available alongside store credit", "refund eligible", str(refund_offered), refund_offered))
        wc = create_claim(sb2, "sc_wrong_color", "shp_riley", "They sent white instead of black.")
        sb2_prod = sb2.get("products", "aerox")
        sb2_prod["inventory_by_variant"]["aerox-bla-10.5"] = 0
        sb2.put("products", sb2_prod)
        try:
            ex = next(r for r in generate_remedies(sb2, wc["claim_id"]) if r["remedy_type"] == "exchange")
            accept_remedy(sb2, ex["remedy_id"], "shp_riley", True, "k2", "original_payment_method")
            blocked = False
        except DomainError as e:
            blocked = e.code == "inventory_conflict"
        except StopIteration:
            blocked = False
        rcases.append(_case("Exchange blocked when replacement sells out", "inventory_conflict", "blocked" if blocked else "not blocked", blocked))
        sections["remedy_policy"] = {"cases": rcases, "compliance": f"{sum(c['pass'] for c in rcases)}/{len(rcases)}"}

        # ---- 5. end-to-end workflow (also produces + approves the learned rule in the sandbox)
        sb3 = MemoryStore()
        seed(sb3)
        steps = canonical_flow(sb3)
        sections["workflow"] = {"steps": steps, "completed": f"{sum(s['pass'] for s in steps)}/{len(steps)}"}

        # ---- 6. controlled comparison on held-out shopper requests
        rule = next((r for r in sb3.find("rules") if r["status"] == "approved"), None)
        comp = []
        for name, text, target in HELD_OUT_SHOPPERS:
            row = {"name": name, "request": text, "labeled_target": target}
            for arm, apply_rules in (("baseline", False), ("intervention", True)):
                it = parse_intent(sb3, "shp_sam", text)
                confirm_intent(sb3, it["intent_id"], Requirements(**it["parsed_requirements"]), None)
                rec = recommend(sb3, it["intent_id"], apply_learned_rules=apply_rules)
                g = rec["guidance"][0]["suggest_size"] if rec["guidance"] else None
                top = rec["top_pick"]
                row[arm] = {"guidance": g, "matches_target": g == target,
                            "constraints_satisfied": bool(top and top["best_option"]["status"] == "PASS"),
                            "top_pick": top["name"] if top else None}
            comp.append(row)
        sections["controlled_comparison"] = {
            "rule_used": rule["rule_id"] if rule else None,
            "rows": comp,
            "baseline_matches": f"{sum(r['baseline']['matches_target'] for r in comp)}/{len(comp)}",
            "intervention_matches": f"{sum(r['intervention']['matches_target'] for r in comp)}/{len(comp)}",
            "constraints_preserved": f"{sum(r['intervention']['constraints_satisfied'] for r in comp)}/{len(comp)}",
            "note": "Measures whether approved guidance appears for the labeled cases and stays silent otherwise. It does not measure prevented returns.",
        }

    total = sum(len(s.get("cases", s.get("steps", s.get("rows", [])))) for s in sections.values())
    passed = sum(sum(c["pass"] for c in s.get("cases", s.get("steps", []))) for s in sections.values())
    passed += sum(r["intervention"]["matches_target"] for r in sections["controlled_comparison"]["rows"])
    run_id = f"eval_{len(live_store.find('eval_runs')) + 1:04d}"
    doc = {
        "_id": run_id, "run_id": run_id, "scenario_version": SCENARIO_VERSION, "baseline_or_intervention": "both",
        "scenarios_evaluated": total, "passed": passed, "sections": sections,
        "assumptions": [
            "Runs in an isolated in-memory copy of the seeded demo store; live demo state is untouched.",
            "Classification, remedy and workflow sections use the deterministic fallback interpreter for repeatability.",
            "All data is synthetic. Results describe program behavior, not real-world business outcomes.",
        ],
        "calculated_metrics": {
            "violation_detection": sections["checkout_verifier"]["violation_detection"],
            "false_rejections": sections["checkout_verifier"]["false_rejections"],
            "parser_field_accuracy": sections["request_parsing"]["field_accuracy"],
            "classification_accuracy": sections["claim_classification"]["accuracy"],
            "appropriate_abstention": sections["claim_classification"]["appropriate_abstention"],
            "remedy_policy_compliance": sections["remedy_policy"]["compliance"],
            "workflow_completion": sections["workflow"]["completed"],
            "comparison_baseline": sections["controlled_comparison"]["baseline_matches"],
            "comparison_intervention": sections["controlled_comparison"]["intervention_matches"],
        },
        "timestamp": now(live_store).isoformat(),
    }
    live_store.put("eval_runs", doc)
    return doc
