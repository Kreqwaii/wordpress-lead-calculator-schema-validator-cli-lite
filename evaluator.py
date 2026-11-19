"""Decimal quote evaluator, scenario matrix fuzzer, shortcode & audit report generator."""

from __future__ import annotations

import itertools
import json
import random
from dataclasses import dataclass, field as dc_field
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Set, Tuple

from validator import ValidationResult, to_decimal


@dataclass
class QuoteResult:
    """Result of evaluating a single quote scenario using exact Decimal math."""

    raw_price: Decimal
    final_price: Decimal
    formatted_price: str
    triggered_rules: List[str] = dc_field(default_factory=list)
    clamped_to_min: bool = False
    clamped_to_max: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "raw_price": str(self.raw_price),
            "final_price": str(self.final_price),
            "formatted_price": self.formatted_price,
            "triggered_rules": list(self.triggered_rules),
            "clamped_to_min": self.clamped_to_min,
            "clamped_to_max": self.clamped_to_max,
        }


@dataclass
class FuzzReport:
    """Summary of Decimal scenario matrix fuzzing across field combinations."""

    scenarios_tested: int
    sampling_mode: str  # "exhaustive" | "stratified"
    min_raw_price: Decimal
    max_raw_price: Decimal
    min_final_price: Decimal
    max_final_price: Decimal
    negative_scenarios: List[Dict[str, Any]] = dc_field(default_factory=list)
    unbounded_warnings: List[str] = dc_field(default_factory=list)
    dead_rules: List[str] = dc_field(default_factory=list)
    rule_hit_counts: Dict[str, int] = dc_field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scenarios_tested": self.scenarios_tested,
            "sampling_mode": self.sampling_mode,
            "min_raw_price": str(self.min_raw_price),
            "max_raw_price": str(self.max_raw_price),
            "min_final_price": str(self.min_final_price),
            "max_final_price": str(self.max_final_price),
            "negative_scenario_count": len(self.negative_scenarios),
            "negative_scenarios": self.negative_scenarios[:5],
            "unbounded_warnings": list(self.unbounded_warnings),
            "dead_rules": list(self.dead_rules),
            "rule_hit_counts": dict(self.rule_hit_counts),
        }


def format_currency(amount: Decimal, currency_cfg: Dict[str, Any]) -> str:
    """Format a Decimal amount according to WordPress calculator currency settings."""
    decimals = int(currency_cfg.get("decimals", 2))
    symbol = str(currency_cfg.get("symbol", "$"))
    position = str(currency_cfg.get("position", "prefix"))
    thousand_sep = str(currency_cfg.get("thousand_sep", ","))
    decimal_sep = str(currency_cfg.get("decimal_sep", "."))

    quant = Decimal("1") if decimals == 0 else Decimal(f"1.{'0' * decimals}")
    rounded = amount.quantize(quant, rounding=ROUND_HALF_UP)
    sign = "-" if rounded < 0 else ""
    abs_val = abs(rounded)

    fixed = f"{abs_val:.{decimals}f}"
    if "." in fixed:
        int_part, frac_part = fixed.split(".", 1)
    else:
        int_part, frac_part = fixed, ""

    # Insert thousand separators
    groups: List[str] = []
    while len(int_part) > 3:
        groups.append(int_part[-3:])
        int_part = int_part[:-3]
    groups.append(int_part)
    int_formatted = thousand_sep.join(reversed(groups))

    number_str = f"{int_formatted}{decimal_sep}{frac_part}" if decimals > 0 else int_formatted
    if position == "suffix":
        return f"{sign}{number_str} {symbol}"
    return f"{sign}{symbol}{number_str}"


def _eval_condition(cond: Dict[str, Any], inputs: Dict[str, Any]) -> bool:
    field_id = cond.get("field", "")
    op = cond.get("op", "==")
    expected = cond.get("value")
    actual = inputs.get(field_id)

    if op == "==":
        return actual == expected
    if op == "!=":
        return actual != expected
    if op == "in":
        return isinstance(expected, list) and actual in expected
    if op == "not_in":
        return isinstance(expected, list) and actual not in expected
    if op == "contains":
        if isinstance(actual, list):
            return expected in actual
        if isinstance(actual, str) and isinstance(expected, str):
            return expected in actual
        return False

    # Numeric comparison
    d_act = to_decimal(actual)
    d_exp = to_decimal(expected)
    if d_act is None or d_exp is None:
        return False
    if op == ">":
        return d_act > d_exp
    if op == ">=":
        return d_act >= d_exp
    if op == "<":
        return d_act < d_exp
    if op == "<=":
        return d_act <= d_exp
    return False


