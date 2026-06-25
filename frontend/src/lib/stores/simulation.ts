import { derived } from 'svelte/store';
import { axiomDashboard } from './axiom';

export const simulationActive = derived(axiomDashboard, ($d) =>
	Boolean($d?.simulation_active)
);

export const simulationPhase = derived(axiomDashboard, ($d) =>
	$d?.simulation_phase || 'idle'
);

export const simulationTime = derived(axiomDashboard, ($d) =>
	$d?.simulation_time || ''
);

export const simulationProgress = derived(axiomDashboard, ($d) =>
	$d?.simulation_progress || 0
);

export const simulationPrices = derived(axiomDashboard, ($d) =>
	$d?.simulation_prices || {}
);
