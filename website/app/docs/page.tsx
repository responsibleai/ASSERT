import DocsSidebar from "./_components/DocsSidebar";
import MarkdownContent from "./_components/MarkdownContent";
import OnThisPage from "./_components/OnThisPage";
import { getDocsIndex, getHeadings } from "./_lib/docs";

export const metadata = {
	title: "ASSERT Documentation · ASSERT Docs",
};

export default function DocsIndex() {
	const index = getDocsIndex();
	const title = index?.title ?? "ASSERT Documentation";
	const description = index?.description;
	const content = index?.content ?? "";
	const headings = getHeadings(content);
	return (
		<div className="docs-layout has-toc">
			<DocsSidebar activeHref="/docs" />
			<main className="docs-main">
				<article className="docs-article">
					<header className="docs-article-header">
						<h1 className="docs-title">{title}</h1>
						{description && <p className="docs-lede">{description}</p>}
					</header>
					<MarkdownContent source={content} />
				</article>
			</main>
			<OnThisPage headings={headings} />
		</div>
	);
}
