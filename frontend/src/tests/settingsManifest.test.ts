import { describe, it, expect } from 'vitest';
import { SETTINGS_MANIFEST, SETTINGS_AREAS, SETTINGS_SUBSECTIONS } from '$lib/settings/manifest';

describe('settings manifest invariants', () => {
  it('every entry has a description', () => {
    for (const e of SETTINGS_MANIFEST) {
      expect(e.description.trim().length, `missing description on ${e.id}`).toBeGreaterThan(0);
    }
  });

  it('every entry has a non-undefined default', () => {
    for (const e of SETTINGS_MANIFEST) {
      expect(e.default, `missing default on ${e.id}`).not.toBeUndefined();
    }
  });

  it('every entry lists a valid area', () => {
    const areas = new Set(SETTINGS_AREAS.map((a) => a.id));
    for (const e of SETTINGS_MANIFEST) {
      expect(areas.has(e.area), `${e.id} -> area ${e.area}`).toBe(true);
    }
  });

  it('every entry lists an existing subsection in its area', () => {
    const pairs = new Set(SETTINGS_SUBSECTIONS.map((s) => `${s.area}/${s.id}`));
    for (const e of SETTINGS_MANIFEST) {
      expect(pairs.has(`${e.area}/${e.subsection}`), `${e.id}`).toBe(true);
    }
  });

  it('every entry has a non-empty usedBy', () => {
    for (const e of SETTINGS_MANIFEST) {
      expect(e.usedBy.length, `${e.id}`).toBeGreaterThan(0);
    }
  });

  it('ids are unique', () => {
    const seen = new Set<string>();
    for (const e of SETTINGS_MANIFEST) {
      expect(seen.has(e.id), `duplicate ${e.id}`).toBe(false);
      seen.add(e.id);
    }
  });

  it('SETTINGS_AREAS has Home first and Danger Zone last', () => {
    expect(SETTINGS_AREAS[0].id).toBe('home');
    expect(SETTINGS_AREAS[SETTINGS_AREAS.length - 1].id).toBe('danger');
  });

  it('Danger Zone is flagged danger: true', () => {
    const danger = SETTINGS_AREAS.find((a) => a.id === 'danger');
    expect(danger?.danger).toBe(true);
  });
});

describe('M-15 gate-floor knobs are surfaced in the manifest', () => {
  // 2026-06-09 audit: these floors were hardcoded in axiom/policy.py and the
  // wired settings were silently ignored. Defaults must match the previously
  // enforced values exactly (wiring, not relaxing — paper->live is the
  // live-money gate).
  const expected: Array<{ id: string; backendPath: string; default: unknown }> = [
    { id: 'pipeline.quick_screen.min_profit_factor', backendPath: 'quick_screen.min_profit_factor', default: 1.05 },
    { id: 'pipeline.gauntlet.min_oos_profit_factor', backendPath: 'gauntlet.min_oos_profit_factor', default: 1.05 },
    {
      id: 'pipeline.paper_trading.min_profit_factor_live',
      backendPath: 'paper_trading.min_profit_factor_live',
      default: 1.5,
    },
    {
      id: 'pipeline.paper_trading.pf_position_reduction_threshold',
      backendPath: 'paper_trading.pf_position_reduction_threshold',
      default: 2,
    },
    { id: 'pipeline.paper_trading.max_oos_is_ratio', backendPath: 'paper_trading.max_oos_is_ratio', default: 1.5 },
  ];

  for (const exp of expected) {
    it(`declares ${exp.id} with default ${exp.default}`, () => {
      const entry = SETTINGS_MANIFEST.find((e) => e.id === exp.id);
      expect(entry, `${exp.id} missing from manifest`).toBeDefined();
      expect(entry?.backendSection).toBe('pipeline');
      expect(entry?.backendPath).toBe(exp.backendPath);
      expect(entry?.default).toBe(exp.default);
      expect(entry?.type).toBe('number');
      expect(entry?.area).toBe('lab');
    });
  }
});

describe('2026-06-13 launch-hardening knobs are surfaced in the manifest', () => {
  // These threshold knobs were added to the backend pipeline config; per the
  // no-dead-knobs / no-hardcoded-settings rule they must be editable from the
  // Settings page (not just the KV).
  const expected: Array<{ id: string; backendPath: string; default: unknown }> = [
    { id: 'pipeline.quick_screen.fitness_min_trades', backendPath: 'quick_screen.fitness_min_trades', default: 20 },
    {
      id: 'pipeline.quick_screen.fitness_min_profit_factor',
      backendPath: 'quick_screen.fitness_min_profit_factor',
      default: 1.3,
    },
    { id: 'pipeline.paper_trading.min_paper_sharpe', backendPath: 'paper_trading.min_paper_sharpe', default: 1 },
    {
      id: 'pipeline.paper_trading.min_profit_factor_paper',
      backendPath: 'paper_trading.min_profit_factor_paper',
      default: 1.2,
    },
  ];

  for (const exp of expected) {
    it(`declares ${exp.id} with default ${exp.default}`, () => {
      const entry = SETTINGS_MANIFEST.find((e) => e.id === exp.id);
      expect(entry, `${exp.id} missing from manifest`).toBeDefined();
      expect(entry?.backendSection).toBe('pipeline');
      expect(entry?.backendPath).toBe(exp.backendPath);
      expect(entry?.default).toBe(exp.default);
      expect(entry?.type).toBe('number');
      expect(entry?.area).toBe('lab');
    });
  }
});
