"""frametvsync: Sync a directory of JPEGs with a Samsung Frame TV.

References:
    https://github.com/xchwarze/samsung-tv-ws-api
    https://github.com/NickWaterton/samsung-tv-ws-api
    https://jonsully.net/blog/samsung-frame-art-api
    https://dashedyellowline.com/2024/06/07/samsung-frame-tv-lightroom/
    https://dashedyellowline.com/2025/09/15/part-2-using-chatgpt-to-create-a-frame-tv-upload-script/
    https://www.reddit.com/r/TheFrame/comments/1c2lgbs/art_mode_api_is_back/


"""

__author__ = "Keith Gorlen"

import argparse
import json
import logging
import os
import platform
import re
import sys
from ipaddress import ip_address
from logging.handlers import RotatingFileHandler
from pathlib import Path
from time import sleep
from typing import Any, NoReturn, TypedDict, cast

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
"""Path to directory containing this Python script."""
sys.path.append(str(SCRIPT_DIR))
"""Allow runstarnet CLI to import modules from script directory."""

# pylint: disable=wrong-import-position
from __init__ import __version__  # pylint: disable=no-name-in-module
from PIL import Image
from samsungtvws import SamsungTVArt
from samsungtvws.exceptions import ConnectionFailure, ResponseError, UnauthorizedError

# pylint: enable=wrong-import-position

# Global Constants

SCRIPT_NAME: str = os.path.basename(__file__).split(".")[0]
"""Name of this script without .py extension."""
LOGGER = logging.getLogger("frametvsync")
"""Logger object."""
LOG_MAX_BYTES = 5 * 1024 * 1024
"""Max rotating logfile size."""
LOG_BACKUP_COUNT = 5
"""Max number of log files."""
STATE_VERSION: int = 1
"""State JSON file version number."""
DEFAULT_PORT: int = 8002
"""Default Samsung Frame TV port."""
DEFAULT_ROTATION: int = 10
"""Default slideshow rotation interval in minutes."""
TV_REQUEST_TIMEOUT: int = 10
"""Timeout for TV requests in seconds."""
SYNC_STATE: str = ".sync_state/.frametvsync_state.json"
"""Path to sync state file."""
TOKEN_FILE: str = ".sync_state/.frametvsync.token"
"""Path to token file."""
VALID_IMAGE_SUFFIXES: set[str] = {".jpg", ".jpeg"}
"""Valid image file extensions."""
# VALID_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}  # .png files cause websocket connection closed during upload errors
DEFAULT_MATTE_TYPE: str = "shadowbox"
"""Default supported matte type."""
DEFAULT_MATTE_COLOR: str = "black"
"""Default supported matte color."""


# Global variables

ARGS: argparse.Namespace = argparse.Namespace()
"""Parsed arguments."""
WARNINGS: int = 0
"""Warning message count."""


def notify(title: str, message: str) -> None:
    """Post notification.

    Args:
        title (str): Title of the notification.
        message (str): Message to display in the notification.
    """
    system: str = platform.system()

    if system == "Windows":
        from win11toast import notify as win_notify

        win_notify(title, message)

    elif system == "Darwin":
        import subprocess

        subprocess.run(
            [
                "osascript",
                "-e",
                f'display notification "{message}" with title "{title}"',
            ],
            check=True,
        )

    else:
        raise RuntimeError(f"Unsupported platform: {system}")


def cleanup_and_exit(status: int = 0) -> NoReturn:
    """Clean up and exit with the given status code.

    Args:
        status (int): Exit status code. Defaults to 0.
    """
    LOGGER.info(f"{SCRIPT_NAME} finished.")
    LOGGER.info(f"{'=' * 60}")
    logging.shutdown()
    sys.exit(status)


def warn_msg(msg: str) -> None:
    """Print a WARNING message and increment the warning count.

    Args:
        msg (str): Warning message to display.
    """
    global WARNINGS
    LOGGER.warning(msg)
    WARNINGS += 1


