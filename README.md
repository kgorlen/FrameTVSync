<!--
Markdown Guide: https://www.markdownguide.org/basic-syntax/
-->
<!--
Disable markdownlint errors:
fenced-code-language MD040
no-inline-html MD033
-->
<!-- markdownlint-disable MD040 MD033-->

# FrameTVSync

Synchronize JPEG images in a Lightroom Classic published folder to Samsung Frame TV Favorites slideshow collection.

**frametvsync** is a Python Command Line Interface (CLI) that reads JPEG metadata from images in a Lightroom Classic published folder, maps the Lightroom matte information into Samsung TV matte IDs, uploads new or changed images, and deletes images that have been removed.

## Features

- Syncs JPEG images from a published folder to the Samsung Frame TV *Favorites* collection.
- Ignores Samsung Collection and Art Store images on the Samsung Frame TV.
- Reads Lightroom XMP `HierarchicalSubject` metadata to set matte type and color.
- Maps published image matte type and color to Samsung matte IDs such as `modern_black`, `shadowbox_navy`, and `none_none`.
- Keeps a sync state file in the published directory so only changed images are re-uploaded.
- Deletes images that were removed from the published folder.
- Sets the slideshow rotation interval on the TV.
- Posts notifications of sync status.
- Logs details of the sync process.
- Optionally watches for changes to the published folder and automatically performs syncs.

## Requirements

- Python 3.12 or later
- A Samsung Frame TV* with art mode enabled and connected to the same local network as the host PC running the script
- A Lightroom published image folder containing JPEG files sized and formatted for the Samsung Frame TV

