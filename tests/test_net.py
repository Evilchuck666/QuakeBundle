"""Exercise downloads locally and simulate platform-specific TLS failures."""

import json
import ssl
import subprocess
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock, call, patch, sentinel

from quake_installer import net


class DownloadTests(unittest.TestCase):
    """Exercise local file downloads."""

    def test_fetch_copies_every_chunk_from_file_url(self):
        """Fetch a file URL and copy every chunk without corruption."""
        payload = bytes(range(256)) * 3
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source with spaces.bin"
            destination = Path(directory) / "download.bin"
            source.write_bytes(payload)
            with (
                patch.object(net, "DOWNLOAD_CHUNK_SIZE", 100),
                patch.object(net, "byte_bar") as byte_bar,
                patch("builtins.print"),
            ):
                net.fetch(source.as_uri(), destination)
            self.assertEqual(destination.read_bytes(), payload)
        byte_bar.assert_called_once_with(total=len(payload), desc="    Downloading")
        progress = byte_bar.return_value.__enter__.return_value
        self.assertEqual(progress.update.call_args_list, [call(100)] * 7 + [call(68)])

    def test_fetch_json_reads_utf8_file_url(self):
        """Fetch JSON from a file URL and decode UTF-8 text."""
        payload = {"name": "Quake — español", "assets": [{"size": 123}]}
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "release data.json"
            source.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(net.fetch_json(source.as_uri()), payload)


class OpenUrlTests(unittest.TestCase):
    """Exercise URL-opening behavior and download errors."""

    url = "https://example.invalid/quake"

    def test_user_agent_is_sent(self):
        """Send the configured user agent when opening a URL."""
        with patch.object(
            net.urllib.request, "urlopen", return_value=sentinel.response
        ) as open_:
            self.assertIs(net.open_url(self.url), sentinel.response)
        request = open_.call_args.args[0]
        self.assertEqual(request.full_url, self.url)
        self.assertEqual(request.get_header("User-agent"), net.USER_AGENT)

    def test_certificate_failure_uses_curl_only_on_windows(self):
        """Retry certificate failures with curl only on Windows."""
        error = urllib.error.URLError(ssl.SSLCertVerificationError("untrusted"))
        with (
            patch.object(net.urllib.request, "urlopen", side_effect=error),
            patch.object(net.platform, "system", return_value="Windows"),
            patch.object(net, "curl_get", return_value=sentinel.response) as curl,
        ):
            self.assertIs(net.open_url(self.url), sentinel.response)
        curl.assert_called_once_with(self.url)

    def test_certificate_failure_on_macos_explains_certificate_installation(self):
        """Explain how to install certificates on macOS when TLS verification fails."""
        error = urllib.error.URLError(ssl.SSLCertVerificationError("untrusted"))
        with (
            patch.object(net.urllib.request, "urlopen", side_effect=error),
            patch.object(net.platform, "system", return_value="Darwin"),
            patch.object(net, "curl_get") as curl,
            self.assertRaisesRegex(SystemExit, "Install Certificates.command"),
        ):
            net.open_url(self.url)
        curl.assert_not_called()

    def test_other_download_errors_report_url_and_reason(self):
        """Report the failing URL and reason for non-certificate download errors."""
        for system in ("Linux", "Darwin", "Windows"):
            with (
                self.subTest(system=system),
                patch.object(
                    net.urllib.request,
                    "urlopen",
                    side_effect=urllib.error.URLError("offline"),
                ),
                patch.object(net.platform, "system", return_value=system),
                patch.object(net, "curl_get") as curl,
                self.assertRaises(SystemExit) as error,
            ):
                net.open_url(self.url)

                self.assertEqual(
                    str(error.exception),
                    f"Could not download {self.url}: offline",
                )

                curl.assert_not_called()


class CurlTests(unittest.TestCase):
    """Exercise the curl-backed download helper."""

    url = "https://example.invalid/quake"

    def test_response_contains_download_and_closes_temporary_file(self):
        """Return a response-like object containing the payload and close the temp file."""
        payload = b"Quake\x00\xff"

        def download(command, **kwargs):
            kwargs["stdout"].write(payload)
            return subprocess.CompletedProcess(command, 0, stderr=b"")

        with tempfile.TemporaryFile() as temporary:
            with (
                patch.object(net.tempfile, "TemporaryFile", return_value=temporary),
                patch.object(net.subprocess, "run", side_effect=download) as run,
            ):
                with net.curl_get(self.url) as response:
                    self.assertEqual(response.read(), payload)
                    self.assertEqual(
                        response.headers["Content-Length"], str(len(payload))
                    )
                    self.assertEqual(response.geturl(), self.url)
                    self.assertEqual(response.getcode(), 200)
                self.assertTrue(temporary.closed)
            run.assert_called_once_with(
                ["curl", "-fsSL", "-A", net.USER_AGENT, self.url],
                check=False,
                stdout=temporary,
                stderr=subprocess.PIPE,
            )

    def test_failed_curl_closes_temporary_file(self):
        """Close the temporary file even when curl is unavailable or fails."""
        cases = (
            (Mock(side_effect=FileNotFoundError()), "curl is not available"),
            (
                Mock(
                    return_value=subprocess.CompletedProcess(
                        [], 22, stderr=b"not found\n"
                    )
                ),
                "not found",
            ),
        )
        for run, message in cases:
            with self.subTest(message=message), tempfile.TemporaryFile() as temporary:
                with (
                    patch.object(net.tempfile, "TemporaryFile", return_value=temporary),
                    patch.object(net.subprocess, "run", run),
                    self.assertRaisesRegex(SystemExit, message),
                ):
                    net.curl_get(self.url)
                self.assertTrue(temporary.closed)
