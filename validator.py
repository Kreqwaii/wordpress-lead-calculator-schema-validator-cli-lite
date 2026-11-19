"""Schema validator for WordPress interactive lead/quote calculator JSON."""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Set, Tuple

VALID_FIELD_TYPES: Set[str] = {
    "number",
    "range",
    "select",
    "radio",
    "checkbox",
    "toggle",
    "text",
}

NUMERIC_FIELD_TYPES: Set[str] = {"number", "range"}
OPTION_FIELD_TYPES: Set[str] = {"select", "radio", "checkbox"}

VALID_OPERATORS: Set[str] = {
    "==",
    "!=",
    ">",
    ">=",
    "<",
    "<=",
    "in",
    "not_in",
    "contains",
}

NUMERIC_OPERATORS: Set[str] = {">", ">=", "<", "<="}

VALID_RULE_ACTIONS: Set[str] = {
    "add",
    "multiply",
    "discount_percent",
    "set_min",
    "set_max",
    "show",
    "hide",
}

PRICING_RULE_ACTIONS: Set[str] = {
    "add",
    "multiply",
    "discount_percent",
    "set_min",
    "set_max",
}

VALID_CURRENCY_POSITIONS: Set[str] = {"prefix", "suffix"}


@dataclass(frozen=True)
class ValidationIssue:
    """Represents a single schema validation error or warning."""

    severity: str  # "error" | "warning"
    code: str
    path: str
    message: str

    def to_dict(self) -> Dict[str, str]:
        return {
            "severity": self.severity,
            "code": self.code,
            "path": self.path,
            "message": self.message,
        }


@dataclass
class ValidationResult:
    """Aggregated validation outcome for a calculator schema."""

    valid: bool
    errors: List[ValidationIssue] = dc_field(default_factory=list)
    warnings: List[ValidationIssue] = dc_field(default_factory=list)
    unreachable_rules: List[str] = dc_field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "valid": self.valid,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "unreachable_rules": list(self.unreachable_rules),
            "errors": [e.to_dict() for e in self.errors],
            "warnings": [w.to_dict() for w in self.warnings],
        }