*NOTE: Tested on Windows 11 and 2018 Samsung Frame TV model UN55LS03NA; other models may not be compatible, see [*Samsung Smart TV WS API wrapper* by xschwarze](https://github.com/xchwarze/samsung-tv-ws-api).

## Frame TV Settings

`Settings → General → Network → Expert Settings → IP Remote Enabled`

`Settings → General → External Device Manager → Device Connection Manager → Access Notification → On` OR `First Time Only`

To find the IP address, go to `Settings → General → Network → Network Status → IP Settings`

## Install Python

See [Python](https://www.python.org/downloads/).

## Install pipx

See [pipx](https://pypi.org/project/pipx/).

## Install git

See [git](https://git-scm.com/install/).

## Install **frametvsync**

```
pipx install git+https://github.com/kgorlen/FrameTVSync.git
```

## Configure Adobe Lightroom Classic

### Create Lightroom keywords for matte types and colors

![Example Keywords](docs/images/Art%20Frame%20Keywords.png)

#### *Art_Frame* keyword

![Art_Frame](docs/images/Art_Frame%20keyword.png)

#### *Matte* keyword

![Matte](docs/images/Matte%20keyword.png)

#### Matte type keyword example

![Example Matte Type Keyword](docs/images/Custom%20keyword.png)

#### *Matte_Color* keyword

![Matte_Color](docs/images/Matte_Color%20keyword.png)

#### Matte color keyword example

![Example Matte Color Keyword](docs/images/Antique%20keyword.png)

Other models may support different matte types and colors.  Older models may not support all types/colors.  Check the `logs/framsynctv.log` file after syncing to see which types/colors are supported; for example:

```
2026-09-10 18:01:12 INFO frametvsync: Supported matte ids: modern_antique, modern_black, modern_burgandy, modern_navy, modern_neutral, modern_polar, modern_sage, modern_sand, modern_seafoam, modern_warm, none, none_none, shadowbox_antique, shadowbox_black, shadowbox_burgandy, shadowbox_navy, shadowbox_neutral, shadowbox_polar, shadowbox_sage, shadowbox_sand, shadowbox_seafoam, shadowbox_warm
```

### Create the *Art Frame Now Showing* Lightroom Collection

![Art Frame Now Showing](docs/images/Now%20Showing%20Collection.png)

Add images to be synced to Samsung Frame TV **Favorites** to this collection.  Images must contain a title and a *Matte* keyword to be synced.

### Create the *Art Frame* publish service

![Art Frame Publish Service](docs/images/Art%20Frame%20Publish%20Service.png)

### Create the *Favorites* Published Smart Folder

![Favorites Published Smart Folder](docs/images/Favorites%20Published%20Smart%20Folder.png)

These smart published folder rules assure that images contain a title and a *Matte* keyword.  Check that the *Favorites* and *Art Frame Now Showing* collections contain the same number of images.

## Command usage

```bash
frametvsync DIRECTORY [--ip IP_ADDRESS] [--rotation MINUTES] [--dry-run] [--quiet | --no-quiet] [--watch]
```

### Arguments

- `directory`: Directory containing JPEG images to sync
- `--ip`: Samsung Frame TV IP address
- `--dry-run`: Show what would change without uploading or deleting anything
- `--rotation`: Slideshow rotation interval in minutes; `0` disables rotation; default 10
- `--quiet`: Disable console logging; default is `--no-quiet` when `--watch` is used
- `--no-quiet`: Keep console logging enabled when `--watch` is used
- `--watch`: Watch the published folder for changes and sync when changed

## Initial sync

After adding some images to the *Art Frame Now Showing* collection, run the following command to set the TV IP address and the (optional) slideshow rotation interval, pair the TV, and perform the initial sync:

<pre>
frametvsync <i>path-to-published-folder</i> --ip <i>tv-ip-address</i> --rotation <i>rotation-minutes</i>
</pre>

For example:

```
frametvsync "C:\Users\Keith\Pictures\Art Frame" --ip 192.168.1.50 --rotation 15
```

A prompt should appear on the TV to allow the pairing request.  Once paired, **frametvsync** will perform the initial sync.  The IP address and rotation interval will be saved in the sync state file and do not need to be specified in subsequent commands.

## Subsequent syncs

After the initial sync, the following command will sync the folder to the TV:

<pre>
frametvsync <i>path-to-published-folder</i>
</pre>

For example:

```
frametvsync "C:\Users\Keith\Pictures\Art Frame"
```

## The `--watch` option

**frametvsync** can be started with the `--watch` option to monitor the published folder for changes and automatically sync the TV when changes are detected.

### Start `frametvsync --watch` with Windows Task Scheduler

Windows Task Scheduler example:

#### General tab

- Set user account name, e.g. `Keith`
- Run only when user is logged on

#### Triggers tab

![Task Scheduler Triggers](docs/images/Task%20Scheduler%20Triggers.png)

#### Actions tab

![Task Scheduler Actions](docs/images/Task%20Scheduler%20Actions.png)

Powershell arguments:

```
-NoProfile -NonInteractive -WindowStyle Hidden -Command "Stop-Process -Name 'frametvsyncw' -Force -ErrorAction SilentlyContinue; Start-Sleep -Seconds 2; & 'C:\Users\Keith\.local\bin\frametvsyncw.exe' --watch '\\NAS0\Multimedia\Pictures\Art Frame\Favorites'"
```

#### Settings tab

![Task Scheduler Settings](docs/images/Task%20Scheduler%20Settings.png)

### Start `frametvsync --watch` on macOS

Not yet implemented.

## Custom mattes and colors

The Lightroom Classic Print Module can be used to create custom mattes and colors:

![USAFA Cadet Chapel Diptych](docs/images/USAFA%20Cadet%20Chapel%20Diptych.jpg)

![Westfield Centre Nordstrom](docs/images/Westfield%20Centre%20Nordstrom%20polar.jpg)

![Entwined Collage](docs/images/Entwined%20Collage.jpg)

Custom mattes fill the Frame, should have a 16:9 aspect ratio, and be tagged with the `Matte → Custom` keyword.

Sample Lightroom Classic templates for the 55 in. Frame TV are available in the `lrtemplates` folder in the [GitHub FrameTVSync repository](https://github.com/kgorlen/FrameTVSync/tree/main/lrtimeplates).

## Authenticaton token, sync state, and log files

The first sync creates these folders/files in the published folder:

- `.sync_state/.frametvsync.token` — Samsung TV authentication token
- `.sync_state/.frametvsync_state.json` — image sync state, TV IP address, and slideshow rotation interval
- `logs/frametvsync.log` — log file for debugging and auditing, limited to five, 5 MB files

## Troubleshooting

### Warnings reported

Check the log file `.logs\frametvsync.log` for details.  Use the `--dry-run` flag for testing.

### **frametvsync** hangs when connecting to the TV

Known issue with the `samsung-tv-ws-api`.  Turn TV off, wait for **frametvsync** to time out, then turn the TV back on and place in Art Mode.

### TV access denied

To reauthorize **frametvsync** to access the TV:

1. Go to `Settings → General → External Device Manager → Device Connection Manager → Device List` and delete the PC (Smart Device).
1. Delete the token file `.sync_state\.framesync.token` in the published folder.
1. With the TV in Art Mode, run `frametvsync`.  The TV should prompt to allow access.

### Missing or wrong matte styling

`frametvsync` expects the published images to contain matte metadata in the form used by Lightroom's `HierarchicalSubject` fields, for example:

- `Art_Frame|Matte|modern`
- `Art_Frame|Matte_Color|black`

If those values are missing or not recognized, matte is set to `shadowbox`, matte color to `black`, and warning messages are logged.

## License

This project is distributed under the GNU GPL-3.0-or-later license. See `LICENSE.txt` for details.

## See Also

- [*Samsung Smart TV WS API wrapper* by xschwarze](https://github.com/xchwarze/samsung-tv-ws-api)
- [*Samsung Smart TV WS API wrapper* fork by Nick Waterton (not used for this project)](https://github.com/NickWaterton/samsung-tv-ws-api)
- [*The Samsung Frame: Best when Scripted* by Jon Scully](https://jonsully.net/blog/samsung-frame-art-api)
- [*Part 1: Uploading Photos from Lightroom to a Samsung Frame TV*, Dashed Yellow Line](https://dashedyellowline.com/2024/06/07/samsung-frame-tv-lightroom)
- [*Part 2: Using ChatGPT to Create a Frame TV Upload Script*, Dashed Yellow Line](https://dashedyellowline.com/2025/09/15/part-2-using-chatgpt-to-create-a-frame-tv-upload-script/)
- [*Art Mode API is Back!*, Reddit](https://www.reddit.com/r/TheFrame/comments/1c2lgbs/art_mode_api_is_back/)

## Author

Keith Gorlen (<kgorlen@gmail.com>)
