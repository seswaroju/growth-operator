"""Pack bundle parser + verifier (MVP-039).

Turns a pack on disk into a validated `ParsedPack`: every file is checked against its
contract (MVP-038) with a path-precise, file-named error on violation, and prompt `.md` files
are split on their anchor headings into `PromptLayerDef` records.

Two trust modes (config `packs_dev_mode`):
- **dev** (default): install from a directory, no signature required.
- **prod**: require a `MANIFEST.sha256` whose per-file digests match the tree exactly (a
  tampered file is refused) and a valid **ed25519** signature over that manifest.

A signed bundle is a `.tar.zst` of the pack tree plus its `MANIFEST.sha256` and `MANIFEST.sig`
(`pack_bundle`); `load_bundle` transparently unpacks a `.tar.zst` (size-capped, path-traversal
safe) to a temp dir and then verifies + parses it, so packing is only compression around the
tree that the digest + signature verification already secures.
"""

from __future__ import annotations

import hashlib
import io
import re
import tarfile
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
import zstandard
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from pydantic import ValidationError

from core.common.config import get_settings
from core.packs.contracts import (
    BindingsPack,
    CalendarPack,
    CatalogSchema,
    CommercialPack,
    EvalSuite,
    IntegrationSpec,
    OnboardingPack,
    PackManifest,
    PricingStrategyDef,
    PromptLayerDef,
    UiPack,
    WorkflowDef,
)

MANIFEST_NAME = "MANIFEST.sha256"
SIGNATURE_NAME = "MANIFEST.sig"
_BUNDLE_META = {MANIFEST_NAME, SIGNATURE_NAME}
MAX_BUNDLE_BYTES = 50 * 1024 * 1024  # decompressed-size cap (spec) — guards against zip bombs


class BundleError(Exception):
    """A bundle file failed its contract, or a digest/signature check failed."""


@dataclass
class ParsedPack:
    manifest: PackManifest
    bindings: BindingsPack
    catalog: CatalogSchema
    pricing: PricingStrategyDef
    prompt_layers: list[PromptLayerDef]
    workflows: list[WorkflowDef] = field(default_factory=list)
    integrations: list[IntegrationSpec] = field(default_factory=list)
    evals: list[EvalSuite] = field(default_factory=list)
    onboarding: OnboardingPack | None = None
    ui: UiPack | None = None
    calendar: CalendarPack | None = None
    commercial: CommercialPack | None = None


# ---- Prompt anchor splitting ----------------------------------------------------------

_ANCHOR = re.compile(r'^##\s*<a id="([^"]+)"></a>\s*Layer:\s*(\S+)\s*$', re.M)
_VERSION = re.compile(r"v(\d+(?:\.\d+)+)")
_FENCE = re.compile(r"```[^\n]*\n(.*?)```", re.S)
_REQUIRES = re.compile(r"Composes on\s+`([^`\s]+)\s*(>=?\s*[^`]+?)`")


def split_prompt_layers(text: str) -> list[PromptLayerDef]:
    """Split a prompt `.md` into layer records. Each `## <a id="x"></a>Layer: a.pack.task`
    heading opens a layer whose content is the following fenced block; the version comes from
    the file header (e.g. `v3.2`)."""
    header = text.splitlines()[0] if text.strip() else ""
    vm = _VERSION.search(header)
    version = vm.group(1) if vm else "0"

    requires: dict[str, str] = {}
    rm = _REQUIRES.search(text)
    if rm:
        ref, constraint = rm.group(1).strip(), rm.group(2).strip()
        requires = {ref.split(".", 1)[0]: constraint}

    anchors = list(_ANCHOR.finditer(text))
    layers: list[PromptLayerDef] = []
    for i, m in enumerate(anchors):
        anchor_id, layer_name = m.group(1), m.group(2)
        section = text[m.end() : (anchors[i + 1].start() if i + 1 < len(anchors) else len(text))]
        fence = _FENCE.search(section)
        content = (fence.group(1) if fence else section).strip()
        layers.append(
            PromptLayerDef(
                archetype=layer_name.split(".", 1)[0], task=anchor_id,
                version=version, content=content, requires=requires,
            )
        )
    return layers


