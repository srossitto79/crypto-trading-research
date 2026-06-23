<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { goto } from '$app/navigation';
	import {
		listBots,
		listTemplates,
		startBot,
		stopBot,
		deleteBot,
		cloneBot,
		killAllBots,
		type BotConfig,
		type BotTemplate,
	} from '$lib/api';

	let bots: BotConfig[] = [];
	let templates: BotTemplate[] = [];
	let loading = true;
	let error: string | null = null;
	let actionMsg: string | null = null;
	let confirmDelete: string | null = null;
	let confirmStop: string | null = null;
	let confirmKillAll = false;
	let pollInterval: ReturnType<typeof setInterval> | null = null;

	// In-flight action guards: track which bots have a request in flight (by id)
	// and a global flag for Kill-All, so buttons can be disabled to prevent
	// double-click races.
	let busyBots = new Set<string>();
	let killAllBusy = false;

	function setBotBusy(id: string, busy: boolean) {
		// Reassign so Svelte tracks the change.
		const next = new Set(busyBots);
		if (busy) next.add(id);
		else next.delete(id);
		busyBots = next;
	}

	$: hasRunningBots = bots.some((b) => (b.runtime_status || b.status) === 'running');

	async function load() {
		try {
			[bots, templates] = await Promise.all([listBots(), listTemplates()]);
			error = null;
		} catch (e: any) {
			error = e.message || 'Failed to load bots';
		} finally {
			loading = false;
		}
	}

	async function handleStart(id: string) {
		if (busyBots.has(id)) return;
		setBotBusy(id, true);
		try {
			await startBot(id);
			actionMsg = 'Bot started';
			await load();
		} catch (e: any) {
			actionMsg = `Error: ${e.message}`;
		} finally {
			setBotBusy(id, false);
		}
	}

	async function handleStop(id: string) {
		if (busyBots.has(id)) return;
		confirmStop = null;
		setBotBusy(id, true);
		try {
			await stopBot(id);
			actionMsg = 'Bot stopped';
			await load();
		} catch (e: any) {
			actionMsg = `Error: ${e.message}`;
		} finally {
			setBotBusy(id, false);
		}
	}

	async function handleDelete(id: string) {
		if (busyBots.has(id)) return;
		confirmDelete = null;
		setBotBusy(id, true);
		try {
			await deleteBot(id);
			actionMsg = 'Bot deleted';
			await load();
		} catch (e: any) {
			actionMsg = `Error: ${e.message}`;
		} finally {
			setBotBusy(id, false);
		}
	}

	async function handleClone(id: string, name: string) {
		if (busyBots.has(id)) return;
		setBotBusy(id, true);
		try {
			const cloned = await cloneBot(id, `${name} (copy)`);
			actionMsg = 'Bot cloned';
			await load();
		} catch (e: any) {
			actionMsg = `Error: ${e.message}`;
		} finally {
			setBotBusy(id, false);
		}
	}

	async function handleKillAll() {
		if (killAllBusy) return;
		confirmKillAll = false;
		killAllBusy = true;
		try {
			const result = await killAllBots();
			actionMsg = `Stopped ${result.stopped} bot(s)`;
			await load();
		} catch (e: any) {
			actionMsg = `Error: ${e.message}`;
		} finally {
			killAllBusy = false;
		}
	}

	function createFromTemplate(templateId: string) {
		goto(`/bot-factory/editor?template=${templateId}`);
	}

	function statusColor(bot: BotConfig): string {
		const s = bot.runtime_status || bot.status;
		if (s === 'running') return 'text-emerald-400';
		if (s === 'error') return 'text-rose-400';
		if (s === 'paused') return 'text-amber-400';
		return 'text-gray-500';
	}

	function statusDot(bot: BotConfig): string {
		const s = bot.runtime_status || bot.status;
		if (s === 'running') return 'bg-emerald-400';
		if (s === 'error') return 'bg-rose-400';
		if (s === 'paused') return 'bg-amber-400';
		return 'bg-gray-600';
	}

	onMount(() => {
		load();
		pollInterval = setInterval(load, 5000);
	});

	onDestroy(() => {
		if (pollInterval) clearInterval(pollInterval);
	});
</script>

<svelte:head>
	<title>Bot Factory | Forven</title>
</svelte:head>

