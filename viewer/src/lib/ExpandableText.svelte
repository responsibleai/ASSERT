<script lang="ts">
	import { untrack } from 'svelte';

	interface Props {
		text: string;
		clampLines?: number;
		/**
		 * Cap on the paragraph height when expanded. Long descriptions scroll
		 * inside the paragraph past this point so they don't push the rest of
		 * the page around when toggled. Accepts any CSS length (e.g. "12em",
		 * "200px", "40vh"). Defaults to "10em".
		 */
		expandedMaxHeight?: string;
		class?: string;
	}

	let {
		text,
		clampLines = 2,
		expandedMaxHeight = '10em',
		class: className = ''
	}: Props = $props();

	let el: HTMLParagraphElement | undefined = $state();
	let expanded = $state(false);
	let overflows = $state(false);

	function measure() {
		if (!el) return;
		overflows = el.scrollHeight > el.clientHeight + 1;
	}

	$effect(() => {
		// Track props so the effect re-runs when this component instance is reused
		// for a different seed/dimension inside an {#each} block.
		void text;
		void clampLines;
		if (!el) return;
		untrack(() => {
			expanded = false;
		});
		measure();
		const ro = new ResizeObserver(() => {
			if (!expanded) measure();
		});
		ro.observe(el);
		return () => ro.disconnect();
	});

	function toggle(e: MouseEvent) {
		e.stopPropagation();
		expanded = !expanded;
		if (!expanded) queueMicrotask(measure);
	}
</script>

<p
	bind:this={el}
	class={className}
	class:clamped={!expanded}
	class:expanded
	style="--clamp-lines: {clampLines}; --expanded-max-height: {expandedMaxHeight};"
>{text}</p>
{#if overflows || expanded}
	<button
		type="button"
		class="mt-1 text-[11px] font-medium text-interactive hover:underline focus:outline-none focus-visible:ring-1 focus-visible:ring-interactive"
		aria-expanded={expanded}
		onclick={toggle}
	>{expanded ? 'Show less' : 'Show more'}</button>
{/if}

<style>
	.clamped {
		display: -webkit-box;
		-webkit-line-clamp: var(--clamp-lines);
		-webkit-box-orient: vertical;
		line-clamp: var(--clamp-lines);
		overflow: hidden;
	}
	.expanded {
		max-height: var(--expanded-max-height);
		overflow-y: auto;
		overscroll-behavior: contain;
	}
</style>
