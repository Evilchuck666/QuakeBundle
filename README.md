# 🎮 QuakeBundle

**Back to Quake, with less setup.**

QuakeBundle is a desktop installer and launcher for Quake. It brings the campaign
and multiplayer into one window, helping you download the game, set up an engine,
and start playing on Windows, Linux, or macOS.

## 🧩 Why QuakeBundle?

Getting Quake ready to play can involve several separate tasks: downloading your
Steam copy, choosing an engine, putting files in the right folders, and editing
configuration files.

QuakeBundle brings those steps together. Choose how you want to play, pick an
installation folder, and let the application handle the downloads and setup.
You can adjust your player settings and launch the game from the same window.

## ⚔️ Two ways to play

| Experience | What you get |
| --- | --- |
| **Campaign** | Quake and its expansions with **vkQuake**. |
| **Multiplayer** | QuakeWorld with **ezQuake**, plus **nQuake** content and addons. |

## ✨ What it does

- Downloads the game through Steam and installs the engine for your chosen experience.
- Guides you through Steam login and Steam Guard in the application.
- Lets you set your player name, mouse sensitivity, and multiplayer preferences.
- Shows installation progress and an activity log.
- Preserves existing configuration files, apart from the player settings you edit.
- Creates a shortcut and lets you launch an installed game without signing in again.

## 🚀 Getting started

1. Download the package for your system from the
   [latest release](https://github.com/Evilchuck666/QuakeBundle/releases/latest).
2. Extract the package and open **QuakeBundle** (`QuakeBundle.exe` on Windows).
3. Choose **Campaign** or **Multiplayer**, select a folder, and enter your Steam
   username and player name.
4. Start the installation and follow any Steam authentication prompts.
5. Once installation finishes, select **Play**. For the campaign, you can also
   choose an installed expansion.
   
## 📥 Download

| Platform | Latest stable release |
| --- | --- |
| GNU/Linux x64 | [Download](https://github.com/Evilchuck666/QuakeBundle/releases/latest/download/QuakeBundle-linux-x64.tar.gz) |
| Windows x64 | [Download](https://github.com/Evilchuck666/QuakeBundle/releases/latest/download/QuakeBundle-windows-x64.zip) |
| macOS Intel | [Download](https://github.com/Evilchuck666/QuakeBundle/releases/latest/download/QuakeBundle-macos-x64.zip) |
| macOS Apple Silicon | [Download](https://github.com/Evilchuck666/QuakeBundle/releases/latest/download/QuakeBundle-macos-arm64.zip) |

[Release notes](https://github.com/Evilchuck666/QuakeBundle/releases/latest)

## 📋 What you need

- A **Steam account that owns Quake**.
- An **Internet connection** for installation and enough disk space for the game.
- A compatible **Windows, Linux, or macOS** system. Platform requirements are
  explained in the [technical guide](TECHNICAL_GUIDE.md).

The standalone application includes its own Python and Qt runtime, so you do not
need to install them yourself. SteamCMD, the engines, and the game data are
downloaded during setup.

## 🔐 Your Steam account

Your password and Steam Guard responses are sent to SteamCMD. QuakeBundle does
not save or log them. SteamCMD may retain its own session for future installations.

## 📚 Learn more

- [Technical guide](TECHNICAL_GUIDE.md): detailed installation information,
  platform requirements, building from source, and verification.
- [Testing guide](tests/README.md): test coverage and how to run the checks.

Built with **Qt6 / PySide6**, using **SteamCMD**, **vkQuake**, **ezQuake**, and
**nQuake** to bring the pieces together.
