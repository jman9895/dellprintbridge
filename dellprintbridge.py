import io
import json
import logging
import os
import socket
import struct
import tempfile
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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
CONFIG_PATH = os.path.join(os.environ.get("PROGRAMDATA", os.getcwd()), APP_NAME, "config.json")
LOG_PATH = os.path.join(os.environ.get("PROGRAMDATA", os.getcwd()), APP_NAME, "dellprintbridge.log")

os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(APP_NAME)

DEFAULT_CONFIG = {
    "printer_name": "",
    "display_name": "Dell Print Bridge",
}


def load_config():
    cfg = DEFAULT_CONFIG.copy()
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    except FileNotFoundError:
        pass
    except Exception:
        log.exception("Failed to load config")
    return cfg


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


def get_printers():
    flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
    printers = win32print.EnumPrinters(flags, None, 2)
    return sorted({p["pPrinterName"] for p in printers}, key=str.lower)


def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


def print_pdf(pdf_bytes, printer_name):
    if not printer_name:
        raise RuntimeError("No Windows printer queue is selected")

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    dc = win32ui.CreateDC()
    dc.CreatePrinterDC(printer_name)

    printable_w = dc.GetDeviceCaps(win32con.HORZRES)
    printable_h = dc.GetDeviceCaps(win32con.VERTRES)

    dc.StartDoc("DellPrintBridge job")
    try:
        for page in doc:
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
    finally:
        dc.EndDoc()
        dc.DeleteDC()
        doc.close()


def ipp_attr(tag, name, value):
    if isinstance(value, str):
        value = value.encode("utf-8")
    return bytes([tag]) + struct.pack(">H", len(name)) + name.encode("utf-8") + struct.pack(">H", len(value)) + value


def ipp_int_attr(tag, name, value):
    return bytes([tag]) + struct.pack(">H", len(name)) + name.encode("utf-8") + struct.pack(">H", 4) + struct.pack(">I", value)


def parse_ipp_request(body):
    if len(body) < 8:
        raise ValueError("IPP request too short")
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


def build_ipp_response(version, request_id, include_printer_attrs=False):
    cfg = load_config()
    display = cfg.get("display_name") or "Dell Print Bridge"
    printer = cfg.get("printer_name") or "Unconfigured Windows Printer"
    host = socket.gethostname()
    uri = f"ipp://{host}.local:{IPP_PORT}/ipp/print"

    out = bytearray()
    out += version
    out += struct.pack(">H", 0x0000)
    out += request_id
    out += b"\x01"
    out += ipp_attr(0x47, "attributes-charset", "utf-8")
    out += ipp_attr(0x48, "attributes-natural-language", "en-us")

    if include_printer_attrs:
        out += b"\x04"
        out += ipp_attr(0x45, "printer-uri-supported", uri)
        out += ipp_attr(0x44, "uri-authentication-supported", "none")
        out += ipp_attr(0x44, "uri-security-supported", "none")
        out += ipp_attr(0x42, "printer-name", display)
        out += ipp_attr(0x41, "printer-info", f"Windows queue: {printer}")
        out += ipp_attr(0x41, "printer-make-and-model", "Windows Printer via DellPrintBridge")
        out += ipp_int_attr(0x23, "printer-state", 3)
        out += ipp_attr(0x44, "printer-state-reasons", "none")
        out += ipp_attr(0x44, "ipp-versions-supported", "1.1")
        out += ipp_attr(0x44, "", "2.0")
        out += ipp_int_attr(0x23, "operations-supported", 0x0002)
        out += ipp_int_attr(0x23, "", 0x0004)
        out += ipp_int_attr(0x23, "", 0x000B)
        out += ipp_attr(0x49, "document-format-default", "application/pdf")
        out += ipp_attr(0x49, "document-format-supported", "application/pdf")
        out += ipp_attr(0x44, "media-default", "na_letter_8.5x11in")
        out += ipp_attr(0x44, "media-supported", "na_letter_8.5x11in")
        out += ipp_attr(0x44, "", "iso_a4_210x297mm")
        out += ipp_attr(0x44, "sides-default", "one-sided")
        out += ipp_attr(0x44, "sides-supported", "one-sided")
        out += bytes([0x22]) + struct.pack(">H", len("color-supported")) + b"color-supported" + struct.pack(">H", 1) + b"\x00"

    out += b"\x03"
    return bytes(out)


class IppHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            version, op_id, request_id, attrs, document = parse_ipp_request(body)
            log.info("IPP operation 0x%04x from %s", op_id, self.client_address[0])

            if op_id == 0x000B:  # Get-Printer-Attributes
                response = build_ipp_response(version, request_id, include_printer_attrs=True)
            elif op_id == 0x0004:  # Validate-Job
                response = build_ipp_response(version, request_id)
            elif op_id == 0x0002:  # Print-Job
                fmt_values = attrs.get("document-format", [b"application/pdf"])
                fmt = fmt_values[-1].decode("utf-8", errors="replace")
                if fmt != "application/pdf":
                    raise ValueError(f"Unsupported document format: {fmt}")
                print_pdf(document, load_config().get("printer_name", ""))
                response = build_ipp_response(version, request_id)
            else:
                response = version + struct.pack(">H", 0x0501) + request_id + b"\x01" + ipp_attr(0x47, "attributes-charset", "utf-8") + ipp_attr(0x48, "attributes-natural-language", "en-us") + b"\x03"

            self.send_response(200)
            self.send_header("Content-Type", "application/ipp")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)
        except Exception as exc:
            log.exception("IPP request failed")
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
    return render_template_string(PAGE, cfg=cfg, printers=get_printers(), saved=saved, ipp_port=IPP_PORT, web_port=WEB_PORT)


def advertise():
    cfg = load_config()
    ip = get_local_ip()
    display = cfg.get("display_name") or "Dell Print Bridge"
    service_name = f"{display}._ipp._tcp.local."
    instance_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{socket.gethostname()}:{display}"))
    props = {
        "txtvers": "1",
        "qtotal": "1",
        "rp": "ipp/print",
        "ty": "Windows Printer via DellPrintBridge",
        "product": "(DellPrintBridge)",
        "pdl": "application/pdf",
        "Color": "F",
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
    log.info("Advertising %s at %s:%d", display, ip, IPP_PORT)
    return zc, info


def run():
    cfg = load_config()
    if not os.path.exists(CONFIG_PATH):
        save_config(cfg)

    ipp_server = ThreadingHTTPServer(("0.0.0.0", IPP_PORT), IppHandler)
    threading.Thread(target=ipp_server.serve_forever, daemon=True).start()
    zc, info = advertise()
    try:
        app.run(host="0.0.0.0", port=WEB_PORT, threaded=True, use_reloader=False)
    finally:
        zc.unregister_service(info)
        zc.close()
        ipp_server.shutdown()


if __name__ == "__main__":
    run()
