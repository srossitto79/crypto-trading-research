import { writable } from 'svelte/store';
import type {
	AxiomDashboardResponse,
	AxiomRiskStatus,
	AxiomRegimeSnapshot,
	AxiomScannerState,
	AxiomSentimentSnapshot,
	AxiomTrade,
} from '$lib/api';

// Passive writable stores — populated by heartbeat.ts via the unified
// /api/system/heartbeat endpoint.  Individual pages that need a direct
// refresh can still call the original API functions and .set() here.

export const axiomDashboard = writable<AxiomDashboardResponse | null>(null);
export const axiomRisk = writable<AxiomRiskStatus | null>(null);
export const axiomSentiment = writable<AxiomSentimentSnapshot | null>(null);
export const axiomRegime = writable<AxiomRegimeSnapshot | null>(null);
export const axiomOpenTrades = writable<AxiomTrade[]>([]);
export const axiomScannerState = writable<AxiomScannerState | null>(null);