def fatal_error(msg: str) -> NoReturn:
    """Print a CRITICAL message and sys.exit(1).

    Args:
        msg (str): Error message to display.
    """
    LOGGER.critical(f"{msg}; exiting.")
    notify("FrameTVSync", msg)
    cleanup_and_exit(1)


class ConsoleHandler(logging.StreamHandler):
    pass


def configure_logging(directory: Path) -> None:
    """Configure console logging and rotating logs.

    Args:
        directory (Path): Directory to store log files.

    """
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    console_handlers = [
        handler for handler in logger.handlers if isinstance(handler, ConsoleHandler)
    ]
    if ARGS.quiet:
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


class PubImage(Path):
    """pathlib.Path subclass for published image files."""

    def __init__(self, *args, **kwargs) -> None:
        """Initialize a PubImage instance and read its metadata."""

        super().__init__(*args, **kwargs)

        LOGGER.info(f"Reading image metadata for {self} ...")
        self.image: Image.Image = Image.open(self)
        """Image object for this file."""
        self.mtime_ns: int = self.stat().st_mtime_ns
        """Image file modification time in nanoseconds."""
        self.matte_id: str = self._get_matte_id()
        """Image matte ID derived from Lightroom's HierarchicalSubject XMP metadata."""

        # Published files usually do not have capture time.

        # try:
        #     # Parse string into a timezone-aware datetime object
        #     capture_time = self._image.getexif().get(36867)
        #     if capture_time is not None:
        #         # Convert to the local system timezone
        #         self._capture_time = (
        #             parser.parse(str(capture_time)).astimezone().strftime("%Y:%m:%d %H:%M:%S")
        #         )
        # except Exception as exc:
        #     warn_msg(f"Error reading capture time from EXIF for {self.name}: {exc}")

        LOGGER.info(
            f"{self.name} metadata: size={self.image.width}x{self.image.height}, "
            f"mtime_ns={self.mtime_ns}, matte_id={self.matte_id}."
        )

    def with_segments(self, *segments):
        """
        CRITICAL: Ensures path modifications (.parent, / operator)
        generate a brand new path instance with its own unique object ID.
        """
        return PubImage(*segments)

    def get_data(self) -> bytes:
        """Read the image file data as bytes.

        Returns:
            bytes: The image file data as bytes.
        """
        return self.read_bytes()

    def size(self) -> tuple[int, int]:
        """Return the image size as a tuple (width, height).

        Returns:
            tuple[int, int]: The image size as a tuple (width, height).
        """
        return self.image.size

    def is_portrait(self) -> bool:
        """Return True if portrait aspect ratio.
        Returns:
            bool: True if portrait aspect ratio.
        """
        w, h = self.image.size
        return h > w

    def _read_xmp_subject(self) -> str | None:
        """Read Lightroom's HierarchicalSubject XMP metadata from a JPEG.

        Returns:
            str | None: The hierarchical subject string or None if not found.
        """
        try:
            xmp_raw: Any | None = None
            if "xmp" in self.image.info:
                xmp_raw = self.image.info.get("xmp")
            else:
                getter = getattr(self.image, "getxmp", None)
                if callable(getter):
                    xmp_raw = getter()

            if not xmp_raw:
                return None

            if isinstance(xmp_raw, bytes):
                xmp_text: str = xmp_raw.decode("utf-8", errors="ignore")
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
            LOGGER.error(f"Error reading XMP HierarchicalSubject from {self.name}: {exc}")

        return None

    def _get_matte_id(self) -> str:
        """Convert matte metadata from XMP HierarchicalSubject to the TV matte_id format.

        Args:
            hierarchical_subjects (str): The full hierarchicalSubject string from the XMP metadata.

        Returns:
            A string in the format "<matte_type>_<matte_color>", e.g. "modern_polar".
            If the matte type is "none" or "custom", returns "none_none".
            If the matte type is not found or unsupported, logs a warning and returns "none_none".
            If the matte color is not specified, defaults to "black" (e.g., "modern_black").

        hierarchicalSubject format:
            *Art_Frame|Matte|<matte_type>*
            *Art_Frame|Color|<matte_color>*
        """

        hierarchical_subjects: str | None = self._read_xmp_subject()

        try:
            if hierarchical_subjects is None:
                raise ValueError("No HierarchicalSubject")

            hierarchical_subjects = hierarchical_subjects.strip()
            if not hierarchical_subjects:
                raise ValueError("Empty HierarchicalSubject")

            matte_types: list[str]
            if matte_types := sorted(
                re.findall(
                    r"\*\s*Art_Frame\s*\|\s*Matte\s*\|\s*([^*]+)",
                    hierarchical_subjects,
                    flags=re.IGNORECASE,
                )
            ):
                matte_type: str = matte_types[0].strip().lower()
                if len(matte_types) > 1:
                    warn_msg(
                        f"{len(matte_types)} matte types found in {self.name}: {', '.join(matte_type for matte_type in matte_types)}; using {matte_type}."
                    )
            else:
                raise ValueError("No Art_Frame|Matte found in HierarchicalSubject")

            if matte_type in ("none", "custom"):
                return "none_none"  # Default color none for no matte or custom matte

            matte_colors: list[str]
            if matte_colors := sorted(
                re.findall(
                    r"\*\s*Art_Frame\s*\|\s*Matte_Color\s*\|\s*([^*]+)",
                    hierarchical_subjects,
                    flags=re.IGNORECASE,
                )
            ):
                matte_color: str = matte_colors[0].strip().lower()
                if len(matte_colors) > 1:
                    warn_msg(
                        f"{len(matte_colors)} matte colors found in {self.name}: {', '.join(matte_color for matte_color in matte_colors)}; using {matte_color}."
                    )
                return f"{matte_type}_{matte_color}"

            else:
                return f"{matte_type}_black"  # Default to black if no color is specified

        except ValueError as exc:
            warn_msg(f"{exc}.")
            return "none_none"


