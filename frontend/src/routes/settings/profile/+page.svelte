<script lang="ts">
	import { onMount } from 'svelte';
	import { beforeNavigate } from '$app/navigation';
	import {
		getProfile,
		putProfile,
		type OperatorProfileResponse,
		type OperatorProfileStructured
	} from '$lib/api/profile';

	let profile: OperatorProfileResponse | null = null;
	let loading = true;
	let saving = false;
	let error: string | null = null;
	let saved: string | null = null;
	let showPreview = false;

	let name = '';
	let timezone = '';
	let startingCapital: number | null = null;
	let riskPerTrade: number | null = null;
	let exchange = '';
	let assetUniverse = '';
	let riskAppetite: 'conservative' | 'balanced' | 'aggressive' | '' = '';
	let responseStyle: 'terse' | 'conversational' | 'verbose' | '' = '';
	let quietHours = '';
	// Notification channels are managed in the dedicated Notifications settings
	// area; we round-trip whatever the backend already has so saving the profile
	// never clobbers channels configured elsewhere.
	let notificationChannels: string[] = [];
	let rules: string[] = [];
	let body = '';

	// Dirty-tracking: snapshot the loaded form so we can warn before navigating
	// away with unsaved edits (mirrors the parent /settings beforeNavigate guard).
	let pristine = '';
	let dirty = false;

	function snapshot(): string {
		return JSON.stringify({
			name,
			timezone,
			startingCapital,
			riskPerTrade,
			exchange,
			assetUniverse,
			riskAppetite,
			responseStyle,
			quietHours,
			notificationChannels,
			rules,
			body
		});
	}

	$: dirty = !loading && snapshot() !== pristine;

	function applyStructured(s: OperatorProfileStructured | null) {
		name = s?.name ?? '';
		timezone = s?.timezone ?? '';
		startingCapital = s?.starting_capital_usd ?? null;
		riskPerTrade = s?.risk_per_trade_pct ?? null;
		exchange = s?.exchange ?? '';
		assetUniverse = s?.asset_universe ?? '';
		riskAppetite = (s?.preferences?.risk_appetite as typeof riskAppetite) ?? '';
		responseStyle = (s?.preferences?.response_style as typeof responseStyle) ?? '';
		quietHours = s?.preferences?.quiet_hours ?? '';
		notificationChannels = [...(s?.preferences?.notification_channels ?? [])];
		rules = [...(s?.rules ?? [])];
	}

	function validate(): string | null {
		if (startingCapital !== null && (Number.isNaN(startingCapital) || startingCapital < 0))
			return 'Starting capital must be a non-negative number.';
		if (
			riskPerTrade !== null &&
			(Number.isNaN(riskPerTrade) || riskPerTrade < 0 || riskPerTrade > 100)
		)
			return 'Risk per trade must be between 0 and 100.';
		const qh = quietHours.trim();
		if (qh && !/^\d{1,2}:\d{2}-\d{1,2}:\d{2}$/.test(qh))
			return 'Quiet hours must look like "HH:MM-HH:MM" (e.g. 22:00-08:00).';
		return null;
	}

	async function load() {
		loading = true;
		error = null;
		try {
			const p = await getProfile();
			profile = p;
			applyStructured(p.structured);
			body = p.body ?? '';
			pristine = snapshot();
		} catch (exc) {
			error = exc instanceof Error ? exc.message : String(exc);
		} finally {
			loading = false;
		}
	}

	async function save() {
		const validationError = validate();
		if (validationError) {
			error = validationError;
			saved = null;
			return;
		}
		saving = true;
		error = null;
		saved = null;
		try {
			const cleanRules = rules.map((r) => r.trim()).filter(Boolean);
			const payload = {
				structured: {
					name: name.trim() || null,
					timezone: timezone.trim() || null,
					starting_capital_usd: startingCapital,
					risk_per_trade_pct: riskPerTrade,
					exchange: exchange.trim() || null,
					asset_universe: assetUniverse.trim() || null,
					preferences: {
						notification_channels: notificationChannels,
						quiet_hours: quietHours.trim() || null,
						risk_appetite: riskAppetite || null,
						response_style: responseStyle || null
					},
					rules: cleanRules
				},
				body
			};
			const updated = await putProfile(payload);
			profile = updated;
			applyStructured(updated.structured);
			body = updated.body ?? '';
			pristine = snapshot();
			saved = 'Profile saved.';
			setTimeout(() => (saved = null), 3000);
		} catch (exc) {
			error = exc instanceof Error ? exc.message : String(exc);
		} finally {
			saving = false;
		}
	}

	function addRule() {
		rules = [...rules, ''];
	}

	function removeRule(i: number) {
		rules = rules.filter((_, idx) => idx !== i);
	}

	function previewSystemPrompt(): string {
		const lines: string[] = ['# OPERATOR PROFILE'];
		if (name) lines.push(`- Name: ${name}`);
		if (timezone) lines.push(`- Timezone: ${timezone}`);
		if (startingCapital !== null && startingCapital !== undefined)
			lines.push(`- Starting capital: $${startingCapital.toLocaleString()}`);
		if (riskPerTrade !== null && riskPerTrade !== undefined)
			lines.push(`- Risk per trade: ${riskPerTrade}%`);
		if (exchange) lines.push(`- Exchange: ${exchange}`);
		if (assetUniverse) lines.push(`- Asset universe: ${assetUniverse}`);
		if (riskAppetite) lines.push(`- Risk appetite: ${riskAppetite}`);
		if (responseStyle) lines.push(`- Response style: ${responseStyle}`);
		if (quietHours) lines.push(`- Quiet hours: ${quietHours}`);
		const cleanRules = rules.map((r) => r.trim()).filter(Boolean);
		if (cleanRules.length) {
			lines.push('- Rules:');
			cleanRules.forEach((r, i) => lines.push(`  ${i + 1}. ${r}`));
		}
		const trimmedBody = body.trim();
		if (trimmedBody) {
			lines.push('');
			lines.push(trimmedBody);
		}
		return lines.join('\n');
	}

	onMount(load);

	beforeNavigate((navigation) => {
		if (!dirty) return;
		const proceed = window.confirm(
			'You have unsaved profile changes. Leave this page and discard them?'
		);
		if (!proceed) navigation.cancel();
	});
