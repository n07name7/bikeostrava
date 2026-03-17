"""
PDF route summary - ReportLab with DejaVu Sans.

Sections
────────
1  Header
2  Map (OSM tiles via requests + Pillow)
3  Route info
4  Safety score
5  Elevation profile  ← new
6  Road type breakdown  ← new
7  Detailed score breakdown
8  Highlights
9  Turn-by-turn instructions  ← new
10 Methodology
11 QR code + footer  ← new
"""
import io
import math
import logging
from datetime import datetime

import requests as _requests

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, Flowable, Image as RLImage, KeepTogether,
)

logger = logging.getLogger(__name__)

# ── Fonts ─────────────────────────────────────────────────────────────────────
_FONT_DIR = "/usr/share/fonts/truetype/dejavu"
pdfmetrics.registerFont(TTFont("DejaVu",      f"{_FONT_DIR}/DejaVuSans.ttf"))
pdfmetrics.registerFont(TTFont("DejaVu-Bold", f"{_FONT_DIR}/DejaVuSans-Bold.ttf"))

# ── Colours (aligned with new 2026 design system) ─────────────────────────────
C_BG      = colors.HexColor("#0A0F14")   # --bg
C_S1      = colors.HexColor("#111820")   # --s1
C_S2      = colors.HexColor("#161E2A")   # --s2
C_S3      = colors.HexColor("#1D2433")   # --s3
C_ACCENT  = colors.HexColor("#00D4AA")   # --a
C_ACCENT2 = colors.HexColor("#00A88A")   # gradient end / table headers
C_TEXT    = colors.HexColor("#E6EDF3")   # --text (inside dark cells)
C_MUTED   = colors.HexColor("#8B949E")   # --muted
C_GREEN   = colors.HexColor("#3FB950")   # --green
C_YELLOW  = colors.HexColor("#FBBF24")   # --yellow
C_RED     = colors.HexColor("#FF6B6B")   # --red
C_VIOLET  = colors.HexColor("#8B5CF6")   # --violet
C_INK     = colors.HexColor("#1A2028")   # body text on white page
C_INK_MUT = colors.HexColor("#57606A")   # secondary text on white page


def _score_color(score: int):
    if score >= 70: return C_GREEN
    if score >= 45: return C_YELLOW
    return C_RED


def _score_verdict(score: int) -> str:
    if score >= 80: return "Výborně bezpečná"
    if score >= 65: return "Poměrně bezpečná"
    if score >= 45: return "Průměrně bezpečná"
    return "Méně bezpečná"


def _bar(value: int, width: int = 22) -> str:
    v      = max(0, min(100, value or 0))
    filled = round((v / 100) * width)
    return "█" * filled + "░" * (width - filled)


def _fmt_dist(m: int) -> str:
    return f"{m // 1000}.{(m % 1000) // 100} km" if m >= 1000 else f"{m} m"


# ── Style helper ──────────────────────────────────────────────────────────────
def _style(name, font="DejaVu", size=10, color=C_INK, bold=False,
           align=TA_LEFT, space_before=0, space_after=6, leading=None):
    return ParagraphStyle(
        name,
        fontName="DejaVu-Bold" if bold else font,
        fontSize=size,
        textColor=color,
        alignment=align,
        spaceBefore=space_before,
        spaceAfter=space_after,
        leading=leading or (size * 1.4),
    )


# ══════════════════════════════════════════════════════════════════════════════
# OSM tile map
# ══════════════════════════════════════════════════════════════════════════════

_TILE_SIZE   = 256
_OSM_URL     = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
_OSM_HEADERS = {"User-Agent": "BikeOstrava/1.0 (educational cycling safety project)"}


def _deg2tile(lat, lng, zoom):
    lat_r = math.radians(lat)
    n = 2 ** zoom
    return (int((lng + 180) / 360 * n),
            int((1 - math.log(math.tan(lat_r) + 1 / math.cos(lat_r)) / math.pi) / 2 * n))


def _lng2px(lng, tx0, zoom):
    return (lng + 180) / 360 * (2 ** zoom) * _TILE_SIZE - tx0 * _TILE_SIZE


