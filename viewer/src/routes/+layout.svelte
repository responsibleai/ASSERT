<!-- Copyright (c) Microsoft Corporation.
     Licensed under the MIT License. -->

<script lang="ts">
	import { onMount } from 'svelte';
	import { activeRuns } from '$lib/active-runs.js';
	import '../app.css';
	let { children } = $props();

	let isDark = $state(true);

	function toggleTheme() {
		isDark = !isDark;
		const mode = isDark ? 'dark' : 'light';
		document.documentElement.setAttribute('data-color-mode', mode);
		localStorage.setItem('theme', mode);
	}

	onMount(() => {
		const saved = localStorage.getItem('theme');
		if (saved === 'light') {
			isDark = false;
			document.documentElement.setAttribute('data-color-mode', 'light');
		} else {
			document.documentElement.setAttribute('data-color-mode', 'dark');
		}
	});
</script>

<svelte:head>
	<script>
		(function () {
			try {
				var s = localStorage.getItem('theme');
				if (s === 'light') {
					document.documentElement.setAttribute('data-color-mode', 'light');
				} else {
					document.documentElement.setAttribute('data-color-mode', 'dark');
				}
			} catch (e) {}
		})();
	</script>
</svelte:head>

<div class="flex min-h-screen flex-col bg-bg text-text">
	<div class="Header">
		<div class="Header-item">
			<a href="/" class="Header-link inline-flex items-center text-sm font-semibold">
				ASSERT
			</a>
		</div>

		{#each $activeRuns as run (run.suiteId + '/' + run.runId)}
			<div class="Header-item">
				<a
					href="/suite/{run.suiteId}/{run.runId}/monitor"
					class="Header-link inline-flex items-center gap-2 text-xs font-medium text-interactive"
				>
					<span class="h-1.5 w-1.5 animate-pulse rounded-full bg-interactive"></span>
					<span class="font-mono">
						{run.suiteId}<span class="text-interactive/50">/</span>{run.runId}
					</span>
					{#if run.currentStage}
						<span class="text-interactive/70">· {run.currentStage}</span>
					{/if}
				</a>
			</div>
		{/each}

		<div class="Header-item Header-item--full"></div>

		<div class="Header-item">
			<button
				onclick={toggleTheme}
				class="btn-octicon"
				title={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
				aria-label={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
			>
				{#if isDark}
					<svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
						<circle cx="12" cy="12" r="5"/>
						<path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/>
					</svg>
				{:else}
					<svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
						<path d="M21 12.79A9 9 0 1111.21 3 7 7 0 0021 12.79z"/>
					</svg>
				{/if}
			</button>
		</div>
	</div>

	<main class="container-xl w-full flex-1 px-6 py-6">
		{@render children()}
	</main>

	<footer class="py-4 text-center font-mono text-text-muted" style="font-size: 12px; line-height: 16px;">
		Made with 💜 by Microsoft
	</footer>
</div>
