<!-- Copyright (c) Microsoft Corporation.
     Licensed under the MIT License. -->

<!--
  Primer-style pagination.
  https://primer.style/product/components/pagination/
-->
<script lang="ts">
	interface Props {
		page: number;
		totalPages: number;
		onPageChange: (page: number) => void;
		ariaLabel?: string;
	}
	let { page, totalPages, onPageChange, ariaLabel = 'Pagination' }: Props = $props();

	// Window of pages to render. Mirrors GitHub's behavior:
	//  - Always show 1 and totalPages
	//  - Show ±1 around current
	//  - Insert "…" gaps for omitted ranges
	let pageItems = $derived.by(() => {
		const items: Array<{ kind: 'page'; n: number } | { kind: 'gap' }> = [];
		if (totalPages <= 7) {
			for (let i = 1; i <= totalPages; i++) items.push({ kind: 'page', n: i });
			return items;
		}
		const window = new Set<number>([1, totalPages, page, page - 1, page + 1]);
		const sorted = [...window].filter((n) => n >= 1 && n <= totalPages).sort((a, b) => a - b);
		let prev = 0;
		for (const n of sorted) {
			if (n - prev > 1) items.push({ kind: 'gap' });
			items.push({ kind: 'page', n });
			prev = n;
		}
		return items;
	});

	function go(n: number) {
		if (n < 1 || n > totalPages || n === page) return;
		onPageChange(n);
	}
</script>

<nav class="paginate-container" aria-label={ariaLabel}>
	<div class="pagination">
		<button
			type="button"
			class="previous_page"
			aria-label="Previous page"
			rel="previous"
			disabled={page <= 1}
			onclick={() => go(page - 1)}
		>Previous</button>

		{#each pageItems as item}
			{#if item.kind === 'gap'}
				<span class="gap" aria-hidden="true">…</span>
			{:else if item.n === page}
				<em class="current" aria-current="page">{item.n}</em>
			{:else}
				<button type="button" onclick={() => go(item.n)} aria-label={`Page ${item.n}`}>{item.n}</button>
			{/if}
		{/each}

		<button
			type="button"
			class="next_page"
			aria-label="Next page"
			rel="next"
			disabled={page >= totalPages}
			onclick={() => go(page + 1)}
		>Next</button>
	</div>
</nav>
