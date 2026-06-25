import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { mount, unmount } from 'svelte';

const apiMocks = vi.hoisted(() => ({
	getAxiomAgents: vi.fn(),
	getAxiomAgentDocuments: vi.fn(),
	updateAxiomAgent: vi.fn(),
	updateAxiomAgentDocument: vi.fn(),
	testAxiomAgentDiscord: vi.fn(),
	getAxiomSchedulerJobs: vi.fn(),
	updateAxiomSchedulerJob: vi.fn(),
}));

vi.mock('$lib/api', () => apiMocks);

import SettingsAgents from '../lib/components/settings/sections/SettingsAgents.svelte';
import { originalValues, clearDirty } from '../lib/settings/dirty';

let target: HTMLElement;
let instance: any;

afterEach(() => {
	if (instance) unmount(instance);
	target?.remove();
});

beforeEach(() => {
	clearDirty();
	originalValues.set({});
	apiMocks.getAxiomAgents.mockReset();
	apiMocks.getAxiomAgentDocuments.mockReset();
	apiMocks.updateAxiomAgent.mockReset();
	apiMocks.updateAxiomAgentDocument.mockReset();
	apiMocks.testAxiomAgentDiscord.mockReset();
	apiMocks.getAxiomSchedulerJobs.mockReset();
	apiMocks.updateAxiomSchedulerJob.mockReset();

	apiMocks.getAxiomAgents.mockResolvedValue([
		{
			id: 'alpha',
			name: 'Alpha',
			role: 'trader',
			model: 'openai',
			model_id: 'gpt-4o',
			schedule_type: 'cron',
			schedule_expr: '0 9 * * *',
			enabled: true,
			instructions: 'Trade carefully.',
			has_discord_token: true,
		},
		{
			id: 'beta',
			name: 'Beta',
			role: 'researcher',
			model: 'anthropic',
			model_id: 'claude-opus-4-7',
			schedule_type: 'interval',
			schedule_expr: '15m',
			enabled: false,
			instructions: '',
			has_discord_token: false,
		},
	]);
	apiMocks.getAxiomAgentDocuments.mockResolvedValue({
		soul: 'SOUL for alpha',
		agents: 'AGENTS for alpha',
		role: 'ROLE for alpha',
	});
	apiMocks.getAxiomSchedulerJobs.mockResolvedValue([
		{
			id: 'job-1',
			name: 'Daily regime check',
			schedule_type: 'cron',
			schedule_expr: '0 8 * * *',
			enabled: true,
		},
		{
			id: 'job-2',
			name: 'Hourly sweep',
			schedule_type: 'interval',
			schedule_expr: '60m',
			enabled: false,
		},
	]);
});

async function flush(): Promise<void> {
	await Promise.resolve();
	await Promise.resolve();
	await new Promise((r) => setTimeout(r, 0));
	await Promise.resolve();
	await new Promise((r) => setTimeout(r, 0));
	await Promise.resolve();
}

describe('SettingsAgents section', () => {
	it('renders the manifest-driven provider and model-policy subsections', async () => {
		target = document.createElement('div');
		document.body.appendChild(target);
		instance = mount(SettingsAgents, {
			target,
			props: { settings: {} },
		});
		await flush();

		const text = target.textContent || '';
		expect(text).toContain('AI providers');
		expect(text).toContain('Agent personas');
		expect(text).toContain('Scheduler');
	});

	it('loads the agent roster and selects the first agent by default', async () => {
		target = document.createElement('div');
		document.body.appendChild(target);
		instance = mount(SettingsAgents, {
			target,
			props: { settings: {} },
		});
		await flush();

		expect(apiMocks.getAxiomAgents).toHaveBeenCalled();
		expect(apiMocks.getAxiomAgentDocuments).toHaveBeenCalledWith('alpha');

		const text = target.textContent || '';
		expect(text).toContain('Alpha');
		expect(text).toContain('Beta');
	});

	it('renders SOUL.md, AGENTS.md, and ROLE.md editors for the selected agent', async () => {
		target = document.createElement('div');
		document.body.appendChild(target);
		instance = mount(SettingsAgents, {
			target,
			props: { settings: {} },
		});
		await flush();

		const soul = target.querySelector<HTMLTextAreaElement>(
			'textarea[aria-label="SOUL.md content"]',
		);
		const agents = target.querySelector<HTMLTextAreaElement>(
			'textarea[aria-label="AGENTS.md content"]',
		);
		const role = target.querySelector<HTMLTextAreaElement>(
			'textarea[aria-label="ROLE.md content"]',
		);
		expect(soul?.value).toBe('SOUL for alpha');
		expect(agents?.value).toBe('AGENTS for alpha');
		expect(role?.value).toBe('ROLE for alpha');
	});

	it('renders the scheduler jobs loaded from the backend', async () => {
		target = document.createElement('div');
		document.body.appendChild(target);
		instance = mount(SettingsAgents, {
			target,
			props: { settings: {} },
		});
		await flush();

		expect(apiMocks.getAxiomSchedulerJobs).toHaveBeenCalled();
		const text = target.textContent || '';
		expect(text).toContain('Daily regime check');
		expect(text).toContain('Hourly sweep');
	});
});
