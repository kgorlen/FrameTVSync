# FrameTVSync

Sync a directory of Lightroom-published JPEG images to a Samsung Frame TV in art mode.

FrameTVSync reads JPEG metadata from Lightroom, maps the Lightroom matte information into Samsung TV matte IDs, uploads new or changed images, deletes images no longer in the published folder, and keeps a small JSON state file so repeated runs stay in sync.

## Features

- Uploads JPEG images from a watched publish folder to a Samsung Frame TV
- Reads Lightroom XMP `HierarchicalSubject` metadata to infer matte type and color
- Maps published images to Samsung matte IDs such as `modern_black`, `shadowbox_white`, and `none_none`
- Keeps a sync-state file in the published directory so only changed images are re-uploaded
- Deletes artwork that was removed from the published folder or is no longer recognized by the TV
- Optionally sets the slideshow rotation interval on the TV
- Works with Windows notifications on supported systems

## Requirements

- Python 3.12+
- A Samsung Frame TV with art mode enabled
- The TV connected to the same local network as the host running the script
- A Lightroom-published image directory containing JPEG files

This project depends on:

- `Pillow`
- `samsungtvws`
- `win11toast` (Windows only)

## Installation

From the project directory:

```bash
python -m pip install .
```

Or install in editable mode while developing:

```bash
python -m pip install -e .
```

After installation, the CLI entry point is:

```bash
frametvsync
```

## Quick start

1. Create a folder containing the images you want to show on the TV.
2. Run the sync command against that folder.
3. Approve the TV pairing request when prompted on the TV.
4. The tool will create a state file and token file inside the directory.

Example:

```bash
frametvsync "C:/Users/you/Pictures/FrameTV" --ip 192.168.1.50 --rotation 10
```

## Command usage

```bash
frametvsync DIRECTORY [--ip IP_ADDRESS] [--rotation MINUTES] [--dry-run] [--quiet]
```

### Arguments

- `directory`: Directory containing JPEG images to sync
- `--ip`: Samsung Frame TV IP address
- `--dry-run`: Show what would change without uploading or deleting anything
- `--rotation`: Slideshow rotation in minutes; `0` disables rotation
- `--quiet`: Suppress console logging while keeping file logging enabled
- `--watch`: Accepted by the CLI parser, but this project currently focuses on one-shot sync runs

## How it works

The workflow is intentionally simple:

1. Scan the published directory for `.jpg` and `.jpeg` files.
2. Read each file's metadata and determine the intended matte style/color from Lightroom's XMP metadata.
3. Connect to the Samsung Frame TV and download the current art list.
4. Compare the local published set with the TV's current set using a sync state JSON file.
5. Upload new or modified images.
6. Delete artwork that no longer belongs in the published set.
7. Save the state file and optionally update the slideshow rotation.

## State and token files

The first sync creates files inside the published directory:

- `.frametvsync.token` — Samsung TV authentication token
- `.sync_state/.frametvsync_state.json` — sync history and TV state

These files are used to persist the token and avoid re-uploading unchanged images.

## Lightroom notes

This utility is designed around Lightroom Classic publish folders and XMP metadata. It expects the published images to contain matte metadata in the form used by Lightroom's `HierarchicalSubject` fields, for example:

- `Art_Frame|Matte|modern`
- `Art_Frame|Matte_Color|black`

If those values are missing or not recognized, the code falls back to a neutral matte such as `none_none` or a default black matte.

## Example

This command syncs a local Lightroom publish folder to a TV at IP `192.168.1.50` with a 10-minute slideshow rotation:

```bash
frametvsync "D:/Lightroom/FrameTV" --ip 192.168.1.50 --rotation 10
```

A dry run is useful before changing the TV:

```bash
frametvsync "D:/Lightroom/FrameTV" --ip 192.168.1.50 --dry-run
```

## Troubleshooting

### TV pairing rejected

Delete the token file inside the image directory and retry:

```bash
rm .frametvsync.token
```

Then run the sync again and approve the pairing request on the TV.

### Art mode is not supported

The script will fail if the target TV does not support art mode.

### Missing or wrong matte styling

If the image metadata does not include Lightroom matte information, the TV may receive a fallback matte. Re-export or re-publish the image with the expected metadata.

## License

This project is licensed under the GNU General Public License v3.0 or later.

See [LICENSE.txt](LICENSE.txt) for details.
