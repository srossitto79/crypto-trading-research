import { fetchApi } from './core';
import type { AxiomTrade } from './axiom';

export interface SimulationStatus {
    active: boolean;
    phase: 'prefetching' | 'running' | 'complete' | 'idle';
    current_time: string;
    progress: number;
    bar: number;
    total_bars: number;
    equity: number;
    exec_mode: 'direct' | 'agent';
    interval?: string;
    prices?: Record<string, number>;
    analytics?: SimulationAnalytics;
}

export interface SimulationAnalytics {
    final_equity: number;
    initial_equity: number;
    total_return_pct: number;
    max_drawdown_pct: number;
    total_trades: number;
    closed_trades: number;
    open_trades: number;
    wins: number;
    losses: number;
    win_rate_pct: number;
    avg_win_pct: number;
    avg_loss_pct: number;
    profit_factor: number | string;
    gross_profit_usd: number;
    gross_loss_usd: number;
    bars_processed: number;
    total_bars: number;
    interval: string;
    exec_mode: string;
    equity_curve: [string, number][];
}

export async function startSimulation(
    startDate: string,
    endDate: string,
    interval: string = '1h',
    initialEquity: number = 10000.0,
    execMode: string = 'direct'
) {
    return fetchApi('/simulation/start', {
        method: 'POST',
        body: JSON.stringify({
            start_date: startDate,
            end_date: endDate,
            interval,
            initial_equity: initialEquity,
            exec_mode: execMode
        })
    });
}

export async function stopSimulation() {
    return fetchApi('/simulation/stop', { method: 'POST', body: '{}' });
}

export async function getSimulationStatus(): Promise<SimulationStatus> {
    return fetchApi('/simulation/status');
}

export async function getSimulationAnalytics(): Promise<SimulationAnalytics> {
    return fetchApi('/simulation/analytics');
}

export async function getSimTrades(): Promise<AxiomTrade[]> {
    return fetchApi('/simulation/trades');
}

export async function getEquityCurve(): Promise<[string, number][]> {
    return fetchApi('/simulation/equity-curve');
}
