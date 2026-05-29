<!-- Copyright (c) Microsoft Corporation.
     Licensed under the MIT License. -->

<!--
  Primer-style description tooltip.
  Follows https://primer.style/product/getting-started/rails/components/tooltip/
  - Trigger: focusable <button> with the info octicon.
  - Tooltip: <span role="tooltip"> visible on hover/focus, linked via aria-describedby.
-->
<script lang="ts">
	interface Props {
		label: string;
		direction?: 'n' | 's' | 'e' | 'w' | 'ne' | 'nw' | 'se' | 'sw';
	}
	let { label, direction = 's' }: Props = $props();
	let tipId = `tip-${Math.random().toString(36).slice(2, 10)}`;
</script>

<span class="primer-tooltip-wrap">
	<button
		type="button"
		class="primer-tooltip-trigger"
		aria-describedby={tipId}
	>
		<svg class="octicon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="12" height="12" fill="currentColor" aria-hidden="true"><path d="M0 8a8 8 0 1 1 16 0A8 8 0 0 1 0 8Zm8-6.5a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13ZM6.5 7.75A.75.75 0 0 1 7.25 7h1a.75.75 0 0 1 .75.75v2.75h.25a.75.75 0 0 1 0 1.5h-2a.75.75 0 0 1 0-1.5h.25v-2h-.25a.75.75 0 0 1-.75-.75ZM8 6a1 1 0 1 1 0-2 1 1 0 0 1 0 2Z"/></svg>
		<span class="sr-only">More info</span>
	</button>
	<span id={tipId} role="tooltip" class="primer-tooltip primer-tooltip--{direction}">{label}</span>
</span>

<style>
	.primer-tooltip-wrap {
		position: relative;
		display: inline-flex;
		align-items: center;
	}

	.primer-tooltip-trigger {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		padding: 0;
		margin: 0;
		background: transparent;
		border: 0;
		color: var(--fgColor-muted, #6e7781);
		cursor: help;
		line-height: 0;
		border-radius: 50%;
	}
	.primer-tooltip-trigger:hover,
	.primer-tooltip-trigger:focus-visible {
		color: var(--fgColor-accent, #0969da);
		outline: none;
	}
	.primer-tooltip-trigger:focus-visible {
		box-shadow: 0 0 0 2px var(--fgColor-accent, #0969da);
	}
	.primer-tooltip-trigger :global(.octicon) {
		vertical-align: middle;
	}

	/* Tooltip surface — mirrors Primer's <tool-tip> styling. */
	.primer-tooltip {
		position: absolute;
		z-index: 1000000;
		display: block;
		max-width: 250px;
		width: max-content;
		padding: 0.5em 0.75em;
		font: normal normal 12px/1.4
			var(--fontStack-sansSerif, -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans', Helvetica, Arial, sans-serif);
		font-weight: 400;
		color: var(--fgColor-onEmphasis, #ffffff);
		text-align: left;
		white-space: normal;
		word-wrap: break-word;
		background: var(--bgColor-emphasis, #24292f);
		border-radius: var(--borderRadius-medium, 6px);
		box-shadow: var(--shadow-floating-small, 0 1px 3px rgba(0, 0, 0, 0.12), 0 8px 24px rgba(0, 0, 0, 0.15));
		opacity: 0;
		visibility: hidden;
		pointer-events: none;
		transition: opacity 80ms ease-in 40ms, visibility 0s linear 120ms;
	}
	.primer-tooltip-wrap:hover .primer-tooltip,
	.primer-tooltip-wrap:focus-within .primer-tooltip {
		opacity: 1;
		visibility: visible;
		transition-delay: 80ms, 0s;
	}

	.primer-tooltip--s,
	.primer-tooltip--se,
	.primer-tooltip--sw {
		top: calc(100% + 6px);
	}
	.primer-tooltip--s { left: 50%; transform: translateX(-50%); }
	.primer-tooltip--se { left: 0; }
	.primer-tooltip--sw { right: 0; }

	.primer-tooltip--n,
	.primer-tooltip--ne,
	.primer-tooltip--nw {
		bottom: calc(100% + 6px);
	}
	.primer-tooltip--n { left: 50%; transform: translateX(-50%); }
	.primer-tooltip--ne { left: 0; }
	.primer-tooltip--nw { right: 0; }

	.primer-tooltip--e { left: calc(100% + 6px); top: 50%; transform: translateY(-50%); }
	.primer-tooltip--w { right: calc(100% + 6px); top: 50%; transform: translateY(-50%); }

	.sr-only {
		position: absolute;
		width: 1px;
		height: 1px;
		padding: 0;
		margin: -1px;
		overflow: hidden;
		clip: rect(0, 0, 0, 0);
		white-space: nowrap;
		border: 0;
	}
</style>