class FrameImage:
    """Frame art metadata returned by available()."""

    def __init__(
        self,
        category_id: str,
        content_id: str,
        image_date: str,
        matte_id: str,
        height: int,
        width: int,
    ) -> None:
        """Initialize FrameImage metadata.

        Args:
            category_id (str): Category ID, format: MY_Cnnnn
                MY-C0002: My Photos
                MY-C0003: Samsung Collection
                MY-C0004: Favorites
            content_id (str): Content ID:
                MY_Fnnnn: My Photo
                SAM_...:  Samsung art
            image_date (str): Image date, format: YYYY:MM:DD hh:mm:ss.
            matte_id (str): Matte ID, format <matte type>_<color>, e.g. shadowbox_black.
            height (int): Image height in pixels.
            width (int): Image width in pixels.
        """

        self.category_id = category_id.strip().upper()
        """Category ID."""
        self.content_id = content_id.strip().upper()
        """Content ID."""
        self.image_date = image_date.strip()
        """Image date, format: YYYY:MM:DD hh:mm:ss."""
        self.matte_id = matte_id.strip().lower()
        """Matte ID, format <matte type>_<color>, e.g. shadowbox_black."""
        self.height = height
        """Image height in pixels."""
        self.width = width
        """Image width in pixels."""


class TVState(TypedDict):
    """TV state in .frametvsync_state.json file."""

    ip: str
    """IP address."""
    port: int
    """Port number."""
    rotation: int
    """Slideshow rotation interval in minutes."""


class ImageState(TypedDict):
    """Frame art image state in .frametvsync_state.json file."""

    content_id: str
    """Content ID, assigned by TV on upload, format: MY_Fnnnn."""
    mtime_ns: int
    """Published image modification time in ns."""
    matte: str
    """Published image matte from HierarchicalImage """


