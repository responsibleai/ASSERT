<script lang="ts">
	import { renderMarkdown } from '$lib/markdown';
	import { formatFactorLabel } from '$lib/grouping.js';
	import type { SeedTool, ViewerSeedGroup } from '$lib/types.js';
	import { slide } from 'svelte/transition';
	import { quintOut } from 'svelte/easing';

	let {
		groups,
		expandedGroup,
		onToggle
	}: {
		groups: ViewerSeedGroup[];
		expandedGroup: string | null;
		onToggle: (name: string) => void;
	} = $props();

	let expandedSeeds = $state<Set<string>>(new Set());

	function toggleSeed(id: string) {
		const next = new Set(expandedSeeds);
		if (next.has(id)) next.delete(id);
		else next.add(id);
		expandedSeeds = next;
	}

	let expandedTools = $state<Set<string>>(new Set());

	function toggleTools(id: string) {
		const next = new Set(expandedTools);
		if (next.has(id)) next.delete(id);
		else next.add(id);
		expandedTools = next;
	}

	function toolNames(tools: SeedTool[] | undefined): string {
		return tools?.map((tool) => tool.name).join(', ') ?? '';
	}
</script>

<div class="overflow-hidden rounded-lg border border-border">
	{#each groups as group, gIdx (group.name || `_flat_${gIdx}`)}
		{@const isFlat = groups.length === 1 && !group.name}
		<div class={gIdx > 0 ? 'border-t border-border' : ''}>
			{#if !isFlat}
			<button
				class="flex w-full items-center gap-3 px-4 py-2.5 text-left text-sm transition-colors hover:bg-surface {expandedGroup === group.name ? 'bg-surface' : ''}"
				onclick={() => onToggle(group.name)}
			>
				<span class="flex-1 truncate font-medium">{group.name}</span>
				<span class="rounded bg-surface-2 px-2 py-0.5 text-xs font-mono text-text-muted">{group.items.length}</span>
				<svg class="h-3.5 w-3.5 text-text-muted transition-transform duration-200 {expandedGroup === group.name ? 'rotate-90' : ''}" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
					<path d="M9 5l7 7-7 7"/>
				</svg>
			</button>
			{/if}
			{#if isFlat || expandedGroup === group.name}
				<div class="{isFlat ? '' : 'border-t border-border'}" transition:slide={{ duration: isFlat ? 0 : 200, easing: quintOut }}>
					{#if group.definition}
						<div class="border-b border-border bg-surface px-5 py-4">
							<div class="prose text-sm text-text-secondary leading-relaxed">{@html renderMarkdown(group.definition)}</div>
						</div>
					{/if}
					<div class="divide-y divide-border/50">
						{#each group.items as entry, sIdx (entry.id)}
							{@const toolsOpen = expandedTools.has(entry.id)}
							{@const seedOpen = expandedSeeds.has(entry.id)}
							<div class="px-5 py-2.5">
								<button class="flex w-full items-center gap-3 text-left transition-colors hover:bg-surface/50 rounded" onclick={() => toggleSeed(entry.id)}>
									<span class="flex-1 truncate text-sm text-text-secondary">{entry.title}</span>
									<svg class="h-3.5 w-3.5 flex-shrink-0 text-text-muted transition-transform duration-200 {seedOpen ? 'rotate-90' : ''}" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
										<path d="M9 5l7 7-7 7"/>
									</svg>
								</button>
								{#if seedOpen}
									<div class="mt-2 pl-4" transition:slide={{ duration: 150, easing: quintOut }}>
										{#if entry.factors && Object.keys(entry.factors).length > 0}
											<div class="mb-2 flex flex-wrap gap-1.5">
												{#each Object.entries(entry.factors) as [name, value]}
													<span class="inline-flex items-center rounded-full bg-zinc-700 px-2 py-0.5 text-[10px] font-medium text-zinc-200">
														{formatFactorLabel(name)}: {value}
													</span>
												{/each}
											</div>
										{/if}
										<div class="prose text-sm text-text-secondary leading-relaxed">{@html renderMarkdown(entry.description)}</div>
										{#if entry.system_prompt}
											<div class="mt-3 rounded border border-yellow-500/20 bg-yellow-500/5 px-3 py-2">
												<div class="mb-1 text-[10px] font-semibold uppercase tracking-wider text-yellow-400">System prompt</div>
												<div class="text-xs text-text-secondary leading-relaxed">{@html renderMarkdown(entry.system_prompt)}</div>
											</div>
										{/if}
										{#if entry.tools && entry.tools.length > 0}
											<div class="mt-3">
												<button class="group flex items-center gap-1.5" onclick={() => toggleTools(entry.id)}>
													<svg class="h-3 w-3 text-purple-400/60 transition-transform duration-150 {toolsOpen ? 'rotate-90' : ''}" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="3"><path d="M9 5l7 7-7 7"/></svg>
													<span class="text-[11px] font-semibold uppercase tracking-wider text-purple-400">Tools</span>
													<span class="text-[10px] text-text-muted">{toolNames(entry.tools)}</span>
												</button>
												{#if toolsOpen}
													<div class="mt-1.5 space-y-2">
														{#each entry.tools as tool}
															<div class="rounded border border-purple-500/20 bg-purple-500/5 px-3 py-2">
																<span class="font-mono text-xs font-semibold text-purple-300">{tool.name}</span>
																{#if tool.description}
																	<p class="mt-0.5 text-xs text-text-secondary">{tool.description}</p>
																{/if}
																{#if tool.parameters?.length}
																	<div class="mt-1.5 space-y-0.5">
																		{#each tool.parameters as parameter}
																			<div class="flex items-baseline gap-1.5">
																				<span class="inline-flex items-center rounded border border-border/20 bg-surface px-1.5 py-0.5 font-mono text-[10px] text-text-muted">
																					{parameter.name}<span class="ml-0.5 text-text-muted/50">:{parameter.type || 'string'}</span>
																				</span>
																				{#if parameter.description}
																					<span class="text-[10px] text-text-muted/70">{parameter.description}</span>
																				{/if}
																			</div>
																		{/each}
																	</div>
																{/if}
															</div>
														{/each}
													</div>
												{/if}
											</div>
										{/if}
									</div>
								{/if}
							</div>
						{/each}
					</div>
				</div>
			{/if}
		</div>
	{/each}
</div>
