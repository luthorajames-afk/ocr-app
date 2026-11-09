#!/usr/bin/env python3
"""Générateur du jeu de documents de test — Phase 0 du projet OCR.

Ce script produit un jeu synthétique de 42 documents (PDF, JPG, PNG) destiné à
l'évaluation des moteurs OCR de l'application hors ligne. Il ne dépend que de
``pypdfium2``, ``pillow`` et ``numpy`` (les seules bibliothèques installées).

Principes
---------
* Les documents « natifs » sont de vrais PDF vectoriels : le texte est ajouté
  avec l'API d'édition de PDFium (accessible via ``pypdfium2.raw``), il est donc
  réellement sélectionnable et extractible, avec les accents français.
* Les documents « scannés » sont rendus en bitmap avec Pillow puis dégradés
  (rotation, contraste, flou, bruit, éclairage inégal, JPEG, poussières).
* Les PDF « mixtes » combinent des pages natives et des pages image dans un
  même fichier (``PdfDocument.import_pages``).
* Chaque document est livré avec sa vérité terrain : une liste de blocs
  (type, texte, bbox en pixels) dans ``manifest.json`` et un fichier texte
  lisible par page dans ``truth/``.

Coordonnées
-----------
Les bboxes sont exprimées en pixels, origine en haut à gauche, sur le rendu du
document à 150 DPI. Une page A4 portrait mesure 1240 × 1754 px ; une page A4
paysage 1754 × 1240 px. Pour les scans, les bboxes sont transformées après
rotation (boîte englobante alignée sur les axes), puis rognées aux bords de la
page.

Usage
-----
    python facts_generator.py                 # (re)génère le jeu complet
    python facts_generator.py --validate-only # vérifie le manifest existant
    python facts_generator.py --seed 123      # autre variante des données

Le résultat est écrit dans le dossier de ce script, jamais ailleurs.
"""

from __future__ import annotations

import argparse
import copy
import ctypes
import io
import json
import math
import random
import re
import shutil
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
import pypdfium2 as pdfium
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps
from pypdfium2 import raw

# ---------------------------------------------------------------------------
# Constantes générales
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
DPI = 150
PX_TO_PT = 72.0 / DPI
A4_PORTRAIT = (1240, 1754)       # A4 @ 150 DPI
A4_LANDSCAPE = (1754, 1240)      # A4 paysage @ 150 DPI
MARGIN = 92
SEED_DEFAULT = 20261109

# Palette sobre, proche de documents administratifs réels.
INK = (28, 28, 30)
MUTED = (92, 92, 98)
ACCENT = (27, 58, 105)
ACCENT_2 = (164, 55, 44)
ACCENT_3 = (36, 105, 92)
LIGHT = (238, 241, 246)
LIGHT_2 = (246, 243, 236)
RULE = (150, 156, 166)
RULE_DARK = (90, 96, 108)
PAPER = (252, 251, 248)
WHITE = (255, 255, 255)

# Chemins de polices Windows (le projet est une application Windows hors ligne).
_WINDIR = Path(__import__("os").environ.get("WINDIR", r"C:\Windows"))
FONT_DIR = _WINDIR / "Fonts"

FONT_FILES: dict[str, tuple[str, ...]] = {
    "sans": ("arial.ttf", "DejaVuSans.ttf"),
    "sans_bold": ("arialbd.ttf", "DejaVuSans-Bold.ttf"),
    "sans_italic": ("ariali.ttf", "DejaVuSans-Oblique.ttf"),
    "sans_bolditalic": ("arialbi.ttf", "DejaVuSans-BoldOblique.ttf"),
    "serif": ("times.ttf", "DejaVuSerif.ttf"),
    "serif_bold": ("timesbd.ttf", "DejaVuSerif-Bold.ttf"),
    "serif_italic": ("timesi.ttf", "DejaVuSerif-Italic.ttf"),
    "serif_bolditalic": ("timesbi.ttf", "DejaVuSerif-BoldItalic.ttf"),
    "mono": ("cour.ttf", "DejaVuSansMono.ttf"),
    "mono_bold": ("courbd.ttf", "DejaVuSansMono-Bold.ttf"),
    "calibri": ("calibri.ttf", "DejaVuSans.ttf"),
    "calibri_bold": ("calibrib.ttf", "DejaVuSans-Bold.ttf"),
    "georgia": ("georgia.ttf", "DejaVuSerif.ttf"),
    "georgia_bold": ("georgiab.ttf", "DejaVuSerif-Bold.ttf"),
}

# Correspondance vers les polices standard PDF (encodage WinAnsi : accents,
# cédilles, œ, €, guillemets et tirets cadratins sont pris en charge).
PDF_FONTS: dict[str, str] = {
    "sans": "Helvetica",
    "sans_bold": "Helvetica-Bold",
    "sans_italic": "Helvetica-Oblique",
    "sans_bolditalic": "Helvetica-BoldOblique",
    "serif": "Times-Roman",
    "serif_bold": "Times-Bold",
    "serif_italic": "Times-Italic",
    "serif_bolditalic": "Times-BoldItalic",
    "mono": "Courier",
    "mono_bold": "Courier-Bold",
    # Les polices Windows sont remplacées par leur équivalent standard dans les
    # pages natives ; les pages raster, elles, utilisent la police réelle.
    "calibri": "Helvetica",
    "calibri_bold": "Helvetica-Bold",
    "georgia": "Times-Roman",
    "georgia_bold": "Times-Bold",
}

_FONT_CACHE: dict[tuple[str, int], ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}


def _font_path(key: str) -> Path | None:
    for name in FONT_FILES.get(key, FONT_FILES["sans"]):
        for candidate in (FONT_DIR / name, Path(name)):
            if candidate.exists():
                return candidate
    return None


def load_font(key: str, size_pt: float):
    """Charge (et met en cache) une police pour un corps donné en points."""
    px = max(5, int(round(size_pt * DPI / 72.0)))
    cache_key = (key, px)
    if cache_key in _FONT_CACHE:
        return _FONT_CACHE[cache_key]
    path = _font_path(key)
    try:
        if path is None:
            raise OSError("police introuvable")
        font = ImageFont.truetype(str(path), px)
    except OSError:
        try:
            font = ImageFont.load_default(size=px)
        except TypeError:  # Pillow < 10
            font = ImageFont.load_default()
    _FONT_CACHE[cache_key] = font
    return font


def wrap_text(text: str, font, max_width: float | None) -> list[str]:
    """Découpe un texte en lignes en respectant les mots et les largeurs."""
    if not text:
        return [""]
    paragraphs = str(text).split("\n")
    lines: list[str] = []
    for paragraph in paragraphs:
        if max_width is None:
            lines.append(paragraph)
            continue
        words = paragraph.split(" ")
        current = ""
        for word in words:
            trial = word if not current else current + " " + word
            if font.getlength(trial) <= max_width or not current:
                current = trial
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines or [""]


def line_height(size_pt: float, line_spacing: float = 1.32) -> int:
    return max(1, int(round(size_pt * DPI / 72.0 * line_spacing)))


def text_height(text: str, font_key: str, size_pt: float, width: float,
                line_spacing: float = 1.32) -> float:
    font = load_font(font_key, size_pt)
    lines = wrap_text(text, font, width)
    return len(lines) * line_height(size_pt, line_spacing)


def _rgba(color: Sequence[int]) -> tuple[int, int, int, int]:
    if len(color) >= 4:
        return int(color[0]), int(color[1]), int(color[2]), int(color[3])
    return int(color[0]), int(color[1]), int(color[2]), 255


def _bbox(x0: float, y0: float, x1: float, y1: float) -> list[int]:
    return [int(round(x0)), int(round(y0)), int(round(x1)), int(round(y1))]


# ---------------------------------------------------------------------------
# Modèle de page et primitives de mise en page
# ---------------------------------------------------------------------------


@dataclass
class PageLayout:
    """Une page décrite indépendamment du rendu (natif ou bitmap).

    Les éléments sont des dictionnaires simples :
    ``{"kind": "text"|"rect"|"line", ...}``. Les blocs et tableaux accumulés
    constituent la vérité terrain de la page (coordonnées en pixels).
    """

    width: int
    height: int
    orientation: str = "portrait"
    elements: list[dict[str, Any]] = field(default_factory=list)
    blocks: list[dict[str, Any]] = field(default_factory=list)
    tables: list[dict[str, Any]] = field(default_factory=list)

    # -- primitives graphiques ------------------------------------------------

    def rect(self, x: float, y: float, w: float, h: float, *,
             fill: Sequence[int] | None = None,
             stroke: Sequence[int] | None = None,
             width: float = 1.0) -> None:
        self.elements.append({
            "kind": "rect", "box": (x, y, w, h),
            "fill": tuple(fill) if fill else None,
            "stroke": tuple(stroke) if stroke else None,
            "width": float(width),
        })

    def line(self, x0: float, y0: float, x1: float, y1: float, *,
             color: Sequence[int] = INK, width: float = 1.0) -> None:
        self.elements.append({
            "kind": "line", "p0": (x0, y0), "p1": (x1, y1),
            "color": tuple(color), "width": float(width),
        })

    def hline(self, x0: float, x1: float, y: float, **kwargs) -> None:
        self.line(x0, y, x1, y, **kwargs)

    def vline(self, x: float, y0: float, y1: float, **kwargs) -> None:
        self.line(x, y0, x, y1, **kwargs)

    # -- texte ----------------------------------------------------------------

    def text(self, text: str, x: float, y: float, width: float | None = None, *,
             font: str = "sans", size: float = 9.5,
             color: Sequence[int] = INK, line_spacing: float = 1.32,
             align: str = "left", block_type: str = "paragraph",
             role: str | None = None, record: bool = True,
             bbox_full_width: bool = False,
             max_lines: int | None = None) -> dict[str, Any] | None:
        """Ajoute un texte (éventuellement multiligne) et enregistre un bloc.

        ``width`` active le retour à la ligne. ``bbox_full_width`` force la
        bbox du bloc à couvrir toute la largeur de colonne (utile pour les
        paragraphes, dont la justification irrégulière ne doit pas fausser
        l'évaluation).
        """
        text = str(text)
        if not text.strip():
            return None
        f = load_font(font, size)
        lines = wrap_text(text, f, width) if width else text.split("\n")
        if max_lines is not None:
            lines = lines[:max_lines]
        lh = line_height(size, line_spacing)
        ascent, descent = f.getmetrics()
        drawn: list[dict[str, Any]] = []
        for index, line_text in enumerate(lines):
            line_width = f.getlength(line_text)
            if align == "center" and width is not None:
                lx = x + (width - line_width) / 2.0
            elif align == "right" and width is not None:
                lx = x + width - line_width
            else:
                lx = x
            ly = y + index * lh
            box = ImageDraw.Draw(Image.new("RGB", (1, 1))).textbbox(
                (lx, ly), line_text, font=f, anchor="la"
            )
            drawn.append({
                "text": line_text, "x": lx, "y": ly,
                "bbox": tuple(float(v) for v in box),
                "ascent": float(ascent),
            })
        self.elements.append({
            "kind": "text", "lines": drawn, "font": font,
            "size": float(size), "color": tuple(color),
        })
        if not record:
            return None
        top = min(item["bbox"][1] for item in drawn)
        bottom = max(item["bbox"][3] for item in drawn)
        left = x if (bbox_full_width and width is not None) else min(
            item["bbox"][0] for item in drawn
        )
        right = (x + width) if (bbox_full_width and width is not None) else max(
            item["bbox"][2] for item in drawn
        )
        block: dict[str, Any] = {
            "type": block_type,
            "text": text,
            "bbox": _bbox(left, top, right, bottom),
        }
        if role:
            block["role"] = role
        self.blocks.append(block)
        return block

    # -- cases à cocher -------------------------------------------------------

    def checkbox(self, x: float, y: float, label: str, checked: bool = False, *,
                 box_size: float = 13.0, font: str = "sans", size: float = 8.5,
                 color: Sequence[int] = INK,
                 record: bool = True) -> dict[str, Any] | None:
        self.rect(x, y, box_size, box_size, fill=None, stroke=color, width=0.9)
        if checked:
            inset = max(2.0, box_size * 0.18)
            self.line(x + inset, y + inset, x + box_size - inset,
                      y + box_size - inset, color=color, width=1.2)
            self.line(x + box_size - inset, y + inset, x + inset,
                      y + box_size - inset, color=color, width=1.2)
        label_block = self.text(
            label, x + box_size + 8, y - 1, font=font, size=size,
            record=False,
        )
        if not record:
            return None
        if label_block is None:
            return None
        right = max(x + box_size + 8 + load_font(font, size).getlength(label),
                    x + box_size)
        block = {
            "type": "paragraph",
            "text": label,
            "bbox": _bbox(x, min(y, label_block["bbox"][1]), right,
                          max(y + box_size, label_block["bbox"][3])),
            "role": "checkbox",
            "checked": bool(checked),
        }
        self.blocks.append(block)
        return block

    # -- tableaux -------------------------------------------------------------

    def table(self, x: float, y: float, col_widths: Sequence[float],
              rows: Sequence[Sequence[str]], *, header: bool = True,
              font: str = "sans", size: float = 8.6,
              header_font: str = "sans_bold",
              header_bg: Sequence[int] = ACCENT,
              header_color: Sequence[int] = WHITE,
              grid_color: Sequence[int] = RULE,
              zebra: bool = False,
              aligns: Sequence[str] | None = None,
              cell_pad_x: float = 6.0, cell_pad_y: float = 4.5,
              line_width: float = 0.7,
              record: bool = True,
              role: str | None = "table") -> dict[str, Any] | None:
        """Dessine un tableau et enregistre un bloc unique de type ``table``.

        Le texte du bloc est la concaténation des lignes, cellules séparées par
        ``" | "``. La grille complète est également exposée dans
        ``PageLayout.tables`` pour l'évaluation TEDS ultérieure.
        """
        rows = [[str(cell) for cell in row] for row in rows]
        if not rows:
            return None
        cols = len(col_widths)
        aligns = list(aligns or ["left"] * cols)
        body_font = load_font(font, size)
        head_font = load_font(header_font, size)
        lh = line_height(size, 1.2)
        row_heights: list[float] = []
        for row_index, row in enumerate(rows):
            is_header = header and row_index == 0
            current_font = head_font if is_header else body_font
            max_h = 0.0
            for col_index in range(cols):
                cell = row[col_index] if col_index < len(row) else ""
                lines = wrap_text(cell, current_font,
                                  col_widths[col_index] - 2 * cell_pad_x)
                max_h = max(max_h, len(lines) * lh + 2 * cell_pad_y)
            row_heights.append(max_h)
        table_w = sum(col_widths)
        table_h = sum(row_heights)
        # Fond d'en-tête et zébrures.
        cursor_y = y
        for row_index, row_h in enumerate(row_heights):
            if header and row_index == 0:
                self.rect(x, cursor_y, table_w, row_h, fill=header_bg)
            elif zebra and (row_index - (1 if header else 0)) % 2 == 1:
                self.rect(x, cursor_y, table_w, row_h, fill=LIGHT)
            cursor_y += row_h
        # Grille.
        self.rect(x, y, table_w, table_h, fill=None, stroke=grid_color,
                  width=line_width)
        cursor_y = y
        for row_h in row_heights:
            self.hline(x, x + table_w, cursor_y, color=grid_color,
                       width=line_width)
            cursor_y += row_h
        cursor_x = x
        for col_w in col_widths[:-1]:
            cursor_x += col_w
            self.vline(cursor_x, y, y + table_h, color=grid_color,
                       width=line_width)
        # Contenu.
        cursor_y = y
        for row_index, row in enumerate(rows):
            is_header = header and row_index == 0
            current_font_key = header_font if is_header else font
            current_color = header_color if is_header else INK
            cursor_x = x
            for col_index in range(cols):
                cell = row[col_index] if col_index < len(row) else ""
                self.text(
                    cell, cursor_x + cell_pad_x, cursor_y + cell_pad_y,
                    width=col_widths[col_index] - 2 * cell_pad_x,
                    font=current_font_key, size=size, color=current_color,
                    align=aligns[col_index], line_spacing=1.2, record=False,
                )
                cursor_x += col_widths[col_index]
            cursor_y += row_heights[row_index]
        if not record:
            return None
        text = "\n".join(" | ".join(row) for row in rows)
        block = {
            "type": "table",
            "text": text,
            "bbox": _bbox(x, y, x + table_w, y + table_h),
        }
        if role:
            block["role"] = role
        self.blocks.append(block)
        self.tables.append({
            "bbox": list(block["bbox"]),
            "rows": len(rows),
            "cols": cols,
            "header": bool(header),
            "grid": rows,
        })
        return block


# ---------------------------------------------------------------------------
# Rendu bitmap (scans, pages image)
# ---------------------------------------------------------------------------


def render_page_raster(page: PageLayout, *, paper: Sequence[int] = PAPER,
                       seed: int = 0) -> Image.Image:
    """Rend une page en bitmap à 150 DPI avec Pillow."""
    img = Image.new("RGB", (page.width, page.height), tuple(paper))
    draw = ImageDraw.Draw(img)
    for element in page.elements:
        kind = element["kind"]
        if kind == "rect":
            x, y, w, h = element["box"]
            fill = element["fill"]
            stroke = element["stroke"]
            width = max(1, int(round(element["width"])))
            draw.rectangle(
                (x, y, x + w, y + h),
                fill=tuple(fill) if fill else None,
                outline=tuple(stroke) if stroke else None,
                width=width if stroke else 0,
            )
        elif kind == "line":
            (x0, y0), (x1, y1) = element["p0"], element["p1"]
            draw.line((x0, y0, x1, y1), fill=tuple(element["color"]),
                      width=max(1, int(round(element["width"]))))
        elif kind == "text":
            font = load_font(element["font"], element["size"])
            for line in element["lines"]:
                draw.text((line["x"], line["y"]), line["text"], font=font,
                          fill=tuple(element["color"]), anchor="la")
    # Grain de papier très léger pour éviter les aplats parfaits.
    if seed:
        rng = np.random.default_rng(seed)
        arr = np.asarray(img).astype(np.float32)
        arr += rng.normal(0.0, 2.0, (page.height, page.width, 1))
        img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")
    return img


def _affine_transform_box(box: Sequence[float], forward, left: int, top: int,
                          width: int, height: int) -> list[int] | None:
    x0, y0, x1, y1 = box
    points = [forward(x0, y0), forward(x1, y0), forward(x1, y1), forward(x0, y1)]
    nx0 = min(p[0] for p in points) - left
    nx1 = max(p[0] for p in points) - left
    ny0 = min(p[1] for p in points) - top
    ny1 = max(p[1] for p in points) - top
    nx0 = max(0.0, min(float(width), nx0))
    nx1 = max(0.0, min(float(width), nx1))
    ny0 = max(0.0, min(float(height), ny0))
    ny1 = max(0.0, min(float(height), ny1))
    if nx1 - nx0 < 2 or ny1 - ny0 < 2:
        return None
    return _bbox(nx0, ny0, nx1, ny1)


def rotate_page(img: Image.Image, blocks: list[dict[str, Any]],
                tables: list[dict[str, Any]], angle_deg: float,
                *, background: Sequence[int] = (214, 211, 205),
                ) -> tuple[Image.Image, list[dict[str, Any]], list[dict[str, Any]]]:
    """Tourne une page et transforme ses bboxes dans le repère final.

    La page source est inclinée sur un fond de scanner gris, puis rognée au
    format d'origine (comme une page mal posée sur la vitre). L'angle positif
    correspond à une rotation dans le sens anti-horaire visuel.
    """
    if abs(angle_deg) < 0.01:
        return img, blocks, tables
    width, height = img.size
    theta = math.radians(angle_deg)
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    cx, cy = width / 2.0, height / 2.0
    out_w = int(math.ceil(abs(width * cos_t) + abs(height * sin_t))) + 2
    out_h = int(math.ceil(abs(width * sin_t) + abs(height * cos_t))) + 2
    out_cx, out_cy = out_w / 2.0, out_h / 2.0

    def forward(x: float, y: float) -> tuple[float, float]:
        return (cos_t * (x - cx) + sin_t * (y - cy) + out_cx,
                -sin_t * (x - cx) + cos_t * (y - cy) + out_cy)

    # Coefficients inverses pour Image.transform (sortie -> entrée).
    a = cos_t
    b = -sin_t
    c = cx - cos_t * out_cx + sin_t * out_cy
    d = sin_t
    e = cos_t
    f = cy - sin_t * out_cx - cos_t * out_cy
    rotated = img.transform(
        (out_w, out_h), Image.AFFINE, (a, b, c, d, e, f),
        resample=Image.BICUBIC, fillcolor=tuple(background),
    )
    left = (out_w - width) // 2
    top = (out_h - height) // 2
    rotated = rotated.crop((left, top, left + width, top + height))

    new_blocks: list[dict[str, Any]] = []
    for block in blocks:
        new_box = _affine_transform_box(block["bbox"], forward, left, top,
                                        width, height)
        if new_box is None:
            continue
        new_block = dict(block)
        new_block["bbox"] = new_box
        new_blocks.append(new_block)
    new_tables: list[dict[str, Any]] = []
    for table in tables:
        new_box = _affine_transform_box(table["bbox"], forward, left, top,
                                        width, height)
        if new_box is None:
            continue
        new_table = dict(table)
        new_table["bbox"] = new_box
        new_tables.append(new_table)
    return rotated, new_blocks, new_tables


