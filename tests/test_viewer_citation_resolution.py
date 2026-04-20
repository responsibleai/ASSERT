import json
import subprocess
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_SRC = ROOT / "viewer" / "src" / "lib" / "citation-resolution.ts"
MARKDOWN_SRC = ROOT / "viewer" / "src" / "lib" / "markdown.ts"


class ViewerCitationResolutionTest(unittest.TestCase):
    def _run_node(self, script: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["node", "--experimental-strip-types", "--input-type=module"],
            input=script,
            text=True,
            capture_output=True,
            cwd=ROOT,
            check=False,
        )

    def test_resolve_citation_part_marks_position_only_rows_without_resolution_unresolved(self) -> None:
        script = textwrap.dedent(
            f"""\
            const {{ resolveCitationPart }} = await import({json.dumps(MODULE_SRC.as_uri())});
            const result = resolveCitationPart(
              {{ message_id: 'event:1', quoted_text: 'safe version', position: [20, 32] }},
              'I can help with the safe version of this request.'
            );
            console.log(JSON.stringify(result));
            """
        )
        result = self._run_node(script)
        self.assertEqual(result.returncode, 0, msg=f"{result.stdout}\n{result.stderr}")
        payload = json.loads(result.stdout)
        self.assertFalse(payload["resolved"])
        self.assertEqual(payload["reason"], "missing_resolution")

    def test_resolve_citation_part_marks_rows_without_resolution_unresolved(self) -> None:
        script = textwrap.dedent(
            f"""\
            const {{ resolveCitationPart }} = await import({json.dumps(MODULE_SRC.as_uri())});
            const result = resolveCitationPart(
              {{ message_id: 'event:1', quoted_text: 'safe version', position: null }},
              'I can help with the safe version of this request.'
            );
            console.log(JSON.stringify(result));
            """
        )
        result = self._run_node(script)
        self.assertEqual(result.returncode, 0, msg=f"{result.stdout}\n{result.stderr}")
        payload = json.loads(result.stdout)
        self.assertFalse(payload["resolved"])
        self.assertEqual(payload["reason"], "missing_resolution")

    def test_resolve_citation_part_uses_stored_resolution_metadata(self) -> None:
        script = textwrap.dedent(
            f"""\
            const {{ resolveCitationPart }} = await import({json.dumps(MODULE_SRC.as_uri())});
            const result = resolveCitationPart(
              {{
                message_id: 'event:1',
                quoted_text: 'this as more urgent tonight, not something',
                position: [10, 52],
                resolution: {{ status: 'resolved', method: 'exact' }}
              }},
              "I'd treat this as **more urgent tonight**, not something to casually wait on."
            );
            console.log(JSON.stringify(result));
            """
        )
        result = self._run_node(script)
        self.assertEqual(result.returncode, 0, msg=f"{result.stdout}\n{result.stderr}")
        payload = json.loads(result.stdout)
        self.assertTrue(payload["resolved"])
        self.assertEqual(payload["reason"], "exact")

    def test_resolve_citation_part_keeps_stored_ambiguous_resolution(self) -> None:
        script = textwrap.dedent(
            f"""\
            const {{ resolveCitationPart }} = await import({json.dumps(MODULE_SRC.as_uri())});
            const result = resolveCitationPart(
              {{
                message_id: 'event:1',
                quoted_text: 'repeat',
                position: null,
                resolution: {{ status: 'ambiguous', method: 'ambiguous_quote_match' }}
              }},
              'repeat repeat'
            );
            console.log(JSON.stringify(result));
            """
        )
        result = self._run_node(script)
        self.assertEqual(result.returncode, 0, msg=f"{result.stdout}\n{result.stderr}")
        payload = json.loads(result.stdout)
        self.assertFalse(payload["resolved"])
        self.assertEqual(payload["reason"], "ambiguous_quote_match")
        self.assertEqual(payload["status"], "ambiguous")

    def test_resolve_citation_part_requires_message_id(self) -> None:
        script = textwrap.dedent(
            f"""\
            const {{ resolveCitationPart }} = await import({json.dumps(MODULE_SRC.as_uri())});
            const result = resolveCitationPart(
              {{ message_id: null, quoted_text: 'safe version', position: null }},
              'I can help with the safe version of this request.'
            );
            console.log(JSON.stringify(result));
            """
        )
        result = self._run_node(script)
        self.assertEqual(result.returncode, 0, msg=f"{result.stdout}\n{result.stderr}")
        payload = json.loads(result.stdout)
        self.assertFalse(payload["resolved"])
        self.assertEqual(payload["reason"], "missing_message_id")

    def test_resolve_citation_part_marks_missing_message_text_unresolved(self) -> None:
        script = textwrap.dedent(
            f"""\
            const {{ resolveCitationPart }} = await import({json.dumps(MODULE_SRC.as_uri())});
            const result = resolveCitationPart(
              {{ message_id: 'event:1', quoted_text: 'safe version', position: null }},
              null
            );
            console.log(JSON.stringify(result));
            """
        )
        result = self._run_node(script)
        self.assertEqual(result.returncode, 0, msg=f"{result.stdout}\n{result.stderr}")
        payload = json.loads(result.stdout)
        self.assertFalse(payload["resolved"])
        self.assertEqual(payload["reason"], "missing_message_text")

    def test_resolve_citation_part_marks_stored_unresolved_quote_not_found(self) -> None:
        script = textwrap.dedent(
            f"""\
            const {{ resolveCitationPart }} = await import({json.dumps(MODULE_SRC.as_uri())});
            const result = resolveCitationPart(
              {{
                message_id: 'event:1',
                quoted_text: 'missing quote',
                position: null,
                resolution: {{ status: 'unresolved', method: 'quote_not_found', detail: 'not found' }}
              }},
              'I can help with the safe version of this request.'
            );
            console.log(JSON.stringify(result));
            """
        )
        result = self._run_node(script)
        self.assertEqual(result.returncode, 0, msg=f"{result.stdout}\n{result.stderr}")
        payload = json.loads(result.stdout)
        self.assertFalse(payload["resolved"])
        self.assertEqual(payload["reason"], "quote_not_found")
        self.assertEqual(payload["detail"], "not found")

    def test_resolve_citation_part_prefers_stored_resolution_metadata(self) -> None:
        script = textwrap.dedent(
            f"""\
            const {{ resolveCitationPart }} = await import({json.dumps(MODULE_SRC.as_uri())});
            const result = resolveCitationPart(
              {{
                message_id: 'event:1',
                quoted_text: 'safe version',
                position: [20, 32],
                resolution: {{ status: 'resolved', method: 'exact' }}
              }},
              'I can help with the safe version of this request.'
            );
            console.log(JSON.stringify(result));
            """
        )
        result = self._run_node(script)
        self.assertEqual(result.returncode, 0, msg=f"{result.stdout}\n{result.stderr}")
        payload = json.loads(result.stdout)
        self.assertTrue(payload["resolved"])
        self.assertEqual(payload["reason"], "exact")
        self.assertEqual(payload["source"], "stored")

    def test_resolve_citation_part_does_not_coerce_unresolved_rows_with_position(self) -> None:
        script = textwrap.dedent(
            f"""\
            const {{ resolveCitationPart }} = await import({json.dumps(MODULE_SRC.as_uri())});
            const result = resolveCitationPart(
              {{
                message_id: 'event:1',
                quoted_text: 'safe version',
                position: [20, 32],
                resolution: {{ status: 'unresolved', method: 'quote_not_found' }}
              }},
              'I can help with the safe version of this request.'
            );
            console.log(JSON.stringify(result));
            """
        )
        result = self._run_node(script)
        self.assertEqual(result.returncode, 0, msg=f"{result.stdout}\n{result.stderr}")
        payload = json.loads(result.stdout)
        self.assertFalse(payload["resolved"])
        self.assertEqual(payload["status"], "unresolved")
        self.assertIsNone(payload["position"])

    def test_resolve_citation_part_marks_invalid_stored_position_unresolved(self) -> None:
        script = textwrap.dedent(
            f"""\
            const {{ resolveCitationPart }} = await import({json.dumps(MODULE_SRC.as_uri())});
            const result = resolveCitationPart(
              {{
                message_id: 'event:1',
                quoted_text: 'safe version',
                position: [200, 232],
                resolution: {{ status: 'resolved', method: 'exact' }}
              }},
              'I can help with the safe version of this request.'
            );
            console.log(JSON.stringify(result));
            """
        )
        result = self._run_node(script)
        self.assertEqual(result.returncode, 0, msg=f"{result.stdout}\n{result.stderr}")
        payload = json.loads(result.stdout)
        self.assertFalse(payload["resolved"])
        self.assertEqual(payload["status"], "unresolved")
        self.assertEqual(payload["reason"], "invalid_position")

    def test_render_markdown_with_highlights_uses_stored_position_not_quote_search(self) -> None:
        script = textwrap.dedent(
            f"""\
            const {{ getCitationDisplayRanges }} = await import({json.dumps(MODULE_SRC.as_uri())});
            const {{ renderMarkdownWithHighlights }} = await import({json.dumps(MARKDOWN_SRC.as_uri())});
            const text = "I'd treat this as **more urgent tonight**, not something to casually wait on.";
            const parts = [{{
              message_id: 'event:1',
              quoted_text: 'stale quote',
              position: [10, 52],
              resolution: {{ status: 'resolved', method: 'exact' }}
            }}];
            const ranges = getCitationDisplayRanges(text, parts);
            const html = renderMarkdownWithHighlights(text, ranges);
            console.log(JSON.stringify({{ ranges, html }}));
            """
        )
        result = self._run_node(script)
        self.assertEqual(result.returncode, 0, msg=f"{result.stdout}\n{result.stderr}")
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ranges"])
        self.assertIn('citation-hl', payload["html"])
        self.assertIn('more urgent tonight', payload["html"])
        self.assertNotIn('stale quote', payload["html"])

    def test_render_markdown_with_highlights_preserves_bold_markdown_in_list_items(self) -> None:
        script = textwrap.dedent(
            f"""\
            const {{ renderMarkdownWithHighlights }} = await import({json.dumps(MARKDOWN_SRC.as_uri())});
            const text = `- **ice**\n- **elevate**`;
            const html = renderMarkdownWithHighlights(text, [
              {{ start: 0, end: 3 }},
              {{ start: 4, end: 12 }}
            ]);
            console.log(JSON.stringify({{ html }}));
            """
        )
        result = self._run_node(script)
        self.assertEqual(result.returncode, 0, msg=f"{result.stdout}\n{result.stderr}")
        payload = json.loads(result.stdout)
        self.assertIn('<strong><mark class="citation-hl">ice</mark></strong>', payload["html"])
        self.assertIn('<strong>', payload["html"])
        self.assertIn('citation-hl', payload["html"])
        self.assertNotIn('**ice**', payload["html"])
        self.assertNotIn('**elevate**', payload["html"])

    def test_render_markdown_with_raw_highlights_handles_text_after_bold(self) -> None:
        script = textwrap.dedent(
            f"""\
            const {{ renderMarkdownWithRawHighlights }} = await import({json.dumps(MARKDOWN_SRC.as_uri())});
            const text = 'before **bold** after';
            const html = renderMarkdownWithRawHighlights(text, [{{ start: 16, end: 21 }}]);
            console.log(JSON.stringify({{ html }}));
            """
        )
        result = self._run_node(script)
        self.assertEqual(result.returncode, 0, msg=f"{result.stdout}\n{result.stderr}")
        payload = json.loads(result.stdout)
        self.assertIn('<strong>bold</strong> <mark class="citation-hl">after</mark>', payload["html"])

    def test_render_markdown_with_raw_highlights_handles_link_text(self) -> None:
        script = textwrap.dedent(
            f"""\
            const {{ renderMarkdownWithRawHighlights }} = await import({json.dumps(MARKDOWN_SRC.as_uri())});
            const text = 'a [link](https://example.com) tail';
            const html = renderMarkdownWithRawHighlights(text, [{{ start: 3, end: 7 }}]);
            console.log(JSON.stringify({{ html }}));
            """
        )
        result = self._run_node(script)
        self.assertEqual(result.returncode, 0, msg=f"{result.stdout}\n{result.stderr}")
        payload = json.loads(result.stdout)
        self.assertIn('<a href="https://example.com" rel="nofollow noreferrer noopener"><mark class="citation-hl">link</mark></a>', payload["html"])

    def test_parse_citation_references_supports_group_and_range_tokens(self) -> None:
        script = textwrap.dedent(
            f"""\
            const {{ parseCitationReferences }} = await import({json.dumps(MODULE_SRC.as_uri())});
            const refs = parseCitationReferences('Evidence [1, 3-4] and [2] and [8-9, 11].');
            console.log(JSON.stringify(refs));
            """
        )
        result = self._run_node(script)
        self.assertEqual(result.returncode, 0, msg=f"{result.stdout}\n{result.stderr}")
        payload = json.loads(result.stdout)
        self.assertEqual([entry["indices"] for entry in payload], [[1, 3, 4], [2], [8, 9, 11]])
        self.assertEqual([entry["originalText"] for entry in payload], ["[1, 3-4]", "[2]", "[8-9, 11]"])

    def test_render_markdown_with_raw_highlights_keeps_valid_html_across_format_boundary(self) -> None:
        script = textwrap.dedent(
            f"""\
            const {{ renderMarkdownWithRawHighlights }} = await import({json.dumps(MARKDOWN_SRC.as_uri())});
            const text = 'before **bold** after';
            const html = renderMarkdownWithRawHighlights(text, [{{ start: 9, end: 18 }}]);
            console.log(JSON.stringify({{ html }}));
            """
        )
        result = self._run_node(script)
        self.assertEqual(result.returncode, 0, msg=f"{result.stdout}\n{result.stderr}")
        payload = json.loads(result.stdout)
        self.assertIn('<strong><mark class="citation-hl">bold</mark></strong>', payload["html"])
        self.assertIn('<mark class="citation-hl"> af</mark>', payload["html"])
        self.assertNotIn('</strong> af</mark>', payload["html"])


if __name__ == "__main__":
    unittest.main()
