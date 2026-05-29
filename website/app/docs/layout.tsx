import TopNav from "../TopNav";

export default function DocsLayout({ children }: { children: React.ReactNode }) {
	return (
		<>
			<TopNav />
			<div className="docs-shell">{children}</div>
		</>
	);
}