def evaluate_quote(schema: Dict[str, Any], inputs: Optional[Dict[str, Any]] = None) -> QuoteResult:
    """Evaluate a quote for given inputs (falling back to field defaults) using Decimal."""
    inputs = inputs or {}
    resolved: Dict[str, Any] = {}
    fields = schema.get("fields", [])

    for fld in fields:
        fid = fld.get("id")
        if not fid:
            continue
        if fid in inputs:
            resolved[fid] = inputs[fid]
        elif "default" in fld:
            resolved[fid] = fld["default"]
        elif fld.get("type") in {"number", "range"}:
            resolved[fid] = fld.get("min", 0)
        elif fld.get("type") == "checkbox":
            resolved[fid] = []
        elif fld.get("type") == "toggle":
            resolved[fid] = False
        else:
            resolved[fid] = ""

    total = to_decimal(schema.get("base_price", 0)) or Decimal("0")
    multipliers: List[Decimal] = []

    for fld in fields:
        fid = fld.get("id")
        ftype = fld.get("type")
        val = resolved.get(fid)

        if ftype in {"number", "range"}:
            qty = to_decimal(val) or Decimal("0")
            unit_price = to_decimal(fld.get("unit_price", 0)) or Decimal("0")
            total += qty * unit_price
        elif ftype in {"select", "radio"}:
            for opt in fld.get("options", []):
                if opt.get("value") == val:
                    total += to_decimal(opt.get("price", 0)) or Decimal("0")
                    if "multiplier" in opt:
                        m = to_decimal(opt["multiplier"])
                        if m is not None:
                            multipliers.append(m)
                    break
        elif ftype == "checkbox":
            selected = val if isinstance(val, list) else []
            for opt in fld.get("options", []):
                if opt.get("value") in selected:
                    total += to_decimal(opt.get("price", 0)) or Decimal("0")
        elif ftype == "toggle":
            if bool(val):
                total += to_decimal(fld.get("price", 0)) or Decimal("0")

    for mult in multipliers:
        total *= mult

    # Conditional rules
    triggered_rules: List[str] = []
    dynamic_min: Optional[Decimal] = None
    dynamic_max: Optional[Decimal] = None

    for idx, rule in enumerate(schema.get("rules", [])):
        rid = str(rule.get("id") or f"rule_{idx}")
        conditions = rule.get("conditions", [])
        logic = rule.get("logic", "AND")
        if not conditions:
            continue
        results = [_eval_condition(c, resolved) for c in conditions]
        matched = all(results) if logic == "AND" else any(results)
        if not matched:
            continue

        triggered_rules.append(rid)
        action = rule.get("action")
        rval = to_decimal(rule.get("value"))

        if action == "add" and rval is not None:
            total += rval
        elif action == "multiply" and rval is not None:
            total *= rval
        elif action == "discount_percent" and rval is not None:
            total *= (Decimal("100") - rval) / Decimal("100")
        elif action == "set_min" and rval is not None:
            dynamic_min = rval if dynamic_min is None else max(dynamic_min, rval)
        elif action == "set_max" and rval is not None:
            dynamic_max = rval if dynamic_max is None else min(dynamic_max, rval)

    raw_price = total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    eff_min = to_decimal(schema.get("min_price")) if "min_price" in schema else None
    if dynamic_min is not None:
        eff_min = dynamic_min if eff_min is None else max(eff_min, dynamic_min)

    eff_max = to_decimal(schema.get("max_price")) if "max_price" in schema else None
    if dynamic_max is not None:
        eff_max = dynamic_max if eff_max is None else min(eff_max, dynamic_max)

    final_price = raw_price
    clamped_min = False
    clamped_max = False

    if eff_min is not None and final_price < eff_min:
        final_price = eff_min.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        clamped_min = True
    if eff_max is not None and final_price > eff_max:
        final_price = eff_max.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        clamped_max = True

    currency_cfg = schema.get("currency", {}) if isinstance(schema.get("currency"), dict) else {}
    formatted = format_currency(final_price, currency_cfg)

    return QuoteResult(
        raw_price=raw_price,
        final_price=final_price,
        formatted_price=formatted,
        triggered_rules=triggered_rules,
        clamped_to_min=clamped_min,
        clamped_to_max=clamped_max,
    )


