import duckdb
import json
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
from typing import Optional

app = FastAPI(title="ManuAI Energy Dashboard", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")

con = duckdb.connect()
DATA_DIR = Path("data")

# ── Helper: safe CSV path ─────────────────────────────────────
def csv(name: str) -> str:
    return str(DATA_DIR / name).replace("\\", "/")

# ══════════════════════════════════════════════════════════════
# ROOT – serve dashboard.html
# ══════════════════════════════════════════════════════════════
@app.get("/", response_class=HTMLResponse)
def serve_dashboard():
    html = (Path("static") / "dashboard.html").read_text(encoding="utf-8")
    return HTMLResponse(content=html)

# ══════════════════════════════════════════════════════════════
# GENERIC ENDPOINTS
# ══════════════════════════════════════════════════════════════
@app.get("/api/files")
def list_files():
    files = [f.name for f in DATA_DIR.glob("*.csv")]
    return {"files": sorted(files), "count": len(files)}

@app.get("/api/data/{filename}")
def get_data(filename: str, limit: int = Query(500, le=5000)):
    filepath = DATA_DIR / filename
    if not filepath.exists():
        return JSONResponse(status_code=404, content={"error": f"{filename} not found"})
    result = con.execute(
        f"SELECT * FROM read_csv_auto('{csv(filename)}') LIMIT {limit}"
    ).fetchdf()
    return result.to_dict(orient="records")

@app.post("/api/query")
def run_query(payload: dict):
    sql = payload.get("sql", "").strip()
    if not sql:
        return JSONResponse(status_code=400, content={"error": "Empty query"})
    try:
        result = con.execute(sql).fetchdf()
        return result.to_dict(orient="records")
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": str(e)})

@app.get("/api/summary/{filename}")
def get_summary(filename: str):
    filepath = DATA_DIR / filename
    if not filepath.exists():
        return JSONResponse(status_code=404, content={"error": f"{filename} not found"})
    result = con.execute(
        f"SUMMARIZE SELECT * FROM read_csv_auto('{csv(filename)}')"
    ).fetchdf()
    return result.to_dict(orient="records")

# ══════════════════════════════════════════════════════════════
# DASHBOARD TAB — energy_hourly + energy_assets + utilities
# ══════════════════════════════════════════════════════════════

@app.get("/api/dashboard/load-profile")
def load_profile(plant: Optional[str] = "all", hours: int = 24):
    """24h load chart data — energy_hourly.csv"""
    try:
        plant_filter = f"WHERE plant_id = '{plant}'" if plant != "all" else ""
        result = con.execute(f"""
            SELECT hour, SUM(load_kw) as load_kw, AVG(grid_price) as grid_price
            FROM read_csv_auto('{csv("energy_hourly.csv")}')
            {plant_filter}
            GROUP BY hour ORDER BY hour
            LIMIT {hours}
        """).fetchdf()
        return result.to_dict(orient="records")
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": str(e)})

@app.get("/api/dashboard/asset-profile")
def asset_profile(plant: Optional[str] = "all"):
    """Asset-level energy breakdown — energy_assets.csv"""
    try:
        plant_filter = f"WHERE plant_id = '{plant}'" if plant != "all" else ""
        result = con.execute(f"""
            SELECT asset_id, asset_name, asset_type, load_kw, anomaly_flag, plant_id
            FROM read_csv_auto('{csv("energy_assets.csv")}')
            {plant_filter}
            ORDER BY load_kw DESC
            LIMIT 20
        """).fetchdf()
        return result.to_dict(orient="records")
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": str(e)})

@app.get("/api/dashboard/kpis")
def dashboard_kpis(plant: Optional[str] = "all"):
    """Top KPI strip — energy_assets + utilities + twin_kpis"""
    try:
        plant_filter = f"WHERE plant_id = '{plant}'" if plant != "all" else ""
        energy = con.execute(f"""
            SELECT
                ROUND(SUM(load_kw), 1)          AS total_kw,
                ROUND(AVG(carbon_intensity), 3)  AS avg_carbon,
                ROUND(MAX(load_kw), 1)           AS peak_kw,
                COUNT(CASE WHEN anomaly_flag = true OR anomaly_flag = 'true' OR anomaly_flag = 1 THEN 1 END) AS anomaly_count
            FROM read_csv_auto('{csv("energy_assets.csv")}')
            {plant_filter}
        """).fetchdf()

        kpis = energy.to_dict(orient="records")[0] if not energy.empty else {}

        # Try to pull twin_kpis for oee/yield
        try:
            twin = con.execute(f"""
                SELECT ROUND(AVG(oee),1) as oee, ROUND(AVG(yield_pct),1) as yield_pct
                FROM read_csv_auto('{csv("twin_kpis.csv")}')
                {plant_filter}
            """).fetchdf()
            kpis.update(twin.to_dict(orient="records")[0] if not twin.empty else {})
        except:
            pass

        return kpis
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": str(e)})

@app.get("/api/dashboard/carbon-trend")
def carbon_trend(plant: Optional[str] = "all"):
    """Monthly carbon intensity trend — energy_hourly or utilities"""
    try:
        plant_filter = f"WHERE plant_id = '{plant}'" if plant != "all" else ""
        result = con.execute(f"""
            SELECT month, ROUND(AVG(carbon_intensity), 3) as carbon_intensity
            FROM read_csv_auto('{csv("utilities.csv")}')
            {plant_filter}
            GROUP BY month ORDER BY month
        """).fetchdf()
        return result.to_dict(orient="records")
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": str(e)})

# ══════════════════════════════════════════════════════════════
# WASTE DETECTION TAB — energy_assets + machines + work_orders
# ══════════════════════════════════════════════════════════════

@app.get("/api/waste/anomalies")
def waste_anomalies(plant: Optional[str] = "all"):
    """Anomaly cards with root cause — energy_assets + machines"""
    try:
        plant_filter = f"AND ea.plant_id = '{plant}'" if plant != "all" else ""
        result = con.execute(f"""
            SELECT
                ea.asset_id, ea.asset_name, ea.asset_type,
                ea.energy_wasted_kwh, ea.cost_today,
                ea.anomaly_type, ea.root_cause,
                ea.dispatch_action, ea.plant_id,
                m.machine_group
            FROM read_csv_auto('{csv("energy_assets.csv")}') ea
            LEFT JOIN read_csv_auto('{csv("machines.csv")}') m
                ON ea.asset_id = m.asset_id
            WHERE (ea.anomaly_flag = true OR ea.anomaly_flag = 'true' OR ea.anomaly_flag = 1)
            {plant_filter}
            ORDER BY ea.cost_today DESC
        """).fetchdf()
        return result.to_dict(orient="records")
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": str(e)})

@app.get("/api/waste/summary")
def waste_summary(plant: Optional[str] = "all"):
    """Total waste cost & kWh — energy_assets"""
    try:
        plant_filter = f"WHERE plant_id = '{plant}'" if plant != "all" else ""
        result = con.execute(f"""
            SELECT
                COUNT(*) as total_anomalies,
                ROUND(SUM(energy_wasted_kwh), 1) as total_kwh_wasted,
                ROUND(SUM(cost_today), 0) as total_cost_today
            FROM read_csv_auto('{csv("energy_assets.csv")}')
            {plant_filter}
        """).fetchdf()
        return result.to_dict(orient="records")[0]
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": str(e)})

# ══════════════════════════════════════════════════════════════
# LOAD DISPATCH TAB — energy_hourly + work_orders
# ══════════════════════════════════════════════════════════════

@app.get("/api/dispatch/grid-prices")
def grid_prices(plant: Optional[str] = "all"):
    """24h grid price signal — energy_hourly.csv"""
    try:
        plant_filter = f"WHERE plant_id = '{plant}'" if plant != "all" else ""
        result = con.execute(f"""
            SELECT hour, ROUND(AVG(grid_price), 2) as grid_price
            FROM read_csv_auto('{csv("energy_hourly.csv")}')
            {plant_filter}
            GROUP BY hour ORDER BY hour
        """).fetchdf()
        return result.to_dict(orient="records")
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": str(e)})

@app.get("/api/dispatch/queue")
def dispatch_queue(plant: Optional[str] = "all"):
    """SCADA write-back queue — energy_assets"""
    try:
        plant_filter = f"WHERE plant_id = '{plant}'" if plant != "all" else ""
        result = con.execute(f"""
            SELECT asset_id, asset_name, dispatch_action,
                   current_setpoint, target_setpoint,
                   dispatch_status, savings_kwh, cost_saving
            FROM read_csv_auto('{csv("energy_assets.csv")}')
            {plant_filter}
            ORDER BY dispatch_status, cost_saving DESC
        """).fetchdf()
        return result.to_dict(orient="records")
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": str(e)})

@app.get("/api/dispatch/recommendations")
def dispatch_recommendations(plant: Optional[str] = "all"):
    """AI dispatch recommendations — energy_assets + energy_hourly"""
    try:
        plant_filter = f"WHERE plant_id = '{plant}'" if plant != "all" else ""
        result = con.execute(f"""
            SELECT asset_id, asset_name, asset_type,
                   dispatch_action, savings_kwh, cost_saving,
                   ai_confidence, dispatch_window, dispatch_status
            FROM read_csv_auto('{csv("energy_assets.csv")}')
            WHERE dispatch_action IS NOT NULL
            {("AND plant_id = '" + plant + "'") if plant != "all" else ""}
            ORDER BY cost_saving DESC
        """).fetchdf()
        return result.to_dict(orient="records")
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": str(e)})

# ══════════════════════════════════════════════════════════════
# ISO 50001 TAB — twin_kpis + yield_params + energy_assets
# ══════════════════════════════════════════════════════════════

@app.get("/api/iso/status")
def iso_status(plant: Optional[str] = "all"):
    """ISO 50001 KPI status — twin_kpis"""
    try:
        plant_filter = f"WHERE plant_id = '{plant}'" if plant != "all" else ""
        result = con.execute(f"""
            SELECT
                ROUND(AVG(enpi_kwh_unit), 2)      AS enpi,
                ROUND(AVG(carbon_intensity), 3)    AS carbon_intensity,
                ROUND(AVG(energy_cost_pct), 1)     AS energy_cost_pct,
                MAX(report_status)                 AS report_status
            FROM read_csv_auto('{csv("twin_kpis.csv")}')
            {plant_filter}
        """).fetchdf()
        return result.to_dict(orient="records")[0]
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": str(e)})

@app.get("/api/iso/benchmarking")
def iso_benchmarking():
    """Cross-plant ENPi benchmarking — twin_kpis"""
    try:
        result = con.execute(f"""
            SELECT plant_id, plant_name,
                   ROUND(AVG(enpi_kwh_unit), 2) AS enpi,
                   ROUND(AVG(carbon_intensity), 3) AS carbon_intensity
            FROM read_csv_auto('{csv("twin_kpis.csv")}')
            GROUP BY plant_id, plant_name
            ORDER BY enpi ASC
        """).fetchdf()
        return result.to_dict(orient="records")
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": str(e)})

@app.get("/api/iso/compliance")
def iso_compliance(plant: Optional[str] = "all"):
    """ISO compliance progress — twin_kpis + yield_params"""
    try:
        plant_filter = f"WHERE plant_id = '{plant}'" if plant != "all" else ""
        result = con.execute(f"""
            SELECT metric_name, current_value, target_value,
                   ROUND(current_value / target_value * 100, 1) AS pct_complete
            FROM read_csv_auto('{csv("yield_params.csv")}')
            {plant_filter}
            ORDER BY pct_complete DESC
        """).fetchdf()
        return result.to_dict(orient="records")
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": str(e)})

# ══════════════════════════════════════════════════════════════
# RIGHT PANEL — safety + quality + lims + supply
# ══════════════════════════════════════════════════════════════

@app.get("/api/safety/alerts")
def safety_alerts(plant: Optional[str] = "all"):
    """Safety alerts — safety_permits + safety_zones"""
    try:
        plant_filter = f"WHERE sz.plant_id = '{plant}'" if plant != "all" else ""
        result = con.execute(f"""
            SELECT
                sp.permit_id, sp.alert_type, sp.severity,
                sp.description, sp.zone_id, sp.timestamp,
                sz.zone_name, sz.plant_id
            FROM read_csv_auto('{csv("safety_permits.csv")}') sp
            LEFT JOIN read_csv_auto('{csv("safety_zones.csv")}') sz
                ON sp.zone_id = sz.zone_id
            WHERE sp.severity IN ('CRITICAL','HIGH')
            {("AND sz.plant_id = '" + plant + "'") if plant != "all" else ""}
            ORDER BY sp.severity DESC, sp.timestamp DESC
            LIMIT 10
        """).fetchdf()
        return result.to_dict(orient="records")
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": str(e)})

@app.get("/api/quality/defects")
def quality_defects(plant: Optional[str] = "all"):
    """Quality defects — quality_defects + quality_lines"""
    try:
        plant_filter = f"WHERE ql.plant_id = '{plant}'" if plant != "all" else ""
        result = con.execute(f"""
            SELECT
                qd.defect_id, qd.defect_type, qd.severity,
                qd.line_id, qd.timestamp, qd.cost_impact,
                ql.line_name, ql.plant_id
            FROM read_csv_auto('{csv("quality_defects.csv")}') qd
            LEFT JOIN read_csv_auto('{csv("quality_lines.csv")}') ql
                ON qd.line_id = ql.line_id
            {plant_filter}
            ORDER BY qd.cost_impact DESC
            LIMIT 20
        """).fetchdf()
        return result.to_dict(orient="records")
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": str(e)})

@app.get("/api/lims/results")
def lims_results(plant: Optional[str] = "all", limit: int = 50):
    """LIMS test results — lims_results.csv"""
    try:
        plant_filter = f"WHERE plant_id = '{plant}'" if plant != "all" else ""
        result = con.execute(f"""
            SELECT * FROM read_csv_auto('{csv("lims_results.csv")}')
            {plant_filter}
            ORDER BY timestamp DESC
            LIMIT {limit}
        """).fetchdf()
        return result.to_dict(orient="records")
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": str(e)})

@app.get("/api/supply/risks")
def supply_risks():
    """Supply chain risks — supply_risks.csv"""
    try:
        result = con.execute(f"""
            SELECT * FROM read_csv_auto('{csv("supply_risks.csv")}')
            ORDER BY risk_level DESC
            LIMIT 20
        """).fetchdf()
        return result.to_dict(orient="records")
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": str(e)})

@app.get("/api/production/orders")
def production_orders(plant: Optional[str] = "all", limit: int = 100):
    """Production orders — production_orders.csv"""
    try:
        plant_filter = f"WHERE plant_id = '{plant}'" if plant != "all" else ""
        result = con.execute(f"""
            SELECT * FROM read_csv_auto('{csv("production_orders.csv")}')
            {plant_filter}
            ORDER BY order_date DESC
            LIMIT {limit}
        """).fetchdf()
        return result.to_dict(orient="records")
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": str(e)})

@app.get("/api/work-orders")
def work_orders(plant: Optional[str] = "all", limit: int = 50):
    """Work orders — work_orders.csv"""
    try:
        plant_filter = f"WHERE plant_id = '{plant}'" if plant != "all" else ""
        result = con.execute(f"""
            SELECT * FROM read_csv_auto('{csv("work_orders.csv")}')
            {plant_filter}
            ORDER BY created_date DESC
            LIMIT {limit}
        """).fetchdf()
        return result.to_dict(orient="records")
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": str(e)})