"""Pack bundle parser + verifier (MVP-039).

Anchor-splits prompt `.md` files into layer records, parses whole pack directories with
path-precise + file-named errors, and verifies the digest manifest + ed25519 signature
(tampered → refused). Pure — filesystem + crypto only, no DB.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from core.packs import bundle
from core.packs.bundle import (
    BundleError,
    ParsedPack,
    compute_manifest,
    load_bundle,
    pack_bundle,
    parse_pack_dir,
    serialize_manifest,
    split_prompt_layers,
    unpack_bundle,
    verify_manifest,
    verify_signature,
)

VERTICALS = Path(__file__).resolve().parents[2] / "verticals"

# (file, expected anchor/layer count) — mirrors the pack prompt files.
PROMPT_COUNTS = [
    ("jewelry/prompts/concierge.md", 4),
    ("jewelry/prompts/campaigner.md", 1),
    ("jewelry/prompts/ops.md", 2),
    ("kirana/prompts/concierge.md", 2),
    ("kirana/prompts/ops.md", 2),
]


def test_concierge_anchor_split_yields_four_versioned_layers() -> None:
    layers = split_prompt_layers((VERTICALS / "jewelry" / "prompts" / "concierge.md").read_text())
    assert len(layers) == 4
    assert {ly.task for ly in layers} == {"qualify", "catalog", "quote", "book"}
    assert all(ly.archetype == "concierge" and ly.version == "3.2" for ly in layers)
    assert all(ly.content for ly in layers)  # fenced content extracted


@pytest.mark.parametrize(("rel", "count"), PROMPT_COUNTS)
def test_prompt_files_split_to_expected_counts(rel: str, count: int) -> None:
    assert len(split_prompt_layers((VERTICALS / rel).read_text())) == count


def test_parse_jewelry_pack_dir() -> None:
    p = parse_pack_dir(VERTICALS / "jewelry")
    assert isinstance(p, ParsedPack)
    assert p.manifest.pack == "jewelry"
    assert len(p.prompt_layers) == 9  # 4+1+1+2+1 across the five prompt files
    assert len(p.workflows) == 4 and len(p.integrations) == 4 and len(p.evals) == 6
    assert p.onboarding is not None and p.ui is not None and p.calendar is not None


def test_parse_kirana_pack_dir() -> None:
    p = parse_pack_dir(VERTICALS / "kirana")
    assert p.manifest.pack == "kirana"
    assert len(p.prompt_layers) == 5 and len(p.workflows) == 2
    assert p.calendar is None  # kirana has no calendar pack


def test_invalid_field_in_bindings_names_the_file(tmp_path: Path) -> None:
    dst = tmp_path / "jewelry"
    shutil.copytree(VERTICALS / "jewelry", dst)
    bindings = dst / "agents" / "bindings.yaml"
    bindings.write_text(bindings.read_text().replace("tier: 1", "tier: high", 1))
    with pytest.raises(BundleError) as ei:
        parse_pack_dir(dst)
    assert "agents/bindings.yaml" in str(ei.value) and "tier" in str(ei.value)


def test_verify_manifest_refuses_tampered_digest(tmp_path: Path) -> None:
    dst = tmp_path / "jewelry"
    shutil.copytree(VERTICALS / "jewelry", dst)
    manifest = compute_manifest(dst)
    verify_manifest(dst, manifest)  # clean tree matches

    (dst / "pack.yaml").write_text((dst / "pack.yaml").read_text() + "\n# tampered\n")
    with pytest.raises(BundleError) as ei:
        verify_manifest(dst, manifest)
    assert "pack.yaml" in str(ei.value)


def test_ed25519_signature_roundtrip() -> None:
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    data = serialize_manifest({"pack.yaml": "abc", "agents/bindings.yaml": "def"})
    sig = priv.sign(data)
    assert verify_signature(data, sig, pub) is True
    assert verify_signature(data + b"x", sig, pub) is False  # tampered manifest
    other = Ed25519PrivateKey.generate().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    assert verify_signature(data, sig, other) is False       # wrong key


def _sign_bundle(pack_dir: Path, priv: Ed25519PrivateKey) -> None:
    manifest = compute_manifest(pack_dir)
    (pack_dir / bundle.MANIFEST_NAME).write_bytes(serialize_manifest(manifest))
    (pack_dir / bundle.SIGNATURE_NAME).write_bytes(priv.sign(serialize_manifest(manifest)))


def test_prod_mode_requires_valid_signature(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GROWTH_OPERATOR_PACKS_DEV_MODE", "false")
    dst = tmp_path / "jewelry"
    shutil.copytree(VERTICALS / "jewelry", dst)

    with pytest.raises(BundleError):  # no manifest/sig yet
        load_bundle(dst)

    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    _sign_bundle(dst, priv)
    parsed = load_bundle(dst, public_key=pub)  # valid → parses
    assert parsed.manifest.pack == "jewelry"

    # Tamper a file after signing → digest mismatch refused.
    (dst / "pricing" / "strategy.yaml").write_text("strategy_key: x\nengine: rules_v1\n")
    with pytest.raises(BundleError):
        load_bundle(dst, public_key=pub)


def test_dev_mode_loads_without_signature() -> None:
    # Default packs_dev_mode=True → a bare directory parses with no manifest/sig.
    assert load_bundle(VERTICALS / "kirana").manifest.pack == "kirana"


# ---- .tar.zst transport ---------------------------------------------------------------


def test_pack_unpack_roundtrip_preserves_tree(tmp_path: Path) -> None:
    out = tmp_path / "jewelry.tar.zst"
    pack_bundle(VERTICALS / "jewelry", out)
    dest = unpack_bundle(out, tmp_path / "unpacked")
    # Every pack file survives the round-trip byte-for-byte, and the tree still parses.
    assert compute_manifest(dest) == compute_manifest(VERTICALS / "jewelry")
    assert parse_pack_dir(dest).manifest.pack == "jewelry"


def test_load_bundle_from_zst_dev_mode(tmp_path: Path) -> None:
    out = tmp_path / "kirana.tar.zst"
    pack_bundle(VERTICALS / "kirana", out)
    assert load_bundle(out).manifest.pack == "kirana"  # unpacks + parses transparently


def test_signed_bundle_verifies_in_prod(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROWTH_OPERATOR_PACKS_DEV_MODE", "false")
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    out = tmp_path / "kirana.tar.zst"
    pack_bundle(VERTICALS / "kirana", out, private_key=priv)

    assert load_bundle(out, public_key=pub).manifest.pack == "kirana"  # valid signature
    other = Ed25519PrivateKey.generate().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    with pytest.raises(BundleError):
        load_bundle(out, public_key=other)  # wrong key → refused


def test_bundle_size_cap_refused(tmp_path: Path) -> None:
    out = tmp_path / "kirana.tar.zst"
    pack_bundle(VERTICALS / "kirana", out)
    with pytest.raises(BundleError):
        unpack_bundle(out, tmp_path / "dest", max_bytes=1024)  # 1KB cap → zip-bomb guard trips
