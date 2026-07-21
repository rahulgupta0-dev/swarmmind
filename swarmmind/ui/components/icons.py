"""Phosphor icons helper for Streamlit."""

from __future__ import annotations

from typing import Optional

_PHOSPHOR_CDN = [
    "https://unpkg.com/@phosphor-icons/web@2.1.1/src/regular/style.css",
    "https://unpkg.com/@phosphor-icons/web@2.1.1/src/fill/style.css",
    "https://unpkg.com/@phosphor-icons/web@2.1.1/src/bold/style.css",
]


def icon_html(
    name: str,
    weight: str = "regular",
    size: str = "1em",
) -> str:
    """Return an HTML ``<i>`` element for a Phosphor icon.

    Args:
        name: Icon name (e.g. ``"magnifying-glass"``, ``"robot"``).
        weight: One of ``"regular"``, ``"thin"``, ``"light"``, ``"bold"``,
            ``"fill"``, ``"duotone"``.
        size: CSS size string (e.g. ``"1.5em"``, ``"24px"``).

    Returns:
        An HTML string for the icon element.
    """
    return (
        f'<i class="ph-{weight} ph-{name}" '
        f'style="font-size: {size};"></i>'
    )


def render_icon(
    st: object,
    name: str,
    weight: str = "regular",
    size: str = "1em",
) -> None:
    """Render a Phosphor icon into a Streamlit app via unsafe HTML.

    Args:
        st: The ``streamlit`` module (passed in so this module has no
            hard Streamlit dependency).
        name: Icon name.
        weight: Icon weight variant.
        size: CSS font-size value.
    """
    # Import here to avoid hard dependency at module level
    import streamlit as _st  # noqa: F811

    _st.markdown(
        icon_html(name, weight=weight, size=size),
        unsafe_allow_html=True,
    )


def load_phosphor_css(st: object) -> None:
    """Inject Phosphor icons CSS into the Streamlit app from CDN.

    Call early in your app (e.g. before the first ``st.markdown``).

    Args:
        st: The ``streamlit`` module.
    """
    import streamlit as _st  # noqa: F811

    for url in _PHOSPHOR_CDN:
        _st.markdown(
            f'<link rel="stylesheet" href="{url}">',
            unsafe_allow_html=True,
        )