<div class="mx-auto max-w-7xl px-4 py-6">
	<!-- Experimental warning -->
	<div class="mb-6 rounded-lg border-2 border-amber-500/60 bg-amber-500/10 p-4">
		<div class="flex items-start gap-3">
			<svg xmlns="http://www.w3.org/2000/svg" class="mt-0.5 h-6 w-6 flex-shrink-0 text-amber-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
				<path stroke-linecap="round" stroke-linejoin="round" d="M12 9v2m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
			</svg>
			<div class="min-w-0 flex-1">
				<h2 class="text-sm font-bold uppercase tracking-wider text-amber-300">Highly Experimental</h2>
				<p class="mt-1 text-sm text-amber-100/90">
					Bot Factory is highly experimental and its features are not currently fully working.
					Expect bugs, incomplete functionality, and breaking changes. Do not rely on it for live trading.
				</p>
			</div>
		</div>
	</div>

	<!-- Header -->
	<div class="mb-6 flex items-center justify-between">
		<div>
			<h1 class="text-2xl font-bold text-white">Bot Factory</h1>
			<p class="mt-1 text-sm text-gray-400">Create and manage autonomous LLM trading bots</p>
		</div>
		<div class="flex gap-2">
			{#if hasRunningBots}
				{#if confirmKillAll}
					<button
						on:click={handleKillAll}
						disabled={killAllBusy}
						aria-busy={killAllBusy}
						class="rounded-lg border border-rose-500/40 bg-rose-500/20 px-4 py-2 text-sm font-medium text-rose-200 transition hover:bg-rose-500/30 disabled:cursor-not-allowed disabled:opacity-50"
					>
						{killAllBusy ? 'Stopping…' : 'Confirm kill all'}
					</button>
					<button
						on:click={() => (confirmKillAll = false)}
						disabled={killAllBusy}
						class="rounded-lg border border-[#333] bg-[#222] px-4 py-2 text-sm font-medium text-gray-400 transition hover:bg-[#2a2a2a] disabled:cursor-not-allowed disabled:opacity-50"
					>
						Cancel
					</button>
				{:else}
					<button
						on:click={() => (confirmKillAll = true)}
						disabled={killAllBusy}
						class="rounded-lg border border-rose-500/30 bg-rose-500/10 px-4 py-2 text-sm font-medium text-rose-300 transition hover:bg-rose-500/20 disabled:cursor-not-allowed disabled:opacity-50"
					>
						Kill All Bots
					</button>
				{/if}
			{/if}
			<button
				on:click={() => goto('/bot-factory/editor')}
				class="rounded-lg bg-sky-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-sky-500"
			>
				Create New Bot
			</button>
		</div>
	</div>

	<!-- Action message -->
	{#if actionMsg}
		<div class="mb-4 rounded-lg border border-sky-500/20 bg-sky-500/5 p-3 text-sm text-sky-300">
			{actionMsg}
			<button on:click={() => (actionMsg = null)} class="ml-2 text-sky-400 hover:text-sky-300">dismiss</button>
		</div>
	{/if}

	{#if loading}
		<div class="py-20 text-center text-gray-500">Loading...</div>
	{:else if error}
		<div class="py-20 text-center text-rose-400">{error}</div>
	{:else if bots.length === 0}
		<!-- Empty state: show templates -->
		<div class="py-12 text-center">
			<div class="mx-auto mb-2 h-16 w-16 rounded-full bg-sky-500/10 p-4">
				<svg class="h-8 w-8 text-sky-400" fill="currentColor" viewBox="0 0 24 24">
					<path d="M20 9V7c0-1.1-.9-2-2-2h-3c0-1.66-1.34-3-3-3S9 3.34 9 5H6c-1.1 0-2 .9-2 2v2c-1.66 0-3 1.34-3 3s1.34 3 3 3v4c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2v-4c1.66 0 3-1.34 3-3s-1.34-3-3-3z" />
				</svg>
			</div>
			<h2 class="mb-1 text-lg font-semibold text-white">Create your first trading bot</h2>
			<p class="mb-8 text-sm text-gray-400">Start from a template or build from scratch</p>

			<div class="mx-auto grid max-w-4xl grid-cols-1 gap-4 sm:grid-cols-2">
				{#each templates as template}
					<button
						on:click={() => createFromTemplate(template.id)}
						class="rounded-xl border border-[#2a2a2a] bg-[#1a1a1a] p-5 text-left transition hover:border-sky-500/30 hover:bg-[#1e1e1e]"
					>
						<h3 class="mb-1 font-semibold text-white">{template.name}</h3>
						<p class="text-sm text-gray-400">{template.description}</p>
					</button>
				{/each}
				<button
					on:click={() => goto('/bot-factory/editor')}
					class="rounded-xl border border-dashed border-[#333] bg-transparent p-5 text-left transition hover:border-sky-500/30"
				>
					<h3 class="mb-1 font-semibold text-gray-300">Start from scratch</h3>
					<p class="text-sm text-gray-500">Build a bot with a blank configuration</p>
				</button>
			</div>
		</div>
	{:else}
		<!-- Bot grid -->
		<div class="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
			{#each bots as bot}
				{@const isBusy = busyBots.has(bot.id)}
				<div class="rounded-xl border border-[#2a2a2a] bg-[#1a1a1a] p-5">
					<!-- Header -->
					<div class="mb-3 flex items-start justify-between">
						<div>
							<a href="/bot-factory/{bot.id}" class="font-semibold text-white hover:text-sky-400">
								{bot.name}
							</a>
							<div class="mt-0.5 flex items-center gap-2 text-xs text-gray-500">
								<span>{bot.model}</span>
								<span class="inline-block h-1 w-1 rounded-full bg-gray-600"></span>
								<span>{bot.asset_mode === 'locked' ? (bot.locked_pairs || []).join(', ') : 'Free roam'}</span>
							</div>
						</div>
						<div class="flex items-center gap-1.5">
							<span class="inline-block h-2 w-2 rounded-full {statusDot(bot)}"></span>
							<span class="text-xs {statusColor(bot)}">{bot.runtime_status || bot.status}</span>
						</div>
					</div>

					<!-- Stats -->
					<div class="mb-4 grid grid-cols-3 gap-2 text-center text-xs">
						<div class="rounded-lg bg-[#121212] px-2 py-1.5">
							<div class="text-gray-500">Capital</div>
							<div class="font-medium text-gray-200">${(bot.capital_allocation || 0).toLocaleString()}</div>
						</div>
						<div class="rounded-lg bg-[#121212] px-2 py-1.5">
							<div class="text-gray-500">LLM Calls</div>
							<div class="font-medium text-gray-200">{bot.llm_calls_today ?? 0}/{bot.max_llm_calls_per_day}</div>
						</div>
						<div class="rounded-lg bg-[#121212] px-2 py-1.5">
							<div class="text-gray-500">Verbosity</div>
							<div class="font-medium text-gray-200">{bot.reasoning_verbosity}</div>
						</div>
					</div>

					{#if bot.error_message && (bot.runtime_status || bot.status) !== 'running'}
						<div class="mb-3 rounded-lg bg-rose-500/5 border border-rose-500/20 p-2 text-xs text-rose-300">
							{bot.error_message}
						</div>
					{/if}

					<!-- Actions -->
					<div class="flex gap-2 text-xs">
						{#if (bot.runtime_status || bot.status) === 'running'}
							{#if confirmStop === bot.id}
								<button on:click={() => handleStop(bot.id)} disabled={isBusy} aria-busy={isBusy} class="rounded bg-rose-600/20 px-2 py-1 text-rose-300 disabled:cursor-not-allowed disabled:opacity-50">{isBusy ? 'Stopping…' : 'Confirm stop'}</button>
								<button on:click={() => (confirmStop = null)} disabled={isBusy} class="rounded bg-[#222] px-2 py-1 text-gray-400 disabled:cursor-not-allowed disabled:opacity-50">Cancel</button>
							{:else}
								<button on:click={() => (confirmStop = bot.id)} disabled={isBusy} class="rounded bg-rose-600/10 px-2 py-1 text-rose-400 hover:bg-rose-600/20 disabled:cursor-not-allowed disabled:opacity-50">Stop</button>
							{/if}
						{:else}
							<button on:click={() => handleStart(bot.id)} disabled={isBusy} aria-busy={isBusy} class="rounded bg-emerald-600/10 px-2 py-1 text-emerald-400 hover:bg-emerald-600/20 disabled:cursor-not-allowed disabled:opacity-50">{isBusy ? 'Starting…' : 'Start'}</button>
						{/if}
						<button on:click={() => goto(`/bot-factory/editor?id=${bot.id}`)} class="rounded bg-[#222] px-2 py-1 text-gray-300 hover:bg-[#2a2a2a]">Edit</button>
						<button on:click={() => handleClone(bot.id, bot.name)} disabled={isBusy} aria-busy={isBusy} class="rounded bg-[#222] px-2 py-1 text-gray-300 hover:bg-[#2a2a2a] disabled:cursor-not-allowed disabled:opacity-50">{isBusy ? 'Cloning…' : 'Clone'}</button>
						{#if confirmDelete === bot.id}
							<button on:click={() => handleDelete(bot.id)} disabled={isBusy} aria-busy={isBusy} class="rounded bg-rose-600/20 px-2 py-1 text-rose-300 disabled:cursor-not-allowed disabled:opacity-50">{isBusy ? 'Deleting…' : 'Confirm'}</button>
							<button on:click={() => (confirmDelete = null)} disabled={isBusy} class="rounded bg-[#222] px-2 py-1 text-gray-400 disabled:cursor-not-allowed disabled:opacity-50">Cancel</button>
						{:else}
							<button
								on:click={() => (confirmDelete = bot.id)}
								class="rounded bg-[#222] px-2 py-1 text-gray-500 hover:text-rose-400 disabled:cursor-not-allowed disabled:opacity-50"
								disabled={(bot.runtime_status || bot.status) === 'running' || isBusy}
							>Delete</button>
						{/if}
					</div>
				</div>
			{/each}
		</div>
	{/if}
</div>
