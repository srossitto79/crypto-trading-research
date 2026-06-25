<script lang="ts">
	import { onMount } from 'svelte';
	import {
		getFactoryResetCategories,
		performFactoryReset,
		type FactoryResetCategory,
	} from '$lib/api/axiom';

	// Accepted for parity with the other section components (the settings shell passes
	// it); the Danger Zone is a custom destructive-action panel and does not read the
	// flat settings blob.
	export let settings: Record<string, unknown> = {};
	void settings;

	let categories: FactoryResetCategory[] = [];
	let keep: Record<string, boolean> = {};
	let loading = true;
	let loadError: string | null = null;

	let confirmOpen = false;
	let confirmText = '';
	let resetting = false;
	let resultMessage: string | null = null;
	let resultError: string | null = null;

	onMount(async () => {
		try {
			const res = await getFactoryResetCategories();
			categories = res.categories ?? [];
			const next: Record<string, boolean> = {};
			for (const c of categories) next[c.id] = !!c.default_keep;
			keep = next;
		} catch (e) {
			loadError = e instanceof Error ? e.message : 'Failed to load reset categories.';
		} finally {
			loading = false;
		}
	});

	$: keepIds = categories.filter((c) => keep[c.id]).map((c) => c.id);
	$: wipeLabels = categories.filter((c) => !keep[c.id]).map((c) => c.label);
	$: confirmArmed = confirmText.trim().toUpperCase() === 'RESET';

	function toggleKeep(id: string, checked: boolean): void {
		keep = { ...keep, [id]: checked };
	}

	function openConfirm(): void {
		confirmText = '';
		resultError = null;
		resultMessage = null;
		confirmOpen = true;
	}

	function cancelConfirm(): void {
		confirmOpen = false;
	}

	async function doReset(): Promise<void> {
		if (!confirmArmed || resetting) return;
		resetting = true;
		resultError = null;
		resultMessage = null;
		try {
			const res = await performFactoryReset(keepIds);
			const wiped = (res.wiped ?? []).join(', ') || 'nothing';
			const kept = (res.kept ?? []).join(', ') || 'nothing';
			resultMessage = `Factory reset complete. Wiped: ${wiped}. Kept: ${kept}.`;
			confirmOpen = false;
		} catch (e) {
			resultError = e instanceof Error ? e.message : 'Factory reset failed.';
		} finally {
			resetting = false;
		}
	}
</script>

<div class="space-y-6">
	<div class="border border-red-900 bg-red-950/30 rounded p-5 space-y-4">
		<div>
			<h2 class="text-lg font-semibold text-red-200">Factory reset</h2>
			<p class="text-sm text-red-300/80 mt-1">
				Permanently wipes the selected data categories and restores a clean slate. This cannot be
				undone. Choose which categories to <strong>keep</strong> — everything else is erased.
			</p>
		</div>

		{#if loading}
			<p class="text-sm text-gray-400">Loading reset categories…</p>
		{:else if loadError}
			<p class="text-sm text-red-300">Could not load categories: {loadError}</p>
		{:else}
			<ul class="space-y-2">
				{#each categories as cat (cat.id)}
					<li class="flex items-start gap-3">
						<input
							id={`keep-${cat.id}`}
							type="checkbox"
							checked={keep[cat.id]}
							on:change={(e) => toggleKeep(cat.id, e.currentTarget.checked)}
							class="mt-1 accent-red-500"
						/>
						<label for={`keep-${cat.id}`} class="text-sm text-gray-200 leading-tight">
							<span class="font-medium">Keep {cat.label}</span>
							{#if cat.description}<span class="block text-gray-500">{cat.description}</span>{/if}
						</label>
					</li>
				{/each}
			</ul>

			<p class="text-xs text-red-300/80">
				{#if wipeLabels.length}
					Will wipe: {wipeLabels.join(', ')}.
				{:else}
					Nothing selected to wipe — every category is kept.
				{/if}
			</p>

			<button
				type="button"
				on:click={openConfirm}
				disabled={categories.length === 0}
				class="px-4 py-2 rounded bg-red-700 hover:bg-red-600 disabled:opacity-50 text-white text-sm font-medium focus:outline-none focus-visible:ring-2 focus-visible:ring-red-400"
			>
				Wipe &amp; factory reset…
			</button>
		{/if}

		{#if resultMessage}
			<p class="text-sm text-emerald-300">{resultMessage}</p>
		{/if}
		{#if resultError}
			<p class="text-sm text-red-300">{resultError}</p>
		{/if}
	</div>
</div>

{#if confirmOpen}
	<div
		class="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
		role="dialog"
		aria-modal="true"
		aria-labelledby="factory-reset-title"
	>
		<div class="w-full max-w-md rounded border border-red-900 bg-[#0a0a0a] p-5 space-y-4 shadow-xl">
			<h2 id="factory-reset-title" class="text-base font-semibold text-red-200">
				Confirm factory reset
			</h2>
			<p class="text-sm text-gray-300">
				This will permanently wipe:
				<strong class="text-red-300">{wipeLabels.join(', ') || 'nothing'}</strong>. This action
				cannot be undone.
			</p>
			<label class="block text-sm text-gray-400">
				Type <span class="font-mono text-red-300">RESET</span> to confirm:
				<input
					type="text"
					bind:value={confirmText}
					class="mt-1 w-full rounded border border-[#333] bg-black px-3 py-1.5 text-sm text-white focus:outline-none focus-visible:ring-2 focus-visible:ring-red-500"
					autocomplete="off"
				/>
			</label>
			<div class="flex justify-end gap-2">
				<button
					type="button"
					on:click={cancelConfirm}
					class="px-3 py-1.5 rounded border border-[#333] text-sm text-gray-300 hover:bg-[#161616] focus:outline-none focus-visible:ring-2 focus-visible:ring-gray-500"
				>
					Cancel
				</button>
				<button
					type="button"
					on:click={doReset}
					disabled={!confirmArmed || resetting}
					class="px-3 py-1.5 rounded bg-red-700 hover:bg-red-600 disabled:opacity-50 text-white text-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-red-400"
				>
					{resetting ? 'Resetting…' : 'Wipe everything'}
				</button>
			</div>
		</div>
	</div>
{/if}
