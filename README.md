# DellPrintBridge

**Give a perfectly good old printer a second life by turning its working Windows driver into a modern, driverless IPP print bridge.**

DellPrintBridge exposes existing Windows printer queues to phones and other modern clients as network IPP printers. Android can discover the bridge through its built-in **Default Print Service** and print without installing a discontinued or proprietary manufacturer mobile app.

> **Confirmed working:** native Android printing through one DellPrintBridge instance to both a Dell C1765nfw Color MFP and a USB-connected PL70e thermal label printer using their existing Windows drivers.

## Why this project exists

This project started because an older printer still worked perfectly from Windows, but useful Android support had disappeared. The hardware was fine; the missing piece was a modern compatibility layer.

Android's built-in printing system expects driverless network printing using protocols such as IPP with service discovery through mDNS/DNS-SD. Older printers often predate those standards, and manufacturers may stop maintaining their mobile apps long before the hardware itself stops working.

DellPrintBridge fills that gap:

```text
Modern device                     Existing printer
     |                                  ^
     | native IPP                       |
     v                                  | existing vendor driver
DellPrintBridge -> Windows spooler -----+
```

The bridge is intentionally designed around **Windows printer queues**, not one specific Dell model. If Windows can print to a device, DellPrintBridge may be able to provide a modern IPP front end for it.

## What it does

DellPrintBridge runs on a Windows machine that already has one or more printers installed and working. It currently:

- Enumerates printer queues installed in Windows.
- Publishes multiple Windows queues simultaneously as independent IPP printers.
- Provides a web console for adding, editing, enabling, disabling, and removing published printers.
- Gives each published printer its own advertised name, IPP resource path, UUID, mDNS service, and capability profile.
- Advertises each enabled printer with **mDNS / DNS-SD** as `_ipp._tcp.local`.
- Hosts the IPP service on **TCP 631**.
- Handles the IPP discovery/query operations required by the tested Android Default Print Service.
- Handles HTTP/1.1 `Expect: 100-continue`, normal `Content-Length` bodies, and chunked transfer encoding.
- Accepts PDF print jobs.
- Renders PDF pages with PyMuPDF/Pillow and sends them through Windows GDI/spooler APIs.
- Leaves final printer-specific communication to the existing Windows vendor driver.
- Includes per-printer profiles for standard Letter/A4 color printers and 4x6 monochrome thermal label printers.
- Refreshes mDNS advertisements automatically when published-printer configuration changes.
- Includes an automatic light/dark web UI that follows the browser/Windows theme.
- Includes a Windows system-tray companion for status and control.
- Includes a tested in-place updater for development/Git installations.
- Includes a one-click Windows installer build/release pipeline.

There is **no DellPrintBridge Android app**. That is intentional. The goal is for published printers to appear directly in Android's normal system print dialog.

## Architecture

```text
                         LOCAL NETWORK

+---------------------------+
| Android phone / tablet    |
| Android Default Print     |
| Service (CUPS / IPP)      |
+-------------+-------------+
              |
              | mDNS discovery - UDP 5353
              | IPP / HTTP - TCP 631
              v
+-------------+-------------+
| Windows PC / Server       |
| DellPrintBridge backend   |
|  - mDNS advertisements    |
|  - IPP server             |
|  - PDF renderer           |
|  - Web console :8631      |
+-------------+-------------+
              |
              | Windows GDI / Print Spooler
        +-----+----------------------+
        |                            |
        v                            v
+-------------------+       +-------------------+
| Dell Windows      |       | Thermal Windows   |
| printer queue     |       | printer queue     |
+---------+---------+       +---------+---------+
          |                           |
          v                           v
+-------------------+       +-------------------+
| Dell C1765nfw     |       | PL70e thermal     |
| network printer   |       | USB printer       |
+-------------------+       +-------------------+

Interactive Windows session
        |
        +--> DellPrintBridge tray companion
             - running/unavailable status
             - open web console
             - start backend
             - update
             - exit backend + tray
```

