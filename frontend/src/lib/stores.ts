/**
 * Svelte stores for application state.
 *
 * NOTE: Legacy polling helpers (startJobPolling, stopJobPolling) were removed.
 * Use the shared `createPoller` utility from `$lib/utils/polling` instead.
 */

import { browser } from '$app/environment';
import { writable, type Writable } from 'svelte/store';
import type { Strategy, Dataset, Job } from './api';

export function createPersistedStore<T>(key: string, initialValue: T): Writable<T> {
	let startingValue = initialValue;
	if (browser) {
		try {
			const raw = localStorage.getItem(key);
			if (raw !== null) {
				startingValue = JSON.parse(raw) as T;
			}
		} catch {
			startingValue = initialValue;
		}
	}

	const store = writable<T>(startingValue);
	if (browser) {
		store.subscribe((value) => {
			try {
				localStorage.setItem(key, JSON.stringify(value));
			} catch {
				// Ignore storage quota and serialization errors.
			}
		});
	}
	return store;
}

// Strategies
export const strategies = writable<Strategy[]>([]);

// Datasets
export const datasets = writable<Dataset[]>([]);

// Selected dataset for workflow (persists across pages)
export const selectedDataset = createPersistedStore<{
	symbol: string;
	timeframe: string;
	source: string;
	strategy?: string | null;
} | null>('axiom.selectedDataset', null);

// Workflow state - tracks progress through Data -> Strategy -> Backtest -> Results
export const workflowState = writable<{
	dataReady: boolean;
	strategySelected: string | null;
	lastBacktestId: string | null;
}>({
	dataReady: false,
	strategySelected: null,
	lastBacktestId: null
});

// Jobs
export const jobs = writable<Job[]>([]);

// Workspace context - persists selections across page navigation
export const workspaceContext = createPersistedStore<{
	strategy: string | null;
	symbol: string | null;
	timeframe: string | null;
	definitionJson: string | null;
}>('axiom.workspaceContext', {
	strategy: null,
	symbol: null,
	timeframe: null,
	definitionJson: null,
});

// UI State
export const backendConnected = writable(false);