def _field_candidate_values(fld: Dict[str, Any]) -> Tuple[List[Any], List[str]]:
    """Generate representative boundary values for a field and note unbounded risks."""
    ftype = fld.get("type")
    fid = fld.get("id", "unknown")
    unbounded: List[str] = []

    if ftype in {"number", "range"}:
        fmin = to_decimal(fld.get("min"))
        fmax = to_decimal(fld.get("max"))
        fdef = to_decimal(fld.get("default"))
        vals: List[Any] = []
        if fmin is None:
            unbounded.append(f"Field '{fid}' has no 'min' bound (risk of negative quantity input).")
            vals.extend([Decimal("-10"), Decimal("0")])
        else:
            vals.append(fmin)
        if fdef is not None and fdef not in vals:
            vals.append(fdef)
        if fmax is None:
            unbounded.append(f"Field '{fid}' has no 'max' bound (unbounded price output possible).")
            vals.append(Decimal("10000"))
        else:
            if fmin is not None and fmax > fmin:
                mid = ((fmin + fmax) / Decimal("2")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
                if mid not in vals:
                    vals.append(mid)
            if fmax not in vals:
                vals.append(fmax)
        return vals, unbounded

    if ftype in {"select", "radio"}:
        opts = [o.get("value") for o in fld.get("options", []) if isinstance(o, dict) and "value" in o]
        return (opts or [""]), unbounded

    if ftype == "checkbox":
        opts = [o.get("value") for o in fld.get("options", []) if isinstance(o, dict) and "value" in o]
        candidates: List[Any] = [[]]
        if opts:
            candidates.append([opts[0]])
            if len(opts) > 1:
                candidates.append(opts)
        return candidates, unbounded

    if ftype == "toggle":
        return [False, True], unbounded

    return ["", "sample"], unbounded


def fuzz_scenario_matrix(schema: Dict[str, Any], max_scenarios: int = 256, seed: int = 42) -> FuzzReport:
    """Run deterministic boundary & scenario matrix fuzzing on a calculator schema."""
    fields = schema.get("fields", [])
    field_ids: List[str] = []
    value_lists: List[List[Any]] = []
    unbounded_warnings: List[str] = []

    for fld in fields:
        fid = fld.get("id")
        if not fid:
            continue
        vals, ub = _field_candidate_values(fld)
        field_ids.append(fid)
        value_lists.append(vals)
        unbounded_warnings.extend(ub)

    if "max_price" not in schema:
        unbounded_warnings.append("Schema has no global 'max_price' cap.")

    total_combinations = 1
    for vl in value_lists:
        total_combinations *= max(1, len(vl))

    scenarios: List[Dict[str, Any]] = []
    if not field_ids:
        scenarios.append({})
        sampling_mode = "exhaustive"
    elif total_combinations <= max_scenarios:
        sampling_mode = "exhaustive"
        for combo in itertools.product(*value_lists):
            scenarios.append(dict(zip(field_ids, combo)))
    else:
        sampling_mode = "stratified"
        # Include all-min, all-max, and single-field sweeps first
        base_min = {fid: vl[0] for fid, vl in zip(field_ids, value_lists)}
        base_max = {fid: vl[-1] for fid, vl in zip(field_ids, value_lists)}
        scenarios.append(base_min)
        scenarios.append(base_max)
        for idx, (fid, vl) in enumerate(zip(field_ids, value_lists)):
            for v in vl:
                s = dict(base_min)
                s[fid] = v
                scenarios.append(s)
                if len(scenarios) >= max_scenarios:
                    break
            if len(scenarios) >= max_scenarios:
                break
        rng = random.Random(seed)
        while len(scenarios) < max_scenarios:
            s = {fid: rng.choice(vl) for fid, vl in zip(field_ids, value_lists)}
            scenarios.append(s)

    rule_ids = [str(r.get("id") or f"rule_{i}") for i, r in enumerate(schema.get("rules", []))]
    rule_hit_counts: Dict[str, int] = {rid: 0 for rid in rule_ids}

    min_raw: Optional[Decimal] = None
    max_raw: Optional[Decimal] = None
    min_final: Optional[Decimal] = None
    max_final: Optional[Decimal] = None
    negative_scenarios: List[Dict[str, Any]] = []

    for scen in scenarios:
        res = evaluate_quote(schema, scen)
        if min_raw is None or res.raw_price < min_raw:
            min_raw = res.raw_price
        if max_raw is None or res.raw_price > max_raw:
            max_raw = res.raw_price
        if min_final is None or res.final_price < min_final:
            min_final = res.final_price
        if max_final is None or res.final_price > max_final:
            max_final = res.final_price

        if res.raw_price < 0 or res.final_price < 0:
            negative_scenarios.append(
                {
                    "inputs": {k: (str(v) if isinstance(v, Decimal) else v) for k, v in scen.items()},
                    "raw_price": str(res.raw_price),
                    "final_price": str(res.final_price),
                }
            )

        for rid in res.triggered_rules:
            rule_hit_counts[rid] = rule_hit_counts.get(rid, 0) + 1

    dead_rules = [rid for rid, hits in rule_hit_counts.items() if hits == 0]

    return FuzzReport(
        scenarios_tested=len(scenarios),
        sampling_mode=sampling_mode,
        min_raw_price=min_raw if min_raw is not None else Decimal("0.00"),
        max_raw_price=max_raw if max_raw is not None else Decimal("0.00"),
        min_final_price=min_final if min_final is not None else Decimal("0.00"),
        max_final_price=max_final if max_final is not None else Decimal("0.00"),
        negative_scenarios=negative_scenarios,
        unbounded_warnings=unbounded_warnings,
        dead_rules=dead_rules,
        rule_hit_counts=rule_hit_counts,
    )


def generate_shortcode(
    schema: Dict[str, Any],
    theme: str = "modern",
    ajax: bool = True,
) -> str:
    """Generate a clean WordPress [lead_calc] shortcode from the schema."""
    calc_id = str(schema.get("id", "lead_calc_1")).replace('"', "")
    slug = str(schema.get("slug", calc_id)).replace('"', "")
    title = str(schema.get("title", "Quote Calculator")).replace('"', "'")
    currency_code = "USD"
    if isinstance(schema.get("currency"), dict):
        currency_code = str(schema["currency"].get("code", "USD")).replace('"', "")
    ajax_str = "true" if ajax else "false"
    return (
        f'[lead_calc id="{calc_id}" slug="{slug}" '
        f'currency="{currency_code}" theme="{theme}" ajax="{ajax_str}" title="{title}"]'
    )


def render_markdown_report(
    schema: Dict[str, Any],
    validation: ValidationResult,
    fuzz: FuzzReport,
    shortcode: str,
) -> str:
    """Render a human-readable Markdown audit report."""
    title = schema.get("title", "Untitled Calculator")
    calc_id = schema.get("id", "unknown")
    currency_cfg = schema.get("currency", {}) if isinstance(schema.get("currency"), dict) else {}

    lines: List[str] = [
        f"# WordPress Lead Calculator Audit: {title}",
        "",
        f"- **Schema ID:** `{calc_id}`",
        f"- **Validation Status:** {'PASS' if validation.valid else 'FAIL'} "
        f"({len(validation.errors)} errors, {len(validation.warnings)} warnings)",
        f"- **Scenarios Tested:** {fuzz.scenarios_tested} (`{fuzz.sampling_mode}`)",
        f"- **Observed Raw Price Range:** {format_currency(fuzz.min_raw_price, currency_cfg)} "
        f"to {format_currency(fuzz.max_raw_price, currency_cfg)}",
        f"- **Observed Final Price Range:** {format_currency(fuzz.min_final_price, currency_cfg)} "
        f"to {format_currency(fuzz.max_final_price, currency_cfg)}",
        f"- **Negative Price Scenarios:** {len(fuzz.negative_scenarios)}",
        f"- **Dead / Untriggered Rules:** {len(fuzz.dead_rules)}",
        "",
        "## Generated WordPress Shortcode",
        "",
        "```text",
        shortcode,
        "```",
        "",
        "## Fields Summary",
        "",
        "| Field ID | Type | Required | Bounds / Options |",
        "| --- | --- | --- | --- |",
    ]

    for fld in schema.get("fields", []):
        fid = fld.get("id", "-")
        ftype = fld.get("type", "-")
        req = "Yes" if fld.get("required") else "No"
        if ftype in {"number", "range"}:
            detail = f"min={fld.get('min', '-')}, max={fld.get('max', '-')}, step={fld.get('step', '-')}"
        elif ftype in {"select", "radio", "checkbox"}:
            detail = f"{len(fld.get('options', []))} options"
        else:
            detail = f"default={fld.get('default', '-')}"
        lines.append(f"| `{fid}` | `{ftype}` | {req} | {detail} |")

    if validation.errors or validation.warnings:
        lines.extend(["", "## Validation Findings", ""])
        for issue in validation.errors + validation.warnings:
            lines.append(
                f"- **[{issue.severity.upper()}]** `{issue.code}` at `{issue.path}`: {issue.message}"
            )

    if fuzz.unbounded_warnings:
        lines.extend(["", "## Unbounded Pricing Risks", ""])
        for w in fuzz.unbounded_warnings:
            lines.append(f"- {w}")

    if fuzz.dead_rules:
        lines.extend(["", "## Untriggered Rules in Matrix", ""])
        for rid in fuzz.dead_rules:
            lines.append(f"- `{rid}`")

    lines.append("")
    return "\n".join(lines)


def render_json_report(
    schema: Dict[str, Any],
    validation: ValidationResult,
    fuzz: FuzzReport,
    shortcode: str,
) -> str:
    """Render a structured JSON audit report."""
    payload = {
        "schema_id": schema.get("id"),
        "title": schema.get("title"),
        "shortcode": shortcode,
        "validation": validation.to_dict(),
        "fuzz_report": fuzz.to_dict(),
    }
    return json.dumps(payload, indent=2)