The Android device does **not** need to understand the physical printer. Android talks to DellPrintBridge, DellPrintBridge talks to the Windows print subsystem, and Windows plus the existing vendor driver talk to the printer.

## Web console

DellPrintBridge includes a local management page on TCP **8631**:

```text
http://localhost:8631
```

The console shows all published printers and allows you to:

- Choose the underlying Windows printer queue.
- Set the advertised printer name shown to Android.
- Choose a capability profile.
- Enable or disable mDNS/IPP publication for a printer.
- Remove a published printer.
- Add additional Windows printer queues.

Each printer receives a unique IPP resource. The original migrated Dell configuration keeps the legacy path:

```text
/ipp/print
```

Additional printers use paths such as:

```text
/ipp/printers/nelko-thermal
```

Configuration changes automatically refresh the mDNS advertisements; a bridge restart is no longer required just to add, edit, enable, disable, or remove a published printer.

### Dark mode

The web console supports both light and dark themes using the browser's `prefers-color-scheme` setting. No separate DellPrintBridge setting is required:

- Windows/browser light theme -> light UI.
- Windows/browser dark theme -> dark UI.

The dark theme covers the page background, cards, form controls, disabled fields, badges, success/warning messages, and other management UI elements.

## Multi-printer support

A single DellPrintBridge instance can now publish multiple Windows queues at the same time. Each appears to Android as a separate printer.

Example tested configuration:

```text
Android
   |
   +--> Dell Print Bridge
   |       IPP: /ipp/print
   |       Profile: Letter / A4 color printer
   |       -> Dell C1765nfw Color MFP-00000
   |
   +--> Nelko Thermal
           IPP: /ipp/printers/nelko-thermal
           Profile: 4 x 6 thermal label printer
           -> PL70e-BT-usb
           -> USB001
```

The PL70e test is important because the physical printer itself does not provide Android IPP printing. It is passed into the Windows print-server VM over USB, installed as a normal Windows queue, and DellPrintBridge supplies the network-facing IPP/mDNS layer.

### Capability profiles

Current built-in profiles are:

**Letter / A4 color printer**

- Letter and A4 media advertisement.
- 300 DPI advertisement.
- Color and monochrome modes.
- One-sided printing.

**4 x 6 thermal label printer**

- 4x6 media advertisement.
- 203 DPI advertisement.
- Monochrome printing.
- One-sided printing.

These profiles control what DellPrintBridge tells Android about the published printer. The actual physical rendering is still performed through the selected Windows queue and driver.

## System tray companion

The tray process runs in the signed-in user's Windows session while the backend runs independently as SYSTEM.

Status:

- **Green:** the DellPrintBridge web endpoint is responding.
- **Gray:** the tray companion is running, but the backend cannot currently be reached.

Tray actions:

- **Double-click / Open DellPrintBridge** opens the local web console.
- **Start DellPrintBridge** starts the backend scheduled task when the bridge is unavailable. The action is disabled while the backend is healthy.
- **Update DellPrintBridge** launches the appropriate updater elevated.
- **Exit DellPrintBridge** stops the backend and closes the tray companion. Installed scheduled tasks remain in place for the next normal startup/logon.

The tray continuously checks backend health and refreshes its icon/menu state automatically. This allows the backend to be recovered after a crash or manual stop without opening Task Scheduler.

The tray process is intentionally separate because Windows isolates SYSTEM tasks/services from the interactive user desktop.

## Current status

### Working today

The current development build has successfully completed both of these end-to-end paths:

```text
Android Default Print Service
        -> DellPrintBridge
        -> Dell Windows queue
        -> Dell C1765nfw
```

and:

```text
Android Default Print Service
        -> DellPrintBridge
        -> PL70e Windows queue
        -> USB
        -> PL70e thermal label printer
```

Current functionality includes:

