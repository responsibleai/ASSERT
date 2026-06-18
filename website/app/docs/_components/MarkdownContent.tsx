"use client";

import {
	isValidElement,
	useRef,
	useState,
	type AnchorHTMLAttributes,
	type ComponentPropsWithoutRef,
	type ReactNode,
} from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import rehypeRaw from "rehype-raw";
import rehypeSlug from "rehype-slug";
import "highlight.js/styles/github-dark.css";

// Repo-relative asset references in the source markdown (e.g. `../assets/foo.png`)
// must be rewritten to URLs that resolve under the site basePath.
const BASE_PATH = "/ASSERT";

// GitHub blob URL used when a markdown link escapes the docs/ tree
// (e.g. `../examples/README.md`). Matches the "GitHub repo" link in
// app/page.tsx and TopNav.tsx so the source-of-truth repo is consistent.
const GITHUB_BLOB_BASE =
	process.env.NEXT_PUBLIC_DOCS_GITHUB_BLOB_BASE ??
	"https://github.com/microsoft/ASSERT/blob/main";

function rewriteAssetPaths(source: string): string {
	// Matches `./assets/` or any depth of `../assets/` after a quote or paren
	return source.replace(/(["'(])(?:\.\.\/)+assets\//g, `$1${BASE_PATH}/assets/`)
		.replace(/(["'(])\.\/assets\//g, `$1${BASE_PATH}/assets/`);
}

// Resolve a POSIX-style relative path against a base directory. Unlike
// `node:path.resolve`, an ascent (`..`) above the base is preserved in the
// output (as a leading `..` segment) so callers can detect that the link
// escaped the docs tree.
function resolveRelativePath(baseDir: string, target: string): string {
	const segments: string[] = [];
	const all = [...baseDir.split("/"), ...target.split("/")];
	for (const segment of all) {
		if (segment === "" || segment === ".") continue;
		if (segment === "..") {
			if (segments.length > 0 && segments[segments.length - 1] !== "..") {
				segments.pop();
			} else {
				segments.push("..");
			}
			continue;
		}
		segments.push(segment);
	}
	return segments.join("/");
}

// Split `path.md#anchor?query` into pieces so we can rewrite just the path part.
function splitLinkHref(href: string): { path: string; suffix: string } {
	const hashIdx = href.indexOf("#");
	const queryIdx = href.indexOf("?");
	const cutAt = [hashIdx, queryIdx].filter((i) => i >= 0).sort((a, b) => a - b)[0] ?? -1;
	if (cutAt === -1) return { path: href, suffix: "" };
	return { path: href.slice(0, cutAt), suffix: href.slice(cutAt) };
}

// Is this an "external-ish" link we should leave alone?
function isExternalHref(href: string): boolean {
	return (
		href.startsWith("http://") ||
		href.startsWith("https://") ||
		href.startsWith("//") ||
		href.startsWith("mailto:") ||
		href.startsWith("tel:") ||
		href.startsWith("#") ||
		href.startsWith("/")
	);
}

// Convert a doc-relative markdown link into a site URL.
//
// `currentRelativePath` is the source doc's path relative to the docs root
// (e.g. "targets/README.md" or "getting-started.md"). Top-level docs use "".
//
// Returns null if the href should be left untouched.
function rewriteMarkdownLink(
	href: string,
	currentRelativePath: string,
): string | null {
	if (!href || isExternalHref(href)) return null;
	const { path: rawPath, suffix } = splitLinkHref(href);
	if (!/\.(md|mdx)$/i.test(rawPath)) return null;

	// Normalize Windows-style separators just in case (the data layer already
	// does this, but be defensive: a bad currentRelativePath here would produce
	// flattened URLs that 404).
	const normalizedCurrent = currentRelativePath.replace(/\\/g, "/");

	// Source doc's directory relative to docs/ root.
	const lastSlash = normalizedCurrent.lastIndexOf("/");
	const baseDir = lastSlash >= 0 ? normalizedCurrent.slice(0, lastSlash) : "";

	const resolved = resolveRelativePath(baseDir, rawPath);

	// Anything that climbed above the docs/ root (e.g. ../examples/README.md)
	// is repo content we do not publish on the docs site. Send those to GitHub
	// so the link is at least live and points at the canonical source.
	if (resolved.startsWith("../") || resolved === "..") {
		const fromRepoRoot = resolveRelativePath(
			"docs/" + baseDir,
			rawPath,
		);
		return `${GITHUB_BLOB_BASE}/${fromRepoRoot}${suffix}`;
	}

	// Strip extension, lowercase the slug to match how docs.ts slugs files.
	const noExt = resolved.replace(/\.(md|mdx)$/i, "").toLowerCase();

	// Top-level README is the docs index. Nested READMEs render as their own
	// `/readme/` page (see app/docs/[...slug]/page.tsx and docs.ts).
	if (noExt === "readme") return `${BASE_PATH}/docs/${suffix}`;

	return `${BASE_PATH}/docs/${noExt}/${suffix}`;
}

type MarkdownContentProps = {
	source: string;
	// Source file's path relative to the docs root, e.g. "targets/README.md"
	// or "getting-started.md". Empty string for the top-level docs index.
	relativePath?: string;
};

function CopyIcon() {
	return (
		<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
			<rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
			<path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
		</svg>
	);
}

function CheckIcon() {
	return (
		<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
			<path d="M20 6 9 17l-5-5" />
		</svg>
	);
}

// Languages whose blocks are illustrative (directory trees, plain output) and
// should not show a copy button.
const NON_COPYABLE_LANGUAGES = new Set(["text", "plaintext", "txt", "plain"]);

function getCodeLanguage(children: ReactNode): string | null {
	if (!isValidElement(children)) return null;
	const className: string = (children.props as { className?: string })?.className ?? "";
	const match = className.match(/language-([\w-]+)/);
	return match ? match[1].toLowerCase() : null;
}

function CodeBlock({ children, ...props }: ComponentPropsWithoutRef<"pre">) {
	const preRef = useRef<HTMLPreElement>(null);
	const [copied, setCopied] = useState(false);

	const language = getCodeLanguage(children);
	const copyable = !language || !NON_COPYABLE_LANGUAGES.has(language);

	async function handleCopy() {
		const text = preRef.current?.innerText ?? "";
		try {
			await navigator.clipboard.writeText(text);
		} catch {
			// Fallback for browsers without the async clipboard API
			const textarea = document.createElement("textarea");
			textarea.value = text;
			textarea.style.position = "fixed";
			textarea.style.opacity = "0";
			document.body.appendChild(textarea);
			textarea.select();
			document.execCommand("copy");
			document.body.removeChild(textarea);
		}
		setCopied(true);
		window.setTimeout(() => setCopied(false), 2000);
	}

	if (!copyable) {
		return <pre {...props}>{children}</pre>;
	}

	return (
		<div className="docs-code-block">
			<button
				type="button"
				className="docs-copy-btn"
				onClick={handleCopy}
				aria-label="Copy to clipboard"
				data-tooltip={copied ? "Copied!" : "Copy to Clipboard"}
			>
				{copied ? <CheckIcon /> : <CopyIcon />}
			</button>
			<pre ref={preRef} {...props}>
				{children}
			</pre>
		</div>
	);
}

export default function MarkdownContent({ source, relativePath = "" }: MarkdownContentProps) {
	const anchorRenderer = ({ href, children, ...rest }: AnchorHTMLAttributes<HTMLAnchorElement>) => {
		const rewritten = href ? rewriteMarkdownLink(href, relativePath) : null;
		return (
			<a href={rewritten ?? href} {...rest}>
				{children}
			</a>
		);
	};
	return (
		<div className="docs-prose">
			<ReactMarkdown
				remarkPlugins={[remarkGfm]}
				rehypePlugins={[rehypeRaw, rehypeSlug, rehypeHighlight]}
				components={{ a: anchorRenderer, pre: CodeBlock }}
			>
				{rewriteAssetPaths(source)}
			</ReactMarkdown>
		</div>
	);
}
