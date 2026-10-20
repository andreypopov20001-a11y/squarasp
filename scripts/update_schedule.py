"""Обновляет data/: schedules/<группа>.json, groups.json, teachers.json.
Читает PDF из data/pdfs (стартовые) и скачивает свежие с сайта колледжа на эту и следующую неделю.
Запуск: python scripts/update_schedule.py (в GitHub Actions — каждые 3 часа)."""
import datetime as dt, hashlib, json, os, re, sys, urllib.request
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))
import schedule_parser as sp
from catalog import CATALOG, groups_of

ROOT = Path(__file__).resolve().parent.parent
D = ROOT / "data"
BASE = os.getenv("SCHEDULE_BASE_URL", "https://balakovo.ssla.ru/images/raspisanie").rstrip("/")
TZ = ZoneInfo("Europe/Saratov")
DMY = "%d.%m.%Y"
SEED_STEM = "БФЮРОО26-010203"  # стартовые PDF в data/pdfs относятся к этому файлу


def fid(group):  # ASCII-имя файла, чтобы GitHub Pages не спотыкался о кириллицу
    return hashlib.md5(group.encode()).hexdigest()[:8]


def load(group):
    p = D / "schedules" / f"{fid(group)}.json"
    return json.loads(p.read_text("utf-8")) if p.exists() else {"meta": {"group": group, "seen": []}, "weeks": {}}


def ingest(data, pdf_bytes, tmp, group, from_seed):
    sha = hashlib.sha1(pdf_bytes).hexdigest()
    seen = data["meta"].setdefault("seen", [])
    if from_seed and sha in seen:
        return
    tmp.write_bytes(pdf_bytes)
    days = sp.parse_pdf(str(tmp), group)
    if not days:
        print("   нет пар для", group); return
    monday = sp.week_monday(min(days, key=lambda s: dt.datetime.strptime(s, DMY))).isoformat()
    old = data["weeks"].get(monday)
    if old and old["sha"] == sha:
        return
    changed = [d for d in sorted(set(days) | set(old["days"])) if days.get(d) != old["days"].get(d)] if old else []
    data["weeks"][monday] = {"sha": sha, "days": days, "changed": changed,
                             "updated": dt.datetime.now(TZ).isoformat(timespec="seconds")}
    if sha not in seen:
        seen.append(sha)
    print("   ", group, monday, "обновлена, изменились:", changed or "новая")


def fetch(stem, monday):
    sat = monday + dt.timedelta(days=5)
    y = monday.year if monday.month >= 8 else monday.year - 1
    folder = f"{monday:%d.%m.%Y}-{sat:%d.%m.%Y}"
    for f in (folder, folder + "_"):
        for sub in ("", "/spo"):
            u = f"{BASE}/{y}-{y+1}/{f}{sub}/{quote(stem)}.pdf"
            try:
                with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=30) as r:
                    body = r.read()
                if body[:4] == b"%PDF":
                    return body
            except Exception:
                pass
    return None


def teachers_from(all_data):
    out = {}
    for data in all_data:
        for w in data["weeks"].values():
            for lessons in w["days"].values():
                for l in lessons:
                    m = re.search(r"^(.*?)\s*[–—-]\s*.*?преп\.,?\s*(.+?),\s*(?:ауд\.|спорт)", l["text"])
                    if m:
                        name = re.sub(r"\.\s+", ".", m[2].strip())
                        out.setdefault(name, set()).add(m[1].strip())
    return [{"n": n, "s": sorted(s)} for n, s in sorted(out.items())]


def main():
    (D / "schedules").mkdir(parents=True, exist_ok=True)
    tmp = D / "_tmp.pdf"
    today = dt.datetime.now(TZ).date()
    mon = today - dt.timedelta(days=today.weekday())
    seeds = sorted((D / "pdfs").glob("*.pdf"))
    catalog, all_data = [], []
    for stem, spec, basis, course in CATALOG:
        print(stem)
        gs = groups_of(stem)
        store = {g: load(g) for g in gs}
        pdfs = [(p.read_bytes(), True) for p in seeds] if stem == SEED_STEM else []
        for m in (mon, mon + dt.timedelta(days=7)):
            body = fetch(stem, m)
            print("  неделя", m, "скачано" if body else "на сайте нет")
            if body:
                pdfs.append((body, False))
        for body, seed in pdfs:
            for g in gs:
                try:
                    ingest(store[g], body, tmp, g, seed)
                except Exception as e:
                    print("   ошибка", g, e)
        for g in gs:
            catalog.append({"id": g, "f": fid(g), "spec": spec, "basis": basis, "course": course,
                            "stream": f"{spec.split()[0]}-{basis.split()[0]}-{course}"})
            if store[g]["weeks"]:  # пустые файлы не пишем
                store[g]["meta"]["group"] = g
                store[g]["meta"]["checked"] = dt.datetime.now(TZ).isoformat(timespec="seconds")
                (D / "schedules" / f"{fid(g)}.json").write_text(json.dumps(store[g], ensure_ascii=False), "utf-8")
                all_data.append(store[g])
    tmp.unlink(missing_ok=True)
    (D / "groups.json").write_text(json.dumps(catalog, ensure_ascii=False, indent=0), "utf-8")
    (D / "teachers.json").write_text(json.dumps(teachers_from(all_data), ensure_ascii=False), "utf-8")
    print("групп:", len(catalog))


if __name__ == "__main__":
    main()
