from pathlib import Path
import json, re

ROOT = Path(__file__).resolve().parent
OUTPUTS = ROOT / "outputs"
MODEL = ROOT / "JAD Marketing Analysis Power BI.SemanticModel" / "definition" / "model.tmdl"
PAGES = ROOT / "JAD Marketing Analysis Power BI.Report" / "definition" / "pages"
REQUIRED = ["executive_kpis.csv", "monthly_performance.csv", "product_performance.csv",
"provider_performance.csv", "travel_priority_providers.csv", "provider_scorecard.csv",
"travel_provider_priority_analysis.csv", "data_quality_checks.csv"]

def main():
    missing = [n for n in REQUIRED if not (OUTPUTS / n).exists()]
    if missing:
        print("Missing CSV outputs:", ", ".join(missing))
        return 2

    text = MODEL.read_text(encoding="utf-8")
    for name in REQUIRED:
        path = str((OUTPUTS / name).resolve()).replace("\\", "\\\\")
        pattern = r'File\.Contents\("[^"]*' + re.escape(name) + r'"\)'
        text, count = re.subn(pattern, lambda _: 'File.Contents("' + path + '")', text)
        if count == 0:
            print("Warning: no source path updated for", name)

    text = text.replace(
        'executive_kpis[metric] = "Submitted Requests"',
        'executive_kpis[metric] = "Submitted Quote Requests"'
    )
    MODEL.write_text(text, encoding="utf-8")

    fixed = 0
    for vf in PAGES.rglob("visual.json"):
        data = json.loads(vf.read_text(encoding="utf-8"))
        vis = data.get("visual", {})
        if vis.get("visualType") == "stackedColumnChart":
            vis["visualType"] = "columnChart"
        if vis.get("visualType") not in ("card", "tableEx"):
            for state in vis.get("query", {}).get("queryState", {}).values():
                for projection in state.get("projections", []):
                    projection["active"] = True
        vf.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        fixed += 1

    print("Updated source paths and normalized", fixed, "visual definitions.")
    print("Open the PBIP file and select Home > Refresh.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
