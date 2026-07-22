from __future__ import annotations

import html
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .config import load_settings
from .storage import connect, init_db, latest_snapshots_for_kpi


CHARTS = {
    "/newsletter/sent": {
        "kpi_code": "newsletter_sent_count_weekly",
        "title": "Newsletters envoyees",
        "format": "count",
        "color": "#5AA0D7",
        "axis": "Nombre d'envois",
        "segment": "newsletter",
    },
    "/newsletter/subscribers": {
        "kpi_code": "newsletter_subscribers_total_weekly",
        "title": "Abonnes newsletter",
        "format": "count",
        "color": "#C9A24A",
        "axis": "Nombre d'abonnes",
        "segment": "newsletter",
        "secondary_kpi": {
            "kpi_code": "newsletter_sent_recipients_weekly",
            "label": "Destinataires envoyes",
            "format": "count",
            "color": "#5AA0D7",
            "axis": "Destinataires envoyes",
        },
    },
    "/newsletter/open-rate": {
        "kpi_code": "newsletter_open_rate_weekly",
        "title": "Taux d'ouverture newsletter",
        "format": "percent",
        "color": "#9B6AC4",
        "axis": "Taux d'ouverture",
        "segment": "newsletter",
    },
    "/website/visits": {
        "kpi_code": "website_visits_weekly",
        "title": "Visites site web",
        "format": "count",
        "color": "#5AA0D7",
        "axis": "Nombre de visites",
        "segment": "website",
    },
    "/linkedin/posts": {
        "kpi_code": "linkedin_posts_count_weekly",
        "title": "Posts LinkedIn publies",
        "format": "count",
        "color": "#5AA0D7",
        "axis": "Nombre de posts",
        "segment": "linkedin",
        "series": True,
        "series_colors": {
            "sebastien": "#5AA0D7",
            "benjamin": "#9B6AC4",
            "cercle_mdb": "#C97091",
            "total": "#C9A24A",
        },
    },
    "/linkedin/engagement-rate": {
        "kpi_code": "linkedin_average_engagement_rate_weekly",
        "title": "Taux d'engagement moyen LinkedIn",
        "format": "percent",
        "color": "#C9A24A",
        "axis": "Taux d'engagement moyen",
        "segment": "linkedin",
    },
}


def format_value(value: float, value_format: str) -> str:
    if value_format == "percent":
        return f"{value * 100:.1f}%"
    if value == int(value):
        return f"{int(value):,}".replace(",", " ")
    return f"{value:.1f}"


def chart_points(rows: list[dict], width: int, height: int, padding_x: int = 72, padding_y: int = 54) -> tuple[list[tuple[float, float]], float, float]:
    if not rows:
        return [], 0, 0
    values = [float(row["value"]) for row in rows]
    min_value = min(values)
    max_value = max(values)
    if min_value == max_value:
        padding = max(abs(max_value) * 0.01, 1)
        min_value = min_value - padding
        max_value = max_value + padding
        if min_value < 0 <= values[0]:
            min_value = 0
    plot_width = width - padding_x * 2
    plot_height = height - padding_y * 2
    denominator = max(1, len(rows) - 1)
    points = []
    for index, row in enumerate(rows):
        value = float(row["value"])
        x = padding_x + (index / denominator) * plot_width
        y = padding_y + (1 - ((value - min_value) / (max_value - min_value))) * plot_height
        points.append((x, y))
    return points, min_value, max_value


def chart_points_with_scale(
    rows: list[dict],
    width: int,
    height: int,
    min_value: float,
    max_value: float,
    padding_x: int = 72,
    padding_y: int = 54,
) -> list[tuple[float, float]]:
    plot_width = width - padding_x * 2
    plot_height = height - padding_y * 2
    denominator = max(1, len(rows) - 1)
    points = []
    for index, row in enumerate(rows):
        value = float(row["value"])
        x = padding_x + (index / denominator) * plot_width
        y = padding_y + (1 - ((value - min_value) / (max_value - min_value))) * plot_height
        points.append((x, y))
    return points


