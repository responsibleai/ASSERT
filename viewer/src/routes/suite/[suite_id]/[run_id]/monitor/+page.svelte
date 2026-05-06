<script lang="ts">
import { onMount, onDestroy } from 'svelte';

let { data } = $props();
let status = $state<string>('connecting');
let currentStage = $state<string | null>(null);
let stages = $state<Record<string, string>>({});
let exitCode = $state<number | null>(null);
let startedAt = $state<string | null>(null);
let elapsed = $state('');
let timerInterval: ReturnType<typeof setInterval> | null = null;
let statusPollTimer: ReturnType<typeof setInterval> | null = null;

const stageIcon: Record<string, string> = {
pending: '○',
running: '⟳',
completed: '✓',
skipped: '–',
error: '✗'
};
const stageColor: Record<string, string> = {
pending: 'text-text-muted',
running: 'text-interactive',
completed: 'text-score-pass',
skipped: 'text-text-muted',
error: 'text-score-fail'
};
const stageBg: Record<string, string> = {
pending: '',
running: 'bg-interactive/10 border-interactive/30',
completed: 'bg-score-pass/5 border-score-pass/20',
skipped: 'border-border/50',
error: 'bg-score-fail/5 border-score-fail/20'
};
const stageLabels: Record<string, string> = {
taxonomy: 'Taxonomy Generation',
seeds: 'Seed Generation',
inference: 'Inference',
judge: 'Scoring',
systematization: 'Systematization',
systematization_convert: 'Taxonomy Conversion'
};

let currentStageLabel = $derived.by(() => {
if (!currentStage) return null;
return stageLabels[currentStage] ?? currentStage;
});

function formatStageStatus(status: string): string {
if (status === 'completed') return 'Complete';
if (status === 'pending') return 'Pending';
if (status === 'running') return 'Running';
if (status === 'skipped') return 'Skipped';
if (status === 'error') return 'Error';
return status;
}

function formatElapsed(start: string): string {
const ms = Date.now() - new Date(start).getTime();
const s = Math.floor(ms / 1000);
if (s < 60) return `${s}s`;
const m = Math.floor(s / 60);
const rs = s % 60;
if (m < 60) return `${m}m ${rs}s`;
const h = Math.floor(m / 60);
return `${h}h ${m % 60}m`;
}

async function fetchStatus() {
try {
const res = await fetch(`/api/runs/${data.suite_id}/${data.run_id}/status`);
if (!res.ok) return;
const d = await res.json();
status = d.status;
currentStage = d.currentStage;
stages = d.stages;
exitCode = d.exitCode;
if (d.startedAt && !startedAt) startedAt = d.startedAt;
if (d.status !== 'running' && statusPollTimer) {
clearInterval(statusPollTimer);
statusPollTimer = null;
}
} catch {
// ignore
}
}

onMount(() => {
fetchStatus();
statusPollTimer = setInterval(fetchStatus, 1500);
timerInterval = setInterval(() => {
if (startedAt && status === 'running') elapsed = formatElapsed(startedAt);
}, 1000);
});

onDestroy(() => {
if (timerInterval) clearInterval(timerInterval);
if (statusPollTimer) clearInterval(statusPollTimer);
});
</script>

