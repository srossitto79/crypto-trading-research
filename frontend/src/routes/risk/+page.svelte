<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import {
		getAxiomDashboard,
		getAxiomRisk,
		resetTradingHalt,
		type AxiomDashboardResponse,
		type AxiomRiskStatus,
	} from '$lib/api';
	import { triggerEmergencyHalt } from '$lib/api/axiom';
	import ErrorBanner from '$lib/components/ErrorBanner.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import { createRealtimeRefresh, type RealtimeRefreshController } from '$lib/utils/realtime';

	let dashboard: AxiomDashboardResponse | null = null;
	let risk: AxiomRiskStatus | null = null;
	let loading = true;
	let error = '';
	let actionMessage = '';
	let resetBusy = false;
	let haltBusy = false;
	let realtime: RealtimeRefreshController | null = null;

	$: limits = risk?.limits ?? {};
	$: portfolio = risk?.portfolio ?? {};
	$: groups = portfolio?.groups ?? {};
	$: accountValue = Number(dashboard?.account?.accountValue ?? dashboard?.daily_risk?.current_equity ?? 0);
	$: highWaterMark = Number(risk?.high_water_mark ?? dashboard?.risk?.high_water_mark ?? 0);
	$: dailyStartEquity = Number(risk?.daily_start_equity ?? dashboard?.daily_risk?.start_equity ?? 0);
	$: currentDrawdown = highWaterMark > 0 ? Math.max(0, (highWaterMark - accountValue) / highWaterMark) : 0;
	$: dailyLoss = dailyStartEquity > 0 ? Math.max(0, (dailyStartEquity - accountValue) / dailyStartEquity) : 0;
	$: portfolioRisk = Number(portfolio?.total_net_risk ?? 0);
	// Largest single-trade risk currently committed across open positions. Backed by
	// the additive `current_per_trade_risk` field on get_risk_status (display-only).
	$: perTradeRisk = Number(risk?.current_per_trade_risk ?? 0);
	$: tradingAllowed = dashboard?.trading_allowed ?? true;
	$: tradingReason = dashboard?.trading_reason || 'OK';
	$: killSwitchActive = Boolean(risk?.kill_switch_active || dashboard?.risk?.kill_switch_active);
	$: dailyLossHalt = Boolean(risk?.daily_loss_halt || dashboard?.risk?.daily_loss_halt);
	$: systemPaused = Boolean(dashboard?.paused);
	$: haltBannerTitle = killSwitchActive
		? 'Kill Switch Active'
		: dailyLossHalt
			? 'Daily Loss Halt Active'
			: systemPaused
				? 'System Paused'
				: 'Trading Halted';
	$: dailyPnlUsd = dailyStartEquity > 0 ? accountValue - dailyStartEquity : 0;

	$: recovery = dashboard?.recovery ?? null;
	$: recoveryActive = Boolean(recovery?.active || risk?.recovery_active);
	$: recoverySummary = recovery?.summary || risk?.recovery_summary || '';
	$: recoveryRequiresOperator = Boolean(recovery?.requires_operator);

	$: circuitBreakers = dashboard?.circuit_breakers
		? [
				{ label: 'Price Feed', state: dashboard.circuit_breakers.hl_price },
				{ label: 'Trade', state: dashboard.circuit_breakers.hl_trade },
				{ label: 'Account', state: dashboard.circuit_breakers.hl_account },
			].filter((cb) => cb.state)
		: [];

	// Distinguish "no telemetry yet" from a genuine all-zero/safe reading. Both
	// upstream calls populate `dashboard`/`risk`; if neither resolved we have no data.
	$: hasRiskData = dashboard !== null || risk !== null;

	$: gauges = [
		{ label: 'Drawdown', value: currentDrawdown, max: Number(limits.max_drawdown ?? 0.1) },
		{ label: 'Daily Loss', value: dailyLoss, max: Number(limits.daily_loss_limit ?? 0.05) },
		{ label: 'Portfolio Risk', value: portfolioRisk, max: Number(limits.portfolio_budget ?? 0.02) },
	];

	$: limitBars = [
		{ label: 'Max Drawdown', current: currentDrawdown, max: Number(limits.max_drawdown ?? 0.1) },
		{ label: 'Daily Loss Limit', current: dailyLoss, max: Number(limits.daily_loss_limit ?? 0.05) },
		{ label: 'Portfolio Budget', current: portfolioRisk, max: Number(limits.portfolio_budget ?? 0.02) },
		{ label: 'Per-Trade Max', current: perTradeRisk, max: Number(limits.max_risk_per_trade ?? 0.02) },
	];

	function breakerColor(state?: string): string {
		const s = (state ?? '').toLowerCase();
		if (s === 'open' || s === 'tripped' || s === 'error') return 'text-red-400 border-red-800';
		if (s === 'half_open' || s === 'half-open' || s === 'degraded' || s === 'warning')
			return 'text-yellow-400 border-yellow-800';
		return 'text-green-400 border-green-800';
	}

	function clampPercent(value: number): number {
		return Math.max(0, Math.min(100, value));
	}

	function gaugeRatio(value: number, max: number): number {
		if (!max || max <= 0) return 0;
		return clampPercent((value / max) * 100);
	}

	function gaugeColor(value: number, max: number): string {
		if (!max || max <= 0) return '#6b7280';
		const ratio = value / max;
		if (ratio >= 1) return '#ef4444';
		if (ratio >= 0.75) return '#f59e0b';
		return '#22c55e';
	}

	function formatPct(value: number): string {
		return `${(value * 100).toFixed(2)}%`;
	}

	function formatUsd(value: number): string {
		return `${value >= 0 ? '+' : '-'}$${Math.abs(value).toFixed(2)}`;
	}

	function getExposureWidth(value: number, budget: number): number {
		const base = budget > 0 ? budget : 0.02;
		const ratio = Math.abs(value) / base;
		return Math.max(2, Math.min(50, ratio * 50));
	}

	async function loadRiskData() {
		error = '';
		const [dashboardResult, riskResult] = await Promise.allSettled([
			getAxiomDashboard(),
			getAxiomRisk(),
		]);

		if (dashboardResult.status === 'fulfilled') {
			dashboard = dashboardResult.value;
		}

		if (riskResult.status === 'fulfilled') {
			risk = riskResult.value;
		}

		if (dashboardResult.status === 'rejected' && riskResult.status === 'rejected') {
			error = 'Risk telemetry unavailable.';
		}

		loading = false;
	}

	async function handleTradingReset() {
		if (resetBusy) return;
		const confirmed = typeof window === 'undefined'
			? true
			: window.confirm('Reset the current trading halt and resume the runtime?');
		if (!confirmed) return;
		resetBusy = true;
		error = '';
		actionMessage = '';
		try {
			const result = await resetTradingHalt();
			await loadRiskData();
			actionMessage = result.trading_allowed
				? 'Trading reset complete. New entries are allowed again.'
				: `Trading reset completed, but entries are still blocked: ${result.trading_reason}`;
		} catch (err) {
			error = err instanceof Error ? err.message : 'Trading halt reset failed';
		} finally {
			resetBusy = false;
		}
	}

	async function handleEmergencyHalt() {
		if (haltBusy) return;
		const confirmed = typeof window === 'undefined'
			? false
			: window.confirm(
					'EMERGENCY HALT will immediately close ALL open positions at market and stop trading. This cannot be undone. Continue?',
				);
		if (!confirmed) return;
		haltBusy = true;
		error = '';
		actionMessage = '';
		try {
			const result = await triggerEmergencyHalt();
			if (result.ok === false) {
				throw new Error(result.error || 'Emergency halt failed');
			}
			await loadRiskData();
			const closedCount = Array.isArray(result.closed) ? result.closed.length : 0;
			actionMessage = `Emergency halt triggered. Closed ${closedCount} position${closedCount === 1 ? '' : 's'}.`;
		} catch (err) {
			error = err instanceof Error ? err.message : 'Emergency halt failed';
		} finally {
			haltBusy = false;
		}
	}

	onMount(() => {
		void loadRiskData();
		realtime = createRealtimeRefresh(loadRiskData, {
			fallbackMs: 20_000,
			wsDebounceMs: 1200,
		});
		realtime.start();
	});

	onDestroy(() => {
		realtime?.stop();
		realtime = null;
	});
