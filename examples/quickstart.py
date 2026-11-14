"""Run two simulations from Python and look at what was verified.

    python examples/quickstart.py
"""
import simulate_anything as sa

# 1. A catalogue template, with your own numbers (any compatible units work).
result = sa.run("heat_sink_fin", {
    "base_temperature": "95 degC",
    "fin_length": "40 mm",
    "h": "40 W/(m^2*K)",          # forced air
    "conductivity": "167 W/(m*K)",  # 6061 aluminium
})
print(result.to_text())
print()

# 2. Results are plain numbers; the report says whether to trust them.
watts = result.value("total_heat")
print(f"The sink removes {watts:.1f} W -> {result.report.verdict}")

# 3. A mistake is caught, not silently computed: h given per metre, not per m^2.
bad = sa.run("heat_sink_fin", {"h": "40 W/(m*K)"})
print()
print(bad.report.to_text())

# 4. Save a standalone HTML report with the plots and the full checklist.
from simulate_anything.report import save_html

print("report:", save_html("fin_report.html", result))
