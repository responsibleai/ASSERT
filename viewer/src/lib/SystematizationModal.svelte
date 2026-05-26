<script lang="ts">
	import { renderMarkdown } from '$lib/markdown';

	interface SummaryItem {
		description: string;
		example: string;
	}

	function parseSummaryItems(raw: unknown): SummaryItem[] {
		if (!Array.isArray(raw)) return [];
		return raw
			.filter(
				(item): item is SummaryItem =>
					typeof item === 'object' &&
					item !== null &&
					typeof (item as Record<string, unknown>).description === 'string' &&
					typeof (item as Record<string, unknown>).example === 'string'
			)
			.map((item) => ({
				description: item.description,
				example: item.example
			}));
	}

	function parseSystematization(raw: unknown): string {
		return typeof raw === 'string' ? raw.trim() : '';
	}

	function parseMode(raw: unknown): string | null {
		if (!raw || typeof raw !== 'object') return null;
		const meta = (raw as Record<string, unknown>).meta;
		if (!meta || typeof meta !== 'object') return null;
		return typeof (meta as Record<string, unknown>).mode === 'string'
			? String((meta as Record<string, unknown>).mode)
			: null;
	}

	let {
		open = $bindable(),
		systematization
	}: {
		open: boolean;
		systematization: Record<string, unknown> | null;
	} = $props();

	let items = $derived(parseSummaryItems(systematization?.summary_items));
	let systematizationText = $derived(parseSystematization(systematization?.systematization));
	let mode = $derived(parseMode(systematization));
</script>

{#if open && systematization}
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div
		class="fixed inset-0 z-50 flex items-center justify-center p-4"
		onkeydown={(e) => {
			if (e.key === 'Escape') open = false;
		}}
	>
		<button
			class="absolute inset-0 bg-black/60 backdrop-blur-sm"
			onclick={() => (open = false)}
			aria-label="Close"
		></button>
		<div class="relative flex max-h-[85vh] w-full max-w-4xl flex-col rounded-xl border border-border bg-bg shadow-2xl">
			<div class="flex items-center justify-between border-b border-border px-6 py-4">
				<div>
					<h2 class="text-base font-semibold text-text">Systematization</h2>
					<p class="mt-0.5 text-xs text-text-muted">
						Operational map used to generate behavior categories
						{#if mode}
							<span class="text-text-secondary">· {mode}</span>
						{/if}
					</p>
				</div>
				<button
					onclick={() => (open = false)}
					aria-label="Close systematization report"
					class="rounded-lg p-1.5 text-text-muted transition-colors hover:bg-surface hover:text-text"
				>
					<svg class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path d="M6 18L18 6M6 6l12 12"/></svg>
				</button>
			</div>
			<div class="flex-1 space-y-6 overflow-y-auto px-6 py-5">
				{#if items.length > 0}
					<div>
						<h3 class="mb-3 text-xs font-semibold uppercase tracking-wider text-text-muted">
							Pattern Summaries ({items.length})
						</h3>
						<div class="space-y-2">
							{#each items as item, idx}
								<div class="rounded-lg border border-border bg-surface px-4 py-3">
									<div class="flex items-start gap-2">
										<span class="mt-0.5 flex h-5 w-5 flex-shrink-0 items-center justify-center rounded-full bg-surface-2 text-[10px] font-mono text-text-muted">
											{idx + 1}
										</span>
										<div class="min-w-0 flex-1">
											<p class="text-sm leading-relaxed text-text">{item.description}</p>
											{#if item.example}
												<div class="mt-2 border-l-2 border-interactive/30 pl-3 text-xs italic text-text-muted">
													"{item.example}"
												</div>
											{/if}
										</div>
									</div>
								</div>
							{/each}
						</div>
					</div>
				{/if}

				{#if systematizationText}
					<div>
						<h3 class="mb-3 text-xs font-semibold uppercase tracking-wider text-text-muted">
							Full Systematization
						</h3>
						<div class="prose prose-sm max-w-none text-text-secondary">
							{@html renderMarkdown(systematizationText)}
						</div>
					</div>
				{/if}
			</div>
		</div>
	</div>
{/if}
