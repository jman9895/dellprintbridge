# DellPrintBridge

**Give a perfectly good old printer a second life by turning its working Windows driver into a modern, driverless IPP print bridge.**

DellPrintBridge exposes an existing Windows printer queue to phones and other modern clients as a network IPP printer. Android can discover the bridge through its built-in **Default Print Service** and send a print job without installing the printer manufacturer's discontinued mobile app.

> **Confirmed working:** native Android printing through DellPrintBridge to a Dell C1765nfw Color MFP using the existing Dell Windows driver.

## Why this project exists

This project started because I had an older printer that had worked flawlessly for roughly 20 years and I didn't want to throw it away simply because mobile software support had disappeared.

The printer itself still worked. Windows could still print to it perfectly using the existing vendor driver. The hardware wasn't broken; the missing piece was modern client support.

Android's built-in printing system expects a modern driverless network printer using protocols such as IPP and service discovery via mDNS/DNS-SD. Older printers often predate those standards, and their manufacturers may no longer maintain Android apps or current mobile drivers.

Replacing working hardware because an app disappeared seemed wasteful.

DellPrintBridge solves that compatibility gap:

```text
Modern device                  Legacy-but-working printer
     |                                  ^
     | native IPP                       |
     v                                  | existing vendor driver
DellPrintBridge -> Windows spooler -----+
```

Instead of trying to teach Android how to use an old Dell driver, DellPrintBridge lets Android speak a modern protocol and lets Windows do what it already does well: drive the printer.

Although the first real-world target is a Dell C1765nfw Color MFP, the bridge is intentionally designed around **Windows printer queues**, not a particular Dell model. The long-term idea is that if Windows can print to a device, DellPrintBridge may be able to provide a modern IPP front end for it.

## What it does

DellPrintBridge runs on a Windows machine that already has the printer installed and working. It:

- Enumerates printers already installed in Windows.
- Provides a small web console for choosing which Windows queue to publish.
- Advertises the selected printer on the LAN with **mDNS / DNS-SD** as `_ipp._tcp.local`.
- Hosts an **IPP endpoint on TCP 631**.
- Handles the IPP discovery/query operations used by Android's CUPS-based Default Print Service.
- Handles HTTP/1.1 `Expect: 100-continue`, normal `Content-Length` request bodies, and chunked transfer encoding.
- Accepts PDF print jobs.
- Renders PDF pages with PyMuPDF/Pillow.
- Sends rendered pages through the selected Windows queue using `pywin32` and the normal Windows GDI/spooler path.
- Leaves the final printer-specific communication to the existing Windows vendor driver.
- Includes a Windows system-tray companion for status and control.
- Includes an in-place updater for development/Git installations.

There is **no DellPrintBridge Android app**. That is intentional. The goal is for the printer to appear in Android's normal system print dialog.

## Architecture

```text
                    LOCAL NETWORK

+---------------------------+
| Android phone / tablet    |
|                           |
| Android Default Print     |
| Service (CUPS / IPP)      |
+-------------+-------------+
              |
              | mDNS discovery
              | UDP 5353
              |
              | IPP / HTTP
              | TCP 631
              v
+-------------+-------------+
| Windows PC / Server       |
|                           |
| DellPrintBridge backend   |
|  - mDNS advertisement     |
|  - IPP server             |
|  - PDF renderer           |
|  - Web console :8631      |
+-------------+-------------+
              |
              | Windows GDI
              | Windows Print Spooler
              v
+-------------+-------------+
| Installed Windows queue   |
+-------------+-------------+
              |
              | Existing manufacturer
              | Windows driver
              v
+-------------+-------------+
| Physical printer          |
|                           |
| Dell C1765nfw in the      |
| original test environment |
+---------------------------+

Interactive Windows session
        |
        +--> DellPrintBridge tray companion
             - running/unavailable status
             - open web console
             - update
             - exit backend + tray
```

The important distinction is that the Android device does **not** need to understand the physical printer.

Android talks to DellPrintBridge. DellPrintBridge talks to the Windows print subsystem. Windows and the existing driver talk to the printer.

That separation is what makes the concept useful for older hardware.

## Web console

DellPrintBridge includes a lightweight local configuration page on TCP **8631**.

Open it on the Windows host:

```text
http://localhost:8631
```

