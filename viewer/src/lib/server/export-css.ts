// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

/**
 * Resolve the viewer's compiled CSS so we can inline it in the standalone
 * export HTML. Works in both `vite dev` and `vite build` because we just
 * grab the live page over a SvelteKit-internal fetch and follow whatever
 * `<link rel="stylesheet">` and `<style>` tags it advertises.
 *
 * The export HTML is meant to open offline, so we resolve the URLs to
 * actual CSS text and bundle them into a single `<style>` block.
 */

const CACHE = new Map<string, { css: string; loadedAt: number }>();
const CACHE_TTL_MS = 5 * 60 * 1000;

export async function loadInlineCss(fetch: typeof globalThis.fetch): Promise<string> {
	const cacheKey = 'root';
	const cached = CACHE.get(cacheKey);
	if (cached && Date.now() - cached.loadedAt < CACHE_TTL_MS) {
		return cached.css;
	}

	const css = await resolveCss(fetch);
	CACHE.set(cacheKey, { css, loadedAt: Date.now() });
	return css;
}

async function resolveCss(fetch: typeof globalThis.fetch): Promise<string> {
	let pageHtml: string;
	try {
		const res = await fetch('/');
		if (!res.ok) {
			console.warn(`[export-css] root page fetch returned ${res.status}`);
			return '';
		}
		pageHtml = await res.text();
	} catch (err) {
		console.warn('[export-css] failed to fetch root page:', err);
		return '';
	}

	const styleHrefs = extractStylesheetHrefs(pageHtml);
	const inlineStyles = extractInlineStyles(pageHtml);

	const fetched: string[] = [];
	for (const href of styleHrefs) {
		try {
			const res = await fetch(href);
			if (!res.ok) {
				console.warn(`[export-css] stylesheet ${href} returned ${res.status}`);
				continue;
			}
			fetched.push(`/* ${href} */\n${await res.text()}`);
		} catch (err) {
			console.warn(`[export-css] failed to fetch stylesheet ${href}:`, err);
		}
	}

	return [...fetched, ...inlineStyles].join('\n\n');
}

function extractStylesheetHrefs(html: string): string[] {
	const hrefs: string[] = [];
	const linkRe = /<link\b[^>]*>/gi;
	for (const match of html.matchAll(linkRe)) {
		const tag = match[0];
		if (!/rel\s*=\s*["']?stylesheet["']?/i.test(tag)) continue;
		const hrefMatch = tag.match(/href\s*=\s*["']([^"']+)["']/i);
		if (hrefMatch) hrefs.push(hrefMatch[1]);
	}
	return hrefs;
}

function extractInlineStyles(html: string): string[] {
	const styles: string[] = [];
	const styleRe = /<style\b[^>]*>([\s\S]*?)<\/style>/gi;
	for (const match of html.matchAll(styleRe)) {
		styles.push(match[1]);
	}
	return styles;
}
