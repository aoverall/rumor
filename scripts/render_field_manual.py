#!/usr/bin/env python3
"""
render_field_manual.py

Builds a single self-contained HTML "field manual" for the RUMOR project.

Inputs:
  - skill/references/cartridge.md   (the distilled taste model -> main sections)
  - docs/eval-set.jsonl             (319 labeled reaction records -> evidence + tallies)

Output:
  - docs/field-manual.html          (one file; inline CSS, system fonts, no JS/CDN)

Aesthetic: WPA / victory-garden / midcentury-airline / Foxfire + leather-bound
reference. Warm paper background, strong typographic hierarchy, rules and
small-caps labels, restrained ink / ochre / muted-red / muted-green palette.
"""

import datetime
import html
import json
import re
from collections import Counter, OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CARTRIDGE = ROOT / "skill" / "references" / "cartridge.md"
EVAL = ROOT / "docs" / "eval-set.jsonl"
OUT = ROOT / "docs" / "field-manual.html"


# ---------------------------------------------------------------------------
# Markdown -> HTML (small, purpose-built converter for the cartridge's subset)
# ---------------------------------------------------------------------------

def md_inline(text):
    """Convert inline markdown (bold, code, em) to HTML. Text is pre-escaped."""
    text = html.escape(text)
    # `code`
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    # **bold**
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    # *em* (avoid matching list bullets / leftover asterisks)
    text = re.sub(r"(?<!\*)\*(?!\s)([^*]+?)\*(?!\*)", r"<em>\1</em>", text)
    return text


def parse_cartridge(md):
    """
    Parse the cartridge into an ordered list of section dicts:
      {"title": str, "num": str|None, "html": str}
    The top H1 + intro blockquote become the front matter.
    """
    lines = md.splitlines()
    # Drop the H1 title line and the leading blockquote (handled separately).
    front_quote = []
    body_lines = []
    seen_first_rule = False
    i = 0
    # Collect intro blockquote (lines starting with '>') before first '---'
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("# "):
            i += 1
            continue
        if ln.strip() == "---":
            seen_first_rule = True
            i += 1
            break
        if ln.startswith(">"):
            front_quote.append(ln.lstrip(">").strip())
        i += 1

    body_lines = lines[i:]
    front_html = render_block("\n".join(front_quote))

    # Split remaining body on H2 headings.
    sections = []
    current = None
    buf = []

    def flush():
        nonlocal current, buf
        if current is not None:
            current["html"] = render_block("\n".join(buf).strip())
            sections.append(current)
        buf = []

    for ln in body_lines:
        if ln.strip() == "---":
            continue
        m = re.match(r"^##\s+(.*)$", ln)
        if m:
            flush()
            raw = m.group(1).strip()
            nm = re.match(r"^(\d+)\.\s*(.*)$", raw)
            if nm:
                current = {"num": nm.group(1), "title": nm.group(2).strip()}
            else:
                current = {"num": None, "title": raw}
            continue
        if current is None:
            if not ln.strip():
                continue  # blank lines before the first H2 are not content
            current = {"num": None, "title": ""}
        buf.append(ln)
    flush()
    return front_html, sections


def render_block(md):
    """Render a block of markdown (paragraphs, bullet lists, H3) to HTML."""
    out = []
    lines = md.splitlines()
    i = 0
    while i < len(lines):
        ln = lines[i]
        stripped = ln.strip()
        if not stripped:
            i += 1
            continue
        # H3
        m = re.match(r"^###\s+(.*)$", stripped)
        if m:
            out.append(f"<h3>{md_inline(m.group(1).strip())}</h3>")
            i += 1
            continue
        # Bullet list (absorbs hard-wrapped continuation lines into the same item,
        # so a wrapped bullet does not split into an orphan paragraph and inline
        # emphasis spanning the wrap still parses)
        if re.match(r"^[-*]\s+", stripped):
            items = []
            while i < len(lines) and re.match(r"^[-*]\s+", lines[i].strip()):
                item = re.sub(r"^[-*]\s+", "", lines[i].strip())
                i += 1
                while i < len(lines):
                    nxt = lines[i].strip()
                    if not nxt or re.match(r"^[-*]\s+", nxt) or nxt.startswith("#"):
                        break
                    item += " " + nxt
                    i += 1
                items.append(f"<li>{md_inline(item)}</li>")
            out.append("<ul>\n" + "\n".join(items) + "\n</ul>")
            continue
        # Paragraph: gather until blank or structural line
        para = [stripped]
        i += 1
        while i < len(lines):
            nxt = lines[i].strip()
            if not nxt or re.match(r"^[-*]\s+", nxt) or nxt.startswith("#"):
                break
            para.append(nxt)
            i += 1
        out.append(f"<p>{md_inline(' '.join(para))}</p>")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Eval set
