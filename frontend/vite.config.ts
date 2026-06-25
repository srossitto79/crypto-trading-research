import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vitest/config';

const backendOrigin =
	process.env.AXIOM_CLIENT_BASE ||
	process.env.AXIOM_API_ORIGIN ||
	`http://127.0.0.1:${process.env.AXIOM_PORT ?? '8003'}`;
const isVitest = process.env.VITEST === 'true';

export default defineConfig({
	plugins: [sveltekit()],
	// Pre-bundle deps that are only imported lazily (e.g. `marked` in AIChatPanel)
	// so Vite doesn't discover them MID-PAGE-LOAD and trigger a re-optimization —
	// that changes chunk hashes and 404s the already-loaded tab (the blank-white
	// screen seen on restart). Listing them here forces pre-bundling at startup.
	optimizeDeps: { include: ['marked'] },
	resolve: isVitest
		? {
			conditions: ['browser']
		}
		: undefined,
	build: {
		// ECharts is intentionally bundled as a dedicated heavy chunk.
		chunkSizeWarningLimit: 1200,
		rollupOptions: {
			onwarn(warning, warn) {
				const code = typeof warning === 'string' ? '' : warning.code;
				const message = typeof warning === 'string' ? warning : String(warning.message || '');
				const sourceId = typeof warning === 'string' ? '' : String((warning as { id?: string }).id || '');
				const isKnownSvelteRuntimeExportNoise =
					code === 'MISSING_EXPORT' &&
					sourceId.includes('@sveltejs/kit/src/runtime/client/client.js') &&
					/(untrack|fork|settled)/.test(message);

				if (isKnownSvelteRuntimeExportNoise) return;
				warn(warning);
			},
			output: {
				manualChunks(id) {
					if (id.includes('node_modules/echarts')) return 'vendor-echarts';
					if (id.includes('node_modules/lightweight-charts')) return 'vendor-lightweight-charts';
				}
			}
		}
	},
	server: {
		proxy: {
			'/api': {
				target: backendOrigin,
				changeOrigin: true,
				ws: true
			},
			'/health': {
				target: backendOrigin,
				changeOrigin: true
			}
		}
	},
	test: {
		include: ['src/**/*.{test,spec}.{js,ts}'],
		environment: 'jsdom',
		globals: true,
		setupFiles: ['./src/tests/setup.ts'],
		coverage: {
			reporter: ['text', 'json', 'html'],
			exclude: [
				'node_modules/',
				'src/tests/',
				'**/*.d.ts',
				'**/*.config.*',
				'.svelte-kit/'
			]
		}
	}
});