The console lets you choose an installed Windows printer queue and set the name that DellPrintBridge advertises to network clients.

The original test configuration used:

```text
Windows queue:   Dell C1765nfw Color MFP-00000
Advertised name: Dell Print Bridge
```

> A screenshot of the web console will be added here once the repository copy of the image is available.

The web UI is deliberately simple right now. It is a configuration surface, not a replacement for the printer's own management interface.

## System tray companion

DellPrintBridge includes a lightweight tray process that runs in the signed-in user's Windows session while the backend continues to run independently as SYSTEM.

The icon provides a quick visual status indication:

- **Green:** the DellPrintBridge web endpoint is responding.
- **Gray:** the tray companion is running, but the backend cannot currently be reached.

Tray actions:

- **Double-click / Open DellPrintBridge** opens the local web console.
- **Update DellPrintBridge** launches the updater elevated.
- **Exit DellPrintBridge** stops the backend and closes the tray companion.

The tray process is intentionally separate from the backend because Windows isolates SYSTEM tasks/services from the interactive user desktop.

## Current status

### Working today

The development prototype has successfully completed the intended end-to-end path:

```text
Android Default Print Service
        -> IPP
        -> DellPrintBridge
        -> Windows printer queue
        -> existing Dell Windows driver
        -> physical printer
```

The successful Android test required no Dell Android application and no DellPrintBridge client application.

Current functionality includes:

- Windows printer queue enumeration.
- Web-based queue selection.
- Configurable advertised printer name.
- mDNS/DNS-SD discovery.
- IPP over TCP 631.
- IPP 1.1/2.0 responses for the operations currently required by the tested Android client.
- Expanded Android-compatible printer capability advertisement.
- `Get-Printer-Attributes`.
- `Get-Jobs`.
- `Validate-Job`.
- `Print-Job`.
- HTTP `Expect: 100-continue` support.
- `Content-Length` request bodies.
- HTTP chunked request-body support.
- PDF input.
- PDF rendering through the Windows graphics/printing stack.
- Rotating diagnostic logs.
- Windows Firewall rule creation from the development setup script.
- Startup scheduled-task installation for the backend.
- Interactive logon scheduled task for the tray companion.
- System-tray status and control.
- In-place Git updater with restart, health check, and rollback handling.

### Prototype limitations

This is still early software. It does **not** yet provide:

- A native Windows Service.
- Automatic mDNS refresh after changing configuration; restart the bridge after changing the published queue/name.
- Automatic discovery of every Windows driver capability.
- Full dynamic color/duplex/tray/media capability translation.
- PWG Raster input.
- Apple URF/AirPrint support as a tested feature.
- Multiple simultaneously published Windows queues.
- Authentication or Internet-facing security.

A packaged Windows installer build pipeline is now included in the repository, but it is still being validated before being treated as the primary installation method.

The current project should be considered a **trusted-LAN prototype**.

## How Android discovers it

DellPrintBridge uses mDNS/DNS-SD to publish an `_ipp._tcp.local` service. The advertisement tells compatible devices that an IPP printer exists and provides the resource path used by the bridge:

```text
ipp/print
```

Android's Default Print Service can then query the bridge using IPP.

During development, the Android client identified itself as a CUPS-based IPP/2.0 client. A key compatibility issue turned out to be HTTP `Expect: 100-continue`: Android sends this before a number of IPP POST bodies. Correctly acknowledging that HTTP exchange was necessary for the client to proceed reliably to the actual print job.

Android also proved sensitive to the advertised IPP capability set. DellPrintBridge now returns a broader set of printer identity, document-format, media, resolution, color, quality, and job-creation attributes so the Android client can accept and present the printer consistently.

This is why DellPrintBridge contains HTTP handling in addition to IPP parsing: **IPP is carried over HTTP**, so both layers have to behave in a way the client accepts.

## Print-job flow

For a PDF job, the current path is roughly:

```text
1. Android discovers DellPrintBridge with mDNS.
2. Android queries printer capabilities over IPP.
3. Android validates the proposed print job.
4. Android sends an IPP Print-Job containing the PDF.
5. DellPrintBridge extracts the PDF document from the IPP request.
6. PyMuPDF renders each PDF page.
7. Pillow/ImageWin prepares the rendered page for Windows GDI.
8. pywin32 opens the selected Windows printer DC.
9. The page is submitted through the Windows print subsystem.
10. The existing manufacturer driver sends the job to the physical printer.
```

