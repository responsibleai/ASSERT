<script lang="ts">
	import { onMount } from 'svelte';
	import { activeRuns } from '$lib/active-runs.js';
	import '../app.css';
	let { children } = $props();

	let isDark = $state(true);

	function toggleTheme() {
		isDark = !isDark;
		document.documentElement.classList.toggle('light', !isDark);
		localStorage.setItem('theme', isDark ? 'dark' : 'light');
	}

	onMount(() => {
		// Restore saved theme
		const saved = localStorage.getItem('theme');
		if (saved === 'light') {
			isDark = false;
			document.documentElement.classList.add('light');
		}
	});
</script>

<div class="min-h-screen bg-bg text-text">
	<nav class="sticky top-0 z-50 border-b border-border bg-bg/80 backdrop-blur-sm">
		<div class="flex h-12 items-center gap-6 px-6">
			<a href="/" class="flex items-center gap-2.5 font-mono text-sm font-medium text-text transition-colors hover:text-interactive">
				<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" class="text-interactive">
					<rect x="3" y="5" width="2" height="14" rx="0.5"/>
					<path d="M7 19V9M9 19V7M11 19V11M13 19V6M15 19V13M17 19V8M19 19V10M21 19V5"/>
					<path d="M3 19h18"/>
				</svg>
				p2m
			</a>
			<div class="h-4 w-px bg-border"></div>
			<span class="text-xs text-text-muted">Taxonomy to Metric</span>

			{#if $activeRuns.length > 0}
				<div class="h-4 w-px bg-border"></div>
				{#each $activeRuns as run}
					<a href="/suite/{run.suiteId}/{run.runId}/monitor"
						class="inline-flex items-center gap-2 rounded-md bg-interactive/10 px-2.5 py-1 text-xs font-medium text-interactive transition-colors hover:bg-interactive/20">
						<span class="h-1.5 w-1.5 animate-pulse rounded-full bg-interactive"></span>
						<span class="font-mono">{run.suiteId}<span class="text-interactive/50">/</span>{run.runId}</span>
						{#if run.currentStage}
							<span class="text-interactive/70">· {run.currentStage}</span>
						{/if}
					</a>
				{/each}
			{/if}

			<div class="ml-auto flex items-center gap-2">
				<button
					onclick={toggleTheme}
					class="flex h-8 w-8 items-center justify-center rounded-lg text-text-muted transition-colors hover:bg-surface hover:text-text"
					title={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
				>
					{#if isDark}
						<svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
							<circle cx="12" cy="12" r="5"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/>
						</svg>
					{:else}
						<svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
							<path d="M21 12.79A9 9 0 1111.21 3 7 7 0 0021 12.79z"/>
						</svg>
					{/if}
				</button>
			</div>
		</div>
	</nav>
	<main class="mx-auto max-w-[1400px] px-6 py-6">
		{@render children()}
	</main>
</div>
