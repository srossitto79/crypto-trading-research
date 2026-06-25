import type { AxiomNotification } from '$lib/api';

export interface NotificationFormattedSection {
	label: string;
	value: string;
}

export interface NotificationFormattedContent {
	summary: string | null;
	previewParagraphs: string[];
	metricChips: string[];
	sections: NotificationFormattedSection[];
	fullText: string | null;
	hasMore: boolean;
}

const FILLER_PATTERNS = [
	/^now let me provide\b/i,
	/^here(?:'s| is) (?:the )?(?:final )?(?:post-mortem|summary|output)\b/i,
	/^---+$/,
];

const SECTION_LABEL_BLOCKLIST = new Set([
	'metric',
	'post-mortem',
	'status',
	'executive summary',
	'ideation cycle results',
]);

export function formatNotificationContent(item: Pick<AxiomNotification, 'summary' | 'body'>): NotificationFormattedContent {
	const summaryText = cleanInlineText(String(item.summary ?? ''));
	const bodyText = normalizeNotificationBody(String(item.body ?? ''));
	const paragraphs = splitParagraphs(bodyText);
	const sections = extractSections(paragraphs);
	const metricChips = extractMetricChips(paragraphs);
	const previewParagraphs = paragraphs
		.filter((paragraph) => !isSectionParagraph(paragraph))
		.filter((paragraph) => !looksLikeMetricRow(paragraph))
		.filter((paragraph) => !isFillerParagraph(paragraph))
		.slice(0, 3);

	const summary = isUsefulSummary(summaryText)
		? summaryText
		: sections[0]
			? `${sections[0].label}: ${trimPreview(sections[0].value, 220)}`
			: previewParagraphs[0]
				? trimPreview(previewParagraphs[0], 240)
				: null;

	const fullText = bodyText || null;
	const previewLength = [
		summary ?? '',
		...previewParagraphs.slice(0, 3),
		...sections.slice(0, 4).map((section) => `${section.label}: ${section.value}`),
	].join('\n').length;

	return {
		summary,
		previewParagraphs: previewParagraphs
			.filter((paragraph) => paragraph !== summary)
			.map((paragraph) => trimPreview(paragraph, 340)),
		metricChips: metricChips.slice(0, 8),
		sections: sections.slice(0, 6).map((section) => ({
			label: section.label,
			value: trimPreview(section.value, 260),
		})),
		fullText,
		hasMore: Boolean(fullText && fullText.length > Math.max(previewLength + 120, 700)),
	};
}

function normalizeNotificationBody(value: string): string {
	if (!value.trim()) return '';
	const rawParagraphs = value
		.replace(/\r/g, '')
		.replace(/```+/g, '')
		.split(/\n{2,}/)
		.map((paragraph) => normalizeParagraph(paragraph))
		.filter(Boolean);

	const seen = new Set<string>();
	const deduped: string[] = [];
	for (const paragraph of rawParagraphs) {
		const key = paragraph.toLowerCase();
		if (seen.has(key)) continue;
		seen.add(key);
		deduped.push(paragraph);
	}
	return deduped.join('\n\n').trim();
}

function normalizeParagraph(value: string): string {
	const lines = value
		.split('\n')
		.map((line) => cleanParagraphLine(line))
		.filter(Boolean);
	return lines.join('\n').trim();
}

function cleanParagraphLine(value: string): string {
	let line = String(value ?? '').trim();
	if (!line) return '';
	if (/^---+$/.test(line)) return '';
	line = line.replace(/^#{1,6}\s*/, '');
	line = line.replace(/^\s*[-*]\s+/, '');
	line = line.replace(/^\s*\d+\.\s+/, '');
	line = line.replace(/\*\*(.*?)\*\*/g, '$1');
	line = line.replace(/__(.*?)__/g, '$1');
	line = line.replace(/`([^`]+)`/g, '$1');
	line = line.replace(/\s+/g, ' ').trim();
	return line;
}

function cleanInlineText(value: string): string {
	return cleanParagraphLine(value);
}

function splitParagraphs(value: string): string[] {
	return value
		.split(/\n{2,}/)
		.map((paragraph) => paragraph.trim())
		.filter(Boolean);
}

function isUsefulSummary(value: string): boolean {
	if (!value) return false;
	return !isFillerParagraph(value);
}

function isFillerParagraph(value: string): boolean {
	const normalized = value.trim();
	if (!normalized) return true;
	return FILLER_PATTERNS.some((pattern) => pattern.test(normalized));
}

function extractSections(paragraphs: string[]): NotificationFormattedSection[] {
	const sections: NotificationFormattedSection[] = [];
	for (const paragraph of paragraphs) {
		const lines = paragraph.split('\n');
		for (const line of lines) {
			const match = /^([A-Za-z][A-Za-z0-9/()%+\- ]{1,32}):\s+(.+)$/.exec(line);
			if (!match) continue;
			const label = match[1].trim();
			const value = match[2].trim();
			if (SECTION_LABEL_BLOCKLIST.has(label.toLowerCase())) continue;
			if (value.length < 6) continue;
			if (looksLikeMetricRow(value)) continue;
			sections.push({ label, value });
			if (sections.length >= 6) return sections;
		}
	}
	return dedupeSections(sections);
}

function dedupeSections(sections: NotificationFormattedSection[]): NotificationFormattedSection[] {
	const seen = new Set<string>();
	return sections.filter((section) => {
		const key = `${section.label}:${section.value}`.toLowerCase();
		if (seen.has(key)) return false;
		seen.add(key);
		return true;
	});
}

function extractMetricChips(paragraphs: string[]): string[] {
	const chips: string[] = [];
	const seen = new Set<string>();
	for (const paragraph of paragraphs) {
		const candidates = paragraph.includes('|')
			? paragraph.split('|')
			: paragraph.split('\n');
		for (const candidate of candidates) {
			const cleaned = cleanInlineText(candidate);
			if (!cleaned) continue;
			if (!/[0-9%]/.test(cleaned) && !/\b(pass|fail|archiv|reject|promising)\b/i.test(cleaned)) continue;
			if (!/[A-Za-z]/.test(cleaned)) continue;
			const normalized = cleaned.toLowerCase();
			if (normalized.length < 6 || normalized.length > 60) continue;
			if (seen.has(normalized)) continue;
			seen.add(normalized);
			chips.push(cleaned);
			if (chips.length >= 8) return chips;
		}
	}
	return chips;
}

function isSectionParagraph(paragraph: string): boolean {
	return paragraph
		.split('\n')
		.some((line) => /^([A-Za-z][A-Za-z0-9/()%+\- ]{1,32}):\s+(.+)$/.test(line));
}

function looksLikeMetricRow(value: string): boolean {
	const normalized = value.trim();
	if (!normalized) return false;
	if (normalized.includes('|') && /[0-9%]/.test(normalized)) return true;
	const metricKeywordMatches = normalized.match(/\b(sharpe|return|maxdd|max drawdown|winrate|win rate|pf|robustness|trades|fitness)\b/gi) ?? [];
	return metricKeywordMatches.length >= 2 && /[0-9%]/.test(normalized);
}

function trimPreview(value: string, maxLength: number): string {
	const text = value.trim();
	if (text.length <= maxLength) return text;
	return `${text.slice(0, maxLength - 1).trimEnd()}...`;
}
