import nbformat

with open('jupyter/data_analysis.ipynb', 'r', encoding='utf-8') as f:
    nb = nbformat.read(f, as_version=4)

for cell in nb.cells:
    if cell.cell_type == 'code':
        if 'def parse_json(x):' in cell.source:
            new_source = cell.source.replace(
"""                def parse_json(x):
                    if not x or pd.isna(x): return {}
                    try:
                        return json.loads(x) if isinstance(x, str) else x
                    except (json.JSONDecodeError, TypeError):
                        return {}""",
"""                def parse_json(x):
                    if not x or pd.isna(x): return {}
                    try:
                        res = json.loads(x) if isinstance(x, str) else x
                        if isinstance(res, list):
                            return {f"item_{i}": v for i, v in enumerate(res)}
                        if not isinstance(res, dict):
                            return {"value": res}
                        return res
                    except (json.JSONDecodeError, TypeError):
                        return {}"""
            )
            cell.source = new_source

with open('jupyter/data_analysis.ipynb', 'w', encoding='utf-8') as f:
    nbformat.write(nb, f)

