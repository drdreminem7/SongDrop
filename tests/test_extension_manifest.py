import base64
import hashlib
import json
from pathlib import Path

from songdrop.api import _EXTENSION_ID


def test_manifest_public_key_matches_authorized_extension_id() -> None:
    manifest_path = Path(__file__).parents[1] / "extension" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    public_key = base64.b64decode(manifest["key"], validate=True)
    identifier_hex = hashlib.sha256(public_key).hexdigest()[:32]
    extension_id = identifier_hex.translate(str.maketrans("0123456789abcdef", "abcdefghijklmnop"))

    assert extension_id == _EXTENSION_ID
