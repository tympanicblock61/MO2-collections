# MO2 Collections

A Mod Organizer 2 plugin that brings native Nexus Mods Collections support to MO2.
Click a collection link in your browser and MO2 handles everything - downloading,
installing, and ordering every mod automatically.

## Features

- **One-click install** - open a collection link from the Nexus website and the plugin does the rest
- **Automatic downloading** - downloads all mods in the collection concurrently
- **Automatic installation** - installs each mod in the correct order without user interaction
- **Fomod support** - automatically selects the options specified in the collection during fomod installers
- **Mod load order** - applies the collection's mod priority order after installation
- **Plugin load order** - applies the collection's plugin load order rules after installation
- **Premium and free accounts** - premium users use the NexusMods API directly; free users download via browser session cookies

## Planned

- Support for collection groups
- Optimise or replace the fomod preprocessor with GUI automation
- Session token refresh when cookies expire

## How It Works

The plugin needs access to your NexusMods account in two ways:

- **API key** - read from the Windows Credential Manager, where MO2 already stores it
- **Session token** (free accounts only) - decrypted from your browser's local cookie storage using your Windows login credentials

No credentials are transmitted anywhere other than to NexusMods. If you are not comfortable with either of these access methods, do not install this plugin.

Supported browsers for cookie extraction: Chrome, Edge, Brave.

## Installation

1. Download the `nexus_collections` and `nxmhandler` folders and place them in your
   MO2 `plugins` directory.

2. Inside `nxmhandler/app`, create a file named `nxmhandler.ini` with the following
   content, replacing the executable path with your own MO2 installation:

```ini
   [handlers]
   size = 1
   1\games = ""
   1\executable = <Path to your ModOrganizer.exe>
   1\types = "mods"
   1\arguments =
```

3. Inside the `nexus_collections` directory, install the required libraries:
   `pip install -r requirements.txt --upgrade --target ./libs`
5. Restart MO2. The plugin will register itself as the handler for `nxm://` collection links.

## Usage

With MO2 open, click any **Add collection** button on a Nexus Collections
page. MO2 will begin downloading and installing all mods in the collection automatically.
Progress is logged to `nexus_collections.log` in the plugin directory.

## Requirements

- Mod Organizer 2 2.5+
- Python 3.12 (bundled with MO2)
- Windows (DPAPI cookie decryption is Windows-only)
- A NexusMods account (free or premium)

## Debugging

If you run into issues, the following logs are helpful for diagnosis:

- **Plugin log** - `<MO2 plugins folder>/nexus_collections/data/nexus_collections.log`
- **MO2 log** - `<MO2 instance folder>/logs/mo_interface.log`

### Known Issues

- **Downloads may occasionally pause, stall, or fail** mid-collection. This is a known
  limitation of how MO2 exposes download state to plugins. If a download stalls,
  you can resume it manually from the MO2 Downloads tab and the plugin will continue
  from where it left off.

- **MO2 may appear to freeze** during installation, particularly while an installer
  dialog is being processed or a large mod is being extracted. This is not a crash -
  MO2's UI is blocked while it processes the installation internally. Wait a minute
  or two before assuming something has gone wrong. If the log is still producing
  output, the process is still running.
  
### Reporting Issues

Please include both logs when reporting a problem. You can find support on the MO2 Discord server, contact me there as `zombiiess`.

When reporting, it helps to also mention:
- Which collection you were installing (slug or URL)
- Whether you are on a free or premium account
- Which browser you use (for free account cookie extraction)
- What step the process appeared to stop at