def smooth_path(points: list[tuple[float, float]]) -> str:
    if not points:
        return ""
    if len(points) == 1:
        x, y = points[0]
        return f"M 72.0 {y:.1f} L 888.0 {y:.1f}"
    path = [f"M {points[0][0]:.1f} {points[0][1]:.1f}"]
    for index in range(len(points) - 1):
        p0 = points[index - 1] if index > 0 else points[index]
        p1 = points[index]
        p2 = points[index + 1]
        p3 = points[index + 2] if index + 2 < len(points) else p2
        c1x = p1[0] + (p2[0] - p0[0]) / 6
        c1y = p1[1] + (p2[1] - p0[1]) / 6
        c2x = p2[0] - (p3[0] - p1[0]) / 6
        c2y = p2[1] - (p3[1] - p1[1]) / 6
        path.append(f"C {c1x:.1f} {c1y:.1f}, {c2x:.1f} {c2y:.1f}, {p2[0]:.1f} {p2[1]:.1f}")
    return " ".join(path)


def padded_bounds(values: list[float]) -> tuple[float, float]:
    if not values:
        return 0, 1
    min_value = min(values)
    max_value = max(values)
    if min_value == max_value:
        if min_value >= 0:
            padding = max(abs(max_value) * 0.01, 1)
            return max(0, min_value - padding), max_value + padding
        return min_value - 1, max_value + 1
    padding = (max_value - min_value) * 0.08
    if min_value >= 0:
        min_value = max(0, min_value - padding)
    else:
        min_value -= padding
    return min_value, max_value + padding


