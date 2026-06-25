import { describe, it, expect, afterEach, vi } from 'vitest';
import { mount, unmount } from 'svelte';

vi.mock('$lib/api', () => ({
	getAxiomLogs: vi.fn().mockResolvedValue([]),
	getAxiomAgents: vi.fn().mockResolvedValue([
		{
			id: 'brain',
			name: 'Brain',
			model: 'claude-opus-4.7',
			model_id: 'claude-opus-4.7',
			status: 'running',
			enabled: true,
		},
		{
			id: 'quant-researcher',
			name: 'Quant Researcher',
			model: 'claude-sonnet-4.5',
			model_id: 'claude-sonnet-4.5',
			status: 'idle',
			enabled: true,
		},
	]),
	getAxiomAgentTasks: vi.fn().mockResolvedValue([
		{ id: 1, agent_id: 'brain', status: 'running', title: 'Thinking' },
	]),
}));

vi.mock('$lib/utils/realtime', () => ({
	createRealtimeRefresh: (fn: () => Promise<void> | void) => ({
		start: () => {
			// Fire the refresh synchronously so mount-time data arrives in time for the test.
			void fn();
		},
		stop: () => {},
	}),
}));

import AgentHeartbeat from '../lib/components/dashboard/AgentHeartbeat.svelte';

let target: HTMLElement;
let instance: any;

afterEach(() => {
	if (instance) unmount(instance);
	target?.remove();
});

async function flush(): Promise<void> {
	await Promise.resolve();
	await Promise.resolve();
	await new Promise((r) => setTimeout(r, 0));
	await Promise.resolve();
}

describe('AgentHeartbeat with roster', () => {
	it('renders the roster pulled from getAxiomAgents', async () => {
		target = document.createElement('div');
		document.body.appendChild(target);
		instance = mount(AgentHeartbeat, { target, props: {} });
		await flush();
		const roster = target.querySelector('[data-testid="agent-roster"]');
		expect(roster).toBeTruthy();
		expect(roster?.textContent).toContain('Brain');
		expect(roster?.textContent).toContain('Quant Researcher');
	});

	it('keeps the roster wrapper rendered even when no agents load', async () => {
		const api = await import('$lib/api');
		(api.getAxiomAgents as ReturnType<typeof vi.fn>).mockResolvedValueOnce([]);
		target = document.createElement('div');
		document.body.appendChild(target);
		instance = mount(AgentHeartbeat, { target, props: {} });
		await flush();
		expect(target.querySelector('[data-testid="agent-roster"]')).toBeTruthy();
	});
});
