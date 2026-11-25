"""Serve the coordinator management panel behind Home Assistant ingress."""

from datetime import datetime, timezone
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from secrets import compare_digest, token_urlsafe
from urllib.parse import parse_qs, urlsplit

from control import MAX_OFFLINE_MINUTES


STYLE = """
:root{color-scheme:light dark;font:16px system-ui}
body{max-width:1100px;margin:auto;padding:1.5rem;color:CanvasText;background:Canvas}
h1{font-size:1.8rem;margin:0 0 .7rem}h2{font-size:1.25rem;margin-top:0}
p{line-height:1.5;margin:.6rem 0}.muted{opacity:.8}
section{border:1px solid GrayText;border-radius:.8rem;padding:1rem;margin:1rem 0}
form,.inline{display:flex;align-items:end;flex-wrap:wrap;gap:.6rem;margin:.5rem 0}
label{display:grid;gap:.25rem}label.check{display:flex;align-items:center;gap:.4rem}
input,button{font:inherit;padding:.35rem .55rem}input[type=number]{width:7ch}
button{cursor:pointer}button:disabled{cursor:default}.scroll{overflow:auto}
table{width:100%;border-collapse:collapse;text-align:left}td,th{padding:.5rem;vertical-align:top;border-bottom:1px solid GrayText}
td form{margin:0}.notice{border-left:4px solid #0078a7;padding:.45rem .65rem}
"""


def text(value):
    return escape(str(value), quote=True)


def number_value(value):
    return "" if value is None else text(value)


def form_fields(csrf, action, uid=None):
    return (f'<input type="hidden" name="csrf" value="{text(csrf)}">'
            f'<input type="hidden" name="action" value="{text(action)}">'
            + (f'<input type="hidden" name="uid" value="{text(uid)}">' if uid else ""))


def offline_field(name, value, label=None):
    field = (
        f'<input type="number" aria-label="{text(label or "Offline minutes")}" name="{name}" '
        f'min="0" max="{MAX_OFFLINE_MINUTES}" value="{number_value(value)}">'
    )
    return f"<label>{text(label)}{field}</label>" if label else field


