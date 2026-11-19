"""Comprehensive unittest suite for WordPress Lead Calculator Schema Validator CLI."""

from __future__ import annotations

import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal
from pathlib import Path

from evaluator import (
    evaluate_quote,
    format_currency,
    fuzz_scenario_matrix,
    generate_shortcode,
    render_json_report,
    render_markdown_report,
)
from validator import to_decimal, validate_schema
from wp_calc_cli import main

SAMPLE_PATH = Path(__file__).parent / "sample_calculator.json"


def load_sample() -> dict:
    with SAMPLE_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


class TestValidator(unittest.TestCase):
    def test_01_valid_sample_schema_passes_cleanly(self) -> None:
        res = validate_schema(load_sample())
        self.assertTrue(res.valid)
        self.assertEqual(res.errors, [])
        self.assertEqual(res.warnings, [])
        self.assertEqual(res.unreachable_rules, [])

    def test_02_non_dict_schema_fails(self) -> None:
        res = validate_schema(["not", "an", "object"])
        self.assertFalse(res.valid)
        self.assertEqual(res.errors[0].code, "SCHEMA_NOT_OBJECT")

    def test_03_missing_metadata_fails(self) -> None:
        schema = load_sample()
        del schema["id"]
        del schema["title"]
        res = validate_schema(schema)
        self.assertFalse(res.valid)
        codes = {e.code for e in res.errors}
        self.assertIn("MISSING_METADATA", codes)

    def test_04_inverted_global_price_bounds_fails(self) -> None:
        schema = load_sample()
        schema["min_price"] = "5000.00"
        schema["max_price"] = "1000.00"
        res = validate_schema(schema)
        self.assertFalse(res.valid)
        self.assertTrue(any(e.code == "PRICE_BOUNDS_INVERTED" for e in res.errors))

    def test_05_invalid_currency_config_fails(self) -> None:
        schema = load_sample()
        schema["currency"] = {"code": "US", "symbol": "", "decimals": 7, "position": "middle"}
        res = validate_schema(schema)
        self.assertFalse(res.valid)
        codes = {e.code for e in res.errors}
        self.assertIn("INVALID_CURRENCY_CODE", codes)
        self.assertIn("INVALID_CURRENCY_SYMBOL", codes)
        self.assertIn("INVALID_CURRENCY_DECIMALS", codes)
        self.assertIn("INVALID_CURRENCY_POSITION", codes)

    def test_06_duplicate_field_id_fails(self) -> None:
        schema = load_sample()
        schema["fields"].append(copy.deepcopy(schema["fields"][0]))
        res = validate_schema(schema)
        self.assertFalse(res.valid)
        self.assertTrue(any(e.code == "DUPLICATE_FIELD_ID" for e in res.errors))

    def test_07_invalid_field_type_fails(self) -> None:
        schema = load_sample()
        schema["fields"][0]["type"] = "slider_3d"
        res = validate_schema(schema)
        self.assertFalse(res.valid)
        self.assertTrue(any(e.code == "INVALID_FIELD_TYPE" for e in res.errors))

    def test_08_numeric_range_inverted_and_bad_step_fails(self) -> None:
        schema = load_sample()
        schema["fields"][1]["min"] = 50
        schema["fields"][1]["max"] = 10
        schema["fields"][1]["step"] = 0
        res = validate_schema(schema)
        self.assertFalse(res.valid)
        codes = {e.code for e in res.errors}
        self.assertIn("FIELD_RANGE_INVERTED", codes)
        self.assertIn("INVALID_FIELD_STEP", codes)

    def test_09_default_out_of_range_fails(self) -> None:
        schema = load_sample()
        schema["fields"][1]["default"] = 999
        res = validate_schema(schema)
        self.assertFalse(res.valid)
        self.assertTrue(any(e.code == "DEFAULT_ABOVE_MAX" for e in res.errors))

    def test_10_default_not_step_aligned_warns(self) -> None:
        schema = load_sample()
        schema["fields"][1]["min"] = 0
        schema["fields"][1]["max"] = 40
        schema["fields"][1]["step"] = 5
        schema["fields"][1]["default"] = 7
        res = validate_schema(schema)
        self.assertTrue(res.valid)
        self.assertTrue(any(w.code == "DEFAULT_NOT_STEP_ALIGNED" for w in res.warnings))

    def test_11_option_field_duplicate_and_bad_default_fails(self) -> None:
        schema = load_sample()
        schema["fields"][0]["options"].append({"value": "landing", "label": "Dup", "price": "10"})
        schema["fields"][0]["default"] = "nonexistent"
        res = validate_schema(schema)
        self.assertFalse(res.valid)
        codes = {e.code for e in res.errors}
        self.assertIn("DUPLICATE_OPTION_VALUE", codes)
        self.assertIn("INVALID_OPTION_DEFAULT", codes)

    def test_12_unknown_condition_field_fails(self) -> None:
        schema = load_sample()
        schema["rules"][0]["conditions"][0]["field"] = "ghost_field"
        res = validate_schema(schema)
        self.assertFalse(res.valid)
        self.assertTrue(any(e.code == "UNKNOWN_CONDITION_FIELD" for e in res.errors))

    def test_13_incompatible_numeric_operator_on_select_fails(self) -> None:
        schema = load_sample()
        schema["rules"].append(
            {
                "id": "bad_op",
                "logic": "AND",
                "conditions": [{"field": "site_tier", "op": ">", "value": 5}],
                "action": "add",
                "value": "10",
            }
        )
        res = validate_schema(schema)
        self.assertFalse(res.valid)
        self.assertTrue(any(e.code == "INCOMPATIBLE_OPERATOR" for e in res.errors))

    def test_14_unreachable_contradictory_numeric_rule_detected(self) -> None:
        schema = load_sample()
        schema["rules"].append(
            {
                "id": "impossible_interval",
                "logic": "AND",
                "conditions": [
                    {"field": "pages_count", "op": ">", "value": 20},
                    {"field": "pages_count", "op": "<", "value": 10},
                ],
                "action": "add",
                "value": "50",
            }
        )
        res = validate_schema(schema)
        self.assertTrue(res.valid)
        self.assertIn("impossible_interval", res.unreachable_rules)

    def test_15_unreachable_threshold_outside_field_bounds_detected(self) -> None:
        schema = load_sample()
        schema["rules"].append(
            {
                "id": "over_max_pages",
                "logic": "AND",
                "conditions": [{"field": "pages_count", "op": ">", "value": 40}],
                "action": "add",
                "value": "100",
            }
        )
        res = validate_schema(schema)
        self.assertIn("over_max_pages", res.unreachable_rules)

    def test_16_unreachable_invalid_option_in_rule_detected(self) -> None:
        schema = load_sample()
        schema["rules"].append(
            {
                "id": "missing_tier",
                "logic": "AND",
                "conditions": [{"field": "site_tier", "op": "==", "value": "enterprise_plus"}],
                "action": "add",
                "value": "500",
            }
        )
        res = validate_schema(schema)
        self.assertIn("missing_tier", res.unreachable_rules)

    def test_17_to_decimal_rejects_bool_and_nan(self) -> None:
        self.assertIsNone(to_decimal(True))
        self.assertIsNone(to_decimal("NaN"))
        self.assertIsNone(to_decimal("Infinity"))
        self.assertEqual(to_decimal("12.34"), Decimal("12.34"))


