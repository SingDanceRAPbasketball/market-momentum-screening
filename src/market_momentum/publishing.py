"""Publish a validated report bundle and remove obsolete HTML artifacts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Tuple


@dataclass(frozen=True)
class PublishResult:
    output_dir: Path
    as_of: str
    removed_html: Tuple[Path, ...]


_BUNDLE_FILES = (
    "latest.html",
    "industry.html",
    "run_manifest.json",
    "industry_manifest.json",
)
_CANONICAL_HTML = {"latest.html", "industry.html"}


def _load_manifest(path: Path) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"invalid report manifest: {path}") from error
    if not isinstance(payload, dict) or payload.get("status") != "success":
        raise RuntimeError(f"report manifest is not successful: {path}")
    return payload


def _write_json_atomic(path: Path, payload: Dict[str, Any]) -> None:
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary_path.replace(path)


def publish_report_bundle(staging_dir: Path, output_dir: Path) -> PublishResult:
    """Replace the current reports only after a complete bundle is available."""

    staging_dir = staging_dir.resolve()
    output_dir = output_dir.resolve()
    missing = [name for name in _BUNDLE_FILES if not (staging_dir / name).is_file()]
    if missing:
        raise RuntimeError(f"report bundle is incomplete: {', '.join(missing)}")

    market_manifest = _load_manifest(staging_dir / "run_manifest.json")
    industry_manifest = _load_manifest(staging_dir / "industry_manifest.json")
    market_date = str(market_manifest.get("as_of") or "")
    industry_date = str(industry_manifest.get("as_of") or "")
    if not market_date or market_date != industry_date:
        raise RuntimeError(
            f"report dates do not match: market={market_date!r}, industry={industry_date!r}"
        )

    if market_date not in (staging_dir / "latest.html").read_text(encoding="utf-8"):
        raise RuntimeError("latest.html does not contain the manifest date")
    if market_date not in (staging_dir / "industry.html").read_text(encoding="utf-8"):
        raise RuntimeError("industry.html does not contain the manifest date")

    output_dir.mkdir(parents=True, exist_ok=True)
    market_manifest["report"] = str(output_dir / "latest.html")
    industry_manifest["report"] = str(output_dir / "industry.html")
    _write_json_atomic(staging_dir / "run_manifest.json", market_manifest)
    _write_json_atomic(staging_dir / "industry_manifest.json", industry_manifest)

    for name in _BUNDLE_FILES:
        (staging_dir / name).replace(output_dir / name)

    removed = []
    for path in output_dir.glob("*.html"):
        if path.is_file() and path.name not in _CANONICAL_HTML:
            path.unlink()
            removed.append(path)

    return PublishResult(
        output_dir=output_dir,
        as_of=market_date,
        removed_html=tuple(sorted(removed)),
    )
