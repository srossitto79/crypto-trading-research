<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import {
		getBot,
		getBotTrades,
		getBotDecisions,
		getBotVersions,
		getBotMemory,
		getBotPositions,
		startBot,
		stopBot,
		type BotConfig,
		type BotTrade,
		type BotDecision,
		type BotConfigVersion,
		type BotMemoryEntry,
		type BotPositionsSnapshot,
	} from '$lib/api';
	// getBotStats / BotStats are imported directly from the module (the barrel
	// re-export is owned by a sibling change). Assumes the agreed signature
	// getBotStats(id): Promise<BotStats>.
	import {
		getBotStats,
		diffBotVersions,
		type BotStats,
		type BotVersionDiff,
	} from '$lib/api/bot_factory';

	let bot: BotConfig | null = null;
	let trades: BotTrade[] = [];
	let decisions: BotDecision[] = [];
	let versions: BotConfigVersion[] = [];
	let memoryEntries: BotMemoryEntry[] = [];
	let positions: BotPositionsSnapshot | null = null;
	let stats: BotStats | null = null;
	let loading = true;
	let error: string | null = null;
	let actionMsg: string | null = null;
	let confirmStop = false;
	let activeView: 'activity' | 'positions' | 'trades' | 'versions' | 'memory' = 'activity';
	let pollInterval: ReturnType<typeof setInterval> | null = null;

	// Live price tracking
	let livePrices: Record<string, number> = {};
	let priceWs: WebSocket | null = null;
	let pnlTick = 0; // Incremented every second to force reactivity

	function connectPriceWs(symbols: string[]) {
		if (priceWs) { priceWs.close(); priceWs = null; }
		if (!symbols.length) return;

		// Binance WS streams — convert BTC/USDT → btcusdt@trade
		const streams = [...new Set(symbols.map(s => s.replace('/', '').toLowerCase() + '@trade'))];
		const url = `wss://stream.binance.com:9443/stream?streams=${streams.join('/')}`;

		try {
			priceWs = new WebSocket(url);
			priceWs.onmessage = (event) => {
				try {
					const msg = JSON.parse(event.data);
					if (msg.data?.s && msg.data?.p) {
						// Convert BTCUSDT back to BTC/USDT
						const raw = msg.data.s as string;
						const sym = raw.endsWith('USDT') ? raw.slice(0, -4) + '/USDT' : raw;
						livePrices[sym] = parseFloat(msg.data.p);
					}
				} catch {}
			};
			priceWs.onerror = () => {};
			priceWs.onclose = () => { priceWs = null; };
		} catch {}
	}

	function getLivePrice(symbol: string): number | null {
		return livePrices[symbol] ?? null;
	}

	function computePnl(trade: BotTrade): { pnl: number; pnlPct: number } | null {
		// Force reactivity on pnlTick
		void pnlTick;
		if (trade.status !== 'OPEN') return trade.pnl != null ? { pnl: trade.pnl, pnlPct: trade.pnl_pct ?? 0 } : null;
		const current = getLivePrice(trade.symbol || trade.asset);
		if (!current || !trade.entry_price) return null;
		const size = trade.size || 1;
		const pnl = trade.direction === 'long'
			? (current - trade.entry_price) * size
			: (trade.entry_price - current) * size;
		const pnlPct = trade.direction === 'long'
			? ((current - trade.entry_price) / trade.entry_price) * 100
			: ((trade.entry_price - current) / trade.entry_price) * 100;
		return { pnl, pnlPct };
	}

	$: botId = $page.params.id;
	$: statusLabel = bot?.runtime_status || bot?.status || 'unknown';
	$: isRunning = statusLabel === 'running';

	// Live unrealized P&L summed across currently-open positions, using live WS
	// marks where available. Positions with no live mark contribute 0 (we don't
	// fabricate a mark from the entry price).
	$: unrealizedPnl = (() => {
		void pnlTick;
		if (!positions) return 0;
		return positions.open_positions.reduce((sum, pos) => {
			const current = getLivePrice(pos.ticker);
			if (current == null || !pos.entry_price) return sum;
			const delta = pos.direction === 'long'
				? (current - pos.entry_price)
				: (pos.entry_price - current);
			return sum + delta * pos.qty;
		}, 0);
	})();

	// Drawdown gauge mirrors the backend's session-scoped enforcement: it works
	// off the positions snapshot's peak_equity and realized_pnl (which already
	// reset when the bot starts), plus live unrealized when a mark is available.
	// If peak_equity is null (no peak recorded yet), fall back to starting
	// capital as the peak.
	$: currentEquity = positions
		? positions.starting_capital + positions.realized_pnl + unrealizedPnl
		: (bot?.capital_allocation ?? 0);
	$: peakEquity = positions
		? (positions.peak_equity ?? positions.starting_capital)
		: (bot?.capital_allocation ?? 0);
	$: drawdownPct = peakEquity > 0
		? Math.max(0, ((peakEquity - currentEquity) / peakEquity) * 100)
		: 0;
	$: drawdownColor = (() => {
		const limit = bot?.max_drawdown_pct ?? 3;
		if (drawdownPct >= limit) return 'text-rose-400';
		if (drawdownPct >= limit * 0.5) return 'text-amber-400';
		return 'text-white';
	})();

	function statusColor(s: string): string {
		if (s === 'running') return 'text-emerald-400';
		if (s === 'error') return 'text-rose-400';
		if (s === 'paused') return 'text-amber-400';
		return 'text-gray-500';
	}

	async function load() {
		const currentBotId = botId;
		if (!currentBotId) {
			error = 'Missing bot id';
			loading = false;
			return;
		}

		try {
			[bot, trades, decisions, versions, memoryEntries, positions, stats] = await Promise.all([
				getBot(currentBotId),
				getBotTrades(currentBotId, 50),
				getBotDecisions(currentBotId, 100),
				getBotVersions(currentBotId),
				getBotMemory(currentBotId, 50).catch(() => []),
				getBotPositions(currentBotId).catch(() => null),
				getBotStats(currentBotId).catch(() => null),
			]);
			error = null;
		} catch (e: any) {
			error = e.message || 'Failed to load bot';
		} finally {
			loading = false;
		}
	}

	async function handleStart() {
		if (!botId) {
			actionMsg = 'Error: Missing bot id';
			return;
		}

		try {
			await startBot(botId);
			actionMsg = 'Bot started';
			await load();
		} catch (e: any) {
			actionMsg = `Error: ${e.message}`;
		}
	}

	async function handleStop() {
		confirmStop = false;
		if (!botId) {
			actionMsg = 'Error: Missing bot id';
			return;
		}

		try {
			await stopBot(botId);
			actionMsg = 'Bot stopped';
			await load();
		} catch (e: any) {
			actionMsg = `Error: ${e.message}`;
		}
	}

	function actionTypeIcon(type: string): string {
		if (type === 'trade') return '💰';
		if (type === 'observation') return '👁';
		if (type === 'pass') return '➖';
		if (type === 'error') return '⚠️';
		if (type === 'paused') return '⏸';
		return '•';
	}

	function actionTypeColor(type: string): string {
		if (type === 'trade') return 'text-emerald-300';
		if (type === 'observation') return 'text-sky-300';
		if (type === 'pass') return 'text-gray-400';
		if (type === 'error') return 'text-rose-300';
		if (type === 'paused') return 'text-amber-300';
		return 'text-white';
	}

	// BUY/SELL/SHORT/COVER from a trade decision's action_data, if present.
	function tradeAction(d: BotDecision): string | null {
		const a = d.action_data?.action;
		return typeof a === 'string' ? a.toUpperCase() : null;
	}

	function tradeDirection(d: BotDecision): string | null {
		const dir = d.action_data?.direction;
		return typeof dir === 'string' ? dir : null;
	}

	function actionBadgeColor(action: string): string {
		if (action === 'BUY' || action === 'COVER') return 'bg-emerald-500/10 text-emerald-400';
		if (action === 'SELL' || action === 'SHORT') return 'bg-rose-500/10 text-rose-400';
		return 'bg-gray-500/10 text-gray-400';
	}

	function formatTime(ts: string | null): string {
		if (!ts) return '';
		try {
			return new Date(ts).toLocaleString();
		} catch {
			return ts;
		}
	}

	function relativeTime(ts: string | null): string {
		if (!ts) return '';
		try {
			const diff = Date.now() - new Date(ts).getTime();
			const mins = Math.floor(diff / 60000);
			if (mins < 1) return 'just now';
			if (mins < 60) return `${mins}m ago`;
			const hours = Math.floor(mins / 60);
			if (hours < 24) return `${hours}h ago`;
			return `${Math.floor(hours / 24)}d ago`;
		} catch {
			return '';
		}
	}

	// ── Config-version diff (Versions tab) ────────────────────────────
	// The diff endpoint can return either a BotVersionDiff or, when a version
	// id is unknown, an error shape { error, available }. We model both.
	type VersionDiffError = { error: string; available: number[] };
	let compareV1: number | null = null;
	let compareV2: number | null = null;
	let versionDiff: BotVersionDiff | null = null;
	let versionDiffError: string | null = null;
	let diffLoading = false;
	let expandedVersion: number | null = null;

	function isDiffError(d: BotVersionDiff | VersionDiffError): d is VersionDiffError {
		return (d as VersionDiffError).error !== undefined;
	}

	// Pretty/compact stringification of an arbitrary snapshot value for the
	// diff table. null/undefined collapse to an em dash; objects/arrays are
	// compact-JSON; everything else is its string form.
	function formatValue(v: unknown): string {
		if (v === null || v === undefined) return '—';
		if (typeof v === 'object') {
			try {
				return JSON.stringify(v);
			} catch {
				return String(v);
			}
		}
		return String(v);
	}

	function prettySnapshot(snapshot: Record<string, unknown>): string {
		try {
			return JSON.stringify(snapshot, null, 2);
		} catch {
			return String(snapshot);
		}
	}

	function toggleVersionExpand(version: number) {
		expandedVersion = expandedVersion === version ? null : version;
	}

	async function runVersionDiff() {
		const id = botId;
		if (!id || compareV1 == null || compareV2 == null) return;
		diffLoading = true;
		versionDiff = null;
		versionDiffError = null;
		try {
			const result = (await diffBotVersions(id, compareV1, compareV2)) as
				| BotVersionDiff
				| VersionDiffError;
			if (isDiffError(result)) {
				const avail = result.available?.length ? ` Available: ${result.available.join(', ')}.` : '';
				versionDiffError = `${result.error}.${avail}`;
			} else {
				versionDiff = result;
			}
		} catch (e: any) {
			versionDiffError = e?.message || 'Failed to compare versions';
		} finally {
			diffLoading = false;
		}
	}

	// Seed the two selectors with the two most recent versions whenever the
	// version list changes (and the user hasn't already picked something).
	// Versions arrive newest-first from the API.
	$: if (versions.length >= 2 && compareV1 == null && compareV2 == null) {
		compareV1 = versions[1].version; // older of the two most recent
		compareV2 = versions[0].version; // newest
	}

	let pnlInterval: ReturnType<typeof setInterval> | null = null;

	onMount(() => {
		load();
		pollInterval = setInterval(load, 5000);
		// Tick P&L every second for live updates
		pnlInterval = setInterval(() => { pnlTick++; }, 1000);
	});

	onDestroy(() => {
		if (pollInterval) clearInterval(pollInterval);
		if (pnlInterval) clearInterval(pnlInterval);
		if (priceWs) { priceWs.close(); priceWs = null; }
	});

	// Connect price WS for every symbol we currently hold — both open trades and
	// open positions (the positions snapshot is the source of truth for live
	// marks now that the backend no longer echoes a fake current_price).
	$: {
		const tradeSymbols = trades.filter(t => t.status === 'OPEN').map(t => t.symbol || t.asset);
		const positionSymbols = positions?.open_positions.map(p => p.ticker) ?? [];
		const openSymbols = [...new Set([...tradeSymbols, ...positionSymbols].filter(Boolean))];
		if (openSymbols.length > 0) {
			connectPriceWs(openSymbols);
		}
	}
