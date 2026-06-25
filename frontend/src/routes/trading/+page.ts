import type { PageLoad } from './$types';
import { getAxiomDashboard } from '$lib/api';
import type { AxiomDashboardResponse } from '$lib/api';

export const ssr = false;

export const load: PageLoad = async () => {
	const dashboardResult = await Promise.allSettled([getAxiomDashboard()]);
	const dashboard = dashboardResult[0].status === 'fulfilled' ? dashboardResult[0].value : null;
	return { dashboard } satisfies { dashboard: AxiomDashboardResponse | null };
};
