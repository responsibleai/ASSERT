// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

import { error, type Handle } from '@sveltejs/kit';

/**
 * Request guards for the artifact viewer.
 *
 * The viewer serves evaluation artifacts — prompts, model outputs, judge
 * reasoning — with no authentication of its own. Binding to localhost is not by
 * itself a control: any page the operator visits can issue requests to
 * `http://localhost:<port>`, and a hostname the attacker controls can be pointed
 * at 127.0.0.1 to defeat a browser's origin checks (DNS rebinding).
 *
 * Three guards, in order of cost:
 *
 *  1. Host header allow-list. A rebinding attack must send the attacker's
 *     hostname in `Host`, so requiring a loopback name blocks it.
 *  2. Origin check. A cross-origin page may still issue simple requests; those
 *     carry an `Origin` that will not match, and are rejected.
 *  3. Bearer token, when `ASSERT_VIEWER_TOKEN` is set. Required — not optional —
 *     once the server is reachable off-host.
 */

const LOOPBACK_HOSTNAMES = new Set(['localhost', '127.0.0.1', '[::1]', '::1']);

function hostnameOf(hostHeader: string): string {
	// Strip the port. Bracketed IPv6 literals keep their brackets.
	if (hostHeader.startsWith('[')) {
		const end = hostHeader.indexOf(']');
		return end === -1 ? hostHeader.toLowerCase() : hostHeader.slice(0, end + 1).toLowerCase();
	}
	const colon = hostHeader.indexOf(':');
	return (colon === -1 ? hostHeader : hostHeader.slice(0, colon)).toLowerCase();
}

function allowedHosts(): Set<string> {
	const configured = process.env.ASSERT_VIEWER_ALLOWED_HOSTS;
	if (!configured) return LOOPBACK_HOSTNAMES;
	return new Set(
		configured
			.split(',')
			.map((h) => h.trim().toLowerCase())
			.filter(Boolean)
	);
}

function timingSafeEquals(a: string, b: string): boolean {
	if (a.length !== b.length) return false;
	let diff = 0;
	for (let i = 0; i < a.length; i++) {
		diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
	}
	return diff === 0;
}

export const handle: Handle = async ({ event, resolve }) => {
	const token = process.env.ASSERT_VIEWER_TOKEN ?? '';
	const hostHeader = event.request.headers.get('host') ?? '';
	const hostname = hostnameOf(hostHeader);
	const permitted = allowedHosts();

	if (!hostHeader || !permitted.has(hostname)) {
		// Without a token there is nothing else standing between an attacker's
		// page and the artifacts, so an unexpected Host is fatal.
		throw error(403, 'Host not allowed');
	}

	// A same-origin browser request either omits Origin or matches the Host.
	const origin = event.request.headers.get('origin');
	if (origin) {
		let originHost: string;
		try {
			originHost = new URL(origin).host.toLowerCase();
		} catch {
			throw error(403, 'Invalid origin');
		}
		if (originHost !== hostHeader.toLowerCase()) {
			throw error(403, 'Cross-origin request rejected');
		}
	}

	if (token) {
		const header = event.request.headers.get('authorization') ?? '';
		const presented = header.startsWith('Bearer ') ? header.slice(7) : '';
		const cookie = event.cookies.get('assert_viewer_token') ?? '';
		const supplied = presented || cookie || event.url.searchParams.get('token') || '';
		if (!timingSafeEquals(supplied, token)) {
			throw error(401, 'Unauthorized');
		}
	}

	return resolve(event);
};
