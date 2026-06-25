<script lang="ts">
	import type { AxiomAgentTask } from '$lib/api';

	type DateDisplayMode = 'relative' | 'absolute';
	type AccentColor = 'cyan' | 'green' | 'amber' | 'rose';
	type NormalizedTask = AxiomAgentTask & {
		_agentId: string;
		_id: string;
	};

	export let tasks: AxiomAgentTask[] = [];
	export let visibleCount = 15;
	export let dateFormat: DateDisplayMode = 'absolute';
	export let accentColor: AccentColor = 'cyan';
	export let agentNamesById: Record<string, string> = {};
	export let onAgentClick: (agentId: string) => void = () => {};
	export let onDismissTask: (task: AxiomAgentTask) => void | Promise<void> = () => {};

	function displayAgentName(agentId: string): string {
		const trimmed = agentId.trim();
		if (!trimmed) return 'Unknown';
		return agentNamesById[trimmed] ?? trimmed;
	}

	let selectedAgent = 'all';
	let selectedStatus = 'all';

	const accentMap: Record<AccentColor, string> = {
		cyan: '#06b6d4',
		green: '#22c55e',
		amber: '#f59e0b',
		rose: '#fb7185'
	};

	let normalized: NormalizedTask[] = [];
	let filtered: NormalizedTask[] = [];
	let visibleTasks: NormalizedTask[] = [];

	$: normalized = tasks
		.filter((task) => !!(task && typeof task === 'object'))
		.map<NormalizedTask>((task) => ({
			...task,
			_agentId: String(task.agent_id ?? '').trim(),
			_id: String(task.id ?? '')
		}))
		.sort((a, b) => parseTs(b.created_at) - parseTs(a.created_at));

	$: agentFilterValues = Array.from(
		new Set(
			normalized
				.map((task) => task._agentId)
				.filter((agentId) => agentId.length > 0)
		)
	).sort((a, b) => a.localeCompare(b));

	$: statusFilterValues = Array.from(
		new Set(
			normalized
				.map((task) => (task.status ?? 'unknown').toLowerCase())
				.filter((status) => status.length > 0)
		)
	).sort((a, b) => a.localeCompare(b));

	$: filtered = normalized.filter((task) => {
		if (selectedAgent !== 'all' && task._agentId !== selectedAgent) return false;
		if (selectedStatus !== 'all' && (task.status ?? '').toLowerCase() !== selectedStatus) return false;
		return true;
	});

	$: visibleTasks = filtered.slice(0, Math.max(1, visibleCount));

	$: accentStyle = accentMap[accentColor] ?? accentMap.cyan;

	function parseTs(raw?: string | null): number {
		if (!raw) return 0;
		const parsed = Date.parse(raw);
		return Number.isNaN(parsed) ? 0 : parsed;
	}

	function formatDateTime(raw?: string | null): string {
		if (!raw) return '--';
		const parsed = new Date(raw);
		if (Number.isNaN(parsed.getTime())) return '--';
		return parsed.toLocaleString('en-US', {
			month: 'short',
			day: 'numeric',
			hour: '2-digit',
			minute: '2-digit',
			hour12: false
		}).replace(',', '');
	}

	function formatRelativeTime(raw?: string | null): string {
		if (!raw) return '--';
		const parsed = Date.parse(raw);
		if (Number.isNaN(parsed)) return '--';

		const now = Date.now();
		const diff = now - parsed;
		const abs = Math.abs(diff);
		const sign = diff >= 0 ? 'ago' : 'from now';

		const minute = 60_000;
		const hour = 60 * minute;
		const day = 24 * hour;

		if (abs < minute) {
			const seconds = Math.round(abs / 1000);
			return `${seconds}s ${sign}`;
		}
		if (abs < hour) {
			const mins = Math.round(abs / minute);
			return `${mins}m ${sign}`;
		}
		if (abs < day) {
			const hours = Math.round(abs / hour);
			return `${hours}h ${sign}`;
		}
		const days = Math.round(abs / day);
		return `${days}d ${sign}`;
	}

	function normalizeStatus(status?: string | null): string {
		return (status ?? 'pending').toLowerCase();
	}

	function statusLabel(status?: string | null): string {
		const value = normalizeStatus(status);
		if (value === 'paused_manual') return 'Paused by manual mode';
		return value || 'pending';
	}

	function getStatusClass(status?: string | null): string {
		const value = normalizeStatus(status);
		if (value === 'running') return 'border-yellow-500 text-yellow-400';
		if (value === 'done' || value === 'completed' || value === 'reviewed') return 'border-green-500 text-green-500';
		if (value === 'error' || value === 'failed') return 'border-red-500 text-red-500';
		if (value === 'paused_manual') return 'border-amber-500 text-amber-300';
		if (value === 'pending') return 'border-gray-500 text-gray-500';
		return 'border-gray-700 text-gray-500';
	}

	function isRunning(task: NormalizedTask): boolean {
		return normalizeStatus(task.status) === 'running';
	}

	function isFailed(task: NormalizedTask): boolean {
		const status = normalizeStatus(task.status);
		return status === 'failed' || status === 'error';
	}

	function getTaskId(task: NormalizedTask): string {
		if (task._id) return task._id;
		if (task.id === 0 || task.id) return String(task.id);
		if (task._agentId) {
			return `${task._agentId}-${parseTs(task.created_at)}-${task.title || task.type || 'task'}`;
		}
		return 'task';
	}

	function formatTime(task: NormalizedTask): string {
		if (dateFormat === 'relative') return formatRelativeTime(task.created_at ?? null);
		return formatDateTime(task.created_at ?? null);
	}

	function safeAgent(task: NormalizedTask, fallback: string): string {
		return task._agentId.length > 0 ? task._agentId : fallback;
	}

	$: if (selectedAgent !== 'all' && !agentFilterValues.includes(selectedAgent)) selectedAgent = 'all';
	$: if (selectedStatus !== 'all' && !statusFilterValues.includes(selectedStatus)) selectedStatus = 'all';
