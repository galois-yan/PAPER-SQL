from __future__ import annotations

import asyncio
import inspect
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openalex_mcp.remote.download import download_pdf


class FakeClient:
    def __init__(self) -> None:
        self.output_paths: list[Path] = []

    async def search(self, **_kwargs):
        return {
            "results": [
                {
                    "id": "https://openalex.org/W1",
                    "title": "Fixed directory paper",
                    "has_content": {"pdf": True},
                }
            ]
        }

    async def download_pdf(self, _work_id: str, output_path: Path):
        self.output_paths.append(output_path)
        output_path.write_bytes(b"%PDF")
        return output_path, output_path.stat().st_size


class DownloadPdfTests(unittest.TestCase):
    def test_download_uses_the_fixed_pdf_directory(self):
        client = FakeClient()
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_dir = Path(temp_dir) / "pdfs"
            with patch("openalex_mcp.remote.download.DEFAULT_PDF_DIR", pdf_dir):
                result = asyncio.run(download_pdf(client, "W1"))

        expected_path = pdf_dir / "W1.pdf"
        self.assertEqual(client.output_paths, [expected_path])
        self.assertIn(str(pdf_dir), result)

    def test_download_uses_project_pdf_directory_only_when_requested(self):
        client = FakeClient()
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir) / "cache-pdfs"
            project_dir = Path(temp_dir) / "project-pdfs"
            with (
                patch("openalex_mcp.remote.download.DEFAULT_PDF_DIR", cache_dir),
                patch("openalex_mcp.remote.download.PROJECT_PDF_DIR", project_dir),
            ):
                result = asyncio.run(
                    download_pdf(client, "W1", save_to_project=True)
                )

        expected_path = project_dir / "W1.pdf"
        self.assertEqual(client.output_paths, [expected_path])
        self.assertIn(str(project_dir), result)

    def test_download_function_has_no_custom_directory_parameter(self):
        parameters = inspect.signature(download_pdf).parameters
        self.assertNotIn("output_dir", parameters)
        self.assertFalse(parameters["save_to_project"].default)


if __name__ == "__main__":
    unittest.main()
