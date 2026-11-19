# WordPress Lead Calculator Schema Validator CLI (Lite)

Zero-dependency Python 3.9+ CLI utility to validate WordPress interactive quote & lead calculator JSON schemas, detect unreachable conditional branches, fuzz pricing edge cases with exact `Decimal` arithmetic, and generate `[lead_calc]` shortcodes + Markdown/JSON audit reports.

---

## Why Use This Tool?

Broken quote calculators silently lose leads or quote negative/absurd prices. Before deploying a WordPress pricing calculator schema to production, run this CLI to verify:

- **Schema Integrity:** Validates field types (`number`, `range`, `select`, `radio`, `checkbox`, `toggle`, `text`), min/max/step alignment, option uniqueness, and currency formatting.
- **Unreachable Branch Detection:** Statically catches contradictory `AND` conditions (e.g., `pages > 20 AND pages < 10`), out-of-bounds thresholds, and impossible option matches.
- **Decimal Scenario Matrix Fuzzing:** Eliminates floating-point drift by testing boundary & pairwise combinations using `decimal.Decimal` to catch negative prices, unbounded multipliers, and dead rules.
- **WordPress Shortcode & Audit Reports:** Outputs ready-to-paste `[lead_calc ...]` shortcodes alongside Markdown or JSON audit reports for CI/CD.

---

## Quick Start (Pure Python Stdlib — Zero `pip install`)

```bash
git clone https://github.com/Kreqwaii/wordpress-lead-calculator-schema-validator-cli-lite.git
cd wordpress-lead-calculator-schema-validator-cli-lite
```

### 1. Validate a Calculator Schema

```bash
py wp_calc_cli.py validate sample_calculator.json --strict
```

### 2. Run Full Pricing Audit & Fuzzing Matrix

```bash
py wp_calc_cli.py audit sample_calculator.json --format markdown --out audit_report.md --strict
```

### 3. Generate WordPress `[lead_calc]` Shortcode

```bash
py wp_calc_cli.py shortcode sample_calculator.json --theme modern
# Output:
# [lead_calc id="wp_web_agency_quote_v1" slug="web-agency-quote" currency="USD" theme="modern" ajax="true" title="Website & SEO Lead Quote Calculator"]
```

### 4. Evaluate a Single Quote

```bash
py wp_calc_cli.py quote sample_calculator.json --input '{"site_tier": "ecommerce", "pages_count": 20, "rush_delivery": true}'
```

---

## Sample Audit Output

| Metric | Result (`sample_calculator.json`) |
| --- | --- |
| Validation Status | PASS (0 errors, 0 warnings) |
| Scenarios Tested | 216 (`exhaustive`) |
| Observed Final Price Range | `$575.00` to `$9,507.10` |
| Negative Price Scenarios | `0` |
| Dead / Untriggered Rules | `0` |

---

## Running Tests

```bash
py -m unittest discover -v
```

---

## Краткое описание (RU)

Лёгкая CLI-утилита на чистом Python 3.9+ (без сторонних зависимостей) для валидации JSON-схем калькуляторов стоимости и лид-форм WordPress:
- Проверяет поля, диапазоны `min`/`max`/`step`, опции, валюту и условные правила.
- Находит недостижимые ветки условий, отрицательные цены и неограниченные множители через `Decimal`-фаззинг матрицы сценариев.
- Генерирует шорткод `[lead_calc]` и отчёт аудита в Markdown или JSON.

---

## Need Full WordPress Interactive Quote Calculator Kit?

This repository is the **Lite CLI schema validator**. If you need the complete production WordPress plugin with visual admin builder, live frontend UI, PDF/HTML quote export, and CRM webhooks:

- **Full Pro Version:** [Ready WordPress Lead Calculator & Quote Builder Plugin Kit on FunPay](https://funpay.com/lots/offer?id=82598059) — includes admin builder, `[lead_calc]` shortcode renderer, PDF/HTML quote output & CRM webhook with instant source delivery.

---

## License

MIT License — see [LICENSE](LICENSE).
