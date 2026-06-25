import { describe, it, expect, afterEach } from 'vitest';
import { mount, unmount } from 'svelte';
import SettingsSubsection from '../lib/components/settings/primitives/SettingsSubsection.svelte';

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

describe('SettingsSubsection', () => {
	it('renders label, description, and deep-link anchor', async () => {
		target = document.createElement('div');
		document.body.appendChild(target);
		instance = mount(SettingsSubsection, {
			target,
			props: {
				label: 'Risk Management',
				description: 'Controls when new trades are blocked.',
				deepLinkTo: '/risk-monitor',
				usedBy: ['axiom.risk_sentinel'],
			},
		});
		await flush();

		expect(target.textContent).toContain('Risk Management');
		expect(target.textContent).toContain('Controls when new trades are blocked.');

		const anchor = target.querySelector('a[href="/risk-monitor"]') as HTMLAnchorElement | null;
		expect(anchor).toBeTruthy();
		expect(anchor!.textContent).toContain('/risk-monitor');
	});

	it('toggles the Used by block closed-by-default', async () => {
		target = document.createElement('div');
		document.body.appendChild(target);
		instance = mount(SettingsSubsection, {
			target,
			props: {
				label: 'X',
				description: '.',
				usedBy: ['axiom.risk_sentinel'],
			},
		});
		await flush();

		// Closed by default — reader name should not yet appear.
		expect(target.textContent).not.toContain('axiom.risk_sentinel');

		const toggle = target.querySelector('button[aria-expanded]') as
			| HTMLButtonElement
			| null;
		expect(toggle).toBeTruthy();
		expect(toggle!.getAttribute('aria-expanded')).toBe('false');

		toggle!.click();
		await flush();

		expect(toggle!.getAttribute('aria-expanded')).toBe('true');
		expect(target.textContent).toContain('axiom.risk_sentinel');
	});

	it('omits the deep-link anchor when deepLinkTo is undefined', async () => {
		target = document.createElement('div');
		document.body.appendChild(target);
		instance = mount(SettingsSubsection, {
			target,
			props: {
				label: 'Plain section',
				description: 'No deep link here.',
			},
		});
		await flush();

		expect(target.querySelector('a')).toBeNull();
	});
});
