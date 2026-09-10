import io
import os
import datetime

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    Image as RLImage, HRFlowable, KeepTogether,
)
from reportlab.graphics.shapes import Drawing, Rect, String, Circle
from reportlab.pdfgen import canvas as pdfcanvas

from config import (
    PDF_NAVY, PDF_GREY_BORDER, PDF_GREY_BG, PDF_GREY_TEXT,
    PDF_ROW_ALT, PDF_BODY_TEXT,
)
from models import Patient, SessionModel
from services.game_metrics import get_fields_for

# ------------------------------------------------------------------
# Brand palette — ties the PDF back to the web dashboard's
# glassmorphism / medical design language (medical-green accent
# alongside the existing navy/grey system).
# ------------------------------------------------------------------
BRAND_GREEN = colors.HexColor("#67CF6C")
BRAND_GREEN_LIGHT = colors.HexColor("#91E494")
PAGE_W, PAGE_H = A4
MARGIN_L, MARGIN_R = 40, 40
HEADER_H = 78
FOOTER_H = 92
CONTENT_W = PAGE_W - MARGIN_L - MARGIN_R

BAND_HEX = {
    "good": "#1F8A5F",
    "ok": "#B77A12",
    "poor": "#B03A2E",
    "neutral": "#1B2A4A",
}
BAND_BG_HEX = {
    "good": "#E7F5EE",
    "ok": "#FDF2E3",
    "poor": "#FBEAE8",
    "neutral": "#EDF2FA",
}
BAND_COLORS = {k: (colors.HexColor(v), colors.HexColor(BAND_BG_HEX[k])) for k, v in BAND_HEX.items()}
TRACK_HEX = "#E7E9EE"


def _band(value, good=80, ok=60):
    if value >= good:
        return "good"
    if value >= ok:
        return "ok"
    return "poor"


# end_reason on a completed session: "manual" (therapist pressed End
# Session), "emergency" (Emergency Stop), or None (the VR app's own
# session_end summary arrived on its own — a natural, unassisted end).
END_REASON_LABELS = {
    "manual": "Ended by Therapist",
    "emergency": "Emergency Stop",
}


# ------------------------------------------------------------------
# Charts
# ------------------------------------------------------------------
def _progress_chart(sessions: list) -> io.BytesIO:
    plot_sessions = sessions[-10:]
    idx = list(range(1, len(plot_sessions) + 1))
    acc = [s.final_accuracy or 0 for s in plot_sessions]
    rom = [s.final_rom or 0 for s in plot_sessions]
    dates = [s.created_at.strftime("%m/%d") for s in plot_sessions]

    fig, ax1 = plt.subplots(figsize=(6.8, 2.35), dpi=150)
    fig.patch.set_facecolor("white")
    ax1.set_facecolor("white")

    ax1.plot(idx, acc, color="#1B2A4A", linewidth=2.2, marker="o", markersize=4.5, label="Accuracy %")
    ax1.fill_between(idx, acc, alpha=0.06, color="#1B2A4A")
    ax2 = ax1.twinx()
    ax2.plot(idx, rom, color="#67CF6C", linewidth=1.8, linestyle="--", marker="s", markersize=4, label="ROM \u00b0")

    ax1.set_xticks(idx)
    ax1.set_xticklabels(dates, fontsize=7, color="#5A6472")
    ax1.tick_params(axis="y", labelsize=7, colors="#5A6472")
    ax2.tick_params(axis="y", labelsize=7, colors="#5A6472")
    ax1.set_ylim(0, 100)

    for spine in ("top",):
        ax1.spines[spine].set_visible(False)
        ax2.spines[spine].set_visible(False)
    ax1.spines["left"].set_color("#B7BEC9")
    ax1.spines["bottom"].set_color("#B7BEC9")
    ax2.spines["right"].set_color("#B7BEC9")
    ax1.grid(axis="y", color="#EEF1F5", linewidth=1)

    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper left", fontsize=7.5,
               frameon=False, ncol=2, bbox_to_anchor=(0, 1.22))

    buf = io.BytesIO()
    # bbox_inches="tight" is required here: the legend sits above the axes
    # (bbox_to_anchor y=1.22), and without it savefig crops to the axes
    # bounding box and cuts the legend off at the top of the image.
    fig.savefig(buf, format="png", facecolor="white", bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def _gauge_chart(value: float, band: str) -> io.BytesIO:
    color_hex = BAND_HEX[band]
    fig, ax = plt.subplots(figsize=(1.5, 1.5), dpi=150)
    fig.patch.set_alpha(0)
    ax.pie(
        [max(value, 0), max(100 - value, 0)],
        colors=[color_hex, TRACK_HEX],
        startangle=90, counterclock=False,
        wedgeprops=dict(width=0.30, edgecolor="white", linewidth=1),
    )
    ax.text(0, 0.05, f"{round(value)}%", ha="center", va="center",
            fontsize=15, fontweight="bold", color="#1B2A4A")
    ax.set(aspect="equal")
    buf = io.BytesIO()
    fig.savefig(buf, format="png", transparent=True)
    plt.close(fig)
    buf.seek(0)
    return buf


def _period_trend_chart(period_stats: list) -> io.BytesIO:
    """Line chart of average accuracy/ROM across labeled periods —
    used by the comparative report instead of per-session x-axis."""
    labels = [p["label"] for p in period_stats]
    idx = list(range(1, len(labels) + 1))
    acc = [p["avg_acc"] for p in period_stats]
    rom = [p["avg_rom"] for p in period_stats]

    fig, ax1 = plt.subplots(figsize=(6.8, 2.35), dpi=150)
    fig.patch.set_facecolor("white")
    ax1.set_facecolor("white")

    ax1.plot(idx, acc, color="#1B2A4A", linewidth=2.2, marker="o", markersize=5, label="Accuracy %")
    ax1.fill_between(idx, acc, alpha=0.06, color="#1B2A4A")
    ax2 = ax1.twinx()
    ax2.plot(idx, rom, color="#67CF6C", linewidth=1.8, linestyle="--", marker="s", markersize=4.5, label="ROM \u00b0")

    ax1.set_xticks(idx)
    ax1.set_xticklabels(labels, fontsize=7.5, color="#5A6472")
    ax1.tick_params(axis="y", labelsize=7, colors="#5A6472")
    ax2.tick_params(axis="y", labelsize=7, colors="#5A6472")
    ax1.set_ylim(0, 100)

    for spine in ("top",):
        ax1.spines[spine].set_visible(False)
        ax2.spines[spine].set_visible(False)
    ax1.spines["left"].set_color("#B7BEC9")
    ax1.spines["bottom"].set_color("#B7BEC9")
    ax2.spines["right"].set_color("#B7BEC9")
    ax1.grid(axis="y", color="#EEF1F5", linewidth=1)

    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper left", fontsize=7.5,
               frameon=False, ncol=2, bbox_to_anchor=(0, 1.22))

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor="white", bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def _category_bar_chart(category_stats: list) -> io.BytesIO:
    """Horizontal bar chart of session counts per exercise category —
    used by the administrative report."""
    names = [c["name"] for c in category_stats][::-1]
    counts = [c["count"] for c in category_stats][::-1]

    fig, ax = plt.subplots(figsize=(6.8, max(1.6, 0.4 * len(names))), dpi=150)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    bars = ax.barh(names, counts, color="#67CF6C", height=0.55)
    ax.bar_label(bars, padding=4, fontsize=7.5, color="#1B2A4A")
    ax.set_xlabel("Sessions", fontsize=8, color="#5A6472")
    ax.tick_params(axis="both", labelsize=8, colors="#5A6472")
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    ax.spines["left"].set_color("#B7BEC9")
    ax.spines["bottom"].set_color("#B7BEC9")
    ax.grid(axis="x", color="#EEF1F5", linewidth=1)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor="white", bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


