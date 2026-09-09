"""frametvsync: Sync a directory of JPEGs with a Samsung Frame TV.

References:
    https://github.com/xchwarze/samsung-tv-ws-api
    https://github.com/NickWaterton/samsung-tv-ws-api
    https://jonsully.net/blog/samsung-frame-art-api
    https://dashedyellowline.com/2024/06/07/samsung-frame-tv-lightroom/
    https://dashedyellowline.com/2025/09/15/part-2-using-chatgpt-to-create-a-frame-tv-upload-script/


"""

__author__ = "Keith Gorlen"

import argparse
import json
import logging
import re
import sys
from ipaddress import ip_address
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TypedDict, cast

from PIL import Image
from samsungtvws import SamsungTVArt
from samsungtvws.exceptions import ResponseError, UnauthorizedError

LOGGER = logging.getLogger("frametvsync")

LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 5


class ConsoleHandler(logging.StreamHandler):
    pass


STATE_VERSION = 1
DEFAULT_PORT = 8002
DEFAULT_ROTATION = 10
TV_REQUEST_TIMEOUT = 10.0
VALID_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
# --- Matte dictionaries (style + color) ---
MATTE_TYPES = {
    "none",
    "modernthin",
    "modern",
    "modernwide",
    "flexible",
    "shadowbox",
    "panoramic",
    "triptych",
    "mix",
    "squares",
}
MATTE_COLORS = {
    "black",
    "neutral",
    "antique",
    "warm",
    "polar",
    "sand",
    "seafoam",
    "sage",
    "burgundy",
    "navy",
    "apricot",
    "byzantine",
    "lavender",
    "redorange",
    "skyblue",
    "turquoise",
}


class TVState(TypedDict):
    ip: str
    port: int
    rotation: int


class ImageState(TypedDict):
    mtime_ns: int
    matte: str
    content_id: str


class State(TypedDict):
    version: int
    tv: TVState
    images: dict[str, ImageState]


def is_portrait(path: Path) -> bool:
    try:
        with Image.open(path) as im:
            w, h = im.size
            return h > w
    except Exception:
        return False


