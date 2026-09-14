"""DellPrintBridge runtime compatibility hook for Windows printer preferences.

The backend normally runs as SYSTEM. Some Windows printer drivers keep important
rendering options (for example thermal-printer dithering) in the interactive
user's per-user DEVMODE. A printer DC created directly as SYSTEM therefore may
not use the same settings that work when printing from an application such as
Paint.

Python imports ``sitecustomize`` automatically during normal startup when this
file is on ``sys.path``. DellPrintBridge runs from the repository directory, so
this hook can wrap ``win32ui.CreateDC`` without changing the core IPP/printing
code while the behavior is validated.

Only ``CreatePrinterDC`` is altered. All other DC methods are forwarded to the
real PyCDC object unchanged.
"""

import logging

try:
    import win32security
    import win32ts
    import win32ui
except ImportError:
    # Keep Python startup usable on non-Windows/build environments.
    win32security = None
    win32ts = None
    win32ui = None


if win32ui is not None:
    _original_create_dc = win32ui.CreateDC

    class _UserPreferenceDC:
        def __init__(self, dc):
            object.__setattr__(self, "_dc", dc)

        def __getattr__(self, name):
            return getattr(self._dc, name)

        def __setattr__(self, name, value):
            if name == "_dc":
                object.__setattr__(self, name, value)
            else:
                setattr(self._dc, name, value)

        def CreatePrinterDC(self, printer_name):
            """Create the printer DC while impersonating the active user.

            This makes Windows resolve that user's per-user printer DEVMODE,
            including driver-private settings such as thermal dithering. The
            resulting HDC remains valid after reverting back to SYSTEM.
            """
            log = logging.getLogger("DellPrintBridge")
            token = None

            try:
                sessions = win32ts.WTSEnumerateSessions(
                    win32ts.WTS_CURRENT_SERVER_HANDLE, 1, 0
                )
                active_state = getattr(win32ts, "WTSActive", 0)
                active_sessions = [
                    session
                    for session in sessions
                    if session.get("State") == active_state
                    and session.get("SessionId", 0) != 0
                ]

                if not active_sessions:
                    log.warning(
                        "No active interactive Windows session found; creating printer DC in current security context for %r",
                        printer_name,
                    )
                    return self._dc.CreatePrinterDC(printer_name)

                # Prefer the console session when it is active; otherwise use
                # the first active interactive session (for example RDP).
                console_session_id = win32ts.WTSGetActiveConsoleSessionId()
                session = next(
                    (
                        item
                        for item in active_sessions
                        if item.get("SessionId") == console_session_id
                    ),
                    active_sessions[0],
                )
                session_id = session["SessionId"]

                token = win32ts.WTSQueryUserToken(session_id)
                win32security.ImpersonateLoggedOnUser(token)
                try:
                    result = self._dc.CreatePrinterDC(printer_name)
                finally:
                    win32security.RevertToSelf()

                log.info(
                    "Printer DC created using active-user preferences: printer=%r session_id=%s station=%r",
                    printer_name,
                    session_id,
                    session.get("WinStationName"),
                )
                return result

            except Exception:
                # Never make printing dependent on impersonation. If Windows
                # refuses WTS token access or there is no usable session, fall
                # back to the original behavior so standard printers continue
                # to work exactly as before.
                log.exception(
                    "Unable to create printer DC using active-user preferences for %r; falling back to current security context",
                    printer_name,
                )
                return self._dc.CreatePrinterDC(printer_name)
            finally:
                if token is not None:
                    try:
                        token.Close()
                    except Exception:
                        pass

    def _create_dc_with_user_preferences(*args, **kwargs):
        return _UserPreferenceDC(_original_create_dc(*args, **kwargs))

    win32ui.CreateDC = _create_dc_with_user_preferences