# ------------------------------------------------------------------
# Small building blocks
# ------------------------------------------------------------------
def _info_table(rows, col_widths=(68, 122)):
    label_style = ParagraphStyle("InfoLabel", fontSize=8, textColor=PDF_GREY_TEXT, leading=12)
    value_style = ParagraphStyle("InfoValue", fontSize=9, textColor=PDF_BODY_TEXT, leading=12, fontName="Helvetica-Bold")
    data = [[Paragraph(label, label_style), Paragraph(str(value), value_style)] for label, value in rows]
    t = Table(data, colWidths=list(col_widths))
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
    ]))
    return t


def _patient_info_rows(patient) -> list[tuple[str, str]]:
    """Standard Patient Information card rows. The Doctor row is only
    included when doctor_name was actually entered — if the field was
    left blank at patient creation, it's omitted entirely instead of
    showing a '-' placeholder."""
    rows = [
        ("Patient name", patient.name),
        ("Patient ID", patient.id),
        ("Age / Gender", f"{patient.age} / {patient.gender}"),
        ("Diagnosis", patient.diagnosis or "-"),
    ]
    if patient.doctor_name:
        rows.append(("Doctor", patient.doctor_name))
    rows.append(("Therapist", patient.therapist_name or "-"))
    return rows


def _draw_signature_block(c: pdfcanvas.Canvas, patient, content_w):
    """Draws the therapist/doctor sign-off directly in the footer band,
    just above the boilerplate footer line, instead of as a flowable in
    the page content. This guarantees it always sits at the bottom of
    the page rather than wherever it happens to land after the last
    content block."""
    name_style = ParagraphStyle("SigName", fontSize=9, textColor=PDF_BODY_TEXT, fontName="Helvetica-Bold")
    label_style = ParagraphStyle("SigLabel", fontSize=8, textColor=PDF_GREY_TEXT)

    rule_y = FOOTER_H - 6
    name_y = rule_y - 13
    label_y = name_y - 11

    def _column(x, width, name, label):
        c.setStrokeColor(PDF_GREY_BORDER)
        c.setLineWidth(0.7)
        c.line(x, rule_y, x + width, rule_y)
        c.setFont("Helvetica-Bold", 9)
        c.setFillColor(PDF_BODY_TEXT)
        c.drawString(x, name_y, name)
        c.setFont("Helvetica", 8)
        c.setFillColor(PDF_GREY_TEXT)
        c.drawString(x, label_y, label)

    if patient.doctor_name:
        col_w = content_w / 2 - 10
        _column(MARGIN_L, col_w, patient.therapist_name or "Therapist", "Therapist")
        _column(MARGIN_L + content_w / 2 + 10, col_w, patient.doctor_name, "Reviewing Physician")
    else:
        col_w = content_w / 2
        _column(MARGIN_L, col_w, patient.therapist_name or "Therapist", "Therapist")





def _card_wrap(inner_flowable, title, width, accent=PDF_NAVY):
    title_style = ParagraphStyle("CardTitle", fontSize=9.5, textColor=PDF_NAVY, fontName="Helvetica-Bold", spaceAfter=6)
    t = Table([[Paragraph(title, title_style)], [inner_flowable]], colWidths=[width])
    t.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.6, PDF_GREY_BORDER),
        ("LINEABOVE", (0, 0), (-1, 0), 2.2, accent),
        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return t


def _score_card(overall: float, width=132):
    band = _band(overall)
    text_color, bg_color = BAND_COLORS[band]
    band_label = {"good": "Good Progress", "ok": "Fair Progress", "poor": "Needs Attention"}[band]
    gauge_img = RLImage(_gauge_chart(overall, band), width=96, height=96)
    label_style = ParagraphStyle("ScoreBand", fontSize=9, textColor=text_color,
                                  alignment=TA_CENTER, fontName="Helvetica-Bold", spaceBefore=4)
    header_style = ParagraphStyle("ScoreHeader", fontSize=8.5, textColor=PDF_GREY_TEXT,
                                   alignment=TA_CENTER, fontName="Helvetica-Bold", spaceAfter=4)
    inner = Table([
        [Paragraph("OVERALL SCORE", header_style)],
        [gauge_img],
        [Paragraph(band_label, label_style)],
    ], colWidths=[width - 20])
    inner.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    card = Table([[inner]], colWidths=[width])
    card.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.6, PDF_GREY_BORDER),
        ("LINEABOVE", (0, 0), (-1, 0), 2.2, text_color),
        ("BACKGROUND", (0, 0), (-1, -1), bg_color),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    return card


def _kpi_card(value_text: str, label_text: str, band: str = "neutral", width=122):
    text_color, bg_color = BAND_COLORS[band]
    value_style = ParagraphStyle("KpiValue", fontSize=16, textColor=text_color, fontName="Helvetica-Bold", leading=19)
    label_style = ParagraphStyle("KpiLabel", fontSize=7.8, textColor=PDF_GREY_TEXT, leading=10)
    t = Table([[Paragraph(value_text, value_style)], [Paragraph(label_text, label_style)]], colWidths=[width])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg_color),
        ("BOX", (0, 0), (-1, -1), 0.6, text_color),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    return t


def _goal_band(actual: float, target: float | None) -> str:
    if not target:
        return "neutral"
    pct = (actual / target) * 100
    if pct >= 100:
        return "good"
    if pct >= 80:
        return "ok"
    return "poor"


def _goal_kpi_card(label: str, actual: float, target: float | None, unit: str, width=122):
    band = _goal_band(actual, target)
    text_color, bg_color = BAND_COLORS[band]
    value_style = ParagraphStyle("GoalValue", fontSize=16, textColor=text_color, fontName="Helvetica-Bold", leading=19)
    label_style = ParagraphStyle("GoalLabel", fontSize=7.8, textColor=PDF_GREY_TEXT, leading=10)
    target_txt = f"Target: {target:.0f}{unit}" if target else "No goal set"
    t = Table([
        [Paragraph(f"{actual:.0f}{unit}", value_style)],
        [Paragraph(f"{label}<br/>{target_txt}", label_style)],
    ], colWidths=[width])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg_color),
        ("BOX", (0, 0), (-1, -1), 0.6, text_color),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    return t


def _kpi_grid(cards, per_row=4, width=CONTENT_W, gap=6):
    card_width = width / per_row
    rows = []
    for i in range(0, len(cards), per_row):
        row_cards = cards[i:i + per_row]
        rows.append(row_cards + [Spacer(1, 1)] * (per_row - len(row_cards)))
    grid = Table(rows, colWidths=[card_width] * per_row)
    grid.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), gap / 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), gap / 2),
        ("TOPPADDING", (0, 0), (-1, -1), gap / 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), gap / 2),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return grid


