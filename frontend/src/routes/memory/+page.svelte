<script lang="ts">
	import { onMount } from 'svelte';
	import ConfidenceSparkline from '$lib/components/ConfidenceSparkline.svelte';
	import SkillDetailDrawer from '$lib/components/SkillDetailDrawer.svelte';
	import {
		applyMemoryAction,
		getMemoryItem,
		getMemoryOverview,
		searchMemory,
		updateMemoryAnnotation,
		getSkillCandidateHypotheses,
		promoteSkillCandidateHypothesis,
		dismissSkillCandidateHypothesis,
		runConsolidation,
		getQuantSkillsStats,
		listSkills,
		getSkillOutcomes,
		type MemoryItem,
		type MemoryItemDetailResponse,
		type MemoryOverviewResponse,
		type MemorySearchResponse,
		type MemorySource,
		type MemorySourceHealth,
		type MemoryTimelineEntry,
		type MemoryView,
		type SkillCandidateHypothesis,
		type QuantSkillsStats,
		type SkillSummary,
		type SkillOutcomeEvent,
	} from '$lib/api';
	// Maintenance (forget / bulk-prune) lives in the memory client but isn't
	// re-exported from $lib/api, so import it directly from the module.
	import {
		getMemoryMaintenancePreview,
		runMemoryMaintenance,
		type MemoryMaintenancePreview,
	} from '$lib/api/memory';

	const SOURCE_OPTIONS: Array<{ value: MemorySource; label: string }> = [
		{ value: 'workspace', label: 'Workspace' },
		{ value: 'chroma', label: 'Chroma' },
		{ value: 'narratives', label: 'Narratives' },
	];

	const TIME_PRESETS = [
		{ value: 'all', label: 'All Time' },
		{ value: '24h', label: '24H' },
		{ value: '7d', label: '7D' },
		{ value: '30d', label: '30D' },
		{ value: '90d', label: '90D' },
	];

	const TIER_OPTIONS = [
		{ value: '', label: 'Untiered' },
		{ value: 'signal', label: 'Signal' },
		{ value: 'working', label: 'Working' },
		{ value: 'canon', label: 'Canon' },
	];

	let loading = true;
	let refreshing = false;
	let saving = false;
	let acting = false;
	let error: string | null = null;
	let statusMessage: string | null = null;

	let view: MemoryView = 'explore';
	let query = '';
	let selectedSources: MemorySource[] = ['workspace', 'chroma', 'narratives'];
	let selectedCollections: string[] = [];
	let includeHidden = false;
	let timePreset = 'all';
	let agentFilter = '';
	let strategyFilter = '';
	let tagsInput = '';

	let overview: MemoryOverviewResponse | null = null;
	let searchResponse: MemorySearchResponse | null = null;
	let selectedItem: MemoryItem | null = null;
	let detail: MemoryItemDetailResponse | null = null;

	// Quant Skills state — Phase 3 surface uses `/api/skills` (L1 metadata-only).
	// The drawer fetches L2 detail + history + outcomes on demand.
	const SKILL_OUTCOME_HYDRATION_CAP = 30;
	let skills: SkillSummary[] = [];
	let skillOutcomes: Record<string, SkillOutcomeEvent[]> = {};
	let drawerSkillName: string | null = null;
	let skillTypeFilter = '';
	let skillMinConfidence = 0;
	let skillsLoading = false;

	// Pipeline state
	let skillCandidates: SkillCandidateHypothesis[] = [];
	let quantStats: QuantSkillsStats | null = null;
	let consolidationRunning = false;
	let consolidationReport: Record<string, number> | null = null;
	let pipelineLoading = false;
	let promotingIds: string[] = [];
	let inspectorOpen = false;

	// Memory maintenance (forget / bulk-prune) state — lives in the pipeline tab.
	let maintenanceOlderThanDays = 30;
	let maintenancePreview: MemoryMaintenancePreview | null = null;
	let maintenancePreviewLoading = false;
	let maintenanceRunning = false;

	// In-app confirmation modal — replaces blocking window.confirm() so the
	// dialog matches the dark UI inside the Tauri webview.
	let confirmState: {
		title: string;
		body: string;
		confirmLabel: string;
		tone: 'amber' | 'rose';
		onConfirm: () => void | Promise<void>;
	} | null = null;

	function openConfirm(opts: {
		title: string;
		body: string;
		confirmLabel: string;
		tone: 'amber' | 'rose';
		onConfirm: () => void | Promise<void>;
	}): void {
		confirmState = opts;
	}

	async function acceptConfirm(): Promise<void> {
		const action = confirmState?.onConfirm;
		confirmState = null;
		if (action) await action();
	}

	function cancelConfirm(): void {
		confirmState = null;
	}

	let draftTitle = '';
	let draftTags = '';
	let draftNote = '';
	let draftTier = '';
	let draftPinned = false;
	let draftHidden = false;

	function sourceAccent(source: string | undefined): string {
		switch (source) {
			case 'narratives':
				return 'border-cyan-500/30 bg-cyan-500/10 text-cyan-200';
			case 'workspace':
				return 'border-amber-500/30 bg-amber-500/10 text-amber-200';
			default:
				return 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200';
		}
	}

	function sourceRailAccent(source: string | undefined): string {
		switch (source) {
			case 'narratives':
				return 'from-cyan-500/25 to-transparent';
			case 'workspace':
				return 'from-amber-500/25 to-transparent';
			default:
				return 'from-emerald-500/25 to-transparent';
		}
	}

	function formatTimestamp(value: string | null | undefined): string {
		if (!value) return '--';
		const date = new Date(value);
		if (Number.isNaN(date.getTime())) return '--';
		return date.toLocaleString([], {
			year: 'numeric',
			month: 'short',
			day: '2-digit',
			hour: '2-digit',
			minute: '2-digit',
		});
	}

	function relativeTime(value: string | null | undefined): string {
		if (!value) return '--';
		const date = new Date(value);
		if (Number.isNaN(date.getTime())) return '--';
		const diffMs = Date.now() - date.getTime();
		const diffMinutes = Math.max(1, Math.round(diffMs / 60000));
		if (diffMinutes < 60) return `${diffMinutes}m ago`;
		const diffHours = Math.round(diffMinutes / 60);
		if (diffHours < 48) return `${diffHours}h ago`;
		const diffDays = Math.round(diffHours / 24);
		return `${diffDays}d ago`;
	}

	function metricChipClass(value: number): string {
		if (value > 0) return 'border-[#2b2b2b] bg-[#111] text-white';
		return 'border-[#1d1d1d] bg-[#0b0b0b] text-gray-500';
	}

	function hasAction(item: MemoryItem | null | undefined, action: string): boolean {
		return Boolean(item?.actions?.includes(action));
	}

	function toggleSource(source: MemorySource): void {
		if (selectedSources.includes(source)) {
			selectedSources = selectedSources.filter((value) => value !== source);
			if (!selectedSources.length) selectedSources = [source];
			return;
		}
		selectedSources = [...selectedSources, source];
	}

	function toggleCollection(collection: string): void {
		if (selectedCollections.includes(collection)) {
			selectedCollections = selectedCollections.filter((value) => value !== collection);
			return;
		}
		selectedCollections = [...selectedCollections, collection];
	}

	function resetFilters(): void {
		query = '';
		selectedSources = ['workspace', 'chroma', 'narratives'];
		selectedCollections = [];
		includeHidden = false;
		timePreset = 'all';
		agentFilter = '';
		strategyFilter = '';
		tagsInput = '';
		searchResponse = null;
		void loadOverview();
	}

	function buildSearchPayload(cursor?: string | null) {
		return {
			query: query.trim(),
			sources: selectedSources,
			collections: selectedCollections,
			tags: tagsInput
				.split(/[,\s]+/)
				.map((tag) => tag.trim())
				.filter(Boolean),
			agent_id: agentFilter.trim(),
			strategy_id: strategyFilter.trim(),
			include_hidden: includeHidden,
			limit: 24,
			page: 1,
			cursor: cursor ?? null,
			time_range: timePreset === 'all' ? null : { preset: timePreset },
		};
	}

	function syncDrafts(payload: MemoryItemDetailResponse | null): void {
		const annotation = payload?.annotation;
		const item = payload?.item;
		draftTitle = String(annotation?.title_override ?? '').trim();
		draftTags = Array.isArray(annotation?.tags) ? annotation.tags.join(', ') : item?.tags?.join(', ') ?? '';
		draftNote = String(annotation?.note ?? item?.note ?? '').trim();
		draftTier = String(annotation?.tier ?? item?.tier ?? '').trim();
		draftPinned = Boolean(annotation?.pinned ?? item?.pinned);
		draftHidden = Boolean(annotation?.hidden ?? item?.hidden);
	}

	function collectionsFromState(): Array<{ name: string; label?: string; count?: number }> {
		const responseCollections = searchResponse?.available_collections ?? [];
		if (responseCollections.length > 0) return responseCollections;
		const chroma = overview?.source_health?.find((entry) => entry.source === 'chroma');
		return chroma?.collections ?? [];
	}

	function activeExploreItems(): MemoryItem[] {
		if (searchResponse?.results?.length) return searchResponse.results;
		return overview?.recent_items ?? [];
	}

	function activeCanonItems(): MemoryItem[] {
		return searchResponse?.canon_items ?? overview?.canon_items ?? [];
	}

	function activeTimeline(): MemoryTimelineEntry[] {
		return searchResponse?.timeline ?? overview?.timeline ?? [];
	}

	function hasActiveFilters(): boolean {
		return Boolean(
			query.trim()
			|| agentFilter.trim()
			|| strategyFilter.trim()
			|| tagsInput.trim()
			|| includeHidden
			|| timePreset !== 'all'
			|| selectedCollections.length > 0
			|| selectedSources.length !== SOURCE_OPTIONS.length,
		);
	}

	async function loadOverview(): Promise<void> {
		refreshing = true;
		error = null;
		try {
			overview = await getMemoryOverview(24);
			searchResponse = null;
		} catch (err) {
			error = err instanceof Error ? err.message : 'Failed to load Memory Bank.';
		} finally {
			loading = false;
			refreshing = false;
		}
	}

	async function runSearch(cursor?: string | null): Promise<void> {
		refreshing = true;
		error = null;
		try {
			const response = await searchMemory(buildSearchPayload(cursor));
			if (cursor && searchResponse) {
				searchResponse = {
					...response,
					results: [...searchResponse.results, ...response.results],
				};
			} else {
				searchResponse = response;
			}
		} catch (err) {
			error = err instanceof Error ? err.message : 'Memory search failed.';
		} finally {
			loading = false;
			refreshing = false;
		}
	}

	async function refreshActiveSurface(): Promise<void> {
		if (hasActiveFilters()) {
			await runSearch();
			return;
		}
		await loadOverview();
	}

	async function selectItem(item: MemoryItem): Promise<void> {
		selectedItem = item;
		inspectorOpen = true;
		detail = null;
		syncDrafts(null);
		try {
			detail = await getMemoryItem(item.source, item.source_id);
			selectedItem = detail.item;
			syncDrafts(detail);
		} catch (err) {
			error = err instanceof Error ? err.message : 'Failed to load memory detail.';
		}
	}

	async function saveAnnotation(): Promise<void> {
		if (!selectedItem) return;
		saving = true;
		statusMessage = null;
		try {
			detail = await updateMemoryAnnotation(selectedItem.source, selectedItem.source_id, {
				title_override: draftTitle || null,
				tags: draftTags
					.split(/[,\n]+/)
					.map((tag) => tag.trim())
					.filter(Boolean),
				note: draftNote || null,
				tier: draftTier || null,
				pinned: draftPinned,
				hidden: draftHidden,
				item_snapshot: selectedItem,
			});
			selectedItem = detail.item;
			syncDrafts(detail);
			statusMessage = 'Memory updated.';
			await refreshActiveSurface();
		} catch (err) {
			error = err instanceof Error ? err.message : 'Failed to save memory annotation.';
		} finally {
			saving = false;
		}
	}

	async function runItemAction(action: 'hide' | 'unhide'): Promise<void> {
		if (!selectedItem || acting) return;
		if (action === 'hide') {
			openConfirm({
				title: 'Hide memory',
				body: 'Hide this memory from default views? You can restore it later with Include hidden.',
				confirmLabel: 'Hide',
				tone: 'amber',
				onConfirm: () => performItemAction('hide'),
			});
			return;
		}
		await performItemAction(action);
	}

	async function performItemAction(action: 'hide' | 'unhide'): Promise<void> {
		if (!selectedItem || acting) return;
		acting = true;
		statusMessage = null;
		try {
			const response = await applyMemoryAction(selectedItem.source, selectedItem.source_id, {
				action,
				item_snapshot: selectedItem,
			});
			if (response.item) {
				selectedItem = response.item;
			}
			detail = await getMemoryItem(selectedItem.source, selectedItem.source_id);
			selectedItem = detail.item;
			syncDrafts(detail);
			statusMessage = action === 'hide' ? 'Memory hidden.' : 'Memory restored.';
			await refreshActiveSurface();
		} catch (err) {
			error = err instanceof Error ? err.message : `Failed to ${action} memory.`;
		} finally {
			acting = false;
		}
	}

	// ── Quant Skills loaders ────────────────────────────────────────────────
	async function loadSkills(): Promise<void> {
		skillsLoading = true;
		try {
			const res = await listSkills();
			let items = res.items ?? [];
			if (skillTypeFilter) items = items.filter((s) => s.type === skillTypeFilter);
			if (skillMinConfidence > 0)
				items = items.filter((s) => s.confidence >= skillMinConfidence);
			skills = items;
			// Fetch outcomes lazily for sparkline rendering — capped to keep this
			// snappy when the catalog grows past ~50 skills.
			void hydrateSkillOutcomes(items.slice(0, SKILL_OUTCOME_HYDRATION_CAP));
		} catch (err) {
			error = err instanceof Error ? err.message : 'Failed to load skills.';
		} finally {
			skillsLoading = false;
		}
	}

	async function hydrateSkillOutcomes(items: SkillSummary[]): Promise<void> {
		const next: Record<string, SkillOutcomeEvent[]> = { ...skillOutcomes };
		await Promise.all(
			items.map(async (s) => {
				if (next[s.name]) return;
				try {
					const res = await getSkillOutcomes(s.name, { limit: 12 });
					next[s.name] = res.items ?? [];
				} catch {
					next[s.name] = [];
				}
			})
		);
		skillOutcomes = next;
	}

	function openSkillDrawer(name: string): void {
		drawerSkillName = name;
	}

	function closeSkillDrawer(): void {
		drawerSkillName = null;
		// Outcome events may have changed if the operator approved a proposal —
		// reload the catalog to refresh confidence values + sparklines.
		void loadSkills();
	}

	// ── Pipeline loaders ────────────────────────────────────────────────────
	async function loadPipeline(): Promise<void> {
		pipelineLoading = true;
		try {
			const [hRes, sRes] = await Promise.all([getSkillCandidateHypotheses(), getQuantSkillsStats()]);
			skillCandidates = hRes.hypotheses ?? [];
			quantStats = sRes;
		} catch (err) {
			error = err instanceof Error ? err.message : 'Failed to load pipeline.';
		} finally {
			pipelineLoading = false;
		}
	}

	function handlePromoteSkillCandidate(id: string): void {
		if (promotingIds.includes(id)) return;
		openConfirm({
			title: 'Promote skill candidate',
			body: `Promote skill candidate "${id}" to a learned skill?`,
			confirmLabel: 'Promote',
			tone: 'amber',
			onConfirm: () => performPromoteSkillCandidate(id),
		});
	}

	async function performPromoteSkillCandidate(id: string): Promise<void> {
		if (promotingIds.includes(id)) return;
		promotingIds = [...promotingIds, id];
		try {
			await promoteSkillCandidateHypothesis(id);
			statusMessage = `Skill candidate ${id} promoted to skill.`;
			await loadPipeline();
		} catch (err) {
			error = err instanceof Error ? err.message : 'Failed to promote hypothesis.';
		} finally {
			promotingIds = promotingIds.filter((value) => value !== id);
		}
	}

	function handleDismissSkillCandidate(id: string): void {
		openConfirm({
			title: 'Dismiss skill candidate',
			body: `Dismiss skill candidate "${id}"? This cannot be undone.`,
			confirmLabel: 'Dismiss',
			tone: 'rose',
			onConfirm: () => performDismissSkillCandidate(id),
		});
	}

	async function performDismissSkillCandidate(id: string): Promise<void> {
		try {
			await dismissSkillCandidateHypothesis(id);
			statusMessage = `Skill candidate ${id} dismissed.`;
			await loadPipeline();
		} catch (err) {
			error = err instanceof Error ? err.message : 'Failed to dismiss hypothesis.';
		}
	}

	// ── Memory maintenance (forget / bulk-prune) ────────────────────────────
	async function loadMaintenancePreview(): Promise<void> {
		maintenancePreviewLoading = true;
		error = null;
		try {
			maintenancePreview = await getMemoryMaintenancePreview({
				older_than_days: maintenanceOlderThanDays,
			});
		} catch (err) {
			error = err instanceof Error ? err.message : 'Failed to preview memory maintenance.';
		} finally {
			maintenancePreviewLoading = false;
		}
	}

	function handleRunMaintenance(): void {
		const reduction = maintenancePreview?.summary?.estimated_visible_reduction ?? 0;
		openConfirm({
			title: 'Prune stale memory',
			body: `Compact daily logs and hide stale memory older than ${maintenanceOlderThanDays} days${reduction ? ` (~${reduction} visible items affected)` : ''}? Hidden items remain recoverable via Include hidden.`,
			confirmLabel: 'Prune',
			tone: 'amber',
			onConfirm: performRunMaintenance,
		});
	}

	async function performRunMaintenance(): Promise<void> {
		maintenanceRunning = true;
		statusMessage = null;
		try {
			maintenancePreview = await runMemoryMaintenance({
				dry_run: false,
				compact_daily_logs: true,
				hide_old_daily_logs: true,
				older_than_days: maintenanceOlderThanDays,
			});
			const hidden = maintenancePreview?.applied?.daily_file_items_hidden ?? 0;
			statusMessage = `Maintenance complete. ${hidden} item${hidden === 1 ? '' : 's'} hidden.`;
			await loadPipeline();
		} catch (err) {
			error = err instanceof Error ? err.message : 'Memory maintenance failed.';
		} finally {
			maintenanceRunning = false;
		}
	}

	async function handleRunConsolidation(): Promise<void> {
		consolidationRunning = true;
		try {
			const res = await runConsolidation();
			consolidationReport = res.report;
			statusMessage = 'Consolidation complete.';
			await loadPipeline();
		} catch (err) {
			error = err instanceof Error ? err.message : 'Consolidation failed.';
		} finally {
			consolidationRunning = false;
		}
	}

	// ── View switching ──────────────────────────────────────────────────────
	function switchView(v: MemoryView) {
		view = v;
		if (v === 'skills') void loadSkills();
		if (v === 'pipeline') void loadPipeline();
	}

	onMount(() => {
		void loadOverview();
	});

	$: totalVisible = overview?.metrics?.visible_count ?? searchResponse?.metrics?.visible_count ?? 0;
	$: canonCount = overview?.metrics?.canon_count ?? searchResponse?.metrics?.canon_count ?? 0;
	$: hiddenCount = overview?.metrics?.hidden_count ?? searchResponse?.metrics?.hidden_count ?? 0;
	$: sourceHealth = searchResponse?.source_health ?? overview?.source_health ?? [];
	$: collectionOptions = collectionsFromState();
	$: skillOutcomesCapped = skills.length > SKILL_OUTCOME_HYDRATION_CAP;
	// The Filters rail only drives memory search; on skills/pipeline it is noise.
	$: isSearchView = view === 'explore' || view === 'canon' || view === 'timeline';
