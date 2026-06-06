# ManuAI — CSV Data Files

These files are your mock data store. Open any of them in **Excel**, edit the numbers, save, and restart the API — changes take effect immediately. No coding needed.

## Files and what they control

| File | What it drives | Replace with |
|---|---|---|
| `machines.csv` | All 8 machines — OEE, health, vibration, temperature, degradation speed | PI Historian tags + MES |
| `utilities.csv` | Compressor, chiller, hydraulic unit, coolant pump | PI Historian + IoT Hub |
| `yield_params.csv` | Reactor temperature, pressure, mixing speed, feed rate, etc. | PI Historian process tags |
| `production_orders.csv` | SAP production order list — batch, product, yield vs plan | SAP PP OData |
| `lims_results.csv` | Lab test results per batch | LIMS REST API |
| `work_orders.csv` | Maintenance work orders | SAP PM OData |
| `supply_risks.csv` | Supplier list with lead times, stock levels, risk scores | SAP MM + D&B API |
| `energy_assets.csv` | Each asset's rated power, whether it can be shifted off-peak | Smart meter API |
| `energy_hourly.csv` | 24-hour plant load profile baseline | Smart meter historical API |
| `safety_zones.csv` | Gas sensor baselines and alarm thresholds per zone | IoT Hub gas sensors |
| `safety_permits.csv` | Permit-to-work list | CMMS / PTW system |
| `quality_defects.csv` | Detected defects — line, type, severity, confidence | Vision AI service |
| `quality_lines.csv` | Per-line quality rate and defect count | Vision AI + MES |
| `twin_kpis.csv` | Actual vs digital twin predicted values | Azure Digital Twins API |

## How to swap a CSV for a real API

1. Open `apps/api/src/data/csv-loader.ts`
2. Find the function for that file (e.g. `loadMachines()`)
3. See the comment `// REPLACE WITH: <connector>`
4. Delete the `parseCSV(...)` call and replace with the connector call
5. Set the connector's env variable in `.env.local`
6. Restart — that module now reads live data; everything else stays on CSV

## Tips for editing CSVs

- Keep the header row exactly as-is — column names are used by the loader
- Numbers: no commas, no units, just the number (e.g. `84000` not `₹84,000`)
- Booleans: use `true` or `false` (lowercase)
- Empty values: just leave the cell blank
- Status fields: use exact values shown — `good`, `caution`, `warning`, `critical`