def _format_game_metric_value(value, fmt: str) -> str:
    if value is None:
        return "-"
    try:
        if fmt == "int":
            return str(int(value))
        if fmt == "float1":
            return f"{float(value):.1f}"
        if fmt == "float2":
            return f"{float(value):.2f}"
        if fmt == "percent":
            return f"{float(value):.0f}%"
        if fmt == "meters":
            return f"{float(value):.2f}m"
        if fmt == "seconds":
            return f"{float(value):.1f}s"
        if fmt == "mps":
            return f"{float(value):.2f} m/s"
    except (TypeError, ValueError):
        pass
    return str(value)


def _game_metrics_grid(session, kpi_card_w):
    """Builds the per-game metrics KPI grid (e.g. Total Arm Reach
    Distance, Movement Accuracy, ...) for whichever game_type this
    session was detected as (services/game_metrics.py registry). Returns
    None when the session has no detected game_type or no stored
    game_metrics, so callers can skip the section entirely."""
    fields = get_fields_for(session.game_type)
    if not fields or not session.game_metrics:
        return None
    cards = []
    for db_key, (_vr_field, label, fmt) in fields.items():
        value = session.game_metrics.get(db_key)
        if value is None:
            continue
        cards.append(_kpi_card(_format_game_metric_value(value, fmt), label, width=kpi_card_w))
    return _kpi_grid(cards, width=CONTENT_W) if cards else None


def _checklist_paragraph(text: str, style):
    lines = [ln.strip() for ln in (text or "").split("\n") if ln.strip()]
    if not lines:
        return Paragraph("-", style)
    html = "<br/>".join(f"\u2713&nbsp;&nbsp;{ln}" for ln in lines)
    return Paragraph(html, style)


def _history_table(sessions: list, width=CONTENT_W):
    """Compact table of the last few completed sessions — gives the
    report a real clinical audit trail instead of just one chart."""
    header_style = ParagraphStyle("HistHead", fontSize=7.8, textColor=colors.white,
                                   fontName="Helvetica-Bold", alignment=TA_CENTER)
    cell_style = ParagraphStyle("HistCell", fontSize=8.3, textColor=PDF_BODY_TEXT, alignment=TA_CENTER)
    date_style = ParagraphStyle("HistDate", fontSize=8.3, textColor=PDF_BODY_TEXT, alignment=TA_LEFT)

    rows = [[
        Paragraph("Date", header_style), Paragraph("Exercise", header_style),
        Paragraph("Accuracy", header_style), Paragraph("ROM", header_style),
        Paragraph("Reps", header_style), Paragraph("Duration", header_style),
    ]]
    recent = sorted(sessions, key=lambda s: s.created_at)[-6:]
    for s in recent:
        dur = f"{(s.duration_seconds or 0) // 60}m {(s.duration_seconds or 0) % 60}s"
        rows.append([
            Paragraph(s.created_at.strftime("%d %b %Y"), date_style),
            Paragraph(s.exercise_name, cell_style),
            Paragraph(f"{s.final_accuracy or 0:.0f}%", cell_style),
            Paragraph(f"{s.final_rom or 0:.0f}\u00b0", cell_style),
            Paragraph(str(s.total_reps or 0), cell_style),
            Paragraph(dur, cell_style),
        ])

    col_widths = [width * w for w in (0.20, 0.30, 0.15, 0.13, 0.10, 0.12)]
    t = Table(rows, colWidths=col_widths, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), PDF_NAVY),
        ("TOPPADDING", (0, 0), (-1, -1), 4.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4.5),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, PDF_NAVY),
        ("LINEBELOW", (0, 1), (-1, -1), 0.5, PDF_GREY_BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]
    for i in range(1, len(rows)):
        if i % 2 == 0:
            style.append(("BACKGROUND", (0, i), (-1, i), PDF_ROW_ALT))
    t.setStyle(TableStyle(style))
    return t


def _clinical_summary(patient, session, acc, overall, band) -> str:
    """Auto-generated narrative line — the kind of sentence a therapist
    would otherwise have to type by hand into every report. Built only
    from this session's own Level 2 (final round) fields — never from
    range_of_motion (that field is only ever populated by the legacy
    manual-entry flow and is always empty for VR sessions), and never
    from any other session's data."""
    name = patient.name.split()[0] if patient.name else "The patient"
    errors = session.error_count or 0
    attempts = session.attempts_count or session.total_reps or 0
    verdict = {
        "good": "is progressing well and is on track with the current treatment plan",
        "ok": "is showing steady but moderate progress; the plan may benefit from minor adjustments",
        "poor": "is showing limited progress this session and may need a review of exercise intensity or technique",
    }[band]
    return (
        f"{name} completed <b>{session.exercise_name}</b> with <b>{acc:.0f}% completion</b> "
        f"across <b>{attempts} attempt(s)</b> and <b>{errors} error(s)</b>, giving an overall session "
        f"score of <b>{overall}/100</b>. Based on this session, the patient {verdict}."
    )


# ------------------------------------------------------------------
# Header / footer drawn directly on the canvas so they repeat
# consistently on every page, independent of flowable content.
# ------------------------------------------------------------------
def _draw_header(c: pdfcanvas.Canvas, report_id, generated_by, subtitle: str,
                  title: str = "THERAPY PROGRESS REPORT"):
    # Navy banner
    c.setFillColor(PDF_NAVY)
    c.rect(0, PAGE_H - HEADER_H, PAGE_W, HEADER_H, stroke=0, fill=1)
    # Green accent underline
    c.setFillColor(BRAND_GREEN)
    c.rect(0, PAGE_H - HEADER_H - 3, PAGE_W, 3, stroke=0, fill=1)

    # Brand mark — actual MedNova logo
    c.drawImage(
        "assets/logo.png",
        MARGIN_L, PAGE_H - HEADER_H + 22, width=34, height=34,
        mask="auto", preserveAspectRatio=True,
    )

    # Brand name + title
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 13.5)
    c.drawString(MARGIN_L + 46, PAGE_H - HEADER_H + 42, "MedNova")
    c.setFont("Helvetica", 8)
    c.setFillColor(colors.HexColor("#C9D3E4"))
    c.drawString(MARGIN_L + 46, PAGE_H - HEADER_H + 30, "VR Rehabilitation Platform")

    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 14)
    c.drawRightString(PAGE_W - MARGIN_R, PAGE_H - HEADER_H + 42, title)
    c.setFont("Helvetica", 8)
    c.setFillColor(colors.HexColor("#C9D3E4"))
    c.drawRightString(PAGE_W - MARGIN_R, PAGE_H - HEADER_H + 30,
                       f"Report ID: {report_id or 'PREVIEW'}  \u2022  {subtitle}")


def _draw_footer(c: pdfcanvas.Canvas, page_num, page_count, patient=None):
    # Signature block only on the actual last page of the report -
    # drawn on the canvas (not as a content flowable) so it's always
    # pinned to the bottom of the page, never floating mid-content.
    if patient is not None and page_num == page_count:
        _draw_signature_block(c, patient, CONTENT_W)

    y = FOOTER_H - 46
    c.setStrokeColor(PDF_GREY_BORDER)
    c.setLineWidth(0.6)
    c.line(MARGIN_L, y, PAGE_W - MARGIN_R, y)

    c.setFont("Helvetica", 7.5)
    c.setFillColor(PDF_GREY_TEXT)
    # The old center line here ("Generated electronically - valid without
    # signature") overlapped this left-hand text on long report titles,
    # and is no longer accurate now that a real signature is rendered
    # above on the last page - removed rather than fixed in place.
    c.drawString(MARGIN_L, y - 13, "MedNova VR Rehabilitation Platform \u2022 Confidential clinical report")
    c.drawRightString(PAGE_W - MARGIN_R, y - 13, f"Page {page_num} of {page_count}")


