<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { getAxiomAgents, getAxiomAgentTasks, getAxiomLogs } from '$lib/api';
	import type { AxiomAgent, AxiomAgentTask } from '$lib/api';
	import { createRealtimeRefresh, type RealtimeRefreshController } from '$lib/utils/realtime';

	interface AgentStyle {
		badgeClass: string;
		dotClass: string;
		icon: string;
	}

	interface FeedEntry {
		id: string;
		timestamp: string;
		level: string;
		source: string;
		message: string;
		agentId: string;
		agentName: string;
		style: AgentStyle;
	}

	const AGENT_STYLES: Record<string, AgentStyle> = {
		brain: {
			badgeClass: 'text-violet-200 border-violet-500/40 bg-violet-500/10',
			dotClass: 'bg-violet-400',
			icon: '◎',
		},
		'strategy-developer': {
			badgeClass: 'text-blue-200 border-blue-500/40 bg-blue-500/10',
			dotClass: 'bg-blue-400',
			icon: '◆',
		},
		'simulation-agent': {
			badgeClass: 'text-amber-200 border-amber-500/40 bg-amber-500/10',
			dotClass: 'bg-amber-400',
			icon: '▣',
		},
		'backtest-engineer': {
			badgeClass: 'text-amber-200 border-amber-500/40 bg-amber-500/10',
			dotClass: 'bg-amber-400',
			icon: '▣',
		},
		'quant-researcher': {
			badgeClass: 'text-cyan-200 border-cyan-500/40 bg-cyan-500/10',
			dotClass: 'bg-cyan-400',
			icon: '◈',
		},
		'risk-manager': {
			badgeClass: 'text-rose-200 border-rose-500/40 bg-rose-500/10',
			dotClass: 'bg-rose-400',
			icon: '▲',
		},
		'execution-trader': {
			badgeClass: 'text-emerald-200 border-emerald-500/40 bg-emerald-500/10',
			dotClass: 'bg-emerald-400',
			icon: '▶',
		},
		'full-stack-engineer': {
			badgeClass: 'text-sky-200 border-sky-500/40 bg-sky-500/10',
			dotClass: 'bg-sky-400',
			icon: '◍',
		},
	};

	const FALLBACK_STYLES: AgentStyle[] = [
		{
			badgeClass: 'text-slate-200 border-slate-500/40 bg-slate-500/10',
			dotClass: 'bg-slate-400',
			icon: '•',
		},
		{
			badgeClass: 'text-lime-200 border-lime-500/40 bg-lime-500/10',
			dotClass: 'bg-lime-400',
			icon: '○',
		},
		{
			badgeClass: 'text-orange-200 border-orange-500/40 bg-orange-500/10',
			dotClass: 'bg-orange-400',
			icon: '◌',
		},
	];

	interface RosterItem {
		id: string;
		name: string;
		model: string;
		running: boolean;
		activeTaskCount: number;
	}

	let entries: FeedEntry[] = [];
	let roster: RosterItem[] = [];
	let loading = true;
	let errorMessage = '';
	let realtime: RealtimeRefreshController | null = null;

	$: rosterNameById = roster.reduce<Record<string, string>>((acc, item) => {
		if (item.id && item.name) acc[item.id] = item.name;
		return acc;
	}, {});

	function parseAgentId(source: string): string {
		if (!source) return 'unknown';
		if (source === 'brain') return 'brain';
		if (source.startsWith('agent:')) return source.slice(6) || 'unknown';
		return source;
	}

	function toAgentName(agentId: string): string {
		if (agentId === 'backtest-engineer') return 'Simulation Agent';
		if (agentId === 'brain') return 'Brain';
		return agentId
			.split(/[-_]/g)
			.filter(Boolean)
			.map((part) => part.charAt(0).toUpperCase() + part.slice(1))
			.join(' ') || 'Unknown';
	}

	function getAgentStyle(agentId: string): AgentStyle {
		if (AGENT_STYLES[agentId]) return AGENT_STYLES[agentId];
		let hash = 0;
		for (let i = 0; i < agentId.length; i += 1) hash = (hash + agentId.charCodeAt(i)) % 2048;
		return FALLBACK_STYLES[hash % FALLBACK_STYLES.length];
	}

	function formatTime(timestamp: string): string {
		if (!timestamp) return '--:--:--';
		const normalized = timestamp.includes(' ') ? timestamp.replace(' ', 'T') : timestamp;
		const parsed = new Date(normalized);
		if (Number.isNaN(parsed.getTime())) return '--:--:--';
		return parsed.toLocaleTimeString([], { hour12: false });
	}

	function normalizeEntry(raw: unknown, index: number): FeedEntry | null {
		if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
		const rec = raw as Record<string, unknown>;
		const level = String(rec.level ?? '').toLowerCase();
		if (level !== 'heartbeat' && level !== 'info') return null;

		const source = String(rec.source ?? rec.module ?? '').trim();
		if (!source) return null;
		if (!(source.startsWith('agent:') || source === 'brain')) return null;

		const message = String(rec.message ?? rec.msg ?? '').trim();
		if (!message) return null;

		const timestamp = String(rec.created_at ?? rec.ts ?? '');
		const id = String(rec.id ?? `${source}-${timestamp}-${index}-${message.slice(0, 24)}`);
		const agentId = parseAgentId(source);
		return {
			id,
			timestamp,
			level,
			source,
			message,
			agentId,
			agentName: toAgentName(agentId),
			style: getAgentStyle(agentId),
		};
	}

	async function loadRoster() {
		try {
			const [agentsResult, tasksResult] = await Promise.allSettled([
				getAxiomAgents(),
				getAxiomAgentTasks(),
			]);
			const rawAgents: AxiomAgent[] =
				agentsResult.status === 'fulfilled' && Array.isArray(agentsResult.value)
					? agentsResult.value
					: [];
			const rawTasks: AxiomAgentTask[] =
				tasksResult.status === 'fulfilled' && Array.isArray(tasksResult.value)
					? tasksResult.value
					: [];

			roster = rawAgents
				.map((agent): RosterItem | null => {
					const id = String(agent.id ?? '').trim();
					if (!id) return null;
					const name = String(agent.name ?? '').trim() || toAgentName(id);
					const model = String(agent.model_id ?? agent.model ?? '').trim();
					const agentTasks = rawTasks.filter((task) => String(task.agent_id ?? '') === id);
					const running =
						String(agent.status ?? '').toLowerCase() === 'running' ||
						agentTasks.some((task) => String(task.status ?? '').toLowerCase() === 'running');
					const activeTaskCount = agentTasks.filter((task) => {
						const status = String(task.status ?? '').toLowerCase();
						return status === 'running' || status === 'pending';
					}).length;
					return { id, name, model, running, activeTaskCount };
				})
				.filter((item): item is RosterItem => Boolean(item));
		} catch {
			// Roster fetch failure should not break the heartbeat feed; leave roster untouched.
		}
	}

	async function loadEntries() {
		try {
			const rawLogs = await getAxiomLogs(120);
			const list = Array.isArray(rawLogs) ? rawLogs : [];
			entries = list
				.map((entry, index) => normalizeEntry(entry, index))
				.filter((entry): entry is FeedEntry => Boolean(entry))
				.slice(0, 60);
			errorMessage = '';
		} catch (error) {
			errorMessage = error instanceof Error ? error.message : 'Failed to load heartbeat feed';
		} finally {
			loading = false;
		}
		await loadRoster();
	}

	onMount(() => {
		realtime = createRealtimeRefresh(loadEntries, {
			fallbackMs: 60_000,
			wsDebounceMs: 6000,
			wsEvents: ['task_completed', 'task_failed', 'strategy_promoted', 'kill_switch_activated', 'kill_switch_cleared'],
			pollWhenWsOfflineOnly: false,
		});
		realtime.start();
	});

	onDestroy(() => {
		realtime?.stop();
		realtime = null;
	});