DellPrintBridge therefore acts as a **protocol and compatibility bridge**, not a replacement printer driver.

## Development setup

### Requirements

- Windows 10/11 or Windows Server.
- Python 3.10 or newer.
- A printer already installed in Windows.
- A working Windows driver for that printer.
- The printer should successfully print from Windows before troubleshooting DellPrintBridge.
- For initial testing, put the Android device and bridge host on the same LAN/subnet so mDNS discovery is straightforward.

### Clone

```powershell
git clone https://github.com/jman9895/dellprintbridge.git
cd dellprintbridge
```

### Install

Open an **elevated PowerShell** window in the repository directory:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\setup-dev.ps1
```

The development setup script:

1. Finds an installed Python 3 runtime.
2. Creates a local `.venv` virtual environment when one does not already exist.
3. Reuses the existing `.venv` on later runs.
4. Installs/updates the Python requirements.
5. Creates private-profile Windows Firewall rules for DellPrintBridge.
6. Registers a startup scheduled task named `DellPrintBridge` running as SYSTEM.
7. Registers a `DellPrintBridge Tray` task for the signed-in user.

Firewall ports:

| Port | Protocol | Purpose |
| --- | --- | --- |
| 631 | TCP | IPP printing |
| 5353 | UDP | mDNS discovery |
| 8631 | TCP | DellPrintBridge web console |

### Run manually

Manual foreground operation is useful during development because log output remains visible:

```powershell
.\.venv\Scripts\python.exe .\dellprintbridge.py
```

### Start the scheduled tasks

```powershell
Start-ScheduledTask -TaskName "DellPrintBridge"
Start-ScheduledTask -TaskName "DellPrintBridge Tray"
```

Check the backend with:

```powershell
Get-ScheduledTask -TaskName "DellPrintBridge" | Get-ScheduledTaskInfo
```

## In-place updater

Git/development installations can update in place with:

```powershell
.\update.ps1
```

The updater:

1. Verifies the Git working tree is safe to update.
2. Fetches the latest code from the configured upstream branch.
3. Stops the backend and tray tasks.
4. Performs a fast-forward-only Git update when a newer commit exists.
5. Reuses the existing Python virtual environment.
6. Updates dependencies and scheduled-task registration.
7. Restarts the backend and tray.
8. Performs a health check against `http://localhost:8631/`.
9. Attempts to roll back to the previous commit if an update fails after pulling new code.

Updater diagnostics are written to:

```text
%ProgramData%\DellPrintBridge\update.log
```

The updater is safe to run when the code is already current; it will still verify dependencies, tasks, restart behavior, and the health check.

## One-click Windows installer

The repository now contains the first installer build workflow. The target experience is a normal Windows setup executable that requires **no Python installation, no Git installation, and no command-line setup** on the destination machine.

The installer build uses:

- **PyInstaller** to create self-contained Windows application folders for the backend and tray companion.
- **Inno Setup 6** to package those files into a standard Windows installer.
- Installer helper scripts to register the scheduled tasks and Windows Firewall rules.
- A GitHub Actions workflow for reproducible Windows builds.