def render_svg(rows: list[dict], title: str, value_format: str, color: str, y_axis: str) -> str:
    width = 960
    height = 460
    points, min_value, max_value = chart_points(rows, width, height)
    if not rows:
        return '<div class="empty">Aucune donnee disponible pour ce KPI.</div>'

    latest = rows[-1]
    first_label = html.escape(rows[0]["period_start"])
    last_label = html.escape(rows[-1]["period_start"])
    latest_value = format_value(float(latest["value"]), value_format)
    max_label = format_value(max_value, value_format)
    mid_label = format_value((max_value + min_value) / 2, value_format)
    min_label = format_value(min_value, value_format)
    path = smooth_path(points)

    circles = []
    hover_bands = []
    denominator = max(1, len(rows) - 1)
    band_width = 816 / denominator if len(rows) > 1 else 816
    for x_y, row in zip(points, rows):
        x, y = x_y
        label = f"{row['period_start']} - {format_value(float(row['value']), value_format)}"
        circles.append(f'<circle class="dot" cx="{x:.1f}" cy="{y:.1f}" r="4" />')
        band_x = max(72, x - band_width / 2)
        hover_bands.append(
            f'<rect class="hover-band" x="{band_x:.1f}" y="54" width="{band_width:.1f}" height="352" data-label="{html.escape(label)}" />'
        )

    tick_indexes = sorted(set([0, max(0, len(rows) // 2), len(rows) - 1]))
    x_ticks = []
    for index in tick_indexes:
        x, _ = points[index]
        x_ticks.append(f'<text x="{x:.1f}" y="432" class="tick x-label" text-anchor="middle">{html.escape(rows[index]["period_start"][5:])}</text>')

    return f"""
    <div class="summary">
      <div>
        <div class="label">Derniere valeur</div>
        <div class="value">{html.escape(latest_value)}</div>
      </div>
      <div>
        <div class="label">Periode</div>
        <div class="period">{first_label} -> {last_label}</div>
      </div>
    </div>
    <div class="chart-wrap">
      <svg viewBox="0 0 {width} {height}" role="img" aria-label="{html.escape(title)}">
        <defs>
          <linearGradient id="lineGradient" x1="0" x2="1" y1="0" y2="0">
            <stop offset="0%" stop-color="#5AA0D7" />
            <stop offset="52%" stop-color="#9B6AC4" />
            <stop offset="100%" stop-color="#C9A24A" />
          </linearGradient>
        </defs>
        <line x1="72" y1="54" x2="72" y2="406" class="axis" />
        <line x1="72" y1="406" x2="888" y2="406" class="axis" />
        <line x1="72" y1="54" x2="888" y2="54" class="grid" />
        <line x1="72" y1="230" x2="888" y2="230" class="grid" />
        <line x1="72" y1="406" x2="888" y2="406" class="grid" />
        <text x="64" y="58" class="tick" text-anchor="end">{html.escape(max_label)}</text>
        <text x="64" y="234" class="tick" text-anchor="end">{html.escape(mid_label)}</text>
        <text x="64" y="410" class="tick" text-anchor="end">{html.escape(min_label)}</text>
        {''.join(x_ticks)}
        <text x="480" y="454" class="axis-title" text-anchor="middle">Semaines</text>
        <text x="18" y="230" class="axis-title" text-anchor="middle" transform="rotate(-90 18 230)">{html.escape(y_axis)}</text>
        <path d="{path}" fill="none" stroke="url(#lineGradient)" stroke-width="4.5" stroke-linecap="round" stroke-linejoin="round" />
        <g fill="{color}">{''.join(circles)}</g>
        <g>{''.join(hover_bands)}</g>
      </svg>
      <div id="tooltip" class="tooltip" hidden></div>
    </div>
    """


def render_svg_dual_axis(
    primary_rows: list[dict],
    secondary_rows: list[dict],
    title: str,
    primary_config: dict,
    secondary_config: dict,
) -> str:
    width = 960
    height = 460
    if not primary_rows:
        return '<div class="empty">Aucune donnee disponible pour ce KPI.</div>'

    secondary_by_period = {row["period_start"]: row for row in secondary_rows}
    aligned_secondary_rows = [
        {
            "period_start": row["period_start"],
            "period_end": row["period_end"],
            "value": float(secondary_by_period.get(row["period_start"], {}).get("value", 0)),
        }
        for row in primary_rows
    ]

    primary_points, primary_min, primary_max = chart_points(primary_rows, width, height)
    secondary_values = [float(row["value"]) for row in aligned_secondary_rows]
    secondary_min, secondary_max = padded_bounds(secondary_values)
    secondary_points = chart_points_with_scale(aligned_secondary_rows, width, height, secondary_min, secondary_max)

    primary_format = primary_config["format"]
    secondary_format = secondary_config["format"]
    primary_color = primary_config["color"]
    secondary_color = secondary_config["color"]
    latest_primary = primary_rows[-1]
    latest_secondary = aligned_secondary_rows[-1]
    first_label = html.escape(primary_rows[0]["period_start"])
    last_label = html.escape(primary_rows[-1]["period_start"])

    primary_path = smooth_path(primary_points)
    secondary_path = smooth_path(secondary_points)

    primary_max_label = format_value(primary_max, primary_format)
    primary_mid_label = format_value((primary_max + primary_min) / 2, primary_format)
    primary_min_label = format_value(primary_min, primary_format)
    secondary_max_label = format_value(secondary_max, secondary_format)
    secondary_mid_label = format_value((secondary_max + secondary_min) / 2, secondary_format)
    secondary_min_label = format_value(secondary_min, secondary_format)

    circles = []
    for x, y in primary_points:
        circles.append(f'<circle class="dot" cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{primary_color}" />')
    for x, y in secondary_points:
        circles.append(f'<circle class="dot" cx="{x:.1f}" cy="{y:.1f}" r="3.5" fill="{secondary_color}" />')

    denominator = max(1, len(primary_rows) - 1)
    band_width = 816 / denominator if len(primary_rows) > 1 else 816
    hover_bands = []
    for index, row in enumerate(primary_rows):
        x, _ = primary_points[index]
        sent_value = float(aligned_secondary_rows[index]["value"])
        label = (
            f"{row['period_start']} - "
            f"Abonnes: {format_value(float(row['value']), primary_format)} | "
            f"Destinataires envoyes: {format_value(sent_value, secondary_format)}"
        )
        band_x = max(72, x - band_width / 2)
        hover_bands.append(
            f'<rect class="hover-band" x="{band_x:.1f}" y="54" width="{band_width:.1f}" height="352" data-label="{html.escape(label)}" />'
        )

    tick_indexes = sorted(set([0, max(0, len(primary_rows) // 2), len(primary_rows) - 1]))
    x_ticks = []
    for index in tick_indexes:
        x, _ = primary_points[index]
        x_ticks.append(f'<text x="{x:.1f}" y="432" class="tick x-label" text-anchor="middle">{html.escape(primary_rows[index]["period_start"][5:])}</text>')

    return f"""
    <div class="summary">
      <div>
        <div class="label">Derniere valeur abonnes</div>
        <div class="value">{html.escape(format_value(float(latest_primary["value"]), primary_format))}</div>
      </div>
      <div>
        <div class="label">Destinataires envoyes semaine</div>
        <div class="value secondary-value">{html.escape(format_value(float(latest_secondary["value"]), secondary_format))}</div>
      </div>
      <div>
        <div class="label">Periode</div>
        <div class="period">{first_label} -> {last_label}</div>
      </div>
    </div>
    <div class="legend">
      <span class="legend-item"><i style="background:{primary_color}"></i>Abonnes newsletter</span>
      <span class="legend-item"><i style="background:{secondary_color}"></i>{html.escape(secondary_config["label"])}</span>
    </div>
    <div class="chart-wrap">
      <svg viewBox="0 0 {width} {height}" role="img" aria-label="{html.escape(title)}">
        <line x1="72" y1="54" x2="72" y2="406" class="axis" />
        <line x1="888" y1="54" x2="888" y2="406" class="axis" />
        <line x1="72" y1="406" x2="888" y2="406" class="axis" />
        <line x1="72" y1="54" x2="888" y2="54" class="grid" />
        <line x1="72" y1="230" x2="888" y2="230" class="grid" />
        <line x1="72" y1="406" x2="888" y2="406" class="grid" />
        <text x="64" y="58" class="tick" text-anchor="end">{html.escape(primary_max_label)}</text>
        <text x="64" y="234" class="tick" text-anchor="end">{html.escape(primary_mid_label)}</text>
        <text x="64" y="410" class="tick" text-anchor="end">{html.escape(primary_min_label)}</text>
        <text x="896" y="58" class="tick secondary-tick" text-anchor="start">{html.escape(secondary_max_label)}</text>
        <text x="896" y="234" class="tick secondary-tick" text-anchor="start">{html.escape(secondary_mid_label)}</text>
        <text x="896" y="410" class="tick secondary-tick" text-anchor="start">{html.escape(secondary_min_label)}</text>
        {''.join(x_ticks)}
        <text x="480" y="454" class="axis-title" text-anchor="middle">Semaines</text>
        <text x="18" y="230" class="axis-title" text-anchor="middle" transform="rotate(-90 18 230)">{html.escape(primary_config["axis"])}</text>
        <text x="942" y="230" class="axis-title axis-title-secondary" text-anchor="middle" transform="rotate(90 942 230)">{html.escape(secondary_config["axis"])}</text>
        <path d="{primary_path}" fill="none" stroke="{primary_color}" stroke-width="4.5" stroke-linecap="round" stroke-linejoin="round" />
        <path d="{secondary_path}" fill="none" stroke="{secondary_color}" stroke-width="3.8" stroke-linecap="round" stroke-linejoin="round" opacity=".92" />
        {''.join(circles)}
        <g>{''.join(hover_bands)}</g>
      </svg>
      <div id="tooltip" class="tooltip" hidden></div>
    </div>
    """


def render_svg_series(rows: list[dict], title: str, value_format: str, colors: dict[str, str], y_axis: str) -> str:
    width = 960
    height = 460
    if not rows:
        return '<div class="empty">Aucune donnee disponible pour ce KPI.</div>'

    periods = []
    period_labels = {}
    for row in rows:
        period = row["period_start"]
        if period not in period_labels:
            periods.append(period)
            period_labels[period] = row["period_end"]

    owners = [owner for owner in ["sebastien", "benjamin", "cercle_mdb", "total"] if any((row.get("owner") or "") == owner for row in rows)]
    if not owners:
        owners = sorted({row.get("owner") or "serie" for row in rows})

    values_by_owner_period = {
        (row.get("owner") or "serie", row["period_start"]): float(row["value"])
        for row in rows
    }
    all_values = list(values_by_owner_period.values()) or [0.0]
    min_value = min(all_values)
    max_value = max(all_values)
    if min_value == max_value:
        min_value = 0 if max_value >= 0 else min_value - 1
        max_value = max_value + 1

    max_label = format_value(max_value, value_format)
    mid_label = format_value((max_value + min_value) / 2, value_format)
    min_label = format_value(min_value, value_format)
    first_label = html.escape(periods[0])
    last_label = html.escape(periods[-1])
    total_latest = values_by_owner_period.get(("total", periods[-1]), sum(values_by_owner_period.get((owner, periods[-1]), 0) for owner in owners))

    series_paths = []
    dots = []
    labels = {
        "sebastien": "Sebastien",
        "benjamin": "Benjamin",
        "cercle_mdb": "Le Cercle MDB",
        "total": "Total",
    }
    for owner in owners:
        owner_rows = [
            {"period_start": period, "value": values_by_owner_period.get((owner, period), 0.0)}
            for period in periods
        ]
        points = chart_points_with_scale(owner_rows, width, height, min_value, max_value)
        stroke = colors.get(owner, "#5AA0D7")
        series_paths.append(
            f'<path d="{smooth_path(points)}" fill="none" stroke="{stroke}" stroke-width="4.2" stroke-linecap="round" stroke-linejoin="round" />'
        )
        for x, y in points:
            dots.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" fill="{stroke}" />')

    denominator = max(1, len(periods) - 1)
    band_width = 816 / denominator if len(periods) > 1 else 816
    hover_bands = []
    for index, period in enumerate(periods):
        x = 72 + (index / denominator) * 816
        parts = [f"{labels.get(owner, owner)}: {format_value(values_by_owner_period.get((owner, period), 0), value_format)}" for owner in owners]
        label = f"{period} - " + " | ".join(parts)
        band_x = max(72, x - band_width / 2)
        hover_bands.append(
            f'<rect class="hover-band" x="{band_x:.1f}" y="54" width="{band_width:.1f}" height="352" data-label="{html.escape(label)}" />'
        )

    tick_indexes = sorted(set([0, max(0, len(periods) // 2), len(periods) - 1]))
    x_ticks = []
    for index in tick_indexes:
        x = 72 + (index / denominator) * 816
        x_ticks.append(f'<text x="{x:.1f}" y="432" class="tick x-label" text-anchor="middle">{html.escape(periods[index][5:])}</text>')

    legend = "".join(
        f'<span class="legend-item"><i style="background:{colors.get(owner, "#5AA0D7")}"></i>{html.escape(labels.get(owner, owner))}</span>'
        for owner in owners
    )

    return f"""
    <div class="summary">
      <div>
        <div class="label">Derniere valeur totale</div>
        <div class="value">{html.escape(format_value(total_latest, value_format))}</div>
      </div>
      <div>
        <div class="label">Periode</div>
        <div class="period">{first_label} -> {last_label}</div>
      </div>
    </div>
    <div class="legend">{legend}</div>
    <div class="chart-wrap">
      <svg viewBox="0 0 {width} {height}" role="img" aria-label="{html.escape(title)}">
        <line x1="72" y1="54" x2="72" y2="406" class="axis" />
        <line x1="72" y1="406" x2="888" y2="406" class="axis" />
        <line x1="72" y1="54" x2="888" y2="54" class="grid" />
        <line x1="72" y1="230" x2="888" y2="230" class="grid" />
        <line x1="72" y1="406" x2="888" y2="406" class="grid" />
        <text x="64" y="58" class="tick" text-anchor="end">{html.escape(max_label)}</text>
        <text x="64" y="234" class="tick" text-anchor="end">{html.escape(mid_label)}</text>
        <text x="64" y="410" class="tick" text-anchor="end">{html.escape(min_label)}</text>
        {''.join(x_ticks)}
        <text x="480" y="454" class="axis-title" text-anchor="middle">Semaines</text>
        <text x="18" y="230" class="axis-title" text-anchor="middle" transform="rotate(-90 18 230)">{html.escape(y_axis)}</text>
        {''.join(series_paths)}
        {''.join(dots)}
        <g>{''.join(hover_bands)}</g>
      </svg>
      <div id="tooltip" class="tooltip" hidden></div>
    </div>
    """


def render_page(path: str, rows: list[dict], related_rows: dict[str, list[dict]] | None = None) -> str:
    config = CHARTS[path]
    title = config["title"]
    if config.get("secondary_kpi"):
        secondary_config = config["secondary_kpi"]
        chart = render_svg_dual_axis(
            rows,
            (related_rows or {}).get(secondary_config["kpi_code"], []),
            title,
            config,
            secondary_config,
        )
    elif config.get("series"):
        chart = render_svg_series(rows, title, config["format"], config.get("series_colors", {}), config["axis"])
    else:
        chart = render_svg(rows, title, config["format"], config["color"], config["axis"])
    return f"""<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{ color-scheme: dark; font-family: Inter, Arial, sans-serif; }}
    body {{ margin: 0; background: #101223; color: #F8FAFC; }}
    main {{ padding: 22px 26px 18px; background: radial-gradient(circle at 85% 10%, rgba(201,162,74,.16), transparent 34%), #101223; min-height: 100vh; box-sizing: border-box; }}
    h1 {{ font-size: 25px; line-height: 1.2; margin: 0 0 14px; letter-spacing: .01em; }}
    .summary {{ display: flex; gap: 28px; align-items: baseline; margin-bottom: 8px; }}
    .legend {{ display: flex; gap: 16px; flex-wrap: wrap; margin: 0 0 12px; color: #A7ADC3; font-size: 13px; }}
    .legend-item {{ display: inline-flex; align-items: center; gap: 7px; }}
    .legend-item i {{ width: 22px; height: 4px; border-radius: 999px; display: inline-block; }}
    .label {{ font-size: 12px; color: #7C8299; text-transform: uppercase; letter-spacing: .08em; }}
    .value {{ font-size: 30px; font-weight: 750; color: {config["color"]}; }}
    .secondary-value {{ color: #5AA0D7; }}
    .period {{ font-size: 14px; color: #A7ADC3; }}
    .chart-wrap {{ position: relative; border: 1px solid #252A48; border-radius: 8px; background: #151832; padding: 8px 10px 2px; }}
    svg {{ width: 100%; height: auto; display: block; }}
    .axis {{ stroke: #333957; stroke-width: 1.5; }}
    .grid {{ stroke: #252A48; stroke-width: 1; }}
    .tick {{ fill: #8B91A8; font-size: 13px; }}
    .axis-title {{ fill: #C9A24A; font-size: 13px; font-weight: 700; letter-spacing: .04em; }}
    .axis-title-secondary {{ fill: #5AA0D7; }}
    .secondary-tick {{ fill: #8FBEE3; }}
    .dot {{ filter: drop-shadow(0 0 5px rgba(255,255,255,.16)); }}
    .hover-band {{ fill: transparent; cursor: crosshair; }}
    .tooltip {{ position: absolute; pointer-events: none; transform: translate(-50%, -112%); background: #0C0E1C; color: #F8FAFC; border: 1px solid #C9A24A; border-radius: 7px; padding: 8px 10px; font-size: 13px; white-space: nowrap; box-shadow: 0 8px 22px rgba(0,0,0,.28); }}
    .empty {{ border: 1px solid #252A48; border-radius: 8px; padding: 18px; color: #A7ADC3; background: #151832; }}
  </style>
</head>
<body>
  <main>
    <h1>{html.escape(title)}</h1>
    {chart}
  </main>
  <script>
    const tooltip = document.getElementById('tooltip');
    document.querySelectorAll('.hover-band').forEach((band) => {{
      band.addEventListener('mousemove', (event) => {{
        const wrap = band.closest('.chart-wrap').getBoundingClientRect();
        tooltip.textContent = band.dataset.label;
        tooltip.hidden = false;
        tooltip.style.left = `${{event.clientX - wrap.left}}px`;
        tooltip.style.top = `${{event.clientY - wrap.top}}px`;
      }});
      band.addEventListener('mouseleave', () => {{ tooltip.hidden = true; }});
    }});
  </script>
</body>
</html>"""


def rows_as_dicts(rows) -> list[dict]:
    return [dict(row) for row in rows]


class DashboardHandler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: str, content_type: str = "text/html; charset=utf-8") -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._send(200, json.dumps({"ok": True}), "application/json")
            return
        if parsed.path == "/":
            links = "".join(f'<li><a href="{path}">{html.escape(config["title"])}</a></li>' for path, config in CHARTS.items())
            self._send(200, f"<h1>KPI Dashboard</h1><ul>{links}</ul>")
            return
        if parsed.path not in CHARTS:
            self._send(404, "Not found", "text/plain; charset=utf-8")
            return
        query = parse_qs(parsed.query)
        limit = int(query.get("weeks", ["52"])[0])
        settings = load_settings()
        connection = connect(settings.database_path)
        init_db(connection)
        chart = CHARTS[parsed.path]
        rows = latest_snapshots_for_kpi(connection, chart["kpi_code"], limit=limit, segment=chart["segment"])
        related_rows = {}
        if chart.get("secondary_kpi"):
            secondary = chart["secondary_kpi"]
            secondary_rows = latest_snapshots_for_kpi(connection, secondary["kpi_code"], limit=limit, segment=chart["segment"])
            related_rows[secondary["kpi_code"]] = rows_as_dicts(secondary_rows)
        self._send(200, render_page(parsed.path, rows_as_dicts(rows), related_rows))


def run_server(host: str = "127.0.0.1", port: int = 8000) -> None:
    server = ThreadingHTTPServer((host, port), DashboardHandler)
    print(f"Dashboard disponible sur http://{host}:{port}")
    server.serve_forever()


def main() -> None:
    host = os.environ.get("KPI_HOST", "127.0.0.1")
    port = int(os.environ.get("KPI_PORT", "8000"))
    run_server(host, port)


if __name__ == "__main__":
    main()