class _ReportCanvas(pdfcanvas.Canvas):
    """Two-pass canvas so the footer can show 'Page X of Y' correctly,
    plus draws the repeating header banner on every page."""

    def __init__(self, *args, **kwargs):
        self._ctx = kwargs.pop("report_ctx")
        super().__init__(*args, **kwargs)
        self._saved_states = []

    def showPage(self):
        self._saved_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        page_count = len(self._saved_states)
        for i, state in enumerate(self._saved_states, start=1):
            self.__dict__.update(state)
            _draw_header(self, self._ctx["report_id"], self._ctx["generated_by"], self._ctx["subtitle"],
                         self._ctx.get("title", "THERAPY PROGRESS REPORT"))
            _draw_footer(self, i, page_count, patient=self._ctx.get("patient"))
            super().showPage()
        super().save()


# ------------------------------------------------------------------
# Main entry point
# ------------------------------------------------------------------
def build_report_sync(patient: Patient, session: SessionModel, output_path: str,
                       report_id: str | None = None, generated_by: str | None = None) -> str:
    styles_section = ParagraphStyle("Section", fontSize=11.5, textColor=PDF_NAVY,
                                     fontName="Helvetica-Bold", spaceBefore=11, spaceAfter=6,
                                     keepWithNext=1)
    styles_body = ParagraphStyle("Body", fontSize=9.5, textColor=PDF_BODY_TEXT, leading=14)
    summary_style = ParagraphStyle("Summary", fontSize=9.7, textColor=PDF_BODY_TEXT, leading=15)

    doc = SimpleDocTemplate(
        output_path, pagesize=A4,
        topMargin=HEADER_H + 16, bottomMargin=FOOTER_H + 14,
        leftMargin=MARGIN_L, rightMargin=MARGIN_R,
    )
    elements = []

    # ---------------- Patient / Session / Score row ----------------
    patient_info = _info_table(_patient_info_rows(patient))
    session_info_rows = [
        ("Exercise", session.exercise_name),
        ("Status", session.status.title()),
        ("Total reps", str(session.total_reps or 0)),
        ("Duration", f"{(session.duration_seconds or 0) // 60}m {(session.duration_seconds or 0) % 60}s"),
    ]
    # Only shown for sessions ended from the dashboard (End Session /
    # Emergency Stop) - a session that finished naturally via the VR
    # app's own session_end summary has end_reason left NULL, so this
    # row is omitted for those rather than showing a misleading "-".
    if session.end_reason:
        session_info_rows.append(("End Reason", END_REASON_LABELS.get(session.end_reason, session.end_reason.title())))
    session_info = _info_table(session_info_rows)

    # acc == this session's own Level 2 completion % (set from the VR
    # summary's completion_percentage, never from another session and
    # never from level1_*). final_rom is intentionally not used here —
    # it's a legacy manual-entry field the VR pipeline never populates,
    # so it was always 0 and made every report's score/summary look
    # identical regardless of the actual round played.
    acc = session.final_accuracy or 0
    overall = round(acc)
    overall_band = _band(overall)

    card_w1, card_w2, card_w3 = 190, 190, CONTENT_W - 380
    info_row = Table([[
        _card_wrap(patient_info, "PATIENT INFORMATION", card_w1),
        _card_wrap(session_info, "SESSION SUMMARY", card_w2, accent=BRAND_GREEN),
        _score_card(overall, card_w3),
    ]], colWidths=[card_w1, card_w2, card_w3])
    info_row.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    elements.append(info_row)

    # ---------------- Clinical summary strip ----------------
    elements.append(Spacer(1, 12))
    summary_text = _clinical_summary(patient, session, acc, overall, overall_band)
    accent_color, bg_color = BAND_COLORS[overall_band]
    summary_box = Table([[Paragraph(summary_text, summary_style)]], colWidths=[CONTENT_W])
    summary_box.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg_color),
        ("LINEBEFORE", (0, 0), (0, -1), 3, accent_color),
        ("LEFTPADDING", (0, 0), (-1, -1), 14),
        ("RIGHTPADDING", (0, 0), (-1, -1), 14),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    elements.append(summary_box)

    # ---------------- KPI cards ----------------
    elements.append(Paragraph("Key Performance Indicators", styles_section))
    kpi_col_w = CONTENT_W / 4
    kpi_card_w = kpi_col_w - 6  # minus the grid cell's own left+right padding
    # Every card below reads only this session's own Level 2 (final
    # round) columns — score/completion_percentage/error_count/
    # attempts_count/response_time_sec/*_hand_usage_pct are all frozen
    # from this session's own VR summary payload and are never touched
    # by another session.
    #
    # For the Arm Movement Analytics game (game_type "virtual_store" —
    # see services/game_metrics.py) the report must show Level 2 data
    # only; Level 1 (Warm Up) numbers are left out entirely, even when
    # present on the session. Any other/未来 game without a game_type
    # match keeps the old Level 1 + Level 2 combination behaviour (for
    # any future game that legitimately needs both levels combined).
    is_ama_game = session.game_type == "virtual_store"
    if is_ama_game:
        combined_score = session.score
        max_hand_raise = session.max_hand_raise_height
    else:
        # Legacy combination: Score card merges Level 1 + Level 2 into a
        # single final score, and Max Hand Raise reads from Level 1
        # (Warm Up) since that's the level the hand-raise action
        # actually happens in.
        combined_score = (session.level1_score or 0) + (session.score or 0) \
            if session.level1_score is not None else session.score
        max_hand_raise = session.level1_max_hand_raise_height if session.level1_max_hand_raise_height is not None \
            else session.max_hand_raise_height
    cards = [
        _kpi_card(f"{acc:.0f}%", "Completion", _band(acc), width=kpi_card_w),
        _kpi_card(str(combined_score if combined_score is not None else 0), "Score", width=kpi_card_w),
        _kpi_card(str(session.attempts_count if session.attempts_count is not None else (session.total_reps or 0)), "Total Reps", width=kpi_card_w),
        _kpi_card(f"{(session.duration_seconds or 0) // 60}m {(session.duration_seconds or 0) % 60}s", "Duration", width=kpi_card_w),
    ]
    if session.left_hand_usage_pct is not None:
        cards.append(_kpi_card(f"{session.left_hand_usage_pct:.0f}%", "Left Hand Usage", width=kpi_card_w))
    if session.right_hand_usage_pct is not None:
        cards.append(_kpi_card(f"{session.right_hand_usage_pct:.0f}%", "Right Hand Usage", width=kpi_card_w))
    if session.error_count is not None:
        err = session.error_count
        err_band = "good" if err == 0 else ("ok" if err <= 3 else "poor")
        cards.append(_kpi_card(str(err), "Error Count", err_band, width=kpi_card_w))
    if session.response_time_sec is not None:
        rt = session.response_time_sec
        rt_band = "good" if rt < 3 else ("ok" if rt <= 6 else "poor")
        cards.append(_kpi_card(f"{rt:.2f}s", "Avg Response Time", rt_band, width=kpi_card_w))
    if max_hand_raise is not None:
        cards.append(_kpi_card(f"{max_hand_raise:.2f}m", "Max Hand Raise", width=kpi_card_w))
    elements.append(_kpi_grid(cards, width=CONTENT_W))

    # ---------------- Per-game metrics (e.g. Arm Movement Analytics) ----------------
    game_metrics_grid = _game_metrics_grid(session, kpi_card_w)
    if game_metrics_grid is not None:
        game_label = session.game_name or session.exercise_name or "Game"
        elements.append(Paragraph(f"{game_label} \u2014 Movement Metrics", styles_section))
        elements.append(game_metrics_grid)

    # Deliberately no "Progress Over Time" chart and no "Recent Session
    # History" table here — this is a single-session report and should
    # only ever show this session's own data. (A patient's multi-session
    # trend belongs in the range/monthly reports, which pull other
    # sessions on purpose and say so in their own titles.)

    # ---------------- Notes / Recommendations ----------------
    if session.therapist_notes or session.recommendations:
        notes_col = None
        recs_col = None
        if session.therapist_notes:
            notes_col = _card_wrap(Paragraph(session.therapist_notes, styles_body), "THERAPIST NOTES", (CONTENT_W - 10) / 2)
        if session.recommendations:
            recs_col = _card_wrap(_checklist_paragraph(session.recommendations, styles_body), "RECOMMENDATIONS",
                                   (CONTENT_W - 10) / 2, accent=BRAND_GREEN)
        elements.append(Paragraph("Notes and Recommendations", styles_section))
        if notes_col and recs_col:
            row = Table([[notes_col, recs_col]], colWidths=[(CONTENT_W - 10) / 2, (CONTENT_W - 10) / 2])
            row.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (1, 0), (1, 0), 10)]))
            elements.append(row)
        elif notes_col:
            elements.append(notes_col)
        elif recs_col:
            elements.append(recs_col)

    ctx = {
        "report_id": report_id,
        "generated_by": generated_by,
        "subtitle": f"Session date: {session.created_at.strftime('%d %b %Y')}",
        "patient": patient,
    }

    def _make_canvas(*args, **kwargs):
        kwargs["report_ctx"] = ctx
        return _ReportCanvas(*args, **kwargs)

    doc.build(elements, canvasmaker=_make_canvas)
    return output_path


