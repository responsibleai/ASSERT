"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import rehypeSlug from "rehype-slug";
import "highlight.js/styles/github-dark.css";

export default function MarkdownContent({ source }: { source: string }) {
	return (
		<div className="docs-prose">
			<ReactMarkdown
				remarkPlugins={[remarkGfm]}
				rehypePlugins={[rehypeSlug, rehypeHighlight]}
			>
				{source}
			</ReactMarkdown>
		</div>
	);
}
