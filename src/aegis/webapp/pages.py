"""Rendering the web app's pages as plain HTML strings.

Every page is built here, in one place, so the server only has to
decide which page a request gets. Two rules run through all of it:

- Text that came from a live source (record titles, summaries, error
  notes) is untrusted and is escaped before it goes into the page.
  A ClinVar title with markup in it renders as text, never as HTML.
- The pages say what the evidence core actually did: the verdict and
  its reason, the counts per source, when each record was fetched
  and whether that copy was live or from the cache, the citations
  the checker removed, and the sources that did not answer.

The look is deliberately simple: white background, one column, the
same family of styles as the trace explorer. This is a product
prototype a person can read, not a design exercise.
"""

from __future__ import annotations

import html

# Display names and reading order for the sources, matching the
# Markdown report: classifications first, then literature, then
# population frequencies.
SOURCE_LABELS = {
    "clinvar": "ClinVar",
    "pubmed": "PubMed",
    "gnomad": "gnomAD",
}
SOURCE_ORDER = ["clinvar", "pubmed", "gnomad"]

_DESCRIPTION = (
    "Aegis is an evidence agent for genomic variant research. "
    "It answers only with verified sources, or it declines."
)

_FOOTER = (
    "Local product prototype. Public data only: ClinVar, PubMed, "
    "and gnomAD. Reports from this session are kept in memory only; "
    "there are no accounts and no database, and closing the server "
    "clears the history."
)

_STYLE = """
  * { box-sizing: border-box; }
  body { margin: 0; font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
         background: #ffffff; color: #1a1a1a; }
  .wrap { max-width: 860px; margin: 0 auto; padding: 28px 20px 40px; }
  header h1 { margin: 0; font-size: 30px; }
  header p { margin: 6px 0 0; color: #444; font-size: 15px; max-width: 640px; }
  form.ask { margin: 26px 0 8px; padding: 18px; border: 1px solid #e5e7eb;
             border-radius: 10px; background: #fafafa; }
  form.ask label { display: block; font-weight: 600; font-size: 14px; margin: 10px 0 4px; }
  form.ask label:first-child { margin-top: 0; }
  form.ask input { width: 100%; padding: 9px 10px; font-size: 15px;
                   border: 1px solid #d1d5db; border-radius: 6px; }
  form.ask .hint { font-size: 12.5px; color: #666; margin-top: 3px; }
  form.ask button { margin-top: 14px; padding: 9px 18px; font-size: 15px;
                    font-weight: 600; color: #fff; background: #1d4ed8;
                    border: none; border-radius: 6px; cursor: pointer; }
  .error { margin: 18px 0 0; padding: 12px 14px; border-radius: 8px;
           background: #fef2f2; border: 1px solid #fecaca; color: #991b1b;
           font-size: 14.5px; }
  .verdict { margin: 22px 0; padding: 16px 18px; border-radius: 10px; }
  .verdict.ok { background: #f0fdf4; border: 1px solid #bbf7d0; }
  .verdict.no { background: #fffbeb; border: 1px solid #fde68a; }
  .verdict .word { font-size: 24px; font-weight: 700; }
  .verdict .reason { margin-top: 5px; font-size: 14.5px; color: #333; }
  .counts { font-size: 14px; color: #444; margin: 4px 0 20px; }
  h2 { font-size: 19px; margin: 26px 0 8px; }
  h3 { font-size: 16px; margin: 20px 0 6px; }
  .item { border-left: 4px solid #2563eb; background: #fafafa;
          padding: 9px 13px; margin: 0 0 9px; border-radius: 0 6px 6px 0; }
  .item .title { font-weight: 600; font-size: 14.5px; }
  .item .meta { font-size: 12.5px; color: #555; margin-top: 3px; }
  .item .summary { font-size: 13.5px; color: #333; margin-top: 4px; }
  .badge { display: inline-block; padding: 1px 7px; border-radius: 9px;
           font-size: 11px; font-weight: 600; margin-left: 6px; }
  .b-live { background: #dcfce7; color: #166534; }
  .b-cache { background: #e0e7ff; color: #3730a3; }
  .note { font-size: 14px; color: #444; padding: 7px 0; border-bottom: 1px solid #f0f0f0; }
  .history { margin-top: 30px; }
  .history .row { padding: 9px 2px; border-bottom: 1px solid #f0f0f0; font-size: 14.5px; }
  .history .when { color: #777; font-size: 12.5px; }
  .empty { color: #777; font-size: 14px; }
  footer { margin-top: 38px; padding-top: 14px; border-top: 1px solid #e5e7eb;
           color: #666; font-size: 12.5px; }
  a { color: #1d4ed8; }
"""


def esc(text: object) -> str:
    """Escape one value for HTML text or attribute context."""
    return html.escape(str(text if text is not None else ""), quote=True)


def _page(title: str, body: str) -> str:
    return (
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
        f"<title>{esc(title)}</title>\n<style>{_STYLE}</style>\n</head>\n<body>\n"
        f"<div class=\"wrap\">\n{body}\n"
        f"<footer>{esc(_FOOTER)}</footer>\n</div>\n</body>\n</html>\n"
    )


def _label(source: str) -> str:
    return SOURCE_LABELS.get(source, source)


