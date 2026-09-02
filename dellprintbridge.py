import io
import json
import logging
import os
import socket
import struct
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import RotatingFileHandler

import fitz
import win32con
import win32print
import win32ui
from flask import Flask, redirect, render_template_string, request, url_for
from PIL import Image, ImageWin
from zeroconf import IPVersion, ServiceInfo, Zeroconf

APP_NAME = "DellPrintBridge"
IPP_PORT = 631
WEB_PORT = 8631
APP_DIR = os.path.join(os.environ.get("PROGRAMDATA", os.getcwd()), APP_NAME)
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
LOG_PATH = os.path.join(APP_DIR, "dellprintbridge.log")
APP_START_MONOTONIC = time.monotonic()

os.makedirs(APP_DIR, exist_ok=True)

log = logging.getLogger(APP_NAME)
log.setLevel(logging.INFO)
log.propagate = False

if not log.handlers:
    formatter = logging.Formatter("%(asctime)s %(levelname)-8s %(message)s")

    file_handler = RotatingFileHandler(
        LOG_PATH,
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    log.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    log.addHandler(console_handler)

DEFAULT_CONFIG = {
    "printer_name": "",
    "display_name": "Dell Print Bridge",
}

IPP_OPERATION_NAMES = {
    0x0002: "Print-Job",
    0x0004: "Validate-Job",
    0x000A: "Get-Jobs",
    0x000B: "Get-Printer-Attributes",
}


def load_config():
    cfg = DEFAULT_CONFIG.copy()
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    except FileNotFoundError:
        pass
    except Exception:
        log.exception("Failed to load configuration from %s", CONFIG_PATH)
    return cfg


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    log.info(
        "Configuration saved: printer=%r advertised_name=%r",
        cfg.get("printer_name", ""),
        cfg.get("display_name", ""),
    )


def get_printers():
    flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
    printers = win32print.EnumPrinters(flags, None, 2)
    names = sorted({p["pPrinterName"] for p in printers}, key=str.lower)
    log.debug("Enumerated %d Windows printer queues", len(names))
    return names


def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


def get_instance_uuid(display_name):
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{socket.gethostname()}:{display_name}"))


def print_pdf(pdf_bytes, printer_name):
    if not printer_name:
        raise RuntimeError("No Windows printer queue is selected")

    started = time.monotonic()
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page_count = len(doc)
    log.info(
        "Print job starting: printer=%r bytes=%d pages=%d",
        printer_name,
        len(pdf_bytes),
        page_count,
    )

    dc = win32ui.CreateDC()
    try:
        dc.CreatePrinterDC(printer_name)
        printable_w = dc.GetDeviceCaps(win32con.HORZRES)
        printable_h = dc.GetDeviceCaps(win32con.VERTRES)

        dc.StartDoc("DellPrintBridge job")
        try:
            for page_number, page in enumerate(doc, start=1):
                dc.StartPage()
                rect = page.rect
                zoom = min(printable_w / rect.width, printable_h / rect.height)
                matrix = fitz.Matrix(zoom, zoom)
                pix = page.get_pixmap(matrix=matrix, alpha=False)
                image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)

                left = max(0, (printable_w - pix.width) // 2)
                top = max(0, (printable_h - pix.height) // 2)
                dib = ImageWin.Dib(image)
                dib.draw(dc.GetHandleOutput(), (left, top, left + pix.width, top + pix.height))
                dc.EndPage()
                log.info("Rendered page %d/%d", page_number, page_count)
        finally:
            dc.EndDoc()
    finally:
        dc.DeleteDC()
        doc.close()

    elapsed = time.monotonic() - started
    log.info(
        "Print job completed: printer=%r pages=%d elapsed=%.2fs",
        printer_name,
        page_count,
        elapsed,
    )


def ipp_attr(tag, name, value):
    if isinstance(value, str):
        value = value.encode("utf-8")
    return bytes([tag]) + struct.pack(">H", len(name)) + name.encode("utf-8") + struct.pack(">H", len(value)) + value


def ipp_int_attr(tag, name, value):
    return bytes([tag]) + struct.pack(">H", len(name)) + name.encode("utf-8") + struct.pack(">H", 4) + struct.pack(">I", value)


def ipp_bool_attr(name, value):
    raw = b"\x01" if value else b"\x00"
    return bytes([0x22]) + struct.pack(">H", len(name)) + name.encode("utf-8") + struct.pack(">H", 1) + raw


def ipp_range_attr(name, lower, upper):
    raw = struct.pack(">ii", lower, upper)
    return bytes([0x33]) + struct.pack(">H", len(name)) + name.encode("utf-8") + struct.pack(">H", len(raw)) + raw


def ipp_resolution_attr(name, x_dpi, y_dpi, units=3):
    # IPP resolution is x-res, y-res, then units. 3 means dots-per-inch.
    raw = struct.pack(">iiB", x_dpi, y_dpi, units)
    return bytes([0x32]) + struct.pack(">H", len(name)) + name.encode("utf-8") + struct.pack(">H", len(raw)) + raw


def parse_ipp_request(body):
    if len(body) < 8:
        raise ValueError(f"IPP request too short ({len(body)} bytes)")
    version = body[0:2]
    op_id = struct.unpack(">H", body[2:4])[0]
    request_id = body[4:8]
    i = 8
    attrs = {}
    last_name = None

    while i < len(body):
        tag = body[i]
        i += 1
        if tag == 0x03:
            return version, op_id, request_id, attrs, body[i:]
        if tag <= 0x0F:
            continue
        if i + 4 > len(body):
            raise ValueError("Malformed IPP attributes")
        name_len = struct.unpack(">H", body[i:i+2])[0]
        i += 2
        if name_len:
            name = body[i:i+name_len].decode("utf-8", errors="replace")
            i += name_len
            last_name = name
        else:
            name = last_name
        value_len = struct.unpack(">H", body[i:i+2])[0]
        i += 2
        value = body[i:i+value_len]
        i += value_len
        if name:
            attrs.setdefault(name, []).append(value)

    raise ValueError("Missing IPP end-of-attributes tag")


def decode_ipp_values(values):
    decoded = []
    for value in values:
        try:
            decoded.append(value.decode("utf-8"))
        except UnicodeDecodeError:
            decoded.append(f"0x{value.hex()}")
    return decoded


def build_ipp_response(version, request_id, include_printer_attrs=False):
    cfg = load_config()
    display = cfg.get("display_name") or "Dell Print Bridge"
    printer = cfg.get("printer_name") or "Unconfigured Windows Printer"
    host = socket.gethostname()
    uri = f"ipp://{host}.local:{IPP_PORT}/ipp/print"
    printer_uuid = get_instance_uuid(display)
    printer_up_time = max(1, int(time.monotonic() - APP_START_MONOTONIC))

    out = bytearray()
    out += version
    out += struct.pack(">H", 0x0000)  # successful-ok
    out += request_id
    out += b"\x01"  # operation-attributes-tag
    out += ipp_attr(0x47, "attributes-charset", "utf-8")
    out += ipp_attr(0x48, "attributes-natural-language", "en-us")

    if include_printer_attrs:
        out += b"\x04"  # printer-attributes-tag

        # Identity and URI information.
        out += ipp_attr(0x45, "printer-uri-supported", uri)
        out += ipp_attr(0x44, "uri-authentication-supported", "none")
        out += ipp_attr(0x44, "uri-security-supported", "none")
        out += ipp_attr(0x42, "printer-name", display)
        out += ipp_attr(0x41, "printer-info", f"Windows queue: {printer}")
        out += ipp_attr(0x41, "printer-location", f"Windows host: {host}")
        out += ipp_attr(0x41, "printer-make-and-model", "Windows Printer via DellPrintBridge")
        out += ipp_attr(0x45, "printer-uuid", f"urn:uuid:{printer_uuid}")
        out += ipp_attr(0x45, "printer-more-info", f"http://{host}.local:{WEB_PORT}/")

        # Basic printer state.
        out += ipp_int_attr(0x23, "printer-state", 3)  # idle
        out += ipp_attr(0x44, "printer-state-reasons", "none")
        out += ipp_bool_attr("printer-is-accepting-jobs", True)
        out += ipp_int_attr(0x21, "queued-job-count", 0)
        out += ipp_int_attr(0x21, "printer-up-time", printer_up_time)
        out += ipp_int_attr(0x21, "printer-config-change-time", 0)

        # Language / protocol support.
        out += ipp_attr(0x47, "charset-configured", "utf-8")
        out += ipp_attr(0x47, "charset-supported", "utf-8")
        out += ipp_attr(0x48, "natural-language-configured", "en-us")
        out += ipp_attr(0x48, "generated-natural-language-supported", "en-us")
        out += ipp_attr(0x44, "ipp-versions-supported", "1.1")
        out += ipp_attr(0x44, "", "2.0")
        out += ipp_int_attr(0x23, "operations-supported", 0x0002)
        out += ipp_int_attr(0x23, "", 0x0004)
        out += ipp_int_attr(0x23, "", 0x000A)
        out += ipp_int_attr(0x23, "", 0x000B)
        out += ipp_bool_attr("multiple-document-jobs-supported", False)
        out += ipp_int_attr(0x21, "multiple-operation-time-out", 60)

        # Document handling. PDF is the only format the bridge currently accepts.
        out += ipp_attr(0x49, "document-format-default", "application/pdf")
        out += ipp_attr(0x49, "document-format-preferred", "application/pdf")
        out += ipp_attr(0x49, "document-format-supported", "application/pdf")
        out += ipp_attr(0x44, "compression-supported", "none")
        out += ipp_attr(0x44, "pdl-override-supported", "attempted")

        # Common job-template capabilities queried by Android/Mopria/CUPS clients.
        out += ipp_int_attr(0x21, "copies-default", 1)
        out += ipp_range_attr("copies-supported", 1, 99)
        out += ipp_int_attr(0x23, "finishings-default", 3)  # none
        out += ipp_int_attr(0x23, "finishings-supported", 3)

        out += ipp_attr(0x44, "media-default", "na_letter_8.5x11in")
        out += ipp_attr(0x44, "media-supported", "na_letter_8.5x11in")
        out += ipp_attr(0x44, "", "iso_a4_210x297mm")
        out += ipp_attr(0x44, "media-ready", "na_letter_8.5x11in")

        out += ipp_attr(0x44, "sides-default", "one-sided")
        out += ipp_attr(0x44, "sides-supported", "one-sided")

        out += ipp_int_attr(0x23, "orientation-requested-default", 3)  # portrait
        out += ipp_int_attr(0x23, "orientation-requested-supported", 3)
        out += ipp_int_attr(0x23, "", 4)  # landscape

        out += ipp_resolution_attr("printer-resolution-default", 300, 300)
        out += ipp_resolution_attr("printer-resolution-supported", 300, 300)

        out += ipp_int_attr(0x23, "print-quality-default", 4)  # normal
        out += ipp_int_attr(0x23, "print-quality-supported", 3)  # draft
        out += ipp_int_attr(0x23, "", 4)  # normal
        out += ipp_int_attr(0x23, "", 5)  # high

        # The PDF -> RGB -> Windows GDI path preserves color and lets the Windows
        # driver perform the final printer-specific rendering.
        out += ipp_bool_attr("color-supported", True)
        out += ipp_attr(0x44, "print-color-mode-default", "color")
        out += ipp_attr(0x44, "print-color-mode-supported", "monochrome")
        out += ipp_attr(0x44, "", "color")

        out += ipp_attr(0x44, "print-scaling-default", "auto")
        out += ipp_attr(0x44, "print-scaling-supported", "auto")
        out += ipp_attr(0x44, "", "fit")
        out += ipp_bool_attr("page-ranges-supported", False)
        out += ipp_int_attr(0x21, "number-up-default", 1)
        out += ipp_range_attr("number-up-supported", 1, 1)

        out += ipp_attr(0x44, "job-creation-attributes-supported", "copies")
        out += ipp_attr(0x44, "", "finishings")
        out += ipp_attr(0x44, "", "media")
        out += ipp_attr(0x44, "", "orientation-requested")
        out += ipp_attr(0x44, "", "print-color-mode")
        out += ipp_attr(0x44, "", "print-quality")
        out += ipp_attr(0x44, "", "print-scaling")
        out += ipp_attr(0x44, "", "printer-resolution")
        out += ipp_attr(0x44, "", "sides")

    out += b"\x03"  # end-of-attributes-tag
    return bytes(out)


class IppHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def handle_expect_100(self):
        client_ip = self.client_address[0]
        log.info(
            "IPP HTTP Expect: client=%s expect=%r content_length=%r transfer_encoding=%r",
            client_ip,
            self.headers.get("Expect"),
            self.headers.get("Content-Length"),
            self.headers.get("Transfer-Encoding"),
        )
        self.send_response_only(100)
        self.end_headers()
        return True

    def read_chunked_body(self):
        body = bytearray()
        chunk_count = 0

        while True:
            size_line = self.rfile.readline(65537)
            if not size_line:
                raise ValueError("Unexpected EOF while reading chunk size")
            if len(size_line) > 65536:
                raise ValueError("HTTP chunk-size line too long")

            size_text = size_line.strip().split(b";", 1)[0]
            try:
                chunk_size = int(size_text, 16)
            except ValueError as exc:
                raise ValueError(f"Invalid HTTP chunk size: {size_text!r}") from exc

            if chunk_size == 0:
                # Consume any trailer headers and the final blank line.
                while True:
                    trailer = self.rfile.readline(65537)
                    if trailer in (b"\r\n", b"\n", b""):
                        break
                break

            chunk = self.rfile.read(chunk_size)
            if len(chunk) != chunk_size:
                raise ValueError(
                    f"Unexpected EOF while reading HTTP chunk: expected {chunk_size}, got {len(chunk)}"
                )
            body.extend(chunk)
            chunk_count += 1

            terminator = self.rfile.read(2)
            if terminator != b"\r\n":
                raise ValueError(f"Invalid HTTP chunk terminator: {terminator!r}")

        log.info(
            "IPP HTTP chunked body complete: client=%s chunks=%d bytes=%d",
            self.client_address[0],
            chunk_count,
            len(body),
        )
        return bytes(body)

    def read_request_body(self):
        transfer_encoding = (self.headers.get("Transfer-Encoding") or "").lower()
        content_length = self.headers.get("Content-Length")

        if "chunked" in transfer_encoding:
            return self.read_chunked_body(), "chunked"

        if content_length is not None:
            try:
                length = int(content_length)
            except ValueError as exc:
                raise ValueError(f"Invalid Content-Length: {content_length!r}") from exc
            if length < 0:
                raise ValueError(f"Invalid negative Content-Length: {length}")
            body = self.rfile.read(length)
            if len(body) != length:
                raise ValueError(
                    f"Unexpected EOF while reading request body: expected {length}, got {len(body)}"
                )
            return body, f"content-length:{length}"

        return b"", "no-body-length"

    def log_request_metadata(self):
        log.info(
            "IPP HTTP request: client=%s method=%s path=%s version=%s content_type=%r content_length=%r transfer_encoding=%r expect=%r connection=%r user_agent=%r",
            self.client_address[0],
            self.command,
            self.path,
            self.request_version,
            self.headers.get("Content-Type"),
            self.headers.get("Content-Length"),
            self.headers.get("Transfer-Encoding"),
            self.headers.get("Expect"),
            self.headers.get("Connection"),
            self.headers.get("User-Agent"),
        )

    def do_POST(self):
        started = time.monotonic()
        client_ip = self.client_address[0]
        operation_name = "Unknown"
        op_id = 0
        try:
            self.log_request_metadata()
            body, body_mode = self.read_request_body()
            log.info(
                "IPP HTTP body received: client=%s mode=%s bytes=%d prefix=%s",
                client_ip,
                body_mode,
                len(body),
                body[:16].hex() if body else "<empty>",
            )

            if not body:
                raise ValueError(
                    "Empty IPP POST body; check Content-Length/Transfer-Encoding diagnostics above"
                )

            version, op_id, request_id, attrs, document = parse_ipp_request(body)
            operation_name = IPP_OPERATION_NAMES.get(op_id, "Unknown")
            log.info(
                "IPP request: client=%s operation=0x%04x (%s) received_bytes=%d document_bytes=%d",
                client_ip,
                op_id,
                operation_name,
                len(body),
                len(document),
            )

            if op_id == 0x000B:  # Get-Printer-Attributes
                requested = decode_ipp_values(attrs.get("requested-attributes", []))
                log.info(
                    "Get-Printer-Attributes requested-attributes: client=%s count=%d values=%s",
                    client_ip,
                    len(requested),
                    requested if requested else "<not supplied>",
                )
                response = build_ipp_response(version, request_id, include_printer_attrs=True)
            elif op_id == 0x000A:  # Get-Jobs
                # A successful response with no job-attributes groups means the queue is empty.
                # Android's Default Print Service polls this operation while evaluating a printer.
                log.info("Get-Jobs: reporting empty DellPrintBridge job list to %s", client_ip)
                response = build_ipp_response(version, request_id)
            elif op_id == 0x0004:  # Validate-Job
                response = build_ipp_response(version, request_id)
            elif op_id == 0x0002:  # Print-Job
                fmt_values = attrs.get("document-format", [b"application/pdf"])
                fmt = fmt_values[-1].decode("utf-8", errors="replace")
                log.info("Print-Job received: client=%s format=%s bytes=%d", client_ip, fmt, len(document))
                if fmt != "application/pdf":
                    raise ValueError(f"Unsupported document format: {fmt}")
                print_pdf(document, load_config().get("printer_name", ""))
                response = build_ipp_response(version, request_id)
            else:
                log.warning("Unsupported IPP operation from %s: 0x%04x", client_ip, op_id)
                response = version + struct.pack(">H", 0x0501) + request_id + b"\x01" + ipp_attr(0x47, "attributes-charset", "utf-8") + ipp_attr(0x48, "attributes-natural-language", "en-us") + b"\x03"

            self.send_response(200)
            self.send_header("Content-Type", "application/ipp")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)
            log.info(
                "IPP response complete: client=%s operation=0x%04x (%s) elapsed=%.3fs",
                client_ip,
                op_id,
                operation_name,
                time.monotonic() - started,
            )
        except Exception as exc:
            log.exception(
                "IPP request failed: client=%s operation=0x%04x (%s) content_length=%r transfer_encoding=%r expect=%r",
                client_ip,
                op_id,
                operation_name,
                self.headers.get("Content-Length"),
                self.headers.get("Transfer-Encoding"),
                self.headers.get("Expect"),
            )
            self.send_error(500, str(exc))

    def log_message(self, fmt, *args):
        log.info("IPP HTTP: " + fmt, *args)


app = Flask(__name__)
PAGE = """
<!doctype html>
<html><head><meta charset="utf-8"><title>DellPrintBridge</title>
<style>
body{font-family:Segoe UI,Arial,sans-serif;background:#f5f6f7;color:#202124;margin:0}
main{max-width:760px;margin:48px auto;background:#fff;padding:32px;border-radius:14px;box-shadow:0 4px 18px #0001}
h1{margin-top:0} label{display:block;font-weight:600;margin:18px 0 6px} select,input{width:100%;padding:10px;box-sizing:border-box}
button{margin-top:20px;padding:10px 18px;border:0;border-radius:8px;background:#137333;color:#fff;font-weight:600;cursor:pointer}
small{color:#666}.ok{padding:10px;background:#e6f4ea;border-radius:8px}
</style></head>
<body><main><h1>DellPrintBridge</h1>
<p>Expose a Windows printer queue to native IPP clients such as Android Default Print Service.</p>
{% if saved %}<p class="ok">Configuration saved.</p>{% endif %}
<form method="post">
<label>Windows printer queue</label>
<select name="printer_name" required>
<option value="">Select a printer...</option>
{% for p in printers %}<option value="{{p}}" {% if p==cfg.printer_name %}selected{% endif %}>{{p}}</option>{% endfor %}
</select>
<label>Advertised printer name</label>
<input name="display_name" value="{{cfg.display_name}}" required>
<button type="submit">Save configuration</button>
</form>
<p><small>IPP: TCP {{ipp_port}} &nbsp; • &nbsp; mDNS: UDP 5353 &nbsp; • &nbsp; Web UI: TCP {{web_port}}</small></p>
<p><small>Log: {{log_path}}</small></p>
</main></body></html>
"""


@app.route("/", methods=["GET", "POST"])
def index():
    cfg = load_config()
    saved = False
    if request.method == "POST":
        cfg["printer_name"] = request.form["printer_name"]
        cfg["display_name"] = request.form["display_name"].strip() or "Dell Print Bridge"
        save_config(cfg)
        saved = True
    return render_template_string(
        PAGE,
        cfg=cfg,
        printers=get_printers(),
        saved=saved,
        ipp_port=IPP_PORT,
        web_port=WEB_PORT,
        log_path=LOG_PATH,
    )


def advertise():
    cfg = load_config()
    ip = get_local_ip()
    display = cfg.get("display_name") or "Dell Print Bridge"
    service_name = f"{display}._ipp._tcp.local."
    instance_uuid = get_instance_uuid(display)
    props = {
        "txtvers": "1",
        "qtotal": "1",
        "rp": "ipp/print",
        "ty": "Windows Printer via DellPrintBridge",
        "product": "(DellPrintBridge)",
        "pdl": "application/pdf",
        "Color": "T",
        "Duplex": "F",
        "UUID": instance_uuid,
    }
    info = ServiceInfo(
        "_ipp._tcp.local.",
        service_name,
        addresses=[socket.inet_aton(ip)],
        port=IPP_PORT,
        properties=props,
        server=f"{socket.gethostname()}.local.",
    )
    zc = Zeroconf(ip_version=IPVersion.V4Only)
    zc.register_service(info)
    log.info(
        "mDNS advertisement registered: name=%r ip=%s port=%d service=%s",
        display,
        ip,
        IPP_PORT,
        service_name,
    )
    return zc, info


def run():
    cfg = load_config()
    if not os.path.exists(CONFIG_PATH):
        save_config(cfg)

    log.info("=" * 72)
    log.info("DellPrintBridge starting")
    log.info("Host: %s", socket.gethostname())
    log.info("Python PID: %d", os.getpid())
    log.info("Config path: %s", CONFIG_PATH)
    log.info("Log path: %s", LOG_PATH)
    log.info("Selected printer: %r", cfg.get("printer_name", ""))
    log.info("Advertised name: %r", cfg.get("display_name", ""))

    try:
        printers = get_printers()
        log.info("Windows printer queues visible to process: %d", len(printers))
        for printer in printers:
            log.info("  Printer queue: %s", printer)
    except Exception:
        log.exception("Failed to enumerate Windows printer queues at startup")

    ipp_server = ThreadingHTTPServer(("0.0.0.0", IPP_PORT), IppHandler)
    threading.Thread(target=ipp_server.serve_forever, daemon=True, name="IPPServer").start()
    log.info("IPP listener started on 0.0.0.0:%d", IPP_PORT)

    zc = None
    info = None
    try:
        zc, info = advertise()
        log.info("Web UI starting on 0.0.0.0:%d", WEB_PORT)
        app.run(host="0.0.0.0", port=WEB_PORT, threaded=True, use_reloader=False)
    except Exception:
        log.exception("DellPrintBridge terminated because of an unhandled error")
        raise
    finally:
        log.info("DellPrintBridge shutting down")
        if zc and info:
            try:
                zc.unregister_service(info)
            except Exception:
                log.exception("Failed to unregister mDNS service")
            zc.close()
        ipp_server.shutdown()
        log.info("DellPrintBridge stopped")


if __name__ == "__main__":
    run()