def render_panel(control, csrf, error=None):
    view = control.snapshot()
    nid = view["nid"]
    nid_display = "Waiting for report" if nid is None and view["connected"] else number_value(nid) or "Unknown"
    ready = view["connected"] and nid is not None and not view["busy"]
    enabled = "" if ready else " disabled"
    repeater_rows, device_rows = [], []
    for item in view["devices"]:
        uid = item["uid"]
        status = {True: "Online", False: "Offline", None: "Monitoring off"}[item["online"]]
        when = datetime.fromtimestamp(item["seen"], timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        window_form = (f'<form method="post" action="./">{form_fields(csrf, "override", uid)}'
                       f'{offline_field("minutes", item["override"])}'
                       '<button>Save</button></form>')
        common = f'<td><code>{text(uid)}</code></td><td>{text(item["model"])}</td><td>{status}<br><small>{when}</small></td>'
        if item["kind"] == "repeater":
            can_set = ready and item["heard"] and item["online"] is not False and item["nid"] != nid
            disabled = "" if can_set else " disabled"
            reported = number_value(item["nid"]) or "Unknown"
            change = (f'<form method="post" action="./">{form_fields(csrf, "repeater", uid)}'
                      f'<input type="hidden" name="nid" value="{number_value(nid)}">'
                      f'<button{disabled}>Apply coordinator NID</button></form>')
            repeater_rows.append(f"<tr>{common}<td>{reported}</td><td>{window_form}</td><td>{change}</td></tr>")
        else:
            device_rows.append(f"<tr>{common}<td>{window_form}</td></tr>")
    repeaters = "".join(repeater_rows) or '<tr><td colspan="6">No repeaters heard yet.</td></tr>'
    devices = "".join(device_rows) or '<tr><td colspan="4">No devices heard yet.</td></tr>'
    link = "Connected" if view["connected"] else "Disconnected"
    notice = error or ("Debug passthrough client controls outbound bytes." if view["debug_writes"] else view["notice"])
    return f"""<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Inovonics EchoStream</title><style>{STYLE}</style>
<h1>Inovonics EchoStream</h1>
<p><strong>{link}</strong> · {text(control.source.mode.upper())} · {text(control.source.label)}
 · <a href="./">Refresh status</a></p>
<section><h2>Coordinator</h2><p>Reported NID <strong>{nid_display}</strong></p>
<p class="notice" role="status">{text(notice)}</p>
<form method="post" action="./">{form_fields(csrf, "coordinator")}
<label>New NID<input name="nid" type="number" min="0" max="31" required></label>
<label class="check"><input type="checkbox" name="confirm" value="yes" required>
I’m ready to update the coordinator and its repeaters</label>
<button{enabled}>Set coordinator NID</button></form>
<p class="muted">NID 0 is broadcast. Changing the coordinator NID can interrupt repeater traffic until the repeaters match it.</p></section>
<section><h2>Offline detection</h2>
<form method="post" action="./">{form_fields(csrf, "defaults")}
{offline_field("device", view["windows"]["device"], "Devices, minutes")}
{offline_field("repeater", view["windows"]["repeater"], "Repeaters, minutes")}
<button>Save defaults</button></form>
<p class="muted">Use 0 to disable monitoring. Leave a device override blank to use its default. Connectivity is published as a separate MQTT sensor.</p></section>
<section><h2>Repeaters heard</h2>
<p class="muted">The coordinator applies its current NID to the selected repeater. A matching reset report from that repeater confirms the change.</p>
<div class="scroll"><table><thead><tr><th>UID</th><th>Model</th><th>Last heard</th>
<th>Reported NID</th><th>Offline minutes</th><th>NID action</th></tr></thead><tbody>{repeaters}</tbody></table></div></section>
<section><h2>Devices heard</h2><div class="scroll"><table><thead><tr><th>UID</th><th>Model</th>
<th>Last heard</th><th>Offline minutes</th></tr></thead><tbody>{devices}</tbody></table></div></section>
</html>"""


class PanelHandler(BaseHTTPRequestHandler):
    server_version = "Inovonics"

    def authorized(self):
        if self.client_address[0] != self.server.ingress_peer or not self.headers.get("X-Remote-User-Id"):
            self.send_error(403)
            return False
        if urlsplit(self.path).path not in ("", "/"):
            self.send_error(404)
            return False
        return True

    def render(self, status=200, error=None):
        content = render_panel(self.server.control, self.server.csrf, error).encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        if self.authorized():
            self.render()

    def do_POST(self):
        if not self.authorized():
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 2048 or self.headers.get_content_type() != "application/x-www-form-urlencoded":
                self.send_error(400)
                return
            form = parse_qs(self.rfile.read(length).decode(), keep_blank_values=True, max_num_fields=12)
            token = form.get("csrf", [""])[0]
            if not token.isascii() or not compare_digest(token, self.server.csrf):
                self.send_error(403)
                return
            self.apply(form)
        except (ValueError, KeyError) as error:
            self.render(400, str(error))
            return
        self.send_response(303)
        self.send_header("Location", "./")
        self.end_headers()

    def apply(self, form):
        control = self.server.control
        action = form["action"][0]
        if action == "coordinator":
            if form.get("confirm") != ["yes"]:
                raise ValueError("Confirm the coordinator NID change first.")
            control.request(int(form["nid"][0]))
        elif action == "repeater":
            control.request(int(form["nid"][0]), form["uid"][0])
        elif action == "defaults":
            control.set_windows({kind: int(form[kind][0]) for kind in ("device", "repeater")})
        elif action == "override":
            value = form["minutes"][0].strip()
            control.set_windows(uid=form["uid"][0], override=int(value) if value else None)
        else:
            raise ValueError("Unknown management action.")


class PanelServer(ThreadingHTTPServer):
    def __init__(self, control, address=("0.0.0.0", 8099), ingress_peer="172.30.32.2"):
        self.control = control
        self.csrf = token_urlsafe(32)
        self.ingress_peer = ingress_peer
        super().__init__(address, PanelHandler)

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(5)
        return connection, address