def _lat2px(lat, ty0, zoom):
    lat_r = math.radians(lat)
    return (1 - math.log(math.tan(lat_r) + 1 / math.cos(lat_r)) / math.pi) / 2 \
        * (2 ** zoom) * _TILE_SIZE - ty0 * _TILE_SIZE


def _best_zoom(min_lat, max_lat, min_lng, max_lng):
    for zoom in range(16, 10, -1):
        tx0, ty1 = _deg2tile(min_lat, min_lng, zoom)
        tx1, ty0 = _deg2tile(max_lat, max_lng, zoom)
        if (tx1 - tx0 + 1) <= 5 and (ty1 - ty0 + 1) <= 5:
            return zoom
    return 10


def _fetch_osm_map(coords, target_w=900):
    """Returns (BytesIO, w_px, h_px) preserving Web Mercator aspect ratio, or None."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None
    if not coords or len(coords) < 2:
        return None

    lngs = [c[0] for c in coords];  lats = [c[1] for c in coords]
    min_lng, max_lng = min(lngs), max(lngs)
    min_lat, max_lat = min(lats), max(lats)
    zoom = _best_zoom(min_lat, max_lat, min_lng, max_lng)

    tx0, ty1_v = _deg2tile(min_lat, min_lng, zoom)
    tx1, ty0_v = _deg2tile(max_lat, max_lng, zoom)
    tx0 -= 1; tx1 += 1; ty0_v -= 1; ty1_v += 1

    composite = Image.new("RGB",
                          ((tx1 - tx0 + 1) * _TILE_SIZE, (ty1_v - ty0_v + 1) * _TILE_SIZE),
                          (220, 220, 220))
    for tx in range(tx0, tx1 + 1):
        for ty in range(ty0_v, ty1_v + 1):
            url = _OSM_URL.format(z=zoom, x=tx, y=ty)
            try:
                r = _requests.get(url, headers=_OSM_HEADERS, timeout=6)
                if r.status_code == 200:
                    composite.paste(Image.open(io.BytesIO(r.content)).convert("RGB"),
                                    ((tx - tx0) * _TILE_SIZE, (ty - ty0_v) * _TILE_SIZE))
            except Exception as e:
                logger.debug("Tile %s: %s", url, e)

    def to_px(lng, lat):
        return int(_lng2px(lng, tx0, zoom)), int(_lat2px(lat, ty0_v, zoom))

    step   = max(1, len(coords) // 800)
    pts    = coords[::step]
    if pts[-1] is not coords[-1]:
        pts = list(pts) + [coords[-1]]
    px_pts = [to_px(c[0], c[1]) for c in pts]

    from PIL import ImageDraw
    draw = ImageDraw.Draw(composite)
    draw.line(px_pts, fill=(0, 0, 0),   width=8)
    draw.line(px_pts, fill=(0, 212, 170), width=4)

    r = 10
    sx, sy = to_px(coords[0][0],  coords[0][1])
    ex, ey = to_px(coords[-1][0], coords[-1][1])
    draw.ellipse([sx-r, sy-r, sx+r, sy+r], fill=(63, 185, 80),  outline=(20, 20, 20), width=2)
    draw.ellipse([ex-r, ey-r, ex+r, ey+r], fill=(248,  81, 73), outline=(20, 20, 20), width=2)

    pad = 50
    cx0 = max(0,               int(_lng2px(min_lng, tx0, zoom)) - pad)
    cx1 = min(composite.width, int(_lng2px(max_lng, tx0, zoom)) + pad)
    cy0 = max(0,                int(_lat2px(max_lat, ty0_v, zoom)) - pad)
    cy1 = min(composite.height, int(_lat2px(min_lat, ty0_v, zoom)) + pad)
    cropped = composite.crop((cx0, cy0, cx1, cy1))

    scale   = target_w / cropped.width
    final_w = target_w
    final_h = max(1, int(cropped.height * scale))

    try:
        resample = Image.Resampling.LANCZOS
    except AttributeError:
        resample = Image.LANCZOS

    result = cropped.resize((final_w, final_h), resample)
    buf    = io.BytesIO()
    result.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf, final_w, final_h


# ══════════════════════════════════════════════════════════════════════════════
# Elevation profile flowable
# ══════════════════════════════════════════════════════════════════════════════

class _ElevationChart(Flowable):
    """Draws a filled elevation profile using canvas primitives."""

    def __init__(self, profile, width, height):
        super().__init__()
        self.profile = profile   # [[dist_km, ele_m], ...]
        self.width   = width
        self.height  = height

    def draw(self):
        if not self.profile or len(self.profile) < 2:
            return
        c  = self.canv
        ML, MR, MT, MB = 42, 8, 6, 22   # margins
        cw = self.width  - ML - MR
        ch = self.height - MT - MB

        dists = [p[0] for p in self.profile]
        eles  = [p[1] for p in self.profile]
        min_d, max_d = 0.0, max(dists)
        raw_min, raw_max = min(eles), max(eles)
        pad_e  = max((raw_max - raw_min) * 0.15, 5)
        min_e  = raw_min - pad_e
        max_e  = raw_max + pad_e
        if max_d == 0 or max_e <= min_e:
            return

        def xy(dist, ele):
            return (ML + (dist - min_d) / (max_d - min_d) * cw,
                    MB + (ele  - min_e) / (max_e - min_e) * ch)

        # Background
        c.setFillColor(C_S1)
        c.setStrokeColor(C_S3)
        c.setLineWidth(0.4)
        c.rect(ML, MB, cw, ch, fill=1, stroke=1)

        # Horizontal grid lines
        c.setStrokeColor(C_S3)
        c.setLineWidth(0.3)
        n_grid = 4
        for i in range(1, n_grid):
            y = MB + i * ch / n_grid
            c.line(ML, y, ML + cw, y)

        # Subsample
        step = max(1, len(self.profile) // 400)
        pts  = self.profile[::step]
        if pts[-1] is not self.profile[-1]:
            pts = list(pts) + [self.profile[-1]]

        # Filled area (teal with alpha via RGBA color)
        fill_col = colors.Color(0, 0.831, 0.667, alpha=0.18)
        path = c.beginPath()
        x0, _ = xy(pts[0][0], min_e)
        path.moveTo(x0, MB)
        for d, e in pts:
            path.lineTo(*xy(d, e))
        path.lineTo(*xy(pts[-1][0], min_e))
        path.close()
        c.setFillColor(fill_col)
        c.drawPath(path, fill=1, stroke=0)

        # Elevation line
        c.setStrokeColor(C_ACCENT)
        c.setLineWidth(1.5)
        line = c.beginPath()
        line.moveTo(*xy(pts[0][0], pts[0][1]))
        for d, e in pts[1:]:
            line.lineTo(*xy(d, e))
        c.drawPath(line, stroke=1, fill=0)

        # Y-axis labels
        c.setFont("DejaVu", 7)
        c.setFillColor(C_MUTED)
        for i in range(n_grid + 1):
            e = min_e + i * (max_e - min_e) / n_grid
            _, y = xy(0, e)
            c.drawRightString(ML - 4, y - 3.5, f"{int(e)} m")

        # X-axis labels
        n_x = 5
        for i in range(n_x + 1):
            d = min_d + i * (max_d - min_d) / n_x
            x, _ = xy(d, min_e)
            c.drawCentredString(x, MB - 13, f"{d:.1f}")
        c.drawCentredString(ML + cw / 2, 1, "km")

        # Ascent / descent stats
        ascent  = sum(max(0, pts[i][1] - pts[i-1][1]) for i in range(1, len(pts)))
        descent = sum(max(0, pts[i-1][1] - pts[i][1]) for i in range(1, len(pts)))
        c.setFillColor(C_MUTED)
        c.setFont("DejaVu", 7)
        stats = f"↑ {int(ascent)} m   ↓ {int(descent)} m"
        c.drawRightString(ML + cw, MB + ch + 2, stats)


# ══════════════════════════════════════════════════════════════════════════════
# Canvas fallback map (no internet)
# ══════════════════════════════════════════════════════════════════════════════

class _CanvasMap(Flowable):
    def __init__(self, coords, width, height):
        super().__init__()
        self.coords = coords
        self.width  = width
        self.height = height

    def draw(self):
        c = self.canv
        c.setFillColor(C_S1); c.setStrokeColor(C_S3); c.setLineWidth(0.5)
        c.rect(0, 0, self.width, self.height, fill=1, stroke=1)
        if not self.coords or len(self.coords) < 2:
            c.setFillColor(C_MUTED); c.setFont("DejaVu", 9)
            c.drawCentredString(self.width/2, self.height/2, "Mapa nedostupná")
            return
        lngs = [p[0] for p in self.coords]; lats = [p[1] for p in self.coords]
        min_lng, max_lng = min(lngs), max(lngs)
        min_lat, max_lat = min(lats), max(lats)
        pl = (max_lng - min_lng) * 0.12 or 0.005
        pa = (max_lat - min_lat) * 0.12 or 0.005
        min_lng -= pl; max_lng += pl; min_lat -= pa; max_lat += pa
        lr = max_lng - min_lng; ar = max_lat - min_lat
        m = 14; w = self.width - 2*m; h = self.height - 2*m - 14
        def to_xy(lng, lat): return m+(lng-min_lng)/lr*w, m+14+(lat-min_lat)/ar*h
        step = max(1, len(self.coords) // 600)
        pts  = self.coords[::step]
        c.setStrokeColor(C_ACCENT); c.setLineWidth(2)
        path = c.beginPath()
        path.moveTo(*to_xy(pts[0][0], pts[0][1]))
        for coord in pts[1:]: path.lineTo(*to_xy(coord[0], coord[1]))
        c.drawPath(path, stroke=1, fill=0)
        for (px, py), col in [(to_xy(self.coords[0][0], self.coords[0][1]),  C_GREEN),
                              (to_xy(self.coords[-1][0], self.coords[-1][1]), C_RED)]:
            c.setFillColor(col); c.setStrokeColor(C_S1); c.setLineWidth(1)
            c.circle(px, py, 5, fill=1, stroke=1)


# ══════════════════════════════════════════════════════════════════════════════
# QR code helper
# ══════════════════════════════════════════════════════════════════════════════

def _make_qr(url: str, size_px: int = 160) -> RLImage | None:
    try:
        import qrcode
        from PIL import Image as PILImage
        qr  = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=6, border=2)
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
        img = img.resize((size_px, size_px))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return RLImage(buf, width=2*cm, height=2*cm)
    except Exception as exc:
        logger.warning("QR generation failed: %s", exc)
        return None


# ══════════════════════════════════════════════════════════════════════════════
# Main PDF builder
# ══════════════════════════════════════════════════════════════════════════════

def generate_route_pdf(saved_route) -> bytes:
    from django.conf import settings as djsettings

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=2*cm, rightMargin=2*cm,
        topMargin=2*cm,  bottomMargin=2*cm,
        title="BikeOstrava - Souhrn trasy",
        author="BikeOstrava",
    )

    score      = saved_route.safety_score
    score_col  = _score_color(score)
    data       = saved_route.route_data or {}
    breakdown  = data.get("score_breakdown", {})
    highlights = data.get("highlights", [])
    generated  = datetime.now().strftime("%-d. %-m. %Y  %H:%M")
    route_url  = f"{djsettings.SITE_URL}/?route={saved_route.id}"

    geojson   = data.get("route_geojson", {})
    coords    = (geojson.get("geometry") or {}).get("coordinates", []) if geojson else []
    elev_prof = data.get("elevation_profile", [])
    instrs    = data.get("instructions", [])
    road_segs = data.get("road_segments", [])

    # ── Styles ────────────────────────────────────────────────────────────────
    s_title   = _style("T",   size=26, color=C_ACCENT,  bold=True,  space_after=2)
    s_sub     = _style("Sub", size=10, color=C_INK_MUT, space_after=18)
    s_h2      = _style("H2",  size=12, color=C_ACCENT,  bold=True,  space_before=14, space_after=8)
    s_body    = _style("B",   size=10, color=C_INK,     space_after=0, leading=15)
    s_score_n = _style("SN",  size=52, color=score_col, bold=True,  align=TA_CENTER, space_after=2)
    s_score_l = _style("SL",  size=10, color=C_INK_MUT, align=TA_CENTER, space_after=16)
    s_verdict = _style("VD",  size=14, color=score_col, bold=True,  align=TA_CENTER, space_after=18)
    s_hl      = _style("HL",  size=10, color=C_INK,     space_after=3, leading=16)
    s_foot    = _style("FT",  size=8,  color=C_INK_MUT, align=TA_CENTER)

    story = []

    # ── 1. Header ─────────────────────────────────────────────────────────────
    story.append(Paragraph("BikeOstrava", s_title))
    story.append(Paragraph(f"Souhrn cyklotrasy  ·  {generated}", s_sub))
    story.append(HRFlowable(width="100%", thickness=1.5, color=C_ACCENT, spaceAfter=16))

    # ── 2. Map ────────────────────────────────────────────────────────────────
    if coords:
        story.append(Paragraph("Mapa trasy", s_h2))
        map_result = _fetch_osm_map(coords, target_w=900)
        if map_result:
            map_buf, map_pw, map_ph = map_result
            story.append(RLImage(map_buf, width=doc.width, height=doc.width * map_ph / map_pw))
        else:
            story.append(_CanvasMap(coords, doc.width, doc.width * 0.45))
        story.append(Spacer(1, 14))

    # ── 3. Route info ─────────────────────────────────────────────────────────
    story.append(Paragraph("Trasa", s_h2))
    lk = _style("LK", size=9,  color=C_MUTED)
    lv = _style("LV", size=10, color=C_TEXT)
    info_table = Table(
        [[Paragraph(k, lk), Paragraph(v, lv)] for k, v in [
            ("Odkud",      saved_route.start_address or "-"),
            ("Kam",        saved_route.end_address   or "-"),
            ("Vzdálenost", f"{saved_route.distance_km:.1f} km"),
            ("Čas jízdy",  f"{saved_route.duration_min} min"),
        ]],
        colWidths=[3.5*cm, None],
    )
    info_table.setStyle(TableStyle([
        ("TOPPADDING",    (0,0),(-1,-1), 5), ("BOTTOMPADDING", (0,0),(-1,-1), 5),
        ("LEFTPADDING",   (0,0),(-1,-1), 10), ("RIGHTPADDING",  (0,0),(-1,-1), 10),
        ("ROWBACKGROUNDS",(0,0),(-1,-1), [C_S1, C_S2]),
        ("VALIGN",        (0,0),(-1,-1), "TOP"),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 14))

    # ── 4. Score ──────────────────────────────────────────────────────────────
    story.append(Paragraph(str(score), s_score_n))
    story.append(Paragraph("Celkové skóre bezpečnosti  (0 – 100)", s_score_l))
    story.append(Paragraph(_score_verdict(score), s_verdict))

    # ── 5. Elevation profile ──────────────────────────────────────────────────
    if elev_prof and len(elev_prof) >= 2:
        story.append(KeepTogether([
            Paragraph("Výškový profil", s_h2),
            _ElevationChart(elev_prof, doc.width, 90),
            Spacer(1, 14),
        ]))

    # ── 6. Road type breakdown ────────────────────────────────────────────────
    if road_segs:
        def _seg_km(cat):
            total = 0.0
            for seg in road_segs:
                if seg.get("category") == cat:
                    pts = seg["coords"]
                    for i in range(1, len(pts)):
                        dx = pts[i][0] - pts[i-1][0]
                        dy = pts[i][1] - pts[i-1][1]
                        total += math.sqrt(dx*dx + dy*dy) * 111.32
            return total

        km_bike    = _seg_km("bike")
        km_neutral = _seg_km("neutral")
        km_busy    = _seg_km("busy")
        km_total   = km_bike + km_neutral + km_busy or 1

        def _pct(v): return f"{int(round(v / km_total * 100))} %"

        road_data = [
            [Paragraph("Typ komunikace", _style("RH", size=9, bold=True, color=C_BG)),
             Paragraph("km",             _style("RH2", size=9, bold=True, color=C_BG, align=TA_CENTER)),
             Paragraph("podíl",          _style("RH3", size=9, bold=True, color=C_BG, align=TA_CENTER))],
            [Paragraph("Cyklostezka / cyklopruh", _style("RL", size=9, color=C_TEXT)),
             Paragraph(f"{km_bike:.1f}",    _style("RV", size=9, color=C_GREEN,  align=TA_CENTER)),
             Paragraph(_pct(km_bike),        _style("RP", size=9, color=C_GREEN,  bold=True, align=TA_CENTER))],
            [Paragraph("Sdílená komunikace",      _style("RL2", size=9, color=C_TEXT)),
             Paragraph(f"{km_neutral:.1f}", _style("RV2", size=9, color=C_YELLOW, align=TA_CENTER)),
             Paragraph(_pct(km_neutral),     _style("RP2", size=9, color=C_YELLOW, bold=True, align=TA_CENTER))],
            [Paragraph("Frekventovaná silnice",   _style("RL3", size=9, color=C_TEXT)),
             Paragraph(f"{km_busy:.1f}",    _style("RV3", size=9, color=C_RED,    align=TA_CENTER)),
             Paragraph(_pct(km_busy),        _style("RP3", size=9, color=C_RED,    bold=True, align=TA_CENTER))],
        ]
        road_table = Table(road_data, colWidths=[None, 2*cm, 2*cm])
        road_table.setStyle(TableStyle([
            ("BACKGROUND",    (0,0),(-1,0),  C_ACCENT2),
            ("ROWBACKGROUNDS",(0,1),(-1,-1), [C_S1, C_S2, C_S1]),
            ("TOPPADDING",    (0,0),(-1,-1), 5), ("BOTTOMPADDING", (0,0),(-1,-1), 5),
            ("LEFTPADDING",   (0,0),(-1,-1), 10), ("RIGHTPADDING",  (0,0),(-1,-1), 10),
            ("FONTNAME",      (0,0),(-1,-1), "DejaVu"),
            ("ALIGN",         (1,0),(-1,-1), "CENTER"),
            ("VALIGN",        (0,0),(-1,-1), "MIDDLE"),
        ]))
        story.append(KeepTogether([Paragraph("Složení trasy", s_h2), road_table, Spacer(1, 14)]))

    # ── 7. Score breakdown ────────────────────────────────────────────────────
    story.append(Paragraph("Detailní hodnocení", s_h2))

    def _bd_row(label, val):
        v   = val if val is not None else 0
        col = _score_color(v)
        return [
            Paragraph(label, _style("BL", size=9, color=C_TEXT)),
            Paragraph(f'<font color="#{col.hexval()[2:]}">{_bar(v)}</font>',
                      _style("BR", size=8, color=col, font="DejaVu", leading=10)),
            Paragraph(str(val) if val is not None else "N/A",
                      _style("BV", size=10, bold=True, color=col, align=TA_CENTER)),
        ]

    bd_table = Table(
        [[Paragraph("Ukazatel",    _style("BH",  size=9, bold=True, color=C_BG)),
          Paragraph("Vizualizace", _style("BH2", size=9, bold=True, color=C_BG)),
          Paragraph("Skóre",       _style("BH3", size=9, bold=True, color=C_BG, align=TA_CENTER))],
         _bd_row("Cyklostezky a cyklopruhy",        breakdown.get("bike_path_coverage")),
         _bd_row("Bezpečnost (nehody, invertováno)", breakdown.get("accident_density")),
         _bd_row("Zelené zóny a parky",             breakdown.get("green_zone_coverage"))],
        colWidths=[6*cm, None, 1.8*cm],
    )
    bd_table.setStyle(TableStyle([
        ("BACKGROUND",    (0,0),(-1,0),  C_ACCENT2),
        ("FONTNAME",      (0,0),(-1,-1), "DejaVu"),
        ("TOPPADDING",    (0,0),(-1,-1), 6), ("BOTTOMPADDING", (0,0),(-1,-1), 6),
        ("LEFTPADDING",   (0,0),(-1,-1), 10), ("RIGHTPADDING",  (0,0),(-1,-1), 10),
        ("ROWBACKGROUNDS",(0,1),(-1,-1), [C_S1, C_S2]),
        ("VALIGN",        (0,0),(-1,-1), "MIDDLE"),
        ("ALIGN",         (2,0),(2,-1),  "CENTER"),
        ("LINEBELOW",     (0,0),(-1,0),  0.5, C_S3),
    ]))
    story.append(bd_table)
    story.append(Spacer(1, 14))

    # ── 8. Highlights ─────────────────────────────────────────────────────────
    if highlights:
        story.append(KeepTogether([
            Paragraph("Klíčové informace o trase", s_h2),
            *[Paragraph(f"•  {h}", s_hl) for h in highlights],
            Spacer(1, 10),
        ]))

    # ── 9. Turn-by-turn instructions ──────────────────────────────────────────
    if instrs:
        story.append(Paragraph("Popis trasy", s_h2))
        s_arr  = _style("AR", size=11, color=C_ACCENT, bold=True, align=TA_CENTER)
        s_itxt = _style("IT", size=9,  color=C_TEXT)
        s_idst = _style("ID", size=9,  color=C_MUTED,  align=TA_RIGHT)

        instr_rows = [
            [Paragraph("", _style("IH1", size=8, bold=True, color=C_BG)),
             Paragraph("Pokyn",       _style("IH2", size=8, bold=True, color=C_BG)),
             Paragraph("Vzdálenost",  _style("IH3", size=8, bold=True, color=C_BG, align=TA_RIGHT))],
        ]
        for ins in instrs:
            instr_rows.append([
                Paragraph(ins["arrow"], s_arr),
                Paragraph(ins["text"],  s_itxt),
                Paragraph(_fmt_dist(ins["dist_m"]), s_idst),
            ])

        instr_table = Table(instr_rows, colWidths=[1.2*cm, None, 2.2*cm])
        instr_table.setStyle(TableStyle([
            ("BACKGROUND",    (0,0),(-1,0),  C_ACCENT2),
            ("ROWBACKGROUNDS",(0,1),(-1,-1), [C_S1, C_S2] * 40),
            ("TOPPADDING",    (0,0),(-1,-1), 4), ("BOTTOMPADDING", (0,0),(-1,-1), 4),
            ("LEFTPADDING",   (0,0),(-1,-1), 8),  ("RIGHTPADDING",  (0,0),(-1,-1), 8),
            ("FONTNAME",      (0,0),(-1,-1), "DejaVu"),
            ("VALIGN",        (0,0),(-1,-1), "MIDDLE"),
            ("ALIGN",         (0,1),(0,-1),  "CENTER"),
            ("ALIGN",         (2,0),(2,-1),  "RIGHT"),
            ("LINEBELOW",     (0,0),(-1,0),  0.5, C_S3),
        ]))
        story.append(instr_table)
        story.append(Spacer(1, 14))

    # ── 10. Methodology ───────────────────────────────────────────────────────
    story.append(Paragraph("Metodika hodnocení", s_h2))
    story.append(Paragraph(
        "Celkové skóre bezpečnosti je vážený průměr tří ukazatelů: "
        "<b>cyklostezky</b> (45 %) - podíl trasy po vyhrazených komunikacích (OSM/GH); "
        "<b>nehody</b> (35 %) - inverzní hustota nehod s cyklisty do 100 m od trasy; "
        "<b>zelené zóny</b> (20 %) - podíl trasy v blízkosti parků (OSM).",
        s_body,
    ))
    story.append(Spacer(1, 20))

    # ── 11. Footer + QR ───────────────────────────────────────────────────────
    story.append(HRFlowable(width="100%", thickness=0.5, color=C_S3, spaceAfter=10))

    qr_img = _make_qr(route_url)
    if qr_img:
        footer_table = Table(
            [[Paragraph(
                f"Vygenerováno službou BikeOstrava  ·  bikeostrava.cz  ·  {generated}\n"
                f"<font size='7' color='#57606A'>Otevřít trasu na webu →</font>",
                s_foot,
             ), qr_img]],
            colWidths=[None, 2.2*cm],
        )
        footer_table.setStyle(TableStyle([
            ("VALIGN",        (0,0),(-1,-1), "MIDDLE"),
            ("LEFTPADDING",   (0,0),(-1,-1), 0),
            ("RIGHTPADDING",  (0,0),(-1,-1), 0),
            ("TOPPADDING",    (0,0),(-1,-1), 0),
            ("BOTTOMPADDING", (0,0),(-1,-1), 0),
        ]))
        story.append(footer_table)
    else:
        story.append(Paragraph(
            f"Vygenerováno službou BikeOstrava  ·  bikeostrava.cz  ·  {generated}", s_foot,
        ))

    doc.build(story)
    return buf.getvalue()