def _scan_notes(effects: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    if effects.get("rotation"):
        notes.append(f"rotation {effects['rotation']:+.1f}°")
    if effects.get("downsample", 1.0) < 1.0:
        notes.append(f"basse résolution ×{effects['downsample']:.2f}")
    if effects.get("grayscale"):
        notes.append("niveaux de gris")
    if effects.get("contrast", 1.0) < 1.0:
        notes.append(f"contraste {effects['contrast']:.2f}")
    if effects.get("brightness", 1.0) != 1.0:
        notes.append(f"luminosité {effects['brightness']:.2f}")
    if effects.get("blur", 0.0) > 0:
        notes.append(f"flou {effects['blur']:.1f} px")
    if effects.get("noise", 0.0) > 0:
        notes.append(f"bruit σ={effects['noise']:.1f}")
    if effects.get("illumination", 0.0) > 0:
        notes.append(f"éclairage inégal {effects['illumination']:.2f}")
    if effects.get("vignette", 0.0) > 0:
        notes.append(f"vignettage {effects['vignette']:.2f}")
    if effects.get("dust", 0) > 0:
        notes.append(f"{effects['dust']} poussières")
    if effects.get("jpeg_quality"):
        notes.append(f"JPEG q={effects['jpeg_quality']}")
    if effects.get("stamp"):
        notes.append(f"tampon « {effects['stamp']} »")
    return notes


def apply_scan_effects(img: Image.Image, effects: dict[str, Any], *,
                       seed: int) -> Image.Image:
    """Applique les dégradations d'un scan (hors rotation géométrique)."""
    rng = np.random.default_rng(seed)
    width, height = img.size

    factor = float(effects.get("downsample", 1.0))
    if factor < 1.0:
        small = img.resize((max(1, int(width * factor)),
                            max(1, int(height * factor))), Image.LANCZOS)
        img = small.resize((width, height), Image.BICUBIC)

    if effects.get("grayscale"):
        img = ImageOps.grayscale(img).convert("RGB")

    contrast = float(effects.get("contrast", 1.0))
    if contrast != 1.0:
        img = ImageEnhance.Contrast(img).enhance(contrast)

    brightness = float(effects.get("brightness", 1.0))
    if brightness != 1.0:
        img = ImageEnhance.Brightness(img).enhance(brightness)

    illumination = float(effects.get("illumination", 0.0))
    vignette = float(effects.get("vignette", 0.0))
    if illumination or vignette:
        arr = np.asarray(img).astype(np.float32)
        yy, xx = np.mgrid[0:height, 0:width]
        gx = xx / max(1, width - 1)
        gy = yy / max(1, height - 1)
        if illumination:
            # Lumière venant du coin supérieur gauche, assombrissement progressif.
            light = 1.0 + illumination * (0.55 - 0.70 * gx - 0.40 * gy)
            arr *= light[..., None]
        if vignette:
            radius = ((gx - 0.5) ** 2 + (gy - 0.5) ** 2) * 2.0
            arr *= (1.0 - vignette * np.clip(radius, 0.0, 1.0))[..., None]
        img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")

    noise = float(effects.get("noise", 0.0))
    if noise > 0:
        arr = np.asarray(img).astype(np.float32)
        arr += rng.normal(0.0, noise, (height, width, 1))
        img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")

    blur = float(effects.get("blur", 0.0))
    if blur > 0:
        img = img.filter(ImageFilter.GaussianBlur(blur))

    dust = int(effects.get("dust", 0))
    if dust > 0:
        draw = ImageDraw.Draw(img)
        for _ in range(dust):
            x = rng.integers(0, width - 3)
            y = rng.integers(0, height - 3)
            radius = int(rng.choice([0, 0, 1, 1, 2]))
            color = tuple(int(v) for v in rng.choice(
                [(30, 30, 30), (80, 80, 80), (205, 205, 200),
                 (160, 140, 110), (120, 90, 60)]
            ))
            draw.ellipse((x, y, x + radius, y + radius), fill=color)
        # Quelques rayures fines.
        for _ in range(max(1, dust // 12)):
            x = rng.integers(0, width)
            y = rng.integers(0, height)
            draw.line((x, y, x + rng.integers(8, 90), y + rng.integers(-3, 4)),
                      fill=(110, 110, 110), width=1)

    stamp = effects.get("stamp")
    if stamp:
        img = add_stamp(img, str(stamp),
                        angle=float(effects.get("stamp_angle", 28.0)),
                        alpha=int(effects.get("stamp_alpha", 55)))

    quality = effects.get("jpeg_quality")
    if quality:
        buffer = io.BytesIO()
        img.save(buffer, "JPEG", quality=int(quality), optimize=True)
        buffer.seek(0)
        img = Image.open(buffer).convert("RGB")
    return img


def add_stamp(img: Image.Image, text: str, *, angle: float = 28.0,
              color: Sequence[int] = (170, 42, 38),
              alpha: int = 55) -> Image.Image:
    """Ajoute un tampon incliné semi-transparent (DUPLICATA, PAYÉ, etc.)."""
    font = load_font("sans_bold", 42)
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1))).textbbox(
        (0, 0), text, font=font
    )
    text_w, text_h = probe[2] - probe[0], probe[3] - probe[1]
    layer = Image.new("RGBA", (text_w + 100, text_h + 70), (0, 0, 0, 0))
    layer_draw = ImageDraw.Draw(layer)
    layer_draw.rectangle(
        (10, 10, layer.width - 10, layer.height - 10),
        outline=tuple(color) + (alpha,), width=4,
    )
    layer_draw.text((50 - probe[0], 35 - probe[1]), text, font=font,
                    fill=tuple(color) + (alpha,))
    layer = layer.rotate(angle, expand=True, resample=Image.BICUBIC)
    base = img.convert("RGBA")
    x = (img.width - layer.width) // 2
    y = (img.height - layer.height) // 2
    base.alpha_composite(layer, (max(0, x), max(0, y)))
    return base.convert("RGB")


# ---------------------------------------------------------------------------
# Rendu PDF natif (texte réellement extractible)
# ---------------------------------------------------------------------------


class NativePageWriter:
    """Écrit les éléments d'une PageLayout dans une page PDFium."""

    def __init__(self, doc: pdfium.PdfDocument, page: pdfium.PdfPage,
                 layout: PageLayout,
                 font_cache: dict[str, Any] | None = None,
                 font_buffers: list[Any] | None = None):
        self.doc = doc
        self.page = page
        self.layout = layout
        self.font_cache = font_cache if font_cache is not None else {}
        self.font_buffers = (font_buffers if font_buffers is not None else [])

    def _font(self, key: str):
        """Charge la police TrueType réelle et l'embarque dans le PDF.

        Les polices standard Helvetica/Times de PDFium provoquent des espaces
        « générés » (code 0xFF, extraits comme ÿ) entre les objets texte situés
        sur la même ligne, notamment dans les mises en page multicolonnes. Une
        police TrueType embarquée donne un texte propre, des métriques exactes
        et une extraction fiable.
        """
        if key in self.font_cache:
            return self.font_cache[key]
        path = _font_path(key)
        if path is not None:
            data = path.read_bytes()
            buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
            handle = raw.FPDFText_LoadFont(
                self.doc.raw, buffer, len(data), 2, True  # 2 = TrueType, CID
            )
            self.font_buffers.append(buffer)
            if handle:
                self.font_cache[key] = handle
                return handle
        # Repli : police standard si le fichier TTF est absent.
        name = PDF_FONTS.get(key, "Helvetica")
        cache_key = f"standard:{name}"
        if cache_key not in self.font_cache:
            handle = raw.FPDFText_LoadStandardFont(
                self.doc.raw, name.encode("ascii")
            )
            if not handle:
                raise RuntimeError(f"Impossible de charger la police PDF {name}")
            self.font_cache[cache_key] = handle
        return self.font_cache[cache_key]

    def _set_fill(self, obj, color: Sequence[int]) -> None:
        r, g, b, a = _rgba(color)
        raw.FPDFPageObj_SetFillColor(obj, r, g, b, a)

    def _set_stroke(self, obj, color: Sequence[int]) -> None:
        r, g, b, a = _rgba(color)
        raw.FPDFPageObj_SetStrokeColor(obj, r, g, b, a)

    def add(self, element: dict[str, Any]) -> None:
        kind = element["kind"]
        if kind == "text":
            self._add_text(element)
        elif kind == "rect":
            self._add_rect(element)
        elif kind == "line":
            self._add_line(element)
        else:  # pragma: no cover - garde-fou
            raise ValueError(f"Élément inconnu : {kind}")

    def _add_text(self, element: dict[str, Any]) -> None:
        font_handle = self._font(element["font"])
        size = float(element["size"])
        color = element["color"]
        page_height = self.layout.height
        for line in element["lines"]:
            if not line["text"]:
                continue
            obj = raw.FPDFPageObj_CreateTextObj(
                self.doc.raw, font_handle, ctypes.c_float(size)
            )
            if not obj:
                raise RuntimeError("Création d'objet texte impossible")
            # create_unicode_buffer fournit une terminaison UTF-16 correcte
            # (deux octets nuls). Un create_string_buffer n'ajoute qu'un octet
            # nul et peut faire lire PDFium au-delà de la chaîne, ce qui
            # produit des glyphes manquants (carrés) et des caractères parasites.
            buffer = ctypes.create_unicode_buffer(line["text"])
            ok = raw.FPDFText_SetText(
                obj, ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ushort))
            )
            if not ok:
                raise RuntimeError(f"PDFium refuse le texte : {line['text']!r}")
            self._set_fill(obj, color)
            x_pt = line["x"] * PX_TO_PT
            baseline_px = line["y"] + line["ascent"]
            y_pt = (page_height - baseline_px) * PX_TO_PT
            raw.FPDFPageObj_Transform(obj, 1.0, 0.0, 0.0, 1.0, x_pt, y_pt)
            if not raw.FPDFPage_InsertObject(self.page.raw, obj):
                raise RuntimeError("Insertion d'objet texte impossible")

    def _add_rect(self, element: dict[str, Any]) -> None:
        x, y, w, h = element["box"]
        page_h = self.layout.height
        obj = raw.FPDFPageObj_CreateNewRect(
            ctypes.c_float(x * PX_TO_PT),
            ctypes.c_float((page_h - y - h) * PX_TO_PT),
            ctypes.c_float(w * PX_TO_PT),
            ctypes.c_float(h * PX_TO_PT),
        )
        if not obj:
            raise RuntimeError("Création de rectangle impossible")
        fill = element["fill"]
        stroke = element["stroke"]
        if fill:
            self._set_fill(obj, fill)
        if stroke:
            self._set_stroke(obj, stroke)
            raw.FPDFPageObj_SetStrokeWidth(obj, ctypes.c_float(
                max(0.1, element["width"] * PX_TO_PT)
            ))
        raw.FPDFPath_SetDrawMode(obj, 1 if fill else 0, 1 if stroke else 0)
        if not raw.FPDFPage_InsertObject(self.page.raw, obj):
            raise RuntimeError("Insertion de rectangle impossible")

    def _add_line(self, element: dict[str, Any]) -> None:
        (x0, y0), (x1, y1) = element["p0"], element["p1"]
        page_h = self.layout.height
        obj = raw.FPDFPageObj_CreateNewPath(
            ctypes.c_float(x0 * PX_TO_PT),
            ctypes.c_float((page_h - y0) * PX_TO_PT),
        )
        raw.FPDFPath_MoveTo(obj, ctypes.c_float(x0 * PX_TO_PT),
                            ctypes.c_float((page_h - y0) * PX_TO_PT))
        raw.FPDFPath_LineTo(obj, ctypes.c_float(x1 * PX_TO_PT),
                            ctypes.c_float((page_h - y1) * PX_TO_PT))
        self._set_stroke(obj, element["color"])
        raw.FPDFPageObj_SetStrokeWidth(obj, ctypes.c_float(
            max(0.1, element["width"] * PX_TO_PT)
        ))
        raw.FPDFPath_SetDrawMode(obj, 0, 1)
        if not raw.FPDFPage_InsertObject(self.page.raw, obj):
            raise RuntimeError("Insertion de ligne impossible")


def write_native_pdf(pages: Sequence[PageLayout], path: Path) -> None:
    """Écrit un PDF entièrement natif (texte sélectionnable)."""
    doc = pdfium.PdfDocument.new()
    font_cache: dict[str, Any] = {}
    font_buffers: list[Any] = []
    try:
        for layout in pages:
            page = doc.new_page(layout.width * PX_TO_PT,
                                layout.height * PX_TO_PT)
            writer = NativePageWriter(doc, page, layout, font_cache,
                                      font_buffers)
            for element in layout.elements:
                writer.add(element)
            if not raw.FPDFPage_GenerateContent(page.raw):
                raise RuntimeError("Génération du contenu PDF impossible")
        doc.save(str(path))
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# Plans de documents
# ---------------------------------------------------------------------------


@dataclass
class PagePlan:
    """Une page prête à être rendue : native (vecteur) ou scannée (bitmap)."""

    layout: PageLayout
    mode: str = "native"             # "native" | "scan"
    effects: dict[str, Any] = field(default_factory=dict)
    blocks: list[dict[str, Any]] = field(default_factory=list)
    tables: list[dict[str, Any]] = field(default_factory=list)
    image: Image.Image | None = None
    rotation: float = 0.0
    degradation_notes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.blocks:
            self.blocks = copy.deepcopy(self.layout.blocks)
        if not self.tables:
            self.tables = copy.deepcopy(self.layout.tables)


@dataclass
class ItemPlan:
    """Un document du jeu de test et ses pages."""

    id: str
    folder: str
    filename: str
    theme: str
    difficulty: str
    notes: str
    pages: list[PagePlan]
    subtype: str = ""
    source: str = "synthetic"
    filetype: str = "pdf"
    language: str = "fr"

    @property
    def path(self) -> str:
        return self.filename

    @property
    def orientation(self) -> str:
        return self.pages[0].layout.orientation if self.pages else "portrait"


def make_item(prefix: str, index: int, folder: str, theme: str,
              difficulty: str, pages: Sequence[PagePlan | PageLayout],
              notes: str, *, subtype: str = "", filetype: str = "pdf",
              language: str = "fr") -> ItemPlan:
    """Fabrique un ItemPlan en normalisant les pages fournies."""
    item_id = f"{prefix}_{index:03d}"
    filename = f"{folder}/{item_id}.{filetype}"
    normalized: list[PagePlan] = []
    for page in pages:
        if isinstance(page, PageLayout):
            normalized.append(PagePlan(layout=page))
        else:
            normalized.append(page)
    return ItemPlan(
        id=item_id,
        folder=folder,
        filename=filename,
        theme=theme,
        difficulty=difficulty,
        notes=notes,
        pages=normalized,
        subtype=subtype,
        filetype=filetype,
        language=language,
    )


def scan_page(layout: PageLayout, **effects: Any) -> PagePlan:
    return PagePlan(layout=layout, mode="scan", effects=dict(effects))


def native_pages(layouts: Sequence[PageLayout]) -> list[PagePlan]:
    return [PagePlan(layout=layout, mode="native") for layout in layouts]


# ---------------------------------------------------------------------------
# Données métier et mise en forme française
# ---------------------------------------------------------------------------


def fmt_money(value: float) -> str:
    """Formate un montant à la française : 1 234,56 €."""
    text = f"{value:,.2f}".replace(",", " ").replace(".", ",")
    return f"{text} €"


def fmt_rate(value: float) -> str:
    text = f"{value:.1f}".replace(".", ",")
    if text.endswith(",0"):
        text = text[:-2]
    return f"{text} %"


def fmt_qty(value: int | float) -> str:
    if isinstance(value, float) and not value.is_integer():
        return f"{value:.2f}".replace(".", ",")
    return str(int(value))


EMITTERS: list[dict[str, Any]] = [
    {
        "name": "SARL Dupont & Fils",
        "activity": "Matériel informatique et bureautique",
        "address": ["42 rue des Lilas", "75012 Paris"],
        "phone": "01 43 27 65 18",
        "email": "contact@dupont-fils.fr",
        "siret": "812 345 678 00027",
        "tva": "FR 32 812345678",
        "iban": "FR76 3000 4008 2800 0123 4567 890",
        "bic": "BNPAFRPPXXX",
    },
    {
        "name": "Atelier Rousseau",
        "activity": "Conseil et maintenance informatique",
        "address": ["7 impasse des Glycines", "31000 Toulouse"],
        "phone": "05 61 48 22 07",
        "email": "devis@atelier-rousseau.fr",
        "siret": "503 219 874 00013",
        "tva": "FR 41 503219874",
        "iban": "FR14 2004 1010 0505 0001 3M02 606",
        "bic": "PSSTFRPPMON",
    },
    {
        "name": "Éditions du Petit Chêne",
        "activity": "Librairie, papeterie et fournitures de bureau",
        "address": ["18 rue des Fossés", "35000 Rennes"],
        "phone": "02 99 31 54 62",
        "email": "commandes@petit-chene.fr",
        "siret": "441 902 367 00018",
        "tva": "FR 09 441902367",
        "iban": "FR33 1520 6000 3312 3456 7890 123",
        "bic": "CCOPFRPPXXX",
    },
    {
        "name": "Menuiserie Delaunay",
        "activity": "Agencement et mobilier sur mesure",
        "address": ["6 route de la Forêt", "45000 Orléans"],
        "phone": "02 38 77 19 40",
        "email": "atelier@menuiserie-delaunay.fr",
        "siret": "789 456 123 00041",
        "tva": "FR 77 789456123",
        "iban": "FR45 3000 2005 5000 0012 3456 789",
        "bic": "CRLYFRPPXXX",
    },
]

CLIENTS: list[dict[str, Any]] = [
    {
        "name": "Mme Élodie Marchand",
        "address": ["14 rue des Écoles", "75011 Paris"],
        "email": "elodie.marchand@example.fr",
        "tva": "FR 12 445566778",
    },
    {
        "name": "M. François Lefèvre",
        "address": ["8 avenue de la Gare", "69003 Lyon"],
        "email": "francois.lefevre@example.fr",
        "tva": "FR 23 556677889",
    },
    {
        "name": "Association Les Amis du Chêne",
        "address": ["3 place du Marché", "37000 Tours"],
        "email": "contact@amis-du-chene.fr",
        "tva": "FR 34 667788990",
    },
    {
        "name": "Mme Amélie Côté",
        "address": ["27 chemin des Vignes", "33000 Bordeaux"],
        "email": "amelie.cote@example.fr",
        "tva": "FR 45 778899001",
    },
    {
        "name": "M. Jérôme Noël",
        "address": ["5 rue de l'Église", "44000 Nantes"],
        "email": "jerome.noel@example.fr",
        "tva": "FR 56 889900112",
    },
    {
        "name": "SCI du Pré Fleuri",
        "address": ["12 allée des Tilleuls", "67000 Strasbourg"],
        "email": "gestion@pre-fleuri.fr",
        "tva": "FR 67 990011223",
    },
    {
        "name": "Mme Céline Baptiste",
        "address": ["9 rue de la Forêt", "59000 Lille"],
        "email": "celine.baptiste@example.fr",
        "tva": "FR 78 101122334",
    },
    {
        "name": "M. Rémi Gauthier",
        "address": ["31 boulevard Voltaire", "75011 Paris"],
        "email": "remi.gauthier@example.fr",
        "tva": "FR 89 112233445",
    },
]

# (désignation, prix unitaire HT, unité, taux de TVA)
CATALOG: list[tuple[str, float, str, float]] = [
    ("Écran 27 pouces LED", 189.00, "unité", 20.0),
    ("Clavier mécanique AZERTY", 79.90, "unité", 20.0),
    ("Souris ergonomique sans fil", 34.50, "unité", 20.0),
    ("Câble HDMI 2 m", 12.90, "unité", 20.0),
    ("Station d'accueil USB-C", 149.00, "unité", 20.0),
    ("Casque audio antibruit", 129.00, "unité", 20.0),
    ("Ramette papier A4 80 g", 5.40, "ramette", 5.5),
    ("Cartouche d'encre noire", 42.80, "unité", 20.0),
    ("Lampe de bureau LED", 39.90, "unité", 20.0),
    ("Fauteuil ergonomique", 329.00, "unité", 20.0),
    ("Bureau assis-debout", 549.00, "unité", 20.0),
    ("Armoire métallique 2 portes", 289.00, "unité", 20.0),
    ("Tableau blanc 120 × 90 cm", 84.00, "unité", 20.0),
    ("Multiprise parafoudre", 24.90, "unité", 20.0),
    ("Disque SSD externe 1 To", 119.00, "unité", 20.0),
    ("Webcam Full HD", 59.90, "unité", 20.0),
    ("Onduleur 650 VA", 99.00, "unité", 20.0),
    ("Pochette de 100 chemises", 18.60, "lot", 20.0),
    ("Stylo bille bleu", 1.20, "unité", 20.0),
    ("Bloc-notes A5", 3.80, "unité", 5.5),
    ("Nettoyant écran", 8.50, "flacon", 20.0),
    ("Support téléphone", 19.90, "unité", 20.0),
    ("Imprimante laser couleur", 439.00, "unité", 20.0),
    ("Livre — Guide de la photographie", 24.90, "exemplaire", 5.5),
    ("Abonnement revue mensuelle", 8.90, "mois", 5.5),
    ("Prestation de maintenance", 90.00, "heure", 20.0),
    ("Installation et configuration", 120.00, "forfait", 20.0),
    ("Formation bureautique", 450.00, "session", 20.0),
]