# ---- Digest manifest + signature ------------------------------------------------------


def _iter_files(pack_dir: Path) -> list[Path]:
    return sorted(
        p for p in pack_dir.rglob("*") if p.is_file() and p.name not in _BUNDLE_META
    )


def compute_manifest(pack_dir: Path) -> dict[str, str]:
    """{posix-relpath: sha256hex} for every file in the tree (excluding bundle metadata)."""
    return {
        p.relative_to(pack_dir).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in _iter_files(pack_dir)
    }


def serialize_manifest(manifest: dict[str, str]) -> bytes:
    """Deterministic bytes for a manifest — what the signature is computed over."""
    return "\n".join(f"{digest}  {path}" for path, digest in sorted(manifest.items())).encode()


def parse_manifest(raw: bytes) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in raw.decode().splitlines():
        line = line.strip()
        if line:
            digest, path = line.split(None, 1)
            out[path] = digest
    return out


def verify_manifest(pack_dir: Path, manifest: dict[str, str]) -> None:
    """Raise `BundleError` if the tree's digests don't exactly match `manifest`."""
    actual = compute_manifest(pack_dir)
    if actual != manifest:
        changed = sorted(
            set(actual) ^ set(manifest)
            | {k for k in actual.keys() & manifest.keys() if actual[k] != manifest[k]}
        )
        raise BundleError(f"digest mismatch (tampered or incomplete): {changed}")


def verify_signature(manifest_bytes: bytes, signature: bytes, public_key: bytes) -> bool:
    """Verify an ed25519 signature over the serialized manifest."""
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, manifest_bytes)
    except (InvalidSignature, ValueError):
        return False
    return True


# ---- .tar.zst transport ---------------------------------------------------------------