- Multiple simultaneously published Windows printer queues.
- Web-based add/edit/enable/disable/remove controls.
- Backward-compatible automatic migration of the original single-printer configuration.
- Unique IPP paths and stable UUIDs per published printer.
- Separate mDNS advertisements per printer.
- Automatic mDNS refresh after configuration changes.
- Per-printer capability profiles.
- Letter/A4 color and 4x6 monochrome thermal profiles.
- Native Android Default Print Service discovery and printing.
- Android-compatible IPP capability advertisement.
- `Get-Printer-Attributes`, `Get-Jobs`, `Validate-Job`, and `Print-Job`.
- HTTP `Expect: 100-continue` support.
- `Content-Length` and chunked request-body support.
- PDF input and rendering through the Windows graphics/printing stack.
- Automatic light/dark web UI.
- Rotating diagnostic logs.
- Private-profile Windows Firewall rules.
- Startup scheduled task for the SYSTEM backend.
- Interactive logon scheduled task for the tray companion.
- Tray health/status, start, update, and exit controls.
- In-place Git updater with restart, health check, and rollback handling.
- Safe updater handoff so updates that replace updater components continue with freshly pulled worker code.
- One-click installer build using PyInstaller and Inno Setup.
- GitHub Actions installer builds and release-aware packaged update support.

### Prototype limitations

DellPrintBridge is still early software. It does **not** yet provide:

- A native Windows Service; the backend currently runs as a SYSTEM scheduled task.
- Automatic discovery of every Windows driver capability.
- Full dynamic duplex/tray/media/copy capability translation from the Windows driver.
- DellPrintBridge-side thermal image preprocessing/dithering; thermal image rendering currently depends on the Windows driver.
- PWG Raster input.
- Apple URF/AirPrint as a tested feature.
- Authentication or Internet-facing security.

The current project should be considered a **trusted-LAN prototype**.

## How Android discovers it

DellPrintBridge publishes one `_ipp._tcp.local` service for each enabled printer. Android's Default Print Service discovers those services and queries the corresponding IPP resource on TCP 631.

Two compatibility details were especially important during development:

1. Android sends HTTP `Expect: 100-continue` before a number of IPP POST bodies. Correctly acknowledging that exchange was required for reliable printing.
2. Android was sensitive to the advertised IPP capability set. DellPrintBridge returns printer identity, document-format, media, resolution, color, quality, and job-creation attributes appropriate to the configured profile.

IPP is carried over HTTP, so both layers must behave in a way the client accepts.

## Print-job flow

For a PDF job, the current path is roughly:

```text
1. Android discovers a published DellPrintBridge printer with mDNS.
2. Android queries that printer's capabilities over its unique IPP resource.
3. Android validates the proposed print job.
4. Android sends an IPP Print-Job containing the PDF.
5. DellPrintBridge maps the request path to the configured Windows queue.
6. DellPrintBridge extracts the PDF from the IPP request.
7. PyMuPDF renders each PDF page.
8. Pillow/ImageWin prepares the rendered page for Windows GDI.
9. pywin32 opens the selected Windows printer DC.
10. The page is submitted through the Windows print subsystem.
11. The existing manufacturer driver sends the job to the physical printer.
```

DellPrintBridge is therefore a **protocol and compatibility bridge**, not a replacement printer driver.

## Development setup

### Requirements

- Windows 10/11 or Windows Server.
- Python 3.10 or newer.
- One or more printers already installed in Windows.
- Working Windows drivers for those printers.
- Each queue should successfully print from Windows before troubleshooting DellPrintBridge.
- For initial testing, place the Android device and bridge host on the same LAN/subnet so mDNS discovery is straightforward.

### Clone and install

```powershell
git clone https://github.com/jman9895/dellprintbridge.git
cd dellprintbridge
Set-ExecutionPolicy -Scope Process Bypass
.\setup-dev.ps1
```

The development setup script creates/reuses `.venv`, installs requirements, creates firewall rules, registers the SYSTEM backend startup task, and registers the interactive tray task.

Firewall ports:

| Port | Protocol | Purpose |
| --- | --- | --- |
| 631 | TCP | IPP printing |
| 5353 | UDP | mDNS discovery |
| 8631 | TCP | DellPrintBridge web console |

