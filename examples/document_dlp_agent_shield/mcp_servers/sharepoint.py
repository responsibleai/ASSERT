"""Mock SharePoint MCP server that reads from a local directory.

Exposes the same tool names as the real mcp-sharepoint package so the demo
agent and guardrails YAML work without changes.  Instead of calling
Microsoft Graph, every operation reads from a local folder specified via
the --root argument.

Document metadata (sensitivity, jurisdictions, title) is loaded from
document_metadata.txt in the same directory as this script.
"""

import argparse
import json
import os

from mcp.server.fastmcp import FastMCP

_METADATA_FILE = os.path.join(os.path.dirname(__file__), "document_metadata.txt")


def _load_metadata() -> dict[str, dict]:
    """Parse document_metadata.txt into a {path: {sensitivity, jurisdictions, title}} dict."""
    metadata: dict[str, dict] = {}
    if not os.path.isfile(_METADATA_FILE):
        return metadata
    with open(_METADATA_FILE, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 4:
                continue
            path, sensitivity, jurisdictions_raw, title = parts[0], parts[1], parts[2], parts[3]
            jurisdictions = [j.strip() for j in jurisdictions_raw.split(",") if j.strip() and j.strip() != "-"]
            metadata[path] = {
                "data_sensitivity": sensitivity,
                "data_jurisdictions": jurisdictions,
                "title": title,
            }
    return metadata


def make_server(root: str) -> FastMCP:
    mcp = FastMCP("sharepoint-mock-mcp")

    @mcp.tool()
    async def Get_SharePoint_Tree(folder_path: str = "") -> str:
        """Get a tree view of the document library structure."""
        base = os.path.join(root, folder_path)
        if not os.path.isdir(base):
            return json.dumps({"error": f"Folder not found: {folder_path}"})

        tree: list[dict] = []
        for dirpath, dirnames, filenames in os.walk(base):
            rel = os.path.relpath(dirpath, root).replace("\\", "/")
            if rel == ".":
                rel = ""
            for d in sorted(dirnames):
                tree.append({"type": "folder", "path": f"{rel}/{d}" if rel else d})
            for f in sorted(filenames):
                tree.append({"type": "file", "path": f"{rel}/{f}" if rel else f})
        return json.dumps({"tree": tree})

    @mcp.tool()
    async def List_SharePoint_Folders(folder_path: str = "") -> str:
        """List folders in the document library."""
        base = os.path.join(root, folder_path)
        if not os.path.isdir(base):
            return json.dumps({"error": f"Folder not found: {folder_path}"})

        folders = sorted(
            entry.name
            for entry in os.scandir(base)
            if entry.is_dir() and not entry.name.startswith(".")
        )
        return json.dumps({"folders": folders})

    @mcp.tool()
    async def List_SharePoint_Documents(folder_path: str = "") -> str:
        """List documents in a folder with basic metadata."""
        base = os.path.join(root, folder_path)
        if not os.path.isdir(base):
            return json.dumps({"error": f"Folder not found: {folder_path}"})

        docs = []
        for entry in sorted(os.scandir(base), key=lambda e: e.name):
            if entry.is_file() and not entry.name.startswith("."):
                stat = entry.stat()
                docs.append({
                    "name": entry.name,
                    "path": os.path.relpath(entry.path, root).replace("\\", "/"),
                    "size": stat.st_size,
                })
        return json.dumps({"documents": docs})

    @mcp.tool()
    async def Get_Document_Content(file_path: str) -> str:
        """Read a document's content from the local mock SharePoint library."""
        full = os.path.normpath(os.path.join(root, file_path))

        # Prevent path traversal outside the root
        if not full.startswith(os.path.normpath(root)):
            return json.dumps({"error": "Access denied — path outside document library"})

        if not os.path.isfile(full):
            return json.dumps({"error": f"Document not found: {file_path}"})

        try:
            with open(full, "r", encoding="utf-8") as fh:
                content = fh.read()
        except UnicodeDecodeError:
            return json.dumps({"error": "Binary file — only text documents are supported in this mock"})

        result: dict = {
            "path": file_path,
            "name": os.path.basename(file_path),
            "content": content,
        }

        # Attach metadata if available
        meta = _load_metadata().get(file_path)
        if meta:
            result.update(meta)

        return json.dumps(result)

    return mcp


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Mock SharePoint MCP server (reads from local directory)"
    )
    parser.add_argument(
        "--root",
        required=True,
        help="Local directory to treat as the SharePoint document library",
    )
    args = parser.parse_args()

    if not os.path.isdir(args.root):
        raise RuntimeError(f"Root directory does not exist: {args.root}")

    mcp = make_server(os.path.abspath(args.root))
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
