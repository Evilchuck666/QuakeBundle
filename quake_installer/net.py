"""HTTP downloads, with a curl fallback for certificate problems on Windows."""

from __future__ import annotations

import email.message
import json
import platform
import ssl
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import urllib.response
from pathlib import Path

from .config import DOWNLOAD_CHUNK_SIZE, USER_AGENT
from .console import byte_bar, mb
from .events import current


def curl_get(url: str):
    """Download url with curl, returning a response like urlopen's."""
    # SIM115 is intentionally ignored here: the temporary file must remain open for
    # the returned addinfourl object to stay usable after this function returns.
    response = tempfile.TemporaryFile()  # noqa: SIM115

    try:
        try:
            result = subprocess.run(
                ["curl", "-fsSL", "-A", USER_AGENT, url],
                check=False,
                stdout=response,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError:
            sys.exit(
                f"Could not verify the certificate of {url}, and curl is not available"
            )

        if result.returncode != 0:
            sys.exit(
                f"Could not download {url}: {result.stderr.decode(errors='replace').strip()}"
            )

        headers = email.message.Message()
        headers["Content-Length"] = str(response.tell())
        response.seek(0)

        return urllib.response.addinfourl(response, headers, url, 200)
    except BaseException:
        response.close()
        raise


def open_url(url: str):
    """Open a URL, falling back to curl when the system certificate store is needed."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    options = {}
    if observer := current.get():
        # Frozen applications carry their own CA store, including on macOS.
        import certifi

        observer.checkpoint()
        options = {
            "timeout": 30,
            "context": ssl.create_default_context(cafile=certifi.where()),
        }

    try:
        return urllib.request.urlopen(request, **options)
    except urllib.error.URLError as error:
        if isinstance(error.reason, ssl.SSLCertVerificationError):
            if platform.system() == "Windows":
                # Python only trusts the root certificates already in the
                # Windows store, while Windows itself downloads missing ones
                # on demand. curl.exe (bundled since Windows 10) uses Windows'
                # own TLS, so it works where Python fails.
                return curl_get(url)

            if platform.system() == "Darwin":
                # Python from python.org ships without CA certificates on macOS.
                sys.exit(
                    f"Could not verify the certificate of {url}.\n"
                    'Run "Install Certificates.command" from your Python folder in Applications.'
                )

        sys.exit(f"Could not download {url}: {error.reason}")


def fetch_json(url: str):
    """Download and deserialize a JSON payload from a URL."""
    with open_url(url) as response:
        return json.load(response)


def fetch(url: str, destination: Path) -> None:
    """Download a URL to disk using a fixed-size buffer."""
    downloaded = 0

    with open_url(url) as response, destination.open("wb") as output:
        total = int(response.headers.get("Content-Length", 0))

        with byte_bar(total=total or None, desc="    Downloading") as progress_bar:
            while chunk := response.read(DOWNLOAD_CHUNK_SIZE):
                output.write(chunk)
                downloaded += len(chunk)
                progress_bar.update(len(chunk))

    print(f"    Downloaded {mb(downloaded)}")
