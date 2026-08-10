"""Regional watchboard — the v2 surface.

English, unlike the v1 pages: v2 is English-first, and mixing the two inside
one screen would be worse than a visible seam between old and new.

What this page is for
---------------------
It answers "where does each region stand" in a way that cannot be misread.
Three states are kept visibly distinct, because collapsing them is the most
dangerous thing an intelligence interface can do:

- **active** — something is happening, with an effect size and evidence
- **quiet** — indicators were tested and found nothing, *and here is the
  smallest thing that would have been caught*
- **not tested / not monitored** — nobody looked, or nothing could be looked
  at. Never rendered as calm.

A blank panel reads as "all clear" to a tired analyst at 07:00. Every empty
state on this page therefore says why it is empty.
"""
from __future__ import annotations

import contextlib
import html as _html
from datetime import datetime

import streamlit as st

from core import storage
from sentinel.core.contracts import MonitoringStatus, Verdict
from sentinel.core.detect.power import DetectionPowerCatalog
from sentinel.core.time import PointInTimeStore
from sentinel.regions import REGIONS, evaluate_region
from sentinel.regions.providers import storage_provider
from sentinel.report import compose, compose_region
from ui.components import render_topbar
from ui.theme import P

_POWER_PATH = "config/detection_power.json"

_VERDICT_STYLE = {
    Verdict.ACTIVE: ("ACTIVE", "bad"),
    Verdict.NOT_ACTIVE: ("QUIET", "ok"),
    Verdict.INSUFFICIENT_DATA: ("NOT TESTED", "muted"),
}

_STATUS_NOTE = {
    MonitoringStatus.NOT_MONITORED: (
        "This region is not monitored. Nothing below is a statement about "
        "what is happening there."),
    MonitoringStatus.DATA_ONLY: (
        "Data is collected for this region but no indicators are defined, so "
        "nothing is being tested and nothing can be reported."),
}


def _colour(kind: str) -> str:
    return {"bad": P["bad"], "ok": P["ok"], "muted": P["text_muted"]}[kind]


def _render_signal(signal, indicator=None) -> None:
    label, kind = _VERDICT_STYLE[signal.verdict]
    st.markdown(
        f"<div style='border-left:3px solid {_colour(kind)};padding-left:12px;"
        f"margin:14px 0;'>"
        f"<span style='color:{_colour(kind)};font-weight:600;font-size:0.8rem;"
        f"letter-spacing:0.05em;'>{label}</span>"
        f"<span style='margin-left:10px;font-weight:600;'>"
        f"{_html.escape(signal.indicator_key)}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )

    if signal.verdict is Verdict.ACTIVE:
        st.markdown(
            f"**{signal.effect_size:.1f}** scale units "
            f"**{signal.direction.value}** the baseline."
        )
    elif signal.verdict is Verdict.INSUFFICIENT_DATA:
        st.caption(f"Could not be tested: {signal.insufficient_reason}")
    else:
        # A null result without its floor is the reassurance this whole
        # design exists to prevent, so it is shown, not tucked away.
        st.caption(signal.detection_power.describe())

    st.caption(
        f"Confidence: **{signal.confidence.level.value}** — "
        f"{'; '.join(signal.confidence.reasons)}"
    )

    if signal.evidence:
        with st.expander(f"Evidence ({len(signal.evidence)})"):
            for item in signal.evidence:
                st.markdown(
                    f"- *{item.kind.value}* — {_html.escape(item.summary)}")

    # The written assessment is generated from the same signal, never typed
    # over it: the prose and the panel above cannot disagree.
    if indicator is not None:
        with st.expander("Written assessment"):
            st.text(compose(signal, indicator).format())


def _render_region(region, dataset_id: int | None, as_of: datetime,
                   catalog: DetectionPowerCatalog) -> None:
    st.subheader(region.name)
    if region.summary:
        st.caption(region.summary)

    note = _STATUS_NOTE.get(region.status)
    if note:
        st.info(note, icon="ℹ️")

    if not region.is_watched:
        if region.activation_requirements:
            st.markdown("**Activating this region would require:**")
            for requirement in region.activation_requirements:
                st.markdown(f"- {_html.escape(requirement)}")
        if region.indicators:
            with st.expander(
                    f"Indicators already written down "
                    f"({len(region.indicators)}) — declared, not yet running"):
                for indicator in region.indicators:
                    st.markdown(
                        f"**{_html.escape(indicator.name)}** "
                        f"*({indicator.status.value})*  \n"
                        f"{_html.escape(indicator.question)}")
        return

    if dataset_id is None:
        st.warning(
            "No dataset selected, so this region's indicators were not run. "
            "This is not a quiet result.", icon="⚠️")
        return

    store = PointInTimeStore()
    provider = storage_provider(dataset_id, region, store.view)
    status = evaluate_region(region, provider, as_of, catalog)

    st.markdown(f"### {_html.escape(status.headline())}")

    # Provenance: a replay resting on assumed arrival times is optimistic by
    # an unknown margin, and the analyst should see that before the verdicts.
    provenance = store.view(as_of).provenance(dataset_id)
    if provenance.n_rows and not provenance.is_faithful:
        st.caption(f"⚠️ {provenance.describe()}")

    by_key = {indicator.key: indicator for indicator in region.indicators}
    for signal in status.signals:
        _render_signal(signal, by_key.get(signal.indicator_key))

    # The periodic product, from the same signals the board is showing. It is
    # offered as a download rather than a copy-paste target so what leaves the
    # tool is the assessed text, not a screenshot of it.
    st.download_button(
        "Download region report (Markdown)",
        data=compose_region(status, by_key),
        file_name=f"{region.key}_{as_of.date()}.md",
        mime="text/markdown",
        key=f"report_{region.key}",
    )


def page_regions() -> None:
    render_topbar()
    st.title("Regional watchboard")
    st.caption(
        "Where each region stands. 'Quiet' means indicators were tested and "
        "found nothing, and states what would have been caught. It is kept "
        "distinct from 'not tested' and 'not monitored' throughout."
    )

    datasets = []
    with contextlib.suppress(Exception):
        datasets = storage.list_datasets()

    left, right = st.columns([2, 1])
    with left:
        if datasets:
            names = {d["name"]: d["id"] for d in datasets}
            chosen = st.selectbox("Dataset", list(names), key="rg_dataset")
            dataset_id = names[chosen]
        else:
            dataset_id = None
            st.warning("No datasets imported yet.", icon="⚠️")
    with right:
        as_of_date = st.date_input(
            "As of", value=datetime.now().date(), key="rg_as_of",
            help=("Renders the board as it would have looked on this date, "
                  "using only what had arrived by then."))
    as_of = datetime.combine(as_of_date, datetime.min.time())

    catalog = DetectionPowerCatalog.load(_POWER_PATH)
    if not catalog.entries:
        st.warning(
            "No measured detection power is available "
            f"(`{_POWER_PATH}`). Statistical indicators will report "
            "'not tested' rather than claiming a quiet result — run "
            "`python scripts/precompute_power.py`.",
            icon="⚠️")

    for tab, region in zip(st.tabs([r.name for r in REGIONS]), REGIONS,
                           strict=True):
        with tab:
            _render_region(region, dataset_id, as_of, catalog)
