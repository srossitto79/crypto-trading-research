import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { mount, unmount } from 'svelte';

vi.mock('$lib/api/axiom', () => ({
	getFactoryResetCategories: vi.fn(),
	performFactoryReset: vi.fn(),
}));

import SettingsDangerZone from '../lib/components/settings/sections/SettingsDangerZone.svelte';
import { getFactoryResetCategories, performFactoryReset } from '$lib/api/axiom';

const mockGet = getFactoryResetCategories as unknown as ReturnType<typeof vi.fn>;
const mockReset = performFactoryReset as unknown as ReturnType<typeof vi.fn>;

let target: HTMLElement;
let instance: any;

async function flush(): Promise<void> {
	await Promise.resolve();
	await Promise.resolve();
	await new Promise((r) => setTimeout(r, 0));
	await Promise.resolve();
}

beforeEach(() => {
	mockGet.mockReset();
	mockReset.mockReset();
	mockGet.mockResolvedValue({
		categories: [
			{ id: 'brain', label: 'Brain memory', description: 'Curated lessons', default_keep: true },
			{ id: 'market_data', label: 'Market data', description: 'OHLCV cache', default_keep: false },
		],
	});
});

afterEach(() => {
	if (instance) unmount(instance);
	target?.remove();
});

describe('SettingsDangerZone factory reset', () => {
	it('renders the factory reset panel with keep options from the catalog', async () => {
		target = document.createElement('div');
		document.body.appendChild(target);
		instance = mount(SettingsDangerZone, { target, props: { settings: {} } });
		await flush();

		const text = target.textContent || '';
		expect(text).toContain('Factory reset');
		expect(text).toContain('Keep Brain memory');
		expect(text).toContain('Keep Market data');
		expect(mockGet).toHaveBeenCalledTimes(1);

		// default_keep drives the initial checkbox state.
		const brain = target.querySelector('#keep-brain') as HTMLInputElement | null;
		const market = target.querySelector('#keep-market_data') as HTMLInputElement | null;
		expect(brain?.checked).toBe(true);
		expect(market?.checked).toBe(false);
	});

	it('requires a typed confirmation before wiping', async () => {
		target = document.createElement('div');
		document.body.appendChild(target);
		instance = mount(SettingsDangerZone, { target, props: { settings: {} } });
		await flush();

		const trigger = Array.from(target.querySelectorAll('button')).find((b) =>
			/factory reset/i.test(b.textContent || ''),
		);
		expect(trigger).toBeTruthy();
		trigger!.click();
		await flush();

		expect(target.textContent).toContain('Confirm factory reset');
		expect(target.querySelector('[role="dialog"]')).not.toBeNull();
		// Nothing is wiped until the operator confirms.
		expect(mockReset).not.toHaveBeenCalled();
	});
});
