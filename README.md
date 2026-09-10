# DellPrintBridge

**Give a perfectly good old printer a second life by turning its working Windows driver into a modern, driverless IPP print bridge.**

DellPrintBridge exposes an existing Windows printer queue to phones and other modern clients as a network IPP printer. Android can discover the bridge through its built-in **Default Print Service** and send a print job without installing the printer manufacturer's discontinued or proprietary mobile app.

> **Confirmed working:** native Android printing through DellPrintBridge to a Dell C1765nfw Color MFP using the existing Dell Windows driver.

## Why this project exists

This project started because I had an older printer that still worked perfectly from Windows, but its useful mobile software support had disappeared. The hardware was fine; the compatibility layer around it was not.

Android's built-in printing system expects a modern driverless network printer using protocols such as IPP and service discovery through mDNS/DNS-SD. Older printers often predate those standards, and manufacturers may no longer maintain their Android apps or current mobile drivers.

DellPrintBridge fills that gap:

```text
Modern device                  Legacy-but-working printer
     |                                  ^
     | native IPP                       |
     v                                  | existing vendor driver
DellPrintBridge -> Windows spooler -----+
```

Instead of teaching Android how to use an old vendor driver, DellPrintBridge lets Android speak a modern protocol and lets Windows do what it already does well: drive the printer.

Although the first real-world target is a Dell C1765nfw Color MFP, the bridge is intentionally designed around **Windows printer queues**, not a particular Dell model. If Windows can print to a device, DellPrintBridge may be able to provide a modern IPP front end for it.

## What it does

DellPrintBridge runs on a Windows machine that already has the printer installed and working. It currently:

- Enumerates printers installed in Windows.
- Provides a small web console for choosing which Windows queue to publish.
- Advertises the selected printer with **mDNS / DNS-SD** as `_ipp._tcp.local`.
- Hosts an **IPP endpoint on TCP 631**.
- Handles the IPP discovery/query operations required by the tested Android Default Print Service.
- Handles HTTP/1.1 `Expect: 100-continue`, normal `Content-Length` bodies, and chunked transfer encoding.
- Accepts PDF print jobs.
- Renders PDF pages with PyMuPDF/Pillow.
- Sends rendered pages through the selected Windows queue using `pywin32` and Windows GDI/spooler APIs.
- Leaves final printer-specific communication to the existing Windows vendor driver.
- Includes a Windows system-tray companion for status and control.
- Includes a tested in-place updater for development/Git installations.
- Includes a one-click Windows installer build/release pipeline.

There is **no DellPrintBridge Android app**. That is intentional. The goal is for the printer to appear in Android's normal system print dialog.

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
|  - mDNS advertisement     |
|  - IPP server             |
|  - PDF renderer           |
|  - Web console :8631      |
+-------------+-------------+
              |
              | Windows GDI / Print Spooler
              v
+-------------+-------------+
| Installed Windows queue   |
+-------------+-------------+
              |
              | Existing manufacturer driver
              v
+-------------+-------------+
| Physical printer          |
+---------------------------+

Interactive Windows session
        |
        +--> DellPrintBridge tray companion
             - running/unavailable status
             - open web console
             - start backend
             - update
             - exit backend + tray
```

The Android device does **not** need to understand the physical printer. Android talks to DellPrintBridge, DellPrintBridge talks to the Windows print subsystem, and Windows plus the existing driver talk to the printer.

## Web console

DellPrintBridge includes a lightweight local configuration page on TCP **8631**:

```text
http://localhost:8631
```

The console currently lets you choose an installed Windows printer queue and set the name DellPrintBridge advertises to network clients.

The original test configuration used:

```text
Windows queue:   Dell C1765nfw Color MFP-00000
Advertised name: Dell Print Bridge
```

The web UI is deliberately simple right now. Multi-printer management is planned; see the roadmap below.

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

The tray continuously checks backend health and refreshes its icon/menu state automatically. This allows the backend to be recovered from the tray after a crash or manual stop without opening Task Scheduler.

The tray process is intentionally separate because Windows isolates SYSTEM tasks/services from the interactive user desktop.

## Current status

### Working today

The prototype has successfully completed the intended end-to-end path:

```text
Android Default Print Service
        -> IPP
        -> DellPrintBridge
        -> Windows printer queue
        -> existing Windows driver
        -> physical printer