def _add_bytes(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    tar.addfile(info, io.BytesIO(data))


def pack_bundle(
    pack_dir: Path, out_path: Path, *, private_key: Ed25519PrivateKey | None = None
) -> None:
    """Pack a pack directory into a signed `.tar.zst` bundle: the tree + `MANIFEST.sha256`
    (per-file digests) + `MANIFEST.sig` (ed25519 over the manifest, when `private_key` given)."""
    manifest_bytes = serialize_manifest(compute_manifest(pack_dir))
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for p in _iter_files(pack_dir):
            tar.add(p, arcname=p.relative_to(pack_dir).as_posix())
        _add_bytes(tar, MANIFEST_NAME, manifest_bytes)
        if private_key is not None:
            _add_bytes(tar, SIGNATURE_NAME, private_key.sign(manifest_bytes))
    out_path.write_bytes(zstandard.ZstdCompressor().compress(buf.getvalue()))


def unpack_bundle(bundle_path: Path, dest_dir: Path, *, max_bytes: int = MAX_BUNDLE_BYTES) -> Path:
    """Decompress + extract a `.tar.zst` bundle into `dest_dir`. The decompressed size is capped
    (zip-bomb guard) and extraction uses the `data` filter (no path traversal / device files)."""
    compressed = bundle_path.read_bytes()
    # Embedded content size (or -1 if unknown) — zstandard ignores max_output_size when the
    # size is embedded, so cap up front; fall back to max_output_size for streamed frames.
    if zstandard.frame_content_size(compressed) > max_bytes:
        raise BundleError("bundle exceeds decompressed-size cap")
    try:
        raw = zstandard.ZstdDecompressor().decompress(compressed, max_output_size=max_bytes)
    except zstandard.ZstdError as exc:
        raise BundleError(f"bundle too large or corrupt: {exc}") from exc
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r") as tar:
        tar.extractall(dest_dir, filter="data")
    return dest_dir


# ---- Directory parser -----------------------------------------------------------------


def _load(path: Path) -> Any:
    import json

    if path.suffix == ".json":
        return json.loads(path.read_text())
    return yaml.safe_load(path.read_text())


def _validate(pack_dir: Path, path: Path, fn: Any) -> Any:
    """Validate one file, wrapping a pydantic error with the offending file's path."""
    try:
        return fn(_load(path))
    except ValidationError as exc:
        raise BundleError(f"{path.relative_to(pack_dir).as_posix()}: {exc}") from exc


def parse_pack_dir(pack_dir: Path) -> ParsedPack:
    """Validate every file in a pack directory and split its prompts (dev-mode parse)."""
    if not (pack_dir / "pack.yaml").is_file():
        raise BundleError(f"{pack_dir}: not a pack (no pack.yaml)")

    manifest = _validate(pack_dir, pack_dir / "pack.yaml", PackManifest.model_validate)
    bindings = _validate(
        pack_dir, pack_dir / "agents" / "bindings.yaml", BindingsPack.model_validate
    )
    catalog = _validate(
        pack_dir, pack_dir / "catalog" / "schema.json", CatalogSchema.from_document
    )
    pricing = _validate(
        pack_dir, pack_dir / "pricing" / "strategy.yaml", PricingStrategyDef.model_validate
    )

    def _many(subdir: str, fn: Any) -> list[Any]:
        d = pack_dir / subdir
        return [_validate(pack_dir, p, fn) for p in sorted(d.glob("*.yaml"))] if d.is_dir() else []

    workflows = _many("workflows", WorkflowDef.model_validate)
    integrations = _many("integrations", IntegrationSpec.model_validate)
    evals = _many("evals", EvalSuite.model_validate)

    def _opt(rel: str, fn: Any) -> Any:
        p = pack_dir / rel
        return _validate(pack_dir, p, fn) if p.is_file() else None

    onboarding = _opt("onboarding/steps.yaml", OnboardingPack.model_validate)
    ui = _opt("ui/templates.yaml", UiPack.model_validate)
    calendar = _opt("calendar/events.yaml", CalendarPack.model_validate)
    # PLAN-1: optional, pointed at by the manifest rather than a fixed path.
    commercial = (
        _opt(manifest.commercial, CommercialPack.model_validate)
        if manifest.commercial else None
    )

    prompt_layers: list[PromptLayerDef] = []
    prompts_dir = pack_dir / "prompts"
    if prompts_dir.is_dir():
        for md in sorted(prompts_dir.glob("*.md")):
            prompt_layers.extend(split_prompt_layers(md.read_text()))

    return ParsedPack(
        manifest=manifest, bindings=bindings, catalog=catalog, pricing=pricing,
        prompt_layers=prompt_layers, workflows=workflows, integrations=integrations,
        evals=evals, onboarding=onboarding, ui=ui, calendar=calendar,
        commercial=commercial,
    )


def load_bundle(pack_dir: Path, *, public_key: bytes | None = None) -> ParsedPack:
    """Load a pack from a directory or a `.tar.zst` bundle (unpacked to a temp dir first). In
    prod (`packs_dev_mode=False`) the tree must carry a matching `MANIFEST.sha256` and a valid
    ed25519 signature (via `public_key`) first."""
    if pack_dir.is_file():  # a packed bundle → unpack, then load the tree
        pack_dir = unpack_bundle(pack_dir, Path(tempfile.mkdtemp(prefix="gop-pack-")))
    if not get_settings().packs_dev_mode:
        manifest_path, sig_path = pack_dir / MANIFEST_NAME, pack_dir / SIGNATURE_NAME
        if not manifest_path.is_file() or not sig_path.is_file():
            raise BundleError("prod mode requires MANIFEST.sha256 + MANIFEST.sig")
        manifest = parse_manifest(manifest_path.read_bytes())
        verify_manifest(pack_dir, manifest)
        if public_key is None or not verify_signature(
            serialize_manifest(manifest), sig_path.read_bytes(), public_key
        ):
            raise BundleError("invalid or missing bundle signature")
    return parse_pack_dir(pack_dir)