</script>

<svelte:head>
	<title>{bot?.name || 'Bot'} | Bot Factory | Axiom</title>
</svelte:head>

<div class="mx-auto max-w-6xl px-4 py-6">
	{#if loading}
		<div class="py-20 text-center text-gray-500">Loading...</div>
	{:else if error || !bot}
		<div class="py-20 text-center text-rose-400">{error || 'Bot not found'}</div>
	{:else}
		<!-- Header -->
		<div class="mb-6">
			<button on:click={() => goto('/bot-factory')} class="mb-2 text-sm text-gray-500 hover:text-gray-300">&larr; Back to Bot Factory</button>
			<div class="flex items-start justify-between">
				<div>
					<h1 class="text-2xl font-bold text-white">{bot.name}</h1>
					<div class="mt-1 flex items-center gap-3 text-sm text-gray-400">
						<span>{bot.model}</span>
						<span class="inline-block h-1 w-1 rounded-full bg-gray-600"></span>
						<span class="{statusColor(statusLabel)} font-medium">{statusLabel}</span>
						{#if bot.started_at && isRunning}
							<span class="inline-block h-1 w-1 rounded-full bg-gray-600"></span>
							<span>started {relativeTime(bot.started_at)}</span>
						{/if}
					</div>
				</div>
				<div class="flex gap-2">
					{#if isRunning}
						{#if confirmStop}
							<button on:click={handleStop} class="rounded-lg bg-rose-600/20 border border-rose-500/30 px-4 py-2 text-sm text-rose-300">Confirm Stop</button>
							<button on:click={() => (confirmStop = false)} class="rounded-lg border border-[#333] px-4 py-2 text-sm text-gray-400">Cancel</button>
						{:else}
							<button on:click={() => (confirmStop = true)} class="rounded-lg border border-rose-500/30 bg-rose-500/10 px-4 py-2 text-sm text-rose-300 hover:bg-rose-500/20">Stop</button>
						{/if}
					{:else}
						<button on:click={handleStart} class="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-500">Start</button>
					{/if}
					<button on:click={() => goto(`/bot-factory/editor?id=${bot?.id ?? ''}`)} class="rounded-lg border border-[#333] px-4 py-2 text-sm text-gray-300 hover:bg-[#222]">Edit</button>
				</div>
			</div>
		</div>

		{#if actionMsg}
			<div class="mb-4 rounded-lg border border-sky-500/20 bg-sky-500/5 p-3 text-sm text-sky-300">
				{actionMsg}
				<button on:click={() => (actionMsg = null)} class="ml-2 text-sky-400">dismiss</button>
			</div>
		{/if}

		{#if bot.error_message && !isRunning}
			<div class="mb-4 rounded-lg border border-rose-500/20 bg-rose-500/5 p-3 text-sm text-rose-300">
				{bot.error_message}
			</div>
		{/if}

		<!-- Stats bar -->
		<div class="mb-6 grid grid-cols-2 gap-3 md:grid-cols-5">
			<div class="rounded-xl border border-[#2a2a2a] bg-[#1a1a1a] p-4 text-center">
				<div class="text-xs text-gray-500">Capital</div>
				<div class="mt-1 text-lg font-semibold text-white">${bot.capital_allocation?.toLocaleString()}</div>
			</div>
			<div class="rounded-xl border border-[#2a2a2a] bg-[#1a1a1a] p-4 text-center">
				<div class="text-xs text-gray-500">Max Position</div>
				<div class="mt-1 text-lg font-semibold text-white">{bot.max_position_pct}%</div>
			</div>
			<div class="rounded-xl border border-[#2a2a2a] bg-[#1a1a1a] p-4 text-center">
				<div class="text-xs text-gray-500">Drawdown</div>
				<div class="mt-1 text-lg font-semibold {drawdownColor}">{drawdownPct.toFixed(2)}% / {bot.max_drawdown_pct}%</div>
			</div>
			<div class="rounded-xl border border-[#2a2a2a] bg-[#1a1a1a] p-4 text-center">
				<div class="text-xs text-gray-500">LLM Calls Today</div>
				<div class="mt-1 text-lg font-semibold text-white">{bot.llm_calls_today ?? 0} / {bot.max_llm_calls_per_day}</div>
			</div>
			<div class="rounded-xl border border-[#2a2a2a] bg-[#1a1a1a] p-4 text-center">
				<div class="text-xs text-gray-500">Consecutive Errors</div>
				<div class="mt-1 text-lg font-semibold {(bot.consecutive_errors || 0) > 0 ? 'text-amber-400' : 'text-white'}">{bot.consecutive_errors ?? 0} / {bot.max_consecutive_errors}</div>
			</div>
		</div>

		<!-- Headline trade summary — sourced from getBotStats (ALL trades, not the
		     last-N slice). -->
		{#if stats}
			<div class="mb-6 flex flex-wrap items-center gap-x-6 gap-y-2 rounded-xl border border-[#2a2a2a] bg-[#1a1a1a] px-4 py-3 text-sm">
				<div class="flex items-center gap-1.5">
					<span class="text-xs text-gray-500">Total trades</span>
					<span class="font-semibold text-white">{stats.total}</span>
					<span class="text-xs text-gray-500">({stats.open_count} open)</span>
				</div>
				<span class="h-4 w-px bg-[#333]"></span>
				<div class="flex items-center gap-1.5">
					<span class="text-xs text-gray-500">Win rate</span>
					<span class="font-semibold text-white">
						{(stats.wins + stats.losses) > 0 ? `${(stats.win_rate * 100).toFixed(0)}%` : '—'}
					</span>
					<span class="text-xs text-gray-500">({stats.wins}W / {stats.losses}L)</span>
				</div>
				<span class="h-4 w-px bg-[#333]"></span>
				<div class="flex items-center gap-1.5">
					<span class="text-xs text-gray-500">Total P&L</span>
					<span class="font-semibold {stats.total_pnl_usd >= 0 ? 'text-emerald-400' : 'text-rose-400'}">
						{stats.total_pnl_usd >= 0 ? '+' : ''}${stats.total_pnl_usd.toFixed(2)}
					</span>
				</div>
			</div>
		{/if}

		<!-- View tabs -->
		<div class="mb-4 flex gap-1 rounded-lg border border-[#2a2a2a] bg-[#121212] p-1">
			{#each [['activity', 'Activity Feed'], ['positions', 'Open Positions'], ['trades', 'Trade History'], ['memory', 'Memory'], ['versions', 'Config Versions']] as [key, label]}
				<button
					on:click={() => (activeView = key as typeof activeView)}
					class="flex-1 rounded-md px-3 py-1.5 text-sm transition {activeView === key ? 'bg-[#2a2a2a] text-white font-medium' : 'text-gray-500 hover:text-gray-300'}"
				>
					{label}
				</button>
			{/each}
		</div>

		<!-- View content -->
		<div class="rounded-xl border border-[#2a2a2a] bg-[#1a1a1a]">
			{#if activeView === 'activity'}
				{#if decisions.length === 0}
					<div class="p-8 text-center text-sm text-gray-500">
						No decisions yet. {isRunning ? 'Waiting for market events...' : 'Start the bot to begin trading.'}
					</div>
				{:else}
					<div class="divide-y divide-[#222]">
						{#each decisions as decision}
							{@const action = decision.action_type === 'trade' ? tradeAction(decision) : null}
							{@const direction = decision.action_type === 'trade' ? tradeDirection(decision) : null}
							<div class="p-4 {decision.action_type === 'error' ? 'border-l-2 border-rose-500/60 bg-rose-500/5' : ''}">
								<div class="flex items-center justify-between">
									<div class="flex flex-wrap items-center gap-2">
										<span class="text-base">{actionTypeIcon(decision.action_type)}</span>
										<span class="text-sm font-medium capitalize {actionTypeColor(decision.action_type)}">{decision.action_type}</span>
										{#if action}
											<span class="rounded px-1.5 py-0.5 text-xs font-medium {actionBadgeColor(action)}">{action}</span>
										{/if}
										{#if direction}
											<span class="rounded px-1.5 py-0.5 text-xs font-medium {direction === 'long' ? 'bg-emerald-500/10 text-emerald-400' : 'bg-rose-500/10 text-rose-400'}">{direction}</span>
										{/if}
									</div>
									<span class="text-xs text-gray-500">{formatTime(decision.timestamp)}</span>
								</div>
								{#if decision.reasoning}
									<p class="mt-2 text-sm leading-relaxed {decision.action_type === 'error' ? 'text-rose-200' : 'text-gray-300'}">{decision.reasoning}</p>
								{/if}
								{#if decision.action_data && (decision.action_type === 'trade' || decision.action_type === 'error')}
									<div class="mt-2 overflow-x-auto rounded-lg bg-[#121212] p-2 text-xs {decision.action_type === 'error' ? 'text-rose-300' : 'text-gray-400'} font-mono whitespace-pre">{JSON.stringify(decision.action_data, null, 2)}</div>
								{/if}
							</div>
						{/each}
					</div>
				{/if}
			{:else if activeView === 'positions'}
				{#if !positions || positions.open_positions.length === 0}
					<div class="p-8 text-center text-sm text-gray-500">
						No open positions. {isRunning ? 'The bot will open positions when it finds a setup.' : 'Start the bot to begin trading.'}
					</div>
				{:else}
					{@const equity = positions.starting_capital + positions.realized_pnl}
					<div class="flex flex-wrap items-center gap-4 border-b border-[#222] px-4 py-3 text-xs">
						<div class="flex items-center gap-1.5">
							<span class="text-gray-500">Realized P&L</span>
							<span class="font-semibold {positions.realized_pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'}">
								{positions.realized_pnl >= 0 ? '+' : ''}${positions.realized_pnl.toFixed(2)}
							</span>
						</div>
						<span class="h-4 w-px bg-[#333]"></span>
						<div class="flex items-center gap-1.5">
							<span class="text-gray-500">Equity (realized)</span>
							<span class="font-semibold text-white">${equity.toFixed(2)}</span>
						</div>
						{#if positions.peak_equity}
							<span class="h-4 w-px bg-[#333]"></span>
							<div class="flex items-center gap-1.5">
								<span class="text-gray-500">Peak equity</span>
								<span class="font-semibold text-white">${positions.peak_equity.toFixed(2)}</span>
							</div>
						{/if}
						<div class="ml-auto text-gray-500">{positions.open_positions.length} open</div>
					</div>
					<div class="overflow-x-auto">
						<table class="w-full text-sm">
							<thead>
								<tr class="border-b border-[#222] text-left text-xs text-gray-500">
									<th class="px-4 py-3">Trade</th>
									<th class="px-4 py-3">Pair</th>
									<th class="px-4 py-3">Direction</th>
									<th class="px-4 py-3">Qty</th>
									<th class="px-4 py-3">Entry</th>
									<th class="px-4 py-3">Current</th>
									<th class="px-4 py-3">Unrealized</th>
									<th class="px-4 py-3">Stop Loss</th>
									<th class="px-4 py-3">Take Profit</th>
									<th class="px-4 py-3">Opened</th>
								</tr>
							</thead>
							<tbody>
								{#each positions.open_positions as pos}
									{@const liveCur = pnlTick >= 0 ? getLivePrice(pos.ticker) : null}
									{@const upnl = (liveCur != null && pos.entry_price)
										? (pos.direction === 'long' ? (liveCur - pos.entry_price) : (pos.entry_price - liveCur)) * pos.qty
										: null}
									<tr class="border-b border-[#1a1a1a] hover:bg-[#222]">
										<td class="px-4 py-3 font-mono text-xs text-gray-400">{pos.trade_id}</td>
										<td class="px-4 py-3 text-white">{pos.ticker}</td>
										<td class="px-4 py-3">
											<span class="rounded px-1.5 py-0.5 text-xs font-medium {pos.direction === 'long' ? 'bg-emerald-500/10 text-emerald-400' : 'bg-rose-500/10 text-rose-400'}">{pos.direction}</span>
										</td>
										<td class="px-4 py-3 text-gray-300">{pos.qty}</td>
										<td class="px-4 py-3 text-gray-300">${pos.entry_price?.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2}) ?? '—'}</td>
										<td class="px-4 py-3 text-white font-medium">
											{#if liveCur != null}
												${liveCur.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}
											{:else}
												<span class="text-gray-500" title="No live price feed">—</span>
											{/if}
										</td>
										<td class="px-4 py-3 font-medium {upnl == null ? '' : upnl >= 0 ? 'text-emerald-400' : 'text-rose-400'}">
											{#if upnl != null}
												{upnl >= 0 ? '+' : ''}${upnl.toFixed(2)}
											{:else}
												<span class="text-gray-500" title="No live price to mark against">—</span>
											{/if}
										</td>
										<td class="px-4 py-3 {pos.stop_loss_price ? 'text-rose-300' : 'text-gray-600'}">
											{pos.stop_loss_price ? `$${pos.stop_loss_price.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}` : 'none'}
										</td>
										<td class="px-4 py-3 {pos.take_profit_price ? 'text-emerald-300' : 'text-gray-600'}">
											{pos.take_profit_price ? `$${pos.take_profit_price.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}` : 'none'}
										</td>
										<td class="px-4 py-3 text-xs text-gray-500">{formatTime(pos.opened_at)}</td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				{/if}
			{:else if activeView === 'trades'}
				{#if trades.length === 0}
					<div class="p-8 text-center text-sm text-gray-500">No trades yet.</div>
				{:else}
					<!-- Overall P&L Summary — aggregate figures come from getBotStats
					     (ALL trades), not the last-N slice below. Unrealized is the only
					     value derived from the live slice since it needs live marks. -->
					{@const openTrades = trades.filter(t => t.status === 'OPEN')}
					{@const openPnl = openTrades.reduce((sum, t) => {
						const live = computePnl(t);
						return sum + (live?.pnl ?? 0);
					}, 0)}
					{@const totalPnl = stats ? stats.total_pnl_usd : trades.filter(t => t.status !== 'OPEN').reduce((s, t) => s + (t.pnl ?? 0), 0)}
					{@const wins = stats?.wins ?? 0}
					{@const losses = stats?.losses ?? 0}
					{@const winRate = stats ? stats.win_rate * 100 : 0}
					{@const totalTrades = stats?.total ?? trades.length}
					<div class="flex flex-wrap items-center gap-4 border-b border-[#222] px-4 py-3">
						<div class="flex items-center gap-1.5">
							<span class="text-xs text-gray-500">Total P&L</span>
							<span class="text-sm font-semibold {totalPnl >= 0 ? 'text-emerald-400' : 'text-rose-400'}">
								{totalPnl >= 0 ? '+' : ''}${totalPnl.toFixed(2)}
							</span>
						</div>
						{#if openTrades.length > 0}
							<span class="h-4 w-px bg-[#333]"></span>
							<div class="flex items-center gap-1.5">
								<span class="text-xs text-gray-500">Unrealized</span>
								<span class="text-sm font-semibold {openPnl >= 0 ? 'text-emerald-400' : 'text-rose-400'}">
									{openPnl >= 0 ? '+' : ''}${openPnl.toFixed(2)}
								</span>
							</div>
						{/if}
						{#if stats}
							<span class="h-4 w-px bg-[#333]"></span>
							<div class="flex items-center gap-1.5">
								<span class="text-xs text-gray-500">Best</span>
								<span class="text-sm font-semibold text-emerald-400">{stats.best_pnl_usd >= 0 ? '+' : ''}${stats.best_pnl_usd.toFixed(2)}</span>
							</div>
							<span class="h-4 w-px bg-[#333]"></span>
							<div class="flex items-center gap-1.5">
								<span class="text-xs text-gray-500">Worst</span>
								<span class="text-sm font-semibold text-rose-400">${stats.worst_pnl_usd.toFixed(2)}</span>
							</div>
						{/if}
						<span class="h-4 w-px bg-[#333]"></span>
						<div class="flex items-center gap-1.5 text-xs text-gray-500">
							<span class="text-emerald-400">{wins}W</span>
							<span>/</span>
							<span class="text-rose-400">{losses}L</span>
							{#if stats && (stats.wins + stats.losses) > 0}
								<span class="text-gray-400">({winRate.toFixed(0)}%)</span>
							{/if}
						</div>
						<div class="ml-auto text-xs text-gray-500">{totalTrades} trade{totalTrades !== 1 ? 's' : ''}{stats ? ` · ${stats.open_count} open` : ''}</div>
					</div>
					<div class="px-4 pt-3 text-xs uppercase tracking-wide text-gray-600">Recent (last {trades.length})</div>
					<div class="overflow-x-auto">
						<table class="w-full text-sm">
							<thead>
								<tr class="border-b border-[#222] text-left text-xs text-gray-500">
									<th class="px-4 py-3">ID</th>
									<th class="px-4 py-3">Pair</th>
									<th class="px-4 py-3">Direction</th>
									<th class="px-4 py-3">Size</th>
									<th class="px-4 py-3">Entry</th>
									<th class="px-4 py-3">Current</th>
									<th class="px-4 py-3">Exit</th>
									<th class="px-4 py-3">P&L</th>
									<th class="px-4 py-3">Status</th>
									<th class="px-4 py-3">Opened</th>
								</tr>
							</thead>
							<tbody>
								{#each trades as trade}
									{@const live = computePnl(trade)}
									{@const currentPrice = getLivePrice(trade.symbol || trade.asset)}
									<tr class="border-b border-[#1a1a1a] hover:bg-[#222]">
										<td class="px-4 py-3 font-mono text-xs text-gray-400">{trade.id}</td>
										<td class="px-4 py-3 text-white">{trade.symbol || trade.asset}</td>
										<td class="px-4 py-3">
											<span class="rounded px-1.5 py-0.5 text-xs font-medium {trade.direction === 'long' ? 'bg-emerald-500/10 text-emerald-400' : 'bg-rose-500/10 text-rose-400'}">
												{trade.direction}
											</span>
										</td>
										<td class="px-4 py-3 text-gray-300">{trade.size}</td>
										<td class="px-4 py-3 text-gray-300">${trade.entry_price?.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2}) ?? '—'}</td>
										<td class="px-4 py-3 text-white font-medium">
											{#if trade.status === 'OPEN' && currentPrice}
												${currentPrice.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}
											{:else}
												<span class="text-gray-500">—</span>
											{/if}
										</td>
										<td class="px-4 py-3 text-gray-300">{trade.exit_price ? `$${trade.exit_price.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}` : '—'}</td>
										<td class="px-4 py-3 font-medium {live && live.pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'}">
											{#if live}
												${live.pnl.toFixed(2)} ({live.pnlPct > 0 ? '+' : ''}{live.pnlPct.toFixed(2)}%)
											{:else}
												<span class="text-gray-500">—</span>
											{/if}
										</td>
										<td class="px-4 py-3">
											<span class="rounded px-1.5 py-0.5 text-xs {trade.status === 'OPEN' ? 'bg-sky-500/10 text-sky-400' : 'bg-gray-500/10 text-gray-400'}">
												{trade.status}
											</span>
										</td>
										<td class="px-4 py-3 text-xs text-gray-500">{formatTime(trade.opened_at)}</td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				{/if}
			{:else if activeView === 'memory'}
				{#if memoryEntries.length === 0}
					<div class="p-8 text-center text-sm text-gray-500">
						No memories yet. {isRunning ? "The bot will store observations and trade outcomes as it runs." : 'Start the bot to begin building memory.'}
					</div>
				{:else}
					<div class="divide-y divide-[#222]">
						{#each memoryEntries as entry}
							{@const meta = entry.metadata || {}}
							{@const entryType = (meta.type as string) || 'memory'}
							<div class="p-4">
								<div class="flex items-center justify-between">
									<div class="flex items-center gap-2">
										<span
											class="rounded px-1.5 py-0.5 text-xs font-medium
												{entryType === 'trade_outcome' && meta.outcome === 'win' ? 'bg-emerald-500/10 text-emerald-400' : ''}
												{entryType === 'trade_outcome' && meta.outcome === 'loss' ? 'bg-rose-500/10 text-rose-400' : ''}
												{entryType === 'trade_outcome' && meta.outcome === 'flat' ? 'bg-gray-500/10 text-gray-400' : ''}
												{entryType === 'trade_entry' ? 'bg-sky-500/10 text-sky-400' : ''}
												{entryType === 'observation' ? 'bg-amber-500/10 text-amber-400' : ''}
												{entryType === 'trade_exit' ? 'bg-gray-500/10 text-gray-400' : ''}
												{entryType === 'memory' ? 'bg-gray-500/10 text-gray-400' : ''}"
										>{entryType.replace('_', ' ')}</span>
										{#if meta.ticker}
											<span class="text-xs text-gray-400">{meta.ticker}</span>
										{/if}
										{#if entryType === 'trade_outcome' && typeof meta.pnl === 'number'}
											<span class="text-xs font-medium {meta.pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'}">
												{meta.pnl >= 0 ? '+' : ''}${meta.pnl.toFixed(2)}
												{#if typeof meta.pnl_pct === 'number'}
													({meta.pnl_pct >= 0 ? '+' : ''}{meta.pnl_pct.toFixed(2)}%)
												{/if}
											</span>
										{/if}
									</div>
									<span class="text-xs text-gray-500">{formatTime((meta.timestamp as string) || null)}</span>
								</div>
								<p class="mt-2 text-sm text-gray-300 leading-relaxed">{entry.text}</p>
							</div>
						{/each}
					</div>
				{/if}
			{:else if activeView === 'versions'}
				{#if versions.length === 0}
					<div class="p-8 text-center text-sm text-gray-500">No config changes recorded yet.</div>
				{:else}
					<!-- Compare controls -->
					<div class="border-b border-[#222] p-4">
						{#if versions.length < 2}
							<p class="text-xs text-gray-500">Record at least two config versions to compare changes.</p>
						{:else}
							<div class="flex flex-wrap items-end gap-3">
								<div class="flex flex-col gap-1">
									<label for="diff-v1" class="text-xs text-gray-500">From version</label>
									<select
										id="diff-v1"
										bind:value={compareV1}
										class="rounded-lg border border-[#333] bg-[#121212] px-3 py-1.5 text-sm text-white focus:border-sky-500/50 focus:outline-none"
									>
										{#each versions as v}
											<option value={v.version}>v{v.version} — {formatTime(v.created_at)}</option>
										{/each}
									</select>
								</div>
								<span class="pb-2 text-gray-600">→</span>
								<div class="flex flex-col gap-1">
									<label for="diff-v2" class="text-xs text-gray-500">To version</label>
									<select
										id="diff-v2"
										bind:value={compareV2}
										class="rounded-lg border border-[#333] bg-[#121212] px-3 py-1.5 text-sm text-white focus:border-sky-500/50 focus:outline-none"
									>
										{#each versions as v}
											<option value={v.version}>v{v.version} — {formatTime(v.created_at)}</option>
										{/each}
									</select>
								</div>
								<button
									on:click={runVersionDiff}
									disabled={diffLoading || compareV1 == null || compareV2 == null || compareV1 === compareV2}
									class="rounded-lg bg-sky-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-sky-500 disabled:cursor-not-allowed disabled:opacity-40"
								>
									{diffLoading ? 'Comparing…' : 'Compare'}
								</button>
							</div>
							{#if compareV1 != null && compareV2 != null && compareV1 === compareV2}
								<p class="mt-2 text-xs text-amber-400">Pick two different versions to compare.</p>
							{/if}

							<!-- Diff result -->
							{#if versionDiffError}
								<div class="mt-4 rounded-lg border border-amber-500/20 bg-amber-500/5 p-3 text-sm text-amber-300">
									{versionDiffError}
								</div>
							{:else if versionDiff}
								{@const changeEntries = Object.entries(versionDiff.changes)}
								<div class="mt-4">
									<div class="mb-2 text-xs uppercase tracking-wide text-gray-600">
										Changes from v{versionDiff.v1} to v{versionDiff.v2}
									</div>
									{#if changeEntries.length === 0}
										<p class="rounded-lg border border-[#222] bg-[#121212] p-3 text-sm text-gray-500">
											No differences between these versions.
										</p>
									{:else}
										<div class="overflow-x-auto rounded-lg border border-[#222]">
											<table class="w-full text-sm">
												<thead>
													<tr class="border-b border-[#222] text-left text-xs text-gray-500">
														<th class="px-4 py-2">Field</th>
														<th class="px-4 py-2">v{versionDiff.v1}</th>
														<th class="px-4 py-2">v{versionDiff.v2}</th>
													</tr>
												</thead>
												<tbody>
													{#each changeEntries as [field, change]}
														<tr class="border-b border-[#1a1a1a] last:border-b-0">
															<td class="px-4 py-2 font-mono text-xs text-gray-300">{field}</td>
															<td class="px-4 py-2 font-mono text-xs text-rose-300 break-all">{formatValue(change.v1)}</td>
															<td class="px-4 py-2 font-mono text-xs text-emerald-300 break-all">{formatValue(change.v2)}</td>
														</tr>
													{/each}
												</tbody>
											</table>
										</div>
									{/if}
								</div>
							{/if}
						{/if}
					</div>

					<!-- Version list (expandable to view full snapshot) -->
					<div class="divide-y divide-[#222]">
						{#each versions as version}
							<div class="p-4">
								<div class="flex items-center justify-between">
									<div class="flex items-center gap-3">
										<span class="text-sm font-medium text-white">Version {version.version}</span>
										<button
											on:click={() => toggleVersionExpand(version.version)}
											class="text-xs text-sky-400 hover:text-sky-300"
											aria-expanded={expandedVersion === version.version}
										>
											{expandedVersion === version.version ? 'Hide config' : 'View config'}
										</button>
									</div>
									<span class="text-xs text-gray-500">{formatTime(version.created_at)}</span>
								</div>
								{#if expandedVersion === version.version}
									<pre class="mt-3 overflow-x-auto rounded-lg bg-[#121212] p-3 text-xs text-gray-400 whitespace-pre">{prettySnapshot(version.config_snapshot)}</pre>
								{/if}
							</div>
						{/each}
					</div>
				{/if}
			{/if}
		</div>
	{/if}
</div>
