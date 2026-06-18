import { getDocsNav, getDocsSearchIndex } from "../_lib/docs";
import DocsSidebarClient from "./DocsSidebarClient";

export default function DocsSidebar({ activeHref }: { activeHref?: string }) {
	const nav = getDocsNav();
	const searchIndex = getDocsSearchIndex();
	return <DocsSidebarClient nav={nav} searchIndex={searchIndex} activeHref={activeHref} />;
}