```

Current functionality includes:

- Windows printer queue enumeration.
- Web-based queue selection.
- Configurable advertised printer name.
- mDNS/DNS-SD discovery.
- IPP over TCP 631.
- Android-compatible IPP capability advertisement.
- `Get-Printer-Attributes`, `Get-Jobs`, `Validate-Job`, and `Print-Job`.
- HTTP `Expect: 100-continue` support.
- `Content-Length` and chunked request-body support.
- PDF input and rendering through the Windows graphics/printing stack.
- Rotating diagnostic logs.
- Private-profile Windows Firewall rules.
- Startup scheduled task for the SYSTEM backend.
- Interactive logon scheduled task for the tray companion.
- Tray health/status, start, update, and exit controls.
- In-place Git updater with restart, health check, and rollback handling.
- Safe updater handoff so an update that replaces the updater itself continues using the newly pulled worker code.
- One-click installer build using PyInstaller and Inno Setup.
- GitHub Actions installer builds and release-aware packaged update support.

### Prototype limitations

This is still early software. It does **not** yet provide:

- A native Windows Service; the backend currently runs as a SYSTEM scheduled task.
- Automatic mDNS refresh after changing configuration; restart the bridge after changing the published queue/name.
- Automatic discovery of every Windows driver capability.
- Full dynamic color/duplex/tray/media capability translation.
- PWG Raster input.
- Apple URF/AirPrint as a tested feature.
- Multiple simultaneously published Windows queues yet.
- Authentication or Internet-facing security.

The current project should be considered a **trusted-LAN prototype**.

## How Android discovers it

DellPrintBridge publishes an `_ipp._tcp.local` service through mDNS/DNS-SD. Android's Default Print Service discovers the service and queries the bridge over IPP.

During development, two compatibility details were especially important:

1. Android sends HTTP `Expect: 100-continue` before a number of IPP POST bodies. Correctly acknowledging that exchange was required for reliable printing.
2. Android was sensitive to the advertised IPP capability set. DellPrintBridge now returns a broader set of printer identity, document-format, media, resolution, color, quality, and job-creation attributes.

IPP is carried over HTTP, so both layers must behave in a way the client accepts.

## Print-job flow

For a PDF job, the current path is roughly:

```text
1. Android discovers DellPrintBridge with mDNS.
2. Android queries printer capabilities over IPP.
3. Android validates the proposed print job.
4. Android sends an IPP Print-Job containing the PDF.
5. DellPrintBridge extracts the PDF from the IPP request.
6. PyMuPDF renders each PDF page.
7. Pillow/ImageWin prepares the rendered page for Windows GDI.
8. pywin32 opens the selected Windows printer DC.
9. The page is submitted through the Windows print subsystem.
10. The existing manufacturer driver sends the job to the physical printer.
```

DellPrintBridge is therefore a **protocol and compatibility bridge**, not a replacement printer driver.

## Development setup

### Requirements

- Windows 10/11 or Windows Server.
- Python 3.10 or newer.
- A printer already installed in Windows.
- A working Windows driver for that printer.
- The printer should successfully print from Windows before troubleshooting DellPrintBridge.
- For initial testing, put the Android device and bridge host on the same LAN/subnet so mDNS discovery is straightforward.

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

The updater verifies the working tree, fetches the upstream branch, performs a fast-forward-only update, and then hands execution to a fresh worker process from the newly pulled code. This handoff is important when an update changes the updater itself: PowerShell no longer continues the remainder of the update using a stale in-memory copy of the old updater.

The worker then:

1. Stops DellPrintBridge and the tray.
2. Reuses the existing Python virtual environment.
3. Updates dependencies and task registration.
4. Restarts the backend and tray.
5. Performs a health check against `http://localhost:8631/`.
6. Attempts rollback if a newly installed update fails.

Diagnostics are written to:

```text
%ProgramData%\DellPrintBridge\update.log
```

The updater has been tested for normal updates, no-change/idempotent runs, tray-initiated updates, and updates that replace updater components.

## One-click Windows installer

The target destination experience is:

