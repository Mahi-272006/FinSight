# FinSight frontend

FinSight's frontend foundation is a React and Vite application using JavaScript.
It provides the shared editorial layout, navigation, design tokens, route shells,
and a live-data Overview backed by the M9 API.

## Run locally

1. Copy `.env.example` to `.env` and set `VITE_API_BASE_URL` if the backend is
   not available at `http://localhost:8000/api/v1`.
2. Install packages with `npm install`.
3. Start the frontend with `npm run dev`.

The backend must be running separately. The Overview uses live companies,
watchlist entries, saved reports, thesis records, and persisted anomaly signals.
An empty response is shown as empty, and unavailable data is reported without
substituting sample financial values. The other six pages are foundation shells;
their workflows are intentionally not connected in this stage.

## Checks

- `npm run build` creates the production bundle in `dist/`.
- `npm run lint` runs Oxlint.
- `npm test` runs the frontend unit and component tests once.

The frontend reads the backend URL from `VITE_API_BASE_URL` in one Axios client.
Vite exposes only `VITE_`-prefixed values to browser code; do not put credentials
or private API keys in frontend environment variables.
