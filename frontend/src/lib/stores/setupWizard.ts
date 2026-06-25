/** Singleton store for the first-launch setup wizard. Persists the last viewed step to localStorage for resume. */

import { writable } from 'svelte/store';

const LAST_STEP_KEY = 'axiom.wizard.last_step';
// Keep in sync with STEPS.length - 1 in SetupWizardModal.svelte.
const MAX_STEP_INDEX = 4;

function readLastStep(): number {
  if (typeof window === 'undefined') return 0;
  try {
    const raw = window.localStorage.getItem(LAST_STEP_KEY);
    if (raw === null) return 0;
    const n = Number.parseInt(raw, 10);
    if (!Number.isFinite(n) || n < 0 || n > MAX_STEP_INDEX) return 0;
    return n;
  } catch {
    return 0;
  }
}

export const wizardOpen = writable(false);
export const wizardStep = writable(readLastStep());

wizardStep.subscribe((step) => {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(LAST_STEP_KEY, String(step));
  } catch {
    // Storage unavailable (private mode, quota, Tauri sandbox) — resume is best-effort.
  }
});

export function clearWizardResume(): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.removeItem(LAST_STEP_KEY);
  } catch {
    // Ignore.
  }
}

export function openWizard(): void {
  wizardStep.set(readLastStep());
  wizardOpen.set(true);
}

export function closeWizard(): void {
  wizardOpen.set(false);
}
