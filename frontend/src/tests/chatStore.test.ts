import { beforeEach, describe, expect, it } from 'vitest';
import { get } from 'svelte/store';
import {
	chatMessages,
	chatUnreadCount,
	clearMessages,
	incrementChatUnread,
	markChatRead,
	pushMessage,
} from '../lib/stores/chatStore';

describe('chatStore', () => {
	beforeEach(() => {
		sessionStorage.clear();
		clearMessages();
		markChatRead();
	});

	it('tracks unread replies until the chat is marked as read', () => {
		incrementChatUnread();
		incrementChatUnread(2);

		expect(get(chatUnreadCount)).toBe(3);
		expect(sessionStorage.getItem('axiom_brain_chat_unread')).toBe('3');

		markChatRead();

		expect(get(chatUnreadCount)).toBe(0);
		expect(sessionStorage.getItem('axiom_brain_chat_unread')).toBe('0');
	});

	it('clears unread state when the chat history is cleared', () => {
		pushMessage({
			role: 'brain',
			content: 'Reply ready',
			timestamp: new Date().toISOString(),
			status: 'done',
		});
		incrementChatUnread();

		clearMessages();

		expect(get(chatMessages)).toEqual([]);
		expect(get(chatUnreadCount)).toBe(0);
		expect(sessionStorage.getItem('axiom_brain_chat')).toBe('[]');
	});
});
