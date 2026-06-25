/**
 * Skills API client (P3-T10) — backs the Memory Skills tab (P3-T11),
 * the Skill detail drawer (P3-T12), and the dashboard declining-skills
 * widget (P3-T15).
 *
 * Contract is fixed by ``axiom/routers/skills.py``. Read-only — operator
 * edits flow through the `skill_update_proposal` approval queue, not via
 * direct PUT. The legacy `/quant-skills/*` client in ``memory.ts`` remains
 * in place for the Hypotheses promotion flow; this module covers the new
 * /api/skills surface introduced in Phase 3.
 */

import { fetchApi } from './core';

// --------------------------------------------------------------------------- //
// Types                                                                       //
// --------------------------------------------------------------------------- //

export type SkillType = 'regime' | 'failure' | 'indicator' | 'combo' | 'params';

/** L1 disclosure — metadata-only catalog row. */
export interface SkillSummary {
	name: string;
	type: SkillType | string;
	confidence: number;
	samples: number;
	version: number;
	regime?: string;
}

/** L2 disclosure — full skill detail. */
export interface SkillDetail {
	name: string;
	description: string;
	skill_type: SkillType | string;
	confidence: number;
	sample_size: number;
	regime: string;
	last_validated: string;
	version: number;
	parent_version: number | null;
	change_summary: string | null;
	what_works: string[];
	what_doesnt_work: string[];
	evidence: Record<string, unknown>[];
	metadata: Record<string, string>;
}

export type SkillSection =
	| 'what_works'
	| 'what_doesnt_work'
	| 'evidence'
	| 'metadata'
	| 'history';

export interface SkillSectionResponse {
	section: SkillSection;
	what_works?: string[];
	what_doesnt_work?: string[];
	evidence?: Record<string, unknown>[];
	metadata?: Record<string, string>;
	items?: SkillHistoryRow[];
}

export interface SkillHistoryRow {
	id: number;
	skill_name: string;
	version: number;
	parent_version: number | null;
	body_diff: string | null;
	change_summary: string | null;
	evidence_task_id: number | null;
	created_by: string | null;
	created_at: string;
}

export interface SkillHistoryResponse {
	skill_name: string;
	history: SkillHistoryRow[];
	count: number;
}

export interface SkillDiffResponse {
	skill_name: string;
	from_version: number;
	to_version: number;
	diff: string;
}

export type SkillOutcomeKind = 'positive' | 'negative';

export interface SkillOutcomeEvent {
	id: number;
	skill_name: string;
	strategy_id: string | null;
	outcome: SkillOutcomeKind;
	confidence_delta: number;
	triggered_by: string | null;
	notes: string | null;
	created_at: string;
}

export interface SkillOutcomesResponse {
	skill_name: string;
	items: SkillOutcomeEvent[];
	count: number;
}

export interface DecliningSkillRow {
	skill_name: string;
	total_delta: number;
	event_count: number;
	last_event_at: string | null;
	confidence: number;
	version: number;
}

export interface DecliningSkillsResponse {
	items: DecliningSkillRow[];
	days: number;
	count: number;
}

// --------------------------------------------------------------------------- //
// Endpoints                                                                   //
// --------------------------------------------------------------------------- //

export async function listSkills(): Promise<{ items: SkillSummary[]; count: number }> {
	return fetchApi('/skills');
}

export async function getSkill(name: string): Promise<SkillDetail> {
	return fetchApi(`/skills/${encodeURIComponent(name)}`);
}

export async function getSkillSection(
	name: string,
	section: SkillSection
): Promise<SkillSectionResponse> {
	return fetchApi(
		`/skills/${encodeURIComponent(name)}/section/${encodeURIComponent(section)}`
	);
}

export async function getSkillHistory(name: string): Promise<SkillHistoryResponse> {
	return fetchApi(`/skills/${encodeURIComponent(name)}/history`);
}

export async function getSkillDiff(
	name: string,
	fromVersion: number,
	toVersion: number
): Promise<SkillDiffResponse> {
	const params = new URLSearchParams({
		from_version: String(fromVersion),
		to_version: String(toVersion)
	});
	return fetchApi(`/skills/${encodeURIComponent(name)}/diff?${params.toString()}`);
}

export async function getSkillOutcomes(
	name: string,
	options: { limit?: number; offset?: number } = {}
): Promise<SkillOutcomesResponse> {
	const params = new URLSearchParams();
	if (options.limit !== undefined) params.set('limit', String(options.limit));
	if (options.offset !== undefined) params.set('offset', String(options.offset));
	const qs = params.toString();
	return fetchApi(
		qs
			? `/skills/${encodeURIComponent(name)}/outcomes?${qs}`
			: `/skills/${encodeURIComponent(name)}/outcomes`
	);
}

export async function listDecliningSkills(
	options: { days?: number; limit?: number } = {}
): Promise<DecliningSkillsResponse> {
	const params = new URLSearchParams();
	if (options.days !== undefined) params.set('days', String(options.days));
	if (options.limit !== undefined) params.set('limit', String(options.limit));
	const qs = params.toString();
	return fetchApi(qs ? `/skills/declining?${qs}` : '/skills/declining');
}