```text
Download EXE -> double-click -> UAC -> install -> tray appears -> configure printer -> print
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

Select a working Windows printer queue and choose the name that should appear on Android.

Configuration is stored in:

```text
%ProgramData%\DellPrintBridge\config.json
```

During the current prototype stage, restart DellPrintBridge after changing the selected printer or advertised name so the mDNS advertisement is recreated.

## Printing from Android

1. Make sure Android's **Default Print Service** is enabled.
2. Connect Android to the same LAN as DellPrintBridge.
3. Open a printable document or PDF.
4. Choose **Print**.
5. Select the printer advertised by DellPrintBridge.
6. Send the job.

No manufacturer print application is necessary for the tested path.

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

## Uninstall behavior

The installer cleanup removes the DellPrintBridge scheduled tasks and Windows Firewall rules. Runtime configuration and logs under `%ProgramData%\DellPrintBridge` are intentionally preserved so a reinstall does not automatically discard the selected printer or diagnostic history.

## Security

DellPrintBridge currently has **no authentication**. It is intended for a trusted private network while the project is under development. Do not expose TCP 631 or TCP 8631 directly to the public Internet.

Firewall rules created by the setup use the Windows **Private** network profile.

## Multi-printer direction

The current implementation publishes one selected Windows queue, but the architecture is intentionally moving toward **multiple simultaneously advertised printers**.

The planned model is one DellPrintBridge instance with multiple configured Windows queues, each exposed as an independent IPP printer and mDNS advertisement. For example:

```text
Android
   |
   +--> Dell Print Bridge
   |       -> Dell Windows queue
   |       -> Dell C1765nfw
   |
   +--> Thermal Label Printer
           -> Windows thermal-printer queue
           -> USB/Bluetooth-connected label printer
```

A USB printer does not need to understand IPP itself. As long as Windows has a working queue/driver, DellPrintBridge can potentially provide the network-facing IPP layer.

Planned multi-printer work includes:

- A list of advertised printers in the web console instead of a single queue selector.
- Add/edit/disable/remove controls for published queues.
- A unique IPP resource path for each printer, such as `/printers/dell` and `/printers/thermal`.
- A separate mDNS advertisement for each published printer.
- Per-printer capabilities rather than one global capability set.
- Media profiles such as Letter/A4 for office printers and 4x6 for thermal label printers.
- Per-printer color/monochrome and resolution advertisement.
- Automatic migration of the existing single-printer configuration into the new multi-printer configuration format.

A thermal label printer connected to the Windows bridge by USB is planned as the first multi-printer/capability-profile test case.

## Project philosophy

A printer shouldn't become e-waste merely because the software ecosystem around it moved on.

There are countless printers, scanners, label printers, and multifunction devices whose hardware remains perfectly serviceable while their mobile applications, cloud services, or driverless-printing support have been abandoned.

DellPrintBridge puts a compatibility layer in front of hardware that already works:

**modern protocol in, proven legacy driver out.**

The Dell C1765nfw was simply the reason to build it. The architecture is intentionally broader than that one printer.

## Roadmap

Near-term work:

- Validate the one-click installer end-to-end on a clean Windows machine/VM.
- Validate packaged in-place upgrades and uninstall/reinstall behavior.
- Add multi-printer publishing and per-printer IPP/mDNS identities.
- Test a USB-connected 4x6 thermal label printer as the second published printer.
- Add dynamic/per-printer media, color, resolution, duplex, tray, and copy capabilities.
- Improve the web management console for multiple printers.
- Dynamically refresh mDNS advertisements after configuration changes.
- Publish signed installer builds when practical.

Longer-term possibilities:

- Run the backend as a native Windows Service.
- Improve IPP job state/status reporting.
- Add additional document formats where useful.
- Explore PWG Raster support.
- Explore/test AirPrint/URF compatibility.

## About

DellPrintBridge was created by **Josh Nichols** as a practical solution to a reliable old printer that Windows could still drive but modern Android could no longer use natively.

The project began with the Dell C1765nfw Color MFP but is being developed with a broader goal: extend the useful life of printers that still have a functional Windows print path.

Contributions, testing against other printers, protocol improvements, and compatibility reports are welcome.

---

**Keep good hardware working.**