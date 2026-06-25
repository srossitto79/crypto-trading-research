<script lang="ts">
	import { onMount } from 'svelte';
	import { beforeNavigate } from '$app/navigation';
	import {
		getApprovalModes,
		putApprovalModes,
		type ApprovalModesSettings,
	} from '$lib/api/axiom';

	type ModeRow = {
		category: string;
		mode: string;
		deadlineHours: number;
	};

	let settings: ApprovalModesSettings | null = null;
	let validModes: string[] = ['manual', 'smart', 'off'];
	let offAllowlist: string[] = [];
	let knownCategories: string[] = [];
	let rows: ModeRow[] = [];
	let defaultMode = 'manual';
	let defaultDeadlineHours = 72;
	let escalationOwner = '';
	let loading = true;
	let saving = false;
	let error: string | null = null;
	let actionMessage: string | null = null;
	let newCategory = '';
	let savedSnapshot = '';

	function snapshot(): string {
		return JSON.stringify({
			defaultMode,
			defaultDeadlineHours: Number(defaultDeadlineHours),
			escalationOwner: escalationOwner.trim(),
			rows: rows.map((r) => ({
				category: r.category,
				mode: r.mode,
				deadlineHours: Number(r.deadlineHours),
			})),
		});
	}

	// Reactive: categories whose chosen mode is rejected by the server (off but
	// not allowlisted). Surfaced inline per-row instead of only on save.
	$: invalidCategories = new Set(
		rows.filter((r) => !isModeAllowed(r.category, r.mode)).map((r) => r.category),
	);
	// Reactive: known categories that the server will not permit mode=off for.
	$: offGatedCategories = knownCategories
		.filter((c) => !offAllowlist.includes(c))
		.sort((a, b) => a.localeCompare(b));
	$: isDirty = !loading && snapshot() !== savedSnapshot;

	function buildRows(s: ApprovalModesSettings): ModeRow[] {
		const seen = new Set<string>();
		const out: ModeRow[] = [];
		for (const cat of s.known_categories) {
			seen.add(cat);
			out.push({
				category: cat,
				mode: s.modes[cat] ?? s.default_mode,
				deadlineHours: s.deadlines_hours[cat] ?? s.default_deadline_hours,
			});
		}
		for (const cat of Object.keys(s.modes)) {
			if (seen.has(cat)) continue;
			seen.add(cat);
			out.push({
				category: cat,
				mode: s.modes[cat] ?? s.default_mode,
				deadlineHours: s.deadlines_hours[cat] ?? s.default_deadline_hours,
			});
		}
		out.sort((a, b) => a.category.localeCompare(b.category));
		return out;
	}

	async function load() {
		loading = true;
		error = null;
		try {
			const fresh = await getApprovalModes();
			settings = fresh;
			validModes = fresh.valid_modes || validModes;
			offAllowlist = fresh.off_allowlist || [];
			knownCategories = fresh.known_categories || [];
			defaultMode = fresh.default_mode || 'manual';
			defaultDeadlineHours = Number(fresh.default_deadline_hours) || 72;
			escalationOwner = fresh.escalation_owner || '';
			rows = buildRows(fresh);
			savedSnapshot = snapshot();
		} catch (err) {
			error = err instanceof Error ? err.message : String(err);
		} finally {
			loading = false;
		}
	}

	function isModeAllowed(category: string, mode: string): boolean {
		if (mode !== 'off') return true;
		return offAllowlist.includes(category);
	}

	function ensureRow(category: string): ModeRow | null {
		const trimmed = category.trim();
		if (!trimmed) return null;
		const existing = rows.find((r) => r.category === trimmed);
		if (existing) return existing;
		const fresh: ModeRow = { category: trimmed, mode: defaultMode, deadlineHours: defaultDeadlineHours };
		rows = [...rows, fresh].sort((a, b) => a.category.localeCompare(b.category));
		return fresh;
	}

	function addCategory() {
		if (!newCategory.trim()) return;
		ensureRow(newCategory);
		newCategory = '';
	}

	function removeRow(category: string) {
		rows = rows.filter((r) => r.category !== category);
	}

	async function save() {
		saving = true;
		error = null;
		actionMessage = null;
		try {
			const modes: Record<string, string> = {};
			const deadlines: Record<string, number> = {};
			for (const row of rows) {
				if (!isModeAllowed(row.category, row.mode)) {
					throw new Error(`Mode 'off' is not allowed for category '${row.category}'.`);
				}
				modes[row.category] = row.mode;
				deadlines[row.category] = Number(row.deadlineHours) || defaultDeadlineHours;
			}
			const payload = {
				modes,
				default_mode: defaultMode,
				deadlines_hours: deadlines,
				default_deadline_hours: Number(defaultDeadlineHours) || 72,
				escalation_owner: escalationOwner.trim(),
			};
			const fresh = await putApprovalModes(payload);
			settings = fresh;
			offAllowlist = fresh.off_allowlist || offAllowlist;
			knownCategories = fresh.known_categories || knownCategories;
			rows = buildRows(fresh);
			savedSnapshot = snapshot();
			actionMessage = 'Approval modes saved.';
		} catch (err) {
			error = err instanceof Error ? err.message : String(err);
		} finally {
			saving = false;
		}
	}

	beforeNavigate((navigation) => {
		if (!isDirty) return;
		const proceed = window.confirm(
			'You have unsaved approval-mode changes. Leave this page and discard them?',
		);
		if (!proceed) navigation.cancel();
	});

	onMount(load);
