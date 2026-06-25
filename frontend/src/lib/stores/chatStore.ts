/**
 * Persistent chat store that survives component remounts and page navigation.
 * Uses sessionStorage so history clears when the browser tab is closed.
 */
import { browser } from '$app/environment';
import { writable } from 'svelte/store';

export interface ChatMessage {
	role: 'user' | 'brain' | 'tool';
	content: string;
	timestamp: string; // ISO string (Date is not JSON serializable)
	taskId?: number;
	status?: 'pending' | 'running' | 'done' | 'error';
	mode?: 'chat' | 'command' | 'deepdive';
	toolName?: string;
}

const MESSAGE_STORAGE_KEY = 'axiom_brain_chat';
const UNREAD_STORAGE_KEY = 'axiom_brain_chat_unread';

function loadJson<T>(key: string, fallback: T): T {
	if (!browser) return fallback;
	try {
		const raw = sessionStorage.getItem(key);
		if (raw) return JSON.parse(raw) as T;
	} catch {
		// Ignore malformed session state and fall back to defaults.
	}
	return fallback;
}

function saveJson<T>(key: string, value: T) {
	if (!browser) return;
	try {
		sessionStorage.setItem(key, JSON.stringify(value));
	} catch {
		// Ignore quota and serialization issues.
	}
}

function loadUnreadCount(): number {
	const value = loadJson<number>(UNREAD_STORAGE_KEY, 0);
	return Number.isFinite(value) && value > 0 ? Math.floor(value) : 0;
}

const initialMessages = loadJson<ChatMessage[]>(MESSAGE_STORAGE_KEY, []);

export const chatMessages = writable<ChatMessage[]>(initialMessages);
export const chatUnreadCount = writable<number>(loadUnreadCount());

chatMessages.subscribe((messages) => saveJson(MESSAGE_STORAGE_KEY, messages));
chatUnreadCount.subscribe((count) => saveJson(UNREAD_STORAGE_KEY, Math.max(0, count)));

export function pushMessage(msg: ChatMessage): number {
	let len = 0;
	chatMessages.update((messages) => {
		const next = [...messages, msg];
		len = next.length;
		return next;
	});
	return len;
}

export function updateMessage(index: number, patch: Partial<ChatMessage>) {
	chatMessages.update((messages) => {
		if (index < 0 || index >= messages.length) return messages;
		const copy = [...messages];
		copy[index] = { ...copy[index], ...patch };
		return copy;
	});
}

export function incrementChatUnread(count = 1) {
	if (count <= 0) return;
	chatUnreadCount.update((value) => value + count);
}

export function markChatRead() {
	chatUnreadCount.set(0);
}

export function clearMessages() {
	chatMessages.set([]);
	markChatRead();
}