def make_invoice_data(rng: random.Random, n_rows: int, *,
                      number: str, date: str, due_date: str,
                      emitter: dict[str, Any] | None = None,
                      client: dict[str, Any] | None = None,
                      rates: Sequence[float] | None = None,
                      discount_pct: float = 0.0,
                      deposit: float = 0.0,
                      kind: str = "FACTURE") -> dict[str, Any]:
    """Construit un jeu de lignes et de totaux cohérents."""
    emitter = emitter or rng.choice(EMITTERS)
    client = client or rng.choice(CLIENTS)
    pool = list(CATALOG)
    rng.shuffle(pool)
    chosen = (pool * ((n_rows // len(pool)) + 1))[:n_rows]
    rows: list[dict[str, Any]] = []
    for index, (label, price, unit, default_rate) in enumerate(chosen):
        rate = float(rates[index % len(rates)]) if rates else default_rate
        qty = rng.choice([1, 1, 2, 2, 3, 4, 5, 6, 8, 10, 12])
        ht = round(qty * price, 2)
        rows.append({
            "label": label,
            "unit": unit,
            "qty": qty,
            "price": price,
            "rate": rate,
            "ht": ht,
        })
    subtotal = round(sum(row["ht"] for row in rows), 2)
    discount_amount = round(subtotal * discount_pct / 100.0, 2)
    net_ht = round(subtotal - discount_amount, 2)
    by_rate: dict[float, float] = {}
    for row in rows:
        by_rate[row["rate"]] = round(by_rate.get(row["rate"], 0.0) + row["ht"], 2)
    vat_lines: list[dict[str, Any]] = []
    for rate in sorted(by_rate):
        base = round(by_rate[rate] * (1.0 - discount_pct / 100.0), 2)
        vat = round(base * rate / 100.0, 2)
        vat_lines.append({"rate": rate, "base": base, "amount": vat})
    vat_total = round(sum(line["amount"] for line in vat_lines), 2)
    total_ttc = round(net_ht + vat_total, 2)
    net_due = round(total_ttc - deposit, 2)
    return {
        "kind": kind,
        "number": number,
        "date": date,
        "due_date": due_date,
        "emitter": emitter,
        "client": client,
        "rows": rows,
        "subtotal": subtotal,
        "discount_pct": discount_pct,
        "discount_amount": discount_amount,
        "net_ht": net_ht,
        "vat_lines": vat_lines,
        "vat_total": vat_total,
        "total_ttc": total_ttc,
        "deposit": deposit,
        "net_due": net_due,
        "reference": f"CMD-{rng.randint(10000, 99999)}",
        "payment_terms": rng.choice([
            "Règlement à 30 jours date de facture.",
            "Paiement à réception de la facture.",
            "Règlement à 45 jours fin de mois.",
        ]),
    }


# ---------------------------------------------------------------------------
# Modèles de factures
# ---------------------------------------------------------------------------


def _invoice_top(pg: PageLayout, data: dict[str, Any], *,
                 compact: bool = False, page_no: int = 1,
                 total_pages: int = 1) -> float:
    """En-tête de facture ; renvoie l'ordonnée de départ du corps."""
    width = pg.width
    accent = ACCENT
    pg.rect(0, 0, width, 18, fill=accent)
    emitter = data["emitter"]
    name_size = 12.5 if compact else 16.0
    pg.text(emitter["name"], MARGIN, 48, font="sans_bold", size=name_size,
            color=accent, block_type="header", role="company")
    detail_lines = [
        emitter["activity"],
        " · ".join(emitter["address"]),
        f"Tél. {emitter['phone']} — {emitter['email']}",
        f"SIRET {emitter['siret']} — TVA {emitter['tva']}",
    ]
    pg.text("\n".join(detail_lines), MARGIN, 88 if not compact else 80,
            width=620, font="sans", size=7.8, color=MUTED,
            line_spacing=1.35, block_type="header", role="company_details")
    title = data.get("kind", "FACTURE")
    if page_no > 1:
        title = f"{title} — suite"
    title_y = 48 if not compact else 40
    title_size = 30.0 if not compact else 20.0
    pg.text(title, width - MARGIN - 500, title_y, width=500, align="right",
            font="sans_bold", size=title_size, color=accent,
            block_type="title", role="document_title")
    meta_y = title_y + (66 if not compact else 48)
    meta = [
        f"N° {data['number']}",
        f"Date : {data['date']}",
        f"Échéance : {data['due_date']}",
    ]
    if page_no > 1:
        meta.append(f"Page {page_no} / {total_pages}")
    pg.text("\n".join(meta), width - MARGIN - 500, meta_y, width=500,
            align="right", font="sans", size=8.8 if not compact else 8.0,
            color=INK, line_spacing=1.45 if not compact else 1.25,
            block_type="paragraph", role="metadata")
    rule_y = 200 if not compact else 182
    pg.hline(MARGIN, width - MARGIN, rule_y, color=RULE, width=1.0)
    return rule_y + 26


def _invoice_client(pg: PageLayout, data: dict[str, Any], x: float, y: float,
                    width: float, *, boxed: bool = True) -> float:
    client = data["client"]
    if boxed:
        pg.rect(x, y, width, 152, fill=LIGHT_2, stroke=RULE, width=0.8)
        inner_x, inner_y = x + 16, y + 12
    else:
        inner_x, inner_y = x, y
    pg.text("FACTURÉ À", inner_x, inner_y, font="sans_bold", size=7.6,
            color=MUTED, block_type="paragraph", role="label")
    pg.text(client["name"], inner_x, inner_y + 18, width=width - 32,
            font="sans_bold", size=10.5, block_type="paragraph",
            role="client")
    details = client["address"] + [client["email"], f"TVA {client['tva']}"]
    pg.text("\n".join(details), inner_x, inner_y + 42, width=width - 32,
            font="sans", size=8.4, line_spacing=1.4,
            block_type="paragraph", role="client_details")
    return y + (152 if boxed else 128)


def _invoice_items(pg: PageLayout, data: dict[str, Any], rows: Sequence[dict[str, Any]],
                   x: float, y: float, widths: Sequence[float], *,
                   size: float = 8.8, header_bg: Sequence[int] = ACCENT) -> float:
    table_rows: list[list[str]] = [[
        "Désignation", "Unité", "Qté", "P.U. HT", "TVA", "Montant HT",
    ]]
    for row in rows:
        table_rows.append([
            row["label"], row["unit"], fmt_qty(row["qty"]),
            fmt_money(row["price"]), fmt_rate(row["rate"]),
            fmt_money(row["ht"]),
        ])
    block = pg.table(
        x, y, widths, table_rows, size=size, header_bg=header_bg,
        aligns=["left", "center", "center", "right", "center", "right"],
        zebra=True, cell_pad_y=3.6, role="invoice_lines",
    )
    return float(block["bbox"][3]) if block else y


def _amount_line(pg: PageLayout, label: str, value: str, x: float, y: float,
                 width: float, *, bold: bool = False, color: Sequence[int] = INK,
                 size: float = 9.2, role: str = "total") -> float:
    font = "sans_bold" if bold else "sans"
    pg.text(label + " :", x + 12, y, width=width - 120, font=font, size=size,
            color=color, record=False)
    pg.text(value, x + width - 145, y, width=133, align="right", font=font,
            size=size, color=color, record=False)
    bottom = y + line_height(size, 1.28)
    block = {
        "type": "paragraph",
        "text": f"{label} : {value}",
        "bbox": _bbox(x, y, x + width, bottom),
        "role": role,
    }
    pg.blocks.append(block)
    return bottom


def _invoice_totals(pg: PageLayout, data: dict[str, Any], x: float, y: float,
                    width: float, *, size: float = 9.2) -> float:
    lines: list[tuple[str, str, bool]] = [
        ("Sous-total HT", fmt_money(data["subtotal"]), False),
    ]
    if data["discount_amount"]:
        lines.append((
            f"Remise {fmt_rate(data['discount_pct'])}",
            f"-{fmt_money(data['discount_amount'])}", False,
        ))
        lines.append(("Total HT net", fmt_money(data["net_ht"]), False))
    for vat in data["vat_lines"]:
        label = f"TVA {fmt_rate(vat['rate'])} (base {fmt_money(vat['base'])})"
        lines.append((label, fmt_money(vat["amount"]), False))
    lines.append(("Total TTC", fmt_money(data["total_ttc"]), True))
    if data["deposit"]:
        lines.append(("Acompte versé", f"-{fmt_money(data['deposit'])}", False))
        lines.append(("Net à payer", fmt_money(data["net_due"]), True))
    height = 20 + len(lines) * line_height(size, 1.28) + 8
    pg.rect(x, y, width, height, fill=LIGHT_2, stroke=RULE, width=0.8)
    cursor = y + 11
    for label, value, bold in lines:
        cursor = _amount_line(pg, label, value, x, cursor, width, bold=bold,
                              size=size + (0.4 if bold else 0.0))
    return y + height


def _invoice_payment(pg: PageLayout, data: dict[str, Any], x: float, y: float,
                     width: float) -> float:
    emitter = data["emitter"]
    pg.text("MODALITÉS DE RÈGLEMENT", x, y, font="sans_bold", size=8.2,
            color=ACCENT, block_type="paragraph", role="section")
    content = (
        f"{data['payment_terms']} Virement bancaire sur le compte "
        f"IBAN {emitter['iban']} — BIC {emitter['bic']}. "
        "Merci de rappeler le numéro de facture en référence."
    )
    block = pg.text(content, x, y + 20, width=width, font="sans", size=7.8,
                    color=MUTED, line_spacing=1.38, bbox_full_width=True,
                    block_type="paragraph", role="payment_terms")
    return float(block["bbox"][3]) if block else y + 40


def _invoice_footer(pg: PageLayout, data: dict[str, Any], *,
                    page_no: int, total_pages: int) -> None:
    y = pg.height - 118
    pg.hline(MARGIN, pg.width - MARGIN, y, color=RULE, width=0.7)
    emitter = data["emitter"]
    legal = (
        f"{emitter['name']} — {emitter['activity']} — SIRET {emitter['siret']} — "
        f"TVA intracommunautaire {emitter['tva']}. "
        "Pénalités de retard : trois fois le taux d'intérêt légal ; "
        "indemnité forfaitaire pour frais de recouvrement : 40 €. "
        "Escompte pour paiement anticipé : néant."
    )
    pg.text(legal, MARGIN, y + 12, width=pg.width - 2 * MARGIN - 90,
            font="sans", size=6.4, color=MUTED, line_spacing=1.35,
            block_type="footer", role="legal")
    pg.text(f"Page {page_no} / {total_pages}", pg.width - MARGIN - 80, y + 12,
            width=80, align="right", font="sans", size=6.8, color=MUTED,
            block_type="footer", role="page_number")


def build_invoice_classic(data: dict[str, Any], *,
                          rows: Sequence[dict[str, Any]] | None = None,
                          two_pages: bool = False) -> list[PageLayout]:
    """Facture classique, en-tête à gauche, tableau à six colonnes."""
    rows = list(rows if rows is not None else data["rows"])
    pages: list[PageLayout] = []
    pg = PageLayout(A4_PORTRAIT[0], A4_PORTRAIT[1], "portrait")
    y = _invoice_top(pg, data)
    y = _invoice_client(pg, data, MARGIN, y, 470)
    table_y = max(y + 24, 398)
    table_bottom = _invoice_items(
        pg, data, rows, MARGIN, table_y,
        [446, 70, 60, 150, 90, 240], size=8.8,
    )
    totals = _invoice_totals(pg, data, pg.width - MARGIN - 470,
                             table_bottom + 18, 470, size=9.0)
    payment_y = max(totals + 26, table_bottom + 40)
    _invoice_payment(pg, data, MARGIN, payment_y, 640)
    _invoice_footer(pg, data, page_no=1, total_pages=1)
    pages.append(pg)
    return pages


def build_invoice_two_col(data: dict[str, Any], *,
                          rows: Sequence[dict[str, Any]] | None = None,
                          two_pages: bool = False) -> list[PageLayout]:
    """Facture avec émetteur et client côte à côte, bandeau de titre."""
    rows = list(rows if rows is not None else data["rows"])
    width = A4_PORTRAIT[0]
    pg = PageLayout(width, A4_PORTRAIT[1], "portrait")
    pg.rect(0, 0, width, 16, fill=ACCENT_3)
    emitter = data["emitter"]
    pg.text(emitter["name"], MARGIN, 48, font="sans_bold", size=14,
            color=ACCENT_3, block_type="header", role="company")
    pg.text("\n".join([emitter["activity"]] + emitter["address"] +
                      [f"Tél. {emitter['phone']}", emitter["email"]]),
            MARGIN, 88, width=520, font="sans", size=7.6, color=MUTED,
            line_spacing=1.38, block_type="header", role="company_details")
    _invoice_client(pg, data, width - MARGIN - 470, 44, 470)
    pg.rect(MARGIN, 212, width - 2 * MARGIN, 62, fill=ACCENT_3)
    pg.text(data.get("kind", "FACTURE"), MARGIN + 18, 228, width=420,
            font="sans_bold", size=22, color=WHITE, block_type="title",
            role="document_title")
    pg.text(f"N° {data['number']}  ·  {data['date']}  ·  échéance {data['due_date']}",
            MARGIN + 470, 239, width=width - 2 * MARGIN - 488, align="right",
            font="sans", size=8.6, color=WHITE, block_type="paragraph",
            role="metadata")
    table_y = 314
    table_bottom = _invoice_items(
        pg, data, rows, MARGIN, table_y,
        [446, 70, 60, 150, 90, 240], size=8.8, header_bg=ACCENT_3,
    )
    totals = _invoice_totals(pg, data, width - MARGIN - 470,
                             table_bottom + 18, 470, size=9.0)
    _invoice_payment(pg, data, MARGIN, max(totals + 24, table_bottom + 36), 650)
    _invoice_footer(pg, data, page_no=1, total_pages=1)
    return [pg]


def build_invoice_landscape(data: dict[str, Any], *,
                            rows: Sequence[dict[str, Any]] | None = None,
                            two_pages: bool = False) -> list[PageLayout]:
    """Facture au format paysage, tableau large et colonnes aérées."""
    rows = list(rows if rows is not None else data["rows"])
    width, height = A4_LANDSCAPE
    pg = PageLayout(width, height, "landscape")
    pg.rect(0, 0, width, 16, fill=ACCENT_2)
    emitter = data["emitter"]
    pg.text(emitter["name"], 70, 42, font="sans_bold", size=15,
            color=ACCENT_2, block_type="header", role="company")
    pg.text(" · ".join(emitter["address"]) + f" — Tél. {emitter['phone']}",
            70, 84, width=760, font="sans", size=8, color=MUTED,
            block_type="header", role="company_details")
    pg.text(data.get("kind", "FACTURE"), width - 70 - 520, 38, width=520,
            align="right", font="sans_bold", size=26, color=ACCENT_2,
            block_type="title", role="document_title")
    pg.text(f"N° {data['number']}  ·  {data['date']}  ·  échéance {data['due_date']}",
            width - 70 - 720, 96, width=720, align="right", font="sans",
            size=9, block_type="paragraph", role="metadata")
    client = data["client"]
    pg.text("FACTURÉ À", 70, 146, font="sans_bold", size=7.6, color=MUTED,
            block_type="paragraph", role="label")
    pg.text(client["name"], 70, 162, width=650, font="sans_bold", size=10.5,
            block_type="paragraph", role="client")
    pg.text(" · ".join(client["address"]) + f" — {client['email']}", 70, 184,
            width=900, font="sans", size=8.4, block_type="paragraph",
            role="client_details")
    pg.hline(70, width - 70, 216, color=RULE, width=0.9)
    table_bottom = _invoice_items(
        pg, data, rows, 70, 236,
        [640, 100, 80, 190, 120, 300], size=8.8, header_bg=ACCENT_2,
    )
    totals = _invoice_totals(pg, data, width - 70 - 520, table_bottom + 16,
                             520, size=9.0)
    _invoice_payment(pg, data, 70, totals + 20, 760)
    pg.hline(70, width - 70, height - 88, color=RULE, width=0.7)
    pg.text(
        f"{emitter['name']} — SIRET {emitter['siret']} — TVA {emitter['tva']} — "
        "Pénalités de retard : trois fois le taux d'intérêt légal. "
        "Indemnité forfaitaire de recouvrement : 40 €.",
        70, height - 74, width=width - 140, font="sans", size=6.4,
        color=MUTED, block_type="footer", role="legal",
    )
    return [pg]


def build_invoice_dense(data: dict[str, Any], *,
                        split_rows: bool = False,
                        rows_per_page: int = 16) -> list[PageLayout]:
    """Facture dense : petites polices, nombreux articles, 1 ou 2 pages."""
    rows = list(data["rows"])
    width = A4_PORTRAIT[0]
    pages: list[PageLayout] = []
    page_no = 1
    total_pages = 2 if split_rows else 1
    first_chunk = rows[:rows_per_page] if split_rows else rows
    chunks = [first_chunk] + ([rows[rows_per_page:]] if split_rows and rows[rows_per_page:] else [])
    for chunk in chunks:
        pg = PageLayout(width, A4_PORTRAIT[1], "portrait")
        y = _invoice_top(pg, data, compact=True, page_no=page_no,
                         total_pages=len(chunks))
        if page_no == 1:
            y = _invoice_client(pg, data, MARGIN, y, 430)
        else:
            pg.text("Suite du détail des prestations", MARGIN, y,
                    font="sans_bold", size=10, color=ACCENT,
                    block_type="paragraph", role="section")
            y += 28
        table_y = max(y + 12, 300 if page_no == 1 else 150)
        table_bottom = _invoice_items(
            pg, data, chunk, MARGIN, table_y,
            [426, 64, 55, 145, 86, 280], size=7.8,
        )
        if page_no == len(chunks):
            totals = _invoice_totals(pg, data, width - MARGIN - 470,
                                     table_bottom + 14, 470, size=8.6)
            _invoice_payment(pg, data, MARGIN, totals + 20, 620)
        else:
            pg.text(
                "Le détail des articles se poursuit page suivante ; "
                "les totaux sont récapitulés en fin de document.",
                MARGIN, table_bottom + 18, width=620, font="sans", size=8,
                color=MUTED, block_type="paragraph", role="note",
            )
        _invoice_footer(pg, data, page_no=page_no, total_pages=len(chunks))
        pages.append(pg)
        page_no += 1
    return pages


# ---------------------------------------------------------------------------
# Modèles de formulaires
# ---------------------------------------------------------------------------


def _form_header(pg: PageLayout, org: str, org_details: Sequence[str],
                 title: str, *, reference: str = "", date: str = "",
                 accent: Sequence[int] = ACCENT) -> float:
    width = pg.width
    pg.rect(0, 0, width, 14, fill=accent)
    pg.text(org, MARGIN, 40, width=700, font="sans_bold", size=13,
            color=accent, block_type="header", role="organization")
    pg.text("\n".join(org_details), MARGIN, 72, width=700, font="sans",
            size=7.6, color=MUTED, line_spacing=1.35, block_type="header",
            role="organization_details")
    pg.text(title, MARGIN, 138, width=width - 2 * MARGIN, font="sans_bold",
            size=16, color=INK, block_type="title", role="form_title")
    meta = []
    if reference:
        meta.append(f"Réf. : {reference}")
    if date:
        meta.append(f"Date : {date}")
    if meta:
        pg.text("\n".join(meta), width - MARGIN - 300, 142, width=300,
                align="right", font="sans", size=8.2, color=MUTED,
                line_spacing=1.35, block_type="paragraph", role="metadata")
    pg.hline(MARGIN, width - MARGIN, 184, color=RULE, width=0.9)
    return 204


def _form_section(pg: PageLayout, x: float, y: float, width: float,
                  title: str, *, accent: Sequence[int] = ACCENT) -> float:
    pg.rect(x, y, width, 28, fill=LIGHT)
    pg.rect(x, y, 5, 28, fill=accent)
    pg.text(title, x + 14, y + 6, width=width - 24, font="sans_bold", size=8.8,
            color=accent, block_type="paragraph", role="section")
    return y + 38


def _form_field(pg: PageLayout, label: str, value: str, x: float, y: float,
                width: float, *, label_width: float = 190,
                size: float = 8.6, boxed: bool = True,
                value_color: Sequence[int] = (36, 54, 94),
                record: bool = True) -> float:
    value = str(value)
    value_font = "sans_italic"
    pad = 7
    box_width = width - label_width
    value_height = text_height(value, value_font, size, box_width - 2 * pad)
    label_height = text_height(label + " :", "sans_bold", size,
                               label_width - 6)
    box_height = max(28.0, value_height + 12)
    row_height = max(box_height, label_height + 10)
    pg.text(label + " :", x, y + 6, width=label_width - 6, font="sans_bold",
            size=size, color=INK, record=False)
    if boxed:
        pg.rect(x + label_width, y, box_width, box_height, fill=WHITE,
                stroke=RULE, width=0.7)
    else:
        pg.hline(x + label_width, x + width, y + box_height - 3,
                 color=RULE, width=0.7)
    pg.text(value, x + label_width + pad, y + 6, width=box_width - 2 * pad,
            font=value_font, size=size, color=value_color, record=False)
    if record:
        pg.blocks.append({
            "type": "paragraph",
            "text": f"{label} : {value}",
            "bbox": _bbox(x, y, x + width, y + row_height),
            "role": "field",
        })
    return y + row_height + 8


def _form_footer(pg: PageLayout, org: str, *, page_no: int = 1,
                 total_pages: int = 1, note: str = "") -> None:
    y = pg.height - 92
    pg.hline(MARGIN, pg.width - MARGIN, y, color=RULE, width=0.7)
    text = note or (
        f"{org} — Formulaire à retourner dûment complété et signé. "
        "Les données sont traitées conformément au RGPD."
    )
    pg.text(text, MARGIN, y + 10, width=pg.width - 2 * MARGIN - 90,
            font="sans", size=6.6, color=MUTED, block_type="footer",
            role="legal")
    pg.text(f"Page {page_no} / {total_pages}", pg.width - MARGIN - 80, y + 10,
            width=80, align="right", font="sans", size=6.8, color=MUTED,
            block_type="footer", role="page_number")


def _signature_box(pg: PageLayout, x: float, y: float, width: float,
                   height: float, label: str) -> None:
    pg.rect(x, y, width, height, fill=None, stroke=RULE, width=0.7)
    pg.text(label, x + 10, y + height - 24, width=width - 20, font="sans",
            size=7.4, color=MUTED, block_type="paragraph", role="signature")


def build_form_renseignements() -> PageLayout:
    """Fiche de renseignements simple (facile)."""
    pg = PageLayout(A4_PORTRAIT[0], A4_PORTRAIT[1], "portrait")
    y = _form_header(
        pg, "Mairie de Saint-Rémy-lès-Chevreuse",
        ["Service état civil et accueil des habitants",
         "2 place de la Mairie, 78470 Saint-Rémy-lès-Chevreuse",
         "Tél. 01 30 52 14 20 — accueil@ville-st-remy.fr"],
        "FICHE DE RENSEIGNEMENTS", reference="FR-2026-0417",
        date="9 novembre 2026", accent=ACCENT_3,
    )
    y = _form_section(pg, MARGIN, y, pg.width - 2 * MARGIN,
                      "1. Identité du demandeur", accent=ACCENT_3)
    y = _form_field(pg, "Nom de naissance", "MARCHAND", MARGIN, y,
                    pg.width - 2 * MARGIN)
    y = _form_field(pg, "Prénom(s)", "Élodie, Marie, Françoise", MARGIN, y,
                    pg.width - 2 * MARGIN)
    y = _form_field(pg, "Date de naissance", "14 février 1987", MARGIN, y,
                    pg.width - 2 * MARGIN)
    y = _form_field(pg, "Lieu de naissance", "Saint-Germain-en-Laye (78)",
                    MARGIN, y, pg.width - 2 * MARGIN)
    y = _form_field(pg, "Nationalité", "Française", MARGIN, y,
                    pg.width - 2 * MARGIN)
    y = _form_section(pg, MARGIN, y + 6, pg.width - 2 * MARGIN,
                      "2. Coordonnées", accent=ACCENT_3)
    y = _form_field(pg, "Adresse", "14 rue des Écoles", MARGIN, y,
                    pg.width - 2 * MARGIN)
    y = _form_field(pg, "Code postal et ville", "75011 Paris", MARGIN, y,
                    pg.width - 2 * MARGIN)
    y = _form_field(pg, "Téléphone", "06 42 18 73 90", MARGIN, y,
                    pg.width - 2 * MARGIN)
    y = _form_field(pg, "Courriel", "elodie.marchand@example.fr", MARGIN, y,
                    pg.width - 2 * MARGIN)
    y = _form_section(pg, MARGIN, y + 6, pg.width - 2 * MARGIN,
                      "3. Situation familiale", accent=ACCENT_3)
    pg.checkbox(MARGIN, y, "Célibataire", checked=False)
    pg.checkbox(MARGIN + 250, y, "Marié(e)", checked=True)
    pg.checkbox(MARGIN + 470, y, "Pacsé(e)", checked=False)
    pg.checkbox(MARGIN + 690, y, "Divorcé(e)", checked=False)
    y += 38
    pg.checkbox(MARGIN, y, "Veuf(ve)", checked=False)
    pg.checkbox(MARGIN + 250, y, "En concubinage", checked=False)
    y += 44
    pg.text(
        "Je certifie sur l'honneur l'exactitude des renseignements portés "
        "sur la présente fiche et m'engage à signaler tout changement.",
        MARGIN, y, width=pg.width - 2 * MARGIN, font="sans", size=8.2,
        line_spacing=1.4, bbox_full_width=True, block_type="paragraph",
        role="declaration",
    )
    _signature_box(pg, MARGIN, y + 60, 330, 100, "Signature du demandeur")
    _signature_box(pg, pg.width - MARGIN - 330, y + 60, 330, 100,
                   "Date et cachet de la mairie")
    _form_footer(pg, "Mairie de Saint-Rémy-lès-Chevreuse")
    return pg


def build_form_sepa() -> PageLayout:
    """Autorisation de prélèvement SEPA (facile)."""
    pg = PageLayout(A4_PORTRAIT[0], A4_PORTRAIT[1], "portrait")
    y = _form_header(
        pg, "Mutuelle Solidaris",
        ["Direction des adhésions et des cotisations",
         "45 avenue de la République, 69002 Lyon",
         "Tél. 04 72 61 08 33 — adherents@solidaris.fr"],
        "AUTORISATION DE PRÉLÈVEMENT SEPA",
        reference="SEPA-2026-1182", date="9 novembre 2026",
        accent=ACCENT,
    )
    y = _form_section(pg, MARGIN, y, pg.width - 2 * MARGIN,
                      "Créancier", accent=ACCENT)
    y = _form_field(pg, "Nom du créancier", "Mutuelle Solidaris", MARGIN, y,
                    pg.width - 2 * MARGIN)
    y = _form_field(pg, "Identifiant créancier (ICS)",
                    "FR98ZZZ123456", MARGIN, y, pg.width - 2 * MARGIN)
    y = _form_section(pg, MARGIN, y + 6, pg.width - 2 * MARGIN,
                      "Débiteur", accent=ACCENT)
    y = _form_field(pg, "Nom et prénom", "CÔTÉ Amélie", MARGIN, y,
                    pg.width - 2 * MARGIN)
    y = _form_field(pg, "Adresse", "27 chemin des Vignes", MARGIN, y,
                    pg.width - 2 * MARGIN)
    y = _form_field(pg, "Code postal et ville", "33000 Bordeaux", MARGIN, y,
                    pg.width - 2 * MARGIN)
    y = _form_field(pg, "IBAN", "FR76 3000 4008 2800 0123 4567 890", MARGIN, y,
                    pg.width - 2 * MARGIN, value_color=INK)
    y = _form_field(pg, "BIC", "BNPAFRPPXXX", MARGIN, y,
                    pg.width - 2 * MARGIN, value_color=INK)
    y = _form_field(pg, "Référence du mandat", "MAND-2026-004182", MARGIN, y,
                    pg.width - 2 * MARGIN)
    y += 8
    pg.text(
        "En signant, j'autorise Mutuelle Solidaris à envoyer des instructions "
        "à ma banque pour débiter mon compte et à ma banque à débiter mon "
        "compte conformément aux instructions de Mutuelle Solidaris. "
        "J'ai la possibilité de demander le remboursement dans les huit "
        "semaines suivant la date de débit.",
        MARGIN, y, width=pg.width - 2 * MARGIN, font="sans", size=8.0,
        line_spacing=1.42, bbox_full_width=True, block_type="paragraph",
        role="legal_consent",
    )
    y += 92
    pg.checkbox(MARGIN, y, "J'accepte le prélèvement récurrent mensuel",
                checked=True)
    y += 40
    _signature_box(pg, MARGIN, y, 420, 110, "Signature du débiteur")
    pg.text("Fait à Bordeaux, le 9 novembre 2026", MARGIN + 470, y + 18,
            width=440, font="sans", size=8.6, block_type="paragraph",
            role="place_date")
    _form_footer(pg, "Mutuelle Solidaris")
    return pg


def build_form_intervention() -> PageLayout:
    """Bon d'intervention avec cases à cocher (facile)."""
    pg = PageLayout(A4_PORTRAIT[0], A4_PORTRAIT[1], "portrait")
    y = _form_header(
        pg, "Atelier Rousseau",
        ["Conseil et maintenance informatique",
         "7 impasse des Glycines, 31000 Toulouse",
         "Tél. 05 61 48 22 07 — intervention@atelier-rousseau.fr"],
        "BON D'INTERVENTION", reference="BI-2026-0731",
        date="9 novembre 2026", accent=ACCENT_2,
    )
    y = _form_section(pg, MARGIN, y, pg.width - 2 * MARGIN,
                      "Client et site d'intervention", accent=ACCENT_2)
    y = _form_field(pg, "Client", "M. François Lefèvre", MARGIN, y,
                    pg.width - 2 * MARGIN)
    y = _form_field(pg, "Adresse", "8 avenue de la Gare", MARGIN, y,
                    pg.width - 2 * MARGIN)
    y = _form_field(pg, "Code postal et ville", "69003 Lyon", MARGIN, y,
                    pg.width - 2 * MARGIN)
    y = _form_field(pg, "Téléphone", "04 78 62 15 44", MARGIN, y,
                    pg.width - 2 * MARGIN)
    y = _form_section(pg, MARGIN, y + 6, pg.width - 2 * MARGIN,
                      "Intervention", accent=ACCENT_2)
    y = _form_field(pg, "Date et heure", "9 novembre 2026 à 14 h 30", MARGIN, y,
                    pg.width - 2 * MARGIN)
    y = _form_field(pg, "Technicien", "Lucas Bertrand", MARGIN, y,
                    pg.width - 2 * MARGIN)
    y = _form_field(pg, "Nature de la demande",
                    "Remplacement du disque système et migration des données",
                    MARGIN, y, pg.width - 2 * MARGIN)
    pg.text("Opérations réalisées :", MARGIN, y + 4, font="sans_bold",
            size=8.8, block_type="paragraph", role="label")
    y += 30
    operations = [
        ("Sauvegarde des données utilisateur", True),
        ("Remplacement du disque SSD", True),
        ("Réinstallation du système", True),
        ("Restauration du profil et des logiciels", True),
        ("Mise à jour des pilotes", False),
        ("Test de bon fonctionnement", True),
    ]
    for index, (operation, checked) in enumerate(operations):
        pg.checkbox(MARGIN, y + (index % 2) * 30,
                    operation, checked=checked,
                    box_size=13)
        if index % 2 == 1:
            y += 30
    y += 52
    y = _form_field(pg, "Durée de l'intervention", "2 h 15", MARGIN, y,
                    pg.width - 2 * MARGIN)
    y = _form_field(pg, "Observations",
                    "Disque d'origine défaillant, aucune perte de données.",
                    MARGIN, y, pg.width - 2 * MARGIN)
    _signature_box(pg, MARGIN, y + 10, 380, 100, "Signature du client")
    _signature_box(pg, pg.width - MARGIN - 380, y + 10, 380, 100,
                   "Signature du technicien")
    _form_footer(pg, "Atelier Rousseau")
    return pg


def build_form_commande() -> PageLayout:
    """Bon de commande fournisseur (moyen)."""
    data = {
        "number": "BC-2026-0184",
        "date": "9 novembre 2026",
        "supplier": EMITTERS[0],
        "client": CLIENTS[2],
    }
    pg = PageLayout(A4_PORTRAIT[0], A4_PORTRAIT[1], "portrait")
    width = pg.width
    pg.rect(0, 0, width, 14, fill=ACCENT)
    pg.text("Association Les Amis du Chêne", MARGIN, 40, width=700,
            font="sans_bold", size=13, color=ACCENT, block_type="header",
            role="organization")
    pg.text("3 place du Marché, 37000 Tours\ncontact@amis-du-chene.fr — "
            "Tél. 02 47 21 38 60", MARGIN, 80, width=700, font="sans",
            size=7.6, color=MUTED, line_spacing=1.35, block_type="header",
            role="organization_details")
    pg.text("BON DE COMMANDE", width - MARGIN - 430, 44, width=430,
            align="right", font="sans_bold", size=19, color=ACCENT,
            block_type="title", role="document_title")
    pg.text(f"N° {data['number']}\nDate : {data['date']}\n"
            "Livraison souhaitée : 24 novembre 2026",
            width - MARGIN - 430, 88, width=430, align="right", font="sans",
            size=8.4, line_spacing=1.4, block_type="paragraph",
            role="metadata")
    pg.hline(MARGIN, width - MARGIN, 150, color=RULE, width=0.9)
    pg.text("FOURNISSEUR", MARGIN, 170, font="sans_bold", size=7.6,
            color=MUTED, block_type="paragraph", role="label")
    supplier = data["supplier"]
    pg.text(supplier["name"] + "\n" + "\n".join(supplier["address"]) +
            f"\nTVA {supplier['tva']}", MARGIN, 188, width=440,
            font="sans", size=8.6, line_spacing=1.4, block_type="paragraph",
            role="supplier")
    pg.text("ADRESSE DE LIVRAISON", width - MARGIN - 440, 170,
            font="sans_bold", size=7.6, color=MUTED, block_type="paragraph",
            role="label")
    pg.text("Association Les Amis du Chêne\nMaison des associations\n"
            "3 place du Marché\n37000 Tours", width - MARGIN - 440, 188,
            width=440, font="sans", size=8.6, line_spacing=1.4,
            block_type="paragraph", role="delivery_address")
    rows = [
        ["Réf.", "Désignation", "Qté", "P.U. HT", "TVA", "Montant HT"],
        ["INF-001", "Écran 27 pouces LED", "4", "189,00 €", "20 %", "756,00 €"],
        ["INF-014", "Station d'accueil USB-C", "4", "149,00 €", "20 %", "596,00 €"],
        ["INF-021", "Clavier mécanique AZERTY", "6", "79,90 €", "20 %", "479,40 €"],
        ["INF-028", "Souris ergonomique sans fil", "6", "34,50 €", "20 %", "207,00 €"],
        ["PAP-003", "Ramette papier A4 80 g", "20", "5,40 €", "5,5 %", "108,00 €"],
        ["DIV-009", "Multiprise parafoudre", "5", "24,90 €", "20 %", "124,50 €"],
    ]
    table = pg.table(
        MARGIN, 300, [100, 416, 60, 140, 90, 250], rows, size=8.8,
        aligns=["left", "left", "center", "right", "center", "right"],
        zebra=True, role="order_lines",
    )
    table_bottom = float(table["bbox"][3])
    subtotal = 756.00 + 596.00 + 479.40 + 207.00 + 108.00 + 124.50
    vat20 = round((756.00 + 596.00 + 479.40 + 207.00 + 124.50) * 0.20, 2)
    vat55 = round(108.00 * 0.055, 2)
    _invoice_totals(pg, {
        "subtotal": round(subtotal, 2), "discount_amount": 0.0,
        "discount_pct": 0.0, "net_ht": round(subtotal, 2),
        "vat_lines": [
            {"rate": 20.0, "base": 2162.90, "amount": vat20},
            {"rate": 5.5, "base": 108.00, "amount": vat55},
        ],
        "vat_total": round(vat20 + vat55, 2),
        "total_ttc": round(subtotal + vat20 + vat55, 2),
        "deposit": 0.0, "net_due": round(subtotal + vat20 + vat55, 2),
    }, width - MARGIN - 460, table_bottom + 18, 460, size=9.0)
    y = table_bottom + 170
    pg.text("Conditions de livraison", MARGIN, y, font="sans_bold", size=8.6,
            color=ACCENT, block_type="paragraph", role="section")
    pg.text(
        "Livraison franco de port sur le site de Tours. Les marchandises "
        "doivent être accompagnées d'un bon de livraison mentionnant le "
        "numéro de commande. Toute réserve doit être signalée dans les "
        "trois jours ouvrés.",
        MARGIN, y + 20, width=650, font="sans", size=7.8, color=MUTED,
        line_spacing=1.4, bbox_full_width=True, block_type="paragraph",
        role="conditions",
    )
    _signature_box(pg, MARGIN, y + 90, 400, 100, "Le président de l'association")
    _signature_box(pg, width - MARGIN - 400, y + 90, 400, 100,
                   "Bon pour accord du fournisseur")
    _form_footer(pg, "Association Les Amis du Chêne")
    return pg


def build_form_conges() -> PageLayout:
    """Demande de congés avec tableaux (moyen)."""
    pg = PageLayout(A4_PORTRAIT[0], A4_PORTRAIT[1], "portrait")
    y = _form_header(
        pg, "Éditions du Petit Chêne",
        ["Service des ressources humaines",
         "18 rue des Fossés, 35000 Rennes",
         "Tél. 02 99 31 54 62 — rh@petit-chene.fr"],
        "DEMANDE DE CONGÉS PAYÉS", reference="CP-2026-0291",
        date="9 novembre 2026", accent=ACCENT_3,
    )
    y = _form_section(pg, MARGIN, y, pg.width - 2 * MARGIN,
                      "Salarié", accent=ACCENT_3)
    y = _form_field(pg, "Nom et prénom", "LEFÈVRE François", MARGIN, y,
                    pg.width - 2 * MARGIN)
    y = _form_field(pg, "Matricule", "MAT-004182", MARGIN, y,
                    pg.width - 2 * MARGIN)
    y = _form_field(pg, "Service", "Logistique et expéditions", MARGIN, y,
                    pg.width - 2 * MARGIN)
    y = _form_field(pg, "Solde de congés au 1er novembre 2026", "18,5 jours",
                    MARGIN, y, pg.width - 2 * MARGIN)
    y = _form_section(pg, MARGIN, y + 6, pg.width - 2 * MARGIN,
                      "Période demandée", accent=ACCENT_3)
    rows = [
        ["Du", "Au", "Nombre de jours", "Type de congé"],
        ["21 décembre 2026", "2 janvier 2027", "10", "Congés payés"],
        ["16 mars 2027", "20 mars 2027", "5", "Congés payés"],
        ["6 avril 2027", "6 avril 2027", "1", "RTT"],
    ]
    table = pg.table(
        MARGIN, y, [230, 230, 200, 396], rows, size=8.8,
        aligns=["left", "left", "center", "left"], zebra=True,
        role="leave_periods",
    )
    y = float(table["bbox"][3]) + 24
    pg.text("Type de congé :", MARGIN, y, font="sans_bold", size=8.6,
            block_type="paragraph", role="label")
    pg.checkbox(MARGIN + 160, y, "Congés payés", checked=True)
    pg.checkbox(MARGIN + 390, y, "RTT", checked=False)
    pg.checkbox(MARGIN + 570, y, "Sans solde", checked=False)
    y += 40
    y = _form_field(pg, "Motif (facultatif)",
                    "Déplacement familial à Nantes", MARGIN, y,
                    pg.width - 2 * MARGIN)
    y = _form_field(pg, "Responsable hiérarchique", "Mme Amélie Côté",
                    MARGIN, y, pg.width - 2 * MARGIN)
    pg.text(
        "La demande doit être adressée au moins quinze jours avant le début "
        "du congé. L'accord du responsable vaut autorisation d'absence.",
        MARGIN, y + 8, width=pg.width - 2 * MARGIN, font="sans", size=7.6,
        color=MUTED, line_spacing=1.4, bbox_full_width=True,
        block_type="paragraph", role="note",
    )
    _signature_box(pg, MARGIN, y + 66, 330, 100, "Signature du salarié")
    _signature_box(pg, MARGIN + 360, y + 66, 330, 100, "Avis du responsable")
    _signature_box(pg, pg.width - MARGIN - 330, y + 66, 330, 100,
                   "Décision des ressources humaines")
    _form_footer(pg, "Éditions du Petit Chêne")
    return pg


def build_form_inscription() -> PageLayout:
    """Inscription scolaire, deux colonnes (moyen)."""
    pg = PageLayout(A4_PORTRAIT[0], A4_PORTRAIT[1], "portrait")
    y = _form_header(
        pg, "Lycée Jules Ferry",
        ["Secrétariat des élèves — année scolaire 2026-2027",
         "12 boulevard des Écoles, 75010 Paris",
         "Tél. 01 42 09 55 31 — secretariat@lycee-jules-ferry.fr"],
        "DOSSIER D'INSCRIPTION", reference="INS-2026-1487",
        date="9 novembre 2026", accent=ACCENT,
    )
    col_width = (pg.width - 2 * MARGIN - 28) / 2
    left_x = MARGIN
    right_x = MARGIN + col_width + 28
    y_left = _form_section(pg, left_x, y, col_width,
                           "Élève", accent=ACCENT)
    y_right = _form_section(pg, right_x, y, col_width,
                            "Responsable légal", accent=ACCENT)
    left_fields = [
        ("Nom de famille", "GAUTHIER"),
        ("Prénoms", "Rémi, Paul"),
        ("Date de naissance", "3 août 2010"),
        ("Lieu de naissance", "Paris 12e"),
        ("Adresse", "31 boulevard Voltaire"),
        ("Code postal, ville", "75011 Paris"),
        ("Téléphone", "06 77 41 28 05"),
        ("Courriel", "remi.gauthier@example.fr"),
        ("Établissement d'origine", "Collège Voltaire"),
    ]
    right_fields = [
        ("Nom et prénom", "GAUTHIER Claire"),
        ("Lien avec l'élève", "Mère"),
        ("Adresse", "31 boulevard Voltaire"),
        ("Code postal, ville", "75011 Paris"),
        ("Téléphone", "06 12 58 74 33"),
        ("Courriel", "claire.gauthier@example.fr"),
        ("Profession", "Cadre administrative"),
        ("N° d'allocataire", "7512345678"),
        ("Assurance scolaire", "MAIF — contrat 98 765 432"),
    ]
    for label, value in left_fields:
        y_left = _form_field(pg, label, value, left_x, y_left, col_width,
                             label_width=150, size=8.0)
    for label, value in right_fields:
        y_right = _form_field(pg, label, value, right_x, y_right, col_width,
                              label_width=150, size=8.0)
    y = max(y_left, y_right) + 4
    y = _form_section(pg, MARGIN, y, pg.width - 2 * MARGIN,
                      "Options et régime scolaire", accent=ACCENT)
    pg.checkbox(MARGIN, y, "Demi-pension", checked=True)
    pg.checkbox(MARGIN + 230, y, "Externat", checked=False)
    pg.checkbox(MARGIN + 430, y, "Internat", checked=False)
    pg.checkbox(MARGIN + 630, y, "Transport scolaire", checked=True)
    y += 40
    pg.checkbox(MARGIN, y, "Latin", checked=True)
    pg.checkbox(MARGIN + 230, y, "Grec ancien", checked=False)
    pg.checkbox(MARGIN + 430, y, "Section européenne anglais", checked=True)
    pg.checkbox(MARGIN + 730, y, "Arts plastiques", checked=False)
    y += 44
    pg.text(
        "Je certifie l'exactitude des renseignements fournis et j'autorise "
        "l'établissement à utiliser les coordonnées ci-dessus pour toute "
        "communication relative à la scolarité de l'élève.",
        MARGIN, y, width=pg.width - 2 * MARGIN, font="sans", size=8.0,
        line_spacing=1.4, bbox_full_width=True, block_type="paragraph",
        role="consent",
    )
    _signature_box(pg, MARGIN, y + 62, 400, 100, "Signature du responsable légal")
    _signature_box(pg, pg.width - MARGIN - 400, y + 62, 400, 100,
                   "Signature de l'élève")
    _form_footer(pg, "Lycée Jules Ferry")
    return pg


def build_form_sinistre() -> PageLayout:
    """Déclaration de sinistre, grille dense sur trois colonnes (difficile)."""
    pg = PageLayout(A4_PORTRAIT[0], A4_PORTRAIT[1], "portrait")
    width = pg.width
    y = _form_header(
        pg, "Assurances du Val d'Oise",
        ["Service gestion des sinistres",
         "8 rue de la Préfecture, 95000 Cergy",
         "Tél. 01 34 25 88 40 — sinistres@assurances-valdoise.fr"],
        "DÉCLARATION DE SINISTRE", reference="SIN-2026-004871",
        date="9 novembre 2026", accent=ACCENT_2,
    )
    y = _form_section(pg, MARGIN, y, width - 2 * MARGIN,
                      "Contrat et assuré", accent=ACCENT_2)
    col_w = (width - 2 * MARGIN - 24) / 3
    xs = [MARGIN, MARGIN + col_w + 12, MARGIN + 2 * (col_w + 12)]
    y1 = _form_field(pg, "N° de contrat", "AV-2024-778210", xs[0], y, col_w,
                     label_width=150, size=7.4)
    y2 = _form_field(pg, "N° d'adhérent", "AD-004182", xs[1], y, col_w,
                     label_width=150, size=7.4)
    y3 = _form_field(pg, "Formule", "Multirisque habitation", xs[2], y, col_w,
                     label_width=150, size=7.4)
    y = max(y1, y2, y3) + 2
    y1 = _form_field(pg, "Nom et prénom", "NOËL Jérôme", xs[0], y, col_w,
                     label_width=150, size=7.4)
    y2 = _form_field(pg, "Date de naissance", "21 juin 1979", xs[1], y, col_w,
                     label_width=150, size=7.4)
    y3 = _form_field(pg, "Téléphone", "06 84 12 55 09", xs[2], y, col_w,
                     label_width=150, size=7.4)
    y = max(y1, y2, y3) + 2
    y1 = _form_field(pg, "Adresse du risque", "5 rue de l'Église", xs[0], y,
                     col_w, label_width=150, size=7.4)
    y2 = _form_field(pg, "Code postal", "44000", xs[1], y, col_w,
                     label_width=150, size=7.4)
    y3 = _form_field(pg, "Ville", "Nantes", xs[2], y, col_w,
                     label_width=150, size=7.4)
    y = max(y1, y2, y3) + 6
    y = _form_section(pg, MARGIN, y, width - 2 * MARGIN,
                      "Circonstances du sinistre", accent=ACCENT_2)
    y1 = _form_field(pg, "Date du sinistre", "2 novembre 2026", xs[0], y, col_w,
                     label_width=150, size=7.6)
    y2 = _form_field(pg, "Heure", "vers 21 h 15", xs[1], y, col_w,
                     label_width=150, size=7.6)
    y3 = _form_field(pg, "Nature", "Dégât des eaux", xs[2], y, col_w,
                     label_width=150, size=7.6)
    y = max(y1, y2, y3) + 2
    y1 = _form_field(pg, "Lieu précis", "Salle de bains, 2e étage", xs[0], y,
                     col_w, label_width=150, size=7.6)
    y2 = _form_field(pg, "Constat amiable", "Non", xs[1], y, col_w,
                     label_width=150, size=7.6)
    y3 = _form_field(pg, "Dépôt de plainte", "Non", xs[2], y, col_w,
                     label_width=150, size=7.6)
    y = max(y1, y2, y3) + 6
    pg.text("Description détaillée :", MARGIN, y, font="sans_bold", size=8,
            block_type="paragraph", role="label")
    y += 18
    pg.rect(MARGIN, y, width - 2 * MARGIN, 100, fill=WHITE, stroke=RULE,
            width=0.7)
    description = (
        "Fuite survenue sur le flexible du lave-linge, dans la salle de bains "
        "du deuxième étage. L'eau s'est répandue sur le carrelage puis a "
        "traversé le plafond du salon situé au premier étage. Les meubles "
        "bas et un tapis ont été endommagés. Le plombier est intervenu le "
        "3 novembre 2026 et a remplacé le flexible défectueux."
    )
    pg.text(description, MARGIN + 8, y + 7, width=width - 2 * MARGIN - 16,
            font="sans", size=7.4, line_spacing=1.35,
            bbox_full_width=True, block_type="paragraph",
            role="description")
    y += 112
    pg.text("Documents joints :", MARGIN, y, font="sans_bold", size=8,
            block_type="paragraph", role="label")
    y += 22
    docs = [
        ("Facture du plombier", True),
        ("Photos des dommages", True),
        ("Devis de réparation", False),
        ("Attestation d'assurance", True),
        ("Rapport d'expertise", False),
    ]
    for index, (label, checked) in enumerate(docs):
        pg.checkbox(xs[index % 3], y + (index // 3) * 32, label,
                    checked=checked, box_size=12, size=7.6)
    y += 76
    y1 = _form_field(pg, "Montant estimé", "3 480,00 €", xs[0], y, col_w,
                     label_width=150, size=7.6)
    y2 = _form_field(pg, "Expert mandaté", "Cabinet Verne", xs[1], y, col_w,
                     label_width=150, size=7.6)
    y3 = _form_field(pg, "Date de visite", "12 novembre 2026", xs[2], y, col_w,
                     label_width=150, size=7.6)
    y = max(y1, y2, y3) + 8
    pg.text(
        "Toute déclaration inexacte ou incomplète peut entraîner la nullité "
        "du contrat ou une réduction de l'indemnité. Le délai de déclaration "
        "est de cinq jours ouvrés à compter de la connaissance du sinistre.",
        MARGIN, y, width=width - 2 * MARGIN, font="sans", size=6.6,
        color=MUTED, line_spacing=1.35, bbox_full_width=True,
        block_type="paragraph", role="small_print",
    )
    _signature_box(pg, MARGIN, y + 52, 390, 90, "Signature de l'assuré")
    pg.text("Fait à Nantes, le 9 novembre 2026", width - MARGIN - 390,
            y + 60, width=390, align="right", font="sans", size=8,
            block_type="paragraph", role="place_date")
    _form_footer(pg, "Assurances du Val d'Oise")
    return pg


def build_form_medical() -> PageLayout:
    """Fiche médicale dense avec antécédents et constantes (difficile)."""
    pg = PageLayout(A4_PORTRAIT[0], A4_PORTRAIT[1], "portrait")
    width = pg.width
    y = _form_header(
        pg, "Cabinet médical des Tilleuls",
        ["Dr Hélène Bouchard — médecine générale",
         "22 avenue des Tilleuls, 67000 Strasbourg",
         "Tél. 03 88 41 26 90 — cabinet@tilleuls-medical.fr"],
        "FICHE MÉDICALE DE SUIVI", reference="MED-2026-0318",
        date="9 novembre 2026", accent=ACCENT_3,
    )
    col_w = (width - 2 * MARGIN - 24) / 3
    xs = [MARGIN, MARGIN + col_w + 12, MARGIN + 2 * (col_w + 12)]
    y = _form_section(pg, MARGIN, y, width - 2 * MARGIN,
                      "Identité et couverture", accent=ACCENT_3)
    y1 = _form_field(pg, "Nom et prénom", "MARCHAND Élodie", xs[0], y, col_w,
                     label_width=150, size=7.4)
    y2 = _form_field(pg, "Date de naissance", "14 février 1987", xs[1], y,
                     col_w, label_width=150, size=7.4)
    y3 = _form_field(pg, "N° de sécurité sociale", "2 87 02 75 116 042 31",
                     xs[2], y, col_w, label_width=150, size=7.4)
    y = max(y1, y2, y3) + 2
    y1 = _form_field(pg, "Adresse", "14 rue des Écoles", xs[0], y, col_w,
                     label_width=150, size=7.4)
    y2 = _form_field(pg, "Code postal, ville", "75011 Paris", xs[1], y, col_w,
                     label_width=150, size=7.4)
    y3 = _form_field(pg, "Téléphone", "06 42 18 73 90", xs[2], y, col_w,
                     label_width=150, size=7.4)
    y = max(y1, y2, y3) + 2
    y1 = _form_field(pg, "Médecin traitant", "Dr Hélène Bouchard", xs[0], y,
                     col_w, label_width=150, size=7.4)
    y2 = _form_field(pg, "Mutuelle", "Mutuelle Solidaris", xs[1], y, col_w,
                     label_width=150, size=7.4)
    y3 = _form_field(pg, "N° d'adhérent", "SOL-884210", xs[2], y, col_w,
                     label_width=150, size=7.4)
    y = max(y1, y2, y3) + 6
    y = _form_section(pg, MARGIN, y, width - 2 * MARGIN,
                      "Antécédents et traitements", accent=ACCENT_3)
    pg.text("Antécédents médicaux et chirurgicaux :", MARGIN, y,
            font="sans_bold", size=7.8, block_type="paragraph", role="label")
    y += 18
    pg.rect(MARGIN, y, width - 2 * MARGIN, 64, fill=WHITE, stroke=RULE,
            width=0.7)
    pg.text(
        "Appendicectomie en 2009. Asthme léger depuis l'enfance, traité par "
        "salbutamol à la demande. Aucune allergie médicamenteuse connue. "
        "Hypertension artérielle diagnostiquée en 2024, sous traitement.",
        MARGIN + 8, y + 7, width=width - 2 * MARGIN - 16, font="sans",
        size=7.3, line_spacing=1.35, bbox_full_width=True,
        block_type="paragraph", role="history",
    )
    y += 76
    y1 = _form_field(pg, "Traitement en cours", "Amlodipine 5 mg — 1/jour",
                     xs[0], y, col_w, label_width=150, size=7.4)
    y2 = _form_field(pg, "Allergies", "Aucune allergie connue", xs[1], y,
                     col_w, label_width=150, size=7.4)
    y3 = _form_field(pg, "Groupe sanguin", "A rhésus positif", xs[2], y, col_w,
                     label_width=150, size=7.4)
    y = max(y1, y2, y3) + 6
    y = _form_section(pg, MARGIN, y, width - 2 * MARGIN,
                      "Constantes du 9 novembre 2026", accent=ACCENT_3)
    rows = [
        ["Poids", "Taille", "IMC", "Tension", "Pouls", "Température"],
        ["64,5 kg", "1,68 m", "22,8", "128/82 mmHg", "72/min", "36,7 °C"],
        ["Saturation", "Glycémie", "Périmètre abdominal", "Fréquence respiratoire",
         "Douleur (EVA)", "Observations"],
        ["98 %", "0,92 g/L", "84 cm", "16/min", "1/10", "Examen normal"],
    ]
    table = pg.table(
        MARGIN, y, [148, 148, 190, 190, 160, 200], rows, size=7.4,
        header=True, header_bg=ACCENT_3, zebra=False,
        aligns=["left"] * 6, cell_pad_x=4, cell_pad_y=3.2,
        role="medical_constants",
    )
    y = float(table["bbox"][3]) + 8
    y = _form_section(pg, MARGIN, y, width - 2 * MARGIN,
                      "Consultation et prescriptions", accent=ACCENT_3)
    y = _form_field(pg, "Motif de consultation", "Suivi de l'hypertension",
                    MARGIN, y, width - 2 * MARGIN, label_width=150, size=7.6)
    y = _form_field(pg, "Examen clinique",
                    "Auscultation cardiaque régulière, pas de souffle. "
                    "Œdèmes des membres inférieurs absents.",
                    MARGIN, y, width - 2 * MARGIN, label_width=150, size=7.6)
    y = _form_field(pg, "Prescription",
                    "Amlodipine 5 mg, 1 comprimé le matin, renouvellement 3 mois",
                    MARGIN, y, width - 2 * MARGIN, label_width=150, size=7.6)
    y = _form_field(pg, "Prochain rendez-vous", "8 février 2027 à 10 h 30",
                    MARGIN, y, width - 2 * MARGIN, label_width=150, size=7.6)
    pg.text(
        "Document confidentiel réservé au patient et aux professionnels de "
        "santé. Conservation : vingt ans à compter de la dernière consultation.",
        MARGIN, y + 8, width=width - 2 * MARGIN, font="sans", size=6.4,
        color=MUTED, line_spacing=1.35, bbox_full_width=True,
        block_type="paragraph", role="small_print",
    )
    _signature_box(pg, width - MARGIN - 330, y + 44, 330, 94,
                   "Signature et cachet du médecin")
    _form_footer(pg, "Cabinet médical des Tilleuls")
    return pg


def build_form_inscription_dense() -> PageLayout:
    """Formulaire associatif très dense sur trois colonnes (difficile)."""
    pg = PageLayout(A4_PORTRAIT[0], A4_PORTRAIT[1], "portrait")
    width = pg.width
    y = _form_header(
        pg, "Association Sportive du Val d'Oise",
        ["Section multisports — saison 2026-2027",
         "Stade municipal, 4 rue des Peupliers, 95000 Cergy",
         "Tél. 01 34 41 27 65 — asvo@example.org"],
        "FORMULAIRE D'INSCRIPTION", reference="ASVO-2026-0734",
        date="9 novembre 2026", accent=ACCENT,
    )
    col_w = (width - 2 * MARGIN - 24) / 3
    xs = [MARGIN, MARGIN + col_w + 12, MARGIN + 2 * (col_w + 12)]
    y = _form_section(pg, MARGIN, y, width - 2 * MARGIN,
                      "Adhérent", accent=ACCENT)
    fields_left = [
        ("Nom de famille", "BAPTISTE"),
        ("Prénoms", "Céline, Louise"),
        ("Date de naissance", "7 décembre 1991"),
        ("Lieu de naissance", "Lille"),
        ("Sexe", "Féminin"),
        ("Nationalité", "Française"),
        ("Adresse", "9 rue de la Forêt"),
        ("Code postal", "59000"),
        ("Ville", "Lille"),
        ("Téléphone fixe", "03 20 41 58 77"),
        ("Téléphone mobile", "06 33 74 21 08"),
        ("Courriel", "celine.baptiste@example.fr"),
    ]
    fields_mid = [
        ("Profession", "Enseignante"),
        ("Catégorie socioprofessionnelle", "Cadre"),
        ("Situation familiale", "Pacsée"),
        ("Nombre d'enfants", "2"),
        ("Personne à prévenir", "M. Rémi Gauthier"),
        ("Téléphone d'urgence", "06 77 41 28 05"),
        ("Groupe sanguin", "O positif"),
        ("Allergies", "Pollen"),
        ("Traitement médical", "Aucun"),
        ("Médecin traitant", "Dr Hélène Bouchard"),
        ("Assurance", "MAIF — 98 765 432"),
        ("N° de licence", "ASVO-2026-8842"),
    ]
    fields_right = [
        ("Section choisie", "Natation"),
        ("Créneau", "Mercredi 18 h — 19 h 30"),
        ("Niveau", "Intermédiaire"),
        ("Tarif annuel", "285,00 €"),
        ("Mode de paiement", "Prélèvement SEPA"),
        ("Nombre de mensualités", "9"),
        ("Première échéance", "15 novembre 2026"),
        ("Certificat médical", "Fourni le 4 novembre 2026"),
        ("Autorisation photo", "Oui"),
        ("Droit à l'image", "Oui"),
        ("Règlement intérieur", "Accepté"),
        ("Date d'inscription", "9 novembre 2026"),
    ]
    for column_index, fields in enumerate((fields_left, fields_mid, fields_right)):
        cursor = y
        for label, value in fields:
            cursor = _form_field(pg, label, value, xs[column_index], cursor,
                                 col_w, label_width=150, size=7.1)
    y = max(y + 12 * 40, y + 480) + 4
    y = _form_section(pg, MARGIN, y, width - 2 * MARGIN,
                      "Activités complémentaires", accent=ACCENT)
    options = [
        ("Aquagym", True), ("Plongée", False), ("Sauvetage", True),
        ("Compétition", False), ("Loisir", True), ("Stage vacances", False),
    ]
    for index, (label, checked) in enumerate(options):
        pg.checkbox(xs[index % 3], y + (index // 3) * 30, label,
                    checked=checked, box_size=12, size=7.6)
    y += 74
    y = _form_section(pg, MARGIN, y, width - 2 * MARGIN,
                      "Paiement", accent=ACCENT)
    rows = [
        ["Échéance", "Date", "Montant", "Moyen", "Statut"],
        ["1/9", "15 novembre 2026", "31,67 €", "Prélèvement", "À venir"],
        ["2/9", "15 décembre 2026", "31,67 €", "Prélèvement", "À venir"],
        ["3/9", "15 janvier 2027", "31,67 €", "Prélèvement", "À venir"],
    ]
    table = pg.table(
        MARGIN, y, [120, 240, 180, 240, 256], rows, size=7.4,
        aligns=["center", "left", "right", "left", "left"],
        zebra=True, role="payment_schedule",
    )
    y = float(table["bbox"][3]) + 10
    pg.text(
        "L'adhésion prend effet à compter de la réception du dossier complet "
        "et du paiement de la première échéance. Le règlement intérieur est "
        "disponible au secrétariat et sur le site de l'association. "
        "Toute inscription est définitive après le délai légal de rétractation "
        "de quatorze jours.",
        MARGIN, y, width=width - 2 * MARGIN, font="sans", size=6.4,
        color=MUTED, line_spacing=1.35, bbox_full_width=True,
        block_type="paragraph", role="small_print",
    )
    _signature_box(pg, MARGIN, y + 52, 380, 90, "Signature de l'adhérent")
    _signature_box(pg, width - MARGIN - 380, y + 52, 380, 90,
                   "Signature du responsable associatif")
    _form_footer(pg, "Association Sportive du Val d'Oise")
    return pg


# ---------------------------------------------------------------------------
# Documents multicolonnes et correspondance
# ---------------------------------------------------------------------------


ARTICLE_CORPUS: list[str] = [
    "Les archives municipales ont rouvert leurs portes après dix-huit mois de "
    "travaux. Le nouveau bâtiment, construit à l'emplacement de l'ancienne "
    "école, accueille désormais l'ensemble des registres d'état civil, les "
    "plans cadastraux et une collection de photographies anciennes. Les "
    "chercheurs peuvent consulter les documents sur place, dans une salle "
    "éclairée naturellement et équipée de tables numériques.",
    "La restauration du patrimoine bâti demeure une priorité pour la commune. "
    "Le lavoir du hameau des Prés, classé monument historique, a retrouvé son "
    "toit de tuiles et ses bassins d'origine. Les artisans ont travaillé "
    "pendant six mois avec des matériaux traditionnels, sous la supervision "
    "de l'architecte des Bâtiments de France.",
    "Le marché des producteurs se tient chaque samedi matin sur la place de "
    "l'Église. Maraîchers, fromagers, apiculteurs et boulangers y proposent "
    "des produits de saison. Depuis le printemps, une dizaine de nouveaux "
    "exposants ont rejoint le marché, attirés par une fréquentation en hausse "
    "et par les travaux de réaménagement du centre-bourg.",
    "L'école élémentaire a inauguré son jardin pédagogique. Les élèves de CM1 "
    "et CM2 y cultivent des herbes aromatiques, des fraises et des tomates "
    "cerises. Ce projet, mené avec une association locale, vise à sensibiliser "
    "les enfants à l'alimentation durable et au rythme des saisons.",
    "La médiathèque propose un nouveau rendez-vous mensuel consacré aux "
    "littératures de l'imaginaire. Lectures, rencontres et ateliers d'écriture "
    "se succèdent dans l'auditorium. La prochaine séance accueillera une "
    "autrice de romans fantastiques, qui évoquera son travail de documentation "
    "et la construction de ses personnages.",
    "Le festival des conteurs revient pour une dixième édition. Pendant trois "
    "jours, les places et les jardins de la ville accueilleront des artistes "
    "venus de France, du Québec et du Sénégal. Les organisateurs ont prévu des "
    "spectacles gratuits pour les familles, ainsi que des ateliers destinés "
    "aux adolescents.",
    "La transition énergétique s'accélère dans les bâtiments publics. Les "
    "toitures de la halle des sports et du centre social seront équipées de "
    "panneaux photovoltaïques avant l'été. La production devrait couvrir près "
    "de quarante pour cent des besoins en électricité de ces deux équipements.",
    "Le conservatoire de musique ouvre une classe de harpe et un atelier de "
    "musique ancienne. Les inscriptions sont ouvertes aux enfants à partir de "
    "sept ans et aux adultes débutants. Les professeurs insistent sur la "
    "pratique collective, considérée comme un moteur essentiel de la "
    "progression.",
    "Les jardins partagés du quartier des Tilleuls comptent désormais soixante "
    "parcelles. Chaque adhérent s'engage à respecter une charte qui proscrit "
    "les pesticides et favorise le compostage. Un verger conservatoire, planté "
    "de variétés anciennes, sera ouvert au public à l'automne.",
    "Le réseau de chaleur alimenté par la géothermie entre en service. Il "
    "desservira à terme plus de deux mille logements, la piscine municipale et "
    "le groupe scolaire. Les premiers relevés montrent une baisse sensible de "
    "la consommation de gaz et une réduction des émissions de dioxyde de "
    "carbone.",
    "Les métiers d'art attirent un public toujours plus nombreux. L'atelier de "
    "céramique a accueilli près de huit cents visiteurs lors des journées "
    "portes ouvertes. Les artisans soulignent la difficulté de transmettre des "
    "gestes longs à apprendre, dans un contexte de concurrence accrue et de "
    "hausse du coût des matières premières.",
    "Le conseil municipal a voté le budget primitif 2027. Les dépenses "
    "d'investissement progressent de six pour cent, avec la rénovation de la "
    "salle polyvalente et la création d'une piste cyclable le long de la "
    "rivière. L'opposition s'est abstenue, regrettant le report du projet de "
    "maison de santé.",
    "La collecte des déchets évolue. À partir du 1er janvier, les bacs seront "
    "relevés tous les quinze jours et une redevance incitative sera "
    "expérimentée dans deux quartiers. La communauté de communes espère ainsi "
    "réduire de vingt pour cent le volume des ordures ménagères résiduelles.",
    "Le jumelage avec la ville de Tübingen fête ses trente ans. Un programme "
    "d'échanges scolaires, un cycle de conférences et une exposition "
    "photographique retraceront trois décennies d'amitié franco-allemande. Les "
    "familles d'accueil sont invitées à se faire connaître avant la fin du "
    "mois.",
    "La fête de la musique a rassemblé plusieurs milliers de spectateurs "
    "malgré une météo capricieuse. Les scènes installées dans les cours "
    "d'école, sur les places et devant la mairie ont accueilli chorales, "
    "groupes de rock et ensembles de jazz. Les commerçants du centre-ville "
    "saluent une fréquentation supérieure à celle de l'an dernier.",
    "Le chantier de la future ligne de tramway entre en phase de travaux "
    "préparatoires. Les réseaux d'eau et d'électricité seront déplacés avant "
    "l'été. Des réunions publiques sont organisées chaque mois pour présenter "
    "le calendrier, les déviations et les mesures d'accompagnement des "
    "commerces.",
    "L'association de sauvegarde du patrimoine a publié un inventaire des "
    "croix de chemin. Ce travail de recensement, mené pendant deux ans par une "
    "équipe de bénévoles, recense cent quarante-deux édifices, dont certains "
    "menacent de s'effondrer. Une souscription sera lancée au printemps pour "
    "financer les premières restaurations.",
    "La forêt de Fontainebleau subit les effets du changement climatique. Les "
    "sécheresses répétées affaiblissent les chênes et favorisent les scolytes, "
    "tandis que les tempêtes ont abattu des milliers d'arbres. Les forestiers "
    "expérimentent des mélanges d'essences plus résistants et protègent les "
    "sols par des cloisonnements adaptés.",
]

ARTICLE_HEADINGS: list[str] = [
    "Un patrimoine vivant",
    "École et jeunesse",
    "Culture et rencontres",
    "Transition écologique",
    "Vie locale",
    "Mobilités",
    "Agriculture et paysages",
    "Mémoire et transmission",
    "Économie et solidarités",
    "Chantiers et projets",
]


def build_multicolumn_doc(*, title: str, kicker: str, subtitle: str,
                          publication: str, date_line: str,
                          page_count: int, columns: int, body_size: float,
                          byline: str = "",
                          accent: Sequence[int] = ACCENT,
                          annex_table: dict[str, Any] | None = None,
                          ) -> list[PageLayout]:
    """Construit un document de presse/rapport en colonnes, sur plusieurs pages.

    Le texte est réparti colonne par colonne, puis page par page. Les
    paragraphes sont prélevés dans ``ARTICLE_CORPUS`` et entrecoupés de
    titres de section. Une annexe tabulaire peut être ajoutée à la fin.
    """
    width, height = A4_PORTRAIT
    gap = 28
    usable = width - 2 * MARGIN
    col_width = (usable - gap * (columns - 1)) / columns
    col_xs = [MARGIN + index * (col_width + gap) for index in range(columns)]
    bottom = height - 112

    items: list[tuple[str, str]] = []
    body_index = 0
    target = page_count * columns * 9
    for item_index in range(target):
        if item_index % 5 == 0:
            items.append(("heading",
                          ARTICLE_HEADINGS[(item_index // 5) % len(ARTICLE_HEADINGS)]))
        items.append(("body", ARTICLE_CORPUS[body_index % len(ARTICLE_CORPUS)]))
        body_index += 1

    pages: list[PageLayout] = []
    cursor = 0
    for page_no in range(1, page_count + 1):
        pg = PageLayout(width, height, "portrait")
        pg.rect(0, 0, width, 10, fill=accent)
        pg.text(publication, MARGIN, 32, width=700, font="sans_bold", size=10.5,
                color=accent, block_type="header", role="publication")
        pg.text(date_line, width - MARGIN - 420, 34, width=420, align="right",
                font="sans", size=7.8, color=MUTED, block_type="header",
                role="date")
        if page_no == 1:
            pg.text(kicker, MARGIN, 84, width=900, font="sans_bold", size=8.5,
                    color=ACCENT_2, block_type="paragraph", role="kicker")
            title_block = pg.text(title, MARGIN, 106, width=usable,
                                  font="serif_bold", size=30.0,
                                  line_spacing=1.08, block_type="title",
                                  role="headline", bbox_full_width=True)
            cursor_y = float(title_block["bbox"][3]) + 8
            subtitle_block = pg.text(
                subtitle, MARGIN, cursor_y, width=usable - 120,
                font="serif_italic", size=11.6, color=(66, 66, 70),
                line_spacing=1.3, block_type="paragraph", role="standfirst",
                bbox_full_width=True,
            )
            cursor_y = float(subtitle_block["bbox"][3]) + 6
            if byline:
                byline_block = pg.text(
                    byline, MARGIN, cursor_y, width=600, font="sans",
                    size=8.0, color=MUTED, block_type="paragraph", role="byline",
                )
                cursor_y = float(byline_block["bbox"][3]) + 8
            pg.hline(MARGIN, width - MARGIN, cursor_y, color=RULE, width=0.8)
            body_top = cursor_y + 20
        else:
            pg.text(f"{publication} — {title}", MARGIN, 70, width=880,
                    font="serif_bold", size=13.0, color=INK,
                    block_type="paragraph", role="running_title")
            pg.hline(MARGIN, width - MARGIN, 106, color=RULE, width=0.8)
            body_top = 124

        y = body_top
        col = 0
        while cursor < len(items) and col < columns:
            kind, text = items[cursor]
            font = "serif" if kind == "body" else "sans_bold"
            size = body_size if kind == "body" else body_size + 3.0
            spacing = 1.34 if kind == "body" else 1.18
            gap_before = 12 if kind == "heading" else 0
            needed = text_height(text, font, size, col_width, spacing)
            # Évite un titre de section orphelin en bas de colonne : on exige
            # que le premier paragraphe qui le suit tienne aussi.
            if kind == "heading" and cursor + 1 < len(items):
                next_text = items[cursor + 1][1]
                next_needed = text_height(next_text, "serif", body_size,
                                          col_width, 1.34)
                if (y + gap_before + needed + 7 + next_needed > bottom
                        and y > body_top + 4):
                    col += 1
                    y = body_top
                    continue
            if y + gap_before + needed > bottom and y > body_top + 4:
                col += 1
                y = body_top
                continue
            if y + gap_before + needed > bottom and y <= body_top + 4:
                break
            y += gap_before
            block = pg.text(
                text, col_xs[col], y, width=col_width, font=font, size=size,
                line_spacing=spacing, block_type="paragraph",
                role=kind if kind == "body" else "heading",
                bbox_full_width=True,
            )
            if block is None:
                cursor += 1
                continue
            y = float(block["bbox"][3]) + (10 if kind == "heading" else 7)
            cursor += 1

        pg.hline(MARGIN, width - MARGIN, height - 86, color=RULE, width=0.7)
        pg.text(f"{publication} — {date_line}", MARGIN, height - 74,
                width=760, font="sans", size=6.6, color=MUTED,
                block_type="footer", role="footer")
        pg.text(f"Page {page_no} / {page_count + (1 if annex_table else 0)}",
                width - MARGIN - 120, height - 74, width=120, align="right",
                font="sans", size=6.8, color=MUTED, block_type="footer",
                role="page_number")
        pages.append(pg)

    if annex_table:
        pg = PageLayout(width, height, "portrait")
        pg.rect(0, 0, width, 10, fill=accent)
        pg.text(publication, MARGIN, 32, width=700, font="sans_bold",
                size=10.5, color=accent, block_type="header",
                role="publication")
        pg.text(annex_table.get("title", "Annexe"), MARGIN, 100, width=usable,
                font="serif_bold", size=22, color=INK, block_type="title",
                role="annex_title")
        note = annex_table.get("note", "")
        y = 150
        if note:
            block = pg.text(note, MARGIN, y, width=usable - 80, font="serif",
                            size=9.4, line_spacing=1.35,
                            bbox_full_width=True, block_type="paragraph",
                            role="annex_note")
            y = float(block["bbox"][3]) + 24
        table = pg.table(
            MARGIN, y, annex_table["col_widths"], annex_table["rows"],
            size=float(annex_table.get("size", 8.2)),
            header_bg=accent, aligns=annex_table.get("aligns"),
            zebra=True, role="annex_table",
        )
        if table:
            y = float(table["bbox"][3]) + 26
        pg.text(
            "Source : données internes de la collectivité, consolidation 2026. "
            "Les chiffres sont arrondis et peuvent ne pas totaliser exactement "
            "les sommes indiquées.",
            MARGIN, y, width=usable - 120, font="sans", size=6.8,
            color=MUTED, line_spacing=1.35, bbox_full_width=True,
            block_type="paragraph", role="source_note",
        )
        pg.hline(MARGIN, width - MARGIN, height - 86, color=RULE, width=0.7)
        pg.text(f"{publication} — {date_line}", MARGIN, height - 74,
                width=760, font="sans", size=6.6, color=MUTED,
                block_type="footer", role="footer")
        pg.text(f"Page {page_count + 1} / {page_count + 1}",
                width - MARGIN - 120, height - 74, width=120, align="right",
                font="sans", size=6.8, color=MUTED, block_type="footer",
                role="page_number")
        pages.append(pg)
    return pages


def build_letter(*, sender: dict[str, Any], recipient: str,
                 recipient_address: Sequence[str], date: str, subject: str,
                 paragraphs: Sequence[str], signer: str,
                 footer_note: str = "") -> PageLayout:
    """Lettre administrative ou commerciale sur une page native."""
    pg = PageLayout(A4_PORTRAIT[0], A4_PORTRAIT[1], "portrait")
    width = pg.width
    pg.rect(0, 0, width, 12, fill=ACCENT)
    pg.text(sender["name"], MARGIN, 44, width=720, font="sans_bold", size=13,
            color=ACCENT, block_type="header", role="company")
    pg.text("\n".join([sender["activity"]] + sender["address"] +
                      [f"Tél. {sender['phone']}", sender["email"]]),
            MARGIN, 84, width=620, font="sans", size=7.6, color=MUTED,
            line_spacing=1.36, block_type="header", role="company_details")
    pg.text(date, width - MARGIN - 360, 52, width=360, align="right",
            font="sans", size=8.6, block_type="paragraph", role="date")
    pg.text(recipient + "\n" + "\n".join(recipient_address),
            width - MARGIN - 440, 150, width=440, font="sans", size=8.8,
            line_spacing=1.4, block_type="paragraph", role="recipient")
    pg.text(subject, MARGIN, 300, width=width - 2 * MARGIN,
            font="sans_bold", size=10.2, block_type="paragraph",
            role="subject")
    y = 340.0
    for paragraph in paragraphs:
        block = pg.text(paragraph, MARGIN, y, width=width - 2 * MARGIN - 40,
                        font="serif", size=10.2, line_spacing=1.45,
                        bbox_full_width=True, block_type="paragraph",
                        role="body")
        y = float(block["bbox"][3]) + 12 if block else y + 24
    y += 16
    pg.text("Veuillez agréer, Madame, Monsieur, l'expression de nos salutations "
            "distinguées.", MARGIN, y, width=width - 2 * MARGIN - 40,
            font="serif", size=10.2, line_spacing=1.45,
            bbox_full_width=True, block_type="paragraph", role="closing")
    y += 54
    signature = pg.text(signer, width - MARGIN - 360, y, width=360,
                        align="right", font="sans_bold", size=9.6,
                        block_type="paragraph", role="signature")
    signature_bottom = float(signature["bbox"][3]) if signature else y + 22
    pg.text(sender["name"], width - MARGIN - 360, signature_bottom + 4,
            width=360, align="right", font="sans", size=8.0, color=MUTED,
            block_type="paragraph", role="signature_details")
    pg.hline(MARGIN, width - MARGIN, pg.height - 92, color=RULE, width=0.7)
    pg.text(footer_note or (
        f"{sender['name']} — SIRET {sender['siret']} — TVA {sender['tva']} — "
        "Correspondance à retourner au service concerné."
    ), MARGIN, pg.height - 80, width=width - 2 * MARGIN - 100,
        font="sans", size=6.4, color=MUTED, block_type="footer",
        role="legal")
    return pg


def build_note_page(*, title: str, reference: str, body: Sequence[str],
                    sender: dict[str, Any]) -> PageLayout:
    """Note interne d'une page, utile pour les PDF mixtes."""
    pg = PageLayout(A4_PORTRAIT[0], A4_PORTRAIT[1], "portrait")
    width = pg.width
    pg.rect(0, 0, width, 12, fill=ACCENT_3)
    pg.text(sender["name"], MARGIN, 42, width=700, font="sans_bold", size=12,
            color=ACCENT_3, block_type="header", role="company")
    pg.text("NOTE INTERNE", width - MARGIN - 360, 40, width=360,
            align="right", font="sans_bold", size=18, color=ACCENT_3,
            block_type="title", role="document_title")
    pg.text(f"Réf. : {reference}", width - MARGIN - 360, 80, width=360,
            align="right", font="sans", size=8.2, color=MUTED,
            block_type="paragraph", role="reference")
    pg.text(title, MARGIN, 130, width=width - 2 * MARGIN, font="sans_bold",
            size=15, block_type="title", role="title")
    y = 180.0
    for paragraph in body:
        block = pg.text(paragraph, MARGIN, y, width=width - 2 * MARGIN - 60,
                        font="sans", size=9.4, line_spacing=1.45,
                        bbox_full_width=True, block_type="paragraph",
                        role="body")
        y = float(block["bbox"][3]) + 14 if block else y + 24
    pg.hline(MARGIN, width - MARGIN, pg.height - 92, color=RULE, width=0.7)
    pg.text("Document interne — ne pas diffuser sans autorisation.",
            MARGIN, pg.height - 80, width=600, font="sans", size=6.6,
            color=MUTED, block_type="footer", role="legal")
    return pg


# ---------------------------------------------------------------------------
# Construction des 42 documents
# ---------------------------------------------------------------------------


def _scan_note(prefix: str, effects: dict[str, Any]) -> str:
    details = ", ".join(_scan_notes(effects))
    return f"{prefix} Dégradations : {details}."


def build_fact_items(rng: random.Random) -> list[ItemPlan]:
    """12 factures : 3 faciles, 5 moyennes, 4 difficiles."""
    specs = [
        # (n°, difficulté, variante, nb lignes, taux, remise, acompte,
        #  émetteur, client, numéro, date, échéance, notes)
        (1, "easy", "classic", 5, None, 0.0, 0.0, 0, 0,
         "FA-2026-0101", "12 janvier 2026", "11 février 2026",
         "Facture propre à une colonne, cinq articles, tableau tarifs/TVA/montant."),
        (2, "easy", "classic", 6, None, 0.0, 0.0, 2, 2,
         "FA-2026-0102", "3 mars 2026", "2 avril 2026",
         "Facture de librairie, six articles, taux réduit et taux normal."),
        (3, "easy", "two_col", 6, None, 0.0, 0.0, 3, 3,
         "FA-2026-0103", "21 avril 2026", "21 mai 2026",
         "En-tête émetteur/client en deux colonnes, six articles."),
        (4, "medium", "two_col", 10, None, 0.0, 0.0, 1, 1,
         "FA-2026-0104", "9 juin 2026", "9 juillet 2026",
         "Dix articles, deux colonnes d'en-tête, totaux détaillés."),
        (5, "medium", "classic", 12, None, 0.0, 0.0, 0, 4,
         "FA-2026-0105", "18 septembre 2026", "18 octobre 2026",
         "Douze articles, tableau dense et mentions légales longues."),
        (6, "medium", "landscape", 9, None, 0.0, 0.0, 2, 5,
         "FA-2026-0106", "7 octobre 2026", "6 novembre 2026",
         "Facture au format paysage, tableau large et colonnes aérées."),
        (7, "medium", "classic", 10, [20.0, 5.5], 3.0, 0.0, 1, 6,
         "FA-2026-0107", "14 octobre 2026", "13 novembre 2026",
         "Deux taux de TVA, remise de 3 %, dix articles."),
        (8, "medium", "two_col", 12, None, 0.0, 500.0, 3, 7,
         "FA-2026-0108", "2 novembre 2026", "2 décembre 2026",
         "Douze articles, acompte de 500 €, en-tête en deux colonnes."),
        (9, "hard", "dense_split", 20, [20.0, 5.5], 0.0, 0.0, 0, 0,
         "FA-2026-0109", "9 novembre 2026", "9 décembre 2026",
         "Facture dense sur deux pages, vingt articles, petits corps."),
        (10, "hard", "dense", 18, [20.0, 10.0, 5.5], 0.0, 0.0, 1, 2,
         "FA-2026-0110", "13 novembre 2026", "13 décembre 2026",
         "Facture dense sur une page, dix-huit articles, trois taux de TVA."),
        (11, "hard", "dense_split", 24, [20.0, 5.5], 4.0, 800.0, 2, 4,
         "FA-2026-0111", "16 novembre 2026", "16 décembre 2026",
         "Vingt-quatre articles sur deux pages, remise et acompte."),
        (12, "hard", "landscape", 14, [20.0, 10.0, 5.5], 2.0, 0.0, 3, 5,
         "FA-2026-0112", "20 novembre 2026", "20 décembre 2026",
         "Paysage dense, quatorze articles, trois taux, remise de 2 %."),
    ]
    items: list[ItemPlan] = []
    for (index, difficulty, variant, n_rows, rates, discount, deposit,
         emitter_index, client_index, number, date, due, notes) in specs:
        data = make_invoice_data(
            rng, n_rows, number=number, date=date, due_date=due,
            emitter=EMITTERS[emitter_index], client=CLIENTS[client_index],
            rates=rates, discount_pct=discount, deposit=deposit,
        )
        if variant == "classic":
            layouts = build_invoice_classic(data)
        elif variant == "two_col":
            layouts = build_invoice_two_col(data)
        elif variant == "landscape":
            layouts = build_invoice_landscape(data)
        elif variant == "dense_split":
            layouts = build_invoice_dense(data, split_rows=True, rows_per_page=14)
        else:
            layouts = build_invoice_dense(data, split_rows=False)
        full_notes = (
            notes + " Accents et cédilles présents (é, è, à, ç, œ)."
        )
        items.append(make_item(
            "fact", index, "facts", "facture", difficulty, layouts,
            full_notes, subtype=variant,
        ))
    return items


def build_form_items() -> list[ItemPlan]:
    """9 formulaires papier : 3 faciles, 3 moyens, 3 difficiles."""
    specs = [
        (1, "easy", "renseignements", build_form_renseignements(),
         "Fiche de renseignements, champs alignés et cases à cocher."),
        (2, "easy", "sepa", build_form_sepa(),
         "Autorisation SEPA, IBAN et BIC, consentement et signature."),
        (3, "easy", "intervention", build_form_intervention(),
         "Bon d'intervention, checklist de six opérations."),
        (4, "medium", "commande", build_form_commande(),
         "Bon de commande, tableau de sept lignes et deux blocs d'adresse."),
        (5, "medium", "conges", build_form_conges(),
         "Demande de congés, tableau de périodes et soldes."),
        (6, "medium", "inscription", build_form_inscription(),
         "Inscription scolaire en deux colonnes, options et consentement."),
        (7, "hard", "sinistre", build_form_sinistre(),
         "Déclaration de sinistre, grille dense sur trois colonnes, petits corps."),
        (8, "hard", "medical", build_form_medical(),
         "Fiche médicale, antécédents, constantes et prescriptions, trois colonnes."),
        (9, "hard", "inscription_dense", build_form_inscription_dense(),
         "Inscription associative très dense, trente-six champs et tableau de paiement."),
    ]
    items: list[ItemPlan] = []
    for index, difficulty, subtype, layout, notes in specs:
        items.append(make_item(
            "form", index, "forms", "formulaire", difficulty, [layout],
            notes + " Formulaire en français avec accents et cédilles.",
            subtype=subtype,
        ))
    return items


def build_scan_items(rng: random.Random) -> list[ItemPlan]:
    """9 scans dégradés : 2 faciles, 3 moyens, 4 difficiles."""
    items: list[ItemPlan] = []

    data1 = make_invoice_data(
        rng, 7, number="SC-2026-001", date="6 février 2026",
        due_date="8 mars 2026", emitter=EMITTERS[0], client=CLIENTS[0],
    )
    picture = build_invoice_classic(data1)[0]
    effects = dict(rotation=0.4, contrast=0.97, noise=2.5, dust=4,
                   jpeg_quality=82)
    items.append(make_item(
        "scan", 1, "scans", "scan", "easy", [scan_page(picture, **effects)],
        _scan_note("Facture imprimée, scan propre.", effects),
        subtype="facture_propre",
    ))

    picture = build_form_renseignements()
    effects = dict(rotation=-0.8, contrast=0.96, brightness=1.02, noise=3.0,
                   dust=6, jpeg_quality=78)
    items.append(make_item(
        "scan", 2, "scans", "scan", "easy", [scan_page(picture, **effects)],
        _scan_note("Formulaire papier, scan propre en JPEG.", effects),
        subtype="formulaire_propre", filetype="jpg",
    ))

    data3 = make_invoice_data(
        rng, 9, number="SC-2026-003", date="4 mai 2026",
        due_date="3 juin 2026", emitter=EMITTERS[1], client=CLIENTS[1],
        rates=[20.0, 5.5],
    )
    picture = build_invoice_two_col(data3)[0]
    effects = dict(rotation=2.2, contrast=0.72, brightness=1.05, blur=0.6,
                   noise=8.0, dust=18, jpeg_quality=55)
    items.append(make_item(
        "scan", 3, "scans", "scan", "medium",
        [scan_page(picture, **effects)],
        _scan_note("Facture inclinée et compressée.", effects),
        subtype="facture_inclinee",
    ))

    picture = build_form_commande()
    effects = dict(rotation=-2.8, contrast=0.68, brightness=1.04,
                   illumination=0.28, noise=10.0, blur=0.5, dust=25,
                   jpeg_quality=48)
    items.append(make_item(
        "scan", 4, "scans", "scan", "medium",
        [scan_page(picture, **effects)],
        _scan_note("Bon de commande, éclairage inégal.", effects),
        subtype="bon_commande",
    ))

    article = build_multicolumn_doc(
        title="Les archives municipales rouvrent leurs portes",
        kicker="PATRIMOINE", subtitle="Un nouveau bâtiment pour les fonds anciens.",
        publication="Revue des patrimoines", date_line="9 novembre 2026",
        page_count=1, columns=2, body_size=9.6,
        byline="Par Élodie Marchand", accent=ACCENT_3,
    )[0]
    effects = dict(rotation=1.7, contrast=0.78, brightness=1.03, noise=7.0,
                   dust=15, jpeg_quality=45)
    items.append(make_item(
        "scan", 5, "scans", "scan", "medium", [scan_page(article, **effects)],
        _scan_note("Page de revue en deux colonnes, scan médiocre.", effects),
        subtype="article_deux_colonnes",
    ))

    data6 = make_invoice_data(
        rng, 18, number="SC-2026-006", date="7 septembre 2026",
        due_date="7 octobre 2026", emitter=EMITTERS[0], client=CLIENTS[4],
        rates=[20.0, 10.0, 5.5],
    )
    picture = build_invoice_dense(data6, split_rows=False)[0]
    effects = dict(rotation=4.5, contrast=0.45, brightness=1.08, blur=1.0,
                   noise=15.0, dust=30, jpeg_quality=30, stamp="DUPLICATA")
    items.append(make_item(
        "scan", 6, "scans", "scan", "hard", [scan_page(picture, **effects)],
        _scan_note("Facture dense, tampon DUPLICATA, contraste faible.", effects),
        subtype="facture_dense_tampon",
    ))

    picture = build_form_sinistre()
    effects = dict(rotation=-6.0, contrast=0.40, brightness=1.10,
                   illumination=0.35, vignette=0.25, noise=18.0, blur=0.8,
                   dust=45, jpeg_quality=28)
    items.append(make_item(
        "scan", 7, "scans", "scan", "hard", [scan_page(picture, **effects)],
        _scan_note("Formulaire dense fortement dégradé.", effects),
        subtype="formulaire_degrade",
    ))

    data8 = make_invoice_data(
        rng, 12, number="SC-2026-008", date="19 octobre 2026",
        due_date="18 novembre 2026", emitter=EMITTERS[2], client=CLIENTS[6],
    )
    picture = build_invoice_classic(data8)[0]
    effects = dict(rotation=8.0, downsample=0.62, contrast=0.52,
                   brightness=1.06, blur=1.2, noise=18.0, dust=35,
                   grayscale=True, jpeg_quality=30)
    items.append(make_item(
        "scan", 8, "scans", "scan", "hard", [scan_page(picture, **effects)],
        _scan_note("Scan très incliné, basse résolution et bruit.", effects),
        subtype="facture_basse_resolution",
    ))

    picture = build_form_medical()
    effects = dict(rotation=-4.2, grayscale=True, contrast=0.42,
                   brightness=1.06, illumination=0.30, noise=20.0, blur=0.7,
                   dust=60, stamp="CONFIDENTIEL", stamp_alpha=38,
                   stamp_angle=-24)
    items.append(make_item(
        "scan", 9, "scans", "scan", "hard", [scan_page(picture, **effects)],
        _scan_note("Fiche médicale en niveaux de gris, PNG bruité.", effects),
        subtype="fiche_medicale", filetype="png",
    ))
    return items


def build_mixed_items(rng: random.Random) -> list[ItemPlan]:
    """12 PDF : 6 documents longues colonnes + 6 PDF natifs/scannés."""
    items: list[ItemPlan] = []

    # --- Documents multicolonnes longs (natifs) -----------------------------
    layouts = build_multicolumn_doc(
        title="Les archives municipales rouvrent leurs portes",
        kicker="ÉDITION DU 9 NOVEMBRE 2026",
        subtitle="Après dix-huit mois de travaux, le nouveau bâtiment accueille "
                 "les fonds anciens et une salle de lecture lumineuse.",
        publication="Le Journal du Vexin", date_line="9 novembre 2026",
        page_count=2, columns=2, body_size=11.0,
        byline="Par Élodie Marchand", accent=ACCENT,
    )
    items.append(make_item(
        "mixed", 1, "mixed", "document_multicolonne", "easy",
        native_pages(layouts),
        "Journal local de deux pages, deux colonnes, corps 11 pt, texte natif.",
        subtype="journal_deux_colonnes",
    ))

    layouts = build_multicolumn_doc(
        title="Une année de projets et de solidarités",
        kicker="COMMUNE DE SAINT-RÉMY-LÈS-CHEVREUSE",
        subtitle="Le conseil municipal présente les actions menées en 2025 et "
                 "les perspectives pour 2026.",
        publication="Rapport d'activité 2025", date_line="9 novembre 2026",
        page_count=2, columns=2, body_size=10.5,
        byline="Le maire et son équipe", accent=ACCENT_3,
    )
    items.append(make_item(
        "mixed", 2, "mixed", "document_multicolonne", "easy",
        native_pages(layouts),
        "Rapport d'activité de deux pages, deux colonnes, titres de section.",
        subtype="rapport_activite",
    ))

    layouts = build_multicolumn_doc(
        title="Les archives municipales rouvrent leurs portes",
        kicker="PATRIMOINE",
        subtitle="Dix-huit mois de travaux, un nouveau bâtiment et une salle de "
                 "lecture ouverte aux chercheurs comme aux curieux.",
        publication="Revue des patrimoines", date_line="9 novembre 2026",
        page_count=3, columns=3, body_size=9.4,
        byline="Par Élodie Marchand", accent=ACCENT_3,
    )
    items.append(make_item(
        "mixed", 3, "mixed", "document_multicolonne", "medium",
        native_pages(layouts),
        "Article de trois pages en trois colonnes, corps 9,4 pt, dix sections.",
        subtype="article_trois_colonnes",
    ))

    layouts = build_multicolumn_doc(
        title="Le printemps des jardins partagés",
        kicker="INFORMATION MUNICIPALE",
        subtitle="Soixante parcelles, un verger conservatoire et une charte "
                 "commune pour les habitants du quartier des Tilleuls.",
        publication="Bulletin municipal — Printemps 2026",
        date_line="9 novembre 2026",
        page_count=3, columns=2, body_size=9.8,
        byline="Service communication", accent=ACCENT_2,
    )
    items.append(make_item(
        "mixed", 4, "mixed", "document_multicolonne", "medium",
        native_pages(layouts),
        "Bulletin municipal de trois pages, deux colonnes, texte natif.",
        subtype="bulletin_municipal",
    ))

    layouts = build_multicolumn_doc(
        title="La forêt de Fontainebleau face au changement climatique",
        kicker="ENVIRONNEMENT",
        subtitle="Sécheresses, scolytes et tempêtes transforment un massif "
                 "emblématique. Les forestiers adaptent leurs pratiques.",
        publication="Cahiers du Gâtinais", date_line="9 novembre 2026",
        page_count=4, columns=3, body_size=8.6,
        byline="Par François Lefèvre", accent=ACCENT,
        annex_table={
            "title": "Annexe — Indicateurs climatiques du massif",
            "note": "Relevés annuels consolidés par l'observatoire forestier "
                    "régional. Les valeurs sont exprimées en millimètres et en "
                    "jours de stress hydrique.",
            "col_widths": [276, 156, 156, 156, 156, 156],
            "aligns": ["left", "center", "center", "center", "center", "center"],
            "rows": [
                ["Indicateur", "2015", "2020", "2024", "2025", "Évolution"],
                ["Précipitations annuelles", "720 mm", "645 mm", "598 mm",
                 "612 mm", "-15 %"],
                ["Jours de stress hydrique", "18", "27", "39", "41", "+23 j"],
                ["Arbres dépérissants", "4 %", "7 %", "12 %", "14 %", "+10 pts"],
                ["Surface boisée", "20 300 ha", "20 180 ha", "20 040 ha",
                 "20 010 ha", "-1,4 %"],
                ["Température moyenne", "11,2 °C", "11,8 °C", "12,4 °C",
                 "12,5 °C", "+1,3 °C"],
            ],
        },
    )
    items.append(make_item(
        "mixed", 5, "mixed", "document_multicolonne", "hard",
        native_pages(layouts),
        "Étude de quatre pages en trois colonnes, corps 8,6 pt, annexe "
        "tabulaire de cinq indicateurs.",
        subtype="etude_foret",
    ))

    layouts = build_multicolumn_doc(
        title="Patrimoine et numérique : ce que les archives changent",
        kicker="DOSSIER",
        subtitle="Numérisation, intelligence artificielle et médiation : "
                 "les institutions patrimoniales réinventent leurs usages.",
        publication="Revue des patrimoines", date_line="9 novembre 2026",
        page_count=4, columns=3, body_size=8.0,
        byline="Collectif", accent=ACCENT_3,
        annex_table={
            "title": "Annexe — Corpus documentaire numérisé",
            "note": "État des collections décrites dans le dossier. Les "
                    "métadonnées ont été contrôlées par les équipes "
                    "scientifiques lors de la campagne 2026.",
            "col_widths": [230, 218, 230, 180, 198],
            "aligns": ["left", "center", "center", "center", "center"],
            "rows": [
                ["Fonds", "Période", "Pièces", "Images", "Numérisation"],
                ["État civil", "1792-1920", "12 480", "38 200", "Terminée"],
                ["Plans cadastraux", "1810-1985", "3 210", "6 420", "En cours"],
                ["Photographies", "1880-1990", "8 760", "8 760", "Terminée"],
                ["Délibérations", "1837-2024", "5 940", "11 880", "En cours"],
                ["Correspondance", "1914-1948", "2 105", "4 210", "À lancer"],
            ],
        },
    )
    items.append(make_item(
        "mixed", 6, "mixed", "document_multicolonne", "hard",
        native_pages(layouts),
        "Dossier de quatre pages en trois colonnes, corps 8 pt, annexe "
        "tabulaire de cinq fonds documentaires.",
        subtype="dossier_patrimoine",
    ))

    # --- PDF mixtes natif + scanné ------------------------------------------
    letter = build_letter(
        sender=EMITTERS[0], recipient="Madame Élodie Marchand",
        recipient_address=["14 rue des Écoles", "75011 Paris"],
        date="9 novembre 2026",
        subject="Objet : confirmation de commande et calendrier de livraison",
        paragraphs=[
            "Madame, nous accusons réception de votre commande passée le "
            "2 novembre 2026 et vous remercions de votre confiance.",
            "Les articles disponibles partiront de nos entrepôts le "
            "16 novembre. La livraison sur site est prévue le 18 novembre "
            "entre 9 h et 12 h. Un avis de passage vous sera adressé la veille.",
            "Le solde de la facture, joint au présent courrier, est payable à "
            "trente jours. Notre service commercial reste à votre disposition "
            "pour toute précision.",
        ],
        signer="Claire Dupont, responsable commerciale",
    )
    data7 = make_invoice_data(
        rng, 7, number="FA-2026-0207", date="9 novembre 2026",
        due_date="9 décembre 2026", emitter=EMITTERS[0], client=CLIENTS[0],
    )
    scanned7 = build_invoice_classic(data7)[0]
    effects7 = dict(rotation=-0.9, contrast=0.90, brightness=1.02, noise=4.0,
                    dust=8, jpeg_quality=72)
    items.append(make_item(
        "mixed", 7, "mixed", "mixte", "easy",
        [PagePlan(layout=letter), scan_page(scanned7, **effects7)],
        "Page 1 native (lettre) + page 2 scannée (facture). "
        + _scan_note("Facture jointe.", effects7),
        subtype="lettre_et_facture",
    ))

    note = build_note_page(
        title="Transmission du dossier d'inscription",
        reference="NOTE-2026-0142",
        body=[
            "Vous trouverez ci-joint le dossier d'inscription complété par la "
            "famille Gauthier. Les pièces justificatives ont été vérifiées par "
            "le secrétariat.",
            "Merci de bien vouloir confirmer la réception du dossier et de "
            "nous indiquer la date de la commission d'admission.",
        ],
        sender=EMITTERS[2],
    )
    scanned8 = build_form_inscription()
    effects8 = dict(rotation=0.7, contrast=0.94, noise=3.5, dust=9,
                    jpeg_quality=76)
    items.append(make_item(
        "mixed", 8, "mixed", "mixte", "easy",
        [PagePlan(layout=note), scan_page(scanned8, **effects8)],
        "Page 1 native (note interne) + page 2 scannée (dossier d'inscription). "
        + _scan_note("Formulaire joint.", effects8),
        subtype="note_et_formulaire",
    ))

    report = build_multicolumn_doc(
        title="Rapport d'activité 2025 — synthèse",
        kicker="COMMUNE DE SAINT-RÉMY-LÈS-CHEVREUSE",
        subtitle="Deux pages de synthèse avant les annexes financières.",
        publication="Rapport d'activité 2025", date_line="9 novembre 2026",
        page_count=2, columns=2, body_size=10.2, accent=ACCENT_3,
    )
    data9 = make_invoice_data(
        rng, 10, number="FA-2026-0309", date="9 novembre 2026",
        due_date="9 décembre 2026", emitter=EMITTERS[1], client=CLIENTS[2],
        discount_pct=2.0,
    )
    scanned9 = build_invoice_classic(data9)[0]
    effects9 = dict(rotation=2.4, contrast=0.70, brightness=1.04, blur=0.4,
                    noise=9.0, dust=20, jpeg_quality=52)
    pages9 = native_pages(report) + [scan_page(scanned9, **effects9)]
    items.append(make_item(
        "mixed", 9, "mixed", "mixte", "medium", pages9,
        "Deux pages natives (rapport) + annexe facture scannée. "
        + _scan_note("Annexe.", effects9),
        subtype="rapport_et_facture",
    ))

    data10 = make_invoice_data(
        rng, 8, number="FA-2026-0410", date="9 novembre 2026",
        due_date="9 décembre 2026", emitter=EMITTERS[3], client=CLIENTS[3],
        rates=[20.0, 5.5],
    )
    scanned10 = build_invoice_two_col(data10)[0]
    effects10 = dict(rotation=-2.1, contrast=0.74, brightness=1.03, noise=8.0,
                     dust=16, jpeg_quality=50)
    reply = build_letter(
        sender=EMITTERS[3], recipient="Madame Amélie Côté",
        recipient_address=["27 chemin des Vignes", "33000 Bordeaux"],
        date="9 novembre 2026",
        subject="Objet : réponse à votre demande de devis complémentaire",
        paragraphs=[
            "Madame, nous avons bien reçu votre facture du 4 novembre ainsi "
            "que votre demande de précisions sur les postes de menuiserie.",
            "Les dimensions indiquées sont compatibles avec le modèle choisi. "
            "Un technicien se rendra sur place le 20 novembre pour relever les "
            "cotes définitives et vérifier l'état du support.",
            "Le devis complémentaire vous sera transmis sous cinq jours ouvrés. "
            "Les délais de fabrication restent estimés à six semaines.",
        ],
        signer="Philippe Delaunay, gérant",
    )
    scanned10b = build_form_renseignements()
    effects10b = dict(rotation=1.3, contrast=0.88, brightness=1.02, noise=5.0,
                      dust=10, jpeg_quality=68)
    items.append(make_item(
        "mixed", 10, "mixed", "mixte", "medium",
        [scan_page(scanned10, **effects10), PagePlan(layout=reply),
         scan_page(scanned10b, **effects10b)],
        "Page 1 facture scannée, page 2 lettre native, page 3 formulaire "
        "scanné. " + _scan_note("Facture et formulaire.",
                                {**effects10, **effects10b}),
        subtype="facture_lettre_formulaire",
    ))

    data11 = make_invoice_data(
        rng, 20, number="FA-2026-0511", date="9 novembre 2026",
        due_date="9 décembre 2026", emitter=EMITTERS[2], client=CLIENTS[5],
        rates=[20.0, 5.5], discount_pct=3.0,
    )
    dense11 = build_invoice_dense(data11, split_rows=True, rows_per_page=12)
    effects11 = dict(rotation=-3.6, contrast=0.48, brightness=1.08, blur=0.9,
                     noise=16.0, dust=32, jpeg_quality=33)
    letter11 = build_letter(
        sender=EMITTERS[2], recipient="SCI du Pré Fleuri",
        recipient_address=["12 allée des Tilleuls", "67000 Strasbourg"],
        date="9 novembre 2026",
        subject="Objet : facture et relevé des livraisons",
        paragraphs=[
            "Madame, Monsieur, veuillez trouver ci-joint la facture "
            "FA-2026-0511 ainsi que le relevé détaillé des livraisons du mois "
            "d'octobre.",
            "Certaines références ont été expédiées en deux fois en raison "
            "d'une rupture temporaire d'approvisionnement. Les quantités "
            "facturées correspondent aux livraisons effectives.",
            "Nous restons à votre disposition pour corriger toute erreur de "
            "référence ou de quantité.",
        ],
        signer="Service comptabilité",
    )
    items.append(make_item(
        "mixed", 11, "mixed", "mixte", "hard",
        [PagePlan(layout=letter11)] + [scan_page(page, **effects11)
                                       for page in dense11],
        "Page 1 native (lettre) + deux pages de facture dense scannée. "
        + _scan_note("Facture.", effects11),
        subtype="lettre_et_facture_dense",
    ))

    article12 = build_multicolumn_doc(
        title="Patrimoine et numérique : ce que les archives changent",
        kicker="DOSSIER", subtitle="Deux pages de synthèse avant les pièces "
        "justificatives numérisées.",
        publication="Revue des patrimoines", date_line="9 novembre 2026",
        page_count=2, columns=2, body_size=9.8, accent=ACCENT_3,
    )
    scanned12a = build_form_sinistre()
    effects12a = dict(rotation=4.8, contrast=0.42, brightness=1.09,
                      illumination=0.30, noise=17.0, blur=0.8, dust=40,
                      jpeg_quality=30, stamp="DUPLICATA")
    scanned12b = build_form_medical()
    effects12b = dict(rotation=-5.2, grayscale=True, contrast=0.40,
                      brightness=1.07, illumination=0.26, noise=19.0,
                      blur=0.9, dust=48, jpeg_quality=27)
    items.append(make_item(
        "mixed", 12, "mixed", "mixte", "hard",
        native_pages(article12) + [scan_page(scanned12a, **effects12a),
                                   scan_page(scanned12b, **effects12b)],
        "Deux pages natives (dossier) + deux formulaires scannés très dégradés. "
        + _scan_note("Formulaires.", {**effects12a, **effects12b}),
        subtype="dossier_et_formulaires",
    ))

    return items


def build_all_items(seed: int = SEED_DEFAULT) -> list[ItemPlan]:
    rng = random.Random(seed)
    items: list[ItemPlan] = []
    items.extend(build_fact_items(rng))
    items.extend(build_form_items())
    items.extend(build_scan_items(rng))
    items.extend(build_mixed_items(rng))
    return items


# ---------------------------------------------------------------------------
# Rendu des documents
# ---------------------------------------------------------------------------


def prepare_item_images(item: ItemPlan, seed: int) -> None:
    """Rasterise et dégrade toutes les pages ``scan`` d'un document."""
    index = int(item.id.rsplit("_", 1)[-1])
    for page_index, plan in enumerate(item.pages):
        if plan.mode != "scan":
            continue
        page_seed = seed + index * 1009 + page_index * 37
        image = render_page_raster(plan.layout, seed=page_seed)
        effects = copy.deepcopy(plan.effects)
        angle = float(effects.get("rotation", 0.0))
        if angle:
            image, plan.blocks, plan.tables = rotate_page(
                image, plan.blocks, plan.tables, angle
            )
        image = apply_scan_effects(image, effects, seed=page_seed + 11)
        plan.image = image
        plan.rotation = angle
        plan.degradation_notes = _scan_notes(effects)


def _save_pdf_from_images(images: Sequence[Image.Image], path: Path) -> None:
    first = images[0]
    if len(images) == 1:
        first.save(str(path), "PDF", resolution=float(DPI), quality=95)
    else:
        first.save(
            str(path), "PDF", resolution=float(DPI), quality=95,
            save_all=True, append_images=list(images[1:]),
        )


def _save_composite_pdf(item: ItemPlan, path: Path) -> None:
    """Assemble pages natives et pages image dans un seul PDF."""
    doc = pdfium.PdfDocument.new()
    sources: list[pdfium.PdfDocument] = []
    font_cache: dict[str, Any] = {}
    font_buffers: list[Any] = []
    try:
        for plan in item.pages:
            if plan.mode == "native":
                page = doc.new_page(plan.layout.width * PX_TO_PT,
                                    plan.layout.height * PX_TO_PT)
                writer = NativePageWriter(doc, page, plan.layout, font_cache,
                                          font_buffers)
                for element in plan.layout.elements:
                    writer.add(element)
                if not raw.FPDFPage_GenerateContent(page.raw):
                    raise RuntimeError(
                        f"Génération PDF impossible pour {item.id}"
                    )
            else:
                if plan.image is None:
                    raise RuntimeError(f"Page scan non rendue : {item.id}")
                buffer = io.BytesIO()
                plan.image.save(buffer, "PDF", resolution=float(DPI),
                                quality=95)
                buffer.seek(0)
                source = pdfium.PdfDocument(buffer)
                sources.append(source)
                doc.import_pages(source, pages=[0], index=len(doc))
        doc.save(str(path))
    finally:
        for source in sources:
            source.close()
        doc.close()


def render_item(item: ItemPlan, out_dir: Path, seed: int) -> Path:
    """Écrit le fichier final d'un document et renvoie son chemin."""
    prepare_item_images(item, seed)
    path = out_dir / item.filename
    path.parent.mkdir(parents=True, exist_ok=True)

    if item.filetype != "pdf":
        if not item.pages or item.pages[0].image is None:
            raise RuntimeError(f"Document image sans page scannée : {item.id}")
        image = item.pages[0].image
        if item.filetype == "jpg":
            image.save(str(path), "JPEG", quality=88, optimize=True,
                       dpi=(DPI, DPI))
        elif item.filetype == "png":
            image.save(str(path), "PNG", dpi=(DPI, DPI))
        else:
            raise ValueError(f"Format inconnu : {item.filetype}")
        return path

    modes = {page.mode for page in item.pages}
    if modes == {"native"}:
        write_native_pdf([page.layout for page in item.pages], path)
    elif modes == {"scan"}:
        _save_pdf_from_images([page.image for page in item.pages], path)
    else:
        _save_composite_pdf(item, path)
    return path


# ---------------------------------------------------------------------------
# Manifest et vérité terrain
# ---------------------------------------------------------------------------

ACCENTED_CHARS = "àâäçéèêëîïôöùûüÿœæÀÂÄÇÉÈÊËÎÏÔÖÙÛÜŸŒÆ"


def _truth_text(item: ItemPlan) -> str:
    pages = ["\n".join(block["text"] for block in plan.blocks)
             for plan in item.pages]
    return "\n\f\n".join(pages)


def item_to_manifest(item: ItemPlan, out_dir: Path) -> dict[str, Any]:
    blocks: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []
    page_modes: list[str] = []
    page_dimensions: list[dict[str, Any]] = []
    rotations: list[float] = []
    for page_number, plan in enumerate(item.pages, start=1):
        page_modes.append(plan.mode)
        page_dimensions.append({
            "page": page_number,
            "width": plan.layout.width,
            "height": plan.layout.height,
            "orientation": plan.layout.orientation,
        })
        rotations.append(round(plan.rotation, 2))
        for block in plan.blocks:
            copied = copy.deepcopy(block)
            copied["page"] = page_number
            blocks.append(copied)
        for table in plan.tables:
            copied = copy.deepcopy(table)
            copied["page"] = page_number
            tables.append(copied)

    truth = _truth_text(item)
    truth_path = out_dir / "truth" / f"{item.id}.txt"
    truth_path.parent.mkdir(parents=True, exist_ok=True)
    truth_path.write_text(truth + "\n", encoding="utf-8")

    notes = item.notes

    return {
        "id": item.id,
        "filename": item.filename,
        "filetype": item.filetype,
        "difficulty": item.difficulty,
        "theme": item.theme,
        "subtype": item.subtype,
        "source": item.source,
        "language": item.language,
        "orientation": item.orientation,
        "pages": len(item.pages),
        "page_dimensions": page_dimensions,
        "page_modes": page_modes,
        "rotation_degrees": rotations,
        "expected_blocks": blocks,
        "expected_tables": bool(tables),
        "table_count": len(tables),
        "tables": tables,
        "text_file": f"truth/{item.id}.txt",
        "has_accents": any(char in ACCENTED_CHARS for char in truth),
        "notes": notes,
    }


def build_manifest(items: Sequence[ItemPlan], out_dir: Path,
                   seed: int) -> dict[str, Any]:
    item_dicts = [item_to_manifest(item, out_dir) for item in items]
    by_difficulty = Counter(item["difficulty"] for item in item_dicts)
    by_theme = Counter(item["theme"] for item in item_dicts)
    by_folder = Counter(item["filename"].split("/", 1)[0] for item in item_dicts)
    by_filetype = Counter(item["filetype"] for item in item_dicts)
    manifest = {
        "schema_version": "1.0",
        "suite": "phase0-ocr-test-suite",
        "generator": "facts_generator.py",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "seed": seed,
        "language": "fr",
        "coordinate_system": {
            "origin": "top-left",
            "units": "pixels",
            "dpi": DPI,
            "note": "Les bboxes sont exprimées sur le rendu à 150 DPI. "
                    "Pour les scans, elles sont transformées après rotation "
                    "et rognées aux bords de la page.",
            "page_sizes": {
                "portrait": [A4_PORTRAIT[0], A4_PORTRAIT[1]],
                "landscape": [A4_LANDSCAPE[0], A4_LANDSCAPE[1]],
            },
        },
        "truth_text_format": {
            "blocks_per_line": True,
            "page_separator": "\\f",
            "note": "Les blocs sont concaténés dans l'ordre de lecture ; un "
                    "saut de page (form feed) sépare les pages. Les tableaux "
                    "sont encodés dans les blocs de type table, cellules "
                    "séparées par ' | '.",
        },
        "counts": {
            "total": len(item_dicts),
            "by_difficulty": dict(sorted(by_difficulty.items())),
            "by_theme": dict(sorted(by_theme.items())),
            "by_folder": dict(sorted(by_folder.items())),
            "by_filetype": dict(sorted(by_filetype.items())),
        },
        "items": item_dicts,
    }
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


# ---------------------------------------------------------------------------
# Validation du jeu produit
# ---------------------------------------------------------------------------


def _normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    text = text.replace("\u00a0", " ").replace("\u202f", " ")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\s+", " ", text).strip()


def _tokens(text: str) -> list[str]:
    """Découpe en mots pour comparer un texte extrait dans un autre ordre."""
    return re.findall(r"[a-z0-9àâäçéèêëîïôöùûüÿœæ]+", _normalize_text(text))


def validate_suite(manifest: dict[str, Any], out_dir: Path,
                   *, verbose: bool = True) -> tuple[list[str], dict[str, Any]]:
    """Vérifie la cohérence du manifest, des fichiers et du texte natif."""
    errors: list[str] = []
    items = manifest.get("items", [])
    stats: dict[str, Any] = {
        "items": len(items),
        "pages": sum(item.get("pages", 0) for item in items),
        "native_blocks_checked": 0,
        "native_blocks_found": 0,
        "native_tokens_checked": 0,
        "native_tokens_found": 0,
        "accent_pages": 0,
    }

    if not 30 <= len(items) <= 50:
        errors.append(f"Nombre d'items hors plage 30–50 : {len(items)}")
    ids = [item.get("id") for item in items]
    if len(ids) != len(set(ids)):
        errors.append("Identifiants dupliqués dans le manifest")
    filenames = [item.get("filename") for item in items]
    if len(filenames) != len(set(filenames)):
        errors.append("Noms de fichiers dupliqués dans le manifest")

    difficulty_counts = Counter(item.get("difficulty") for item in items)
    for level in ("easy", "medium", "hard"):
        if difficulty_counts.get(level, 0) < 8:
            errors.append(
                f"Couverture insuffisante en difficulté {level} : "
                f"{difficulty_counts.get(level, 0)}"
            )
    filetypes = {item.get("filetype") for item in items}
    if not {"pdf", "jpg", "png"} <= filetypes:
        errors.append(f"Formats manquants (pdf/jpg/png) : {sorted(filetypes)}")

    for item in items:
        item_id = item.get("id", "?")
        path = out_dir / item.get("filename", "")
        if not path.exists() or path.stat().st_size == 0:
            errors.append(f"{item_id}: fichier absent ou vide ({path})")
            continue
        blocks = item.get("expected_blocks", [])
        if not blocks:
            errors.append(f"{item_id}: aucun bloc de vérité terrain")
        if item.get("expected_tables") != bool(item.get("tables")):
            errors.append(f"{item_id}: incohérence expected_tables/tables")
        truth_path = out_dir / item.get("text_file", "")
        if not truth_path.exists() or truth_path.stat().st_size == 0:
            errors.append(f"{item_id}: fichier truth absent ou vide")
        truth = truth_path.read_text(encoding="utf-8") if truth_path.exists() else ""
        if not truth.strip():
            errors.append(f"{item_id}: vérité terrain texte vide")
        if item.get("has_accents") and not any(c in ACCENTED_CHARS for c in truth):
            errors.append(f"{item_id}: has_accents vrai mais aucun accent détecté")

        dims = {entry["page"]: entry for entry in item.get("page_dimensions", [])}
        for block in blocks:
            page = block.get("page")
            if page not in dims:
                errors.append(f"{item_id}: bloc sur page inconnue {page}")
                continue
            width = dims[page]["width"]
            height = dims[page]["height"]
            bbox = block.get("bbox", [])
            if len(bbox) != 4 or not all(isinstance(v, (int, float)) for v in bbox):
                errors.append(f"{item_id}: bbox invalide {bbox}")
                continue
            x0, y0, x1, y1 = bbox
            if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
                errors.append(f"{item_id}: bbox hors page {bbox} ({width}×{height})")
            if not str(block.get("text", "")).strip():
                errors.append(f"{item_id}: bloc sans texte {bbox}")

        if item.get("filetype") != "pdf":
            try:
                with Image.open(path) as image:
                    if image.size != (dims[1]["width"], dims[1]["height"]):
                        errors.append(
                            f"{item_id}: taille image {image.size} != "
                            f"{dims[1]['width']}×{dims[1]['height']}"
                        )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{item_id}: image illisible ({exc})")
            continue

        try:
            pdf = pdfium.PdfDocument(str(path))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{item_id}: PDF illisible ({exc})")
            continue
        try:
            if len(pdf) != item.get("pages"):
                errors.append(
                    f"{item_id}: {len(pdf)} pages dans le PDF, "
                    f"{item.get('pages')} annoncées"
                )
            modes = item.get("page_modes", [])
            for page_index in range(min(len(pdf), len(dims))):
                width_pt, height_pt = pdf.get_page_size(page_index)
                expected = dims[page_index + 1]
                if (abs(width_pt - expected["width"] * PX_TO_PT) > 1.5 or
                        abs(height_pt - expected["height"] * PX_TO_PT) > 1.5):
                    errors.append(
                        f"{item_id}: page {page_index + 1} dimensions PDF "
                        f"{width_pt:.1f}×{height_pt:.1f} pt inattendues"
                    )
                page_text = pdf[page_index].get_textpage().get_text_bounded()
                if any(char in ACCENTED_CHARS for char in page_text):
                    stats["accent_pages"] += 1
                if page_index < len(modes) and modes[page_index] == "native":
                    page_tokens = set(_tokens(page_text))
                    page_blocks = [
                        block for block in blocks
                        if block.get("page") == page_index + 1
                        and block.get("type") != "table"
                    ]
                    missing: list[str] = []
                    for block in page_blocks:
                        stats["native_blocks_checked"] += 1
                        block_tokens = [
                            token for token in _tokens(block.get("text", ""))
                            if len(token) >= 3
                        ]
                        if not block_tokens:
                            stats["native_blocks_found"] += 1
                            continue
                        found = sum(1 for token in block_tokens
                                    if token in page_tokens)
                        stats["native_tokens_checked"] += len(block_tokens)
                        stats["native_tokens_found"] += found
                        recall = found / len(block_tokens)
                        # L'extraction PDF peut entrelacer les colonnes ligne
                        # par ligne ; on mesure donc la présence des mots et non
                        # la contiguïté exacte du bloc.
                        if recall >= 0.75:
                            stats["native_blocks_found"] += 1
                        else:
                            missing.append(block.get("text", "")[:60])
                    if missing and len(missing) / max(1, len(page_blocks)) > 0.12:
                        errors.append(
                            f"{item_id}: page native {page_index + 1}, "
                            f"{len(missing)}/{len(page_blocks)} blocs trop "
                            f"incomplets dans le texte PDF (ex. {missing[0]!r})"
                        )
                elif modes and modes[page_index] == "scan":
                    if page_text.strip():
                        errors.append(
                            f"{item_id}: page scannée {page_index + 1} contient "
                            "du texte extractible inattendu"
                        )
        finally:
            pdf.close()

    if stats["native_blocks_checked"] and stats["native_blocks_found"] == 0:
        errors.append("Aucun texte natif retrouvé dans les PDF")
    if stats["native_blocks_checked"]:
        ratio = (stats["native_blocks_found"] /
                 stats["native_blocks_checked"])
        stats["native_text_recall"] = round(ratio, 4)
    if stats["native_tokens_checked"]:
        stats["native_token_recall"] = round(
            stats["native_tokens_found"] / stats["native_tokens_checked"], 4
        )
    if verbose:
        print(
            f"Validation : {stats['items']} documents, {stats['pages']} pages, "
            f"{stats['native_blocks_found']}/{stats['native_blocks_checked']} "
            f"blocs natifs retrouvés (mots : "
            f"{stats['native_tokens_found']}/{stats['native_tokens_checked']})."
        )
    return errors, stats


def load_manifest(out_dir: Path) -> dict[str, Any]:
    path = out_dir / "manifest.json"
    if not path.exists():
        raise FileNotFoundError(f"Manifest absent : {path}")
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Orchestration et ligne de commande
# ---------------------------------------------------------------------------


def generate_suite(seed: int, out_dir: Path, *, verbose: bool = True) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    for folder in ("facts", "forms", "scans", "mixed", "truth"):
        target = out_dir / folder
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True)
    manifest_path = out_dir / "manifest.json"
    if manifest_path.exists():
        manifest_path.unlink()

    items = build_all_items(seed)
    if verbose:
        print(f"Génération de {len(items)} documents dans {out_dir} …")
    for item in items:
        render_item(item, out_dir, seed)
        if verbose:
            print(f"  {item.filename}")
    manifest = build_manifest(items, out_dir, seed)
    errors, stats = validate_suite(manifest, out_dir, verbose=verbose)
    if errors:
        print("\nErreurs de validation :", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        raise SystemExit(1)
    if verbose:
        print(
            f"Terminé : {stats['items']} documents, {stats['pages']} pages, "
            f"difficultés {manifest['counts']['by_difficulty']}."
        )
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Génère le jeu de documents de test OCR (Phase 0)."
    )
    parser.add_argument("--seed", type=int, default=SEED_DEFAULT,
                        help=f"Graine aléatoire (défaut : {SEED_DEFAULT}).")
    parser.add_argument("--out", type=Path, default=BASE_DIR,
                        help="Dossier de sortie (défaut : dossier du script).")
    parser.add_argument("--validate-only", action="store_true",
                        help="Valide le manifest existant sans régénérer.")
    parser.add_argument("--quiet", action="store_true",
                        help="Réduit les messages de progression.")
    args = parser.parse_args(argv)
    out_dir = args.out.resolve()

    if args.validate_only:
        manifest = load_manifest(out_dir)
        errors, stats = validate_suite(manifest, out_dir, verbose=True)
        if errors:
            for error in errors:
                print(f"ERREUR : {error}", file=sys.stderr)
            return 1
        print("Manifest valide.")
        print(json.dumps(manifest["counts"], ensure_ascii=False, indent=2))
        return 0

    generate_suite(args.seed, out_dir, verbose=not args.quiet)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