# ---------------------------------------------------------------------------

def load_records():
    recs = []
    with open(EVAL) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            recs.append(json.loads(line))
    return recs


def tally(recs):
    verdicts = Counter(r.get("verdict", "?") for r in recs)
    modes = Counter(r.get("mode", "?") for r in recs)
    return verdicts, modes


def key_of(r):
    return (r.get("session"), r.get("idx"))


def select_exemplars(recs):
    """
    Curate ~12 signal-rich records content-agnostically: every rare "amazing"
    first (the loudest signal), then a balanced spread of rejected / redirected /
    confused / acceptable. No hardcoded quotes, so it works for any eval-set and
    leaks no one's words. Deterministic (file order) so the selection survives re-runs.
    """
    cap = 12
    by_verdict = OrderedDict()
    for r in recs:
        if r.get("is_reaction"):
            by_verdict.setdefault(r.get("verdict"), []).append(r)
    chosen = list(by_verdict.get("amazing", []))[:cap]
    pools = [list(by_verdict.get(v, []))
             for v in ("rejected", "redirected", "confused", "acceptable", "neutral")]
    while len(chosen) < cap and any(pools):
        for pool in pools:
            if pool and len(chosen) < cap:
                chosen.append(pool.pop(0))
    return chosen


# ---------------------------------------------------------------------------
# HTML assembly
# ---------------------------------------------------------------------------

VERDICT_META = OrderedDict([
    ("amazing", ("Amazing", "amazing")),
    ("acceptable", ("Acceptable", "acceptable")),
    ("neutral", ("Neutral", "neutral")),
    ("redirected", ("Redirected", "redirected")),
    ("confused", ("Confused", "confused")),
    ("rejected", ("Rejected", "rejected")),
])

MODE_ORDER = ["push", "interrogate", "neutral"]


def tag(verdict):
    label, cls = VERDICT_META.get(verdict, (verdict.title(), "neutral"))
    return f'<span class="tag tag-{cls}">{html.escape(label)}</span>'


def evidence_card(r):
    v = r.get("verdict", "neutral")
    human = html.escape(r.get("human_text", "").strip())
    art = html.escape((r.get("artifact_summary") or "").strip())
    why = html.escape((r.get("why") or "").strip())
    return f"""    <article class="card card-{VERDICT_META.get(v, ('', 'neutral'))[1]}">
      <header class="card-head">{tag(v)}</header>
      <blockquote class="card-quote">&ldquo;{human}&rdquo;</blockquote>
      <div class="card-meta">
        <p class="card-line"><span class="label">Reacting to</span>{art}</p>
        <p class="card-line"><span class="label">The taste read</span>{why}</p>
      </div>
    </article>"""