def to_decimal(value: Any) -> Optional[Decimal]:
    """Safely convert int/float/str/Decimal to Decimal, rejecting bool/NaN/Inf."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        dec = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not dec.is_finite():
        return None
    return dec


def _is_step_aligned(value: Decimal, minimum: Decimal, step: Decimal) -> bool:
    if step <= 0:
        return False
    remainder = (value - minimum) % step
    return remainder == Decimal("0")


def validate_schema(schema: Any) -> ValidationResult:
    """Validate a WordPress lead calculator JSON schema and detect unreachable rules."""
    errors: List[ValidationIssue] = []
    warnings: List[ValidationIssue] = []
    unreachable_rules: List[str] = []

    def add_error(code: str, path: str, message: str) -> None:
        errors.append(ValidationIssue("error", code, path, message))

    def add_warning(code: str, path: str, message: str) -> None:
        warnings.append(ValidationIssue("warning", code, path, message))

    if not isinstance(schema, dict):
        add_error("SCHEMA_NOT_OBJECT", "$", "Top-level schema must be a JSON object.")
        return ValidationResult(valid=False, errors=errors, warnings=warnings)

    # 1. Top-level metadata
    for key in ("id", "title"):
        val = schema.get(key)
        if not isinstance(val, str) or not val.strip():
            add_error("MISSING_METADATA", f"$.{key}", f"Required non-empty string '{key}' is missing.")

    slug = schema.get("slug")
    if slug is not None and (not isinstance(slug, str) or not slug.strip() or " " in slug):
        add_warning(
            "INVALID_SLUG",
            "$.slug",
            "Slug should be a non-empty URL-safe string without spaces.",
        )

    # 2. Base price & bounds
    base_price = to_decimal(schema.get("base_price", 0))
    if base_price is None:
        add_error("INVALID_BASE_PRICE", "$.base_price", "base_price must be a valid finite number.")
    elif base_price < 0:
        add_warning("NEGATIVE_BASE_PRICE", "$.base_price", "base_price is negative.")

    min_price = to_decimal(schema.get("min_price")) if "min_price" in schema else None
    if "min_price" in schema and min_price is None:
        add_error("INVALID_MIN_PRICE", "$.min_price", "min_price must be a valid finite number.")

    max_price = to_decimal(schema.get("max_price")) if "max_price" in schema else None
    if "max_price" in schema and max_price is None:
        add_error("INVALID_MAX_PRICE", "$.max_price", "max_price must be a valid finite number.")

    if min_price is not None and max_price is not None and min_price > max_price:
        add_error(
            "PRICE_BOUNDS_INVERTED",
            "$.min_price",
            f"min_price ({min_price}) cannot exceed max_price ({max_price}).",
        )

    # 3. Currency config
    currency = schema.get("currency")
    if not isinstance(currency, dict):
        add_error("MISSING_CURRENCY", "$.currency", "currency configuration object is required.")
    else:
        code = currency.get("code")
        if not isinstance(code, str) or len(code.strip()) != 3:
            add_error("INVALID_CURRENCY_CODE", "$.currency.code", "currency.code must be a 3-letter ISO code.")
        symbol = currency.get("symbol")
        if not isinstance(symbol, str) or not symbol.strip():
            add_error("INVALID_CURRENCY_SYMBOL", "$.currency.symbol", "currency.symbol must be a non-empty string.")
        decimals = currency.get("decimals", 2)
        if isinstance(decimals, bool) or not isinstance(decimals, int) or not (0 <= decimals <= 4):
            add_error("INVALID_CURRENCY_DECIMALS", "$.currency.decimals", "currency.decimals must be an integer 0..4.")
        position = currency.get("position", "prefix")
        if position not in VALID_CURRENCY_POSITIONS:
            add_error(
                "INVALID_CURRENCY_POSITION",
                "$.currency.position",
                f"currency.position must be one of {sorted(VALID_CURRENCY_POSITIONS)}.",
            )

    # 4. Fields validation
    fields_raw = schema.get("fields")
    fields_by_id: Dict[str, Dict[str, Any]] = {}
    if not isinstance(fields_raw, list) or len(fields_raw) == 0:
        add_error("MISSING_FIELDS", "$.fields", "fields must be a non-empty list of field definitions.")
    else:
        for idx, fld in enumerate(fields_raw):
            fpath = f"$.fields[{idx}]"
            if not isinstance(fld, dict):
                add_error("INVALID_FIELD_OBJECT", fpath, "Each field must be a JSON object.")
                continue

            fid = fld.get("id")
            if not isinstance(fid, str) or not fid.strip():
                add_error("MISSING_FIELD_ID", f"{fpath}.id", "Field 'id' must be a non-empty string.")
                continue
            if fid in fields_by_id:
                add_error("DUPLICATE_FIELD_ID", f"{fpath}.id", f"Duplicate field id '{fid}'.")
                continue
            fields_by_id[fid] = fld

            ftype = fld.get("type")
            if ftype not in VALID_FIELD_TYPES:
                add_error(
                    "INVALID_FIELD_TYPE",
                    f"{fpath}.type",
                    f"Unsupported field type '{ftype}'. Allowed: {sorted(VALID_FIELD_TYPES)}.",
                )
                continue

            label = fld.get("label")
            if not isinstance(label, str) or not label.strip():
                add_warning("MISSING_FIELD_LABEL", f"{fpath}.label", f"Field '{fid}' should have a non-empty label.")

            if ftype in NUMERIC_FIELD_TYPES:
                _validate_numeric_field(fld, fpath, add_error, add_warning)
            elif ftype in OPTION_FIELD_TYPES:
                _validate_option_field(fld, ftype, fpath, add_error, add_warning)
            elif ftype == "toggle":
                if "price" in fld and to_decimal(fld["price"]) is None:
                    add_error("INVALID_TOGGLE_PRICE", f"{fpath}.price", "toggle price must be a finite number.")
                if "default" in fld and not isinstance(fld["default"], bool):
                    add_error("INVALID_TOGGLE_DEFAULT", f"{fpath}.default", "toggle default must be boolean.")

    # 5. Conditional rules validation & unreachable branch detection
    rules_raw = schema.get("rules", [])
    if not isinstance(rules_raw, list):
        add_error("INVALID_RULES_LIST", "$.rules", "rules must be a list if provided.")
    else:
        seen_rule_ids: Set[str] = set()
        for idx, rule in enumerate(rules_raw):
            rpath = f"$.rules[{idx}]"
            if not isinstance(rule, dict):
                add_error("INVALID_RULE_OBJECT", rpath, "Each rule must be a JSON object.")
                continue

            rid = rule.get("id") or f"rule_{idx}"
            if not isinstance(rid, str) or not rid.strip():
                add_error("INVALID_RULE_ID", f"{rpath}.id", "Rule id must be a non-empty string.")
                rid = f"rule_{idx}"
            elif rid in seen_rule_ids:
                add_warning("DUPLICATE_RULE_ID", f"{rpath}.id", f"Duplicate rule id '{rid}'.")
            seen_rule_ids.add(rid)

            logic = rule.get("logic", "AND")
            if logic not in {"AND", "OR"}:
                add_error("INVALID_RULE_LOGIC", f"{rpath}.logic", "Rule logic must be 'AND' or 'OR'.")

            action = rule.get("action")
            if action not in VALID_RULE_ACTIONS:
                add_error(
                    "INVALID_RULE_ACTION",
                    f"{rpath}.action",
                    f"Unsupported rule action '{action}'. Allowed: {sorted(VALID_RULE_ACTIONS)}.",
                )
            elif action in PRICING_RULE_ACTIONS:
                rval = to_decimal(rule.get("value"))
                if rval is None:
                    add_error(
                        "INVALID_RULE_VALUE",
                        f"{rpath}.value",
                        f"Pricing action '{action}' requires a valid numeric 'value'.",
                    )
                elif action == "discount_percent" and (rval < 0 or rval > 100):
                    add_warning(
                        "EXTREME_DISCOUNT",
                        f"{rpath}.value",
                        f"discount_percent ({rval}%) is outside 0..100.",
                    )
                elif action == "multiply" and rval < 0:
                    add_warning(
                        "NEGATIVE_MULTIPLIER",
                        f"{rpath}.value",
                        f"Negative multiplier ({rval}) can produce negative quotes.",
                    )
            elif action in {"show", "hide"}:
                target = rule.get("target_field")
                if not isinstance(target, str) or target not in fields_by_id:
                    add_error(
                        "UNKNOWN_TARGET_FIELD",
                        f"{rpath}.target_field",
                        f"Action '{action}' references unknown target_field '{target}'.",
                    )

            conditions = rule.get("conditions", [])
            if not isinstance(conditions, list) or len(conditions) == 0:
                add_error("MISSING_RULE_CONDITIONS", f"{rpath}.conditions", "Rule must have a non-empty conditions list.")
                continue

            valid_conditions = True
            for cidx, cond in enumerate(conditions):
                cpath = f"{rpath}.conditions[{cidx}]"
                if not isinstance(cond, dict):
                    add_error("INVALID_CONDITION", cpath, "Each condition must be an object.")
                    valid_conditions = False
                    continue

                cfield = cond.get("field")
                cop = cond.get("op")
                if not isinstance(cfield, str) or cfield not in fields_by_id:
                    add_error(
                        "UNKNOWN_CONDITION_FIELD",
                        f"{cpath}.field",
                        f"Condition references unknown field '{cfield}'.",
                    )
                    valid_conditions = False
                    continue

                if cop not in VALID_OPERATORS:
                    add_error(
                        "INVALID_OPERATOR",
                        f"{cpath}.op",
                        f"Unsupported operator '{cop}'. Allowed: {sorted(VALID_OPERATORS)}.",
                    )
                    valid_conditions = False
                    continue

                fld_def = fields_by_id[cfield]
                if cop in NUMERIC_OPERATORS and fld_def.get("type") not in NUMERIC_FIELD_TYPES:
                    add_error(
                        "INCOMPATIBLE_OPERATOR",
                        f"{cpath}.op",
                        f"Numeric operator '{cop}' cannot be used on field '{cfield}' of type '{fld_def.get('type')}'.",
                    )
                    valid_conditions = False
                elif cop in NUMERIC_OPERATORS and to_decimal(cond.get("value")) is None:
                    add_error(
                        "INVALID_CONDITION_VALUE",
                        f"{cpath}.value",
                        f"Numeric operator '{cop}' requires a numeric value.",
                    )
                    valid_conditions = False

            if valid_conditions:
                reason = _detect_unreachable_rule(conditions, logic, fields_by_id)
                if reason:
                    unreachable_rules.append(rid)
                    add_warning(
                        "UNREACHABLE_RULE_BRANCH",
                        rpath,
                        f"Rule '{rid}' appears unreachable: {reason}",
                    )

    return ValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
        unreachable_rules=unreachable_rules,
    )


def _validate_numeric_field(fld: Dict[str, Any], fpath: str, add_error: Any, add_warning: Any) -> None:
    min_val = to_decimal(fld["min"]) if "min" in fld else None
    max_val = to_decimal(fld["max"]) if "max" in fld else None
    step_val = to_decimal(fld["step"]) if "step" in fld else None

    if "min" in fld and min_val is None:
        add_error("INVALID_FIELD_MIN", f"{fpath}.min", "min must be a valid finite number.")
    if "max" in fld and max_val is None:
        add_error("INVALID_FIELD_MAX", f"{fpath}.max", "max must be a valid finite number.")
    if min_val is not None and max_val is not None and min_val > max_val:
        add_error("FIELD_RANGE_INVERTED", f"{fpath}.min", f"min ({min_val}) cannot exceed max ({max_val}).")

    if "step" in fld:
        if step_val is None or step_val <= 0:
            add_error("INVALID_FIELD_STEP", f"{fpath}.step", "step must be a positive number > 0.")
            step_val = None

    if fld.get("type") == "range" and (min_val is None or max_val is None):
        add_error("RANGE_MISSING_BOUNDS", fpath, "range field requires both numeric min and max.")

    if "unit_price" in fld and to_decimal(fld["unit_price"]) is None:
        add_error("INVALID_UNIT_PRICE", f"{fpath}.unit_price", "unit_price must be a valid finite number.")

    if "default" in fld:
        dval = to_decimal(fld["default"])
        if dval is None:
            add_error("INVALID_FIELD_DEFAULT", f"{fpath}.default", "Numeric field default must be a finite number.")
        else:
            if min_val is not None and dval < min_val:
                add_error("DEFAULT_BELOW_MIN", f"{fpath}.default", f"default ({dval}) is below min ({min_val}).")
            if max_val is not None and dval > max_val:
                add_error("DEFAULT_ABOVE_MAX", f"{fpath}.default", f"default ({dval}) exceeds max ({max_val}).")
            if min_val is not None and step_val is not None and not _is_step_aligned(dval, min_val, step_val):
                add_warning(
                    "DEFAULT_NOT_STEP_ALIGNED",
                    f"{fpath}.default",
                    f"default ({dval}) is not aligned with min ({min_val}) + k * step ({step_val}).",
                )


def _validate_option_field(
    fld: Dict[str, Any], ftype: str, fpath: str, add_error: Any, add_warning: Any
) -> None:
    options = fld.get("options")
    if not isinstance(options, list) or len(options) == 0:
        add_error("MISSING_OPTIONS", f"{fpath}.options", f"Field type '{ftype}' requires a non-empty options list.")
        return

    seen_values: Set[str] = set()
    for oidx, opt in enumerate(options):
        opath = f"{fpath}.options[{oidx}]"
        if not isinstance(opt, dict):
            add_error("INVALID_OPTION_OBJECT", opath, "Each option must be an object.")
            continue
        oval = opt.get("value")
        if not isinstance(oval, str) or not oval.strip():
            add_error("MISSING_OPTION_VALUE", f"{opath}.value", "Option value must be a non-empty string.")
            continue
        if oval in seen_values:
            add_error("DUPLICATE_OPTION_VALUE", f"{opath}.value", f"Duplicate option value '{oval}'.")
        seen_values.add(oval)

        if "price" in opt and to_decimal(opt["price"]) is None:
            add_error("INVALID_OPTION_PRICE", f"{opath}.price", "Option price must be a finite number.")
        if "multiplier" in opt:
            mult = to_decimal(opt["multiplier"])
            if mult is None:
                add_error("INVALID_OPTION_MULTIPLIER", f"{opath}.multiplier", "Option multiplier must be finite.")
            elif mult < 0:
                add_warning("NEGATIVE_MULTIPLIER", f"{opath}.multiplier", "Negative option multiplier detected.")

    if "default" in fld:
        dval = fld["default"]
        if ftype == "checkbox":
            if not isinstance(dval, list) or any(v not in seen_values for v in dval):
                add_error(
                    "INVALID_CHECKBOX_DEFAULT",
                    f"{fpath}.default",
                    "checkbox default must be a list of valid option values.",
                )
        else:
            if not isinstance(dval, str) or dval not in seen_values:
                add_error(
                    "INVALID_OPTION_DEFAULT",
                    f"{fpath}.default",
                    f"default '{dval}' is not in field options {sorted(seen_values)}.",
                )


def _detect_unreachable_rule(
    conditions: List[Dict[str, Any]],
    logic: str,
    fields_by_id: Dict[str, Dict[str, Any]],
) -> Optional[str]:
    """Static satisfiability check for single-condition bounds and AND contradictions."""
    # Check each condition against field domain
    cond_outcomes: List[Optional[str]] = []
    for cond in conditions:
        fid = cond["field"]
        op = cond["op"]
        val = cond.get("value")
        fld = fields_by_id[fid]
        ftype = fld.get("type")

        if ftype in NUMERIC_FIELD_TYPES:
            fmin = to_decimal(fld["min"]) if "min" in fld else None
            fmax = to_decimal(fld["max"]) if "max" in fld else None
            cval = to_decimal(val)
            if cval is not None:
                if op == ">" and fmax is not None and cval >= fmax:
                    cond_outcomes.append(f"{fid} > {cval} exceeds max ({fmax})")
                    continue
                if op == ">=" and fmax is not None and cval > fmax:
                    cond_outcomes.append(f"{fid} >= {cval} exceeds max ({fmax})")
                    continue
                if op == "<" and fmin is not None and cval <= fmin:
                    cond_outcomes.append(f"{fid} < {cval} is below min ({fmin})")
                    continue
                if op == "<=" and fmin is not None and cval < fmin:
                    cond_outcomes.append(f"{fid} <= {cval} is below min ({fmin})")
                    continue
                if op == "==":
                    if (fmin is not None and cval < fmin) or (fmax is not None and cval > fmax):
                        cond_outcomes.append(f"{fid} == {cval} is outside [{fmin}, {fmax}]")
                        continue
        elif ftype in {"select", "radio"}:
            opt_vals = {o.get("value") for o in fld.get("options", []) if isinstance(o, dict)}
            if op == "==" and val not in opt_vals:
                cond_outcomes.append(f"{fid} == '{val}' matches no option in {sorted(opt_vals)}")
                continue
            if op == "in":
                if not isinstance(val, list) or not any(v in opt_vals for v in val):
                    cond_outcomes.append(f"{fid} in {val} has no overlap with options {sorted(opt_vals)}")
                    continue

        cond_outcomes.append(None)

    if logic == "AND":
        for reason in cond_outcomes:
            if reason is not None:
                return reason
        # Check cross-condition contradictions on the same field
        by_field: Dict[str, List[Tuple[str, Any]]] = {}
        for cond in conditions:
            by_field.setdefault(cond["field"], []).append((cond["op"], cond.get("value")))

        for fid, pairs in by_field.items():
            fld = fields_by_id[fid]
            if fld.get("type") in NUMERIC_FIELD_TYPES:
                low: Optional[Decimal] = None
                low_inc = True
                high: Optional[Decimal] = None
                high_inc = True
                eq_vals: Set[Decimal] = set()
                for op, val in pairs:
                    d = to_decimal(val)
                    if d is None:
                        continue
                    if op == "==":
                        eq_vals.add(d)
                    elif op in {">", ">="}:
                        if low is None or d > low or (d == low and op == ">"):
                            low = d
                            low_inc = op == ">="
                    elif op in {"<", "<="}:
                        if high is None or d < high or (d == high and op == "<"):
                            high = d
                            high_inc = op == "<="
                if len(eq_vals) > 1:
                    return f"contradictory equality constraints on '{fid}': {sorted(eq_vals)}"
                if low is not None and high is not None:
                    if low > high or (low == high and (not low_inc or not high_inc)):
                        return f"empty numeric interval on '{fid}': lower bound {low} vs upper bound {high}"
                if len(eq_vals) == 1:
                    ev = next(iter(eq_vals))
                    if low is not None and (ev < low or (ev == low and not low_inc)):
                        return f"'{fid} == {ev}' contradicts lower bound {low}"
                    if high is not None and (ev > high or (ev == high and not high_inc)):
                        return f"'{fid} == {ev}' contradicts upper bound {high}"
            else:
                eq_strs = {v for op, v in pairs if op == "=="}
                neq_strs = {v for op, v in pairs if op == "!="}
                if len(eq_strs) > 1:
                    return f"contradictory equality constraints on '{fid}': {sorted(map(str, eq_strs))}"
                if eq_strs & neq_strs:
                    return f"contradictory == and != on '{fid}'"
    else:  # OR
        if cond_outcomes and all(r is not None for r in cond_outcomes):
            return "; ".join(r for r in cond_outcomes if r is not None)

    return None