</script>

<div class="bg-[#111] border border-[#333] rounded-lg flex flex-col">
	<div class="px-4 py-3 border-b border-[#333] flex flex-col gap-3">
		<div class="flex items-center justify-between">
			<h3 class="font-bold text-sm text-gray-300 uppercase tracking-wider">Task Queue</h3>
			<div class="text-xs text-gray-500">Showing {visibleTasks.length}/{filtered.length}</div>
		</div>
		<div class="grid grid-cols-1 md:grid-cols-3 gap-2">
			<div>
				<label class="block text-[10px] text-gray-500 uppercase tracking-wider mb-1" for="task-agent-filter">Agent</label>
				<select id="task-agent-filter" class="terminal-select" bind:value={selectedAgent}>
					<option value="all">All Agents</option>
					{#each agentFilterValues as agentId}
						<option value={agentId}>{displayAgentName(agentId)}</option>
					{/each}
				</select>
			</div>
			<div>
				<label class="block text-[10px] text-gray-500 uppercase tracking-wider mb-1" for="task-status-filter">Status</label>
				<select id="task-status-filter" class="terminal-select" bind:value={selectedStatus}>
					<option value="all">All Statuses</option>
					{#each statusFilterValues as status}
						<option value={status}>{statusLabel(status)}</option>
					{/each}
				</select>
			</div>
			<div class="flex items-end">
				<div class="text-[10px] text-gray-500 uppercase tracking-wider">Rows shown: {Math.min(visibleTasks.length, visibleCount)}</div>
			</div>
		</div>
	</div>
	<div class="overflow-x-auto">
		<table class="w-full text-left text-xs">
			<thead>
					<tr class="border-b border-[#222] text-gray-500 uppercase tracking-wider">
						<th class="px-4 py-2 font-medium">ID</th>
						<th class="px-4 py-2 font-medium">Time</th>
						<th class="px-4 py-2 font-medium">Agent</th>
						<th class="px-4 py-2 font-medium">Type</th>
						<th class="px-4 py-2 font-medium">Status</th>
						<th class="px-4 py-2 font-medium text-right">Actions</th>
					</tr>
				</thead>
				<tbody class="divide-y divide-[#222]">
				{#each visibleTasks as task}
					<tr
						class="hover:bg-[#1a1a1a] transition-colors {isRunning(task) ? 'running-task' : ''}"
						style={isRunning(task) ? `--hub-task-accent:${accentStyle}; border-left: 3px solid ${accentStyle};` : ''}
					>
						<td class="px-4 py-2 text-gray-500">#{getTaskId(task)}</td>
						<td class="px-4 py-2 text-gray-400">{formatTime(task)}</td>
						<td class="px-4 py-2">
							<button
								type="button"
								class="text-cyan-400 hover:text-cyan-300 hover:underline"
								on:click={() => {
									const agentId = task._agentId.trim();
									if (agentId) onAgentClick(agentId);
								}}
							>
								{displayAgentName(task._agentId)}
							</button>
						</td>
						<td class="px-4 py-2 text-gray-300 truncate max-w-[180px]" title={task.title}>{task.title || task.type}</td>
							<td class="px-4 py-2">
								<span class={`text-[10px] px-1.5 py-0.5 rounded border ${getStatusClass(task.status)} uppercase font-bold tracking-wider`}>
									{statusLabel(task.status)}
								</span>
							</td>
							<td class="px-4 py-2 text-right">
								{#if isFailed(task)}
									<button
										type="button"
										class="text-[10px] text-red-300 hover:text-red-200"
										on:click={() => onDismissTask(task)}
									>
										Dismiss
									</button>
								{/if}
							</td>
						</tr>
					{:else}
						<tr><td colspan="6" class="px-4 py-4 text-center text-gray-500">No matching tasks</td></tr>
					{/each}
				</tbody>
			</table>
		</div>
</div>

<style>
	@keyframes task-row-pulse {
		0% {
			box-shadow: inset 3px 0 0 var(--hub-task-accent);
		}
		100% {
			box-shadow: inset 0 0 0 var(--hub-task-accent), 0 0 8px var(--hub-task-accent);
		}
	}

	.running-task {
		animation: task-row-pulse 2s ease-in-out infinite;
	}
</style>