The planned installed layout is:

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
```

Runtime configuration and logs remain under:

```text
%ProgramData%\DellPrintBridge\
```

### Build the installer locally

On a Windows development machine with Python 3 and Inno Setup 6 installed:

```powershell
.\build-installer.ps1 -Version 0.1.0
```

If Inno Setup is not installed:

```powershell
winget install --id JRSoftware.InnoSetup -e
```

A successful build produces:

```text
build\installer\DellPrintBridge-Setup-0.1.0.exe
```

### GitHub Actions build

The workflow at:

```text
.github/workflows/build-installer.yml
```

can be run manually with a version number or triggered by a `v*` tag. It builds on a Windows runner and uploads the resulting setup executable as a workflow artifact.

The installer path is currently under active validation. Until that validation is complete, the development setup remains the known-good installation method.

## Configuration

Browse to:

```text
http://localhost:8631
```

Select a working Windows printer queue and choose the name that should appear on Android.

During the prototype stage, restart DellPrintBridge after changing the selected printer or advertised name so the mDNS advertisement is recreated.

Configuration is stored in:

```text
%ProgramData%\DellPrintBridge\config.json
```

## Printing from Android

1. Make sure Android's **Default Print Service** is enabled.
2. Connect the Android device to the same LAN as the DellPrintBridge host.
3. Open a printable document or PDF.
4. Choose **Print** from the Android application.
5. Select the printer name advertised by DellPrintBridge.
6. Send the job.

No manufacturer print application should be necessary for the tested path.

## Logging and troubleshooting

Logs are stored at:

```text
%ProgramData%\DellPrintBridge\dellprintbridge.log
```

Follow the log live with PowerShell:

```powershell
Get-Content "$env:ProgramData\DellPrintBridge\dellprintbridge.log" -Wait
```

The bridge logs useful stages such as:

```text
DellPrintBridge starting
IPP listener started
mDNS advertisement registered
IPP HTTP Expect
IPP HTTP request
IPP HTTP body received
Get-Printer-Attributes
Get-Jobs
Validate-Job
Print-Job received
Print job starting
Rendered page
Print job completed
```

### Verify TCP 631

```powershell
Get-NetTCPConnection -LocalPort 631 -State Listen
```

If another process owns the port:

```powershell
Get-NetTCPConnection -LocalPort 631 -State Listen |
    Select-Object LocalAddress, LocalPort, OwningProcess
```

Then inspect the PID:

```powershell
Get-Process -Id <PID>
```

### Test Windows first

DellPrintBridge depends on the Windows print path. If the existing Windows queue cannot print, fix that before troubleshooting IPP.

For example:

```powershell
"DellPrintBridge Windows test" | Out-Printer -Name "Your Windows Printer Queue"
```

If that succeeds but DellPrintBridge does not, the problem is much more likely to be in discovery, HTTP, IPP, document handling, or the account context running the bridge.

## Runtime files

DellPrintBridge stores runtime data under:

```text
%ProgramData%\DellPrintBridge\
```

Currently:

```text
config.json
dellprintbridge.log
update.log
```

The log uses rotation so normal diagnostics do not grow indefinitely.

## Uninstall behavior

The installer cleanup path removes the DellPrintBridge scheduled tasks and Windows Firewall rules. Runtime configuration and logs under `%ProgramData%\DellPrintBridge` are intentionally preserved so a reinstall does not automatically discard the selected printer or diagnostic history.

## Security

DellPrintBridge currently has **no authentication**.

It is designed for use on a trusted private network while the project is under development. Do not expose TCP 631 or TCP 8631 directly to the public Internet.

The setup scripts create inbound firewall rules only for the Windows **Private** network profile.

## Project philosophy

A printer shouldn't become e-waste merely because the software ecosystem around it moved on.

There are countless printers, scanners, label printers, and multifunction devices whose hardware remains perfectly serviceable while their mobile applications, cloud services, or driverless-printing support have been abandoned.

DellPrintBridge is an experiment in putting a compatibility layer in front of hardware that already works:

**modern protocol in, proven legacy driver out.**

The Dell C1765nfw was simply the reason to build it. The architecture is intentionally broader than that one printer.

## Roadmap

Potential next steps include:

- Validate the one-click installer on a clean Windows machine/VM.
- Publish signed/release installer builds.
- Add release-aware self-update support for packaged installations.
- Install/run the backend as a proper Windows Service.
- Improve the web management console.
- Dynamically refresh mDNS advertisements after configuration changes.
- Read capabilities from the selected Windows printer/driver.
- Advertise accurate color, duplex, media, tray, resolution, and copy capabilities dynamically.
- Improve IPP job state/status reporting.
- Add additional document formats where useful.
- Explore PWG Raster support.
- Explore/test AirPrint compatibility.
- Support multiple published Windows queues.

## About

DellPrintBridge was created by **Josh Nichols** as a practical solution to a very specific annoyance: a reliable old printer still did its job perfectly, Windows could still drive it, but modern Android support had disappeared.

Rather than replace working hardware, the project uses Windows as the compatibility layer between a modern driverless-printing client and the printer's existing vendor driver.

The project began with the Dell C1765nfw Color MFP but is being developed with a more general goal in mind: extend the useful life of older printers that still have a functional Windows print path.

Contributions, testing against other printers, protocol improvements, and compatibility reports are welcome.

---

**Keep good hardware working.**
