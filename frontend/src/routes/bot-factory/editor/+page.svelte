<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import {
		getBot,
		getTemplate,
		createBot,
		updateBot,
		listTemplates,
		type BotConfig,
		type BotTemplate,
	} from '$lib/api';
	import {
		createBotFromStrategy,
		createTemplate,
		deleteTemplate,
		type BotSessionHours,
	} from '$lib/api/bot_factory';

	const WEEKDAYS = [
		'monday',
		'tuesday',
		'wednesday',
		'thursday',
		'friday',
		'saturday',
		'sunday',
	] as const;

	let loading = true;
	let saving = false;
	let error: string | null = null;
	let editId: string | null = null;
	let templates: BotTemplate[] = [];
	let activeTab: 'core' | 'trading' | 'advanced' = 'core';
	let showTemplateSelector = false;

	// Form state
	let name = 'Untitled Bot';
	let model = ''; // empty = server resolves the operator's configured default
	let soul = '';
	let context = '';
	let strategy = '';
	let guardrails = '';
	let capitalAllocation = 100000;
	let maxPositionPct = 10;
	let maxConcurrentPositions = 5;
	let maxDrawdownPct = 3;
	let stopLossPct: number | null = null;
	let takeProfitPct: number | null = null;
	let takerFeeBps = 0;
	let slippageBps = 0;
	let cooldownSeconds = 60;
	let reasoningVerbosity = 'standard';
	let assetMode: 'free_roam' | 'locked' = 'free_roam';
	let lockedPairsText = '';
	let maxLlmCallsPerDay = 200;
	let maxConsecutiveErrors = 5;

	// Session hours — leaving unset (sessionHoursEnabled = false) = always active
	let sessionHoursEnabled = false;
	let sessionTimezone = 'America/New_York';
	let sessionDays: string[] = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday'];
	let sessionStart = '09:30';
	let sessionEnd = '16:00';

	function toggleSessionDay(day: string) {
		sessionDays = sessionDays.includes(day)
			? sessionDays.filter((d) => d !== day)
			: [...sessionDays, day];
	}

	function populateFromConfig(config: Partial<BotConfig> | Record<string, unknown>) {
		name = (config.name as string) || name;
		// Empty/null model is valid — server resolves the configured default.
		model = (config.model as string) ?? model ?? '';
		soul = (config.soul as string) || '';
		context = (config.context as string) || '';
		strategy = (config.strategy as string) || '';
		guardrails = (config.guardrails as string) || '';
		capitalAllocation = (config.capital_allocation as number) ?? capitalAllocation;
		maxPositionPct = (config.max_position_pct as number) ?? maxPositionPct;
		maxConcurrentPositions = (config.max_concurrent_positions as number) ?? maxConcurrentPositions;
		maxDrawdownPct = (config.max_drawdown_pct as number) ?? maxDrawdownPct;
		stopLossPct = (config.stop_loss_pct as number | null) ?? null;
		takeProfitPct = (config.take_profit_pct as number | null) ?? null;
		takerFeeBps = (config.taker_fee_bps as number) ?? takerFeeBps;
		slippageBps = (config.slippage_bps as number) ?? slippageBps;
		cooldownSeconds = (config.cooldown_seconds as number) ?? cooldownSeconds;
		reasoningVerbosity = (config.reasoning_verbosity as string) || 'standard';
		assetMode = ((config.asset_mode as string) || 'free_roam') as 'free_roam' | 'locked';
		const lp = config.locked_pairs;
		lockedPairsText = Array.isArray(lp) ? lp.join(', ') : '';
		maxLlmCallsPerDay = (config.max_llm_calls_per_day as number) ?? 200;
		maxConsecutiveErrors = (config.max_consecutive_errors as number) ?? 5;

		const sh = config.session_hours as Partial<BotSessionHours> | null | undefined;
		if (sh && typeof sh === 'object') {
			sessionHoursEnabled = true;
			sessionTimezone = sh.timezone || sessionTimezone;
			sessionDays = Array.isArray(sh.days) && sh.days.length ? sh.days : sessionDays;
			sessionStart = sh.start || sessionStart;
			sessionEnd = sh.end || sessionEnd;
		} else {
			sessionHoursEnabled = false;
		}
	}

	async function loadTemplateById(templateId: string) {
		try {
			const template = await getTemplate(templateId);
			if (template?.config_snapshot) {
				populateFromConfig(template.config_snapshot);
				name = template.name;
			}
		} catch {}
	}

	function buildConfig(): Record<string, unknown> {
		return {
			name,
			// Empty string → null so the server resolves the configured default model.
			model: model.trim() || null,
			soul: soul || null,
			context: context || null,
			strategy: strategy || null,
			guardrails: guardrails || null,
			capital_allocation: capitalAllocation,
			max_position_pct: maxPositionPct,
			max_concurrent_positions: maxConcurrentPositions,
			max_drawdown_pct: maxDrawdownPct,
			stop_loss_pct: stopLossPct,
			take_profit_pct: takeProfitPct,
			taker_fee_bps: takerFeeBps,
			slippage_bps: slippageBps,
			cooldown_seconds: cooldownSeconds,
			session_hours: sessionHoursEnabled
				? {
						timezone: sessionTimezone.trim() || 'America/New_York',
						days: sessionDays,
						start: sessionStart,
						end: sessionEnd,
					}
				: null,
			reasoning_verbosity: reasoningVerbosity,
			asset_mode: assetMode,
			locked_pairs:
				assetMode === 'locked'
					? lockedPairsText.split(',').map((s) => s.trim()).filter(Boolean)
					: null,
			max_llm_calls_per_day: maxLlmCallsPerDay,
			max_consecutive_errors: maxConsecutiveErrors,
		};
	}

	async function handleSave() {
		saving = true;
		error = null;
		try {
			const config = buildConfig();
			if (editId) {
				await updateBot(editId, config as Partial<BotConfig>);
			} else {
				await createBot(config as Partial<BotConfig>);
			}
			goto('/bot-factory');
		} catch (e: any) {
			error = e.message || 'Failed to save';
		} finally {
			saving = false;
		}
	}

	let savingTemplate = false;
	let deletingTemplateId: string | null = null;

	async function loadTemplates() {
		templates = await listTemplates();
	}

	async function handleSaveAsTemplate() {
		const templateName = (window.prompt('Template name', name) || '').trim();
		if (!templateName) return;
		const templateDesc = (window.prompt('Description (optional)', '') || '').trim();
		savingTemplate = true;
		error = null;
		try {
			await createTemplate(templateName, templateDesc || null, buildConfig());
			await loadTemplates();
		} catch (e: any) {
			error = e.message || 'Failed to save template';
		} finally {
			savingTemplate = false;
		}
	}

	async function handleDeleteTemplate(template: BotTemplate) {
		if (template.is_builtin) return;
		if (!window.confirm(`Delete the template "${template.name}"? This cannot be undone.`)) return;
		deletingTemplateId = template.id;
		error = null;
		try {
			await deleteTemplate(template.id);
			await loadTemplates();
		} catch (e: any) {
			error = e.message || 'Failed to delete template';
		} finally {
			deletingTemplateId = null;
		}
	}

	onMount(async () => {
		try {
			await loadTemplates();
			const params = $page.url.searchParams;
			editId = params.get('id');
			const templateId = params.get('template');

			const strategyId = params.get('strategy');

			if (editId) {
				const bot = await getBot(editId);
				if (bot) populateFromConfig(bot);
			} else if (strategyId) {
				try {
					const result = await createBotFromStrategy(strategyId);
					if (result?.config) populateFromConfig(result.config);
				} catch (e: any) {
					error = `Failed to load strategy: ${e.message}`;
				}
			} else if (templateId) {
				await loadTemplateById(templateId);
			} else {
				showTemplateSelector = true;
			}
		} catch (e: any) {
			error = e.message;
		} finally {
			loading = false;
		}
	});