class FrameArt:
    """Samsung Frame TV."""

    def __init__(self, token_file: Path, ip: str, port: int, rotation: int) -> None:
        """Initialize the Samsung Frame TV connection and download its artwork.

        Args:
            token_file (Path): Path to the Samsung TV authorization token.
            ip (str): Samsung Frame TV IP address.
            port (int): Samsung Frame TV port.
            rotation (int): Slideshow rotation interval in minutes.
        """

        self.matte_ids: set[str] = set()
        """Supported matte types and colors downloaded from TV."""
        self.frame_art: dict[str, FrameImage] = {}
        """Frame images currently available on the TV, keyed by content_id."""
        self.tv: SamsungTVArt
        """SamsungTVArt object"""
        self.deleted_art: set[str] = set()
        """content_ids of deleted art."""
        self._image_count: int = 0
        """content_id sequnce number for --dry-run."""

        LOGGER.info(f"Connecting to Samsung Frame TV at {ip}:{port} ...")

        token: str = ""
        """Samsung Frame TV authorization token."""
        if token_file.is_file():
            token = token_file.read_text(encoding="utf-8").strip()
            if token:
                LOGGER.info(f"Using auth token from {token_file}.")
            else:
                token_file.unlink()
                LOGGER.info(f"No auth token found in {token_file}.")
        else:
            LOGGER.info(f"No auth token file found at {token_file}.")

        try:
            self.tv = SamsungTVArt(
                host=ip,
                port=port,
                token_file=str(token_file),
                name="frametvsync",
                timeout=TV_REQUEST_TIMEOUT,
            )

            self.tv.open()

            if not self.tv.get_artmode():
                LOGGER.info("Switching to art mode ...")
                self.tv.set_artmode(True)

            self.download_art()

            matte_ids: list[dict[str, str]] = self.tv.get_matte_list()["matte_types"]
            self.matte_ids = {matte_id["matte_id"].strip().lower() for matte_id in matte_ids}
            self.matte_ids.add("none_none")
            LOGGER.info(f"Supported matte ids: {', '.join(sorted(self.matte_ids))}")

            if f"{DEFAULT_MATTE_TYPE}_{DEFAULT_MATTE_COLOR}" not in self.matte_ids:
                fatal_error(
                    f"Default matte id {DEFAULT_MATTE_TYPE}_{DEFAULT_MATTE_COLOR} not supported by TV."
                )

            token = token_file.read_text(encoding="utf-8").strip() if token_file.exists() else ""
            if not token:
                warn_msg(f"SamsungTVWS connected but did not write a token to {token_file}.")

            LOGGER.info(f"Connected to Samsung Frame TV at {ip}:{port}.")

        except UnauthorizedError as exc:  # pragma: no cover - runtime interaction
            raise RuntimeError(
                "The TV rejected the connection. Approve the pairing request on the TV, then retry. "
                f"If no prompt appears, delete {token_file} and retry."
            ) from exc

    def supported_matte_id(self, pub_image: PubImage) -> str:
        """Convert matte ID to one supported by the TV.

        Args:
            pub_image (PubImage): Published image.

        Returns:
            str: A supported matte_id.
        """

        matte_type, matte_color = pub_image.matte_id.split("_")
        if matte_type != "none" and not f"{matte_type}_black" in self.matte_ids:
            warn_msg(
                f'Unsupported matte type "{matte_type}" for {pub_image.name}, using {DEFAULT_MATTE_TYPE}.'
            )
            matte_type = DEFAULT_MATTE_TYPE
        if matte_color != "none" and not f"shadowbox_{matte_color}" in self.matte_ids:
            ok_color = "none" if matte_type == "none" else DEFAULT_MATTE_COLOR
            warn_msg(
                f'Unsupported matte color "{matte_color}" for {pub_image.name}, using "{ok_color}".'
            )
            return f"{matte_type}_{ok_color}"

        return f"{matte_type}_{matte_color}"

    def set_rotation(self, rotation: int) -> bool:
        """Set slideshow rotation interval.

        Args:
            rotation (int): Slideshow rotation interval in minutes.

        Returns:
            bool: True if rotation set succussfully.
        """

        if ARGS.dry_run:
            return True

        LOGGER.info(f"Setting TV artwork rotation to {rotation} minutes ...")
        set_slideshow_status = getattr(self.tv, "set_slideshow_status", None)
        if callable(set_slideshow_status):
            try:
                set_slideshow_status(duration=rotation, category_id="MY-C0004")
            except ResponseError:
                LOGGER.info("TV rejected slideshow API; trying legacy rotation API ...")
                try:
                    self.tv.set_auto_rotation_status(duration=rotation, category_id="MY-C0004")
                except ResponseError as exc:
                    warn_msg(f"TV rejected both artwork rotation APIs: {exc}.")
                    return False

        else:
            try:
                self.tv.set_auto_rotation_status(duration=rotation, category_id="MY-C0004")
            except ResponseError as exc:
                warn_msg(f"TV rejected the artwork rotation API: {exc}.")
                return False

        LOGGER.info(f"TV artwork rotation set to {rotation} minutes.")
        return True

    def download_art(self) -> dict[str, FrameImage]:
        """Download available art metadata from TV.

        Returns:
            dict[str, FrameImage]: Dictionary of FrameImage objects keyed by content_id.
        """
        LOGGER.info("Downloading Samsung Frame TV artwork information ...")
        tv_images = self.tv.available()
        self.frame_art = {
            image["content_id"]: FrameImage(**image)
            for image in tv_images
            if image["content_id"].startswith("MY_")
        }
        LOGGER.info(f"Downloaded {len(self.frame_art)} artwork items from the TV.")

        return self.frame_art

    def upload_art(self, pub_image: PubImage) -> str:
        """Upload a published image to the TV.

        Args:
            pub_image (PubImage): Image to publish.

        Returns:
            str: Content_id of uploaded image assigned by TV or "" if upload failed.
        """

        supported_matte_id: str = self.supported_matte_id(pub_image)
        LOGGER.info(f"Reading image data for {pub_image.name} ...")
        image_data: bytes = pub_image.get_data()

        try:
            if ARGS.dry_run:
                if "PLUMBING PROBLEM!!!" in pub_image.stem:
                    LOGGER.info(f"DRY-RUN: Simulating {pub_image.name} upload failure ...")
                    raise ConnectionFailure("DRY-RUN: Simulated upload failure.")

                LOGGER.info(
                    "DRY-RUN: Would upload %s with matte=%s.",
                    pub_image.name,
                    supported_matte_id,
                )
                self._image_count += 1
                return f"XX_F{self._image_count:04d}"  # Dummy content_id for --dry-run

            LOGGER.info(f"Uploading {pub_image.name} with matte={supported_matte_id} ...")

            content_id: str = self.tv.upload(
                image_data,
                file_type="PNG" if pub_image.suffix.lower() == ".png" else "JPEG",
                matte=supported_matte_id,
                portrait_matte=supported_matte_id,
                # date=(pub_image.capture_time if pub_image.capture_time else None)
            )
        except Exception as exc:  # noqa: BLE001
            warn_msg(f"Failed to upload TV artwork {pub_image.name}: {exc}")
            return ""

        LOGGER.info(f"Uploaded {pub_image.name}.")
        sleep(2)
        LOGGER.info(f"{pub_image.name} uploaded, content_id {content_id}.")
        LOGGER.info(f"Setting {pub_image.name} as favorite ...")
        self.tv.set_favourite(content_id, status="on")
        LOGGER.info(f"{pub_image.name} set as favorite.")
        return content_id

    def delete_art(self, content_id: str, image_name: str | None = None) -> bool:
        """Delete art from TV.

        Args:
            content_id (str): content_id of image to be deleted.
            image_name (str | None, optional): Name of published image. Defaults to None.

        Returns:
            bool: True if delete successful
        """

        art_name: str = f" {image_name}" if image_name else ""

        if ARGS.dry_run:
            if image_name and "PLUMBING PROBLEM!!!" in image_name:
                LOGGER.info(f"DRY-RUN: Simulating {image_name} delete failure ...")
                return False

            LOGGER.info(f"DRY-RUN: Would delete TV artwork {content_id}{art_name}.")
            self.deleted_art.add(content_id)
            return True

        LOGGER.info(f"Deleting TV artwork {content_id}{art_name} ...")
        try:
            self.tv.delete(content_id)
            LOGGER.info(f"Deleted TV artwork {content_id}{art_name}.")
            sleep(2)
        except Exception as exc:  # pragma: no cover - runtime interaction  # noqa: BLE001
            warn_msg(f"Failed to delete TV artwork {content_id}{art_name}: {exc}.")
            return False

        LOGGER.info(f"TV artwork {content_id}{art_name} deleted.")
        self.deleted_art.add(content_id)
        return True


