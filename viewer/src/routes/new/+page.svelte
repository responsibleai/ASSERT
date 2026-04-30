<script lang="ts">
	// Placeholder for the new evaluation run wizard.
	// The visual entry point matches viewer-next; the wizard itself is not yet ported to Svelte.
</script>

<div class="mb-6">
	<a href="/" class="text-sm text-interactive no-underline hover:underline">← Measurement suites</a>
</div>

<div class="rounded-lg border border-border bg-surface p-6">
	<h1 class="text-xl font-semibold tracking-tight">New evaluation run</h1>
	<p class="mt-2 text-sm text-text-muted">
		The new-run wizard is still managed via the CLI. To create a new evaluation run, use:
	</p>
	<pre class="mt-3 rounded-md bg-bg/60 px-3 py-2 font-mono text-xs text-text-secondary">uv run p2m run --config &lt;config.yaml&gt;</pre>
	<p class="mt-4 text-sm text-text-muted">
		Once the run starts it will appear in the active runs section on the home page.
	</p>
</div>