</script>

<svelte:head>
	<title>Risk Command | Axiom</title>
	<meta name="description" content="Monitor drawdown, kill-switch state, and live portfolio risk guardrails." />
</svelte:head>

<div class="h-full overflow-y-auto p-6 space-y-6">
	<div class="flex items-center justify-between">
		<div class="flex items-center gap-3">
			<svg class="w-6 h-6 text-red-400" viewBox="0 0 24 24" fill="currentColor">
				<path d="M12 1L3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4zm0 11H5V6.3l7-3.11v8.8h7c-.53 4.12-3.28 7.79-7 8.94V12z" />
			</svg>
			<h1 class="text-2xl font-bold tracking-tight">Risk Command</h1>
		</div>
		<div class="flex items-center gap-2">
			<button
				type="button"
				class="text-xs border border-red-800 bg-red-950/30 px-3 py-1.5 font-bold uppercase tracking-wider text-red-300 hover:bg-red-900/50 hover:text-red-100 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
				on:click={handleEmergencyHalt}
				disabled={haltBusy}
				title="Immediately close all open positions and halt trading"
			>
				{haltBusy ? 'Halting...' : 'Emergency Halt'}
			</button>
			<a href="/settings" class="text-xs border border-[#333] px-3 py-1.5 text-gray-300 hover:text-white hover:border-[#555] transition-colors">
				Open Settings
			</a>
		</div>
	</div>

	{#if !tradingAllowed}
		<div class="rounded border p-4 flex items-start justify-between gap-4 {killSwitchActive ? 'border-red-800 bg-red-950/20' : dailyLossHalt ? 'border-amber-800 bg-amber-950/20' : 'border-amber-700/60 bg-amber-950/10'}">
			<div>
				<div class="text-sm font-bold tracking-wider uppercase {killSwitchActive ? 'text-red-300' : 'text-amber-300'}">
					{haltBannerTitle}
				</div>
				<div class="text-xs mt-1 {killSwitchActive ? 'text-red-200/90' : 'text-amber-100/90'}">
					{tradingReason}
					{#if killSwitchActive && risk?.kill_switch_triggered_at}
						Triggered at {new Date(risk.kill_switch_triggered_at).toLocaleString()}.
					{/if}
				</div>
			</div>
			<button
				class="px-3 py-1.5 text-xs border border-blue-700 text-blue-200 hover:bg-blue-900/30 transition-colors disabled:opacity-60"
				on:click={handleTradingReset}
				disabled={resetBusy}
			>
				{resetBusy ? 'Resetting...' : 'Reset Trading Halt'}
			</button>
		</div>
	{/if}

	{#if recoveryActive}
		<div class="rounded border p-4 {recoveryRequiresOperator ? 'border-red-800 bg-red-950/20' : 'border-amber-700/60 bg-amber-950/10'}">
			<div class="flex items-center gap-2">
				<span class="text-sm font-bold tracking-wider uppercase {recoveryRequiresOperator ? 'text-red-300' : 'text-amber-300'}">
					Position Recovery {recoveryRequiresOperator ? '— Operator Intervention Required' : 'In Progress'}
				</span>
				{#if recovery?.status}
					<span class="text-[10px] uppercase tracking-wider border rounded px-2 py-0.5 {recoveryRequiresOperator ? 'text-red-200 border-red-800' : 'text-amber-200 border-amber-800'}">
						{recovery.status}
					</span>
				{/if}
			</div>
			{#if recoverySummary}
				<div class="text-xs mt-1 {recoveryRequiresOperator ? 'text-red-200/90' : 'text-amber-100/90'}">
					{recoverySummary}
				</div>
			{/if}
			<div class="mt-2 flex flex-wrap gap-4 text-[11px] text-gray-400">
				<span>Positions: <span class="text-gray-200">{recovery?.position_count ?? 0}</span></span>
				<span>Discrepancies: <span class="text-gray-200">{recovery?.discrepancy_count ?? 0}</span></span>
				<span>Open orders: <span class="text-gray-200">{recovery?.open_order_count ?? 0}</span></span>
				{#if recovery?.last_checked_at}
					<span>Checked: <span class="text-gray-200">{new Date(recovery.last_checked_at).toLocaleString()}</span></span>
				{/if}
			</div>
		</div>
	{/if}

	{#if actionMessage}
		<div class="rounded border border-[#3a3220] bg-[#16130d] px-4 py-3 text-sm text-amber-200">
			{actionMessage}
		</div>
	{/if}

	{#if circuitBreakers.length > 0}
		<div class="flex flex-wrap items-center gap-2">
			<span class="text-[10px] uppercase tracking-wider text-gray-500">Circuit Breakers</span>
			{#each circuitBreakers as cb}
				<span class={`text-[11px] px-2 py-1 border rounded ${breakerColor(cb.state)}`}>
					{cb.label}: {cb.state}
				</span>
			{/each}
		</div>
	{/if}

	{#if hasRiskData}
	<div class="grid grid-cols-1 md:grid-cols-3 gap-4">
		{#each gauges as gauge}
			{@const ratio = gaugeRatio(gauge.value, gauge.max)}
			{@const color = gaugeColor(gauge.value, gauge.max)}
			<div class="border border-[#333] bg-[#0d0d0d] rounded p-4">
				<div class="flex items-center gap-4">
					<div class="relative w-20 h-20 rounded-full" style={`background: conic-gradient(${color} ${ratio * 3.6}deg, #1f2937 0deg);`}>
						<div class="absolute inset-2 rounded-full bg-[#050505] flex items-center justify-center text-[11px] font-bold text-gray-300">
							{formatPct(gauge.value)}
						</div>
					</div>
					<div class="min-w-0">
						<div class="text-[11px] uppercase tracking-wider text-gray-500">{gauge.label}</div>
						<div class={`text-lg font-bold ${ratio >= 100 ? 'text-red-400' : ratio >= 75 ? 'text-yellow-400' : 'text-green-400'}`}>
							{formatPct(gauge.value)}
						</div>
						<div class="text-[10px] text-gray-500">Limit: {formatPct(gauge.max)}</div>
					</div>
				</div>
			</div>
		{/each}
	</div>

	<div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
		<div class="border border-[#333] bg-[#0d0d0d] rounded p-4 space-y-3">
			<div class="flex items-center justify-between">
				<h2 class="text-sm font-bold uppercase tracking-wider text-gray-200">Trading Status</h2>
				<span class={`text-xs px-2 py-1 border rounded ${tradingAllowed ? 'text-green-400 border-green-800' : 'text-red-400 border-red-800'}`}>
					{tradingAllowed ? 'Allowed' : 'Halted'}
				</span>
			</div>
			<div class="text-xs text-gray-400">{tradingReason}</div>
			<div class="grid grid-cols-1 md:grid-cols-2 gap-3 pt-2">
				<div class="border border-[#222] bg-[#080808] rounded p-3">
					<div class="text-[10px] uppercase tracking-wider text-gray-500 mb-1">Daily PnL</div>
					<div class={`text-base font-bold ${dailyPnlUsd >= 0 ? 'text-green-400' : 'text-red-400'}`}>{formatUsd(dailyPnlUsd)}</div>
				</div>
				<div class="border border-[#222] bg-[#080808] rounded p-3">
					<div class="text-[10px] uppercase tracking-wider text-gray-500 mb-1">Equity Anchors</div>
					<div class="text-xs text-gray-300">HWM: ${highWaterMark.toFixed(2)}</div>
					<div class="text-xs text-gray-300">Daily Start: ${dailyStartEquity.toFixed(2)}</div>
				</div>
			</div>
		</div>

		<div class="border border-[#333] bg-[#0d0d0d] rounded p-4 space-y-3">
			<h2 class="text-sm font-bold uppercase tracking-wider text-gray-200">Risk Limits</h2>
			{#each limitBars as bar}
				<div class="space-y-1">
					<div class="flex items-center justify-between text-[11px]">
						<span class="text-gray-400">{bar.label}</span>
						<span class={bar.current > bar.max ? 'text-red-400' : 'text-gray-300'}>
							{formatPct(bar.current)} / {formatPct(bar.max)}
						</span>
					</div>
					<div class="h-2 rounded bg-[#1a1a1a] overflow-hidden">
						<div
							class={`h-full ${bar.current > bar.max ? 'bg-red-500' : 'bg-green-500'}`}
							style={`width: ${clampPercent(bar.max > 0 ? (bar.current / bar.max) * 100 : 0)}%;`}
						></div>
					</div>
				</div>
			{/each}
		</div>
	</div>
	{:else if !loading}
		<div class="border border-[#3a2f1a] bg-[#161208] rounded p-4 text-sm text-amber-200">
			Risk telemetry is unavailable. Gauges and limits cannot be displayed — the values below are not safe-zero readings.
		</div>
	{/if}

	<div class="border border-[#333] bg-[#0d0d0d] rounded p-4 space-y-3">
		<h2 class="text-sm font-bold uppercase tracking-wider text-gray-200">Correlation Groups</h2>
		{#if Object.entries(groups).length === 0}
			<div class="text-xs text-gray-500">No active position groups.</div>
		{:else}
			<div class="space-y-3">
				{#each Object.entries(groups) as [name, group]}
					{@const budget = Number(limits.portfolio_budget ?? 0.02)}
					{@const longValue = Number(group.gross_long ?? 0)}
					{@const shortValue = Number(group.gross_short ?? 0)}
					{@const netValue = Number(group.net ?? 0)}
					<div class="border border-[#222] bg-[#090909] rounded p-3">
						<div class="flex items-center justify-between text-xs mb-2">
							<span class="font-bold text-gray-300">{name}</span>
							<span class={netValue >= 0 ? 'text-green-400' : 'text-red-400'}>
								Net {formatPct(netValue)}
							</span>
						</div>
						<div class="h-2 rounded bg-[#141414] overflow-hidden flex">
							<div class="bg-green-600" style={`width: ${getExposureWidth(longValue, budget)}%;`}></div>
							<div class="bg-red-600" style={`width: ${getExposureWidth(shortValue, budget)}%;`}></div>
						</div>
						<div class="mt-2 text-[10px] text-gray-500 flex gap-4">
							<span>Long {formatPct(longValue)}</span>
							<span>Short {formatPct(shortValue)}</span>
						</div>
					</div>
				{/each}
			</div>
		{/if}
	</div>

	{#if loading}
		<LoadingState message="Loading risk telemetry..." />
	{/if}

	{#if error}
		<ErrorBanner message={error} tone="error" />
	{/if}
</div>