class SyncStats:
    """Sync statistics returned by SyncStats.discover_changes()."""

    def __init__(self, unknown, missing, deleted, added, modified) -> None:
        self.unknown: int = unknown
        """Number of images that were found on the TV but not published."""
        self.missing: int = missing
        """Number of images that were found on the TV but were not published."""
        self.deleted: int = deleted
        """Number of images that were deleted from the published folder."""
        self.added: int = added
        """Number of images that were added to the published folder."""
        self.modified: int = modified
        """Number of images that were modified in the published folder."""

    def __str__(self) -> str:
        return f"Unknown: {self.unknown}, Missing: {self.missing}, Deleted: {self.deleted}, Added: {self.added}, Modified: {self.modified}"


class SyncState:
    """Published folder <-> Frame TV synchronization state."""

    def __init__(self, state_file: Path) -> None:
        """Initialize sync state.

        Args:
            state_file (Path): Path to the JSON file storing the sync state.
        """
        self.pub_dir: Path = ARGS.directory
        """Lightroom publish directory containing images to sync with the TV."""
        self.pub_dir: Path = ARGS.directory
        """Lightroom publish directory containing images to sync with the TV."""
        self.state_file: Path = state_file
        """Path to the JSON file storing the sync state."""
        self.version: int = STATE_VERSION
        """Version of the sync state format."""
        self.tv_state: TVState = {
            "ip": str(ARGS.ip) or "",
            "port": DEFAULT_PORT,
            "rotation": ARGS.rotation,
        }
        """State of the connected Samsung Frame TV."""
        self.images: dict[str, ImageState] = {}
        """Image state keyed by filename."""
        self.id_map: dict[str, str] = {}
        """Image name keyed by TV content_id."""
        self.to_upload: list[PubImage] = []
        """List of images that need to be uploaded to the TV."""
        self.to_delete: list[str] = []
        """List of content_ids of images that need to be deleted from the TV."""

        if not self.state_file.exists():
            LOGGER.info(f"No existing sync state file found at {self.state_file}; initializing.")
            if not ARGS.ip:
                fatal_error("A TV IP address is required, specify --ip..")

            self.tv_state["ip"] = str(ARGS.ip)
            return

        LOGGER.info(f"Loading sync state from {self.state_file} ...")
        with self.state_file.open("r", encoding="utf-8") as file:
            state = json.load(file)
        LOGGER.info(f"Sync state loaded from {self.state_file}.")

        if state["version"] != STATE_VERSION:
            fatal_error(
                f"State file version mismatch: expected {STATE_VERSION}, found {state['version']}. ",
            )

        self.tv_state = state["tv"]
        if ARGS.ip and self.tv_state["ip"] != ARGS.ip:
            self.tv_state["ip"] = str(ARGS.ip)
        self.images = cast(dict[str, ImageState], state["images"])
        self.id_map = {image["content_id"]: name for name, image in self.images.items()}
        LOGGER.info(f"Loaded {len(self.images)} image state entries from {self.state_file}.")

    def __len__(self) -> int:
        return len(self.images)

    def save(self) -> None:
        """Save sync state to file."""

        LOGGER.info(f"Saving sync state to {self.state_file} ...")
        self.pub_dir.mkdir(parents=True, exist_ok=True)
        with self.state_file.open("w", encoding="utf-8") as file:
            json.dump(
                {"version": self.version, "tv": self.tv_state, "images": self.images},
                file,
                indent=2,
            )
            file.write("\n")
        LOGGER.info(f"Sync state saved to {self.state_file}.")

    def discover_changes(self, pub_images: dict[str, PubImage], frame_art: FrameArt) -> SyncStats:
        """Update the sync state and to_delete and to_upload lists.

        Args:
            pub_images (dict[str, PubImage]): Dictionary of published images keyed by filename.
            frame_art (FrameArt): FrameArt object containing the TV artwork.

        Returns:
            dict[str, int]: Dictionary of counts keyed by type: unknown, missing, deleted, added, and modified.
        """
        unknown: int = 0
        """Count of unknown images on TV to be deleted."""
        missing: int = 0
        """Count of images missing on TV."""
        deleted: int = 0
        """Count of deleted published images."""
        added: int = 0
        """Count of added published images."""
        modified: int = 0
        """Count of modified published images."""

        LOGGER.info("Checking for unknown images on TV ...")
        for unknown, content_id in enumerate(frame_art.frame_art.keys() - self.id_map.keys(), 1):
            LOGGER.info(f"Adding content_id {content_id} of unknown image to delete list.")
            self.to_delete.append(content_id)

        LOGGER.info("Checking for missing images on TV ...")
        for missing, content_id in enumerate(self.id_map.keys() - frame_art.frame_art.keys(), 1):
            image_name = self.id_map[content_id]
            LOGGER.info(
                f"Deleting content_id {content_id} of missing image {image_name} from sync state."
            )
            del self.images[image_name]
            del self.id_map[content_id]

        LOGGER.info("Processing deleted published images ...")
        for deleted, image in enumerate(self.images.keys() - pub_images.keys(), 1):
            content_id = self.images[image]["content_id"]
            LOGGER.info(f"Adding content_id {content_id} of deleted image {image} to delete list.")
            self.to_delete.append(content_id)
            del self.images[image]
            del self.id_map[content_id]

        LOGGER.info("Processing new published images ...")
        for added, pub_image_name in enumerate(pub_images.keys() - self.images.keys(), 1):
            LOGGER.info(f"Adding new published image {pub_image_name} to upload list.")
            pub_image: PubImage = pub_images[pub_image_name]
            self.to_upload.append(pub_image)

        LOGGER.info("Processing modified published images ...")
        for pub_image_name in pub_images.keys() & self.images.keys():
            pub_image = pub_images[pub_image_name]
            image_state: ImageState = self.images[pub_image_name]
            if (
                pub_image.mtime_ns != image_state["mtime_ns"]
                or pub_image.matte_id != image_state["matte"]
            ):
                content_id = image_state["content_id"]
                LOGGER.info(
                    f"Adding content_id {content_id} of modified image {pub_image.name} to upload and delete lists."
                )
                self.to_upload.append(pub_image)
                self.to_delete.append(content_id)
                modified += 1

        return SyncStats(unknown, missing, deleted, added, modified)

    def commit_changes(self, frame_art: FrameArt) -> tuple[int, int]:
        """Commit art deletions and uploads to TV.

        Args:
            frame_art (FrameArt): FrameArt object containing the TV artwork.

        Returns:
            tuple[int, int]: Tuple of counts of deleted and uploaded images.
        """

        deleted: int = 0
        """Count of images deleted from TV."""
        uploaded: int = 0
        """Count of image uploads to TV."""

        dry_run: str = "would be " if ARGS.dry_run else ""

        for content_id in self.to_delete:
            image_name = self.id_map.get(content_id, None)
            if frame_art.delete_art(content_id, image_name):
                deleted += 1

        LOGGER.info(f"{len(self.to_delete)} images {dry_run}deleted from TV.")
        self.to_delete.clear()

        for pub_image in self.to_upload:
            new_content_id = frame_art.upload_art(pub_image)
            if new_content_id:
                self.images[pub_image.name] = ImageState(
                    content_id=new_content_id,
                    mtime_ns=pub_image.mtime_ns,
                    matte=pub_image.matte_id,
                )
                self.id_map[new_content_id] = pub_image.name
                uploaded += 1

        LOGGER.info(f"{len(self.to_upload)} images {dry_run}uploaded to TV.")
        self.to_upload.clear()

        if not ARGS.dry_run:
            self.save()

        frame_art.set_rotation(self.tv_state["rotation"])

        return deleted, uploaded