<div class="mb-6">
<div class="flex items-center gap-1.5 text-xs text-text-muted">
<a href="/" class="transition-colors hover:text-interactive">Measurement Suites</a>
<svg class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path d="M9 5l7 7-7 7"/></svg>
<a href="/suite/{data.suite_id}" class="transition-colors hover:text-interactive">{data.suite_id}</a>
<svg class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path d="M9 5l7 7-7 7"/></svg>
<a href="/suite/{data.suite_id}/{data.run_id}" class="transition-colors hover:text-interactive">{data.run_id}</a>
<svg class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path d="M9 5l7 7-7 7"/></svg>
<span class="text-text-secondary">Monitor</span>
</div>
<div class="mt-3 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
<div class="flex flex-wrap items-center gap-3">
<h1 class="font-mono text-xl font-semibold tracking-tight">{data.run_id}</h1>
<span class="inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium {status === 'running' ? 'bg-interactive/10 text-interactive' : status === 'completed' ? 'bg-score-pass/10 text-score-pass' : status === 'failed' ? 'bg-score-fail/10 text-score-fail' : 'bg-surface-2 text-text-muted'}">
{#if status === 'running'}
<span class="h-1.5 w-1.5 animate-pulse rounded-full bg-interactive"></span>
{/if}
{status}
</span>
{#if elapsed && status === 'running'}
<span class="text-xs text-text-muted tabular-nums">{elapsed}</span>
{/if}
</div>
<div class="flex flex-wrap items-center gap-2">
{#if status === 'running'}
<span class="inline-flex items-center gap-1 rounded-full bg-interactive/10 px-2.5 py-0.5 text-xs text-interactive">
Read-only monitor
</span>
{:else if status === 'completed'}
<a
href="/suite/{data.suite_id}/{data.run_id}"
class="inline-flex items-center gap-1.5 rounded-md bg-interactive px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-interactive-hover"
>
View Results
<svg class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path d="M13 7l5 5m0 0l-5 5m5-5H6"/></svg>
</a>
{:else if status === 'failed'}
<span class="inline-flex items-center gap-1 rounded-full bg-score-fail/10 px-2.5 py-0.5 text-xs text-score-fail">
Exit code: {exitCode}
</span>
{/if}
</div>
</div>
</div>

{#if currentStageLabel || startedAt}
<div class="mb-6 grid gap-3 lg:grid-cols-[minmax(0,1.5fr)_minmax(20rem,1fr)]">
<div class="rounded-[1.25rem] border border-interactive/15 bg-[radial-gradient(circle_at_top_left,rgba(58,130,246,0.16),transparent_52%),linear-gradient(135deg,rgba(15,23,42,0.9),rgba(15,23,42,0.72))] p-5 text-white shadow-[0_20px_60px_rgba(15,23,42,0.18)]">
<div class="text-[11px] font-semibold uppercase tracking-[0.22em] text-white/55">Current Focus</div>
<div class="mt-3 flex flex-wrap items-center gap-3">
<div class="inline-flex h-10 w-10 items-center justify-center rounded-2xl bg-white/10 text-lg">{status === 'failed' ? '✗' : status === 'completed' ? '✓' : '→'}</div>
<div class="min-w-0">
<div class="truncate text-lg font-semibold tracking-tight">{currentStageLabel ?? 'Preparing run'}</div>
<div class="mt-1 text-sm text-white/65">
{#if status === 'running'}
Watching manifest updates from the artifacts directory
{:else if status === 'completed'}
Run finished successfully
{:else if status === 'failed'}
Run stopped with an error
{:else}
Waiting for status updates
{/if}
</div>
</div>
</div>
</div>
<div class="rounded-[1.25rem] border border-border bg-surface p-5">
<div class="grid gap-4 sm:grid-cols-2">
<div>
<div class="text-[11px] font-semibold uppercase tracking-[0.2em] text-text-muted">Suite</div>
<div class="mt-2 truncate font-mono text-sm text-text">{data.suite_id}</div>
</div>
<div>
<div class="text-[11px] font-semibold uppercase tracking-[0.2em] text-text-muted">Run</div>
<div class="mt-2 truncate font-mono text-sm text-text">{data.run_id}</div>
</div>
<div>
<div class="text-[11px] font-semibold uppercase tracking-[0.2em] text-text-muted">Started</div>
<div class="mt-2 text-sm text-text-secondary">{startedAt ? new Date(startedAt).toLocaleString() : 'Waiting…'}</div>
</div>
<div>
<div class="text-[11px] font-semibold uppercase tracking-[0.2em] text-text-muted">Progress</div>
<div class="mt-2 text-sm text-text-secondary">
{Object.values(stages).filter((s) => s === 'completed').length}/{Object.keys(stages).length || '—'} stages complete
</div>
</div>
</div>
</div>
</div>
{/if}

{#if Object.keys(stages).length > 0}
<div class="mb-6 rounded-lg border border-border bg-surface p-4">
<div class="flex items-center gap-2 mb-3">
<h2 class="text-xs font-semibold uppercase tracking-widest text-text-muted">Pipeline</h2>
</div>

<div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
{#each Object.entries(stages) as [name, st]}
<div class="relative rounded-lg border p-3.5 transition-all {stageBg[st] || 'border-border/50 bg-bg/30'}">
<div class="flex items-start justify-between gap-3">
<div class="min-w-0">
<div class="text-sm font-medium text-text">{stageLabels[name] ?? name}</div>
<div class="mt-0.5 text-[10px] uppercase tracking-wider text-text-muted">Pipeline stage</div>
</div>
<span class="inline-flex shrink-0 items-center gap-1 rounded-full bg-bg px-2 py-0.5 text-[10px] font-medium {stageColor[st]}">
<span>{stageIcon[st]}</span>
{formatStageStatus(st)}
</span>
</div>

<div class="mt-3 text-[10px] text-text-muted/60">Status loaded from <span class="font-mono">manifest.json</span></div>

{#if st === 'running'}
<div class="absolute bottom-0 left-0 right-0 h-0.5 overflow-hidden rounded-b-lg">
<div class="h-full w-1/3 animate-[shimmer_1.5s_ease-in-out_infinite] bg-interactive/50 rounded-full"></div>
</div>
{/if}
</div>
{/each}
</div>
</div>
{/if}

<div class="rounded-lg border border-border bg-surface p-4 text-sm text-text-secondary">
<div class="text-xs font-semibold uppercase tracking-widest text-text-muted">About this monitor</div>
<p class="mt-2">This page is read-only. It polls the run manifest under <span class="font-mono">artifacts/results/{data.suite_id}/{data.run_id}</span> and does not stream process logs or control runs.</p>
</div>

<style>
@keyframes shimmer {
0% { transform: translateX(-100%); }
100% { transform: translateX(400%); }
}
</style>
