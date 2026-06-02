import { error } from '@sveltejs/kit';
import { render } from 'svelte/server';
import { isSafeArtifactId } from '$lib/server/artifacts.js';
import { loadRunPageData, loadPromptDrawerItem, loadScenarioDrawerItem } from '$lib/server/data.js';
import { loadInlineCss } from '$lib/server/export-css.js';
import ExportPage from '$lib/export/ExportPage.svelte';
import type { ViewerResultItem } from '$lib/types.js';
import type { RequestHandler } from './$types.js';

export const GET: RequestHandler = async ({ params, fetch, url }) => {
	if (!isSafeArtifactId(params.suite_id)) throw error(400, 'Invalid suite ID');
	if (!isSafeArtifactId(params.run_id)) throw error(400, 'Invalid run ID');

	const promptPayload = loadRunPageData(params.suite_id, params.run_id, 'prompts');
	const auditPayload = loadRunPageData(params.suite_id, params.run_id, 'audit');
	if (!promptPayload && !auditPayload) {
		throw error(404, `Run "${params.run_id}" not found in suite "${params.suite_id}"`);
	}

	const samples = promptPayload?.samples ?? [];
	const auditScores = auditPayload?.auditScores ?? [];

	const promptDrawerItems: Record<string, ViewerResultItem> = {};
	for (const sample of samples) {
		const id = sample.test_case_id;
		if (!id) continue;
		try {
			const item = await loadPromptDrawerItem(params.suite_id, params.run_id, id);
			if (item) promptDrawerItems[id] = item;
		} catch (err) {
			console.warn(`[export] failed to load prompt drawer item ${id}:`, err);
		}
	}

	const scenarioDrawerItems: Record<string, ViewerResultItem> = {};
	for (const score of auditScores) {
		const id = score.test_case_id;
		if (!id) continue;
		try {
			const item = await loadScenarioDrawerItem(params.suite_id, params.run_id, id);
			if (item) scenarioDrawerItems[id] = item;
		} catch (err) {
			console.warn(`[export] failed to load scenario drawer item ${id}:`, err);
		}
	}

	const css = await loadInlineCss(fetch, url.origin);

	const merged = {
		...(promptPayload ?? auditPayload!),
		samples,
		auditScores,
		promptCount: promptPayload?.promptCount ?? samples.length,
		auditCount: auditPayload?.auditCount ?? auditScores.length,
		hasAuditContent: (auditPayload?.hasAuditContent ?? false) || auditScores.length > 0,
		metrics: promptPayload?.metrics ?? null,
		auditMetrics: auditPayload?.auditMetrics ?? null,
		multiJudgeStats: promptPayload?.multiJudgeStats ?? auditPayload?.multiJudgeStats ?? null,
		auditMultiJudgeStats: auditPayload?.multiJudgeStats ?? null,
		// loadRunPageData's uncompleted-judge path only populates scenarioSeedMap when
		// activeTab='audit', so the prompts call returns {}. Merge both so audit row
		// titles still resolve to scenario descriptions.
		scenarioSeedMap: {
			...(promptPayload?.scenarioSeedMap ?? {}),
			...(auditPayload?.scenarioSeedMap ?? {})
		},
		promptSeedTitleMap: {
			...(promptPayload?.promptSeedTitleMap ?? {}),
			...(auditPayload?.promptSeedTitleMap ?? {})
		},
		dimensionDefs: promptPayload?.dimensionDefs ?? auditPayload?.dimensionDefs ?? null
	};

	const props = {
		data: merged,
		promptDrawerItems,
		scenarioDrawerItems,
		generatedAt: new Date().toISOString()
	};
	const rendered = render(ExportPage as never, { props: props as never });

	const html = `<!doctype html>
<html lang="en" class="dark">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<meta name="generator" content="adaptive-eval-viewer export" />
<title>${escapeHtml(params.suite_id)} / ${escapeHtml(params.run_id)} — Adaptive Eval export</title>
<style>${css.replace(/<\/style/gi, '<\\/style')}</style>
${rendered.head ?? ''}
</head>
<body class="bg-bg text-text">
<main class="mx-auto max-w-[1400px] px-6 py-6">
${rendered.body}
</main>
</body>
</html>
`;

	const filename = `${params.suite_id}__${params.run_id}.html`;
	return new Response(html, {
		headers: {
			'content-type': 'text/html; charset=utf-8',
			'content-disposition': `attachment; filename="${filename}"`,
			'cache-control': 'no-store'
		}
	});
};

function escapeHtml(s: string): string {
	return s
		.replace(/&/g, '&amp;')
		.replace(/</g, '&lt;')
		.replace(/>/g, '&gt;')
		.replace(/"/g, '&quot;')
		.replace(/'/g, '&#39;');
}