</script>

<svelte:head>
	<title>{editId ? 'Edit Bot' : 'Create Bot'} | Bot Factory | Forven</title>
</svelte:head>

<div class="mx-auto max-w-4xl px-4 py-6">
	<!-- Header -->
	<div class="mb-6 flex items-center justify-between">
		<div>
			<button on:click={() => goto('/bot-factory')} class="mb-2 text-sm text-gray-500 hover:text-gray-300">&larr; Back to Bot Factory</button>
			<h1 class="text-xl font-bold text-white">{editId ? 'Edit Bot' : 'Create New Bot'}</h1>
		</div>
		<div class="flex items-center gap-2">
			<button
				on:click={handleSaveAsTemplate}
				disabled={saving || savingTemplate || !name.trim()}
				class="rounded-lg border border-[#333] bg-[#121212] px-4 py-2 text-sm font-medium text-gray-300 transition hover:border-sky-500/30 hover:text-white disabled:opacity-50"
			>
				{savingTemplate ? 'Saving...' : 'Save as Template'}
			</button>
			<button
				on:click={handleSave}
				disabled={saving || savingTemplate || !name.trim()}
				class="rounded-lg bg-sky-600 px-5 py-2 text-sm font-medium text-white transition hover:bg-sky-500 disabled:opacity-50"
			>
				{saving ? 'Saving...' : editId ? 'Save Changes' : 'Create Bot'}
			</button>
		</div>
	</div>

	{#if error}
		<div class="mb-4 rounded-lg border border-rose-500/20 bg-rose-500/5 p-3 text-sm text-rose-300">{error}</div>
	{/if}

	{#if loading}
		<div class="py-20 text-center text-gray-500">Loading...</div>
	{:else}
		<!-- Template selector (only on create, initially) -->
		{#if showTemplateSelector && !editId && templates.length > 0}
			<div class="mb-6 rounded-xl border border-[#2a2a2a] bg-[#1a1a1a] p-5">
				<h2 class="mb-3 text-sm font-semibold text-gray-300">Start from a template</h2>
				<div class="grid grid-cols-2 gap-3">
					{#each templates as template}
						<div class="relative">
							<button
								on:click={() => { populateFromConfig(template.config_snapshot); name = template.name; showTemplateSelector = false; }}
								class="w-full rounded-lg border border-[#333] bg-[#121212] p-3 text-left transition hover:border-sky-500/30"
							>
								<div class="pr-6 text-sm font-medium text-white">{template.name}</div>
								<div class="mt-0.5 text-xs text-gray-500">{template.description}</div>
							</button>
							{#if !template.is_builtin}
								<button
									type="button"
									on:click={() => handleDeleteTemplate(template)}
									disabled={deletingTemplateId === template.id}
									aria-label={`Delete template ${template.name}`}
									title="Delete template"
									class="absolute right-2 top-2 flex h-6 w-6 items-center justify-center rounded-md text-gray-500 transition hover:bg-rose-500/10 hover:text-rose-400 disabled:opacity-50"
								>
									{#if deletingTemplateId === template.id}
										<span class="h-3 w-3 animate-spin rounded-full border border-rose-400/40 border-t-rose-400"></span>
									{:else}
										&times;
									{/if}
								</button>
							{/if}
						</div>
					{/each}
				</div>
				<button on:click={() => (showTemplateSelector = false)} class="mt-3 text-xs text-gray-500 hover:text-gray-300">
					or start from scratch &rarr;
				</button>
			</div>
		{/if}

		<!-- Tabs -->
		<div class="mb-4 flex gap-1 rounded-lg border border-[#2a2a2a] bg-[#121212] p-1">
			{#each [['core', 'Core'], ['trading', 'Trading'], ['advanced', 'Advanced']] as [key, label]}
				<button
					on:click={() => (activeTab = key as typeof activeTab)}
					class="flex-1 rounded-md px-3 py-1.5 text-sm transition {activeTab === key ? 'bg-[#2a2a2a] text-white font-medium' : 'text-gray-500 hover:text-gray-300'}"
				>
					{label}
				</button>
			{/each}
		</div>

		<!-- Tab content -->
		<div class="rounded-xl border border-[#2a2a2a] bg-[#1a1a1a] p-6">
			{#if activeTab === 'core'}
				<div class="space-y-4">
					<div class="grid grid-cols-2 gap-4">
						<div>
							<label for="name" class="mb-1 block text-xs font-medium text-gray-400">Name</label>
							<input id="name" bind:value={name} class="w-full rounded-lg border border-[#333] bg-[#121212] px-3 py-2 text-sm text-white" />
						</div>
						<div>
							<label for="model" class="mb-1 block text-xs font-medium text-gray-400">Model</label>
							<input id="model" bind:value={model} placeholder="(default provider)" class="w-full rounded-lg border border-[#333] bg-[#121212] px-3 py-2 text-sm text-white" />
							<p class="mt-1 text-xs text-gray-600">Leave blank to use your configured default provider.</p>
						</div>
					</div>
					<div>
						<label for="soul" class="mb-1 block text-xs font-medium text-gray-400">Soul <span class="text-gray-600">— personality, temperament, decision style</span></label>
						<textarea id="soul" bind:value={soul} rows="4" class="w-full rounded-lg border border-[#333] bg-[#121212] px-3 py-2 text-sm text-white" placeholder="You are an aggressive momentum trader who thrives on volatility..."></textarea>
					</div>
					<div>
						<label for="strategy" class="mb-1 block text-xs font-medium text-gray-400">Strategy <span class="text-gray-600">— trading approach, broad or narrow</span></label>
						<textarea id="strategy" bind:value={strategy} rows="4" class="w-full rounded-lg border border-[#333] bg-[#121212] px-3 py-2 text-sm text-white" placeholder="Trade momentum breakouts on high-volume assets..."></textarea>
					</div>
					<div>
						<label for="context" class="mb-1 block text-xs font-medium text-gray-400">Context <span class="text-gray-600">— seed knowledge, research notes, market thesis</span></label>
						<textarea id="context" bind:value={context} rows="3" class="w-full rounded-lg border border-[#333] bg-[#121212] px-3 py-2 text-sm text-white" placeholder="BTC tends to correlate with macro risk-on sentiment..."></textarea>
					</div>
					<div>
						<label for="guardrails" class="mb-1 block text-xs font-medium text-gray-400">Guardrails <span class="text-gray-600">— behavioral rules (best-effort, LLM-interpreted)</span></label>
						<textarea id="guardrails" bind:value={guardrails} rows="3" class="w-full rounded-lg border border-[#333] bg-[#121212] px-3 py-2 text-sm text-white" placeholder="Never hold a position for more than 2 hours..."></textarea>
					</div>
				</div>
			{:else if activeTab === 'trading'}
				<div class="space-y-4">
					<div class="grid grid-cols-2 gap-4">
						<div>
							<label for="capital" class="mb-1 block text-xs font-medium text-gray-400">Capital Allocation ($)</label>
							<input id="capital" type="number" bind:value={capitalAllocation} min="0.01" step="any" class="w-full rounded-lg border border-[#333] bg-[#121212] px-3 py-2 text-sm text-white" />
						</div>
						<div>
							<label for="maxPos" class="mb-1 block text-xs font-medium text-gray-400">Max Position Size (%)</label>
							<input id="maxPos" type="number" bind:value={maxPositionPct} min="0.01" max="100" step="0.5" class="w-full rounded-lg border border-[#333] bg-[#121212] px-3 py-2 text-sm text-white" />
						</div>
						<div>
							<label for="maxConcurrent" class="mb-1 block text-xs font-medium text-gray-400">Max Concurrent Positions</label>
							<input id="maxConcurrent" type="number" bind:value={maxConcurrentPositions} min="1" max="100" step="1" class="w-full rounded-lg border border-[#333] bg-[#121212] px-3 py-2 text-sm text-white" />
						</div>
						<div>
							<label for="maxDrawdown" class="mb-1 block text-xs font-medium text-gray-400">Max Drawdown (%)</label>
							<input id="maxDrawdown" type="number" bind:value={maxDrawdownPct} min="0.01" max="100" step="0.5" class="w-full rounded-lg border border-[#333] bg-[#121212] px-3 py-2 text-sm text-white" />
						</div>
						<div>
							<label for="stopLoss" class="mb-1 block text-xs font-medium text-gray-400">Stop Loss (%) <span class="text-gray-600">— optional</span></label>
							<input id="stopLoss" type="number" bind:value={stopLossPct} min="0.01" max="100" step="0.5" placeholder="none" class="w-full rounded-lg border border-[#333] bg-[#121212] px-3 py-2 text-sm text-white" />
						</div>
						<div>
							<label for="takeProfit" class="mb-1 block text-xs font-medium text-gray-400">Take Profit (%) <span class="text-gray-600">— optional</span></label>
							<input id="takeProfit" type="number" bind:value={takeProfitPct} min="0.01" step="0.5" placeholder="none" class="w-full rounded-lg border border-[#333] bg-[#121212] px-3 py-2 text-sm text-white" />
						</div>
						<div>
							<label for="takerFee" class="mb-1 block text-xs font-medium text-gray-400">Taker Fee (bps)</label>
							<input id="takerFee" type="number" bind:value={takerFeeBps} min="0" step="0.1" class="w-full rounded-lg border border-[#333] bg-[#121212] px-3 py-2 text-sm text-white" />
						</div>
						<div>
							<label for="slippage" class="mb-1 block text-xs font-medium text-gray-400">Slippage (bps)</label>
							<input id="slippage" type="number" bind:value={slippageBps} min="0" step="0.1" class="w-full rounded-lg border border-[#333] bg-[#121212] px-3 py-2 text-sm text-white" />
						</div>
						<div>
							<label for="cooldown" class="mb-1 block text-xs font-medium text-gray-400">Cooldown (seconds)</label>
							<input id="cooldown" type="number" bind:value={cooldownSeconds} min="1" step="1" class="w-full rounded-lg border border-[#333] bg-[#121212] px-3 py-2 text-sm text-white" />
						</div>
						<div>
							<label for="verbosity" class="mb-1 block text-xs font-medium text-gray-400">Reasoning Verbosity</label>
							<select id="verbosity" bind:value={reasoningVerbosity} class="w-full rounded-lg border border-[#333] bg-[#121212] px-3 py-2 text-sm text-white">
								<option value="minimal">Minimal</option>
								<option value="standard">Standard</option>
								<option value="verbose">Verbose</option>
							</select>
						</div>
					</div>

					<div>
						<span class="mb-1 block text-xs font-medium text-gray-400">Asset Mode</span>
						<div class="flex gap-2">
							<button
								on:click={() => (assetMode = 'free_roam')}
								class="rounded-lg px-4 py-2 text-sm {assetMode === 'free_roam' ? 'bg-sky-600/20 border border-sky-500/30 text-sky-300' : 'border border-[#333] bg-[#121212] text-gray-400'}"
							>Default Pairs (BTC/ETH)</button>
							<button
								on:click={() => (assetMode = 'locked')}
								class="rounded-lg px-4 py-2 text-sm {assetMode === 'locked' ? 'bg-sky-600/20 border border-sky-500/30 text-sky-300' : 'border border-[#333] bg-[#121212] text-gray-400'}"
							>Locked Pairs</button>
						</div>
						{#if assetMode === 'free_roam'}
							<p class="mt-1 text-xs text-gray-600">Observes only BTC/USDT and ETH/USDT — this is NOT a market-wide scan. Use Locked Pairs to pick specific symbols.</p>
						{/if}
					</div>

					{#if assetMode === 'locked'}
						<div>
							<label for="pairs" class="mb-1 block text-xs font-medium text-gray-400">Locked Pairs (comma-separated)</label>
							<input id="pairs" bind:value={lockedPairsText} class="w-full rounded-lg border border-[#333] bg-[#121212] px-3 py-2 text-sm text-white" placeholder="BTC/USDT, ETH/USDT, SOL/USDT" />
						</div>
					{/if}

					<!-- Session Hours -->
					<div class="rounded-lg border border-[#2a2a2a] bg-[#121212] p-4">
						<label class="flex items-center gap-2 text-xs font-medium text-gray-300">
							<input type="checkbox" bind:checked={sessionHoursEnabled} class="h-3.5 w-3.5 rounded border-[#333] bg-[#1a1a1a]" />
							Restrict to session hours
						</label>
						<p class="mt-1 text-xs text-gray-600">When off, the bot is always active. When on, it only trades within the window below.</p>

						{#if sessionHoursEnabled}
							<div class="mt-4 space-y-4">
								<div class="grid grid-cols-2 gap-4">
									<div>
										<label for="sessionTz" class="mb-1 block text-xs font-medium text-gray-400">Timezone</label>
										<input id="sessionTz" bind:value={sessionTimezone} placeholder="America/New_York" class="w-full rounded-lg border border-[#333] bg-[#1a1a1a] px-3 py-2 text-sm text-white" />
									</div>
									<div class="grid grid-cols-2 gap-4">
										<div>
											<label for="sessionStart" class="mb-1 block text-xs font-medium text-gray-400">Start (HH:MM)</label>
											<input id="sessionStart" type="time" bind:value={sessionStart} class="w-full rounded-lg border border-[#333] bg-[#1a1a1a] px-3 py-2 text-sm text-white" />
										</div>
										<div>
											<label for="sessionEnd" class="mb-1 block text-xs font-medium text-gray-400">End (HH:MM)</label>
											<input id="sessionEnd" type="time" bind:value={sessionEnd} class="w-full rounded-lg border border-[#333] bg-[#1a1a1a] px-3 py-2 text-sm text-white" />
										</div>
									</div>
								</div>
								<div>
									<span class="mb-1 block text-xs font-medium text-gray-400">Active Days</span>
									<div class="flex flex-wrap gap-2">
										{#each WEEKDAYS as day}
											<button
												type="button"
												on:click={() => toggleSessionDay(day)}
												class="rounded-lg px-3 py-1.5 text-xs capitalize {sessionDays.includes(day) ? 'bg-sky-600/20 border border-sky-500/30 text-sky-300' : 'border border-[#333] bg-[#1a1a1a] text-gray-400'}"
											>{day.slice(0, 3)}</button>
										{/each}
									</div>
									<p class="mt-1 text-xs text-gray-600">An end earlier than start is treated as an overnight window.</p>
								</div>
							</div>
						{/if}
					</div>
				</div>
			{:else if activeTab === 'advanced'}
				<div class="space-y-4">
					<div class="grid grid-cols-2 gap-4">
						<div>
							<label for="llmCap" class="mb-1 block text-xs font-medium text-gray-400">Daily LLM Call Cap</label>
							<input id="llmCap" type="number" bind:value={maxLlmCallsPerDay} min="1" step="1" class="w-full rounded-lg border border-[#333] bg-[#121212] px-3 py-2 text-sm text-white" />
						</div>
						<div>
							<label for="maxErrors" class="mb-1 block text-xs font-medium text-gray-400">Circuit Breaker (max errors)</label>
							<input id="maxErrors" type="number" bind:value={maxConsecutiveErrors} min="1" step="1" class="w-full rounded-lg border border-[#333] bg-[#121212] px-3 py-2 text-sm text-white" />
						</div>
					</div>
				</div>
			{/if}
		</div>
	{/if}
</div>