def summary_tables(verdicts, modes):
    vrows = []
    total = sum(verdicts.values())
    for v, (label, cls) in VERDICT_META.items():
        c = verdicts.get(v, 0)
        vrows.append(
            f'<tr><td>{tag(v)}</td><td class="num">{c}</td></tr>'
        )
    vtable = "\n".join(vrows)

    mrows = []
    for m in MODE_ORDER:
        c = modes.get(m, 0)
        mrows.append(
            f'<tr><td class="modecell">{html.escape(m.title())}</td>'
            f'<td class="num">{c}</td></tr>'
        )
    mtable = "\n".join(mrows)

    return f"""    <div class="tables">
      <table class="tally">
        <caption>By verdict</caption>
        <tbody>
{vtable}
        <tr class="total"><td>Total</td><td class="num">{total}</td></tr>
        </tbody>
      </table>
      <table class="tally">
        <caption>By mode</caption>
        <tbody>
{mtable}
        <tr class="total"><td>Total</td><td class="num">{sum(modes.values())}</td></tr>
        </tbody>
      </table>
    </div>"""


CSS = """
:root{
  --paper:#f4ece0;
  --paper-2:#efe5d6;
  --ink:#23201b;
  --ink-soft:#4a443b;
  --rule:#c9bca6;
  --rule-strong:#8a7a5e;
  --ochre:#b07a26;
  --ochre-deep:#8a5d17;
  --red:#9c3a2c;
  --green:#4f6b3a;
  --measure:65ch;
}
*{box-sizing:border-box;}
html{-webkit-text-size-adjust:100%;}
body{
  margin:0;
  background:var(--paper);
  color:var(--ink);
  font-family:"Iowan Old Style","Palatino Linotype",Palatino,"Book Antiqua",Georgia,"Times New Roman",serif;
  font-size:18px;
  line-height:1.6;
  background-image:
    radial-gradient(circle at 18% 12%, rgba(0,0,0,0.018) 0, transparent 60%),
    radial-gradient(circle at 82% 78%, rgba(0,0,0,0.018) 0, transparent 60%);
}
.wrap{
  max-width:760px;
  margin:0 auto;
  padding:2.4rem 1.4rem 5rem;
}
.smallcaps{
  font-variant:small-caps;
  letter-spacing:0.12em;
  text-transform:lowercase;
}

/* ---------- Masthead ---------- */
.masthead{
  text-align:center;
  border-top:3px double var(--rule-strong);
  border-bottom:3px double var(--rule-strong);
  padding:1.8rem 0 1.6rem;
  margin-bottom:2.2rem;
}
.kicker{
  font-variant:small-caps;
  letter-spacing:0.28em;
  font-size:0.78rem;
  color:var(--ochre-deep);
  margin:0 0 0.5rem;
}
.masthead h1{
  font-size:2.5rem;
  line-height:1.05;
  margin:0.2rem 0 0.4rem;
  letter-spacing:0.02em;
  font-weight:700;
}
.masthead h1 .em{color:var(--ochre-deep);}
.subtitle{
  font-style:italic;
  color:var(--ink-soft);
  max-width:54ch;
  margin:0.6rem auto 0;
  font-size:1.02rem;
}
.colophon{
  font-variant:small-caps;
  letter-spacing:0.16em;
  font-size:0.72rem;
  color:var(--ink-soft);
  margin-top:1.1rem;
}
.colophon span{color:var(--rule-strong);padding:0 0.4em;}

/* ---------- Front matter ---------- */
.frontmatter{
  border-left:4px solid var(--ochre);
  background:var(--paper-2);
  padding:1rem 1.3rem;
  margin:0 auto 2.6rem;
  max-width:var(--measure);
  font-style:italic;
  color:var(--ink-soft);
}
.frontmatter p{margin:0.4rem 0;}

/* ---------- Sections ---------- */
section{margin:0 auto 2.8rem;max-width:var(--measure);}
.sec-head{
  display:flex;
  align-items:baseline;
  gap:0.7rem;
  border-bottom:2px solid var(--ink);
  padding-bottom:0.35rem;
  margin-bottom:1rem;
}
.sec-num{
  font-variant:small-caps;
  letter-spacing:0.1em;
  color:var(--ochre-deep);
  font-size:1rem;
  font-weight:700;
  min-width:1.8em;
}
.sec-head h2{
  font-size:1.45rem;
  margin:0;
  letter-spacing:0.01em;
  font-weight:700;
}
section h3{
  font-size:1.05rem;
  font-variant:small-caps;
  letter-spacing:0.06em;
  color:var(--ochre-deep);
  margin:1.4rem 0 0.4rem;
}
section p{margin:0.7rem 0;}
section ul{margin:0.6rem 0 0.9rem;padding-left:1.1rem;list-style:none;}
section ul li{
  position:relative;
  padding-left:1.0rem;
  margin:0.5rem 0;
}
section ul li::before{
  content:"";
  position:absolute;
  left:0;
  top:0.62em;
  width:0.42em;
  height:0.42em;
  background:var(--ochre);
  transform:rotate(45deg);
}
code{
  font-family:"SF Mono",ui-monospace,"Menlo",Consolas,monospace;
  font-size:0.82em;
  background:rgba(138,122,94,0.14);
  border:1px solid var(--rule);
  border-radius:2px;
  padding:0.04em 0.34em;
  white-space:nowrap;
}
strong{color:var(--ink);font-weight:700;}
em{font-style:italic;}

/* ---------- Tally tables ---------- */
.tables{
  display:flex;
  flex-wrap:wrap;
  gap:1.6rem;
  margin-top:0.6rem;
}
.tally{
  border-collapse:collapse;
  flex:1 1 240px;
  font-size:0.95rem;
}
.tally caption{
  font-variant:small-caps;
  letter-spacing:0.12em;
  text-align:left;
  color:var(--ochre-deep);
  padding-bottom:0.35rem;
  border-bottom:2px solid var(--ink);
  font-weight:700;
}
.tally td{
  padding:0.34rem 0.5rem;
  border-bottom:1px solid var(--rule);
}
.tally td.num{
  text-align:right;
  font-variant-numeric:tabular-nums;
  font-weight:700;
}
.tally tr.total td{
  border-top:2px solid var(--ink);
  border-bottom:none;
  font-variant:small-caps;
  letter-spacing:0.08em;
  padding-top:0.5rem;
}
.modecell{font-variant:small-caps;letter-spacing:0.06em;}

/* ---------- Tags ---------- */
.tag{
  display:inline-block;
  font-variant:small-caps;
  letter-spacing:0.12em;
  font-size:0.72rem;
  font-weight:700;
  padding:0.12em 0.7em;
  border:1.5px solid currentColor;
  border-radius:2px;
  line-height:1.5;
}
.tag-amazing{color:var(--green);background:rgba(79,107,58,0.10);}
.tag-acceptable{color:var(--ink-soft);background:rgba(74,68,59,0.06);}
.tag-neutral{color:var(--rule-strong);background:transparent;}
.tag-redirected{color:var(--ochre-deep);background:rgba(176,122,38,0.10);}
.tag-confused{color:var(--ochre-deep);background:rgba(176,122,38,0.08);}
.tag-rejected{color:var(--red);background:rgba(156,58,44,0.09);}

/* ---------- Evidence cards ---------- */
.cards{display:flex;flex-direction:column;gap:1.4rem;}
.card{
  background:var(--paper-2);
  border:1px solid var(--rule);
  border-left:5px solid var(--rule-strong);
  padding:1.1rem 1.2rem 1rem;
}
.card-amazing{border-left-color:var(--green);}
.card-rejected{border-left-color:var(--red);}
.card-redirected{border-left-color:var(--ochre);}
.card-confused{border-left-color:var(--ochre);}
.card-head{margin-bottom:0.7rem;}
.card-quote{
  margin:0 0 0.85rem;
  font-size:1.15rem;
  line-height:1.45;
  font-style:italic;
  color:var(--ink);
}
.card-meta{font-size:0.92rem;color:var(--ink-soft);}
.card-line{margin:0.45rem 0;}
.card-line .label{
  display:block;
  font-variant:small-caps;
  letter-spacing:0.12em;
  font-size:0.7rem;
  color:var(--ochre-deep);
  font-weight:700;
  font-style:normal;
  margin-bottom:0.1rem;
}

/* ---------- Footer ---------- */
.manual-foot{
  max-width:var(--measure);
  margin:3rem auto 0;
  border-top:3px double var(--rule-strong);
  padding-top:1.1rem;
  text-align:center;
  font-variant:small-caps;
  letter-spacing:0.14em;
  font-size:0.74rem;
  color:var(--ink-soft);
}

@media (max-width:640px){
  body{font-size:17px;}
  .wrap{padding:1.6rem 1.05rem 3.5rem;}
  .masthead h1{font-size:2rem;}
  .tables{flex-direction:column;gap:1.4rem;}
}
"""