### Manual run

```powershell
.\.venv\Scripts\python.exe .\dellprintbridge.py
```

### Scheduled tasks

```powershell
Start-ScheduledTask -TaskName "DellPrintBridge"
Start-ScheduledTask -TaskName "DellPrintBridge Tray"
```

## In-place development updater

Git/development installations update with:

```powershell
.\update.ps1
```

The updater verifies the working tree, fetches the upstream branch, performs a fast-forward-only update, and hands execution to a fresh worker process from the newly pulled code. This avoids the previous self-update race where the first update attempt could continue using stale in-memory updater code.

The worker then:

1. Stops DellPrintBridge and the tray.
2. Waits for project Python processes to exit and cleans up leftovers if necessary.
3. Reuses the existing Python virtual environment.
4. Updates dependencies and scheduled-task registration.
5. Restarts the backend and tray.
6. Performs a health check against `http://localhost:8631/`.
7. Attempts rollback if a newly installed update fails.

Diagnostics are written to:

```text
%ProgramData%\DellPrintBridge\update.log
```

The updater has been tested for normal updates, no-change/idempotent runs, tray-initiated updates, updates that replace updater components, and first-attempt success after the staged worker change.

## One-click Windows installer

The target destination experience is:

```text
Download EXE -> double-click -> UAC -> install -> tray appears -> configure printers -> print
```

The destination machine does **not** need Python, Git, pip, a repository clone, or command-line setup.

The build uses:

- **PyInstaller** for self-contained backend and tray application folders.
- **Inno Setup 6** for the Windows setup executable.
- Installer helper scripts for scheduled tasks, firewall configuration, startup, health checking, and cleanup.
- **GitHub Actions** for reproducible Windows builds.

Installed layout:

```text
C:\Program Files\DellPrintBridge\
    backend\
        DellPrintBridge.exe
        ...runtime files...
    tray\
        DellPrintBridgeTray.exe
        ...runtime files...
    installer\
        install-runtime.ps1
        uninstall-runtime.ps1
        update-release.ps1
```

Runtime configuration and logs remain under:

```text
%ProgramData%\DellPrintBridge\
```

### Build locally

Install Inno Setup if necessary:

```powershell
winget install --id JRSoftware.InnoSetup -e
```

Then build:

```powershell
.\build-installer.ps1 -Version 0.1.0
```

Output:

```text
build\installer\DellPrintBridge-Setup-0.1.0.exe
```

### Packaged updates

The tray automatically distinguishes between a development/Git installation and a packaged installation.

- **Development install:** `Update DellPrintBridge` uses the Git updater.
- **Packaged install:** `Update DellPrintBridge` uses the release updater, checks GitHub Releases for a newer version, downloads the matching setup executable, and performs an in-place upgrade.

Version tags such as `v0.1.0` can trigger the GitHub Actions installer workflow and publish the installer as a release asset.

> Public installer distribution will benefit from code signing. Unsigned downloaded executables may produce Windows SmartScreen warnings even when the application itself is safe.

## Configuration

Browse to:

```text
http://localhost:8631
```

Use the web console to publish any Windows queues you want Android to see. For each published printer choose:

- The Windows printer queue.
- The advertised printer name.
- The capability profile.
- Whether the printer is currently advertised.

Configuration is stored in:

```text
%ProgramData%\DellPrintBridge\config.json
```

Legacy configuration files containing the original `printer_name` and `display_name` fields are automatically migrated to the current `printers` list format. The migrated original printer keeps the `/ipp/print` resource so existing Android discovery continues to work.

## Printing from Android

1. Make sure Android's **Default Print Service** is enabled.
2. Connect Android to the same LAN as DellPrintBridge.
3. Open a printable document or PDF.
4. Choose **Print**.
5. Select the desired DellPrintBridge-published printer.
6. Send the job.

Multiple published printers appear as separate choices in the Android print interface.

No manufacturer Android print application is required for the tested paths.

## Thermal-printer notes