def _clinical_summary_range(patient, sessions, avg_acc, avg_rom, overall, band, start_date, end_date) -> str:
    """Same idea as _clinical_summary, but narrates an aggregated period
    instead of a single session."""
    name = patient.name.split()[0] if patient.name else "The patient"
    verdict = {
        "good": "is progressing well and is on track with the current treatment plan",
        "ok": "is showing steady but moderate progress; the plan may benefit from minor adjustments",
        "poor": "is showing limited progress over this period and may need a review of exercise intensity or technique",
    }[band]
    period_txt = f"{start_date.strftime('%d %b %Y')} \u2013 {end_date.strftime('%d %b %Y')}"
    return (
        f"Between <b>{period_txt}</b>, {name} completed <b>{len(sessions)} session(s)</b> with an average of "
        f"<b>{avg_acc:.0f}% movement accuracy</b> and <b>{avg_rom:.0f}\u00b0 range of motion</b>, "
        f"giving an overall period score of <b>{overall}/100</b>. Based on this period, the patient {verdict}."
    )


# ------------------------------------------------------------------
# Date-range entry point — aggregates multiple completed sessions for
# one patient into a single report (weekly / monthly / custom range).
# Reuses every building block above (_kpi_card, _progress_chart,
# _history_table, etc.) — only the info cards and clinical summary
# differ from the single-session report.
# ------------------------------------------------------------------
def build_range_report_sync(patient: Patient, sessions: list, start_date, end_date, output_path: str,
                             report_id: str | None = None, generated_by: str | None = None) -> str:
    styles_section = ParagraphStyle("Section", fontSize=11.5, textColor=PDF_NAVY,
                                     fontName="Helvetica-Bold", spaceBefore=11, spaceAfter=6,
                                     keepWithNext=1)
    styles_body = ParagraphStyle("Body", fontSize=9.5, textColor=PDF_BODY_TEXT, leading=14)
    summary_style = ParagraphStyle("Summary", fontSize=9.7, textColor=PDF_BODY_TEXT, leading=15)

    doc = SimpleDocTemplate(
        output_path, pagesize=A4,
        topMargin=HEADER_H + 16, bottomMargin=FOOTER_H + 14,
        leftMargin=MARGIN_L, rightMargin=MARGIN_R,
    )
    elements = []

    sessions = sorted(sessions, key=lambda s: s.created_at)

    def _avg(values):
        vals = [v for v in values if v is not None]
        return sum(vals) / len(vals) if vals else 0

    avg_acc = _avg([s.final_accuracy for s in sessions])
    avg_rom = _avg([s.final_rom for s in sessions])
    total_reps = sum(s.total_reps or 0 for s in sessions)
    total_duration = sum(s.duration_seconds or 0 for s in sessions)
    avg_errors = _avg([s.error_count for s in sessions])
    avg_reaction = _avg([s.avg_reaction_time_ms for s in sessions])
    avg_left_hand = _avg([s.left_hand_usage_pct for s in sessions])
    avg_right_hand = _avg([s.right_hand_usage_pct for s in sessions])
    has_errors = any(s.error_count is not None for s in sessions)
    has_reaction = any(s.avg_reaction_time_ms is not None for s in sessions)
    has_left = any(s.left_hand_usage_pct is not None for s in sessions)
    has_right = any(s.right_hand_usage_pct is not None for s in sessions)

    # ---------------- Patient / Period / Score row ----------------
    patient_info = _info_table(_patient_info_rows(patient))
    period_info = _info_table([
        ("Period", f"{start_date.strftime('%d %b %Y')} - {end_date.strftime('%d %b %Y')}"),
        ("Total Sessions", str(len(sessions))),
        ("Total Reps", str(total_reps)),
        ("Total Duration", f"{total_duration // 60}m {total_duration % 60}s"),
    ])

    rom_pct = min(avg_rom / 180 * 100, 100)
    overall = round(avg_acc * 0.65 + rom_pct * 0.35)
    overall_band = _band(overall)

    card_w1, card_w2, card_w3 = 190, 190, CONTENT_W - 380
    info_row = Table([[
        _card_wrap(patient_info, "PATIENT INFORMATION", card_w1),
        _card_wrap(period_info, "PERIOD SUMMARY", card_w2, accent=BRAND_GREEN),
        _score_card(overall, card_w3),
    ]], colWidths=[card_w1, card_w2, card_w3])
    info_row.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    elements.append(info_row)

    # ---------------- Clinical summary strip ----------------
    elements.append(Spacer(1, 12))
    summary_text = _clinical_summary_range(patient, sessions, avg_acc, avg_rom, overall, overall_band,
                                            start_date, end_date)
    accent_color, bg_color = BAND_COLORS[overall_band]
    summary_box = Table([[Paragraph(summary_text, summary_style)]], colWidths=[CONTENT_W])
    summary_box.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg_color),
        ("LINEBEFORE", (0, 0), (0, -1), 3, accent_color),
        ("LEFTPADDING", (0, 0), (-1, -1), 14),
        ("RIGHTPADDING", (0, 0), (-1, -1), 14),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    elements.append(summary_box)

    # ---------------- KPI cards (period averages / totals) ----------------
    elements.append(Paragraph("Key Performance Indicators (Period Average)", styles_section))
    kpi_col_w = CONTENT_W / 4
    kpi_card_w = kpi_col_w - 6
    cards = [
        _kpi_card(f"{avg_acc:.0f}%", "Avg Accuracy", _band(avg_acc), width=kpi_card_w),
        _kpi_card(f"{avg_rom:.0f}\u00b0", "Avg Range of Motion", _band(rom_pct), width=kpi_card_w),
        _kpi_card(str(total_reps), "Total Reps", width=kpi_card_w),
        _kpi_card(f"{total_duration // 60}m {total_duration % 60}s", "Total Duration", width=kpi_card_w),
    ]
    if has_left:
        cards.append(_kpi_card(f"{avg_left_hand:.0f}%", "Avg Left Hand Usage", width=kpi_card_w))
    if has_right:
        cards.append(_kpi_card(f"{avg_right_hand:.0f}%", "Avg Right Hand Usage", width=kpi_card_w))
    if has_errors:
        err_band = "good" if avg_errors == 0 else ("ok" if avg_errors <= 3 else "poor")
        cards.append(_kpi_card(f"{avg_errors:.1f}", "Avg Error Count", err_band, width=kpi_card_w))
    if has_reaction:
        rt_band = "good" if avg_reaction < 400 else ("ok" if avg_reaction <= 700 else "poor")
        cards.append(_kpi_card(f"{avg_reaction:.0f} ms", "Avg Reaction Time", rt_band, width=kpi_card_w))
    elements.append(_kpi_grid(cards, width=CONTENT_W))

    # ---------------- Progress chart + session history ----------------
    if len(sessions) >= 2:
        elements.append(Paragraph("Progress Over Time", styles_section))
        elements.append(RLImage(_progress_chart(sessions), width=CONTENT_W, height=138))
    if sessions:
        elements.append(Paragraph("Session History", styles_section))
        elements.append(_history_table(sessions, width=CONTENT_W))

    subtitle = f"Period: {start_date.strftime('%d %b %Y')} \u2013 {end_date.strftime('%d %b %Y')}"
    ctx = {"report_id": report_id, "generated_by": generated_by, "subtitle": subtitle, "patient": patient}

    def _make_canvas(*args, **kwargs):
        kwargs["report_ctx"] = ctx
        return _ReportCanvas(*args, **kwargs)

    doc.build(elements, canvasmaker=_make_canvas)
    return output_path