def synchronize(token_file: Path, state_file: Path) -> None:
    """Synchronize a directory of JPEG images to a Samsung Frame TV.

    Args:
        token_file (Path): Path to the token file containing the TV authentication token.
        state_file (Path): Path to the state file containing the TV state.

    Returns:
        None
    """

    notify("FrameTVSync", f"Syncing images in {ARGS.directory} ...")

    pub_images: dict[str, PubImage] = {
        path.name: PubImage(path)
        for path in sorted(ARGS.directory.iterdir())
        if path.is_file() and path.suffix.lower() in VALID_IMAGE_SUFFIXES
    }
    """Published images in the sync directory, keyed by filename."""

    state: SyncState = SyncState(state_file)
    frame_art: FrameArt = FrameArt(
        token_file,
        **state.tv_state,
    )
    sync_stats: SyncStats = state.discover_changes(pub_images, frame_art)
    """Sync statistics."""

    to_be_added: int = sync_stats.added
    """Count of images added to TV."""
    to_be_modified: int = sync_stats.modified
    """Count of images modified on TV."""
    to_be_deleted: int = sync_stats.deleted + sync_stats.unknown
    """Count of images deleted from TV."""

    deleted, uploaded = state.commit_changes(frame_art)
    upload_fail = to_be_added + to_be_modified - uploaded
    delete_fail = to_be_deleted + to_be_modified - deleted

    if ARGS.dry_run:
        stats: str = f"{to_be_added} images would be added, {to_be_modified} images would be modified, {to_be_deleted} images would be deleted."
        dry_run: str = "DRY RUN "
    else:
        stats: str = f"{to_be_added} images added, {to_be_modified} images modified, {to_be_deleted} images deleted."
        dry_run: str = ""

    if WARNINGS:
        LOGGER.info(f"Sync {dry_run}completed with {WARNINGS} warning(s).")
        if upload_fail > 0 or delete_fail > 0:
            stats = f"{upload_fail} upload(s) failed, {delete_fail} deletion(s) failed."
        notify(
            "FrameTVSync", f"Sync {dry_run}completed with {WARNINGS} warning(s) -- see log\n{stats}"
        )

    else:
        notify("FrameTVSync", f"Sync {dry_run}completed!\n{stats}")

    LOGGER.info(stats)

    if not ARGS.dry_run:
        state.save()


