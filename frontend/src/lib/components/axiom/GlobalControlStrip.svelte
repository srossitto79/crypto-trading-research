<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { axiomWsConnected } from '$lib/stores/axiomWebSocket';
	import {
		axiomDashboard,
		axiomRisk,
		axiomSentiment,
		axiomRegime,
	} from '$lib/stores/axiom';
	import { simulationActive, simulationPhase, simulationTime } from '$lib/stores/simulation';
	import {
		getSystemStatus,
		setSystemMode,
		resetTradingHalt,
		setAxiomExecutionMode,
		triggerAxiomEmergencyHalt,
		type PausedManualCounts,
		type SystemMode,
	} from '$lib/api';
	import { createRealtimeRefresh, type RealtimeRefreshController } from '$lib/utils/realtime';

	type ModalAction = 'mode-toggle' | 'emergency-halt' | 'trading-reset' | 'system-mode-change' | null;
	type ExecutionMode = 'paper' | 'live';

	let systemPaused = false;
	let generationPaused = false;
	let systemMode: SystemMode = 'manual';
	let systemModeTarget: SystemMode = 'manual';
	let wsConnected = false;
	let realtime: RealtimeRefreshController | null = null;
	let actionError = '';
	let actionBusy = false;
	let modalOpen = false;
	let modalAction: ModalAction = null;
	let executionMode: ExecutionMode = 'paper';
	let modeSwitchTarget: ExecutionMode = 'live';
	let pausedManualCounts: PausedManualCounts = emptyPausedManualCounts();

	const SYSTEM_MODES: { value: SystemMode; label: string; short: string }[] = [
		{ value: 'manual', label: 'Manual', short: 'MANUAL' },
		{ value: 'semi_auto', label: 'Semi', short: 'SEMI' },
		{ value: 'auto', label: 'Auto', short: 'AUTO' },
	];

	function normalizeSystemMode(mode: unknown): SystemMode {
		if (mode === 'auto' || mode === 'semi_auto' || mode === 'manual') return mode;
		return 'manual';
	}

	function systemModeDescription(mode: SystemMode): string {
		if (mode === 'manual') {
			return 'Manual mode: all autonomous background work freezes. Scheduled jobs stop, queued autonomous tasks pause, and only direct operator actions can run until you leave manual mode.';
		}
		if (mode === 'semi_auto') {
			return 'Semi-automatic mode: the system will not spawn new crucibles on its own. Crucibles you enter manually are fully evaluated by the research, Gauntlet, and lifecycle machinery. Trading stays active.';
		}
		return 'Fully automatic mode: the scanner and agents autonomously generate, evaluate, and promote crucibles. Live trading is active. This is the original pipeline behavior.';
	}

	function emptyPausedManualCounts(): PausedManualCounts {
		return { agent_tasks: 0, brain_tasks: 0, total: 0 };
	}

	function normalizePausedManualCounts(value: unknown): PausedManualCounts {
		if (!value || typeof value !== 'object') return emptyPausedManualCounts();
		const raw = value as Record<string, unknown>;
		const agentTasks = Number(raw.agent_tasks ?? 0);
		const brainTasks = Number(raw.brain_tasks ?? 0);
		const total = Number(raw.total ?? agentTasks + brainTasks);
		return {
			agent_tasks: Number.isFinite(agentTasks) ? agentTasks : 0,
			brain_tasks: Number.isFinite(brainTasks) ? brainTasks : 0,
			total: Number.isFinite(total) ? total : 0,
		};
	}

	function pausedManualBannerText(counts: PausedManualCounts): string {
		if (counts.total <= 0) {
			return 'Manual mode - all background work frozen. Only direct operator actions run.';
		}
		const pausedLabel = counts.total === 1 ? '1 queued task paused' : `${counts.total} queued tasks paused`;
		return `Manual mode - all background work frozen. ${pausedLabel}.`;
	}

	function normalizeMode(mode: unknown): ExecutionMode {
		return mode === 'live' ? 'live' : 'paper';
	}

	$: executionMode = normalizeMode($axiomDashboard?.execution_mode);
	$: hlNetwork = (() => {
		const raw = ($axiomDashboard?.account?.network || '').toString().trim().toLowerCase();
		if (raw === 'mainnet' || raw === 'testnet') return raw;
		return executionMode === 'live' ? 'mainnet' : 'testnet';
	})();
	$: daemonStatus = $axiomDashboard ? ($axiomDashboard.daemon_running ? 'ACTIVE' : 'OFFLINE') : 'SYNCING';
	$: btcRegime = ($axiomRegime as Record<string, Record<string, string>> | null)?.BTC?.regime || '--';
	$: ethRegime = ($axiomRegime as Record<string, Record<string, string>> | null)?.ETH?.regime || null;
	$: solRegime = ($axiomRegime as Record<string, Record<string, string>> | null)?.SOL?.regime || null;
	$: sentimentScore = typeof ($axiomSentiment as Record<string, unknown> | null)?.composite === 'number' ? ($axiomSentiment as Record<string, number>).composite : null;
	$: tradingAllowed = $axiomDashboard?.trading_allowed ?? true;
	$: tradingReason = ($axiomDashboard as Record<string, unknown> | null)?.trading_reason as string || 'OK';
	$: killSwitchActive = Boolean($axiomRisk?.kill_switch_active || ($axiomDashboard?.risk as Record<string, unknown> | undefined)?.kill_switch_active);
	$: modeSwitchTarget = executionMode === 'paper' ? 'live' : 'paper';
	$: wsConnected = $axiomWsConnected;
	$: simTimeFormatted = $simulationTime ? new Date($simulationTime).toLocaleString() : '--';
	$: simPhase = ($simulationPhase || 'idle').toUpperCase();

	$: modalTitle = getModalTitle(modalAction, modeSwitchTarget);
	$: modalMessage = getModalMessage(modalAction, executionMode, modeSwitchTarget);
	$: modalDanger =
		modalAction === 'emergency-halt'
		|| modalAction === 'trading-reset'
		|| (modalAction === 'mode-toggle' && modeSwitchTarget === 'live')
		|| (modalAction === 'system-mode-change' && systemModeTarget === 'auto');

	function getModalTitle(action: ModalAction, targetMode: 'paper' | 'live'): string {
		if (action === 'system-mode-change') {
			const target = SYSTEM_MODES.find((m) => m.value === systemModeTarget);
			return `Switch to ${target?.label ?? systemModeTarget} mode`;
		}
		if (action === 'emergency-halt') return 'Emergency Halt';
		if (action === 'trading-reset') return 'Reset Trading Halt';
		if (action === 'mode-toggle') return `Switch To ${targetMode.toUpperCase()}`;
		return '';
	}

	function getModalMessage(action: ModalAction, currentMode: 'paper' | 'live', targetMode: 'paper' | 'live'): string {
		if (action === 'system-mode-change') {
			return systemModeDescription(systemModeTarget);
		}
		if (action === 'emergency-halt') {
			return 'This will immediately close all open positions via market orders.';
		}
		if (action === 'trading-reset') {
			return 'This clears the current trading halt, resets risk halt flags, and resumes the runtime gate. Only continue if the trigger cause is resolved.';
		}
		if (action === 'mode-toggle') {
			if (targetMode === 'live') {
				return `Switch from ${currentMode.toUpperCase()} to LIVE mode? Live mode can send real orders.`;
			}
			return `Switch from ${currentMode.toUpperCase()} to PAPER mode?`;
		}
		return '';
	}

	async function loadSystemStatus() {
		try {
			const result = await getSystemStatus();
			systemPaused = result.paused;
			generationPaused = result.generation_paused ?? false;
			systemMode = normalizeSystemMode(result.system_mode);
			pausedManualCounts = normalizePausedManualCounts(result.paused_manual_counts);
		} catch {
			// System status unavailable - keep last known state
		}
	}

	function openModal(action: ModalAction) {
		actionError = '';
		modalAction = action;
		modalOpen = true;
	}

	function requestSystemMode(target: SystemMode) {
		if (target === systemMode) return;
		systemModeTarget = target;
		openModal('system-mode-change');
	}

	function closeModal(force = false) {
		modalOpen = false;
		modalAction = null;
		if (force || !actionBusy) {
			actionError = '';
		}
	}

	async function confirmModal() {
		if (!modalAction) return;
		actionBusy = true;
		actionError = '';
		try {
			if (modalAction === 'system-mode-change') {
				const result = await setSystemMode(systemModeTarget);
				systemMode = normalizeSystemMode(result.system_mode);
				pausedManualCounts = normalizePausedManualCounts(result.paused_manual_counts);
				closeModal(true);
				void loadSystemStatus();
				return;
			} else if (modalAction === 'mode-toggle') {
				await setAxiomExecutionMode(modeSwitchTarget);
			} else if (modalAction === 'emergency-halt') {
				await triggerAxiomEmergencyHalt();
			} else if (modalAction === 'trading-reset') {
				await resetTradingHalt();
			}
			await loadSystemStatus();
			closeModal(true);
		} catch (err) {
			actionError = err instanceof Error ? err.message : 'Action failed';
		} finally {
			actionBusy = false;
		}
	}

	function getSentimentClass(score: number | null): string {
		if (score === null) return 'border-[#333] text-gray-500';
		if (score >= 60) return 'border-green-700 text-green-400';
		if (score >= 40) return 'border-yellow-700 text-yellow-400';
		return 'border-red-700 text-red-400';
	}

	onMount(() => {
		void loadSystemStatus();
		realtime = createRealtimeRefresh(loadSystemStatus, {
			fallbackMs: 30_000,
			wsDebounceMs: 1200,
			wsEvents: ['kill_switch_activated', 'kill_switch_cleared', 'strategy_transition', 'trade'],
		});
		realtime.start();
	});

	onDestroy(() => {
		realtime?.stop();
		realtime = null;
	});