# ------------------------------------------------------------------
# Monthly report — same shape as the range report, plus a therapeutic
# goals comparison card (actual period average vs. the patient's
# target_accuracy / target_rom).
# ------------------------------------------------------------------
def build_monthly_report_sync(patient: Patient, sessions: list, month_start, month_end, output_path: str,
                               report_id: str | None = None, generated_by: str | None = None) -> str:
    styles_section = ParagraphStyle("Section", fontSize=11.5, textColor=PDF_NAVY,
                                     fontName="Helvetica-Bold", spaceBefore=11, spaceAfter=6,
                                     keepWithNext=1)
    styles_body = ParagraphStyle("Body", fontSize=9.5, textColor=PDF_BODY_TEXT, leading=14)
    summary_style = ParagraphStyle("Summary", fontSize=9.7, textColor=PDF_BODY_TEXT, leading=15)

    doc = SimpleDocTemplate(
        output_path, pagesize=A4,
        topMargin=HEADER_H + 16, bottomMargin=FOOTER_H + 14,
        leftMargin=MARGIN_L, rightMargin=MARGIN_R,
    )
    elements = []

    sessions = sorted(sessions, key=lambda s: s.created_at)

    def _avg(values):
        vals = [v for v in values if v is not None]
        return sum(vals) / len(vals) if vals else 0

    avg_acc = _avg([s.final_accuracy for s in sessions])
    avg_rom = _avg([s.final_rom for s in sessions])
    total_reps = sum(s.total_reps or 0 for s in sessions)
    total_duration = sum(s.duration_seconds or 0 for s in sessions)

    month_label = month_start.strftime("%B %Y")

    patient_info = _info_table(_patient_info_rows(patient))
    period_info = _info_table([
        ("Month", month_label),
        ("Total Sessions", str(len(sessions))),
        ("Total Reps", str(total_reps)),
        ("Total Duration", f"{total_duration // 60}m {total_duration % 60}s"),
    ])

    rom_pct = min(avg_rom / 180 * 100, 100)
    overall = round(avg_acc * 0.65 + rom_pct * 0.35)
    overall_band = _band(overall)

    card_w1, card_w2, card_w3 = 190, 190, CONTENT_W - 380
    info_row = Table([[
        _card_wrap(patient_info, "PATIENT INFORMATION", card_w1),
        _card_wrap(period_info, "MONTH SUMMARY", card_w2, accent=BRAND_GREEN),
        _score_card(overall, card_w3),
    ]], colWidths=[card_w1, card_w2, card_w3])
    info_row.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    elements.append(info_row)

    elements.append(Spacer(1, 12))
    summary_text = _clinical_summary_range(patient, sessions, avg_acc, avg_rom, overall, overall_band,
                                            month_start, month_end)
    accent_color, bg_color = BAND_COLORS[overall_band]
    summary_box = Table([[Paragraph(summary_text, summary_style)]], colWidths=[CONTENT_W])
    summary_box.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg_color),
        ("LINEBEFORE", (0, 0), (0, -1), 3, accent_color),
        ("LEFTPADDING", (0, 0), (-1, -1), 14),
        ("RIGHTPADDING", (0, 0), (-1, -1), 14),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    elements.append(summary_box)

    # ---------------- Therapeutic goals ----------------
    elements.append(Spacer(1, 12))
    elements.append(Paragraph("Therapeutic Goals", styles_section))
    goal_col_w = (CONTENT_W - 10) / 2
    goals_row = Table([[
        _goal_kpi_card("Accuracy vs. Goal", avg_acc, patient.target_accuracy, "%", width=goal_col_w - 12),
        _goal_kpi_card("Range of Motion vs. Goal", avg_rom, patient.target_rom, "\u00b0", width=goal_col_w - 12),
    ]], colWidths=[goal_col_w, goal_col_w])
    goals_row.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (1, 0), (1, 0), 10)]))
    elements.append(goals_row)
    if not patient.target_accuracy and not patient.target_rom:
        elements.append(Spacer(1, 4))
        elements.append(Paragraph(
            "No therapeutic goals have been set for this patient yet — set target accuracy/ROM "
            "on the patient profile to see progress-to-goal here.",
            ParagraphStyle("GoalNote", fontSize=8, textColor=PDF_GREY_TEXT, leading=11)))

    # ---------------- KPI cards ----------------
    elements.append(Paragraph("Key Performance Indicators (Month Average)", styles_section))
    kpi_col_w = CONTENT_W / 4
    kpi_card_w = kpi_col_w - 6
    cards = [
        _kpi_card(f"{avg_acc:.0f}%", "Avg Accuracy", _band(avg_acc), width=kpi_card_w),
        _kpi_card(f"{avg_rom:.0f}\u00b0", "Avg Range of Motion", _band(rom_pct), width=kpi_card_w),
        _kpi_card(str(total_reps), "Total Reps", width=kpi_card_w),
        _kpi_card(f"{total_duration // 60}m {total_duration % 60}s", "Total Duration", width=kpi_card_w),
    ]
    elements.append(_kpi_grid(cards, width=CONTENT_W))

    if len(sessions) >= 2:
        elements.append(Paragraph("Progress Over the Month", styles_section))
        elements.append(RLImage(_progress_chart(sessions), width=CONTENT_W, height=138))
    if sessions:
        elements.append(Paragraph("Session History", styles_section))
        elements.append(_history_table(sessions, width=CONTENT_W))

    ctx = {
        "report_id": report_id, "generated_by": generated_by,
        "subtitle": f"Month: {month_label}",
        "title": "MONTHLY PROGRESS REPORT",
        "patient": patient,
    }

    def _make_canvas(*args, **kwargs):
        kwargs["report_ctx"] = ctx
        return _ReportCanvas(*args, **kwargs)

    doc.build(elements, canvasmaker=_make_canvas)
    return output_path


