import type { PageLoad } from './$types';
import { getAxiomAllTrades } from '$lib/api';
import type { AxiomTradesPage } from '$lib/api';

export const ssr = false;

export const load: PageLoad = async () => {
	const [result] = await Promise.allSettled([getAxiomAllTrades({ limit: 200 })]);
	return {
		initialPage: result.status === 'fulfilled' ? result.value : null,
	} satisfies { initialPage: AxiomTradesPage | null };
};
