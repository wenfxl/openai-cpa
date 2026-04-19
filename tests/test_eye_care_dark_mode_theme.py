from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "static/css/style.css").read_text(encoding="utf-8")


def test_eye_care_dark_mode_redefines_light_gradient_start_tokens():
    light_gradient_starts = [
        "from-white",
        "from-slate-50",
        "from-indigo-50",
        "from-sky-50",
        "from-blue-50",
        "from-amber-50",
        "from-orange-50",
        "from-emerald-50",
        "from-purple-50",
        "from-rose-50",
    ]

    for token in light_gradient_starts:
        if token in HTML:
            assert f'[class*="{token}"]' in CSS, f"missing dark-mode override for {token}"


def test_eye_care_dark_mode_redefines_light_gradient_end_tokens():
    light_gradient_ends = [
        "to-white",
        "to-blue-50",
        "to-indigo-50",
        "to-slate-50",
    ]

    for token in light_gradient_ends:
        if token in HTML:
            selector = f'body.theme-dark [class*="{token}"]'
            assert selector in CSS, f"missing dark-mode selector for {token}"
            start = CSS.index(selector)
            end = CSS.find("}", start)
            block = CSS[start:end]
            assert "--tw-gradient-to" in block or "--tw-gradient-stops" in block, (
                f"{token} still needs a dark-mode gradient override"
            )