def _clinical_summary_comparative(patient, period_stats, band) -> str:
    name = patient.name.split()[0] if patient.name else "The patient"
    first, last = period_stats[0], period_stats[-1]
    delta = last["overall"] - first["overall"]
    if delta > 3:
        trend_txt = f"an improving trend (+{delta} points)"
    elif delta < -3:
        trend_txt = f"a declining trend ({delta} points)"
    else:
        trend_txt = "a broadly stable trend"
    verdict = {
        "good": "and is on track with the current treatment plan",
        "ok": "the plan may benefit from minor adjustments",
        "poor": "a review of exercise intensity or technique is recommended",
    }[band]
    return (
        f"Comparing <b>{first['label']}</b> to <b>{last['label']}</b>, {name}'s overall score moved from "
        f"<b>{first['overall']}/100</b> to <b>{last['overall']}/100</b>, showing {trend_txt}. "
        f"Based on the most recent period, {verdict}."
    )


def _period_table(period_stats: list, width=CONTENT_W):
    header_style = ParagraphStyle("PeriodHead", fontSize=7.8, textColor=colors.white,
                                   fontName="Helvetica-Bold", alignment=TA_CENTER)
    cell_style = ParagraphStyle("PeriodCell", fontSize=8.3, textColor=PDF_BODY_TEXT, alignment=TA_CENTER)
    label_style = ParagraphStyle("PeriodLabel", fontSize=8.3, textColor=PDF_BODY_TEXT, alignment=TA_LEFT, fontName="Helvetica-Bold")

    rows = [[
        Paragraph("Period", header_style), Paragraph("Sessions", header_style),
        Paragraph("Avg Accuracy", header_style), Paragraph("Avg ROM", header_style),
        Paragraph("Overall Score", header_style), Paragraph("Trend", header_style),
    ]]
    prev_overall = None
    for p in period_stats:
        if prev_overall is None:
            trend = "-"
        elif p["overall"] > prev_overall:
            trend = "\u2191 Improving"
        elif p["overall"] < prev_overall:
            trend = "\u2193 Declining"
        else:
            trend = "\u2192 Stable"
        rows.append([
            Paragraph(p["label"], label_style),
            Paragraph(str(p["session_count"]), cell_style),
            Paragraph(f"{p['avg_acc']:.0f}%", cell_style),
            Paragraph(f"{p['avg_rom']:.0f}\u00b0", cell_style),
            Paragraph(f"{p['overall']}/100", cell_style),
            Paragraph(trend, cell_style),
        ])
        prev_overall = p["overall"]

    col_widths = [width * w for w in (0.22, 0.14, 0.18, 0.14, 0.16, 0.16)]
    t = Table(rows, colWidths=col_widths, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), PDF_NAVY),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, PDF_NAVY),
        ("LINEBELOW", (0, 1), (-1, -1), 0.5, PDF_GREY_BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]
    for i in range(1, len(rows)):
        if i % 2 == 0:
            style.append(("BACKGROUND", (0, i), (-1, i), PDF_ROW_ALT))
    t.setStyle(TableStyle(style))
    return t


# ------------------------------------------------------------------
# Comparative report — compares one patient's performance across
# multiple caller-defined periods (e.g. month over month) to surface
# the overall progress trend, rather than a single period's average.
#
# `periods` is a list of dicts, each already containing its own
# completed sessions:
#   [{"label": "May 2026", "start": date, "end": date, "sessions": [...]}, ...]
# ------------------------------------------------------------------
def build_comparative_report_sync(patient: Patient, periods: list, output_path: str,
                                   report_id: str | None = None, generated_by: str | None = None) -> str:
    styles_section = ParagraphStyle("Section", fontSize=11.5, textColor=PDF_NAVY,
                                     fontName="Helvetica-Bold", spaceBefore=11, spaceAfter=6,
                                     keepWithNext=1)
    summary_style = ParagraphStyle("Summary", fontSize=9.7, textColor=PDF_BODY_TEXT, leading=15)

    doc = SimpleDocTemplate(
        output_path, pagesize=A4,
        topMargin=HEADER_H + 16, bottomMargin=FOOTER_H + 14,
        leftMargin=MARGIN_L, rightMargin=MARGIN_R,
    )
    elements = []

    def _avg(values):
        vals = [v for v in values if v is not None]
        return sum(vals) / len(vals) if vals else 0

    period_stats = []
    for p in periods:
        sess = sorted(p["sessions"], key=lambda s: s.created_at)
        avg_acc = _avg([s.final_accuracy for s in sess])
        avg_rom = _avg([s.final_rom for s in sess])
        rom_pct = min(avg_rom / 180 * 100, 100)
        overall = round(avg_acc * 0.65 + rom_pct * 0.35)
        period_stats.append({
            "label": p["label"], "start": p["start"], "end": p["end"],
            "sessions": sess, "session_count": len(sess),
            "avg_acc": avg_acc, "avg_rom": avg_rom, "overall": overall,
        })

    overall_last = period_stats[-1]["overall"]
    overall_band = _band(overall_last)

    patient_info = _info_table(_patient_info_rows(patient))
    periods_info = _info_table([
        ("Periods compared", str(len(period_stats))),
        ("From", period_stats[0]["label"]),
        ("To", period_stats[-1]["label"]),
        ("Total Sessions", str(sum(p["session_count"] for p in period_stats))),
    ])

    card_w1, card_w2, card_w3 = 190, 190, CONTENT_W - 380
    info_row = Table([[
        _card_wrap(patient_info, "PATIENT INFORMATION", card_w1),
        _card_wrap(periods_info, "COMPARISON OVERVIEW", card_w2, accent=BRAND_GREEN),
        _score_card(overall_last, card_w3),
    ]], colWidths=[card_w1, card_w2, card_w3])
    info_row.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    elements.append(info_row)

    elements.append(Spacer(1, 12))
    summary_text = _clinical_summary_comparative(patient, period_stats, overall_band)
    accent_color, bg_color = BAND_COLORS[overall_band]
    summary_box = Table([[Paragraph(summary_text, summary_style)]], colWidths=[CONTENT_W])
    summary_box.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg_color),
        ("LINEBEFORE", (0, 0), (0, -1), 3, accent_color),
        ("LEFTPADDING", (0, 0), (-1, -1), 14),
        ("RIGHTPADDING", (0, 0), (-1, -1), 14),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    elements.append(summary_box)

    elements.append(Paragraph("Progress Trend Across Periods", styles_section))
    elements.append(RLImage(_period_trend_chart(period_stats), width=CONTENT_W, height=138))

    elements.append(Paragraph("Period-by-Period Breakdown", styles_section))
    elements.append(_period_table(period_stats, width=CONTENT_W))

    for p in period_stats:
        if p["sessions"]:
            elements.append(Paragraph(f"Session History \u2014 {p['label']}", styles_section))
            elements.append(_history_table(p["sessions"], width=CONTENT_W))

    subtitle = f"Comparing {period_stats[0]['label']} \u2192 {period_stats[-1]['label']}"
    ctx = {
        "report_id": report_id, "generated_by": generated_by,
        "subtitle": subtitle, "title": "COMPARATIVE PROGRESS REPORT",
        "patient": patient,
    }

    def _make_canvas(*args, **kwargs):
        kwargs["report_ctx"] = ctx
        return _ReportCanvas(*args, **kwargs)

    doc.build(elements, canvasmaker=_make_canvas)
    return output_path


