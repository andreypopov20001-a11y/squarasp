"""Разбор PDF-расписания БФ СГЮА (Балаково) для одной группы.

В PDF границы таблицы нарисованы тонкими прямоугольниками (а не линиями), поэтому
pdfplumber сам ячейки не собирает. Делаем так:
  1. строки таблицы = промежутки между горизонтальными границами в колонке «Время»;
  2. для строки смотрим, есть ли вертикальная граница слева/справа от колонки нашей
     группы: если нет, ячейка объединённая (лекция на несколько групп) и мы берём
     весь её текст;
  3. дата берётся из колонки «Дата» (одна ячейка на весь день).
"""
import datetime as dt
import re

import pdfplumber

DATE_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")
TIME_RE = re.compile(r"(\d{1,2})[.:](\d{2})\s*[–—-]\s*(\d{1,2})[.:](\d{2})")

WEEKDAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]

TIME_COL = (101, 172)   # колонка «Время»
DATE_COL = (24, 101)    # колонка «Дата»
TABLE_RIGHT = 824


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s).upper()


def _crop_text(page, x0, top, x1, bottom) -> str:
    x0, x1 = max(x0, 0), min(x1, page.width)
    top, bottom = max(top, 0), min(bottom, page.height)
    if x1 - x0 < 3 or bottom - top < 3:
        return ""
    txt = page.crop((x0, top, x1, bottom)).extract_text() or ""
    return re.sub(r"\s+", " ", txt.replace("\n", " ")).strip()


def _merge(values, tol=1.5):
    out = []
    for v in sorted(values):
        if not out or v - out[-1] > tol:
            out.append(v)
    return out


def _find_group_x(pdf, group: str) -> float:
    seen = []
    for page in pdf.pages:
        for w in page.extract_words():
            if w["text"].upper().startswith("БФ"):
                seen.append(w["text"])
            if _norm(group) == _norm(w["text"]):
                return (w["x0"] + w["x1"]) / 2
    raise ValueError(f"Не нашла колонку группы {group}. В файле есть: {sorted(set(seen))}")


def _borders(page):
    """Горизонтальные и вертикальные границы таблицы как тонкие прямоугольники."""
    hor, ver = [], []
    for r in page.rects:
        w, h = r["x1"] - r["x0"], r["bottom"] - r["top"]
        if h < 1.6 and w > 20:
            hor.append(r)
        elif w < 1.6 and h > 3:
            ver.append(r)
    return hor, ver


def parse_pdf(path: str, group: str = "БФЮРОО26-01") -> dict[str, list[dict]]:
    """Возвращает {'19.10.2026': [{'start','end','text'}, ...], ...}."""
    days: dict[str, list[dict]] = {}
    last_date = None
    with pdfplumber.open(path) as pdf:
        cx = _find_group_x(pdf, group)
        for page in pdf.pages:
            hor, ver = _borders(page)

            # --- строки по колонке «Время»
            ys = _merge(
                (r["top"] + r["bottom"]) / 2
                for r in hor
                if r["x0"] <= TIME_COL[0] + 2 and r["x1"] >= TIME_COL[1] - 2
            )
            # --- блоки дней по колонке «Дата»
            day_ys = _merge(
                (r["top"] + r["bottom"]) / 2
                for r in hor
                if r["x0"] <= DATE_COL[0] + 2 and r["x1"] >= DATE_COL[1] - 2
            )
            day_blocks = []
            for a, b in zip(day_ys, day_ys[1:]):
                m = DATE_RE.search(_crop_text(page, DATE_COL[0], a + 1, DATE_COL[1], b - 1))
                if m:
                    day_blocks.append((a, b, f"{m[1]}.{m[2]}.{m[3]}"))

            for top, bottom in zip(ys, ys[1:]):
                if bottom - top < 8:
                    continue
                mt = TIME_RE.search(_crop_text(page, TIME_COL[0] + 1, top + 1, TIME_COL[1] - 1, bottom - 1))
                if not mt:
                    continue
                ymid = (top + bottom) / 2

                date = next((d for a, b, d in day_blocks if a <= ymid <= b), None) or last_date
                if date is None:
                    continue
                last_date = date

                # границы ячейки нашей группы в этой строке
                xs = [
                    (r["x0"] + r["x1"]) / 2
                    for r in ver
                    if r["top"] - 1 <= ymid <= r["bottom"] + 1 and r["x0"] >= TIME_COL[1] - 3
                ]
                left = max([x for x in xs if x < cx] or [TIME_COL[1]])
                right = min([x for x in xs if x > cx] or [TABLE_RIGHT])
                text = _crop_text(page, left + 1, top + 1, right - 1, bottom - 1)
                if not text:
                    continue

                start = f"{int(mt[1]):02d}:{mt[2]}"
                end = f"{int(mt[3]):02d}:{mt[4]}"
                days.setdefault(date, []).append({"start": start, "end": end, "text": text})
    for date, lessons in list(days.items()):
        lessons.sort(key=lambda x: x["start"])
        # «День самоподготовки» занимает несколько строк: склеиваем в одну запись
        merged: list[dict] = []
        for les in lessons:
            if merged and merged[-1]["text"] == les["text"] and "САМОПОДГОТОВК" in les["text"].upper():
                merged[-1]["end"] = les["end"]
            else:
                merged.append(les)
        days[date] = merged
    return days


def week_monday(date_str: str) -> dt.date:
    d, m, y = map(int, date_str.split("."))
    day = dt.date(y, m, d)
    return day - dt.timedelta(days=day.weekday())


if __name__ == "__main__":
    import json
    import sys

    res = parse_pdf(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "БФЮРОО26-01")
    print(json.dumps(res, ensure_ascii=False, indent=1))
