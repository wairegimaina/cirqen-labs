"""CalSoft.pdf_generators.drift — historical drift fetch, metrics and drift-analysis section."""
"""CalSoft.pdf_generators — the B-12 hospital certificate generator + thin wrapper."""
import base64
import calendar
import io
import os
from datetime import datetime, timedelta
from decimal import Decimal
try:
    from zoneinfo import ZoneInfo
    EAT = ZoneInfo('Africa/Nairobi')
except ImportError:
    import pytz
    EAT = pytz.timezone('Africa/Nairobi')
import matplotlib
matplotlib.use('Agg')
import logging
import matplotlib.pyplot as plt
import numpy as np
import qrcode
from core.eat import fmt_eat
from django.conf import settings
from django.core.files.storage import default_storage
from django.utils import timezone
from PIL import Image as PILImage
from reportlab.graphics import renderPDF
from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.charts.linecharts import HorizontalLineChart
from reportlab.graphics.shapes import Drawing, Line, Rect
from reportlab.lib import colors
from reportlab.lib.colors import HexColor, black, blue, gray, green, red, white
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4, letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm, inch, mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import BaseDocTemplate, CondPageBreak, Frame, Image, KeepTogether, PageBreak, PageTemplate, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
logger = logging.getLogger(__name__)

# sibling modules in this package
from .signatures import SignatureImageLoader
from .watermark import _LogoWatermarkCanvas
from CalSoft.utils import grade_drift



