# Settings manifest authoring guide

The settings page is a thin shell over a single source of truth: `SETTINGS_MANIFEST` in `manifest.ts`. To add, change, or remove a setting, edit the manifest. Section components render whatever the manifest tells them to.

## Adding a setting

Append a new `SettingsEntry` object to `SETTINGS_MANIFEST`. Required fields:

```ts
{
  id: 'risk.max_daily_loss',           // unique; convention is `<backendSection>.<backendPath>`
  label: 'Max daily loss',             // shown in the UI
  default: 200,                        // type-correct fallback when the backend hasn't persisted a value yet
  type: 'number',                      // 'number' | 'text' | 'toggle' | 'select' | 'secret'
  area: 'trading',                     // one of SETTINGS_AREAS ids
  subsection: 'trading-risk-loss-limits', // must exist in SETTINGS_SUBSECTIONS for the chosen area
  backendSection: 'risk',              // routes the PUT to /api/settings/risk
  backendPath: 'max_daily_loss',       // dot-path inside the PUT payload
  description: 'Stop trading once realized losses for the day reach this dollar amount.',
  usedBy: ['axiom.api_core', 'axiom.risk'], // backend modules that consume the value
  unit: '$',                           // optional: unit suffix
  options: [...],                      // required for type: 'select'
  deepLinkTo: '/risk-monitor',         // optional: a related dashboard route
  advanced: true,                      // optional: collapse under "Advanced" by default
}
```

## How rendering works

- `area` + `subsection` decide which `Settings*.svelte` section the row appears in. Add a new subsection to `SETTINGS_SUBSECTIONS` first if you need a new container.
- `area: 'home'` is reserved — Home is hand-built and does not iterate the manifest.
- `area: 'danger'` is rendered with extra confirm-typing guards.

## How saving works

- Each entry edit calls `markField(id, value)` which writes into the `pendingValues` store and updates `dirtyFields`.
- The sticky `SettingsSaveBar` groups dirty ids by `backendSection`, then issues parallel `PUT /api/settings/{backendSection}` calls, each carrying a payload built from the entries' `backendPath`s.
- The backend diffs old vs. new and appends to a 50-entry rolling `settings.audit_log`. The Home "Recently changed" panel reads from there.

## Backend wiring

- `backendSection` must match a known section the backend's `put_settings_section` handler recognizes (currently: `risk`, `exchange`, `hyperliquid`, `trading-mode`, `notifications`, `agents`, `system`, `lab-pipeline`, etc.).
- `backendPath` is the key inside the payload. Use a dot-path for nested keys (e.g., `cooldowns.daily_loss_minutes`).
- Do not rename a `backendPath` that has live data — the backend is the source of truth for already-persisted values.

## Invariants

`src/tests/settingsManifest.test.ts` enforces:

- non-empty `description`
- non-undefined `default`
- `area` exists in `SETTINGS_AREAS`
- `subsection` exists in `SETTINGS_SUBSECTIONS` and matches the entry's `area`
- non-empty `usedBy` (forces you to grep the backend before adding the entry)
- unique `id`
- `home` is first in `SETTINGS_AREAS`, `danger` is last and `danger: true`

If any invariant fails, the manifest test breaks the build — fix the entry, don't relax the test.

## Removing a setting

1. Delete the entry from `SETTINGS_MANIFEST`.
2. Remove the field from `_default_settings_payload()` in `axiom/api_core.py`.
3. Remove backend reads (`settings.<key>` / `settings.get('<key>')`) and any `setattr(settings, '<key>', ...)` writes.
4. If the field shipped to users, add a CHANGELOG note: `Removed setting <id> (<date>, <reason>)`.

A field with no UI but live backend reads is also valid — just leave it out of the manifest. The 13 keys listed in `docs/plans/2026-04-17-settings-audit-findings.md` section 4 are examples.