def build_html(front_html, sections, exemplars, verdicts, modes, n_records, generated):
    # Section rendering. We give the Evidence + tally their own treatment;
    # everything from the cartridge is rendered in order.
    sec_blocks = []
    for s in sections:
        num = s.get("num")
        numhtml = f'<span class="sec-num">{html.escape("§"+num)}</span>' if num else ""
        sec_blocks.append(f"""  <section>
    <div class="sec-head">{numhtml}<h2>{html.escape(s['title'])}</h2></div>
    {s['html']}
  </section>""")
    cartridge_html = "\n".join(sec_blocks)

    tables_html = summary_tables(verdicts, modes)
    cards_html = "\n".join(evidence_card(r) for r in exemplars)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RUMOR · A Field Manual of Your Taste</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">

  <header class="masthead">
    <p class="kicker">Field Manual &middot; Restricted Circulation</p>
    <h1>RUMOR</h1>
    <p class="kicker" style="letter-spacing:0.18em;margin-top:-0.2rem;">A Field Manual of Your Taste</p>
    <p class="subtitle">A <em>rumor</em>: a confident, useful fabrication of your
    taste, mined from {n_records} real reactions, not the real
    thing. Trust it the way you&rsquo;d trust hearsay from someone who knows you well.</p>
    <p class="colophon">Distilled {generated}<span>&bull;</span>N = {n_records} reactions<span>&bull;</span>Runtime layer</p>
  </header>

  <div class="frontmatter">
    {front_html}
  </div>

