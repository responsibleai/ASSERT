// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

import { browser } from '$app/environment';
import { readable } from 'svelte/store';

export interface ActiveRunSummary {
	suiteId: string;
	runId: string;
	status: string;
	startedAt: string | null;
	currentStage: string | null;
	stages: Record<string, string>;
}

export const activeRuns = readable<ActiveRunSummary[]>([], (set) => {
	if (!browser) return undefined;

	let stopped = false;

	async function refresh() {
		try {
			const response = await fetch('/api/runs');
			if (!response.ok || stopped) return;
			set((await response.json()) as ActiveRunSummary[]);
		} catch {
			if (!stopped) set([]);
		}
	}

	refresh();
	const pollTimer = setInterval(refresh, 3000);

	return () => {
		stopped = true;
		clearInterval(pollTimer);
	};
});
