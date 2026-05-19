<script lang="ts">
  import { onMount } from 'svelte';

  interface DropdownOption {
    value: string;
    label: string;
  }

  interface Props {
    options?: DropdownOption[];
    selected?: string;
    onSelect?: (value: string) => void;
    label?: string;
    ariaLabel?: string;
  }

  let {
    options = [],
    selected = '',
    onSelect = () => {},
    label = '',
    ariaLabel = label || 'Select'
  }: Props = $props();

  let open = $state(false);
  let highlightedIndex = $state(-1);
  let triggerButton: HTMLButtonElement;
  let menuList: HTMLUListElement;

  function handleSelect(value: string) {
    selected = value;
    onSelect(value);
    open = false;
    highlightedIndex = -1;
    triggerButton?.focus();
  }

  function handleToggle() {
    open = !open;
    highlightedIndex = -1;
    if (open) {
      setTimeout(() => menuList?.focus(), 0);
    }
  }

  function handleKeyDown(e: KeyboardEvent) {
    if (!open && (e.key === 'Enter' || e.key === ' ' || e.key === 'ArrowDown')) {
      e.preventDefault();
      open = true;
      highlightedIndex = -1;
      setTimeout(() => menuList?.focus(), 0);
      return;
    }

    if (!open) return;

    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault();
        highlightedIndex = Math.min(highlightedIndex + 1, options.length - 1);
        break;
      case 'ArrowUp':
        e.preventDefault();
        highlightedIndex = Math.max(highlightedIndex - 1, -1);
        break;
      case 'Enter':
        e.preventDefault();
        if (highlightedIndex >= 0) {
          handleSelect(options[highlightedIndex].value);
        }
        break;
      case 'Escape':
        e.preventDefault();
        open = false;
        triggerButton?.focus();
        break;
      case ' ':
        e.preventDefault();
        if (highlightedIndex >= 0) {
          handleSelect(options[highlightedIndex].value);
        }
        break;
    }
  }

  function handleMenuBlur(e: FocusEvent) {
    const target = e.relatedTarget as HTMLElement;
    if (target && !target.closest('[data-dropdown-menu]')) {
      open = false;
      highlightedIndex = -1;
    }
  }

  onMount(() => {
    if (!selected && options.length > 0) {
      selected = options[0].value;
    }
  });

  $effect(() => {
    if (open && highlightedIndex >= 0 && menuList) {
      const items = menuList.querySelectorAll('[role="option"]');
      const item = items[highlightedIndex] as HTMLElement;
      item?.scrollIntoView({ block: 'nearest' });
    }
  });
</script>