{cartridge_html}

  <section id="ledger">
    <div class="sec-head"><span class="sec-num">§L</span><h2>The Ledger</h2></div>
    <p>What {n_records} reactions look like in aggregate. Your &ldquo;amazing&rdquo;
    is rare and loud (usually only a handful of records). Most reactions are terse
    trust and clean corrections; the tallies below show the split.</p>
{tables_html}
  </section>

  <section id="evidence">
    <div class="sec-head"><span class="sec-num">§E</span><h2>Evidence · The Record</h2></div>
    <p>Selected exemplars in your own words: the rare gushes, the sharpest
    rejections, and the redirects. Read the verdict, then the words, then the
    read.</p>
    <div class="cards">
{cards_html}
    </div>
  </section>

  <p class="manual-foot">RUMOR &middot; This is hearsay, well-sourced &middot; Act on it, don&rsquo;t recite it</p>

</div>
</body>
</html>
"""


def main():
    if not EVAL.exists():
        raise SystemExit("No docs/eval-set.jsonl yet. Generate your eval-set first "
                         "(run `rumor remine`, see ONBOARDING.md), then re-run.")
    md = CARTRIDGE.read_text()
    front_html, sections = parse_cartridge(md)
    recs = load_records()
    verdicts, modes = tally(recs)
    exemplars = select_exemplars(recs)

    n_records = len(recs)
    generated = datetime.date.today().isoformat()
    out_html = build_html(front_html, sections, exemplars, verdicts, modes,
                          n_records, generated)
    OUT.write_text(out_html)

    print(f"Wrote {OUT} ({len(out_html):,} bytes)")
    print(f"  cartridge sections: {len(sections)}")
    print(f"  evidence exemplars: {len(exemplars)}")
    print(f"  verdict tally: {dict(verdicts)}")
    print(f"  mode tally: {dict(modes)}")


if __name__ == "__main__":
    main()
