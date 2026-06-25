import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mount, tick, unmount } from 'svelte';

import LabPage from '../routes/lab/+page.svelte';

const apiMocks = vi.hoisted(() => ({
	getAxiomStrategiesQuery: vi.fn(),
	getNowWorking: vi.fn(),
	getGraveyard: vi.fn(),
	transitionStage: vi.fn(),
	reviveFromGraveyard: vi.fn(),
	deleteStrategy: vi.fn(),
	batchDeleteStrategies: vi.fn(),
}));

const healthMocks = vi.hoisted(() => ({
	getHealthStatus: vi.fn(),
}));

const realtimeController = vi.hoisted(() => ({
	start: vi.fn(),
	stop: vi.fn(),
}));

vi.mock('$app/navigation', () => ({
	goto: vi.fn(),
}));

vi.mock('$lib/api', () => apiMocks);
vi.mock('$lib/api/axiom', () => healthMocks);
vi.mock('$lib/utils/realtime', () => ({
	createRealtimeRefresh: vi.fn(() => realtimeController),
}));
vi.mock('$lib/components/ui/StrategyLink.svelte', async () => ({
	default: (await import('./fixtures/Stub.svelte')).default,
}));

type MountedComponent = ReturnType<typeof mount>;

async function flush(): Promise<void> {
	await Promise.resolve();
	await tick();
	await Promise.resolve();
	await tick();
}

describe('/lab activity feed', () => {
	let app: MountedComponent | null = null;
	let target: HTMLDivElement;

	beforeEach(() => {
		target = document.createElement('div');
		document.body.appendChild(target);
		apiMocks.getAxiomStrategiesQuery.mockResolvedValue([]);
		apiMocks.getGraveyard.mockResolvedValue({ archived: [] });
		apiMocks.getNowWorking.mockResolvedValue([
			{
				strategy_id: 'S12345',
				name: 'Testing cycle completed',
				stage: 'quick_screen',
				since: '2026-03-21T18:00:00Z',
				current_task: {
					type: 'axiom-testing-cycle',
					status: 'running',
					started_at: '2026-03-21T18:00:00Z',
					stalled: false,
				},
			},
		]);
		healthMocks.getHealthStatus.mockResolvedValue({
			components: [
				{
					name: 'scheduler',
					state: 'green',
					last_seen: '2026-04-23T17:00:00Z',
					message: 'ok',
					component_type: 'service',
				},
			],
			data_checks: [
				{
					name: 'candle_freshness',
					passed: false,
					severity: 'warn',
					detail: 'No active bots',
				},
			],
			overall: 'amber',
			checked_at: '2026-04-23T17:00:00Z',
			monitor_running: true,
		});
		realtimeController.start.mockClear();
		realtimeController.stop.mockClear();
	});

	afterEach(() => {
		if (app) {
			unmount(app);
			app = null;
		}
		target.remove();
		vi.clearAllMocks();
	});

	it('renders the now-working feed instead of the dormant regime worker feed', async () => {
		app = mount(LabPage, {
			target,
		});

		await flush();

		expect(apiMocks.getNowWorking).toHaveBeenCalledTimes(1);
		expect(target.textContent).toContain('Testing cycle completed');
		expect(target.textContent).not.toContain('Failed to load active work');
	});

	it('renders filters and operational status as compact manager toolbar controls', async () => {
		app = mount(LabPage, {
			target,
		});

		await flush();

		const search = target.querySelector('input[placeholder="Search container, symbol, timeframe, id…"]');
		const healthChip = target.querySelector('[data-testid="forge-health-chip"]');
		const nowWorkingChip = target.querySelector('[data-testid="forge-now-working-chip"]');
		const table = target.querySelector('table');
		const largeNowWorkingHeading = Array.from(target.querySelectorAll('h2')).find(
			(heading) => heading.textContent?.trim() === 'Now Working',
		);

		expect(search).not.toBeNull();
		expect(healthChip?.textContent).toContain('System Health');
		expect(healthChip?.textContent).toContain('Degraded');
		expect(healthChip?.textContent).toContain('1 issue');
		expect(nowWorkingChip?.textContent).toContain('Now Working');
		expect(nowWorkingChip?.textContent).toContain('1 active');
		expect(target.textContent).toContain('Testing cycle completed');
		expect(largeNowWorkingHeading).toBeUndefined();
		expect(table).not.toBeNull();
		expect(Boolean(search!.compareDocumentPosition(table!) & Node.DOCUMENT_POSITION_FOLLOWING)).toBe(
			true,
		);
	});
});