def file_type_for(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".png":
        return "PNG"
    return "JPEG"


def get_matte_id(hierarchical_subjects: str | None) -> str:
    """Convert matte metadata from XMP HierarchicalSubject to the TV matte_id format.

    Arguments:
        hierarchical_subjects: The full hierarchicalSubject string from the XMP metadata.

    Returns:
        A string in the format "<matte_type>_<matte_color>", e.g. "modern_polar".
        If the matte type is "none" or "custom", returns "none_none".
        If the matte color is not specified, defaults to "black" (e.g., "modern_black").
        If the matte type is not found or unrecognized, logs a warning and returns "none_none".

    hierarchicalSubject format:
        *Art_Frame|Matte|<matte_type>*
        *Art_Frame|Color|<matte_color>*
    """

    try:
        if hierarchical_subjects is None:
            raise ValueError("No hierarchicalSubject")

        hierarchical_subjects = hierarchical_subjects.strip()
        if not hierarchical_subjects:
            raise ValueError("Empty hierarchicalSubject")

        if m := re.search(
            r"\*\s*Art_Frame\s*\|\s*Matte\s*\|\s*([^*]+)",
            hierarchical_subjects,
            flags=re.IGNORECASE,
        ):
            matte_type: str = m.group(1).strip().lower()
        else:
            raise ValueError("No Art_Frame|Matte found in hierarchicalSubject")

        if matte_type in ("none", "custom"):
            return "none_none"  # Default color none for no matte or custom matte

        if matte_type not in MATTE_TYPES:
            raise ValueError(f"Unknown matte type: {matte_type}")

        if m := re.search(
            r"\*\s*Art_Frame\s*\|\s*Matte_Color\s*\|\s*([^*]+)",
            hierarchical_subjects,
            flags=re.IGNORECASE,
        ):
            matte_color: str = m.group(1).strip().lower()
        else:
            return f"{matte_type}_black"  # Default to black if no color is specified

        if matte_color not in MATTE_COLORS:
            raise ValueError(f"Unknown matte color: {matte_color}")
    except ValueError as exc:
        LOGGER.warning(exc)
        return "none_none"

    return f"{matte_type}_{matte_color}"


def should_ignore_content_id(content_id: str | None) -> bool:
    """Samsung Art Store content identifiers should not be treated as user-managed images."""
    if not content_id:
        return False
    return content_id.upper().startswith("SAM-")


def reset_tv_art(tv: SamsungTVArt, dry_run: bool) -> set[str]:
    """Delete all user-uploaded artwork from the TV and return deleted content IDs."""
    content_ids = {
        str(item.get("content_id")).strip()
        for item in tv.available()
        if isinstance(item, dict) and item.get("content_id")
    }
    content_ids -= {
        content_id for content_id in content_ids if should_ignore_content_id(content_id)
    }

    if not content_ids:
        LOGGER.info("No non-Samsung Art Store images found on the TV.")
        return set()

    if dry_run:
        for content_id in sorted(content_ids):
            LOGGER.info("DRY-RUN: would delete TV artwork %s", content_id)
        return content_ids

    answer = input(f"Delete {len(content_ids)} non-Samsung Art Store image(s) from the TV? [y/N] ")
    if answer.strip().lower() not in {"y", "yes"}:
        LOGGER.info("Reset cancelled.")
        return set()

    deleted: set[str] = set()
    for content_id in sorted(content_ids):
        try:
            tv.delete(content_id)
            deleted.add(content_id)
            LOGGER.info("Deleted TV artwork %s", content_id)
        except Exception as exc:  # pragma: no cover - runtime interaction  # noqa: BLE001
            LOGGER.warning("Failed to delete %s on the TV: %s", content_id, exc)
    return deleted


def load_state(path: Path) -> State:
    """Load or create the JSON state file used to track uploaded images."""
    default_state: State = {
        "version": STATE_VERSION,
        "tv": {"ip": "", "port": DEFAULT_PORT, "rotation": DEFAULT_ROTATION},
        "images": {},
    }

    if not path.exists():
        save_state(path, default_state)
        return default_state

    try:
        with path.open("r", encoding="utf-8") as handle:
            loaded = json.load(handle)
    except (OSError, json.JSONDecodeError):
        save_state(path, default_state)
        return default_state

    if not isinstance(loaded, dict):
        save_state(path, default_state)
        return default_state

    normalized = default_state.copy()
    loaded_tv = loaded.get("tv")
    if isinstance(loaded_tv, dict):
        normalized["tv"] = cast(
            TVState,
            {
                "ip": loaded_tv.get("ip", default_state["tv"]["ip"]),
                "port": loaded_tv.get("port", default_state["tv"]["port"]),
                "rotation": loaded_tv.get("rotation", default_state["tv"]["rotation"]),
            },
        )
    normalized["images"] = (
        cast(dict[str, ImageState], loaded["images"])
        if isinstance(loaded.get("images"), dict)
        else {}
    )
    normalized["version"] = STATE_VERSION
    save_state(path, normalized)
    return normalized


def save_state(path: Path, state: State) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, sort_keys=True)
        handle.write("\n")


def read_xmp_subject(path: Path) -> str | None:
    """Read Lightroom's hierarchicalSubject XMP metadata from a JPEG.

    Arguments:
        path (Path): The path to the JPEG file.

    Returns:
        str | None: The hierarchical subject string or None if not found.
    """
    try:
        with Image.open(path) as image:
            xmp_raw = None
            if "xmp" in image.info:
                xmp_raw = image.info.get("xmp")
            else:
                getter = getattr(image, "getxmp", None)
                if callable(getter):
                    xmp_raw = getter()

            if not xmp_raw:
                return None

            if isinstance(xmp_raw, bytes):
                xmp_text = xmp_raw.decode("utf-8", errors="ignore")
            else:
                xmp_text = str(xmp_raw)

            if m := re.search(
                r"<(?:[A-Za-z_][\w.-]*:)?hierarchicalSubject>.*?<rdf:(?:Bag|Seq)>(.*?)</rdf:(?:Bag|Seq)>",
                xmp_text,
                flags=re.DOTALL | re.IGNORECASE,
            ):
                items = re.findall(
                    r"<rdf:li[^>]*>(.*?)</rdf:li>", m.group(1), flags=re.DOTALL | re.IGNORECASE
                )
                if items:
                    return "*".join(item.strip() for item in items if item.strip())

            return None

    except Exception as exc:  # noqa: BLE001
        LOGGER.error("Error reading XMP HierarchicalSubject from %s: %s", path, exc)

    return None


