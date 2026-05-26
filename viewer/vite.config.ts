import { sveltekit } from '@sveltejs/kit/vite';
import tailwindcss from '@tailwindcss/vite';
import { defineConfig, loadEnv } from 'vite';

export default defineConfig(({ mode }) => {
	// Vite's `envDir` controls which .env files feed `import.meta.env`, but it
	// does NOT push values into `process.env` — and SvelteKit's
	// `$env/dynamic/private` reads from `process.env`. Load .env from the repo
	// root manually so the viewer (and any `p2m` child it spawns) sees the
	// same provider credentials and model vars as the CLI.
	const rootEnv = loadEnv(mode, '..', '');
	for (const [key, value] of Object.entries(rootEnv)) {
		if (process.env[key] === undefined) process.env[key] = value;
	}

	return {
		plugins: [tailwindcss(), sveltekit()],
		envDir: '..',
		server: {
			host: '127.0.0.1',
			port: 5174,
			strictPort: true
		}
	};
});
