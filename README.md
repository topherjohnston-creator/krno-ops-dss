# KRNO Ops DSS

KRNO ground operations decision support dashboard.

## Methodology

Preserve the existing KRNO dashboard UI and change the backend data methodology.

- 8 hazards
- 4 top row / 4 bottom row
- Hazards sorted highest to lowest risk from left to right
- 24-hour max/min NBM products determine peak severity
- Hourly NBM products determine timing
- Timeline displays 3-hour operational risk blocks
- Python backend generates JSON
- Frontend fetches JSON and renders the dashboard

## Hazards

- Wind
- Snow
- Rain/Flooding
- Freezing Rain
- Flash Freeze
- Temperature
- Lightning
- Visibility

## Local Test

```bash
python scripts/build_mock_outputs.py
```

Then open:

```text
docs/index.html
```

## GitHub Pages

Configure GitHub Pages to serve from the `main` branch `/docs` folder.

## Next Build Phase

Replace mock JSON with real Herbie NBM Core extraction in `scripts/extract_nbm.py`.