def read_matte_tag(path: Path) -> str:
    matte_source = read_xmp_subject(path)
    if matte_source:
        return get_matte_id(matte_source)
    LOGGER.warning("No Lightroom hierarchical matte found for %s; using shadowbox_black", path)
    return "shadowbox_black"


def connect_tv(ip: str, port: int, token_file: str) -> SamsungTVArt:
    LOGGER.info("Connecting to Samsung Frame TV at %s:%d ...", ip, port)
    token_path: Path = Path(token_file).expanduser()
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token: str = token_path.read_text(encoding="utf-8").strip() if token_path.exists() else ""
    if token_path.exists() and not token:
        token_path.unlink()
    try:
        tv = SamsungTVArt(
            host=ip,
            port=port,
            token_file=str(token_path),
            name="frametvsync",
            timeout=TV_REQUEST_TIMEOUT,
        )
        tv.open()
        tv.available()
        token = token_path.read_text(encoding="utf-8").strip() if token_path.exists() else ""
        if not token:
            LOGGER.warning("SamsungTVWS connected but did not write a token to %s", token_path)
        LOGGER.info("Connected to Samsung Frame TV at %s:%d.", ip, port)
        return tv
    except UnauthorizedError as exc:  # pragma: no cover - runtime interaction
        raise RuntimeError(
            "The TV rejected the connection. Approve the pairing request on the TV, then retry. "
            f"If no prompt appears, delete {token_path} and retry."
        ) from exc


def configure_logging(directory: Path, quiet: bool = False) -> None:
    """Configure console logging and rotating logs for a sync directory."""
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    console_handlers = [
        handler for handler in logger.handlers if isinstance(handler, ConsoleHandler)
    ]
    if quiet:
        for handler in console_handlers:
            logger.removeHandler(handler)
    elif not console_handlers:
        console_handler = ConsoleHandler()
        console_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
        )
        logger.addHandler(console_handler)

    log_directory = directory / "logs"
    log_directory.mkdir(parents=True, exist_ok=True)
    log_path = log_directory / "frametvsync.log"
    if not any(
        isinstance(handler, RotatingFileHandler) and Path(handler.baseFilename) == log_path
        for handler in logger.handlers
    ):
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        logger.addHandler(file_handler)


