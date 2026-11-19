"""CLI entrypoint for WordPress Lead Calculator Schema Validator & Quote Auditor."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from evaluator import (
    evaluate_quote,
    fuzz_scenario_matrix,
    generate_shortcode,
    render_json_report,
    render_markdown_report,
)
from validator import validate_schema


def _load_json(path_str: str) -> Dict[str, Any]:
    path = Path(path_str)
    if not path.is_file():
        raise FileNotFoundError(f"Schema file not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("JSON root must be an object.")
    return data


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wp-calc-cli",
        description="Validate WordPress lead calculator JSON schemas, fuzz pricing edge cases, and generate [lead_calc] shortcodes.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # validate
    p_val = sub.add_parser("validate", help="Validate calculator JSON schema and detect unreachable rules.")
    p_val.add_argument("schema", help="Path to calculator JSON schema file.")
    p_val.add_argument("--strict", action="store_true", help="Exit with code 1 if any warnings are present.")
    p_val.add_argument("--json", action="store_true", help="Output validation result as JSON.")

    # audit
    p_aud = sub.add_parser("audit", help="Run full schema validation + Decimal scenario matrix fuzzing.")
    p_aud.add_argument("schema", help="Path to calculator JSON schema file.")
    p_aud.add_argument("--max-scenarios", type=int, default=256, help="Max scenarios to test (default: 256).")
    p_aud.add_argument("--format", choices=("markdown", "json"), default="markdown", help="Report format.")
    p_aud.add_argument("--out", help="Optional output file path.")
    p_aud.add_argument("--strict", action="store_true", help="Fail if negative prices or unreachable/dead rules exist.")

    # shortcode
    p_sc = sub.add_parser("shortcode", help="Generate a WordPress [lead_calc] shortcode.")
    p_sc.add_argument("schema", help="Path to calculator JSON schema file.")
    p_sc.add_argument("--theme", default="modern", help="Shortcode theme attribute (default: modern).")
    p_sc.add_argument("--no-ajax", action="store_true", help="Disable AJAX submission attribute.")

    # quote
    p_qt = sub.add_parser("quote", help="Evaluate a single quote with Decimal precision.")
    p_qt.add_argument("schema", help="Path to calculator JSON schema file.")
    p_qt.add_argument("--input", dest="input_json", default="{}", help="JSON string of field inputs.")
    p_qt.add_argument("--input-file", help="Path to JSON file containing field inputs.")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        schema = _load_json(args.schema)
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        print(f"Error loading schema: {exc}", file=sys.stderr)
        return 2

    if args.command == "validate":
        res = validate_schema(schema)
        if args.json:
            print(json.dumps(res.to_dict(), indent=2))
        else:
            status = "VALID" if res.valid else "INVALID"
            print(f"Schema: {schema.get('id', 'unknown')} -> {status}")
            print(f"Errors: {len(res.errors)} | Warnings: {len(res.warnings)}")
            for issue in res.errors + res.warnings:
                print(f"  [{issue.severity.upper()}] {issue.code} ({issue.path}): {issue.message}")
        if not res.valid or (args.strict and res.warnings):
            return 1
        return 0

    if args.command == "shortcode":
        sc = generate_shortcode(schema, theme=args.theme, ajax=not args.no_ajax)
        print(sc)
        return 0

    if args.command == "quote":
        try:
            if args.input_file:
                inputs = _load_json(args.input_file)
            else:
                inputs = json.loads(args.input_json)
            if not isinstance(inputs, dict):
                raise ValueError("Quote input must be a JSON object.")
        except (FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
            print(f"Error parsing quote inputs: {exc}", file=sys.stderr)
            return 2
        qres = evaluate_quote(schema, inputs)
        print(json.dumps(qres.to_dict(), indent=2))
        return 0

    if args.command == "audit":
        val = validate_schema(schema)
        fuzz = fuzz_scenario_matrix(schema, max_scenarios=args.max_scenarios)
        sc = generate_shortcode(schema)
        if args.format == "json":
            report = render_json_report(schema, val, fuzz, sc)
        else:
            report = render_markdown_report(schema, val, fuzz, sc)

        if args.out:
            Path(args.out).write_text(report, encoding="utf-8")
            print(f"Audit report written to {args.out}")
        else:
            print(report)

        if not val.valid:
            return 1
        if args.strict and (fuzz.negative_scenarios or fuzz.dead_rules or val.warnings):
            return 1
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
