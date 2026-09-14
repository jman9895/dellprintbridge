import json
import logging
import os
import re
import socket
import struct
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import RotatingFileHandler

import fitz
import win32con
import win32print
import win32ui
import sitecustomize  # Ensure active-user printer preference hook is loaded under SYSTEM.
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
    file_handler = RotatingFileHandler(LOG_PATH, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
    file_handler.setFormatter(formatter)
    log.addHandler(file_handler)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    log.addHandler(console_handler)

DEFAULT_CONFIG = {"printers": []}

PRINTER_PROFILES = {
    "standard": {
        "label": "Letter / A4 color printer",
        "media_default": "na_letter_8.5x11in",
        "media_supported": ["na_letter_8.5x11in", "iso_a4_210x297mm"],
        "dpi": 300,
        "color": True,
        "mdns_ty": "Windows Printer via DellPrintBridge",
    },
    "thermal_4x6": {
        "label": "4 x 6 thermal label printer",
        "media_default": "na_index-4x6_4x6in",
        "media_supported": ["na_index-4x6_4x6in"],
        "dpi": 203,
        # Advertise color-capable input so Android preserves grayscale/color source data.
        # The physical thermal printer remains monochrome; its Windows driver performs
        # the final grayscale-to-dot conversion/dithering.
        "color": True,
        "mdns_ty": "4x6 Thermal Printer via DellPrintBridge",
    },
}

IPP_OPERATION_NAMES = {0x0002: "Print-Job", 0x0004: "Validate-Job", 0x000A: "Get-Jobs", 0x000B: "Get-Printer-Attributes"}
_mdns_lock = threading.Lock()
_mdns_zc = None
_mdns_infos = []


def slugify(value):
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "printer"


def unique_printer_id(cfg, base):
    existing = {p.get("id") for p in cfg.get("printers", [])}
    candidate = slugify(base)
    if candidate not in existing:
        return candidate
    i = 2
    while f"{candidate}-{i}" in existing:
        i += 1
    return f"{candidate}-{i}"


def normalize_config(raw):
    if isinstance(raw.get("printers"), list):
        cfg = {"printers": []}
        for index, item in enumerate(raw["printers"]):
            if not isinstance(item, dict):
                continue
            printer_id = item.get("id") or f"printer-{index + 1}"
            profile = item.get("profile", "standard")
            if profile not in PRINTER_PROFILES:
                profile = "standard"
            cfg["printers"].append({"id": printer_id, "queue_name": item.get("queue_name", ""), "display_name": item.get("display_name") or "Windows Printer", "profile": profile, "enabled": bool(item.get("enabled", True)), "resource": item.get("resource") or f"ipp/printers/{printer_id}"})
        return cfg, False
    legacy_queue = raw.get("printer_name", "")
    legacy_display = raw.get("display_name") or "Dell Print Bridge"
    cfg = {"printers": []}
    if legacy_queue or "printer_name" in raw or "display_name" in raw:
        cfg["printers"].append({"id": "default", "queue_name": legacy_queue, "display_name": legacy_display, "profile": "standard", "enabled": True, "resource": "ipp/print"})
        return cfg, True
    return cfg, False


def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        raw = DEFAULT_CONFIG.copy()
    except Exception:
        log.exception("Failed to load configuration from %s", CONFIG_PATH)
        raw = DEFAULT_CONFIG.copy()
    cfg, migrated = normalize_config(raw)
    if migrated:
        log.info("Migrated legacy single-printer configuration to multi-printer format")
    return cfg


def save_config(cfg):
    clean_cfg, _ = normalize_config(cfg)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(clean_cfg, f, indent=2)
    log.info("Configuration saved: published_printers=%d enabled=%d", len(clean_cfg["printers"]), sum(1 for p in clean_cfg["printers"] if p.get("enabled")))


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


def get_instance_uuid(printer_cfg):
    stable_key = printer_cfg.get("id") or printer_cfg.get("display_name") or "printer"
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{socket.gethostname()}:{stable_key}"))


def get_profile(printer_cfg):
    return PRINTER_PROFILES.get(printer_cfg.get("profile"), PRINTER_PROFILES["standard"])


def find_printer_for_path(path):
    request_path = path.split("?", 1)[0].strip("/")
    for printer in load_config().get("printers", []):
        if printer.get("enabled") and printer.get("resource", "").strip("/") == request_path:
            return printer
    return None


def print_pdf(pdf_bytes, printer_name):
    if not printer_name:
        raise RuntimeError("No Windows printer queue is selected")
    started = time.monotonic()
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page_count = len(doc)
    log.info("Print job starting: printer=%r bytes=%d pages=%d", printer_name, len(pdf_bytes), page_count)
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
                pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
                image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                left = max(0, (printable_w - pix.width) // 2)
                top = max(0, (printable_h - pix.height) // 2)
                ImageWin.Dib(image).draw(dc.GetHandleOutput(), (left, top, left + pix.width, top + pix.height))
                dc.EndPage()
                log.info("Rendered page %d/%d", page_number, page_count)
        finally:
            dc.EndDoc()
    finally:
        dc.DeleteDC()
        doc.close()
    log.info("Print job completed: printer=%r pages=%d elapsed=%.2fs", printer_name, page_count, time.monotonic() - started)


def ipp_attr(tag, name, value):
    if isinstance(value, str): value = value.encode("utf-8")
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
    raw = struct.pack(">iiB", x_dpi, y_dpi, units)
    return bytes([0x32]) + struct.pack(">H", len(name)) + name.encode("utf-8") + struct.pack(">H", len(raw)) + raw


def parse_ipp_request(body):
    if len(body) < 8: raise ValueError(f"IPP request too short ({len(body)} bytes)")
    version, op_id, request_id, i, attrs, last_name = body[0:2], struct.unpack(">H", body[2:4])[0], body[4:8], 8, {}, None
    while i < len(body):
        tag = body[i]; i += 1
        if tag == 0x03: return version, op_id, request_id, attrs, body[i:]
        if tag <= 0x0F: continue
        if i + 4 > len(body): raise ValueError("Malformed IPP attributes")
        name_len = struct.unpack(">H", body[i:i + 2])[0]; i += 2
        if name_len:
            name = body[i:i + name_len].decode("utf-8", errors="replace"); i += name_len; last_name = name
        else: name = last_name
        value_len = struct.unpack(">H", body[i:i + 2])[0]; i += 2
        value = body[i:i + value_len]; i += value_len
        if name: attrs.setdefault(name, []).append(value)
    raise ValueError("Missing IPP end-of-attributes tag")


def decode_ipp_values(values):
    decoded = []
    for value in values:
        try: decoded.append(value.decode("utf-8"))
        except UnicodeDecodeError: decoded.append(f"0x{value.hex()}")
    return decoded


def build_ipp_response(version, request_id, printer_cfg, include_printer_attrs=False):
    display = printer_cfg.get("display_name") or "Windows Printer"
    queue_name = printer_cfg.get("queue_name") or "Unconfigured Windows Printer"
    profile = get_profile(printer_cfg)
    host = socket.gethostname()
    resource = printer_cfg.get("resource", "ipp/print").strip("/")
    uri = f"ipp://{host}.local:{IPP_PORT}/{resource}"
    printer_uuid = get_instance_uuid(printer_cfg)
    out = bytearray(version + struct.pack(">H", 0x0000) + request_id + b"\x01")
    out += ipp_attr(0x47, "attributes-charset", "utf-8") + ipp_attr(0x48, "attributes-natural-language", "en-us")
    if include_printer_attrs:
        out += b"\x04"
        for tag, name, value in [(0x45,"printer-uri-supported",uri),(0x44,"uri-authentication-supported","none"),(0x44,"uri-security-supported","none"),(0x42,"printer-name",display),(0x41,"printer-info",f"Windows queue: {queue_name}"),(0x41,"printer-location",f"Windows host: {host}"),(0x41,"printer-make-and-model",profile["mdns_ty"]),(0x45,"printer-uuid",f"urn:uuid:{printer_uuid}"),(0x45,"printer-more-info",f"http://{host}.local:{WEB_PORT}/")]: out += ipp_attr(tag,name,value)
        out += ipp_int_attr(0x23,"printer-state",3)+ipp_attr(0x44,"printer-state-reasons","none")+ipp_bool_attr("printer-is-accepting-jobs",True)+ipp_int_attr(0x21,"queued-job-count",0)+ipp_int_attr(0x21,"printer-up-time",max(1,int(time.monotonic()-APP_START_MONOTONIC)))+ipp_int_attr(0x21,"printer-config-change-time",0)
        for tag,name,value in [(0x47,"charset-configured","utf-8"),(0x47,"charset-supported","utf-8"),(0x48,"natural-language-configured","en-us"),(0x48,"generated-natural-language-supported","en-us"),(0x44,"ipp-versions-supported","1.1"),(0x44,"","2.0")]: out += ipp_attr(tag,name,value)
        for value in [0x0002,0x0004,0x000A,0x000B]: out += ipp_int_attr(0x23,"operations-supported" if value==0x0002 else "",value)
        out += ipp_bool_attr("multiple-document-jobs-supported",False)+ipp_int_attr(0x21,"multiple-operation-time-out",60)
        for name in ["document-format-default","document-format-preferred","document-format-supported"]: out += ipp_attr(0x49,name,"application/pdf")
        out += ipp_attr(0x44,"compression-supported","none")+ipp_attr(0x44,"pdl-override-supported","attempted")
        out += ipp_int_attr(0x21,"copies-default",1)+ipp_range_attr("copies-supported",1,99)+ipp_int_attr(0x23,"finishings-default",3)+ipp_int_attr(0x23,"finishings-supported",3)
        out += ipp_attr(0x44,"media-default",profile["media_default"])
        for index,media in enumerate(profile["media_supported"]): out += ipp_attr(0x44,"media-supported" if index==0 else "",media)
        out += ipp_attr(0x44,"media-ready",profile["media_default"])+ipp_attr(0x44,"sides-default","one-sided")+ipp_attr(0x44,"sides-supported","one-sided")
        out += ipp_int_attr(0x23,"orientation-requested-default",3)+ipp_int_attr(0x23,"orientation-requested-supported",3)+ipp_int_attr(0x23,"",4)
        dpi=int(profile["dpi"]); out += ipp_resolution_attr("printer-resolution-default",dpi,dpi)+ipp_resolution_attr("printer-resolution-supported",dpi,dpi)
        out += ipp_int_attr(0x23,"print-quality-default",4)+ipp_int_attr(0x23,"print-quality-supported",3)+ipp_int_attr(0x23,"",4)+ipp_int_attr(0x23,"",5)
        out += ipp_bool_attr("color-supported",profile["color"])
        if profile["color"]:
            out += ipp_attr(0x44,"print-color-mode-default","color")+ipp_attr(0x44,"print-color-mode-supported","monochrome")+ipp_attr(0x44,"","color")
        else:
            out += ipp_attr(0x44,"print-color-mode-default","monochrome")+ipp_attr(0x44,"print-color-mode-supported","monochrome")
        out += ipp_attr(0x44,"print-scaling-default","auto")+ipp_attr(0x44,"print-scaling-supported","auto")+ipp_attr(0x44,"","fit")+ipp_bool_attr("page-ranges-supported",False)+ipp_int_attr(0x21,"number-up-default",1)+ipp_range_attr("number-up-supported",1,1)
        for index,value in enumerate(["copies","finishings","media","orientation-requested","print-color-mode","print-quality","print-scaling","printer-resolution","sides"]): out += ipp_attr(0x44,"job-creation-attributes-supported" if index==0 else "",value)
    out += b"\x03"
    return bytes(out)


class IppHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def handle_expect_100(self):
        log.info("IPP HTTP Expect: client=%s expect=%r content_length=%r transfer_encoding=%r",self.client_address[0],self.headers.get("Expect"),self.headers.get("Content-Length"),self.headers.get("Transfer-Encoding")); self.send_response_only(100); self.end_headers(); return True
    def read_chunked_body(self):
        body=bytearray(); chunk_count=0
        while True:
            size_line=self.rfile.readline(65537)
            if not size_line: raise ValueError("Unexpected EOF while reading chunk size")
            size_text=size_line.strip().split(b";",1)[0]
            chunk_size=int(size_text,16)
            if chunk_size==0:
                while self.rfile.readline(65537) not in (b"\r\n",b"\n",b""): pass
                break
            chunk=self.rfile.read(chunk_size)
            if len(chunk)!=chunk_size: raise ValueError("Unexpected EOF while reading HTTP chunk")
            body.extend(chunk); chunk_count+=1
            if self.rfile.read(2)!=b"\r\n": raise ValueError("Invalid HTTP chunk terminator")
        log.info("IPP HTTP chunked body complete: client=%s chunks=%d bytes=%d",self.client_address[0],chunk_count,len(body)); return bytes(body)
    def read_request_body(self):
        transfer=(self.headers.get("Transfer-Encoding") or "").lower(); length=self.headers.get("Content-Length")
        if "chunked" in transfer: return self.read_chunked_body(),"chunked"
        if length is not None:
            length=int(length); body=self.rfile.read(length)
            if len(body)!=length: raise ValueError("Unexpected EOF while reading request body")
            return body,f"content-length:{length}"
        return b"","no-body-length"
    def do_POST(self):
        started=time.monotonic(); client_ip=self.client_address[0]; op_id=0; operation_name="Unknown"
        try:
            printer_cfg=find_printer_for_path(self.path)
            if not printer_cfg: self.send_error(404,"Unknown or disabled DellPrintBridge printer"); return
            body,body_mode=self.read_request_body(); log.info("IPP HTTP body received: client=%s printer=%r mode=%s bytes=%d",client_ip,printer_cfg.get("display_name"),body_mode,len(body))
            version,op_id,request_id,attrs,document=parse_ipp_request(body); operation_name=IPP_OPERATION_NAMES.get(op_id,"Unknown")
            if op_id==0x000B:
                log.info("Get-Printer-Attributes requested-attributes: %s",decode_ipp_values(attrs.get("requested-attributes",[]))); response=build_ipp_response(version,request_id,printer_cfg,True)
            elif op_id in (0x000A,0x0004): response=build_ipp_response(version,request_id,printer_cfg)
            elif op_id==0x0002:
                fmt=attrs.get("document-format",[b"application/pdf"])[-1].decode("utf-8",errors="replace")
                if fmt!="application/pdf": raise ValueError(f"Unsupported document format: {fmt}")
                print_pdf(document,printer_cfg.get("queue_name","")); response=build_ipp_response(version,request_id,printer_cfg)
            else: response=version+struct.pack(">H",0x0501)+request_id+b"\x01"+ipp_attr(0x47,"attributes-charset","utf-8")+ipp_attr(0x48,"attributes-natural-language","en-us")+b"\x03"
            self.send_response(200); self.send_header("Content-Type","application/ipp"); self.send_header("Content-Length",str(len(response))); self.end_headers(); self.wfile.write(response)
            log.info("IPP response complete: client=%s printer=%r operation=0x%04x (%s) elapsed=%.3fs",client_ip,printer_cfg.get("display_name"),op_id,operation_name,time.monotonic()-started)
        except Exception as exc:
            log.exception("IPP request failed: client=%s operation=0x%04x (%s) path=%s",client_ip,op_id,operation_name,self.path); self.send_error(500,str(exc))
    def log_message(self,fmt,*args): log.info("IPP HTTP: "+fmt,*args)


app=Flask(__name__)
PAGE='''<!doctype html><html><head><meta charset="utf-8"><meta name="color-scheme" content="light dark"><title>DellPrintBridge</title><style>
:root{color-scheme:light dark}body{font-family:Segoe UI,Arial,sans-serif;background:#f5f6f7;color:#202124;margin:0}main{max-width:900px;margin:48px auto;background:#fff;padding:32px;border-radius:14px;box-shadow:0 4px 18px #0001}h1{margin-top:0}.subtle{color:#666}.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}.card{border:1px solid #dadce0;border-radius:12px;padding:18px;margin:16px 0;background:#fff}label{display:block;font-weight:600;margin:12px 0 6px}select,input{width:100%;padding:10px;box-sizing:border-box;border:1px solid #bbb;border-radius:6px;background:#fff;color:#202124}input:disabled{background:#f1f3f4;color:#5f6368}button{margin-top:14px;padding:10px 18px;border:0;border-radius:8px;background:#137333;color:#fff;font-weight:600;cursor:pointer}.danger{background:#b3261e}.inline{display:inline}.ok{padding:10px;background:#e6f4ea;color:#137333;border-radius:8px}.warn{padding:10px;background:#fef7e0;color:#5f4200;border-radius:8px}.badge{display:inline-block;padding:3px 8px;border-radius:999px;font-size:12px;background:#e6f4ea;color:#137333}.badge.off{background:#eee;color:#666}.checkbox{display:flex;align-items:center;gap:8px;margin-top:12px}.checkbox input{width:auto}code{background:#f1f3f4;padding:2px 5px;border-radius:4px}
@media(prefers-color-scheme:dark){body{background:#111315;color:#e8eaed}main{background:#1c1f22;box-shadow:0 4px 18px #0008}.subtle{color:#aeb4ba}.card{background:#24282c;border-color:#3b4147}select,input{background:#171a1d;color:#e8eaed;border-color:#555d65}input:disabled{background:#2c3136;color:#aeb4ba}button{background:#188038}.danger{background:#c5221f}.ok{background:#173b25;color:#81c995}.warn{background:#493b16;color:#fdd663}.badge{background:#173b25;color:#81c995}.badge.off{background:#34383d;color:#b7bdc3}code{background:#2c3136;color:#e8eaed}}@media(max-width:700px){.grid{grid-template-columns:1fr}main{margin:0;border-radius:0}}</style></head><body><main>
<h1>DellPrintBridge</h1><p>Publish one or more Windows printer queues to native IPP clients such as Android Default Print Service.</p>{% if saved %}<p class="ok">{{saved}}</p>{% endif %}{% if error %}<p class="warn">{{error}}</p>{% endif %}<h2>Published printers</h2>{% if not cfg.printers %}<p class="subtle">No printers are published yet. Add one below.</p>{% endif %}{% for p in cfg.printers %}<div class="card"><form method="post" action="{{url_for('save_printer',printer_id=p.id)}}"><div style="display:flex;justify-content:space-between;gap:12px;align-items:center"><strong>{{p.display_name}}</strong><span class="badge {% if not p.enabled %}off{% endif %}">{{"Enabled" if p.enabled else "Disabled"}}</span></div><div class="grid"><div><label>Windows printer queue</label><select name="queue_name" required>{% for q in printers %}<option value="{{q}}" {% if q==p.queue_name %}selected{% endif %}>{{q}}</option>{% endfor %}</select></div><div><label>Advertised printer name</label><input name="display_name" value="{{p.display_name}}" required></div><div><label>Capability profile</label><select name="profile">{% for key,profile in profiles.items() %}<option value="{{key}}" {% if key==p.profile %}selected{% endif %}>{{profile.label}}</option>{% endfor %}</select></div><div><label>IPP resource</label><input value="/{{p.resource}}" disabled></div></div><label class="checkbox"><input type="checkbox" name="enabled" value="1" {% if p.enabled %}checked{% endif %}> Advertise this printer</label><button type="submit">Save printer</button></form><form method="post" action="{{url_for('delete_printer',printer_id=p.id)}}" class="inline" onsubmit="return confirm('Remove this published printer?');"><button type="submit" class="danger">Remove</button></form></div>{% endfor %}<h2>Add printer</h2><div class="card"><form method="post" action="{{url_for('add_printer')}}"><div class="grid"><div><label>Windows printer queue</label><select name="queue_name" required><option value="">Select a printer...</option>{% for q in printers %}<option value="{{q}}">{{q}}</option>{% endfor %}</select></div><div><label>Advertised printer name</label><input name="display_name" placeholder="e.g. Nelko Thermal" required></div><div><label>Capability profile</label><select name="profile">{% for key,profile in profiles.items() %}<option value="{{key}}">{{profile.label}}</option>{% endfor %}</select></div></div><button type="submit">+ Add printer</button></form></div><p><small>IPP: TCP {{ipp_port}} &nbsp; • &nbsp; mDNS: UDP 5353 &nbsp; • &nbsp; Web UI: TCP {{web_port}}</small></p><p><small>Changes to published printers refresh mDNS automatically. Log: {{log_path}}</small></p></main></body></html>'''


def duplicate_display_name(cfg,display_name,exclude_id=None): return any(p.get("id")!=exclude_id and p.get("display_name","").casefold()==display_name.casefold() for p in cfg.get("printers",[]))
@app.route("/")
def index(): return render_template_string(PAGE,cfg=load_config(),printers=get_printers(),profiles=PRINTER_PROFILES,saved=request.args.get("saved"),error=request.args.get("error"),ipp_port=IPP_PORT,web_port=WEB_PORT,log_path=LOG_PATH)
@app.post("/printer/add")
def add_printer():
    cfg=load_config(); queue=request.form.get("queue_name","").strip(); display=request.form.get("display_name","").strip(); profile=request.form.get("profile","standard")
    if not queue or not display: return redirect(url_for("index",error="Queue and advertised name are required."))
    if queue not in get_printers(): return redirect(url_for("index",error="Selected Windows printer queue was not found."))
    if profile not in PRINTER_PROFILES: profile="standard"
    if duplicate_display_name(cfg,display): return redirect(url_for("index",error="Each advertised printer name must be unique."))
    pid=unique_printer_id(cfg,display); cfg["printers"].append({"id":pid,"queue_name":queue,"display_name":display,"profile":profile,"enabled":True,"resource":f"ipp/printers/{pid}"}); save_config(cfg); refresh_mdns(); return redirect(url_for("index",saved=f"Added {display}."))
@app.post("/printer/<printer_id>/save")
def save_printer(printer_id):
    cfg=load_config(); p=next((p for p in cfg["printers"] if p.get("id")==printer_id),None)
    if not p: return redirect(url_for("index",error="Published printer was not found."))
    queue=request.form.get("queue_name","").strip(); display=request.form.get("display_name","").strip(); profile=request.form.get("profile","standard"); enabled=request.form.get("enabled")=="1"
    if not queue or not display: return redirect(url_for("index",error="Queue and advertised name are required."))
    if queue not in get_printers(): return redirect(url_for("index",error="Selected Windows printer queue was not found."))
    if duplicate_display_name(cfg,display,printer_id): return redirect(url_for("index",error="Each advertised printer name must be unique."))
    if profile not in PRINTER_PROFILES: profile="standard"
    p.update({"queue_name":queue,"display_name":display,"profile":profile,"enabled":enabled}); save_config(cfg); refresh_mdns(); return redirect(url_for("index",saved=f"Saved {display}."))
@app.post("/printer/<printer_id>/delete")
def delete_printer(printer_id):
    cfg=load_config(); target=next((p for p in cfg["printers"] if p.get("id")==printer_id),None)
    if not target: return redirect(url_for("index",error="Published printer was not found."))
    cfg["printers"]=[p for p in cfg["printers"] if p.get("id")!=printer_id]; save_config(cfg); refresh_mdns(); return redirect(url_for("index",saved=f"Removed {target.get('display_name','printer')}."))


def build_mdns_info(printer_cfg,ip):
    display=printer_cfg.get("display_name") or "Windows Printer"; resource=printer_cfg.get("resource","ipp/print").strip("/"); profile=get_profile(printer_cfg); instance_uuid=get_instance_uuid(printer_cfg)
    props={"txtvers":"1","qtotal":"1","rp":resource,"ty":profile["mdns_ty"],"product":"(DellPrintBridge)","pdl":"application/pdf","Color":"T" if profile["color"] else "F","Duplex":"F","UUID":instance_uuid}
    return ServiceInfo("_ipp._tcp.local.",f"{display}._ipp._tcp.local.",addresses=[socket.inet_aton(ip)],port=IPP_PORT,properties=props,server=f"{socket.gethostname()}.local.")


def refresh_mdns():
    global _mdns_zc,_mdns_infos
    with _mdns_lock:
        if _mdns_zc is None: return
        for info in _mdns_infos:
            try: _mdns_zc.unregister_service(info)
            except Exception: log.exception("Failed to unregister mDNS service %s",info.name)
        _mdns_infos=[]; ip=get_local_ip()
        for printer in load_config().get("printers",[]):
            if not printer.get("enabled") or not printer.get("queue_name"): continue
            info=build_mdns_info(printer,ip); _mdns_zc.register_service(info); _mdns_infos.append(info); log.info("mDNS advertisement registered: name=%r queue=%r resource=/%s ip=%s port=%d profile=%s",printer.get("display_name"),printer.get("queue_name"),printer.get("resource"),ip,IPP_PORT,printer.get("profile"))


def run():
    global _mdns_zc
    cfg=load_config(); raw_needs_save=not os.path.exists(CONFIG_PATH)
    if not raw_needs_save:
        try:
            with open(CONFIG_PATH,"r",encoding="utf-8") as f: raw_needs_save="printers" not in json.load(f)
        except Exception: raw_needs_save=False
    if raw_needs_save: save_config(cfg)
    log.info("="*72); log.info("DellPrintBridge starting"); log.info("Host: %s",socket.gethostname()); log.info("Python PID: %d",os.getpid()); log.info("Configured published printers: %d",len(cfg.get("printers",[])))
    for p in cfg.get("printers",[]): log.info("  Published printer: name=%r queue=%r enabled=%s profile=%s resource=/%s",p.get("display_name"),p.get("queue_name"),p.get("enabled"),p.get("profile"),p.get("resource"))
    try:
        printers=get_printers(); log.info("Windows printer queues visible to process: %d",len(printers))
        for p in printers: log.info("  Printer queue: %s",p)
    except Exception: log.exception("Failed to enumerate Windows printer queues at startup")
    ipp_server=ThreadingHTTPServer(("0.0.0.0",IPP_PORT),IppHandler); threading.Thread(target=ipp_server.serve_forever,daemon=True,name="IPPServer").start(); log.info("IPP listener started on 0.0.0.0:%d",IPP_PORT)
    _mdns_zc=Zeroconf(ip_version=IPVersion.V4Only)
    try:
        refresh_mdns(); log.info("Web UI starting on 0.0.0.0:%d",WEB_PORT); app.run(host="0.0.0.0",port=WEB_PORT,threaded=True,use_reloader=False)
    finally:
        with _mdns_lock:
            for info in _mdns_infos:
                try: _mdns_zc.unregister_service(info)
                except Exception: pass
            _mdns_zc.close(); _mdns_zc=None
        ipp_server.shutdown(); log.info("DellPrintBridge stopped")


if __name__=="__main__": run()
