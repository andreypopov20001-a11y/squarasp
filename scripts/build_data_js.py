"""Собирает data.js из data/*.json, чтобы index.html работал при открытии двойным кликом и на Netlify Drop."""
import json, pathlib
R = pathlib.Path(__file__).resolve().parent.parent; D = R / "data"
sch = {p.stem: json.loads(p.read_text("utf-8")) for p in (D / "schedules").glob("*.json")}
data = {"groups": json.loads((D / "groups.json").read_text("utf-8")),
        "teachers": json.loads((D / "teachers.json").read_text("utf-8")), "schedules": sch}
(R / "data.js").write_text("window.DATA=" + json.dumps(data, ensure_ascii=False) + ";", "utf-8")
print("data.js готов, групп с расписанием:", len(sch))