</script>

<svelte:head>
	<title>Operator profile — Axiom</title>
</svelte:head>

<main class="container">
	<header>
		<h1>Operator profile</h1>
		<p class="muted">
			This profile is fed to Brain at every chat. Empty fields are skipped — fill in only what
			matters.
		</p>
	</header>

	{#if loading}
		<p>Loading…</p>
	{:else if error}
		<p class="error">{error}</p>
	{:else}
		{#if profile && !profile.exists}
			<div class="info">
				No operator profile exists yet. Fill in what matters below — your first save creates
				<code>USER.md</code>.
			</div>
		{/if}

		{#if profile?.parse_error}
			<div class="warning">
				<strong>Frontmatter parse error:</strong>
				{profile.parse_error}. Fix the YAML in <code>USER.md</code> manually, or save below to
				rewrite from this form.
			</div>
		{/if}

		<section class="card">
			<h2>Identity</h2>
			<div class="grid-two">
				<label>
					<span>Name</span>
					<input type="text" bind:value={name} placeholder="Your name" />
				</label>
				<label>
					<span>Timezone</span>
					<input type="text" bind:value={timezone} placeholder="America/Chicago" />
				</label>
			</div>
		</section>

		<section class="card">
			<h2>Trading</h2>
			<p class="note">
				These are the narrative values Brain sees in your profile. They are stored separately from
				the live engine config in <strong>Settings → Trading</strong> — set them consistently to
				avoid Brain reading a contradiction.
			</p>
			<div class="grid-two">
				<label>
					<span>Starting capital (USD)</span>
					<input type="number" bind:value={startingCapital} step="100" />
				</label>
				<label>
					<span>Risk per trade (%)</span>
					<input type="number" bind:value={riskPerTrade} step="0.1" min="0" />
				</label>
				<label>
					<span>Exchange</span>
					<input type="text" bind:value={exchange} placeholder="hyperliquid" />
				</label>
				<label>
					<span>Asset universe</span>
					<input type="text" bind:value={assetUniverse} placeholder="crypto" />
				</label>
			</div>
		</section>

		<section class="card">
			<h2>Preferences</h2>
			<div class="grid-two">
				<label>
					<span>Risk appetite</span>
					<select bind:value={riskAppetite}>
						<option value="">—</option>
						<option value="conservative">Conservative</option>
						<option value="balanced">Balanced</option>
						<option value="aggressive">Aggressive</option>
					</select>
				</label>
				<label>
					<span>Response style</span>
					<select bind:value={responseStyle}>
						<option value="">—</option>
						<option value="terse">Terse</option>
						<option value="conversational">Conversational</option>
						<option value="verbose">Verbose</option>
					</select>
				</label>
				<label>
					<span>Quiet hours</span>
					<input type="text" bind:value={quietHours} placeholder="22:00-08:00" />
				</label>
			</div>
		</section>

		<section class="card">
			<div class="row-between">
				<h2>Rules</h2>
				<button class="btn-secondary" type="button" on:click={addRule}>+ Add rule</button>
			</div>
			{#if rules.length === 0}
				<p class="muted">No rules. Add invariants Brain should obey (e.g., "no live without backtest").</p>
			{:else}
				<ul class="rules">
					{#each rules as rule, i}
						<li>
							<input type="text" bind:value={rules[i]} />
							<button class="btn-link" type="button" on:click={() => removeRule(i)}>Remove</button>
						</li>
					{/each}
				</ul>
			{/if}
		</section>

		<section class="card">
			<h2>Free-form notes</h2>
			<textarea rows="8" bind:value={body} placeholder="Anything else Brain should know…"></textarea>
		</section>

		<section class="card">
			<button class="btn-link" type="button" on:click={() => (showPreview = !showPreview)}>
				{showPreview ? 'Hide' : 'Preview'} rendered system prompt
			</button>
			{#if showPreview}
				<pre class="preview">{previewSystemPrompt()}</pre>
			{/if}
		</section>

		<div class="actions">
			<button on:click={save} disabled={saving} class="btn-primary">
				{saving ? 'Saving…' : 'Save profile'}
			</button>
			{#if saved}<span class="ok">{saved}</span>{/if}
		</div>
	{/if}
</main>

<style>
	.container {
		max-width: 720px;
		margin: 0 auto;
		padding: 24px 16px 64px;
	}
	h1 {
		margin: 0;
	}
	h2 {
		margin: 0 0 12px;
		font-size: 16px;
	}
	.muted {
		color: var(--muted, #888);
	}
	.card {
		background: var(--card-bg, #14161a);
		border: 1px solid var(--border, #232830);
		border-radius: 8px;
		padding: 16px;
		margin: 16px 0;
	}
	.grid-two {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 12px;
	}
	@media (max-width: 600px) {
		.grid-two {
			grid-template-columns: 1fr;
		}
	}
	label {
		display: flex;
		flex-direction: column;
		gap: 4px;
		font-size: 13px;
	}
	label span {
		color: var(--muted, #999);
	}
	input[type='text'],
	input[type='number'],
	select,
	textarea {
		background: var(--input-bg, #0c0e12);
		border: 1px solid var(--border, #232830);
		color: inherit;
		padding: 8px;
		border-radius: 4px;
		font-family: inherit;
		font-size: 14px;
	}
	textarea {
		width: 100%;
		resize: vertical;
	}
	.note {
		margin: 0 0 12px;
		font-size: 12px;
		color: var(--muted, #888);
	}
	.row-between {
		display: flex;
		justify-content: space-between;
		align-items: center;
		margin-bottom: 12px;
	}
	.rules {
		list-style: none;
		padding: 0;
		margin: 0;
	}
	.rules li {
		display: flex;
		gap: 8px;
		margin-bottom: 8px;
	}
	.rules li input {
		flex: 1;
	}
	.actions {
		display: flex;
		gap: 12px;
		align-items: center;
		margin-top: 24px;
	}
	.btn-primary {
		background: var(--accent, #4a90e2);
		color: white;
		border: none;
		padding: 10px 20px;
		border-radius: 4px;
		cursor: pointer;
		font-size: 14px;
	}
	.btn-primary:disabled {
		opacity: 0.6;
		cursor: not-allowed;
	}
	.btn-secondary {
		background: transparent;
		color: var(--accent, #4a90e2);
		border: 1px solid var(--accent, #4a90e2);
		padding: 6px 12px;
		border-radius: 4px;
		cursor: pointer;
		font-size: 13px;
	}
	.btn-link {
		background: none;
		border: none;
		color: var(--accent, #4a90e2);
		cursor: pointer;
		text-decoration: underline;
		padding: 0;
		font-size: 13px;
	}
	.info {
		background: rgba(74, 144, 226, 0.12);
		border: 1px solid var(--accent, #4a90e2);
		padding: 12px;
		border-radius: 4px;
		margin: 16px 0;
		font-size: 13px;
		color: var(--accent, #4a90e2);
	}
	.warning {
		background: rgba(231, 178, 53, 0.12);
		border: 1px solid #c89d2c;
		padding: 12px;
		border-radius: 4px;
		margin: 16px 0;
		color: #e7b235;
	}
	.error {
		color: #e74c3c;
	}
	.ok {
		color: #2ecc71;
	}
	.preview {
		background: #0a0c0f;
		padding: 12px;
		border-radius: 4px;
		white-space: pre-wrap;
		font-size: 12px;
		max-height: 400px;
		overflow: auto;
		margin-top: 12px;
	}
</style>