</script>

<svelte:head><title>Approval Modes | Axiom</title></svelte:head>

<div class="space-y-6 p-6 max-w-5xl">
	<header class="flex items-center justify-between">
		<div>
			<div class="text-[11px] uppercase tracking-[0.18em] text-gray-500">Settings</div>
			<h1 class="text-2xl font-semibold text-gray-100">Approval Modes</h1>
			<p class="mt-1 text-xs text-gray-500 max-w-2xl">
				Configure per-category approval behavior. <strong>manual</strong> requires operator review,
				<strong>smart</strong> classifies via the auxiliary LLM and auto-approves only when
				the classifier returns <code>auto_approve</code> with high confidence, and
				<strong>off</strong> auto-approves immediately (allowed only for low-stakes categories).
			</p>
		</div>
		<div class="flex items-center gap-2">
			<a
				href="/approval"
				class="text-xs border border-[#333] px-3 py-1.5 rounded text-gray-300 hover:text-gray-100"
			>
				View pending approvals
			</a>
			<button
				type="button"
				class="text-xs border border-[#333] px-3 py-1.5 rounded text-gray-300"
				on:click={() => void load()}
			>
				Reload
			</button>
		</div>
	</header>

	{#if actionMessage}<div class="bg-emerald-900/20 border border-emerald-800 text-emerald-300 text-xs px-3 py-2 rounded">{actionMessage}</div>{/if}
	{#if error}<div class="bg-red-900/20 border border-red-800 text-red-300 text-xs px-3 py-2 rounded">{error}</div>{/if}

	{#if loading}
		<div class="text-gray-500">Loading...</div>
	{:else}
		<section class="border border-[#222] rounded p-4 space-y-4 bg-[#0a0a0a]">
			<h2 class="text-sm uppercase tracking-wider text-gray-400">Defaults</h2>
			<div class="grid sm:grid-cols-3 gap-4">
				<label class="block text-xs">
					<span class="text-gray-500 uppercase tracking-wider">Default mode</span>
					<select bind:value={defaultMode} class="mt-1 w-full bg-black border border-[#222] px-2 py-1.5 text-gray-200">
						{#each validModes as mode}
							<option value={mode}>{mode}</option>
						{/each}
					</select>
				</label>
				<label class="block text-xs">
					<span class="text-gray-500 uppercase tracking-wider">Default deadline (hours)</span>
					<input
						type="number"
						min="1"
						max="720"
						bind:value={defaultDeadlineHours}
						class="mt-1 w-full bg-black border border-[#222] px-2 py-1.5 text-gray-200"
					/>
				</label>
				<label class="block text-xs">
					<span class="text-gray-500 uppercase tracking-wider">Escalation owner (display id)</span>
					<input
						type="text"
						bind:value={escalationOwner}
						placeholder="e.g. operator"
						class="mt-1 w-full bg-black border border-[#222] px-2 py-1.5 text-gray-200"
					/>
				</label>
			</div>
		</section>

		<section class="border border-[#222] rounded bg-[#0a0a0a] overflow-hidden">
			<header class="px-4 py-3 flex items-center justify-between border-b border-[#222]">
				<h2 class="text-sm uppercase tracking-wider text-gray-400">Per-category overrides</h2>
				<div class="flex items-center gap-2">
					<input
						type="text"
						placeholder="add category…"
						bind:value={newCategory}
						class="bg-black border border-[#222] px-2 py-1 text-xs text-gray-200"
					/>
					<button
						type="button"
						class="border border-[#333] px-3 py-1 text-xs text-gray-300 rounded"
						on:click={addCategory}
					>
						Add
					</button>
				</div>
			</header>
			{#if rows.length === 0}
				<div class="px-4 py-6 text-xs text-gray-500">No category overrides yet — defaults apply to everything.</div>
			{:else}
				<table class="w-full text-xs">
					<thead class="bg-[#101010] text-gray-500 uppercase tracking-wider">
						<tr>
							<th class="text-left px-3 py-2">Category</th>
							<th class="text-left px-3 py-2">Mode</th>
							<th class="text-left px-3 py-2">Deadline (hours)</th>
							<th class="text-left px-3 py-2">Off allowed?</th>
							<th></th>
						</tr>
					</thead>
					<tbody>
						{#each rows as row (row.category)}
							<tr class="border-t border-[#1a1a1a]">
								<td class="px-3 py-2 font-mono text-gray-200">{row.category}</td>
								<td class="px-3 py-2">
									<select
										bind:value={row.mode}
										class="bg-black border px-2 py-1 text-gray-200 {invalidCategories.has(row.category) ? 'border-red-700' : 'border-[#222]'}"
									>
										{#each validModes as mode}
											<option value={mode} disabled={mode === 'off' && !offAllowlist.includes(row.category)}>{mode}</option>
										{/each}
									</select>
									{#if invalidCategories.has(row.category)}
										<div class="mt-1 text-[10px] text-red-400">mode=off not allowed for this category</div>
									{/if}
								</td>
								<td class="px-3 py-2">
									<input
										type="number"
										min="1"
										max="720"
										bind:value={row.deadlineHours}
										class="w-24 bg-black border border-[#222] px-2 py-1 text-gray-200"
									/>
								</td>
								<td class="px-3 py-2">
									{#if offAllowlist.includes(row.category)}
										<span class="text-emerald-400">Yes</span>
									{:else}
										<span class="text-gray-500" title="Server rejects mode=off for this category. Controlled in backend code.">No (server-gated)</span>
									{/if}
								</td>
								<td class="px-3 py-2 text-right">
									<button
										type="button"
										class="text-gray-500 hover:text-red-300 text-xs"
										on:click={() => removeRow(row.category)}
									>
										Remove
									</button>
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			{/if}
		</section>

		<section class="border border-[#222] rounded p-4 bg-[#0a0a0a]">
			<h2 class="text-sm uppercase tracking-wider text-gray-400 mb-2">Off allowlist (server-enforced)</h2>
			<p class="text-[11px] text-gray-500 mb-3">
				Setting a category to <code>off</code> auto-approves it immediately, so eligibility is
				deliberately controlled in backend code rather than this UI — the server rejects any
				<code>off</code> mode for a category not on the allowlist below. This is a safety guardrail,
				not an oversight: high-stakes categories cannot be silenced from the operator console.
			</p>
			<div class="mb-2 text-[11px] uppercase tracking-wider text-gray-500">Eligible for off ({offAllowlist.length})</div>
			<div class="flex flex-wrap gap-2 text-[11px]">
				{#each offAllowlist as cat}
					<span class="border border-[#333] bg-[#111] px-2 py-1 rounded font-mono text-gray-300">{cat}</span>
				{:else}
					<span class="text-gray-500">No categories permit mode=off.</span>
				{/each}
			</div>
			{#if offGatedCategories.length > 0}
				<div class="mt-3 mb-2 text-[11px] uppercase tracking-wider text-gray-500">Server-gated (off not permitted)</div>
				<div class="flex flex-wrap gap-2 text-[11px]">
					{#each offGatedCategories as cat}
						<span class="border border-[#222] bg-black px-2 py-1 rounded font-mono text-gray-500">{cat}</span>
					{/each}
				</div>
			{/if}
		</section>

		<div class="flex items-center gap-3">
			<button
				type="button"
				disabled={saving || invalidCategories.size > 0}
				class="border border-emerald-700 bg-emerald-900/20 hover:bg-emerald-900/40 text-emerald-300 px-4 py-2 rounded disabled:opacity-40"
				on:click={() => void save()}
			>
				{saving ? 'Saving...' : 'Save changes'}
			</button>
			{#if invalidCategories.size > 0}
				<span class="text-[11px] text-red-400">Resolve the highlighted row(s) before saving.</span>
			{:else if isDirty}
				<span class="text-[11px] text-amber-400">Unsaved changes</span>
			{/if}
		</div>
	{/if}
</div>
