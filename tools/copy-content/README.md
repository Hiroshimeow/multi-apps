# Copy-Content

Bundled Windows utility shipped with `multi-run-apps`.

Run directly from this directory:

```powershell
uv run copy-content.py
```

The script uses PEP 723 inline dependency metadata, so `uv` provisions the required Python dependencies automatically. No Conda environment or command arguments are required.

Runtime state is intentionally local and ignored by Git:

- `copy-content-config.json` — saved per-machine path/filter preferences.
- `copy-content.txt` — latest generated output.
- `logs/` and `__pycache__/` — runtime artifacts.

The bundled `setting.yaml.sample` points to this directory with a relative path, so a fresh clone of `multi-run-apps` can launch Copy-Content without editing its command or arguments.