</script>

<section class="h-full flex flex-col border border-[#242424] rounded bg-[#080808]">
	<header class="flex items-center justify-between px-3 py-2 border-b border-[#202020] bg-[#0d0d0d]">
		<div class="flex items-center gap-2">
			<span class="w-2 h-2 rounded-full bg-green-400 animate-pulse"></span>
			<h3 class="text-xs tracking-[0.2em] uppercase text-gray-200 font-semibold">Heartbeat / Agent Activity</h3>
		</div>
		<span class="text-[10px] text-gray-500 font-mono">ws live + fallback</span>
	</header>

	<div class="flex-1 min-h-0 overflow-y-auto px-2 py-2 font-mono text-xs">
		{#if loading && entries.length === 0}
			<div class="h-full flex items-center justify-center text-gray-500 animate-pulse">Booting activity stream...</div>
		{:else if entries.length === 0}
			<div class="h-full flex items-center justify-center text-gray-500">No heartbeat events yet.</div>
		{:else}
			<div class="space-y-1">
				{#each entries as entry (entry.id)}
					<div
						class="group flex items-start gap-2 px-2 py-1 rounded border border-transparent hover:border-[#2a2a2a] hover:bg-[#111] transition-colors"
					>
						<span class="text-[10px] text-gray-500 w-[74px] flex-shrink-0 pt-[2px]">{formatTime(entry.timestamp)}</span>
						<span class="w-1.5 h-1.5 rounded-full mt-[7px] flex-shrink-0 {entry.style.dotClass}"></span>
						<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded border text-[10px] uppercase tracking-wide flex-shrink-0 {entry.style.badgeClass}">
							<span>{entry.style.icon}</span>
							{rosterNameById[entry.agentId] ?? entry.agentName}
						</span>
						<span class="text-gray-200 leading-5 break-words">{entry.message}</span>
					</div>
				{/each}
			</div>
		{/if}
	</div>

	<!-- Roster as a single chip strip: one footer row instead of one row per
	     agent, so the activity feed keeps the panel's height. Full detail
	     (model, task count) lives in each chip's tooltip. -->
	<div
		data-testid="agent-roster"
		class="flex flex-wrap items-center gap-x-2 gap-y-1 border-t border-[#1a1a1a] bg-[#0b0b0b] px-2.5 py-1.5"
	>
		{#if roster.length === 0}
			<span class="text-[10px] italic text-gray-600">No agents registered.</span>
		{:else}
			{#each roster as agent (agent.id)}
				<span
					class="inline-flex items-center gap-1 font-mono text-[10px] {agent.running ? 'text-gray-300' : 'text-gray-600'}"
					title="{agent.name}{agent.model ? ` · ${agent.model}` : ''} · {agent.activeTaskCount} active task(s) · {agent.running ? 'running' : 'idle'}"
				>
					<span class={`inline-block h-1.5 w-1.5 flex-shrink-0 rounded-full ${agent.running ? 'bg-emerald-400' : 'bg-gray-700'}`}></span>
					{agent.name}{#if agent.activeTaskCount > 0}<span class="text-cyan-400">·{agent.activeTaskCount}</span>{/if}
				</span>
			{/each}
			<span class="ml-auto font-mono text-[10px] text-gray-600">{roster.length} agents</span>
		{/if}
	</div>

	{#if errorMessage}
		<div class="px-3 py-2 border-t border-red-900/40 bg-red-900/10 text-[10px] text-red-300">
			{errorMessage}
		</div>
	{/if}
</section>
