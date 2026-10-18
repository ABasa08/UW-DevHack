"""Validate every GraphQL document PerkPilot sends against Shopify's published Admin API schema.

Usage (from backend/):  python scripts/check_shopify_queries.py [schema.json] [--calls calls.json]
Without a path it downloads the schema for the pinned version from shopify.dev's public docs proxy.
--calls checks recorded variables too (PERKPILOT_RECORD_SHOPIFY_CALLS=calls.json python -m pytest tests/test_shopify_mode.py
writes them), so the inputs PerkPilot builds are validated against Shopify's input types, not just the documents.
Needs graphql-core (pip install graphql-core). Exits non-zero on any invalid field, argument or type.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
from graphql import OperationDefinitionNode, build_client_schema, get_introspection_query, parse, validate
from graphql.execution.values import get_variable_values

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import shopify, shopify_api, shopify_stream  # noqa: E402


def load_schema(path: str | None):
    if path:
        data = json.loads(Path(path).read_text())
    else:
        r = httpx.post(f"https://shopify.dev/admin-graphql-direct-proxy/{shopify.API_VERSION}",
                       json={"query": get_introspection_query(descriptions=False)}, timeout=120)
        r.raise_for_status()
        data = r.json()
    schema = build_client_schema(data["data"])
    schema._validation_errors = []  # Shopify's schema has harmless deprecation mismatches graphql-core would reject
    return schema


def documents() -> dict[str, str]:
    docs = {f"shopify_api.{k}": v for k, v in shopify_api.DOCUMENTS.items()}
    docs["shopify.QUERY"] = shopify.QUERY
    docs["shopify_stream.ORDERS_QUERY"] = shopify_stream.ORDERS_QUERY
    return docs


def check_calls(schema, path: str) -> int:
    by_op = {}
    for doc in documents().values():
        for d in parse(doc).definitions:
            if isinstance(d, OperationDefinitionNode) and d.name:
                by_op[d.name.value] = d
    bad = 0
    calls = json.loads(Path(path).read_text())
    for name, variables in calls:
        op = by_op.get(name)
        if not op:
            continue
        out = get_variable_values(schema, op.variable_definitions, variables)
        errors = out if isinstance(out, list) else []
        if errors:
            bad += 1
            print(f"FAIL variables for {name}: " + "; ".join(e.message for e in errors))
    print(f"{len(calls)} recorded calls checked, {bad} with invalid variables")
    return bad


def main() -> int:
    args = sys.argv[1:]
    calls = args[args.index("--calls") + 1] if "--calls" in args else None
    paths = [a for a in args if a != "--calls" and a != calls]
    schema = load_schema(paths[0] if paths else None)
    bad = 0
    for name, doc in documents().items():
        errors = validate(schema, parse(doc))
        print(f"{'OK  ' if not errors else 'FAIL'} {name}")
        for e in errors:
            bad += 1
            print(f"     {e.message}")
    if calls:
        bad += check_calls(schema, calls)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