def sync_directory(
    tv: SamsungTVArt | None, directory: Path, state: State, dry_run: bool
) -> tuple[int, int]:
    """Upload any newer or changed local images and prune stale state entries no longer present locally."""
    local_files: dict[str, Path] = {
        path.name: path
        for path in sorted(directory.iterdir())
        if path.is_file() and path.suffix.lower() in VALID_IMAGE_SUFFIXES
    }
    local_names: set[str] = set(local_files)
    uploaded = 0
    deleted = 0

    for name, image_path in local_files.items():
        previous = state["images"].get(name)
        mtime_ns = image_path.stat().st_mtime_ns
        matte = read_matte_tag(image_path)

        if (
            previous
            and previous.get("mtime_ns") == mtime_ns
            and previous.get("matte") == matte
            and previous.get("content_id")
        ):
            continue

        if dry_run:
            LOGGER.info("DRY-RUN: would upload %s with matte=%s", name, matte)
            uploaded += 1
            continue

        if tv is None:
            raise RuntimeError("TV connection is required for a live sync")

        with image_path.open("rb") as handle:
            data = handle.read()

        LOGGER.info("Uploading %s with matte=%s ...", name, matte)
        content_id = tv.upload(
            data, file_type=file_type_for(image_path), matte=matte, portrait_matte=matte
        )
        tv.set_favourite(content_id, status="on")
        state["images"][name] = {
            "mtime_ns": mtime_ns,
            "matte": matte,
            "content_id": content_id,
        }
        uploaded += 1
        LOGGER.info("Uploaded %s -> %s (matte=%s)", name, content_id, matte)

    for name in list(state["images"]):
        if name not in local_names:
            if dry_run:
                LOGGER.info("DRY-RUN: would remove %s from TV", name)
                deleted += 1
            else:
                stale_content_id = state["images"][name]["content_id"]
                try:
                    assert tv is not None
                    tv.delete(stale_content_id)
                except Exception as exc:
                    LOGGER.warning("Failed to delete %s on the TV: %s", name, exc)
                LOGGER.info("Removed state entry for %s", name)
                deleted += 1
            del state["images"][name]

    return uploaded, deleted


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sync a directory of JPEGs to a Samsung Frame TV.")
    parser.add_argument("directory", type=Path, help="Directory containing JPEG images to sync")
    parser.add_argument("--ip", type=ip_address, help="Samsung Frame TV IP address")
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help="Samsung Frame TV websocket port, default: %(default)d",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Display what would change without uploading anything",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Suppress logging to the console (file logging remains enabled)",
    )
    parser.add_argument(
        "-r",
        "--rotation",
        type=int,
        help="Set the TV artwork rotation period in minutes (0 disables rotation)",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete all non-Samsung Art Store images from the TV",
    )
    args = parser.parse_args(argv)

    directory = Path(args.directory).expanduser().resolve()
    if not directory.exists() or not directory.is_dir():
        parser.error(f"Directory not found: {directory}")

    configure_logging(directory, quiet=args.quiet)

    token_file = directory / ".frametvsync.token"
    LOGGER.info("SamsungTVWS token file: %s", token_file)
    state_path = directory / ".frametvsync_state.json"

    if not state_path.exists() and not args.ip:
        parser.error("A TV IP address is required on the first run. Pass --ip.")

    state: State = load_state(state_path)

    ip = str(args.ip) if args.ip else str(state["tv"].get("ip") or "").strip()
    if not ip:
        parser.error("A TV IP address is required. Pass --ip or set state.tv.ip.")

    state["tv"]["ip"] = ip
    state["tv"]["port"] = args.port
    rotation = args.rotation if args.rotation is not None else state["tv"]["rotation"]
    if rotation < 0:
        parser.error("Rotation period must be zero or greater.")
    state["tv"]["rotation"] = rotation

    if args.dry_run and not args.reset:
        LOGGER.info("Dry run enabled; no uploads will be sent to the TV.")
        uploaded, deleted = sync_directory(None, directory, state, dry_run=True)
        LOGGER.info(
            f"Dry run complete: {uploaded} image(s) would be uploaded, {deleted} image(s) would be deleted from the TV."
        )
        return 0

    tv = connect_tv(ip=ip, port=args.port, token_file=str(token_file))

    try:
        if not args.dry_run:
            LOGGER.info("Setting TV artwork rotation to %d minutes ...", rotation)
            set_slideshow_status = getattr(tv, "set_slideshow_status", None)
            if callable(set_slideshow_status):
                try:
                    set_slideshow_status(duration=rotation, category_id="MY-C0004")
                except ResponseError:
                    LOGGER.info("TV rejected slideshow API; retrying legacy rotation API")
                    tv.set_auto_rotation_status(duration=rotation, category_id="MY-C0004")
            else:
                tv.set_auto_rotation_status(duration=rotation, category_id="MY-C0004")
            LOGGER.info("TV artwork rotation set to %d minutes", rotation)
        if args.reset:
            deleted_ids: set[str] = reset_tv_art(tv, dry_run=args.dry_run)
            if not args.dry_run:
                state["images"] = {
                    name: image
                    for name, image in state["images"].items()
                    if image.get("content_id") not in deleted_ids
                }
                save_state(state_path, state)
            LOGGER.info(
                f"Reset {'would remove' if args.dry_run else 'removed'} {len(deleted_ids)} image(s)"
            )
            return 0

        uploaded, deleted = sync_directory(tv, directory, state, dry_run=False)
        save_state(state_path, state)
        LOGGER.info(f"Uploaded {uploaded} image(s) from {directory}")
        LOGGER.info(f"Deleted {deleted} image(s) from TV")
        return 0
    finally:
        save_state(state_path, state)
        tv.close()


if __name__ == "__main__":
    sys.exit(cli())
