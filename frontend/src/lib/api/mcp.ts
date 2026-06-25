/**
 * Phase 4 / P4-T09 — MCP servers API client.
 *
 * Backs:
 *   - /integrations/mcp page (P4-T10)
 *   - Agents page MCP grants UI (P4-T12)
 *
 * Contract is fixed by ``axiom/routers/mcp.py``. If the backend payload
 * shape changes, update these interfaces in lockstep.
 */

import { fetchApi } from './core';

export type MCPTransport = 'stdio' | 'http';

export interface MCPServer {
	name: string;
	transport: MCPTransport;
	command: string | null;
	args: string[];
	env: Record<string, string>;
	url: string | null;
	headers: Record<string, string>;
	enabled: boolean;
	tools_include: string[] | null;
	tools_exclude: string[];
	last_status: string | null;
	last_status_at: string | null;
	last_error: string | null;
	created_at: string | null;
	updated_at: string | null;
	registered_tool_count?: number;
}

export interface MCPServerListResponse {
	servers: MCPServer[];
}

export interface MCPToolDef {
	name: string;
	description?: string;
	inputSchema?: Record<string, unknown>;
}

export interface MCPToolsResponse {
	tools: MCPToolDef[];
}

export interface MCPTestResponse {
	ok: boolean;
	error?: string;
	protocol_version?: string;
	server_info?: Record<string, unknown>;
}

export interface MCPGrant {
	server_name: string;
	granted_at: string | null;
	granted_by: string | null;
}

export interface MCPGrantsResponse {
	agent_id: string;
	grants: MCPGrant[];
}

export interface MCPServerCreatePayload {
	name: string;
	transport: MCPTransport;
	command?: string | null;
	args?: string[];
	env?: Record<string, string>;
	url?: string | null;
	headers?: Record<string, string>;
	enabled?: boolean;
	tools_include?: string[] | null;
	tools_exclude?: string[];
}

export type MCPServerUpdatePayload = Partial<MCPServerCreatePayload> & {
	transport?: MCPTransport;
};

// ---------------------------------------------------------------------------
// Servers
// ---------------------------------------------------------------------------

export async function listMCPServers(): Promise<MCPServerListResponse> {
	return fetchApi<MCPServerListResponse>('/mcp/servers');
}

export async function getMCPServer(name: string): Promise<MCPServer> {
	return fetchApi<MCPServer>(`/mcp/servers/${encodeURIComponent(name)}`);
}

export async function createMCPServer(body: MCPServerCreatePayload): Promise<MCPServer> {
	return fetchApi<MCPServer>('/mcp/servers', {
		method: 'POST',
		headers: { 'content-type': 'application/json' },
		body: JSON.stringify(body),
	});
}

export async function updateMCPServer(
	name: string,
	body: MCPServerUpdatePayload,
): Promise<MCPServer> {
	return fetchApi<MCPServer>(`/mcp/servers/${encodeURIComponent(name)}`, {
		method: 'PUT',
		headers: { 'content-type': 'application/json' },
		body: JSON.stringify(body),
	});
}

export async function deleteMCPServer(name: string): Promise<void> {
	await fetchApi<void>(`/mcp/servers/${encodeURIComponent(name)}`, {
		method: 'DELETE',
	});
}

export async function testMCPServer(name: string): Promise<MCPTestResponse> {
	return fetchApi<MCPTestResponse>(`/mcp/servers/${encodeURIComponent(name)}/test`, {
		method: 'POST',
	});
}

export async function listMCPServerTools(name: string): Promise<MCPToolsResponse> {
	return fetchApi<MCPToolsResponse>(`/mcp/servers/${encodeURIComponent(name)}/tools`);
}

// ---------------------------------------------------------------------------
// Grants
// ---------------------------------------------------------------------------

export async function listMCPGrants(agentId: string): Promise<MCPGrantsResponse> {
	return fetchApi<MCPGrantsResponse>(`/mcp/agents/${encodeURIComponent(agentId)}/grants`);
}

export async function grantMCPServer(agentId: string, serverName: string): Promise<void> {
	await fetchApi<unknown>(`/mcp/agents/${encodeURIComponent(agentId)}/grants`, {
		method: 'POST',
		headers: { 'content-type': 'application/json' },
		body: JSON.stringify({ server_name: serverName }),
	});
}

export async function revokeMCPServer(agentId: string, serverName: string): Promise<void> {
	await fetchApi<void>(
		`/mcp/agents/${encodeURIComponent(agentId)}/grants/${encodeURIComponent(serverName)}`,
		{ method: 'DELETE' },
	);
}
