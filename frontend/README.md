# Axiom Frontend

This is the SvelteKit operator UI for Axiom.

## Stack

- SvelteKit 2
- Svelte 5
- Tailwind CSS
- Vite
- Vitest
- Playwright

## Development

```bash
npm install
npm run dev
```

Default local URL: `http://127.0.0.1:5173`

The frontend expects the backend at `http://127.0.0.1:8003` by default. During local development, Vite proxies `/api` and `/health` to the backend.

## Commands

```bash
npm run dev
npm run build
npm run preview
npm test
npm run check
npm run test:e2e
```

## Route Surface

- `/`
- `/agents`
- `/ai-dropzone`
- `/approval`
- `/data`
- `/lab`
- `/lab/strategy/[id]`
- `/memory`
- `/risk`
- `/runs`
- `/settings`
- `/tasks`
- `/trades`

The dashboard also supports `/?view=quant_factory` and `/?view=beta`.

## Conventions

- Use typed API wrappers from `src/lib/api/`
- Use shared stores from `src/lib/stores/`
- Keep reusable UI in `src/lib/components/`
- Prefer route-local components only when the UI is truly route-specific
- Do not add raw `fetch()` calls directly inside components when a typed API module belongs there

For the full repo onboarding flow, go back to the root [README.md](../README.md).