</script>

<svelte:head>
	<title>Memory | Axiom</title>
</svelte:head>

<div class="min-h-full bg-[#050505] text-gray-100">
	<section class="border-b border-[#1b1b1b] bg-[radial-gradient(circle_at_top_left,_rgba(34,211,238,0.12),_transparent_24%),radial-gradient(circle_at_top_right,_rgba(245,158,11,0.1),_transparent_24%),radial-gradient(circle_at_bottom,_rgba(16,185,129,0.1),_transparent_28%),linear-gradient(180deg,_#090909,_#050505)]">
		<div class="px-4 py-5 md:px-6">
			<div class="flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
				<div class="space-y-2">
					<div class="inline-flex items-center gap-2 rounded-full border border-[#242424] bg-black/40 px-3 py-1 text-[10px] font-semibold uppercase tracking-[0.24em] text-gray-400 backdrop-blur">
						<span class="h-1.5 w-1.5 rounded-full bg-cyan-400"></span>
						Management / Memory Bank
					</div>
					<div>
						<h1 class="text-3xl font-semibold tracking-tight text-white md:text-4xl">Memory Bank</h1>
						<p class="mt-2 max-w-3xl text-sm leading-6 text-gray-400">
							A provenance-first command center for quant skills, agent narratives, Chroma research memory, and workspace logs.
							Curate what matters, hide what doesn't, and keep the system's memory visible instead of mysterious.
						</p>
					</div>
				</div>
				<div class="grid grid-cols-2 gap-2 md:grid-cols-4">
					<div class={`rounded-2xl border px-3 py-3 ${metricChipClass(totalVisible)}`}>
						<div class="text-[10px] uppercase tracking-[0.22em] text-gray-500">Visible</div>
						<div class="mt-2 text-2xl font-semibold text-white">{totalVisible}</div>
					</div>
					<div class={`rounded-2xl border px-3 py-3 ${metricChipClass(canonCount)}`}>
						<div class="text-[10px] uppercase tracking-[0.22em] text-gray-500">Canon</div>
						<div class="mt-2 text-2xl font-semibold text-white">{canonCount}</div>
					</div>
					<div class={`rounded-2xl border px-3 py-3 ${metricChipClass(hiddenCount)}`}>
						<div class="text-[10px] uppercase tracking-[0.22em] text-gray-500">Hidden</div>
						<div class="mt-2 text-2xl font-semibold text-white">{hiddenCount}</div>
					</div>
					<div class={`rounded-2xl border px-3 py-3 ${metricChipClass(sourceHealth.length)}`}>
						<div class="text-[10px] uppercase tracking-[0.22em] text-gray-500">Sources</div>
						<div class="mt-2 text-2xl font-semibold text-white">{sourceHealth.length}</div>
					</div>
				</div>
			</div>

			<div class="mt-5 grid gap-3 lg:grid-cols-[minmax(0,1fr)_auto]">
				<div class="relative overflow-hidden rounded-2xl border border-[#232323] bg-black/40 backdrop-blur">
					<div class="absolute inset-0 opacity-40" style="background-image: linear-gradient(rgba(255,255,255,0.04) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.04) 1px, transparent 1px); background-size: 20px 20px;"></div>
					<div class="relative flex flex-col gap-3 p-4">
						<div class="flex flex-col gap-3 lg:flex-row lg:items-center">
							<div class="min-w-0 flex-1">
								<label for="memory-search" class="mb-1 block text-[10px] font-semibold uppercase tracking-[0.24em] text-gray-500">Search Memory</label>
								<input
									id="memory-search"
									bind:value={query}
									class="w-full rounded-xl border border-[#2b2b2b] bg-[#090909] px-4 py-3 text-sm text-white outline-none transition focus:border-cyan-400/70"
									placeholder="drawdown regime, post-mortem, slippage pattern, lesson learned..."
									on:keydown={(event) => {
										if (event.key === 'Enter') {
											void refreshActiveSurface();
										}
									}}
								/>
							</div>
							<div class="flex items-center gap-2">
								<button
									type="button"
									on:click={() => refreshActiveSurface()}
									disabled={refreshing}
									class="rounded-xl border border-cyan-500/40 bg-cyan-500/10 px-4 py-3 text-xs font-semibold uppercase tracking-[0.22em] text-cyan-200 transition hover:border-cyan-300 hover:text-white disabled:opacity-50"
								>
									{refreshing ? 'Loading' : 'Search'}
								</button>
								<button
									type="button"
									on:click={resetFilters}
									class="rounded-xl border border-[#2c2c2c] bg-[#0a0a0a] px-4 py-3 text-xs font-semibold uppercase tracking-[0.22em] text-gray-300 transition hover:border-white hover:text-white"
								>
									Reset
								</button>
							</div>
						</div>
						<div class="flex flex-wrap items-center gap-2">
							{#each SOURCE_OPTIONS as source}
								<button
									type="button"
									on:click={() => toggleSource(source.value)}
									class={`rounded-full border px-3 py-1.5 text-[10px] font-semibold uppercase tracking-[0.22em] transition ${selectedSources.includes(source.value) ? sourceAccent(source.value) : 'border-[#2a2a2a] bg-[#0b0b0b] text-gray-500 hover:border-white hover:text-white'}`}
								>
									{source.label}
								</button>
							{/each}
							{#each TIME_PRESETS as preset}
								<button
									type="button"
									on:click={() => (timePreset = preset.value)}
									class={`rounded-full border px-3 py-1.5 text-[10px] font-semibold uppercase tracking-[0.22em] transition ${timePreset === preset.value ? 'border-white bg-white text-black' : 'border-[#2a2a2a] bg-[#0b0b0b] text-gray-500 hover:border-white hover:text-white'}`}
								>
									{preset.label}
								</button>
							{/each}
						</div>
					</div>
				</div>

				<div class="flex items-end gap-2">
					{#each ['explore', 'skills', 'pipeline', 'canon', 'timeline'] as tab}
						<button
							type="button"
							on:click={() => switchView(tab as MemoryView)}
							class={`rounded-xl border px-4 py-3 text-xs font-semibold uppercase tracking-[0.22em] transition ${view === tab ? 'border-white bg-white text-black' : 'border-[#2a2a2a] bg-[#090909] text-gray-400 hover:border-white hover:text-white'}`}
						>
							{tab}
						</button>
					{/each}
				</div>
			</div>
		</div>
	</section>

	<div class="px-4 py-4 md:px-6">
		{#if error}
			<div class="mb-4 rounded-2xl border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-200">{error}</div>
		{/if}
		{#if statusMessage}
			<div class="mb-4 rounded-2xl border border-emerald-500/30 bg-emerald-500/10 px-4 py-3 text-sm text-emerald-200">{statusMessage}</div>
		{/if}

		<div class="grid gap-4 lg:grid-cols-[280px_minmax(0,1fr)_360px]">
			<aside class="space-y-4">
				{#if isSearchView}
					<div class="rounded-2xl border border-[#202020] bg-[#090909] p-4">
						<div class="text-[10px] font-semibold uppercase tracking-[0.24em] text-gray-500">Filters</div>
						<div class="mt-4 space-y-4">
							<div>
								<label for="memory-agent-filter" class="mb-1 block text-[10px] font-semibold uppercase tracking-[0.22em] text-gray-500">Agent</label>
								<input id="memory-agent-filter" bind:value={agentFilter} class="w-full rounded-xl border border-[#2a2a2a] bg-black px-3 py-2 text-sm text-white outline-none transition focus:border-cyan-400/70" placeholder="brain, quant-researcher" />
							</div>
							<div>
								<label for="memory-strategy-filter" class="mb-1 block text-[10px] font-semibold uppercase tracking-[0.22em] text-gray-500">Strategy</label>
								<input id="memory-strategy-filter" bind:value={strategyFilter} class="w-full rounded-xl border border-[#2a2a2a] bg-black px-3 py-2 text-sm text-white outline-none transition focus:border-cyan-400/70" placeholder="S00225" />
							</div>
							<div>
								<label for="memory-tags-filter" class="mb-1 block text-[10px] font-semibold uppercase tracking-[0.22em] text-gray-500">Tags</label>
								<input id="memory-tags-filter" bind:value={tagsInput} class="w-full rounded-xl border border-[#2a2a2a] bg-black px-3 py-2 text-sm text-white outline-none transition focus:border-cyan-400/70" placeholder="postmortem, lesson, failure" />
							</div>
							<div class="rounded-xl border border-[#1f1f1f] bg-black/60 p-3">
								<div class="mb-2 text-[10px] font-semibold uppercase tracking-[0.22em] text-gray-500">Chroma Collections</div>
								<div class="flex flex-wrap gap-2">
									{#each collectionOptions as collection}
										<button
											type="button"
											on:click={() => toggleCollection(collection.name)}
											class={`rounded-full border px-3 py-1.5 text-[10px] font-semibold uppercase tracking-[0.18em] transition ${selectedCollections.includes(collection.name) ? 'border-emerald-400/60 bg-emerald-500/10 text-emerald-200' : 'border-[#2a2a2a] bg-[#0a0a0a] text-gray-500 hover:border-white hover:text-white'}`}
										>
											{collection.label ?? collection.name}
											{#if collection.count !== undefined}
												<span class="ml-1 text-[9px] opacity-70">{collection.count}</span>
											{/if}
										</button>
									{/each}
								</div>
							</div>
							<label class="flex items-center justify-between rounded-xl border border-[#1f1f1f] bg-black/60 px-3 py-2 text-sm text-gray-300">
								<span>Include hidden</span>
								<input bind:checked={includeHidden} type="checkbox" class="h-4 w-4 rounded border-[#333] bg-black text-cyan-400 focus:ring-cyan-400" />
							</label>
						</div>
					</div>
				{:else}
					<div class="rounded-2xl border border-[#202020] bg-[#090909] p-4">
						<div class="text-[10px] font-semibold uppercase tracking-[0.24em] text-gray-500">{view === 'skills' ? 'Skills' : 'Pipeline'}</div>
						<p class="mt-3 text-sm leading-6 text-gray-400">
							{#if view === 'skills'}
								Type and confidence filters for the skills catalog live in the panel on the right. Search filters apply to memory records only.
							{:else}
								The skill-candidate pipeline, consolidation, and memory maintenance controls live in the panel on the right. Search filters apply to memory records only.
							{/if}
						</p>
					</div>
				{/if}

				<div class="rounded-2xl border border-[#202020] bg-[#090909] p-4">
					<div class="text-[10px] font-semibold uppercase tracking-[0.24em] text-gray-500">Source Health</div>
					<div class="mt-4 space-y-3">
						{#each sourceHealth as source}
							<div class="overflow-hidden rounded-xl border border-[#1f1f1f] bg-black/60">
								<div class={`h-1 bg-gradient-to-r ${sourceRailAccent(source.source)}`}></div>
								<div class="p-3">
									<div class="flex items-center justify-between gap-2">
										<span class={`inline-flex items-center gap-2 rounded-full border px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.2em] ${sourceAccent(source.source)}`}>
											<span class={`h-1.5 w-1.5 rounded-full ${source.healthy ? 'bg-current' : 'bg-rose-300'}`}></span>
											{source.source}
										</span>
										<span class="text-[10px] uppercase tracking-[0.18em] text-gray-500">{source.status}</span>
									</div>
									<p class="mt-2 text-sm text-gray-300">{source.summary}</p>
									<div class="mt-2 text-[11px] text-gray-500">
										{#if source.count !== undefined}
											<span>{source.count} indexed</span>
										{/if}
										{#if source.latest_updated_at}
											<span class="ml-2">{relativeTime(source.latest_updated_at)}</span>
										{/if}
									</div>
								</div>
							</div>
						{/each}
					</div>
				</div>
			</aside>

			<section class="min-w-0 space-y-4">
				{#if view === 'skills'}
					<!-- Skills View -->
					<div class="rounded-2xl border border-[#1f1f1f] bg-[#090909] p-4">
						<div class="mb-4 flex flex-wrap items-center gap-3">
							<select bind:value={skillTypeFilter} on:change={() => loadSkills()} class="rounded-lg border border-[#2a2a2a] bg-black px-3 py-2 text-xs text-gray-300">
								<option value="">All Types</option>
								<option value="regime">Regime</option>
								<option value="failure">Failure</option>
								<option value="indicator">Indicator</option>
								<option value="combo">Combo</option>
								<option value="params">Params</option>
							</select>
							<label class="flex items-center gap-2 text-xs text-gray-400">
								Min Confidence:
								<input type="range" min="0" max="1" step="0.1" bind:value={skillMinConfidence} on:change={() => loadSkills()} class="w-24" />
								<span class="text-white">{Math.round(skillMinConfidence * 100)}%</span>
							</label>
							<span class="ml-auto text-xs text-gray-500">{skills.length} skill{skills.length !== 1 ? 's' : ''}</span>
						</div>
						{#if skillsLoading}
							<div class="grid gap-3 md:grid-cols-2">
								{#each Array(4) as _}
									<div class="animate-pulse rounded-xl border border-[#1b1b1b] bg-black/40 p-4">
										<div class="flex items-center justify-between gap-2">
											<div class="h-4 w-16 rounded bg-[#151515]"></div>
											<div class="h-3 w-12 rounded bg-[#101010]"></div>
										</div>
										<div class="mt-3 h-4 w-2/3 rounded bg-[#151515]"></div>
										<div class="mt-4 h-1.5 w-full rounded-full bg-[#151515]"></div>
									</div>
								{/each}
							</div>
						{:else}
						{#if skillOutcomesCapped}
							<div class="mb-3 rounded-xl border border-amber-500/20 bg-amber-500/5 px-3 py-2 text-[11px] text-amber-200/80">
								Showing outcome sparklines for the first {SKILL_OUTCOME_HYDRATION_CAP} skills; the rest render without trend history. Narrow by type or confidence to inspect them.
							</div>
						{/if}
						<div class="grid gap-3 md:grid-cols-2">
							{#each skills as skill}
								<button type="button" on:click={() => openSkillDrawer(skill.name)} class="rounded-xl border border-[#1f1f1f] bg-black/60 p-4 text-left transition hover:border-white/30 hover:bg-[#0e0e0e]">
									<div class="flex items-center justify-between gap-2">
										<span class="inline-flex rounded-full border border-purple-500/30 bg-purple-500/10 px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.2em] text-purple-200">{skill.type}</span>
										<span class="text-[10px] text-gray-500">v{skill.version} · n={skill.samples}</span>
									</div>
									<div class="mt-2 text-sm font-semibold text-white">{skill.name}</div>
									<div class="mt-3 flex items-center gap-2">
										<div class="h-1.5 flex-1 rounded-full bg-[#1a1a1a]">
											<div class="h-full rounded-full {skill.confidence > 0.7 ? 'bg-emerald-500' : skill.confidence > 0.4 ? 'bg-amber-500' : 'bg-rose-500'}" style="width: {Math.round(skill.confidence * 100)}%"></div>
										</div>
										<span class="text-[10px] font-semibold {skill.confidence > 0.7 ? 'text-emerald-300' : skill.confidence > 0.4 ? 'text-amber-300' : 'text-rose-300'}">{Math.round(skill.confidence * 100)}%</span>
									</div>
									<div class="mt-3 flex items-center justify-between gap-2">
										{#if skill.regime}
											<span class="inline-flex rounded-full border border-cyan-500/20 bg-cyan-500/5 px-2 py-0.5 text-[10px] text-cyan-300">{skill.regime}</span>
										{:else}
											<span></span>
										{/if}
										<ConfidenceSparkline outcomes={skillOutcomes[skill.name] ?? []} currentConfidence={skill.confidence} />
									</div>
								</button>
							{:else}
								<div class="col-span-2 py-8 text-center text-sm text-gray-500">No skills yet. Skills are created automatically from backtest results.</div>
							{/each}
						</div>
						{/if}
					</div>
				{:else if view === 'pipeline'}
					<!-- Pipeline View -->
					<div class="space-y-4">
						<div class="rounded-2xl border border-[#1f1f1f] bg-[#090909] p-4">
							<div class="mb-3 flex items-start justify-between gap-3">
								<div>
									<h2 class="text-sm font-semibold uppercase tracking-[0.22em] text-gray-300" title="Learned-skill candidates from Gauntlet runs — distinct from the Hypotheses page, which tracks trading hypotheses.">Skill Candidates</h2>
									<p class="mt-1 text-xs text-gray-500">Learned-skill candidates from Gauntlet runs. Not the same as the Hypotheses page (trading hypotheses).</p>
								</div>
								<span class="whitespace-nowrap text-xs text-gray-500">{skillCandidates.length} pending</span>
							</div>
							{#if pipelineLoading}
								<div class="space-y-3">
									{#each Array(3) as _}
										<div class="animate-pulse rounded-xl border border-[#1b1b1b] bg-black/40 p-4">
											<div class="flex items-center justify-between gap-2">
												<div class="h-3 w-32 rounded bg-[#151515]"></div>
												<div class="h-3 w-16 rounded bg-[#101010]"></div>
											</div>
											<div class="mt-3 h-3 w-full rounded bg-[#101010]"></div>
											<div class="mt-2 h-3 w-4/5 rounded bg-[#101010]"></div>
										</div>
									{/each}
								</div>
							{:else}
							{#each skillCandidates as h}
								<div class="mb-3 rounded-xl border border-[#1f1f1f] bg-black/60 p-4">
									<div class="flex items-center justify-between gap-2">
										<span class="text-xs font-semibold text-gray-200">{h.pattern}</span>
										<span class="text-[10px] text-gray-500">{h.id}</span>
									</div>
									<p class="mt-2 text-sm text-gray-400">{h.observation}</p>
									<div class="mt-3 flex items-center gap-3">
										<div class="flex items-center gap-2 flex-1">
											<div class="h-1.5 w-24 rounded-full bg-[#1a1a1a]">
												<div class="h-full rounded-full bg-cyan-500" style="width: {Math.round((h.count / 3) * 100)}%"></div>
											</div>
											<span class="text-[10px] font-semibold text-cyan-300">{h.count}/3</span>
										</div>
										<button type="button" on:click={() => handlePromoteSkillCandidate(h.id)} disabled={promotingIds.includes(h.id)} class="rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-3 py-1.5 text-[10px] font-semibold uppercase tracking-[0.18em] text-emerald-200 hover:border-emerald-300 disabled:opacity-50">{promotingIds.includes(h.id) ? 'Promoting...' : 'Promote'}</button>
										<button type="button" on:click={() => handleDismissSkillCandidate(h.id)} disabled={promotingIds.includes(h.id)} class="rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-1.5 text-[10px] font-semibold uppercase tracking-[0.18em] text-rose-200 hover:border-rose-300 disabled:opacity-50">Dismiss</button>
									</div>
									<div class="mt-2 text-[10px] text-gray-600">Gauntlet runs: {h.backtest_ids.join(', ') || 'none'}</div>
								</div>
							{:else}
								<div class="py-6 text-center text-sm text-gray-500">No pending skill candidates. Created automatically from Gauntlet results.</div>
							{/each}
							{/if}
						</div>
						<div class="rounded-2xl border border-[#1f1f1f] bg-[#090909] p-4">
							<div class="mb-3 flex items-center justify-between">
								<h2 class="text-sm font-semibold uppercase tracking-[0.22em] text-gray-300">Consolidation</h2>
								<button type="button" on:click={handleRunConsolidation} disabled={consolidationRunning} class="rounded-lg border border-[#2c2c2c] bg-black px-3 py-1.5 text-[10px] font-semibold uppercase tracking-[0.18em] text-gray-300 hover:border-white hover:text-white disabled:opacity-50">
									{consolidationRunning ? 'Running...' : 'Run Now'}
								</button>
							</div>
							{#if consolidationReport}
								<div class="flex flex-wrap gap-4 text-sm">
									<span class="text-gray-400">Archived: <span class="text-white">{consolidationReport.archived ?? 0}</span></span>
									<span class="text-gray-400">Stale: <span class="text-white">{consolidationReport.stale_flagged ?? 0}</span></span>
									<span class="text-gray-400">Pruned: <span class="text-white">{consolidationReport.hypotheses_pruned ?? 0}</span></span>
								</div>
							{:else}
								<p class="text-xs text-gray-500">Archives low-confidence skills, flags stale ones, prunes old hypotheses.</p>
							{/if}
						</div>

						<div class="rounded-2xl border border-[#1f1f1f] bg-[#090909] p-4">
							<div class="mb-3 flex flex-wrap items-center justify-between gap-2">
								<div>
									<h2 class="text-sm font-semibold uppercase tracking-[0.22em] text-gray-300">Memory Maintenance</h2>
									<p class="mt-1 text-xs text-gray-500">Compact old daily logs and hide stale workspace memory. Hidden items stay recoverable via Include hidden.</p>
								</div>
								<label class="flex items-center gap-2 text-xs text-gray-400">
									Older than
									<input type="number" min="1" max="365" bind:value={maintenanceOlderThanDays} class="w-16 rounded-lg border border-[#2a2a2a] bg-black px-2 py-1 text-xs text-white outline-none transition focus:border-cyan-400/70" />
									days
								</label>
							</div>
							<div class="flex flex-wrap items-center gap-2">
								<button type="button" on:click={loadMaintenancePreview} disabled={maintenancePreviewLoading || maintenanceRunning} class="rounded-lg border border-[#2c2c2c] bg-black px-3 py-1.5 text-[10px] font-semibold uppercase tracking-[0.18em] text-gray-300 hover:border-white hover:text-white disabled:opacity-50">
									{maintenancePreviewLoading ? 'Previewing...' : 'Preview'}
								</button>
								<button type="button" on:click={handleRunMaintenance} disabled={maintenanceRunning || maintenancePreviewLoading || !maintenancePreview} class="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-1.5 text-[10px] font-semibold uppercase tracking-[0.18em] text-amber-200 hover:border-amber-300 disabled:opacity-50">
									{maintenanceRunning ? 'Pruning...' : 'Prune Now'}
								</button>
							</div>
							{#if maintenancePreview}
								<div class="mt-3 flex flex-wrap gap-4 text-sm">
									<span class="text-gray-400">Logs to compact: <span class="text-white">{maintenancePreview.summary.daily_log_files_to_compact}</span></span>
									<span class="text-gray-400">Items to hide: <span class="text-white">{maintenancePreview.summary.daily_file_items_to_hide}</span></span>
									<span class="text-gray-400">Est. visible reduction: <span class="text-white">{maintenancePreview.summary.estimated_visible_reduction}</span></span>
									{#if maintenancePreview.applied}
										<span class="text-gray-400">Hidden: <span class="text-white">{maintenancePreview.applied.daily_file_items_hidden}</span></span>
									{/if}
								</div>
								{#if !maintenancePreview.applied}
									<p class="mt-2 text-[11px] text-gray-600">Preview only — nothing is changed until you press Prune Now.</p>
								{/if}
							{:else}
								<p class="mt-3 text-xs text-gray-500">Press Preview to see what would be compacted or hidden before running.</p>
							{/if}
						</div>
						{#if quantStats}
							<div class="rounded-2xl border border-[#1f1f1f] bg-[#090909] p-4">
								<h2 class="mb-3 text-sm font-semibold uppercase tracking-[0.22em] text-gray-300">Stats</h2>
								<div class="grid grid-cols-2 gap-4 md:grid-cols-5">
									<div class="text-center"><div class="text-2xl font-semibold text-white">{quantStats.total_skills}</div><div class="text-[10px] uppercase tracking-[0.2em] text-gray-500">Skills</div></div>
									<div class="text-center"><div class="text-2xl font-semibold text-white">{quantStats.total_hypotheses}</div><div class="text-[10px] uppercase tracking-[0.2em] text-gray-500">Skill Candidates</div></div>
									<div class="text-center"><div class="text-2xl font-semibold text-white">{quantStats.total_archived}</div><div class="text-[10px] uppercase tracking-[0.2em] text-gray-500">Archived</div></div>
									<div class="text-center"><div class="text-2xl font-semibold {quantStats.avg_confidence > 0.7 ? 'text-emerald-300' : quantStats.avg_confidence > 0.4 ? 'text-amber-300' : 'text-rose-300'}">{Math.round(quantStats.avg_confidence * 100)}%</div><div class="text-[10px] uppercase tracking-[0.2em] text-gray-500">Avg Conf</div></div>
									<div class="text-center"><div class="text-2xl font-semibold text-white">{quantStats.total_evidence}</div><div class="text-[10px] uppercase tracking-[0.2em] text-gray-500">Evidence</div></div>
								</div>
							</div>
						{/if}
					</div>
				{:else if view === 'explore' && !hasActiveFilters() && overview?.curation_candidates?.length}
					<div class="rounded-2xl border border-[#1f1f1f] bg-[#090909] p-4">
						<div class="mb-3 flex items-center justify-between gap-3">
							<div>
								<h2 class="text-sm font-semibold uppercase tracking-[0.22em] text-gray-300">Needs Curation</h2>
								<p class="mt-1 text-xs text-gray-500">Recent memory that still needs a note, tags, or a canon decision.</p>
							</div>
						</div>
						<div class="grid gap-3 md:grid-cols-2">
							{#each overview.curation_candidates as candidate}
								<button type="button" on:click={() => selectItem(candidate)} class="rounded-xl border border-[#1f1f1f] bg-black/60 p-4 text-left transition hover:border-white/30 hover:bg-[#0e0e0e]">
									<div class="flex items-center justify-between gap-3">
										<span class={`inline-flex rounded-full border px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.2em] ${sourceAccent(candidate.source)}`}>{candidate.source}</span>
										<span class="text-[10px] uppercase tracking-[0.18em] text-gray-500">{relativeTime(candidate.updated_at ?? candidate.created_at)}</span>
									</div>
									<div class="mt-3 text-sm font-semibold text-white">{candidate.title}</div>
									<p class="mt-2 text-sm leading-6 text-gray-400">{candidate.excerpt}</p>
								</button>
							{/each}
						</div>
					</div>
				{/if}

				<div class="rounded-2xl border border-[#1f1f1f] bg-[#090909]">
					<div class="border-b border-[#1f1f1f] px-4 py-3">
						<div class="flex flex-wrap items-center justify-between gap-3">
							<div>
								<h2 class="text-sm font-semibold uppercase tracking-[0.22em] text-gray-300">
									{view === 'explore' ? 'Explore' : view === 'canon' ? 'Canon' : 'Timeline'}
								</h2>
								<p class="mt-1 text-xs text-gray-500">
									{#if view === 'explore'}
										{hasActiveFilters() ? `${searchResponse?.total ?? 0} matching memory records` : 'Recent memory across every active source'}
									{:else if view === 'canon'}
										Promoted, pinned, and operator-approved memories
									{:else}
										Recent memory events, observations, and curation actions
									{/if}
								</p>
							</div>
							{#if view === 'explore' && searchResponse?.next_cursor}
								<button type="button" on:click={() => runSearch(searchResponse?.next_cursor ?? null)} class="rounded-xl border border-[#2c2c2c] bg-black px-3 py-2 text-[10px] font-semibold uppercase tracking-[0.2em] text-gray-300 transition hover:border-white hover:text-white">
									Load More
								</button>
							{/if}
						</div>
					</div>

					<div class="divide-y divide-[#151515]">
						{#if loading}
							<div class="space-y-3 p-4">
								{#each Array(4) as _}
									<div class="animate-pulse rounded-2xl border border-[#1b1b1b] bg-black/40 p-4">
										<div class="h-3 w-28 rounded bg-[#151515]"></div>
										<div class="mt-3 h-4 w-2/3 rounded bg-[#151515]"></div>
										<div class="mt-3 h-3 w-full rounded bg-[#101010]"></div>
										<div class="mt-2 h-3 w-5/6 rounded bg-[#101010]"></div>
									</div>
								{/each}
							</div>
						{:else if view === 'timeline'}
							{#each activeTimeline() as entry}
								<button
									type="button"
									on:click={() => entry.item && selectItem(entry.item)}
									class="w-full px-4 py-4 text-left transition hover:bg-[#0d0d0d]"
								>
									<div class="flex flex-wrap items-center justify-between gap-2">
										<div class="flex items-center gap-2">
											<span class={`inline-flex rounded-full border px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.2em] ${sourceAccent(entry.source)}`}>{entry.source ?? entry.kind}</span>
											<span class="text-[10px] uppercase tracking-[0.18em] text-gray-500">{entry.action}</span>
										</div>
										<span class="text-[10px] uppercase tracking-[0.18em] text-gray-500">{formatTimestamp(entry.timestamp)}</span>
									</div>
									<div class="mt-2 text-sm font-semibold text-white">{entry.summary ?? entry.item?.title ?? 'Memory event'}</div>
									{#if entry.item}
										<p class="mt-2 text-sm leading-6 text-gray-400">{entry.item.excerpt}</p>
									{/if}
								</button>
							{/each}
						{:else}
							{#each (view === 'canon' ? activeCanonItems() : activeExploreItems()) as item}
								<button
									type="button"
									on:click={() => selectItem(item)}
									class={`group relative w-full overflow-hidden px-4 py-4 text-left transition hover:bg-[#0d0d0d] ${selectedItem?.source === item.source && selectedItem?.source_id === item.source_id ? 'bg-[#0d0d0d]' : ''}`}
								>
									<div class={`absolute inset-y-0 left-0 w-1 bg-gradient-to-b ${sourceRailAccent(item.source)}`}></div>
									<div class="flex flex-wrap items-start justify-between gap-3 pl-2">
										<div class="min-w-0 flex-1">
											<div class="flex flex-wrap items-center gap-2">
												<span class={`inline-flex rounded-full border px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.2em] ${sourceAccent(item.source)}`}>{item.source}</span>
												{#if item.collection}
													<span class="rounded-full border border-[#222] bg-[#0c0c0c] px-2 py-1 text-[10px] uppercase tracking-[0.18em] text-gray-500">{item.collection}</span>
												{/if}
												{#if item.pinned}
													<span class="rounded-full border border-white/20 bg-white/10 px-2 py-1 text-[10px] uppercase tracking-[0.18em] text-white">Pinned</span>
												{/if}
												{#if item.tier}
													<span class="rounded-full border border-[#333] bg-[#111] px-2 py-1 text-[10px] uppercase tracking-[0.18em] text-gray-300">{item.tier}</span>
												{/if}
											</div>
											<div class="mt-3 flex flex-wrap items-start justify-between gap-3">
												<div class="min-w-0 flex-1">
													<h3 class="truncate text-base font-semibold text-white">{item.title}</h3>
													<p class="mt-2 text-sm leading-6 text-gray-400">{item.excerpt}</p>
												</div>
												<div class="text-right text-[10px] uppercase tracking-[0.18em] text-gray-500">
													<div>{relativeTime(item.updated_at ?? item.created_at)}</div>
													{#if item.strategy_id}<div class="mt-1">{item.strategy_id}</div>{/if}
												</div>
											</div>
											{#if item.tags?.length}
												<div class="mt-3 flex flex-wrap gap-2">
													{#each item.tags.slice(0, 6) as tag}
														<span class="rounded-full border border-[#1f1f1f] bg-black/60 px-2 py-1 text-[10px] uppercase tracking-[0.16em] text-gray-400">{tag}</span>
													{/each}
												</div>
											{/if}
										</div>
									</div>
								</button>
							{/each}
						{/if}

						{#if !loading && ((view === 'timeline' && activeTimeline().length === 0) || (view === 'canon' && activeCanonItems().length === 0) || (view === 'explore' && activeExploreItems().length === 0))}
							<div class="px-4 py-12 text-center">
								<div class="text-sm font-semibold uppercase tracking-[0.22em] text-gray-500">No memory here yet</div>
								<p class="mx-auto mt-3 max-w-xl text-sm leading-6 text-gray-400">
									{#if view === 'canon'}
										Pin a memory or set its tier to <span class="text-white">Canon</span> to build your curated shelf.
									{:else if view === 'timeline'}
										Memory events will appear here once you start curating, hiding, or forgetting items.
									{:else}
										Try a broader search, clear filters, or let the system accumulate more workspace, Chroma, and narrative context.
									{/if}
								</p>
							</div>
						{/if}
					</div>
				</div>
			</section>

			<aside class="hidden min-h-[420px] lg:block">
				<div class="sticky top-4 rounded-2xl border border-[#1f1f1f] bg-[#090909]">
					<div class="border-b border-[#1f1f1f] px-4 py-3">
						<div class="text-[10px] font-semibold uppercase tracking-[0.24em] text-gray-500">Inspector</div>
					</div>
					<div class="max-h-[calc(100vh-180px)] overflow-auto p-4">
						{#if detail?.item}
							<div class="space-y-5">
								<div>
									<div class="flex flex-wrap items-center gap-2">
										<span class={`inline-flex rounded-full border px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.2em] ${sourceAccent(detail.item.source)}`}>{detail.item.source}</span>
										{#if detail.item.collection}
											<span class="rounded-full border border-[#222] bg-black/70 px-2 py-1 text-[10px] uppercase tracking-[0.18em] text-gray-400">{detail.item.collection}</span>
										{/if}
									</div>
									<h2 class="mt-3 text-xl font-semibold text-white">{detail.item.title}</h2>
									<p class="mt-3 whitespace-pre-wrap text-sm leading-6 text-gray-400">{detail.item.content_preview}</p>
								</div>

								<div class="grid gap-3 rounded-2xl border border-[#1d1d1d] bg-black/60 p-4">
									<label>
										<div class="mb-1 text-[10px] font-semibold uppercase tracking-[0.22em] text-gray-500">Title Override</div>
										<input bind:value={draftTitle} class="w-full rounded-xl border border-[#2a2a2a] bg-black px-3 py-2 text-sm text-white outline-none transition focus:border-cyan-400/70" />
									</label>
									<label>
										<div class="mb-1 text-[10px] font-semibold uppercase tracking-[0.22em] text-gray-500">Tags</div>
										<input bind:value={draftTags} class="w-full rounded-xl border border-[#2a2a2a] bg-black px-3 py-2 text-sm text-white outline-none transition focus:border-cyan-400/70" placeholder="canon, lesson, slippage" />
									</label>
									<label>
										<div class="mb-1 text-[10px] font-semibold uppercase tracking-[0.22em] text-gray-500">Operator Note</div>
										<textarea bind:value={draftNote} rows="4" class="w-full rounded-xl border border-[#2a2a2a] bg-black px-3 py-2 text-sm text-white outline-none transition focus:border-cyan-400/70"></textarea>
									</label>
									<div class="grid grid-cols-2 gap-3">
										<label>
											<div class="mb-1 text-[10px] font-semibold uppercase tracking-[0.22em] text-gray-500">Tier</div>
											<select bind:value={draftTier} class="w-full rounded-xl border border-[#2a2a2a] bg-black px-3 py-2 text-sm text-white outline-none transition focus:border-cyan-400/70">
												{#each TIER_OPTIONS as option}
													<option value={option.value}>{option.label}</option>
												{/each}
											</select>
										</label>
										<div class="flex flex-col justify-end gap-2 text-sm text-gray-300">
											<label class="flex items-center justify-between rounded-xl border border-[#202020] bg-[#0a0a0a] px-3 py-2">
												<span>Pinned</span>
												<input bind:checked={draftPinned} type="checkbox" class="h-4 w-4 rounded border-[#333] bg-black text-cyan-400 focus:ring-cyan-400" />
											</label>
											<label class="flex items-center justify-between rounded-xl border border-[#202020] bg-[#0a0a0a] px-3 py-2">
												<span>Hidden</span>
												<input bind:checked={draftHidden} type="checkbox" class="h-4 w-4 rounded border-[#333] bg-black text-cyan-400 focus:ring-cyan-400" />
											</label>
										</div>
									</div>
									<button type="button" on:click={saveAnnotation} disabled={saving} class="rounded-xl border border-cyan-500/40 bg-cyan-500/10 px-4 py-3 text-xs font-semibold uppercase tracking-[0.22em] text-cyan-200 transition hover:border-cyan-300 hover:text-white disabled:opacity-50">
										{saving ? 'Saving' : 'Save Annotation'}
									</button>
								</div>

								<div class="grid gap-2">
									{#if hasAction(detail.item, 'hide') && !detail.item.hidden}
										<button type="button" on:click={() => runItemAction('hide')} disabled={acting} class="rounded-xl border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-xs font-semibold uppercase tracking-[0.22em] text-amber-200 transition hover:border-amber-300 hover:text-white disabled:opacity-50">Hide From Default Views</button>
									{/if}
									{#if detail.item.hidden}
										<button type="button" on:click={() => runItemAction('unhide')} disabled={acting} class="rounded-xl border border-emerald-500/30 bg-emerald-500/10 px-4 py-3 text-xs font-semibold uppercase tracking-[0.22em] text-emerald-200 transition hover:border-emerald-300 hover:text-white disabled:opacity-50">Restore To Default Views</button>
									{/if}
									</div>

								<div class="rounded-2xl border border-[#1d1d1d] bg-black/60 p-4">
									<div class="text-[10px] font-semibold uppercase tracking-[0.22em] text-gray-500">Provenance</div>
									<div class="mt-3 space-y-2 text-sm text-gray-300">
										<div>Updated: <span class="text-white">{formatTimestamp(detail.item.updated_at ?? detail.item.created_at)}</span></div>
										{#if detail.item.strategy_id}<div>Strategy: <span class="text-white">{detail.item.strategy_id}</span></div>{/if}
										{#if detail.item.agent_id}<div>Agent: <span class="text-white">{detail.item.agent_id}</span></div>{/if}
										{#if detail.item.provenance?.relative_path}<div class="break-all">File: <span class="text-white">{String(detail.item.provenance.relative_path)}</span></div>{/if}
										{#if detail.item.provenance?.doc_id}<div>Doc ID: <span class="text-white">{String(detail.item.provenance.doc_id)}</span></div>{/if}
									</div>
								</div>

								{#if detail.related_items.length}
									<div class="rounded-2xl border border-[#1d1d1d] bg-black/60 p-4">
										<div class="text-[10px] font-semibold uppercase tracking-[0.22em] text-gray-500">Related</div>
										<div class="mt-3 space-y-2">
											{#each detail.related_items as related}
												<button type="button" on:click={() => selectItem(related)} class="w-full rounded-xl border border-[#1f1f1f] bg-[#090909] px-3 py-3 text-left transition hover:border-white/30">
													<div class="text-sm font-semibold text-white">{related.title}</div>
													<div class="mt-1 text-xs text-gray-500">{related.source} · {relativeTime(related.updated_at ?? related.created_at)}</div>
												</button>
											{/each}
										</div>
									</div>
								{/if}

								<div class="rounded-2xl border border-[#1d1d1d] bg-black/60 p-4">
									<div class="text-[10px] font-semibold uppercase tracking-[0.22em] text-gray-500">Audit Trail</div>
									<div class="mt-3 space-y-3">
										{#if detail.events.length}
											{#each detail.events as event}
												<div class="rounded-xl border border-[#1f1f1f] bg-[#090909] px-3 py-3">
													<div class="flex items-center justify-between gap-3">
														<div class="text-xs font-semibold uppercase tracking-[0.18em] text-gray-300">{event.action}</div>
														<div class="text-[10px] uppercase tracking-[0.18em] text-gray-500">{formatTimestamp(event.created_at)}</div>
													</div>
													<div class="mt-2 text-sm text-gray-400">{String(event.payload?.summary ?? event.action)}</div>
												</div>
											{/each}
										{:else}
											<div class="text-sm text-gray-500">No curation events yet.</div>
										{/if}
									</div>
								</div>
							</div>
						{:else}
							<div class="py-16 text-center">
								<div class="text-[10px] font-semibold uppercase tracking-[0.24em] text-gray-500">Select A Memory</div>
								<p class="mx-auto mt-3 max-w-xs text-sm leading-6 text-gray-400">Choose any card from Explore, Canon, or Timeline to inspect provenance, add notes, and curate recall safely.</p>
							</div>
						{/if}
					</div>
				</div>
			</aside>
		</div>
	</div>

	{#if inspectorOpen && detail?.item}
		<div class="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm lg:hidden">
			<div class="absolute inset-x-0 bottom-0 top-20 overflow-auto rounded-t-[28px] border border-[#1f1f1f] bg-[#090909] p-4">
				<div class="mb-4 flex items-center justify-between gap-3">
					<div class="text-[10px] font-semibold uppercase tracking-[0.24em] text-gray-500">Inspector</div>
					<button type="button" on:click={() => (inspectorOpen = false)} class="rounded-full border border-[#2a2a2a] px-3 py-1.5 text-[10px] font-semibold uppercase tracking-[0.2em] text-gray-300">Close</button>
				</div>
				<div class="text-lg font-semibold text-white">{detail.item.title}</div>
				<p class="mt-3 whitespace-pre-wrap text-sm leading-6 text-gray-400">{detail.item.content_preview}</p>
				<div class="mt-4 flex flex-wrap gap-2">
					<button type="button" on:click={saveAnnotation} disabled={saving} class="rounded-xl border border-cyan-500/40 bg-cyan-500/10 px-4 py-3 text-xs font-semibold uppercase tracking-[0.22em] text-cyan-200 transition hover:border-cyan-300 hover:text-white disabled:opacity-50">Save</button>
					{#if hasAction(detail.item, 'hide') && !detail.item.hidden}
						<button type="button" on:click={() => runItemAction('hide')} disabled={acting} class="rounded-xl border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-xs font-semibold uppercase tracking-[0.22em] text-amber-200 transition hover:border-amber-300 hover:text-white disabled:opacity-50">Hide</button>
					{/if}
					{#if detail.item.hidden}
						<button type="button" on:click={() => runItemAction('unhide')} disabled={acting} class="rounded-xl border border-emerald-500/30 bg-emerald-500/10 px-4 py-3 text-xs font-semibold uppercase tracking-[0.22em] text-emerald-200 transition hover:border-emerald-300 hover:text-white disabled:opacity-50">Unhide</button>
					{/if}
				</div>
			</div>
		</div>
	{/if}

	{#if drawerSkillName}
		<SkillDetailDrawer name={drawerSkillName} on:close={closeSkillDrawer} />
	{/if}

	{#if confirmState}
		<div class="fixed inset-0 z-[60] flex items-center justify-center bg-black/70 backdrop-blur-sm p-4">
			<div class="w-full max-w-sm rounded-2xl border border-[#242424] bg-[#090909] p-5" role="dialog" aria-modal="true">
				<h3 class="text-sm font-semibold uppercase tracking-[0.22em] text-white">{confirmState.title}</h3>
				<p class="mt-3 text-sm leading-6 text-gray-400">{confirmState.body}</p>
				<div class="mt-5 flex justify-end gap-2">
					<button type="button" on:click={cancelConfirm} class="rounded-xl border border-[#2c2c2c] bg-black px-4 py-2 text-xs font-semibold uppercase tracking-[0.2em] text-gray-300 transition hover:border-white hover:text-white">Cancel</button>
					<button
						type="button"
						on:click={acceptConfirm}
						class={`rounded-xl border px-4 py-2 text-xs font-semibold uppercase tracking-[0.2em] transition ${confirmState.tone === 'rose' ? 'border-rose-500/40 bg-rose-500/10 text-rose-200 hover:border-rose-300' : 'border-amber-500/40 bg-amber-500/10 text-amber-200 hover:border-amber-300'}`}
					>
						{confirmState.confirmLabel}
					</button>
				</div>
			</div>
		</div>
	{/if}
</div>