class TestEvaluatorAndFuzzer(unittest.TestCase):
    def test_18_evaluate_quote_defaults_exact_decimal(self) -> None:
        schema = load_sample()
        # base 490 + business 650 + 5*85 (425) = 1565.00
        res = evaluate_quote(schema, {})
        self.assertEqual(res.raw_price, Decimal("1565.00"))
        self.assertEqual(res.final_price, Decimal("1565.00"))
        self.assertEqual(res.formatted_price, "$1,565.00")
        self.assertEqual(res.triggered_rules, [])

    def test_19_evaluate_quote_with_multiplier_discount_and_surcharge(self) -> None:
        schema = load_sample()
        # base 490 + ecommerce 1450 + 20*85(1700) + seo 320 + rush 390 = 4350 * 1.15 = 5002.50
        # 10% discount => 4502.25; +250 rush ecommerce surcharge => 4752.25
        res = evaluate_quote(
            schema,
            {
                "site_tier": "ecommerce",
                "pages_count": 20,
                "addons": ["seo_setup"],
                "rush_delivery": True,
                "retainer_months": 0,
            },
        )
        self.assertEqual(res.raw_price, Decimal("4752.25"))
        self.assertIn("rule_enterprise_page_discount", res.triggered_rules)
        self.assertIn("rule_rush_ecommerce_surcharge", res.triggered_rules)

    def test_20_currency_formatting_suffix_and_eu_separators(self) -> None:
        cfg = {
            "symbol": "€",
            "decimals": 2,
            "position": "suffix",
            "thousand_sep": " ",
            "decimal_sep": ",",
        }
        self.assertEqual(format_currency(Decimal("12345.678"), cfg), "12 345,68 €")
        self.assertEqual(format_currency(Decimal("-50"), cfg), "-50,00 €")

    def test_21_fuzz_matrix_exhaustive_on_sample(self) -> None:
        schema = load_sample()
        fuzz = fuzz_scenario_matrix(schema, max_scenarios=500)
        self.assertEqual(fuzz.sampling_mode, "exhaustive")
        self.assertGreater(fuzz.scenarios_tested, 100)
        self.assertEqual(fuzz.negative_scenarios, [])
        self.assertEqual(fuzz.dead_rules, [])
        self.assertGreaterEqual(fuzz.min_final_price, Decimal("350.00"))

    def test_22_fuzz_detects_negative_and_unbounded_outputs(self) -> None:
        schema = {
            "id": "neg_test",
            "title": "Negative Risk Calc",
            "currency": {"code": "USD", "symbol": "$", "decimals": 2, "position": "prefix"},
            "base_price": "100.00",
            "fields": [
                {"id": "qty", "type": "number", "label": "Qty", "unit_price": "10.00"},
            ],
            "rules": [
                {
                    "id": "huge_credit",
                    "logic": "AND",
                    "conditions": [{"field": "qty", "op": ">=", "value": 0}],
                    "action": "add",
                    "value": "-500.00",
                }
            ],
        }
        fuzz = fuzz_scenario_matrix(schema, max_scenarios=64)
        self.assertGreater(len(fuzz.negative_scenarios), 0)
        self.assertGreaterEqual(len(fuzz.unbounded_warnings), 2)

    def test_23_fuzz_detects_dead_rule(self) -> None:
        schema = load_sample()
        schema["rules"].append(
            {
                "id": "never_hits",
                "logic": "AND",
                "conditions": [{"field": "pages_count", "op": ">", "value": 999}],
                "action": "add",
                "value": "50",
            }
        )
        fuzz = fuzz_scenario_matrix(schema, max_scenarios=256)
        self.assertIn("never_hits", fuzz.dead_rules)

    def test_24_shortcode_and_reports_generation(self) -> None:
        schema = load_sample()
        val = validate_schema(schema)
        fuzz = fuzz_scenario_matrix(schema, max_scenarios=128)
        sc = generate_shortcode(schema, theme="minimal", ajax=False)
        self.assertIn('[lead_calc id="wp_web_agency_quote_v1"', sc)
        self.assertIn('theme="minimal"', sc)
        self.assertIn('ajax="false"', sc)

        md = render_markdown_report(schema, val, fuzz, sc)
        self.assertIn("# WordPress Lead Calculator Audit:", md)
        self.assertIn("`site_tier`", md)

        js = render_json_report(schema, val, fuzz, sc)
        parsed = json.loads(js)
        self.assertEqual(parsed["schema_id"], "wp_web_agency_quote_v1")
        self.assertTrue(parsed["validation"]["valid"])


class TestCLI(unittest.TestCase):
    def test_25_cli_validate_valid_schema(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(["validate", str(SAMPLE_PATH)])
        self.assertEqual(code, 0)
        self.assertIn("VALID", buf.getvalue())

    def test_26_cli_audit_writes_markdown_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "audit.md"
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main(["audit", str(SAMPLE_PATH), "--format", "markdown", "--out", str(out_path), "--strict"])
            self.assertEqual(code, 0)
            self.assertTrue(out_path.is_file())
            content = out_path.read_text(encoding="utf-8")
            self.assertIn("[lead_calc", content)

    def test_27_cli_quote_command(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(["quote", str(SAMPLE_PATH), "--input", '{"site_tier": "landing", "pages_count": 1}'])
        self.assertEqual(code, 0)
        data = json.loads(buf.getvalue())
        self.assertEqual(data["final_price"], "575.00")

    def test_28_cli_missing_file_returns_exit_2(self) -> None:
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(["validate", "does_not_exist_12345.json"])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