def main() -> None:
    """Main entry point of the FrameTVSync script."""

    global ARGS

    parser = argparse.ArgumentParser(description="Sync a directory of JPEGs to a Samsung Frame TV.")
    parser.add_argument("directory", type=Path, help="Directory containing JPEG images to sync")
    parser.add_argument("--ip", type=ip_address, help="Samsung Frame TV IP address")
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
        "--rotation",
        type=int,
        default=DEFAULT_ROTATION,
        help="Set the TV artwork rotation period in minutes (0 disables rotation), default: %(default)d",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Watch the published folder for changes",
    )
    ARGS = parser.parse_args(sys.argv[1:])

    directory: Path = Path(ARGS.directory).expanduser().resolve()
    if not directory.exists() or not directory.is_dir():
        parser.error(f"Directory not found: {directory}.")
    ARGS.directory = directory

    if ARGS.rotation < 0:
        parser.error("Rotation interval must be zero or greater.")

    configure_logging(directory)
    LOGGER.info(f"{'=' * 60}")
    LOGGER.info(f"{SCRIPT_NAME} version {__version__} starting ...")
    LOGGER.info(f"Command: {', '.join(sys.argv[1:])}")

    token_file = directory / TOKEN_FILE
    LOGGER.info(f"SamsungTVWS token file: {token_file}.")
    state_file = directory / SYNC_STATE
    LOGGER.info(f"Sync state file: {state_file}.")

    synchronize(token_file, state_file)


def cli() -> None:
    """Command-line interface."""
    try:
        main()
        cleanup_and_exit(0)

    except Exception as e:  # noqa: BLE001
        fatal_error(str(e))


if __name__ == "__main__":
    cli()