# ------------------------------------------------------------------
# Administrative report — department-level operational statistics,
# not tied to any single patient: session volume, completion/usage
# rate, and which exercise categories see the most engagement.
#
# `stats` is a plain dict prepared by the caller:
#   {
#     "total_sessions": int, "completed_sessions": int,
#     "active_patients": int, "active_devices": int, "total_devices": int,
#     "categories": [{"name": str, "count": int}, ...],
#   }
# ------------------------------------------------------------------
def build_admin_report_sync(start_date, end_date, stats: dict, output_path: str,
                             report_id: str | None = None, generated_by: str | None = None) -> str:
    styles_section = ParagraphStyle("Section", fontSize=11.5, textColor=PDF_NAVY,
                                     fontName="Helvetica-Bold", spaceBefore=11, spaceAfter=6,
                                     keepWithNext=1)
    summary_style = ParagraphStyle("Summary", fontSize=9.7, textColor=PDF_BODY_TEXT, leading=15)
    cell_style = ParagraphStyle("Cell", fontSize=9, textColor=PDF_BODY_TEXT)

    doc = SimpleDocTemplate(
        output_path, pagesize=A4,
        topMargin=HEADER_H + 16, bottomMargin=FOOTER_H + 14,
        leftMargin=MARGIN_L, rightMargin=MARGIN_R,
    )
    elements = []

    total_sessions = stats.get("total_sessions", 0)
    completed_sessions = stats.get("completed_sessions", 0)
    completion_rate = (completed_sessions / total_sessions * 100) if total_sessions else 0
    active_patients = stats.get("active_patients", 0)
    active_devices = stats.get("active_devices", 0)
    total_devices = stats.get("total_devices", 0)
    device_util = (active_devices / total_devices * 100) if total_devices else 0
    categories = stats.get("categories", [])

    period_info = _info_table([
        ("Period", f"{start_date.strftime('%d %b %Y')} - {end_date.strftime('%d %b %Y')}"),
        ("Total Sessions", str(total_sessions)),
        ("Completed Sessions", str(completed_sessions)),
        ("Active Patients", str(active_patients)),
    ])
    completion_band = _band(completion_rate)
    info_row = Table([[
        _card_wrap(period_info, "DEPARTMENT OVERVIEW", 260, accent=BRAND_GREEN),
        _score_card(round(completion_rate), CONTENT_W - 260),
    ]], colWidths=[260, CONTENT_W - 260])
    info_row.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    elements.append(info_row)

    elements.append(Spacer(1, 12))
    summary_text = (
        f"Between <b>{start_date.strftime('%d %b %Y')}</b> and <b>{end_date.strftime('%d %b %Y')}</b>, the "
        f"department logged <b>{total_sessions} session(s)</b> across <b>{active_patients} patient(s)</b>, "
        f"with a <b>{completion_rate:.0f}% completion rate</b> and <b>{device_util:.0f}% device utilization</b> "
        f"({active_devices} of {total_devices} registered devices active)."
    )
    accent_color, bg_color = BAND_COLORS[completion_band]
    summary_box = Table([[Paragraph(summary_text, summary_style)]], colWidths=[CONTENT_W])
    summary_box.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg_color),
        ("LINEBEFORE", (0, 0), (0, -1), 3, accent_color),
        ("LEFTPADDING", (0, 0), (-1, -1), 14),
        ("RIGHTPADDING", (0, 0), (-1, -1), 14),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    elements.append(summary_box)

    elements.append(Paragraph("Operational KPIs", styles_section))
    kpi_col_w = CONTENT_W / 4
    kpi_card_w = kpi_col_w - 6
    cards = [
        _kpi_card(str(total_sessions), "Total Sessions", width=kpi_card_w),
        _kpi_card(f"{completion_rate:.0f}%", "Completion Rate", completion_band, width=kpi_card_w),
        _kpi_card(str(active_patients), "Active Patients", width=kpi_card_w),
        _kpi_card(f"{device_util:.0f}%", "Device Utilization", _band(device_util), width=kpi_card_w),
    ]
    elements.append(_kpi_grid(cards, width=CONTENT_W))

    if categories:
        elements.append(Paragraph("Most Engaged Exercise Categories", styles_section))
        elements.append(RLImage(_category_bar_chart(categories), width=CONTENT_W,
                                 height=max(70, 32 * len(categories))))

        header_style = ParagraphStyle("CatHead", fontSize=7.8, textColor=colors.white,
                                       fontName="Helvetica-Bold", alignment=TA_CENTER)
        rows = [[Paragraph("Category", header_style), Paragraph("Sessions", header_style),
                 Paragraph("Share", header_style)]]
        total_cat = sum(c["count"] for c in categories) or 1
        for c in categories:
            rows.append([
                Paragraph(c["name"], cell_style),
                Paragraph(str(c["count"]), cell_style),
                Paragraph(f"{c['count'] / total_cat * 100:.0f}%", cell_style),
            ])
        t = Table(rows, colWidths=[CONTENT_W * 0.5, CONTENT_W * 0.25, CONTENT_W * 0.25], repeatRows=1)
        style = [
            ("BACKGROUND", (0, 0), (-1, 0), PDF_NAVY),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LINEBELOW", (0, 1), (-1, -1), 0.5, PDF_GREY_BORDER),
            ("ALIGN", (1, 0), (-1, -1), "CENTER"),
        ]
        for i in range(1, len(rows)):
            if i % 2 == 0:
                style.append(("BACKGROUND", (0, i), (-1, i), PDF_ROW_ALT))
        t.setStyle(TableStyle(style))
        elements.append(t)

    subtitle = f"Period: {start_date.strftime('%d %b %Y')} \u2013 {end_date.strftime('%d %b %Y')}"
    ctx = {
        "report_id": report_id, "generated_by": generated_by,
        "subtitle": subtitle, "title": "ADMINISTRATIVE REPORT",
    }

    def _make_canvas(*args, **kwargs):
        kwargs["report_ctx"] = ctx
        return _ReportCanvas(*args, **kwargs)

    doc.build(elements, canvasmaker=_make_canvas)
    return output_path