The tested PL70e appears in Windows as a normal local printer queue:

```text
Queue:  PL70e-BT-usb
Driver: PL70e-BT
Port:   USB001
```

DellPrintBridge can publish that queue to Android using the **4 x 6 thermal label printer** profile even though the printer itself does not provide native Android IPP support.

At present DellPrintBridge sends rendered page graphics to the Windows driver and relies on that driver for final monochrome conversion, thresholding, halftoning, or dithering. The PL70e driver exposes threshold/dither controls, but driver rendering behavior is still being evaluated. DellPrintBridge-side optional thermal preprocessing/dithering is a possible future enhancement.

Because the backend runs as SYSTEM, printer preferences that are stored per Windows user may not necessarily be the same preferences seen by the backend. If a driver setting affects direct interactive-user printing but not DellPrintBridge jobs, SYSTEM-context printer DEVMODE/preferences are worth investigating.

## Logging and troubleshooting

Main log:

```text
%ProgramData%\DellPrintBridge\dellprintbridge.log
```

Follow it live:

```powershell
Get-Content "$env:ProgramData\DellPrintBridge\dellprintbridge.log" -Wait
```

Verify TCP 631:

```powershell
Get-NetTCPConnection -LocalPort 631 -State Listen
```

Test the Windows print path first:

```powershell
"DellPrintBridge Windows test" | Out-Printer -Name "Your Windows Printer Queue"
```

If Windows cannot print to the queue, fix that before troubleshooting DellPrintBridge.

When troubleshooting multiple printers, the IPP log includes the advertised printer name and request path so jobs can be traced to the correct configured Windows queue.

## Uninstall behavior

The installer cleanup removes the DellPrintBridge scheduled tasks and Windows Firewall rules. Runtime configuration and logs under `%ProgramData%\DellPrintBridge` are intentionally preserved so a reinstall does not automatically discard published-printer configuration or diagnostic history.

## Security

DellPrintBridge currently has **no authentication**. It is intended for a trusted private network while the project is under development. Do not expose TCP 631 or TCP 8631 directly to the public Internet.

Firewall rules created by the setup use the Windows **Private** network profile.

## Project philosophy

A printer shouldn't become e-waste merely because the software ecosystem around it moved on.

There are countless printers, scanners, label printers, and multifunction devices whose hardware remains perfectly serviceable while their mobile applications, cloud services, or driverless-printing support have been abandoned.

DellPrintBridge puts a compatibility layer in front of hardware that already works:

**modern protocol in, proven legacy driver out.**

The Dell C1765nfw was simply the reason to build it. The working PL70e USB thermal-printer test demonstrates why the architecture is intentionally broader than that one printer.

## Roadmap

Near-term work:

- Validate the one-click installer end-to-end on a clean Windows machine/VM.
- Validate packaged in-place upgrades and uninstall/reinstall behavior.
- Continue improving per-printer capabilities and management UI.
- Investigate optional DellPrintBridge-side thermal dithering/image preprocessing if Windows thermal drivers do not provide reliable results.
- Investigate SYSTEM-context printer preferences/DEVMODE handling where driver settings are user-specific.
- Publish signed installer builds when practical.

Longer-term possibilities:

- Run the backend as a native Windows Service.
- Read more capabilities dynamically from Windows printer drivers.
- Improve IPP job state/status reporting.
- Add additional document formats where useful.
- Explore PWG Raster support.
- Explore/test AirPrint/URF compatibility.

## About

DellPrintBridge was created by **Josh Nichols** as a practical solution to a reliable old printer that Windows could still drive but modern Android could no longer use natively.

The project began with the Dell C1765nfw Color MFP and has since successfully bridged a second, very different device: a USB-connected PL70e 4x6 thermal label printer. The broader goal is to extend the useful life of printers that still have a functional Windows print path, regardless of whether the physical printer itself knows anything about modern mobile printing.

Contributions, testing against other printers, protocol improvements, and compatibility reports are welcome.

---

**Keep good hardware working.**