class DriftMixin:
    _DRIFT_COLORS = {
        'pass_bg':   HexColor('#d1fae5'),
        'pass_text': HexColor('#065f46'),
        'fail_bg':   HexColor('#fee2e2'),
        'fail_text': HexColor('#991b1b'),
        'warn_bg':   HexColor('#fef3c7'),
        'warn_text': HexColor('#92400e'),
        'hdr_bg':    HexColor('#0f172a'),
        'row_alt':   HexColor('#f8fafc'),
        'border':    HexColor('#cbd5e1'),
        'label_bg':  HexColor('#f1f5f9'),
    }

    def _fetch_historical_drift_data(self):
        """
        Fetch drift data from ALL previous CalibrationSession records for the same
        device serial, grouped by (parameter_name, sub_parameter_name).

        Each entry carries per-reading metadata including pass/fail and the
        overall session-level pass flag so we can surface last-session outcomes.

        Returns:
            dict: {(param_name, sub_param_name): [reading_dict, ...]}
        """
        try:
            from CalSoft.models import CalibrationReading, CalibrationSession

            device_serial = getattr(self.session, 'device_serial', None)
            if not device_serial:
                return {}

            previous_sessions = (
                CalibrationSession.objects
                .filter(
                    device_serial=device_serial,
                    timestamp__lt=self.session.timestamp,
                    active_status=True,
                )
                .exclude(id=self.session.id)
                .order_by('timestamp')
            )

            if not previous_sessions.exists():
                return {}

            grouped = {}

            for sess in previous_sessions:
                cert_num = getattr(sess, 'certificate_number', None) or '\u2014'
                sess_overall_pass = getattr(sess, 'overall_pass', None)

                readings = (
                    CalibrationReading.objects
                    .filter(session=sess, active_status=True)
                    .select_related('parameter', 'sub_parameter', 'set_value')
                )
                for r in readings:
                    if r.error is None:
                        continue
                    param_name = r.parameter.name if r.parameter else 'Unknown'
                    sub_name   = r.sub_parameter.name if r.sub_parameter else ''
                    key = (param_name, sub_name)
                    if key not in grouped:
                        grouped[key] = []
                    # The tolerance travels with the reading because a drift
                    # rate can only be graded as a fraction of it.
                    tolerance = None
                    if r.sub_parameter and r.sub_parameter.tolerance is not None:
                        tolerance = r.sub_parameter.tolerance
                    elif r.parameter and r.parameter.tolerance is not None:
                        tolerance = r.parameter.tolerance
                    grouped[key].append({
                        'tolerance':            tolerance,
                        'session_id':           str(sess.id),
                        'session_date':         sess.timestamp,
                        'session_cert':         cert_num,
                        'session_overall_pass': sess_overall_pass,
                        'error':                float(r.error),
                        'mean':                 float(r.mean) if r.mean is not None else None,
                        'uncertainty':          float(r.expanded_uncertainty) if r.expanded_uncertainty is not None else 0.0,
                        'passes':               bool(r.passes_tolerance),
                    })

            for key in grouped:
                grouped[key].sort(key=lambda x: x['session_date'])

            return grouped

        except Exception as e:
            logger.error(f"[DRIFT] Error fetching historical drift data: {e}")
            return {}

    @staticmethod
    def _compute_drift_metrics(readings):
        """
        Linear-regression drift analysis over a list of reading dicts.
        Returns a rich metrics dict including last-session pass/fail details.
        """
        n = len(readings)
        if n < 2:
            return None

        errors    = [r['error'] for r in readings]
        base_date = readings[0]['session_date']
        days      = [(r['session_date'] - base_date).days for r in readings]

        sum_x  = sum(days)
        sum_y  = sum(errors)
        sum_xy = sum(x * y for x, y in zip(days, errors))
        sum_xx = sum(x * x for x in days)
        denom  = n * sum_xx - sum_x ** 2

        drift_rate_per_day  = (n * sum_xy - sum_x * sum_y) / denom if denom != 0 else 0.0
        drift_rate_per_year = drift_rate_per_day * 365
        intercept           = (sum_y - drift_rate_per_day * sum_x) / n
        total_drift         = errors[-1] - errors[0]

        if abs(drift_rate_per_day) < 1e-8:
            direction = 'Stable'
        elif drift_rate_per_day > 0:
            direction = 'Increasing \u25b2'
        else:
            direction = 'Decreasing \u25bc'

        # Grade as a fraction of this parameter's own tolerance. The previous
        # thresholds (0.1 / 0.5 / 1.0 per year) were absolute, so the same
        # grade was applied to mmHg, mV, degrees C and mL/min alike — and that
        # grade drove the printed interval recommendation below.
        tolerance = next(
            (r.get('tolerance') for r in readings if r.get('tolerance') is not None),
            None,
        )
        stability, stability_advice, tolerance_fraction = grade_drift(
            drift_rate_per_year, tolerance
        )

        mean_y = sum_y / n
        ss_tot = sum((y - mean_y) ** 2 for y in errors)
        if ss_tot > 0:
            ss_res    = sum((errors[i] - (drift_rate_per_day * days[i] + intercept)) ** 2 for i in range(n))
            r_squared = max(0.0, 1 - ss_res / ss_tot)
        else:
            r_squared = 1.0

        uncertainties = [r['uncertainty'] for r in readings]
        avg_unc    = sum(uncertainties) / n
        half       = max(n // 2, 1)
        first_avg  = sum(uncertainties[:half]) / half
        second_avg = sum(uncertainties[half:]) / max(n - half, 1)
        if second_avg > first_avg * 1.2:
            unc_trend = 'Increasing'
        elif second_avg < first_avg * 0.8:
            unc_trend = 'Decreasing'
        else:
            unc_trend = 'Stable'

        fail_count = sum(1 for r in readings if not r['passes'])
        pass_count = n - fail_count
        fail_rate  = fail_count / n

        last                = readings[-1]
        last_reading_pass   = last['passes']
        last_session_pass   = last['session_overall_pass']
        last_cert           = last['session_cert']
        last_date           = last['session_date']
        last_error          = last['error']
        last_uncertainty    = last['uncertainty']

        return {
            'drift_rate_per_day':  drift_rate_per_day,
            'drift_rate_per_year': drift_rate_per_year,
            'total_drift':         total_drift,
            'direction':           direction,
            'stability':           stability,
            'stability_advice':    stability_advice,
            'tolerance':           tolerance,
            'tolerance_fraction':  tolerance_fraction,
            'r_squared':           r_squared,
            'uncertainty_trend':   unc_trend,
            'avg_uncertainty':     avg_unc,
            'n_sessions':          n,
            'fail_count':          fail_count,
            'pass_count':          pass_count,
            'fail_rate':           fail_rate,
            'first_date':          readings[0]['session_date'],
            'last_date':           last_date,
            'last_cert':           last_cert,
            'last_reading_pass':   last_reading_pass,
            'last_session_pass':   last_session_pass,
            'last_error':          last_error,
            'last_uncertainty':    last_uncertainty,
        }

    def build_drift_analysis_content(self):
        """
        Build a single comprehensive drift analysis table that includes:
          - Device-level last-session PASS / FAIL banner
          - Six-cell summary strip (sessions, params, failures, drift rate, stability, date range)
          - Colour-coded recommendation bar
          - One unified per-parameter table with all metrics and last-session status
          - Legend footnote
        """
        elements = []
        DC = self._DRIFT_COLORS

        historical = self._fetch_historical_drift_data()

        if not historical:
            empty = Table(
                [[Paragraph(
                    "No previous calibration sessions found for this device serial. "
                    "Drift analysis becomes available once a second session is recorded.",
                    self.styles['DriftNormal']
                )]],
                colWidths=[6.9 * inch]
            )
            empty.setStyle(TableStyle([
                ('BACKGROUND',    (0, 0), (-1, -1), DC['label_bg']),
                ('BOX',           (0, 0), (-1, -1), 0.5, DC['border']),
                ('LEFTPADDING',   (0, 0), (-1, -1), 10),
                ('RIGHTPADDING',  (0, 0), (-1, -1), 10),
                ('TOPPADDING',    (0, 0), (-1, -1), 10),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
            ]))
            elements.append(empty)
            return elements

        all_metrics = []
        for key, readings in historical.items():
            m = self._compute_drift_metrics(readings)
            if m:
                m['param_name'] = key[0]
                m['sub_param']  = key[1]
                all_metrics.append(m)

        if not all_metrics:
            elements.append(Paragraph(
                "Insufficient repeated readings across sessions to compute drift "
                "(need \u2265 2 sessions with matching parameters).",
                self.styles['DriftNormal']
            ))
            return elements

        # ── BANNER: last-session outcome ──────────────────────────────────────
        any_last_fail     = any(not m['last_reading_pass'] for m in all_metrics)
        overall_last_pass = all_metrics[0]['last_session_pass']

        if overall_last_pass is False or any_last_fail:
            b_bg, b_tc  = DC['fail_bg'], DC['fail_text']
            b_label     = "\u26a0  LAST CALIBRATION SESSION: FAILED"
            b_sub       = ("One or more parameters did not pass tolerance in the most recent "
                           "historical session. Refer to per-parameter details below.")
        elif overall_last_pass is True:
            b_bg, b_tc  = DC['pass_bg'], DC['pass_text']
            b_label     = "\u2714  LAST CALIBRATION SESSION: PASSED"
            b_sub       = "All parameters were within tolerance in the most recent historical session."
        else:
            b_bg, b_tc  = DC['warn_bg'], DC['warn_text']
            b_label     = "\u25cf  LAST SESSION STATUS: UNKNOWN"
            b_sub       = "Pass/fail outcome was not recorded on the most recent historical session."

        last_cert_shown = all_metrics[0]['last_cert']
        last_date_shown = fmt_eat(all_metrics[0]['last_date'], '%d %b %Y')

        b_title_s = ParagraphStyle('_bt', fontSize=10, fontName='Helvetica-Bold',
                                   textColor=b_tc, leading=14)
        b_sub_s   = ParagraphStyle('_bs', fontSize=7.5, fontName='Helvetica',
                                   textColor=b_tc, leading=11)
        b_meta_s  = ParagraphStyle('_bm', fontSize=8, fontName='Helvetica',
                                   textColor=b_tc, leading=12, alignment=TA_RIGHT)

        banner = Table(
            [[Paragraph(f"{b_label}<br/><font size=\'7.5\'>{b_sub}</font>", b_title_s),
              Paragraph(f"<b>Cert:</b> {last_cert_shown}<br/><b>Date:</b> {last_date_shown}",
                        b_meta_s)]],
            colWidths=[5.0 * inch, 1.9 * inch]
        )
        banner.setStyle(TableStyle([
            ('BACKGROUND',    (0, 0), (-1, -1), b_bg),
            ('BOX',           (0, 0), (-1, -1), 1.5, b_tc),
            ('LEFTPADDING',   (0, 0), (-1, -1), 8),
            ('RIGHTPADDING',  (0, 0), (-1, -1), 8),
            ('TOPPADDING',    (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        elements.append(banner)
        elements.append(Spacer(1, 5))

        # ── SUMMARY STRIP ─────────────────────────────────────────────────────
        total_sessions = max(m['n_sessions'] for m in all_metrics)
        earliest       = min(m['first_date'] for m in all_metrics)
        latest         = max(m['last_date']  for m in all_metrics)
        total_fails    = sum(m['fail_count']  for m in all_metrics)
        total_readings = sum(m['n_sessions']  for m in all_metrics)

        # Drift is summarised as a fraction of tolerance, which is
        # dimensionless and therefore comparable across parameters. Averaging
        # absolute rates in different units — as this did — produces a number
        # with no meaning.
        graded = [m for m in all_metrics if m.get('tolerance_fraction') is not None]
        worst = max(graded, key=lambda m: m['tolerance_fraction']) if graded else None

        if worst is not None:
            worst_pct = float(worst['tolerance_fraction']) * 100
            summary_value = f"{worst_pct:.0f}% of tol"
        else:
            summary_value = "\u2014"

        # The overall grade is the worst parameter's, not an average: one
        # parameter drifting toward its limit is the thing worth surfacing, and
        # averaging hides it.
        GRADE_STYLE = {
            'Very stable': (DC['pass_bg'], DC['pass_text']),
            'Normal':      (DC['pass_bg'], DC['pass_text']),
            'Drifting':    (DC['warn_bg'], DC['warn_text']),
            'Urgent':      (DC['fail_bg'], DC['fail_text']),
            'Ungraded':    (DC['label_bg'], DC['warn_text']),
        }
        ovr_label = worst['stability'] if worst is not None else 'Ungraded'
        ovr_bg, ovr_tc = GRADE_STYLE.get(ovr_label, (DC['label_bg'], DC['warn_text']))

        if worst is not None:
            recommendation = worst['stability_advice']
        else:
            recommendation = (
                "No tolerance on record for these parameters, so the drift rate "
                "cannot be graded and no interval change is recommended."
            )

        lbl_s  = ParagraphStyle('_sl', fontSize=7, fontName='Helvetica-Bold',
                                textColor=HexColor('#64748b'), alignment=TA_CENTER)
        val_s  = ParagraphStyle('_sv', fontSize=9.5, fontName='Helvetica-Bold',
                                textColor=HexColor('#0f172a'), alignment=TA_CENTER)
        fval_s = ParagraphStyle('_sf', fontSize=9.5, fontName='Helvetica-Bold',
                                textColor=DC['fail_text'] if total_fails else DC['pass_text'],
                                alignment=TA_CENTER)
        oval_s = ParagraphStyle('_so', fontSize=9.5, fontName='Helvetica-Bold',
                                textColor=ovr_tc, alignment=TA_CENTER)

        strip_w = 6.9 * inch / 6
        strip_top = [
            Paragraph('SESSIONS', lbl_s), Paragraph('PARAMETERS', lbl_s),
            Paragraph('FAIL HISTORY', lbl_s), Paragraph('WORST DRIFT', lbl_s),
            Paragraph('OVERALL STABILITY', lbl_s), Paragraph('DATE RANGE', lbl_s),
        ]
        strip_bot = [
            Paragraph(str(total_sessions), val_s),
            Paragraph(str(len(all_metrics)), val_s),
            Paragraph(f"{total_fails} / {total_readings}", fval_s),
            Paragraph(summary_value, val_s),
            Paragraph(ovr_label, oval_s),
            Paragraph(f"{fmt_eat(earliest, '%d %b %y')} \u2192 {fmt_eat(latest, '%d %b %y')}", val_s),
        ]

        strip = Table([strip_top, strip_bot], colWidths=[strip_w] * 6)
        strip.setStyle(TableStyle([
            ('BACKGROUND',    (0, 0), (-1, -1), DC['label_bg']),
            ('BACKGROUND',    (2, 0), (2, 1),
             DC['fail_bg'] if total_fails else DC['pass_bg']),
            ('BACKGROUND',    (4, 0), (4, 1), ovr_bg),
            ('BOX',           (0, 0), (-1, -1), 0.5, DC['border']),
            ('INNERGRID',     (0, 0), (-1, -1), 0.5, DC['border']),
            ('ALIGN',         (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING',    (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))
        elements.append(strip)
        elements.append(Spacer(1, 3))

        # Recommendation bar
        rec_bg, rec_tc = ovr_bg, ovr_tc
        rec_s  = ParagraphStyle('_rec', fontSize=8, fontName='Helvetica-Bold',
                                textColor=rec_tc, alignment=TA_LEFT)
        rec_tbl = Table(
            [[Paragraph(f"Recommendation: {recommendation}", rec_s)]],
            colWidths=[6.9 * inch]
        )
        rec_tbl.setStyle(TableStyle([
            ('BACKGROUND',    (0, 0), (-1, -1), rec_bg),
            ('BOX',           (0, 0), (-1, -1), 0.5, rec_tc),
            ('LEFTPADDING',   (0, 0), (-1, -1), 8),
            ('RIGHTPADDING',  (0, 0), (-1, -1), 8),
            ('TOPPADDING',    (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))
        elements.append(rec_tbl)
        elements.append(Spacer(1, 8))

        # ── MAIN UNIFIED PARAMETER TABLE ──────────────────────────────────────
        # Columns: Parameter | Sub-Param | Sessions | Last Status | Fail History |
        #          Last Error ±Unc | Drift/Year | Direction | Stability | R²
        COL_W = [1.10*inch, 0.82*inch, 0.50*inch, 0.68*inch, 0.72*inch,
                 0.88*inch, 0.72*inch, 0.88*inch, 0.72*inch, 0.54*inch]

        def hdr(txt):
            return Paragraph(txt, ParagraphStyle(
                '_h', fontSize=7, fontName='Helvetica-Bold',
                textColor=colors.white, alignment=TA_CENTER, leading=9))

        def cell(txt, align=TA_CENTER, bold=False, tc=HexColor('#1e293b')):
            fn = 'Helvetica-Bold' if bold else 'Helvetica'
            return Paragraph(txt, ParagraphStyle(
                '_c', fontSize=7.5, fontName=fn,
                textColor=tc, alignment=align, leading=10))

        def badge(txt, tc, bg):
            inner = Table([[Paragraph(txt, ParagraphStyle(
                '_ib', fontSize=7.5, fontName='Helvetica-Bold',
                textColor=tc, alignment=TA_CENTER, leading=10))]],
                colWidths=[0.60 * inch])
            inner.setStyle(TableStyle([
                ('BACKGROUND',    (0, 0), (-1, -1), bg),
                ('LEFTPADDING',   (0, 0), (-1, -1), 2),
                ('RIGHTPADDING',  (0, 0), (-1, -1), 2),
                ('TOPPADDING',    (0, 0), (-1, -1), 1),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
            ]))
            return inner

        tbl_rows   = [[hdr('Parameter'), hdr('Sub-\nParam'), hdr('Sess.'),
                       hdr('Last\nStatus'), hdr('Fail\nHistory'),
                       hdr('Last Err\n\u00b1Unc'), hdr('Drift\n/Year'),
                       hdr('Direction'), hdr('Stability'), hdr('R\u00b2')]]
        row_styles = []

        dir_colors = {
            'Increasing \u25b2': (DC['fail_text'], HexColor('#fff1f2')),
            'Decreasing \u25bc': (DC['warn_text'], HexColor('#fffbeb')),
            'Stable':             (DC['pass_text'], HexColor('#f0fdf4')),
        }
        stab_colors = {
            'Very stable': (DC['pass_text'], DC['pass_bg']),
            'Normal':      (DC['pass_text'], DC['pass_bg']),
            'Drifting':    (DC['warn_text'], DC['warn_bg']),
            'Urgent':      (DC['fail_text'], DC['fail_bg']),
            'Ungraded':    (DC['warn_text'], DC['label_bg']),
        }

        for i, m in enumerate(all_metrics, start=1):
            row_bg = colors.white if i % 2 == 0 else DC['row_alt']

            # Last status badge
            if m['last_reading_pass']:
                lst_tc, lst_bg = DC['pass_text'], DC['pass_bg']
                lst_lbl = 'PASS'
            else:
                lst_tc, lst_bg = DC['fail_text'], DC['fail_bg']
                lst_lbl = 'FAIL'

            # Fail history badge
            fp = m['fail_rate'] * 100
            if fp == 0:
                fh_tc, fh_bg = DC['pass_text'], DC['pass_bg']
            elif fp < 40:
                fh_tc, fh_bg = DC['warn_text'], DC['warn_bg']
            else:
                fh_tc, fh_bg = DC['fail_text'], DC['fail_bg']
            fh_lbl = (f"{m['fail_count']}/{m['n_sessions']}"
                      if m['fail_count'] == 0
                      else f"{m['fail_count']}/{m['n_sessions']}\n({fp:.0f}%)")

            d_tc, d_bg = dir_colors.get(m['direction'], (HexColor('#0f172a'), colors.white))
            s_tc, s_bg = stab_colors.get(m['stability'], (HexColor('#0f172a'), colors.white))

            tbl_rows.append([
                cell(m['param_name'], align=TA_LEFT),
                cell(m['sub_param'] or '\u2014'),
                cell(str(m['n_sessions'])),
                badge(lst_lbl,        lst_tc, lst_bg),
                badge(fh_lbl,         fh_tc,  fh_bg),
                cell(f"{m['last_error']:+.5f}\n\u00b1{m['last_uncertainty']:.5f}"),
                cell(
                    f"{m['drift_rate_per_year']:+.5f}"
                    + (f"\n({float(m['tolerance_fraction']) * 100:.0f}% of tol)"
                       if m.get('tolerance_fraction') is not None else "")
                ),
                badge(m['direction'],  d_tc,   d_bg),
                badge(m['stability'],  s_tc,   s_bg),
                cell(f"{m['r_squared']:.3f}"),
            ])
            row_styles.append(('BACKGROUND', (0, i), (-1, i), row_bg))

        base_style = [
            ('BACKGROUND',    (0, 0), (-1, 0), DC['hdr_bg']),
            ('TEXTCOLOR',     (0, 0), (-1, 0), colors.white),
            ('ALIGN',         (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
            ('LINEBELOW',     (0, 0), (-1, 0), 1.0, HexColor('#334155')),
            ('GRID',          (0, 0), (-1, -1), 0.4, DC['border']),
            ('LEFTPADDING',   (0, 0), (-1, -1), 3),
            ('RIGHTPADDING',  (0, 0), (-1, -1), 3),
            ('TOPPADDING',    (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ] + row_styles

        main_tbl = Table(tbl_rows, colWidths=COL_W)
        main_tbl.setStyle(TableStyle(base_style))
        elements.append(main_tbl)

        # Legend footnote
        elements.append(Spacer(1, 3))
        elements.append(Paragraph(
            "Last Status = most recent reading pass/fail per parameter  |  "
            "Fail History = failed readings across all sessions  |  "
            "Drift/Year = linear regression rate, with its size as a percentage "
            "of that parameter's tolerance  |  "
            "Stability = graded on that percentage, so parameters in different "
            "units are comparable  |  "
            "R\u00b2 = regression fit confidence (1.000 = perfect)",
            ParagraphStyle('_leg', fontSize=6.5, fontName='Helvetica',
                           textColor=HexColor('#94a3b8'), leading=9)
        ))

        return elements
