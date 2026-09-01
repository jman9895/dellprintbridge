# DellPrintBridge

DellPrintBridge exposes an existing Windows printer queue as a driverless IPP printer for phones and other modern clients.

The immediate goal is to restore native Android printing to an older Dell multifunction printer whose Windows driver still works. The bridge itself is intentionally printer-agnostic: the target is any printer queue already installed and working in Windows.

## Current status

**v0.1 development prototype**

Current scope:

- Enumerates local/connected Windows printer queues.
- Web UI for selecting the target Windows queue.
- Advertises the bridge with mDNS / DNS-SD as `_ipp._tcp.local`.
- Implements a minimal IPP endpoint on TCP 631.
- Accepts `application/pdf` jobs.
- Renders PDFs with PyMuPDF and sends them through the selected Windows printer driver with `pywin32`.
- Uses Android's native/default print service; no Dell or DellPrintBridge Android app is intended.

Not implemented yet:

- Windows Service installation.
- Packaged EXE / installer.
- Dynamic mDNS refresh after changing printer settings (restart the bridge after changing the selected queue for now).
- PWG Raster / URF input.
- Automatic capability detection for color, duplex, trays, and media.
- Multiple simultaneously published printer queues.
- Authentication / Internet-facing operation. This project is intended for trusted LAN use.

## Architecture

```text
Android Default Print Service
          |
          | IPP / TCP 631
          v
   DellPrintBridge
          |
          | Windows GDI / Spooler
          v
Existing Windows printer queue
          |
          v
   Existing vendor driver
          |
          v
       Printer
```

## Development setup

### Requirements

- Windows 10/11 or Windows Server with a working printer queue.
- Python 3.10 or newer.
- The target printer must successfully print a Windows test page before testing the bridge.
- Phone and server should initially be on the same subnet so mDNS discovery can work.

### Install

Open an elevated PowerShell window in the repository directory and run:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\setup-dev.ps1
```

The setup script creates a virtual environment, installs the Python dependencies, and creates Windows Firewall rules for:

- TCP 631 - IPP
- UDP 5353 - mDNS
- TCP 8631 - configuration web UI

### Run

```powershell
.\.venv\Scripts\python.exe .\dellprintbridge.py
```

Open the management page on the server:

```text
http://localhost:8631
```

Select the Dell Windows printer queue and set the advertised name, for example:

```text
Dell MFP
```

Restart DellPrintBridge after changing the selected printer during this prototype stage.

## Android test

On Android:

1. Make sure **Default Print Service** is enabled under Android's Printing settings.
2. Make sure the phone is on the same LAN/subnet as the Windows server for the first test.
3. Open a PDF or another app that can print.
4. Choose **Print**.
5. Look for the advertised DellPrintBridge printer name.
6. Send a one-page test document.

Expected path:

```text
Android -> native IPP -> DellPrintBridge -> selected Windows queue -> Dell driver -> printer
```

## Configuration and logs

DellPrintBridge stores its runtime files under:

```text
%ProgramData%\DellPrintBridge\
```

Files:

```text
config.json
dellprintbridge.log
```

## First validation target

For the initial Dell test, success means:

- Android discovers the bridge without installing a third-party print app.
- The Android native print dialog can select the bridge.
- A one-page PDF reaches the selected Dell Windows queue.
- The existing Dell Windows driver successfully prints the page.

Once this works reliably, the next milestone is to package DellPrintBridge as a self-contained Windows Service and then expand printer capability detection and broader printer support.

## Security note

The current prototype intentionally has no authentication and should only be run on a trusted private LAN. Do not expose TCP 631 or TCP 8631 directly to the Internet.