<div class="ActionMenu" data-dropdown-menu>
  <button
    bind:this={triggerButton}
    type="button"
    class="ActionMenu-button"
    aria-haspopup="listbox"
    aria-expanded={open}
    aria-label={ariaLabel}
    onclick={handleToggle}
    onkeydown={handleKeyDown}
  >
    <span class="ActionMenu-button-content">
      {#if label}
        <span class="ActionMenu-button-label">{label}:</span>
      {/if}
      <span class="ActionMenu-button-value">
        {options.find(o => o.value === selected)?.label || 'Select'}
      </span>
    </span>
    <svg class="ActionMenu-button-chevron" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
      <path d="M4 6l4 4 4-4"></path>
    </svg>
  </button>

  {#if open}
    <div class="ActionMenu-overlay" onclick={() => (open = false)}></div>
    <ul
      bind:this={menuList}
      class="ActionList ActionMenu-list"
      role="listbox"
      onkeydown={handleKeyDown}
      onblur={handleMenuBlur}
      tabindex={-1}
    >
      {#each options as option, idx}
        <li class="ActionList-item" role="option" aria-selected={selected === option.value}>
          <button
            type="button"
            class="ActionList-content w-full text-left {selected === option.value ? 'ActionList-content--selected' : ''} {highlightedIndex === idx ? 'ActionList-content--highlighted' : ''}"
            onclick={() => handleSelect(option.value)}
            onmouseenter={() => (highlightedIndex = idx)}
            onmouseleave={() => (highlightedIndex = -1)}
          >
            <span class="ActionList-content-text">
              {option.label}
            </span>
          </button>
        </li>
      {/each}
    </ul>
  {/if}
</div>

<style>
  :global(.ActionMenu) {
    position: relative;
    display: inline-block;
  }

  :global(.ActionMenu-overlay) {
    position: fixed;
    inset: 0;
    z-index: 9;
  }

  :global(button.ActionMenu-button) {
    display: inline-flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.5rem;
    padding: 0 0.75rem;
    height: 32px;
    min-height: 32px;
    background-color: var(--bgColor-default, #ffffff);
    border: 1px solid var(--borderColor-default, #d1d9e0);
    border-radius: 0.375rem;
    color: var(--fgColor-default, #1f2328);
    font-size: 0.875rem;
    font-weight: 500;
    line-height: 1.5;
    cursor: pointer;
    transition: background-color 0.12s, border-color 0.12s, box-shadow 0.12s;
    white-space: nowrap;
    box-sizing: border-box;
  }

  :global(button.ActionMenu-button:hover) {
    background-color: var(--bgColor-neutral-muted, rgba(175, 184, 193, 0.08));
    border-color: var(--borderColor-default, #d1d9e0);
  }

  :global(.ActionMenu-button:focus-visible) {
    outline: 2px solid var(--fgColor-accent, #0969da);
    outline-offset: 2px;
  }

  :global(.ActionMenu-button-content) {
    display: inline-flex;
    align-items: center;
    gap: 0.375rem;
    min-width: 0;
  }

  :global(.ActionMenu-button-label) {
    color: var(--fgColor-muted, #59636e);
    font-size: 0.75rem;
  }

  :global(.ActionMenu-button-value) {
    font-weight: 500;
  }

  :global(.ActionMenu-button-chevron) {
    width: 1rem;
    height: 1rem;
    flex-shrink: 0;
    color: var(--fgColor-muted, #59636e);
    transition: transform 0.12s;
  }

  :global(.ActionMenu-button[aria-expanded="true"] .ActionMenu-button-chevron) {
    transform: rotate(180deg);
  }

  :global(.ActionMenu-list) {
    position: absolute;
    z-index: 10;
    top: calc(100% + 0.25rem);
    left: 0;
    min-width: 12rem;
    margin: 0;
    padding: 0.25rem;
    background-color: var(--bgColor-default, #ffffff);
    border: 1px solid var(--borderColor-default, #d1d9e0);
    border-radius: 0.375rem;
    box-shadow: var(--shadow-floating-small, 0 1px 3px rgba(31, 35, 40, 0.12), 0 8px 24px rgba(140, 149, 159, 0.12));
    list-style: none;
    max-height: 20rem;
    overflow-y: auto;
    outline: none;
  }

  :global(.ActionList-item) {
    display: block;
    list-style: none;
  }

  :global(.ActionList-content) {
    display: block;
    width: 100%;
    padding: 0.625rem 0.75rem;
    background: transparent;
    border: none;
    border-radius: 0.25rem;
    color: var(--fgColor-default, #1f2328);
    font-size: 0.875rem;
    line-height: 1.5;
    cursor: pointer;
    text-align: left;
    transition: background-color 0.12s, color 0.12s;
  }

  :global(.ActionList-content:hover) {
    background-color: var(--bgColor-neutral-muted, rgba(175, 184, 193, 0.12));
  }

  :global(.ActionList-content--highlighted) {
    background-color: var(--bgColor-neutral-muted, rgba(175, 184, 193, 0.16));
  }

  :global(.ActionList-content--selected) {
    background-color: var(--bgColor-neutral-muted, rgba(175, 184, 193, 0.2));
    color: var(--fgColor-default, #1f2328);
    font-weight: 500;
  }

  :global(.ActionList-content:focus-visible) {
    outline: 2px solid var(--fgColor-accent, #0969da);
    outline-offset: -2px;
  }

  :global(.ActionList-content-text) {
    display: block;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  :global(html[data-color-mode="dark"] .ActionMenu-button) {
    background-color: var(--bgColor-default, #0d1117);
    border-color: var(--borderColor-default, #30363d);
    color: var(--fgColor-default, #e6edf3);
  }

  :global(html[data-color-mode="dark"] .ActionMenu-button:hover) {
    background-color: var(--bgColor-neutral-muted, rgba(48, 54, 61, 0.4));
  }

  :global(html[data-color-mode="dark"] .ActionMenu-list) {
    background-color: var(--bgColor-default, #0d1117);
    border-color: var(--borderColor-default, #30363d);
  }

  :global(html[data-color-mode="dark"] .ActionList-content) {
    color: var(--fgColor-default, #e6edf3);
  }

  :global(html[data-color-mode="dark"] .ActionList-content:hover) {
    background-color: var(--bgColor-neutral-muted, rgba(48, 54, 61, 0.32));
  }

  :global(html[data-color-mode="dark"] .ActionList-content--highlighted) {
    background-color: var(--bgColor-neutral-muted, rgba(48, 54, 61, 0.4));
  }

  :global(html[data-color-mode="dark"] .ActionList-content--selected) {
    background-color: var(--bgColor-neutral-muted, rgba(48, 54, 61, 0.48));
  }
</style>