def render_home(history, error: str | None = None, gene: str = "", variant: str = "") -> str:
    """The home page: description, the question form, session history."""
    parts = []
    parts.append("<header>")
    parts.append("<h1>Aegis</h1>")
    parts.append(f"<p>{esc(_DESCRIPTION)}</p>")
    parts.append("</header>")

    if error:
        parts.append(f"<div class=\"error\">{esc(error)}</div>")

    parts.append("<form class=\"ask\" method=\"post\" action=\"/query\">")
    parts.append("<label for=\"gene\">Gene (required)</label>")
    parts.append(
        f"<input id=\"gene\" name=\"gene\" type=\"text\" value=\"{esc(gene)}\" "
        "placeholder=\"For example BRCA1\">"
    )
    parts.append("<label for=\"variant\">Variant (optional)</label>")
    parts.append(
        f"<input id=\"variant\" name=\"variant\" type=\"text\" value=\"{esc(variant)}\" "
        "placeholder=\"For example 17-43044295-G-A\">"
    )
    parts.append(
        "<div class=\"hint\">Variant ids use the gnomAD form "
        "chrom-position-ref-alt, as in the example. HGVS forms like "
        "c.711del or p.Glu237fs are also accepted. Leave it blank to "
        "ask about the gene as a whole.</div>"
    )
    parts.append("<button type=\"submit\">Build evidence report</button>")
    parts.append("</form>")

    parts.append("<div class=\"history\">")
    parts.append("<h2>Reports from this session</h2>")
    entries = list(history)
    if not entries:
        parts.append(
            "<div class=\"empty\">No reports yet. Ask about a gene above "
            "and the report will be listed here.</div>"
        )
    for entry in entries:
        verdict_word = "Answerable" if entry.verdict.answerable else "Declined"
        question = entry.query.text
        parts.append(
            "<div class=\"row\">"
            f"<a href=\"/report/{esc(entry.entry_id)}\">{esc(question)}</a>"
            f" | {esc(verdict_word)}"
            f" | <span class=\"when\">{esc(entry.created_at)}</span>"
            "</div>"
        )
    parts.append("</div>")
    return _page("Aegis", "\n".join(parts))


def _counts_line(entry) -> str:
    """Per-source verified counts, plus what checking removed."""
    verified = entry.check.verified
    counts = {}
    for item in verified:
        counts[item.source] = counts.get(item.source, 0) + 1
    pieces = [
        f"{_label(source)}: {counts[source]}"
        for source in SOURCE_ORDER
        if counts.get(source)
    ]
    counts_text = ", ".join(pieces) if pieces else "no verified items"
    return (
        f"Verified evidence by source: {counts_text}. "
        f"Gathered before checking: {len(entry.dossier.items)}; "
        f"removed by the citation checker: {len(entry.check.failures)}."
    )


def render_result(entry) -> str:
    """One report page: verdict first, then the evidence behind it."""
    dossier = entry.dossier
    check = entry.check
    verdict = entry.verdict
    parts = []
    parts.append("<header>")
    parts.append("<h1>Aegis</h1>")
    parts.append(f"<p>{esc(_DESCRIPTION)}</p>")
    parts.append("</header>")
    parts.append("<p><a href=\"/\">Ask another question</a></p>")
    parts.append(f"<h2>Evidence report: {esc(dossier.query.text)}</h2>")

    css = "ok" if verdict.answerable else "no"
    word = "Answerable" if verdict.answerable else "Declined"
    parts.append(f"<div class=\"verdict {css}\">")
    parts.append(f"<div class=\"word\">{esc(word)}</div>")
    parts.append(f"<div class=\"reason\">{esc(verdict.reason)}</div>")
    parts.append("</div>")
    if not verdict.answerable:
        parts.append(
            "<p>No conclusion is drawn from evidence this thin. What "
            "was found and verified is shown below so the gap is "
            "visible, not filled in.</p>"
        )
    parts.append(f"<div class=\"counts\">{esc(_counts_line(entry))}</div>")

    parts.append("<h2>Evidence</h2>")
    if not check.verified:
        parts.append(
            "<div class=\"empty\">No evidence items passed citation "
            "checking for this question.</div>"
        )
    for source in SOURCE_ORDER:
        items = [item for item in check.verified if item.source == source]
        if not items:
            continue
        parts.append(f"<h3>{esc(_label(source))} ({len(items)})</h3>")
        for item in items:
            origin = "from cache" if item.from_cache else "live fetch"
            badge_cls = "b-cache" if item.from_cache else "b-live"
            fetched = item.fetched_at or "fetch time not recorded"
            parts.append("<div class=\"item\">")
            parts.append(f"<div class=\"title\">{esc(item.title)}</div>")
            parts.append(
                f"<div class=\"summary\">{esc(item.summary)}</div>"
            )
            parts.append(
                "<div class=\"meta\">Record: "
                f"{esc(item.record_id)} | "
                f"<a href=\"{esc(item.source_url)}\">source record</a> | "
                f"Fetched {esc(fetched)}"
                f"<span class=\"badge {badge_cls}\">{esc(origin)}</span>"
                "</div>"
            )
            parts.append("</div>")

    if check.failures:
        parts.append("<h2>Citations removed by the checker</h2>")
        parts.append(
            "<p>These items were in the gathered dossier and did not "
            "pass citation checking. They support nothing in this "
            "report.</p>"
        )
        for failure in check.failures:
            parts.append(
                "<div class=\"note\">"
                f"{esc(_label(failure.source))} record "
                f"{esc(failure.record_id)} ({esc(failure.title)}): "
                f"{esc(failure.reason)}</div>"
            )

    if dossier.source_errors:
        parts.append("<h2>Sources that did not answer</h2>")
        for note in dossier.source_errors:
            parts.append(f"<div class=\"note\">{esc(note)}</div>")

    return _page(f"Aegis report: {dossier.query.text}", "\n".join(parts))


def render_message(title: str, message: str) -> str:
    """A small plain page for a dead end a user can understand."""
    body = (
        "<header><h1>Aegis</h1></header>"
        f"<h2>{esc(title)}</h2>"
        f"<p>{esc(message)}</p>"
        "<p><a href=\"/\">Back to the home page</a></p>"
    )
    return _page(title, body)