</script>

{#if systemMode === 'manual'}
	<div class="bg-amber-700/85 border-b border-amber-500 px-4 py-1 text-[11px] uppercase tracking-wider text-white font-bold flex flex-wrap items-center justify-between gap-2">
		<span>{pausedManualBannerText(pausedManualCounts)}</span>
		<button
			class="px-2 py-0.5 border border-white/40 rounded text-[10px] hover:bg-white/10 transition-colors"
			on:click={() => requestSystemMode('semi_auto')}
		>
			Switch to Semi
		</button>
	</div>
{:else if systemMode === 'semi_auto'}
	<div class="bg-sky-800/85 border-b border-sky-500 px-4 py-1 text-[11px] uppercase tracking-wider text-white font-bold flex flex-wrap items-center justify-between gap-2">
		<span>Semi mode - autonomous generation off; user-created hypotheses still run through the pipeline.</span>
		<button
			class="px-2 py-0.5 border border-white/40 rounded text-[10px] hover:bg-white/10 transition-colors"
			on:click={() => requestSystemMode('auto')}
		>
			Switch to Auto
		</button>
	</div>
{/if}
{#if !wsConnected}
	<div class="bg-red-700/85 border-b border-red-500 px-4 py-1 text-[11px] uppercase tracking-wider text-white font-bold">
		Connection lost. Reconnecting to Axiom websocket...
	</div>
{/if}
{#if $simulationActive}
	<div class="bg-gray-900/90 border-b border-gray-700 px-4 py-1 text-[11px] uppercase tracking-wider text-white font-bold flex flex-wrap items-center justify-between gap-2">
		<span class="flex items-center gap-2">
			<span class="w-2 h-2 bg-white rounded-full animate-pulse"></span>
			Simulation Active &mdash; Virtual Time: {simTimeFormatted} &mdash; {simPhase}
		</span>
		<a href="/lab" class="px-2 py-0.5 border border-white/40 rounded text-[10px] hover:bg-white/10 transition-colors">
			Open Strategies
		</a>
	</div>
{/if}
<header class="border-b border-[#222] bg-[#0b0b0b] px-4 py-2 flex flex-wrap items-center gap-3 text-[11px] uppercase tracking-wider">
	<div class="flex min-w-0 flex-wrap items-center gap-3 lg:gap-4">
		<div class="flex items-center gap-2 whitespace-nowrap">
			<span class={`w-2 h-2 rounded-full ${daemonStatus === 'OFFLINE' ? 'bg-red-500' : daemonStatus === 'SYNCING' ? 'bg-yellow-500' : 'bg-green-500'}`}></span>
			<span class="text-gray-300">Daemon {daemonStatus}</span>
		</div>
		<div class="flex items-center gap-2 whitespace-nowrap">
			<span class={`w-2 h-2 rounded-full ${wsConnected ? 'bg-green-500' : 'bg-red-500'}`}></span>
			<span class={wsConnected ? 'text-green-400' : 'text-red-400'}>{wsConnected ? 'WS Live' : 'WS Offline'}</span>
		</div>
	</div>

	<div class="flex min-w-0 flex-1 flex-wrap items-center justify-center gap-2">
		<span class="px-2 py-1 border border-cyan-900 text-cyan-300 bg-cyan-950/20 rounded whitespace-nowrap">
			BTC: {btcRegime}
			{#if ethRegime}<span class="text-cyan-500/80 ml-1">| ETH: {ethRegime}</span>{/if}
			{#if solRegime}<span class="text-cyan-500/80 ml-1">| SOL: {solRegime}</span>{/if}
		</span>

		<!-- Execution mode is display-only: Axiom supports paper trading +
		     Hyperliquid testnet only. Live/mainnet is not a supported feature, so
		     there is no in-app switch to it. -->
		<span
			class={`px-2 py-1 border rounded whitespace-nowrap ${executionMode === 'live' ? 'border-red-800 text-red-300 bg-red-950/20' : 'border-yellow-800 text-yellow-300 bg-yellow-950/20'}`}
			title={`Execution mode: ${executionMode.toUpperCase()} — paper trading + Hyperliquid testnet only`}
		>
			Mode: {executionMode.toUpperCase()}
		</span>

		<span
			class={`px-2 py-1 border rounded whitespace-nowrap font-bold ${hlNetwork === 'mainnet' ? 'border-red-600 text-red-300 bg-red-950/30' : 'border-emerald-800 text-emerald-300 bg-emerald-950/20'}`}
			title={hlNetwork === 'mainnet' ? 'HyperLiquid MAINNET — orders use real funds' : 'HyperLiquid testnet — no real funds at risk'}
		>
			{hlNetwork === 'mainnet' ? 'MAINNET' : 'TESTNET'}
		</span>

		<span class={`px-2 py-1 border rounded whitespace-nowrap ${getSentimentClass(sentimentScore)}`}>
			F&G: {sentimentScore !== null ? Math.round(sentimentScore) : '--'}
		</span>

		<a href="/risk" class={`px-2 py-1 border rounded whitespace-nowrap ${tradingAllowed ? 'border-green-800 text-green-400' : 'border-red-800 text-red-400'}`}>
			{tradingAllowed ? 'Trading Allowed' : 'Trading Halted'}
		</a>

		{#if killSwitchActive}
			<span class="px-2 py-1 border border-red-700 text-red-300 rounded whitespace-nowrap">Kill Switch Active</span>
		{/if}
	</div>

	<div class="ml-auto flex min-w-0 flex-wrap items-center justify-end gap-2">
		<div
			class="inline-flex items-stretch border border-[#333] rounded overflow-hidden"
			role="group"
			aria-label="System mode"
			title="System mode - controls whether the system runs autonomously"
		>
			{#each SYSTEM_MODES as option (option.value)}
				<button
					class={`px-2.5 py-1 text-[11px] font-bold transition-colors border-r border-[#333] last:border-r-0 ${
						systemMode === option.value
							? option.value === 'auto'
								? 'bg-red-900/40 text-red-200'
								: option.value === 'semi_auto'
									? 'bg-sky-900/40 text-sky-200'
									: 'bg-amber-900/40 text-amber-200'
							: 'text-gray-400 hover:bg-[#1a1a1a] hover:text-white'
					}`}
					on:click={() => requestSystemMode(option.value)}
					aria-pressed={systemMode === option.value}
				>
					{option.short}
				</button>
			{/each}
		</div>
		{#if !tradingAllowed}
			<button class="px-2 py-1 border border-gray-700 text-gray-300 hover:bg-gray-800/40 rounded whitespace-nowrap transition-colors" on:click={() => openModal('trading-reset')}>
				Reset Halt
			</button>
		{/if}
		<button
			class="px-2 py-1 border border-red-800 text-red-300 hover:bg-red-900/30 rounded whitespace-nowrap transition-colors"
			on:click={() => openModal('emergency-halt')}
		>
			Emergency Halt
		</button>
	</div>
</header>

{#if !tradingAllowed}
	<div class="border-b border-[#222] bg-[#080808] px-4 py-1 text-[10px] uppercase tracking-wider text-red-300">
		Trading halted: {tradingReason}
	</div>
{/if}

{#if modalOpen && modalAction}
	<!-- svelte-ignore a11y-click-events-have-key-events -->
	<!-- svelte-ignore a11y-no-static-element-interactions -->
	<div class="fixed inset-0 z-[10010] bg-black/75 backdrop-blur-sm flex items-center justify-center p-4 pointer-events-none" on:click={() => closeModal()}>
		<div class="w-full max-w-md border border-[#333] bg-[#0f0f0f] rounded p-4 space-y-3 pointer-events-auto" on:click|stopPropagation>
			<h3 class={`text-sm font-bold uppercase tracking-wider ${modalDanger ? 'text-red-300' : 'text-white'}`}>{modalTitle}</h3>
			<p class="text-xs text-gray-300 leading-relaxed">{modalMessage}</p>
			{#if actionError}
				<div class="text-xs border border-red-900 bg-red-950/40 text-red-300 px-2 py-1 rounded">{actionError}</div>
			{/if}
			<div class="flex justify-end gap-2 pt-1">
				<button class="px-3 py-1.5 text-xs border border-[#444] text-gray-300 hover:text-white hover:border-[#666] rounded" on:click={() => closeModal()}>
					Cancel
				</button>
				<button
					class={`px-3 py-1.5 text-xs border rounded ${modalDanger ? 'border-red-700 text-red-200 hover:bg-red-900/40' : 'border-white text-white hover:bg-white hover:text-black'}`}
					on:click={confirmModal}
					disabled={actionBusy}
				>
					{actionBusy ? 'Working...' : 'Confirm'}
				</button>
			</div>
		</div>
	</div>
{/if}
