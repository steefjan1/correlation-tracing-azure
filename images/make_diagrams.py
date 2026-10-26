#!/usr/bin/env python3
"""Draws the diagrams for the correlation-tracing post from the official Azure architecture icons.

    pip install pillow cairosvg
    python images/make_diagrams.py

Icons: images/icons/*.svg, copied from Microsoft's Azure Public Service Icons (V24) set.
Output: images/architecture.png, images/trace.png and images/identities.png (2800 px wide, 2x of a 1400 px canvas).
"""
import io
import os

import cairosvg
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ICONS = os.path.join(HERE, "icons")
SCALE = 2                       # render at 2x for crisp text in WordPress
W = 1400

INK = (27, 27, 31)
MUTE = (95, 99, 104)
LINE = (200, 204, 210)
LANE = (242, 245, 249)
LANE_EDGE = (220, 225, 232)
CARD = (255, 255, 255)
BLUE = (0, 120, 212)
AMBER = (178, 106, 0)
AMBER_BG = (255, 247, 230)
GREEN = (30, 126, 52)
RED = (179, 38, 30)

FONT_DIR = "/usr/share/fonts/opentype/inter"
WIN_FONTS = r"C:\Windows\Fonts"


def font(size, weight="Regular"):
    candidates = [
        os.path.join(FONT_DIR, f"Inter-{weight}.otf"),
        os.path.join(WIN_FONTS, {"Regular": "segoeui.ttf", "Medium": "segoeui.ttf",
                                 "SemiBold": "seguisb.ttf", "Bold": "segoeuib.ttf"}[weight]),
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for c in candidates:
        if os.path.exists(c):
            return ImageFont.truetype(c, int(size * SCALE))
    return ImageFont.load_default()


F_TITLE = font(26, "SemiBold")
F_SUB = font(14)
F_LANE = font(13, "SemiBold")
F_BOX = font(14, "SemiBold")
F_BOXSUB = font(11.5)
F_LABEL = font(11.5, "Medium")
F_NOTE = font(11.5)
F_NUM = font(12, "Bold")

_icon_cache = {}


def icon(name, size):
    key = (name, size)
    if key not in _icon_cache:
        png = cairosvg.svg2png(url=os.path.join(ICONS, f"{name}.svg"),
                               output_width=size * SCALE, output_height=size * SCALE)
        _icon_cache[key] = Image.open(io.BytesIO(png)).convert("RGBA")
    return _icon_cache[key]


class Canvas:
    def __init__(self, height):
        self.img = Image.new("RGB", (W * SCALE, height * SCALE), (255, 255, 255))
        self.d = ImageDraw.Draw(self.img)

    def s(self, v):
        return int(round(v * SCALE))

    def text_width(self, text, f):
        return self.d.textlength(text, font=f) / SCALE

    def wrap(self, text, f, max_w):
        words, lines, cur = text.split(), [], ""
        for w in words:
            trial = (cur + " " + w).strip()
            if self.text_width(trial, f) <= max_w or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        return lines

    def text(self, x, y, s, f, fill=INK, anchor="la", max_w=None, line_h=None):
        lines = self.wrap(s, f, max_w) if max_w else [s]
        lh = line_h or (f.size / SCALE * 1.3)
        for i, line in enumerate(lines):
            self.d.text((self.s(x), self.s(y + i * lh)), line, font=f, fill=fill, anchor=anchor)
        return len(lines) * lh

    def rect(self, x, y, w, h, fill=CARD, outline=LINE, r=10, width=1):
        self.d.rounded_rectangle([self.s(x), self.s(y), self.s(x + w), self.s(y + h)],
                                 radius=self.s(r), fill=fill, outline=outline, width=self.s(width))

    def lane(self, x, y, w, h, title):
        self.rect(x, y, w, h, fill=LANE, outline=LANE_EDGE, r=14)
        self.text(x + w / 2, y + 12, title.upper(), F_LANE, MUTE, anchor="ma")

    def box(self, x, y, w, h, icon_name, title, sub=None, icon_size=40, accent=None):
        self.rect(x, y, w, h, fill=CARD, outline=accent or LINE, r=10, width=1.5 if accent else 1)
        ic = icon(icon_name, icon_size)
        self.img.paste(ic, (self.s(x + 12), self.s(y + (h - icon_size) / 2)), ic)
        tx = x + 12 + icon_size + 10
        tw = w - (tx - x) - 10
        title_lines = self.wrap(title, F_BOX, tw)
        sub_lines = self.wrap(sub, F_BOXSUB, tw) if sub else []
        block_h = len(title_lines) * 18 + len(sub_lines) * 15
        ty = y + (h - block_h) / 2
        for line in title_lines:
            self.d.text((self.s(tx), self.s(ty)), line, font=F_BOX, fill=INK)
            ty += 18
        for line in sub_lines:
            self.d.text((self.s(tx), self.s(ty)), line, font=F_BOXSUB, fill=MUTE)
            ty += 15

    def arrow(self, p1, p2, color=BLUE, label=None, width=2, dashed=False, label_side=-1, head=True):
        (x1, y1), (x2, y2) = p1, p2
        if dashed:
            self._dashed(p1, p2, color, width)
        else:
            self.d.line([self.s(x1), self.s(y1), self.s(x2), self.s(y2)], fill=color, width=self.s(width))
        if head:
            import math
            ang = math.atan2(y2 - y1, x2 - x1)
            L, A = 9, 0.5
            pts = [(x2, y2),
                   (x2 - L * math.cos(ang - A), y2 - L * math.sin(ang - A)),
                   (x2 - L * math.cos(ang + A), y2 - L * math.sin(ang + A))]
            self.d.polygon([(self.s(px), self.s(py)) for px, py in pts], fill=color)
        if label:
            mx, my = (x1 + x2) / 2, (y1 + y2) / 2
            tw = self.text_width(label, F_LABEL)
            if abs(y2 - y1) < abs(x2 - x1):      # mostly horizontal: label above
                lx, ly = mx - tw / 2, my + (label_side * 16) - 6
            else:                                  # mostly vertical: label right
                lx, ly = mx + 8, my - 7
            self.rect(lx - 4, ly - 2, tw + 8, 17, fill=(255, 255, 255), outline=(255, 255, 255), r=3)
            self.d.text((self.s(lx), self.s(ly)), label, font=F_LABEL, fill=color)

    def _dashed(self, p1, p2, color, width, dash=6, gap=5):
        import math
        (x1, y1), (x2, y2) = p1, p2
        length = math.hypot(x2 - x1, y2 - y1)
        n = max(int(length / (dash + gap)), 1)
        for i in range(n):
            t0 = i * (dash + gap) / length
            t1 = min((i * (dash + gap) + dash) / length, 1)
            self.d.line([self.s(x1 + (x2 - x1) * t0), self.s(y1 + (y2 - y1) * t0),
                         self.s(x1 + (x2 - x1) * t1), self.s(y1 + (y2 - y1) * t1)],
                        fill=color, width=self.s(width))

    def elbow(self, p1, p2, color=BLUE, label=None, dashed=False, via="h", label_on=1):
        """Right-angled connector: via='h' goes horizontal then vertical; 'v' the other way."""
        (x1, y1), (x2, y2) = p1, p2
        mid = (x2, y1) if via == "h" else (x1, y2)
        self.arrow(p1, mid, color, dashed=dashed, head=False, label=label if label_on == 1 else None)
        self.arrow(mid, p2, color, dashed=dashed, label=label if label_on == 2 else None)

    def callout(self, x, y, w, num, text):
        lines = self.wrap(text, F_NOTE, w - 44)
        h = 14 + len(lines) * 15 + 6
        self.rect(x, y, w, h, fill=AMBER_BG, outline=AMBER, r=8)
        self.d.ellipse([self.s(x + 10), self.s(y + 8), self.s(x + 30), self.s(y + 28)], fill=AMBER)
        self.d.text((self.s(x + 20), self.s(y + 18)), str(num), font=F_NUM, fill=(255, 255, 255), anchor="mm")
        ty = y + 9
        for line in lines:
            self.d.text((self.s(x + 38), self.s(ty)), line, font=F_NOTE, fill=INK)
            ty += 15
        return h

    def badge(self, x, y, num):
        self.d.ellipse([self.s(x - 11), self.s(y - 11), self.s(x + 11), self.s(y + 11)], fill=BLUE)
        self.d.text((self.s(x), self.s(y)), str(num), font=F_NUM, fill=(255, 255, 255), anchor="mm")

    def save(self, name):
        path = os.path.join(HERE, name)
        self.img.save(path, optimize=True)
        print("wrote", path, self.img.size)



GREY = (120, 124, 130)
PURPLE = (98, 70, 180)

def hop_label(c, x, y, lines, color=BLUE, w=None):
    """Small stacked label listing what crosses a hop."""
    w = w or max(c.text_width(l, F_LABEL) for l in lines) + 12
    h = 6 + len(lines) * 15
    c.rect(x, y, w, h, fill=(255, 255, 255), outline=color, r=6)
    for i, l in enumerate(lines):
        c.d.text((c.s(x + 6), c.s(y + 4 + i * 15)), l, font=F_LABEL, fill=color)
    return h


# --------------------------------------------------------------------------- diagram 1: architecture
def architecture():
    c = Canvas(820)
    c.text(40, 32, "Two identities per request: the business correlation id and the W3C trace", F_TITLE)
    c.text(40, 70, "Order intake on Azure. Blue labels show what carries context across each hop; amber marks the "
                   "hop where one of the two identities does not travel on its own.", F_SUB, MUTE, max_w=1150)

    # row 1
    c.box(40, 150, 150, 80, "browser", "Client", "may send X-Correlation-ID", icon_size=36)
    c.box(260, 140, 240, 100, "apim", "API Management", "Consumption. Mints X-Correlation-ID when absent, W3C correlation, echoes it back", icon_size=40)
    c.box(570, 140, 260, 100, "app-service", "Order API (App Service)", ".NET 8, OpenTelemetry distro. Baggage + span tag + log scope", icon_size=40)
    c.box(900, 140, 240, 100, "table", "Table Storage", "Orders: CorrelationId, TraceId, PaymentTraceId, NotificationTraceId", icon_size=38)

    c.arrow((190, 190), (260, 190))
    hop_label(c, 170, 112, ["X-Correlation-ID (optional)"])
    c.arrow((500, 190), (570, 190))
    hop_label(c, 470, 96, ["traceparent (W3C mode)", "X-Correlation-ID"])
    c.arrow((830, 190), (900, 190))
    hop_label(c, 838, 112, ["entity"], color=GREY)

    # row 2: provider + bus
    c.box(570, 330, 260, 90, "functions", "Payment provider (HTTP)", "simulated external API on the Functions app; reports which headers arrived", icon_size=40)
    c.box(900, 330, 240, 90, "service-bus", "Service Bus topic 'orders'", "subscriptions payment, notification; duplicate detection on", icon_size=40)

    c.arrow((700, 240), (700, 330))
    hop_label(c, 500, 258, ["traceparent", "baggage: correlation.id", "X-Correlation-ID"])
    c.arrow((790, 240), (790, 310), head=False)
    c.arrow((790, 310), (1020, 310), head=False)
    c.arrow((1020, 310), (1020, 330))
    hop_label(c, 800, 250, ["Diagnostic-Id = traceparent", "ApplicationProperties.CorrelationId", "(baggage does NOT cross)"], color=AMBER)

    # row 3: workers
    c.box(570, 500, 260, 90, "functions", "PaymentWorker", "subscription 'payment'; restores correlation id from the property; calls provider capture", icon_size=40)
    c.box(900, 500, 240, 90, "functions", "NotificationWorker", "subscription 'notification'; SendEmail span", icon_size=40)
    c.arrow((940, 420), (940, 460), head=False)
    c.arrow((940, 460), (760, 460), head=False)
    c.arrow((760, 460), (760, 500))
    c.arrow((1020, 420), (1020, 500))
    hop_label(c, 1030, 440, ["same message, two branches"], color=GREY)
    c.arrow((700, 500), (700, 420))
    hop_label(c, 500, 450, ["capture: same three carriers"], color=BLUE)

    # ops column (every box reports to the same resource; lines would only add clutter)
    c.box(40, 330, 190, 80, "app-insights", "Application Insights", "one resource for every hop", icon_size=36)
    c.box(40, 430, 190, 80, "log-analytics", "Log Analytics", "AppRequests, AppDependencies, AppTraces", icon_size=36)
    c.rect(40, 530, 190, 78, fill=(236, 244, 252), outline=(190, 215, 240), r=8)
    c.text(50, 536, "Two queries", F_BOX)
    c.text(50, 556, "by OperationId for the engineer, by correlation.id for the service desk", F_BOXSUB, MUTE, max_w=172)

    # identity band
    c.rect(260, 630, 880, 56, fill=(236, 244, 252), outline=(190, 215, 240), r=10)
    ic = icon("managed-identity", 34)
    c.img.paste(ic, (c.s(274), c.s(641)), ic)
    c.text(318, 636, "Managed identities only", F_BOX)
    c.text(318, 656, "API: Table Data Contributor + Service Bus Data Sender. Workers (user-assigned): Table Data Contributor + Service Bus Data Receiver + host storage. "
                     "Shared keys and local auth are off.", F_BOXSUB, MUTE, max_w=800)

    # callouts
    notes = [
        "The gateway owns the business id. If the client sends X-Correlation-ID it is kept; otherwise one is minted at the edge and echoed in the response, so the caller can quote it.",
        "The platform owns the trace. traceparent starts at the gateway (W3C mode) and the SDKs forward it over HTTP and, as Diagnostic-Id, over Service Bus. Nobody writes that code.",
        "Baggage crosses HTTP, not Service Bus. The business id therefore rides in an application property the API sets by hand and the workers restore. That one line is the whole pattern.",
    ]
    y = 705
    for i, n in enumerate(notes):
        c.callout(40 + i * 375, y, 360, i + 1, n)
    c.save("architecture.png")


# --------------------------------------------------------------------------- diagram 2: one trace
def trace():
    c = Canvas(660)
    c.text(40, 32, "One order, one trace: what the waterfall looks like and what each hop carried", F_TITLE)
    c.text(40, 70, "Spans for a SLOW-PAY order. Durations are from the measured run where marked, illustrative otherwise. "
                   "Every span carries correlation.id as an attribute, copied from baggage or from the message property.", F_SUB, MUTE, max_w=1150)

    rows = [
        (0,  "apim",        "API Management",   "POST /orders/api/orders",      0,    5450, "gateway span, root of the trace", BLUE),
        (1,  "app-service", "Order API",        "POST /api/orders",             20,   5400, "", BLUE),
        (2,  "table",       "Table Storage",    "AddEntity Orders",             40,   90,   "", GREY),
        (2,  "functions",   "Payment provider", "POST /api/payments/authorize", 140,  5180, "SLOW-PAY: provider sleeps 5 s", RED),
        (2,  "service-bus", "Service Bus",      "orders send",                  5340, 60,   "Diagnostic-Id + CorrelationId", BLUE),
        (1,  "functions",   "PaymentWorker",    "ServiceBus trigger",           5600, 700,  "delivery 1, same OperationId", PURPLE),
        (2,  "functions",   "Payment provider", "POST /api/payments/capture",   5700, 120,  "", GREY),
        (2,  "table",       "Table Storage",    "MergeEntity Orders",           5900, 60,   "", GREY),
        (1,  "functions",   "NotificationWorker","ServiceBus trigger",          5620, 320,  "second branch of the same message", PURPLE),
        (2,  "functions",   "SendEmail",        "custom span",                  5650, 200,  "", GREY),
    ]
    x0, y0, row_h = 40, 150, 36
    lane_x, lane_w = 440, 560
    total = 6400.0
    # header
    c.text(x0, y0 - 22, "span", F_LANE, MUTE)
    c.text(lane_x, y0 - 22, "0 ms", F_LANE, MUTE)
    c.text(lane_x + lane_w - 60, y0 - 22, "6,400 ms", F_LANE, MUTE)
    for t in range(0, 7):
        gx = lane_x + lane_w * (t * 1000 / total)
        c.d.line([c.s(gx), c.s(y0 - 6), c.s(gx), c.s(y0 + len(rows) * row_h)], fill=(232, 234, 238), width=c.s(1))
    for i, (depth, ic, role, name, start, dur, note, col) in enumerate(rows):
        y = y0 + i * row_h
        im = icon(ic, 22)
        c.img.paste(im, (c.s(x0 + depth * 22), c.s(y + 5)), im)
        c.d.text((c.s(x0 + depth * 22 + 28), c.s(y + 3)), role, font=F_BOX, fill=INK)
        c.d.text((c.s(x0 + depth * 22 + 28), c.s(y + 19)), name, font=F_BOXSUB, fill=MUTE)
        bx = lane_x + lane_w * (start / total)
        bw = max(lane_w * (dur / total), 3)
        c.rect(bx, y + 8, bw, 18, fill=col, outline=col, r=4)
        label = f"{dur:,} ms" + (f"   {note}" if note else "")
        tw = c.text_width(label, F_LABEL)
        lx = bx + bw + 6
        if lx + tw > W - 30:          # would run off the canvas: put it left of the bar instead
            lx = bx - tw - 6
        c.d.text((c.s(lx), c.s(y + 10)), label, font=F_LABEL, fill=col if col != GREY else MUTE)

    # carriers strip
    ys = y0 + len(rows) * row_h + 20
    c.text(40, ys, "What moved the context on each hop", F_BOX)
    items = [
        ("Client -> gateway", ["X-Correlation-ID (optional)"], BLUE),
        ("Gateway -> API", ["traceparent (W3C mode)", "X-Correlation-ID (policy)"], BLUE),
        ("API -> provider (HTTP)", ["traceparent", "baggage: correlation.id", "X-Correlation-ID"], BLUE),
        ("API -> topic -> workers", ["Diagnostic-Id = traceparent", "ApplicationProperties.CorrelationId", "baggage: lost"], AMBER),
        ("Worker -> provider (HTTP)", ["traceparent", "baggage restored", "X-Correlation-ID"], BLUE),
    ]
    x = 40
    for title, lines, col in items:
        c.text(x, ys + 24, title, F_LABEL, INK)
        hop_label(c, x, ys + 42, lines, color=col, w=245)
        x += 262
    c.save("trace.png")


# --------------------------------------------------------------------------- diagram 3: the identities
def identities():
    c = Canvas(430)
    c.text(40, 32, "Four identifiers that get called \"the correlation id\"", F_TITLE)
    c.text(40, 70, "They have different owners, lifetimes and readers. Confusing them is how a team ends up with a trace id in a customer email.", F_SUB, MUTE, max_w=1150)
    cols = ["", "Correlation id", "Trace id", "Span id", "Message id"]
    rowsx = [
        ("Owner", "the business (gateway mints, client may supply)", "the platform (W3C traceparent)", "the platform", "the producer (Service Bus)"),
        ("Scope", "a workflow: place, pay, ship, refund", "one request and its fan-out", "one operation", "one message"),
        ("Lifetime", "days to years", "seconds", "milliseconds", "message TTL"),
        ("Reader", "service desk, customer, auditor", "engineer in a waterfall", "engineer", "the broker (duplicate detection)"),
        ("Carrier", "X-Correlation-ID, baggage, application property", "traceparent header, Diagnostic-Id", "inside traceparent", "MessageId"),
        ("Example", "ticket-4711, ord-20261025-a1b2c3", "4bf92f3577b34da6a3ce929d0e0e4736", "00f067aa0ba902b7", "same as order id here"),
    ]
    x0, y0 = 40, 118
    widths = [100, 330, 300, 230, 300]
    rh = 40
    xs = [x0]
    for w in widths[:-1]:
        xs.append(xs[-1] + w)
    # header
    c.rect(x0, y0, sum(widths), rh, fill=(236, 244, 252), outline=(190, 215, 240), r=6)
    for i, h in enumerate(cols):
        c.d.text((c.s(xs[i] + 10), c.s(y0 + 11)), h, font=F_BOX, fill=INK)
    for r_i, row in enumerate(rowsx):
        y = y0 + rh * (r_i + 1)
        c.d.line([c.s(x0), c.s(y + rh), c.s(x0 + sum(widths)), c.s(y + rh)], fill=LINE, width=c.s(1))
        for i, cell in enumerate(row):
            f = F_BOX if i == 0 else F_BOXSUB
            c.text(xs[i] + 10, y + 8, cell, f, INK if i == 0 else MUTE, max_w=widths[i] - 20, line_h=14)
    c.save("identities.png")


if __name__ == "__main__":
    architecture()
    trace()
    identities()
